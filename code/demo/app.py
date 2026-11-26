"""Loopback-only HTTP API. Video bytes are streamed to this project's data folder."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import uuid
from collections import Counter
from urllib.parse import quote, unquote, urlparse
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect
from starlette.concurrency import run_in_threadpool

from .jobs import BusyError, JobManager
from .media import PROFILES
from .demo_catalog import demo_courses, upgrade_catalog, upgrade_programming_samples, seed_activity, upgrade_practice_solutions
from .teaching_catalog import upgrade_teaching_samples
from .demo_assignments import upgrade_assignment_titles
from .assignment_questions import (answers_visible, record_answers_visible, upgrade_independent_assignments, validate_questions)
from .learning_metrics import (READING_THRESHOLD, build_profiles, scoped_reading_progress,
                               teacher_analytics as aggregate_teacher)
from .knowledge_graph import build_graph_update, graph_view
from .enrollments import is_enrolled, matches_major, subject_key, upgrade_enrollments, replace_roster
from .accounts import AccountStore, DEMO_STUDENT, DEMO_TEACHER
from .summaries import SummaryManager, SummaryError, SUMMARY_CSP
from . import summaries as summary_service
from .transcriptions import TranscriptionManager, TranscriptionError
from . import transcriptions as transcription_service
from .programming import ProgrammingManager, validate_question, LIMITS as PYTHON_LIMITS
from .programming_ai import LABELS as CODE_AI_LABELS, PROMPTS as CODE_AI_PROMPTS
from .live_quizzes import LiveQuizManager, LiveQuizError
from .quizzes import QuizManager, ACTIVE as QUIZ_ACTIVE
from .analyses import AnalysisManager

# Windows 下 Python 的 mimetypes 会把 .js 识别为 text/plain，浏览器会拒绝执行
# 前端 module script，导致页面空白。这里强制修正为 application/javascript。
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 8 * 1024**3
EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts"}
TOKEN = secrets.token_urlsafe(32)

# 预置课程使用的默认知识点（教师可继续手动维护课程内的知识点列表）。
DEFAULT_KNOWLEDGE_POINTS = [
    {"id": "kp1", "name": "变量与数据类型"},
    {"id": "kp2", "name": "字符串类型"},
    {"id": "kp3", "name": "print 输出函数"},
]
DEFAULT_KP_MAP = {"q1": "kp1", "q2": "kp2", "q3": "kp3"}
DEFAULT_MAJORS = [
    {"id": "m-soft", "name": "软件工程"},
    {"id": "m-ai", "name": "人工智能"},
    {"id": "m-cs", "name": "计算机科学"},
]


def _within_seconds(left: str, right: str, seconds: int) -> bool:
    """比较两条 ISO 时间戳是否在 seconds 秒以内（用于防重复提交）。"""
    try:
        delta = datetime.fromisoformat(left) - datetime.fromisoformat(right)
    except (TypeError, ValueError):
        return False
    return abs(delta.total_seconds()) <= seconds


def _answers_signature(answers: dict) -> list:
    """把答题内容归一为可比较的签名：仅当两次提交的答案完全一致才视为同次重复。"""
    return sorted((str(key), int(value)) for key, value in answers.items() if value is not None)


def _days_later(days: int) -> str:
    """返回当前 UTC 时间 days 天后的 ISO 时间戳（复习计划引擎调度用）。"""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# —— 三信号 SM-2 复习调度（features.review_scheduler = "sm2"）——
# 回忆质量 q 不依赖学生自评、答题耗时或额外按钮，只用三个客观信号推导：
#   1) 本次对错；2) 该题历史答错次数；3) 实际间隔是否显著超过计划间隔（逾期才复习还答对）。
SM2_QUALITY_DELTA = {5: 0.10, 4: 0.0, 3: -0.14, 2: -0.32}
SM2_EASE_MIN, SM2_EASE_MAX = 1.3, 3.0
SM2_MAX_INTERVAL = 180                      # 间隔上限，避免极端膨胀
SM2_MASTERED_INTERVAL = 60                  # 达到该间隔视为掌握（SM-2 需要放开 14 天封顶）

FEATURES = {
    "course_audiences": True, "student_profiles": True, "python_programming": True,
    "ai_quiz": True, "ai_quiz_actions": True, "ai_analysis": True, "python_ai_advice": True,
    "teacher_content": True, "assignments": True, "gradebook": True, "announcements": True,
    "teacher_workspace": True, "teaching_classes": True, "professional_classes": True,
    "assignment_practice_progress": True, "live_quiz": True, "independent_assignments": True,
    "review_calendar_export": True, "student_enrollments": True, "review_scheduler": "sm2",
}


def sm2_quality(record: dict, correct: bool, now: datetime) -> int:
    """三信号回忆质量：答错=2；反复错且首次复习就答对=3；逾期(≥1.5 倍计划间隔)答对=5；其余=4。

    反复答错过的题即使逾期答对也压到 4，避免"刚看完解析的瞬时记忆/蒙对"被当成熟练。
    """
    if not correct:
        return 2
    wrong = int(record.get("wrong_count") or 0)
    reviewed = int(record.get("review_count") or 0)
    if wrong >= 2 and reviewed == 0:
        return 3
    planned = int(record.get("review_interval_days") or 1)
    started = record.get("last_reviewed_at") or record.get("first_wrong_at") or record.get("added_at")
    actual = planned
    try:
        actual = (now - datetime.fromisoformat(started)).total_seconds() / 86400
    except (TypeError, ValueError):
        pass
    if actual >= planned * 1.5:
        return 4 if wrong >= 2 else 5
    return 4


def sm2_interval(record: dict, ease: float) -> int:
    """下一次复习间隔：从重置态(≤1 天)首次答对给 2 天、第二次给 6 天，之后按记忆倍率增长，封顶 180 天。"""
    previous = int(record.get("review_interval_days") or 1)
    if previous <= 1:
        return 2
    if previous == 2:
        return 6
    return min(int(previous * ease + 0.5), SM2_MAX_INTERVAL)


def review_scheduler() -> str:
    """读取复习调度算法开关；默认三信号 SM-2，显式配置为 linear 时回退旧版 2 倍封顶逻辑。"""
    return "linear" if FEATURES.get("review_scheduler") == "linear" else "sm2"


# —— 学习计划与打卡 ——
# 目标按本地自然周（周一至周日）统计；打卡只认真实事件（正式提交、错题复习），
# 打开页面、看视频、点错题本都不记打卡，保证"连续天数"能对外解释。
STUDY_PLAN_DEFAULT = {"weekly_submissions": 3, "weekly_reviews": 10}
STUDY_PLAN_LIMIT = 50
STUDY_CALENDAR_DAYS = 30
STUDY_ACHIEVEMENTS = [
    ("first_submission", "第一次练习", "完成第一次正式练习提交"),
    ("first_review", "第一次复习", "在错题本里完成第一次复习"),
    ("perfect_score", "满分一次", "任意一次练习提交拿到 100 分"),
    ("streak_3", "连续三天", "连续 3 天完成当日全部计划任务"),
    ("streak_7", "连续七天", "连续 7 天完成当日全部计划任务"),
    ("review_20", "复习二十题", "累计完成 20 次错题复习"),
    ("clear_wrong", "错题清零", "曾经有错题，且当前全部已掌握"),
("week_goal", "周目标达成", "本周计划排出的每日任务全部完成"),
]
WEEKDAY_LABELS = "日一二三四五六"

# 学习事件：一行一个动作，是打卡、画像与复习的共同事实来源。
# 打卡的两类（练习提交 / 错题复习）由事件派生，保证"日计数"与"明细"永远一致。
EVENT_KINDS = ("practice_submit", "code_submit", "review", "live_answer",
               "ai_question_add", "video_watch", "resource_read")
EVENT_CHECKIN = {"practice_submit": "submissions", "code_submit": "submissions", "review": "reviews"}
EVENT_LABELS = {"practice_submit": "练习提交", "code_submit": "编程题提交", "review": "错题复习",
                "live_answer": "课堂抢答", "ai_question_add": "加入 AI 题", "video_watch": "视频学习",
                "resource_read": "资料阅读"}

# 学习资料读到 60% 才记一次阅读：进度条过半不等于学过，避免"点开就打卡"。
# 知识点级复习沿用题目级 SM-2 的间隔序列（天），只是没有独立 EF，用正确率修正档位。
KNOWLEDGE_INTERVALS = (2, 6, 15, 38, 95)


def local_day(value=None) -> date:
    """UTC 时间（ISO 字符串 / datetime / 空）转本地日期，让打卡按“本地日”归档。"""
    moment = value if isinstance(value, datetime) else None
    if moment is None and value:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            moment = None
    if moment is None:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().date()


def bump_study_activity(data, student_id, kind, moment=None):
    """按本地日期累加一次活动；kind 只允许 submissions / reviews。"""
    entry = (data.setdefault("study_activity", {}).setdefault(student_id, {})
             .setdefault(local_day(moment).isoformat(), {"submissions": 0, "reviews": 0}))
    entry[kind] = int(entry.get(kind, 0)) + 1


def activity_days(data, student_id) -> dict:
    """取某学生的“日期 → 活动计数”表；缺失时返回空表，不写回数据。"""
    return dict((data.get("study_activity") or {}).get(student_id) or {})


def record_event(data, student_id, kind, moment=None, *, course_id=None, ref_id=None,
                 correct=None, duration_seconds=0, source="course", knowledge_point_id=None):
    """追加一条学习事件（只增不改），并同步打卡计数，返回写入的事件本身。

    事件是明细，study_activity 是它的日聚合视图：两者同一次写入，避免"画像有、
    打卡没有"这类口径分裂。
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind}")
    if moment is None:
        stamp = datetime.now(timezone.utc).isoformat()
    elif isinstance(moment, datetime):
        stamp = moment.isoformat()
    else:
        stamp = str(moment)
    event = {"id": uuid.uuid4().hex[:12], "ts": stamp, "student_id": student_id,
             "course_id": course_id, "type": kind, "ref_id": ref_id,
             "correct": correct, "duration_seconds": int(duration_seconds or 0),
             "source": source}
    if knowledge_point_id is not None:
        event["knowledge_point_id"] = knowledge_point_id
    data.setdefault("learning_events", []).append(event)
    checkin = EVENT_CHECKIN.get(kind)
    if checkin:
        bump_study_activity(data, student_id, checkin, stamp)
    return event


def study_streak(days: dict, today: date) -> tuple[int, int]:
    """返回 (当前连续天数, 历史最长连续天数)。

    今天还没完成当日计划时从昨天开始回溯——当天没打卡不该立刻清零，第二天仍没打卡才算中断。
    """
    active = sorted(day for day, entry in days.items() if entry.get("submissions") or entry.get("reviews"))
    if not active:
        return 0, 0
    points = [date.fromisoformat(day) for day in active]
    longest = run = 1
    for previous, moment in zip(points, points[1:]):
        run = run + 1 if (moment - previous).days == 1 else 1
        longest = max(longest, run)
    known = set(active)
    cursor = today if active[-1] == today.isoformat() else today - timedelta(days=1)
    current = 0
    while cursor.isoformat() in known:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def plan_rate(value: int, goal: int) -> int:
    """目标完成率，封顶 100；目标为 0 时视为未设置。"""
    return min(100, round(value / goal * 100)) if goal else 0


def _local_moment(value):
    """ISO 时间戳 → 本地时区 datetime；解析不了返回 None。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def knowledge_schedule(submissions, moment=None):
    """知识点级复习调度：把题目级 SM-2 的间隔序列上移到知识点。

    与题目级一致：间隔按"连续答对次数"走 2/6/15/38/95 天，封顶 SM2_MAX_INTERVAL。
    与题目级不同：知识点没有自己的 EF，用该点正确率近似记忆强度——低于 50% 时退回上一档，
    这样"反复错的知识点"不会因为偶然答对一次就被排到 95 天以后。
    """
    now = moment or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_stamp = now.isoformat()
    ordered = sorted(submissions, key=lambda item: item.get("submitted_at") or "")
    known_points = {}
    for record in ordered:
        for detail in record.get("details") or []:
            if detail.get("knowledge_point_id") and detail.get("knowledge_point_name"):
                key = (record.get("course_id"), detail["knowledge_point_name"])
                known_points.setdefault(key, set()).add(detail["knowledge_point_id"])
    points = {}
    for record in ordered:
        stamp = record.get("submitted_at") or ""
        for detail in record.get("details") or []:
            name = detail.get("knowledge_point_name") or "未分类"
            if name == "未分类":
                continue  # 没有知识点的题无法排进复习，不占位
            course_id = record.get("course_id")
            point_id = detail.get("knowledge_point_id")
            # Existing schedules may have only a label. Merge when this course has one matching ID.
            candidates = known_points.get((course_id, name), set())
            if not point_id and len(candidates) == 1:
                point_id = next(iter(candidates))
            key = (course_id, point_id) if point_id else name
            entry = points.setdefault(key, {"id": point_id or name, "name": name, "answered": 0,
                                            "correct": 0, "streak": 0, "last_seen": stamp})
            entry["answered"] += 1
            if detail.get("correct"):
                entry["correct"] += 1
                entry["streak"] += 1
            else:
                entry["streak"] = 0
            if stamp > entry["last_seen"]:
                entry["last_seen"] = stamp
    rows = []
    for entry in points.values():
        accuracy = round(entry["correct"] / entry["answered"] * 100) if entry["answered"] else 0
        stage = min(len(KNOWLEDGE_INTERVALS) - 1, max(0, entry["streak"] - 1))
        interval = 1 if entry["streak"] == 0 else KNOWLEDGE_INTERVALS[stage]
        if entry["streak"] and accuracy < 50 and stage > 0:
            interval = KNOWLEDGE_INTERVALS[stage - 1]
        seen = _local_moment(entry["last_seen"]) or now
        next_at = (seen + timedelta(days=interval)).isoformat()
        rows.append({"id": entry["id"], "name": entry["name"], "answered": entry["answered"],
                     "wrong": entry["answered"] - entry["correct"], "accuracy": accuracy,
                     "streak": entry["streak"], "interval_days": interval,
                     "next_review_at": next_at, "due": next_at <= now_stamp,
                     "mastered": accuracy >= 80 and entry["streak"] >= 3})
    rows.sort(key=lambda item: (not item["due"], -item["wrong"], item["next_review_at"]))
    return rows


def rhythm_payload(events, submissions, moment=None):
    """学习节律：从事件时间戳看"几点在学"，再从迟交记录看"是否赶截止"。"""
    now = moment or datetime.now(timezone.utc)
    hours = Counter()
    weekend = 0
    dated = 0
    for item in events:
        seen = _local_moment(item.get("ts"))
        if not seen:
            continue
        dated += 1
        hours[seen.hour] += 1
        if seen.weekday() >= 5:
            weekend += 1
    windows = {"深夜 0–6": 0, "上午 6–12": 0, "下午 12–18": 0, "夜间 18–24": 0}
    for hour, count in hours.items():
        slot = ("深夜 0–6" if hour < 6 else "上午 6–12" if hour < 12
                else "下午 12–18" if hour < 18 else "夜间 18–24")
        windows[slot] += count
    weekend_ratio = round(weekend / dated * 100) if dated else None
    late_count = sum(1 for item in submissions if item.get("late"))
    late_ratio = round(late_count / len(submissions) * 100) if submissions else None
    peak = max(windows, key=windows.get) if dated else None
    peak_share = round(windows[peak] / dated * 100) if dated else None
    if not dated:
        label = "还没有足够活动判断节律"
    elif weekend_ratio >= 40:
        label = "周末集中型"
    elif peak in {"深夜 0–6", "夜间 18–24"} and peak_share >= 35:
        label = "夜间型"
    elif peak == "上午 6–12" and peak_share >= 40:
        label = "上午型"
    elif peak == "下午 12–18" and peak_share >= 40:
        label = "下午型"
    else:
        label = "分散型"
    return {"label": label, "windows": windows, "peak": peak, "peak_share": peak_share,
            "weekend_ratio": weekend_ratio, "late_ratio": late_ratio,
            "late_count": late_count, "dated_events": dated}


def plan_progress(days, goals, today, weeks=4, plan=None):
    """元认知里的"计划达成"：按自然周回看，两项目标都完成才算达标一周。

    本周还没结束，只报进度不判"未达成"，否则周一看画像就是"失败"。
    """
    rows = []
    for offset in range(weeks):
        start = today - timedelta(days=today.weekday()) - timedelta(days=7 * offset)
        entries = [days.get((start + timedelta(days=index)).isoformat()) or {} for index in range(7)]
        subs = sum(int(item.get("submissions", 0)) for item in entries)
        revs = sum(int(item.get("reviews", 0)) for item in entries)
        if offset == 0 and plan and plan.get("active"):
            rows.append({"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                         "submissions": subs, "reviews": revs,
                         "rate": int(plan.get("rate") or 0), "met": False,
                         "finished": False, "current": True, "mode": "plan",
                         "planned": int(plan.get("planned") or 0),
                         "done": int(plan.get("done") or 0)})
            continue
        rate = round((plan_rate(subs, goals["weekly_submissions"]) +
                      plan_rate(revs, goals["weekly_reviews"])) / 2)
        finished = offset > 0
        met = finished and subs >= goals["weekly_submissions"] and revs >= goals["weekly_reviews"]
        rows.append({"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                     "submissions": subs, "reviews": revs, "rate": rate,
                     "met": met, "finished": finished, "current": offset == 0})
    closed = [item for item in rows if item["finished"]]
    return {"weeks": rows, "met_count": sum(1 for item in closed if item["met"]),
            "closed_count": len(closed), "current_rate": rows[0]["rate"]}


def reading_payload(data, student_id, courses=None):
    """学习资料阅读进度：替代视频观看进度，读到 60% 才算读过一份。

    只统计带正文（content）的资料：仅登记了课件的资源没有阅读进度上报点，
    计入分母会让完成度永远到不了 100%。
    """
    table = (data.get("reading_progress") or {}).get(student_id) or {}
    resources, entries = scoped_reading_progress(table, courses or [])
    done = sum(int(value.get("percent") or 0) >= READING_THRESHOLD for value in entries.values())
    recent = [{"id": key, "name": resources[key][1] or value.get("name"),
               "percent": int(value.get("percent") or 0), "updated_at": value.get("updated_at")}
              for key, value in sorted(entries.items(),
                                       key=lambda pair: pair[1].get("updated_at") or "", reverse=True)[:4]]
    return {"total": len(resources), "done": done,
            "percent": round(done / len(resources) * 100) if resources else None,
            "recent": recent}


# ── 学习计划：一句话目标 → 工作量核算 → 每周/每天任务 ──────────────────────────

PLAN_TASK_LABELS = {"resource": "读资料", "knowledge": "知识点", "practice": "练习",
                    "code": "编程", "review": "复习"}
PLAN_ESTIMATE = {"resource_base": 5.0, "chars_per_minute": 300.0, "knowledge": 10.0,
                 "choice": 1.5, "code": 12.0, "review": 2.0}
PLAN_MIN_WEEKLY_MINUTES = 30
PLAN_MAX_WEEKLY_MINUTES = 1800
PLAN_MAX_WEEKS = 26
PLAN_DAILY_CAP_MINUTES = 90
PLAN_REVIEW_SHARE = 0.2
PLAN_DAILY_REVIEW_LIMIT = 4
PLAN_LOOSE_RATIO = 0.6
PLAN_TIGHT_RATIO = 1.2
PLAN_DEFAULT_WEEKS = 8
PLAN_DEFAULT_WEEKLY_MINUTES = 180
PLAN_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
                   "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


PLAN_WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def plan_weekdays_for(count):
    """可学天数换成具体星期：5 天按工作日、2 天按周末，其余从周一起顺数。"""
    count = max(1, min(int(count or 7), 7))
    if count >= 7:
        return list(range(7))
    if count == 5:
        return [0, 1, 2, 3, 4]
    if count == 2:
        return [5, 6]
    return list(range(count))


def plan_item_counts(items):
    """把一组卡片折成「几份资料、几个知识点、几道练习、几道编程、几题复习、几分钟」。"""
    counts = {"resource": 0, "knowledge": 0, "practice": 0, "code": 0, "review": 0}
    titles, minutes = [], 0
    for item in items:
        minutes += int(item.get("minutes") or 0)
        kind = item.get("kind")
        if kind == "practice":
            counts["practice"] += int(item.get("count") or 1)
        elif kind in counts:
            counts[kind] += 1
        if kind == "knowledge" and item.get("title"):
            titles.append(item["title"])
    points = []
    for item in items:
        point = str(item.get("point") or "").strip()
        if point and point not in points:
            points.append(point)
    return {"counts": counts, "cards": len(items), "minutes": minutes,
            "knowledge": titles, "points": points[:8]}


def build_plan_days(weeks, parsed, reviews=None):
    """把每周的卡片摊到具体日期：复习只放到期的那天，新内容按日容量顺序填满。"""
    allowed = parsed.get("allowed_weekdays") or plan_weekdays_for(parsed.get("allowed_days"))
    capacity = max(1, int(parsed.get("daily_capacity") or PLAN_DAILY_CAP_MINUTES))
    review_cap = max(1, int(PLAN_DAILY_REVIEW_LIMIT))
    queue = sorted((item for item in (reviews or []) if item.get("kind") == "review"),
                   key=lambda item: item.get("due") or "")
    days = []
    for plan in weeks:
        begin = date.fromisoformat(plan["start"])
        slots = [begin + timedelta(days=offset) for offset in range(7)]
        slots = [day for day in slots if day.weekday() in allowed] or [begin]
        others = [item for item in plan["items"] if item["kind"] != "review"]
        other_at = 0
        for one_day in slots:
            key = one_day.isoformat()
            bucket, spent, placed = [], 0, 0
            # 只放到期（due <= 今天）的复习，单日不超过上限；没到期的留到那一天
            while queue and placed < review_cap and (queue[0].get("due") or "") <= key:
                item = queue.pop(0)
                bucket.append(item)
                spent += int(item["minutes"])
                placed += 1
            while other_at < len(others):
                item = others[other_at]
                if bucket and spent + int(item["minutes"]) > capacity:
                    break
                bucket.append(item)
                spent += int(item["minutes"])
                other_at += 1
                if spent >= capacity:
                    break
            if not bucket:
                continue
            days.append({"date": key, "week": plan["index"],
                         "weekday": PLAN_WEEKDAY_LABELS[one_day.weekday()],
                         "items": bucket, "counts": plan_item_counts(bucket)})
        # 本周排满后剩下的新内容压到最后一天；未到期的复习不压缩，自会落到后面的日期
        leftover = others[other_at:]
        if leftover:
            for entry in reversed(days):
                if entry["week"] == plan["index"]:
                    entry["items"].extend(leftover)
                    entry["counts"] = plan_item_counts(entry["items"])
                    break
    # 计划窗口内仍未投放的逾期复习，压到最后一天，避免静默丢失
    overdue = [item for item in queue if days and (item.get("due") or "") <= days[-1]["date"]]
    if overdue:
        days[-1]["items"].extend(overdue)
        days[-1]["counts"] = plan_item_counts(days[-1]["items"])
    return days


def plan_week_calendar(week, days, parsed, data, student, saved=None):
    """本周 7 天的逐日安排；不可学的日子标 rest，有任务的日子算出完成度。

    当天已经在页面上生成过任务卡时，优先用那张卡，避免「今日任务」与「本周进度」两套口径不一致。
    """
    begin = date.fromisoformat(week["start"])
    allowed = parsed.get("allowed_weekdays") or plan_weekdays_for(parsed.get("allowed_days"))
    table = {entry["date"]: entry for entry in days}
    tasks = (saved or {}).get("tasks") or {}
    rows = []
    for offset in range(7):
        one_day = begin + timedelta(days=offset)
        key = one_day.isoformat()
        entry = table.get(key)
        cards = tasks.get(key)
        if entry is None and not cards:
            rows.append({"date": key, "weekday": PLAN_WEEKDAY_LABELS[one_day.weekday()],
                         "rest": one_day.weekday() not in allowed, "minutes": 0,
                         "counts": plan_item_counts([])["counts"], "total": 0, "done": 0, "items": []})
            continue
        cards = cards or (entry or {}).get("items") or []
        counts = plan_item_counts(cards)
        activity = plan_day_activity(data, student, one_day)
        view = [{**card, "id": f"{card['kind']}:{card['ref_id']}", "done": plan_task_done(card, activity)}
                for card in cards]
        rows.append({"date": key, "weekday": PLAN_WEEKDAY_LABELS[one_day.weekday()],
                     "rest": one_day.weekday() not in allowed, "minutes": counts["minutes"],
                     "counts": counts["counts"],
                     "total": len(view), "done": sum(1 for card in view if card["done"]), "items": view})
    return rows


def plan_day_checkin(data, student, saved, day):
    """某天是否算成功打卡：当天排了任务卡时要求全部完成；没生成任务卡的日子退回「当天有练习或复习」。"""
    cards = (saved.get("tasks") or {}).get(day.isoformat())
    if cards:
        activity = plan_day_activity(data, student, day)
        done = sum(1 for card in cards if plan_task_done(card, activity))
        return {"date": day.isoformat(), "plan_total": len(cards), "plan_done": done,
                "complete": done == len(cards), "has_plan": True, "kind": "plan"}
    counts = ((data.get("study_activity") or {}).get(student["id"]) or {}).get(day.isoformat()) or {}
    active = bool(counts.get("submissions") or counts.get("reviews"))
    return {"date": day.isoformat(), "plan_total": 0, "plan_done": 0, "complete": False,
            "has_plan": False, "kind": "activity" if active else "none"}


def plan_week_progress(rows, today):
    """本周截止今天的计划完成度：只算已经到来的日子，今天之后不算欠。"""
    due = [row for row in rows if row["date"] <= today.isoformat() and row["total"]]
    total = sum(row["total"] for row in due)
    done = sum(row["done"] for row in due)
    review_due = [card for row in due for card in row.get("items") or [] if card.get("kind") == "review"]
    return {"planned": total, "done": done,
            "rate": int(round(done / total * 100)) if total else None,
            "review_planned": len(review_due),
            "review_done": sum(1 for card in review_due if card.get("done"))}


def plan_number(token: str) -> int:
    """把阿拉伯数字或常见中文数字读数成整数；读不出就返回 0，交给默认值兜底。"""
    token = (token or "").strip()
    if token.isdigit():
        return int(token)
    if token in PLAN_CN_NUMBERS:
        return PLAN_CN_NUMBERS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in PLAN_CN_NUMBERS:
        return 10 + PLAN_CN_NUMBERS[token[1]]
    if token.startswith("二十") and len(token) == 3 and token[2] in PLAN_CN_NUMBERS:
        return 20 + PLAN_CN_NUMBERS[token[2]]
    return 0


def plan_course_matches(text: str, courses) -> list:
    """从原话里找课程：标题片段命中越长越可信，英文词（Python/Web）单独加权。"""
    lowered = text.lower()
    scores = []
    for course in courses or []:
        title = str(course.get("title") or "")
        best = 0
        for word in re.findall(r"[A-Za-z][A-Za-z0-9+#]{1,}", title):
            if word.lower() in lowered:
                best = max(best, len(word) + 2)
        for start in range(len(title)):
            for end in range(start + 3, min(len(title), start + 8) + 1):
                if title[start:end] in text:
                    best = max(best, end - start)
        if best >= 3:
            scores.append((best, course["id"]))
    if not scores:
        return []
    top = max(score for score, _ in scores)
    return [course_id for score, course_id in scores if score >= max(3, top - 1)][:4]


def parse_plan_text(text, courses, moment=None):
    """规则解析：期限、每周可投入、可学天数、目标课程、强度。不调用模型也能出结果。"""
    now = moment or datetime.now(timezone.utc)
    today = local_day(now)
    raw = re.sub(r"\s+", " ", str(text or "")).strip()[:300]
    notes = []
    weeks = 0
    match = re.search(r"(\d+|[一两二三四五六七八九十]+)\s*(?:个)?\s*(周|星期|礼拜)", raw)
    if match:
        weeks = plan_number(match.group(1))
    if not weeks:
        match = re.search(r"(\d+|[一两二三四五六七八九十]+)\s*个月", raw)
        if match:
            weeks = plan_number(match.group(1)) * 4
    if not weeks:
        match = re.search(r"(\d+|[一两二三四五六七八九十]+)\s*天", raw)
        if match:
            weeks = max(1, math.ceil(plan_number(match.group(1)) / 7))
    if not weeks:
        weeks = PLAN_DEFAULT_WEEKS
        notes.append(f"没识别到期限，先按 {PLAN_DEFAULT_WEEKS} 周估算")
    weeks = max(1, min(int(weeks), PLAN_MAX_WEEKS))

    weekly = 0
    match = re.search(r"每\s*(?:周|星期|礼拜)[^0-9]{0,6}(\d+(?:\.\d+)?)\s*(?:个)?\s*(小时|分钟|分|h|H|min)", raw)
    if match:
        unit = match.group(2)
        weekly = float(match.group(1)) * (60 if unit in ("小时", "h", "H") else 1)
    else:
        match = re.search(r"每\s*天[^0-9]{0,6}(\d+(?:\.\d+)?)\s*(?:个)?\s*(小时|分钟|分|h|H|min)", raw)
        if match:
            unit = match.group(2)
            weekly = float(match.group(1)) * (60 if unit in ("小时", "h", "H") else 1) * 7
            notes.append("按每天的可投入折算成每周")
    if not weekly:
        weekly = PLAN_DEFAULT_WEEKLY_MINUTES
        notes.append(f"没识别到每周可投入，先按每周 {PLAN_DEFAULT_WEEKLY_MINUTES} 分钟估算")
    weekly = int(max(PLAN_MIN_WEEKLY_MINUTES, min(round(weekly), PLAN_MAX_WEEKLY_MINUTES)))

    allowed = 7
    weekdays = plan_weekdays_for(7)
    if re.search(r"周末[^。，,]{0,4}(?:不|没|不能|无法)|只在?工作日|工作日学", raw):
        allowed = 5
        weekdays = [0, 1, 2, 3, 4]
    elif re.search(r"(?:只|就|仅)[能可在]?\s*(?:在)?\s*周末|只有周末|仅周末", raw):
        allowed = 2
        weekdays = [5, 6]

    intensity = "standard"
    if re.search(r"掌握|熟练|做项目|深入|精通|能写出", raw):
        intensity = "deep"
    elif re.search(r"了解|过一遍|预习|随便看看|随便学", raw):
        intensity = "light"

    course_ids = plan_course_matches(raw, courses)
    if not course_ids:
        notes.append("没匹配到具体课程，按你已加入的教学班全部课程计算")

    return {"text": raw, "weeks": int(weeks), "weekly_minutes": int(weekly),
            "allowed_days": int(allowed), "allowed_weekdays": weekdays, "intensity": intensity,
            "daily_capacity": int(min(weekly / allowed, PLAN_DAILY_CAP_MINUTES)),
            "course_ids": course_ids, "start": today.isoformat(),
            "deadline": (today + timedelta(days=weeks * 7)).isoformat(),
            "source": "rules", "notes": notes}


def _visible_pending_reviews(data, student_id, submissions):
    assignments = {item["id"]: item for item in (data.get("assignments") or [])}
    return [record for record in (data.get("wrong_questions") or [])
            if record.get("student_id") == student_id and record.get("status") != "mastered"
            and record_answers_visible(record, assignments, submissions)]


def estimate_plan_workload(data, student, courses):
    """把"学什么"折算成分钟：资料按字数（带兜底）、知识点、单选、编程、到期复习分别计时。"""
    sid = student["id"]
    submissions = [item for item in (data.get("submissions") or []) if item.get("student_id") == sid]
    answered = {detail.get("question_id") for item in submissions
                for detail in (item.get("details") or []) if detail.get("question_id")}
    reading = (data.get("reading_progress") or {}).get(sid) or {}
    items = []
    for course in courses or []:
        cid = course.get("id")
        for resource in course.get("resources") or []:
            body = str(resource.get("content") or "")
            if not body.strip():
                continue  # 没有正文的资料没有阅读上报点，排进计划就永远完不成
            if int((reading.get(resource.get("id")) or {}).get("percent") or 0) >= READING_THRESHOLD:
                continue
            minutes = PLAN_ESTIMATE["resource_base"] + max(
                0.0, (len(body) - 300) / PLAN_ESTIMATE["chars_per_minute"])
            items.append({"kind": "resource", "course_id": cid, "ref_id": resource.get("id"),
                          "title": str(resource.get("name") or "学习资料")[:40],
                          "minutes": round(minutes)})
        names = {kp["id"]: kp.get("name") or "未命名知识点" for kp in course.get("knowledge_points") or []}
        questions = course.get("questions") or []
        for kp_id, kp_name in names.items():
            all_ids = [q.get("id") for q in questions if q.get("knowledge_point_id") == kp_id]
            todo = [qid for qid in all_ids if qid not in answered]
            if not todo:
                continue  # 这个知识点的题都做过了，不再当新任务
            if len(todo) == len(all_ids):
                # 全新知识点：一张卡覆盖这个点的全部题，做完这些题才算学完这个点
                items.append({"kind": "knowledge", "course_id": cid, "ref_id": kp_id, "title": kp_name,
                              "point": kp_name, "question_ids": all_ids, "count": len(all_ids),
                              "policy": "all",
                              "minutes": round(PLAN_ESTIMATE["knowledge"]
                                               + len(all_ids) * PLAN_ESTIMATE["choice"])})
            else:
                # 已经开始过：只补剩下的题，不重复占用知识点卡的额度
                items.append({"kind": "practice", "course_id": cid, "ref_id": kp_id,
                              "title": f"{kp_name} · {len(todo)} 题", "point": kp_name,
                              "question_ids": todo, "count": len(todo),
                              "minutes": round(len(todo) * PLAN_ESTIMATE["choice"])})
        for question in course.get("programming_questions") or []:
            if question.get("id") in answered:
                continue
            items.append({"kind": "code", "course_id": cid, "ref_id": question.get("id"),
                          "title": str(question.get("title") or "编程题")[:40],
                          "minutes": round(PLAN_ESTIMATE["code"])})
    wrong = _visible_pending_reviews(data, sid, submissions)
    for record in wrong[:60]:
        text = (record.get("question_snapshot") or {}).get("text") or "错题复习"
        due_day = local_day(record.get("next_review_at")) if record.get("next_review_at") else None
        items.append({"kind": "review", "course_id": record.get("course_id"), "ref_id": record.get("id"),
                      "title": str(text)[:30], "point": str(record.get("knowledge_point_name") or ""),
                      "due": due_day.isoformat() if due_day else "",
                      "minutes": round(PLAN_ESTIMATE["review"])})
    counts, by_kind = {}, {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + item["minutes"]
    return {"items": items, "minutes": sum(item["minutes"] for item in items),
            "by_kind": by_kind, "counts": counts}


def plan_verdict(workload_minutes, weekly_minutes, weeks):
    """工作量 / 预算：偏松、匹配、偏紧三档，并给出能照着改的建议。"""
    budget = max(1, int(weekly_minutes) * int(weeks))
    ratio = workload_minutes / budget
    if ratio < PLAN_LOOSE_RATIO:
        verdict = "loose"
        advice = (f"目标偏松：{workload_minutes} 分钟的工作量只占 {budget} 分钟预算的 "
                  f"{round(ratio * 100)}%，可以扩到更多课程，或者把编程题重做一遍。")
    elif ratio <= PLAN_TIGHT_RATIO:
        verdict = "ok"
        advice = f"目标和容量匹配（{round(ratio * 100)}%），建议留 20% 预算给到期复习。"
    else:
        verdict = "tight"
        advice = (f"工作量超出预算 {round((ratio - 1) * 100)}%，需要缩范围、延长期限，"
                  f"或者把每周可投入调高。")
    return {"budget": budget, "ratio": round(ratio, 2), "verdict": verdict, "advice": advice}


def build_plan_weeks(items, parsed, start_day):
    """按周装填新内容；复习不预分到周，交给 build_plan_days 按遗忘曲线到期日投放。"""
    weekly = max(1, int(parsed.get("weekly_minutes") or PLAN_DEFAULT_WEEKLY_MINUTES))
    weeks_count = max(1, int(parsed.get("weeks") or PLAN_DEFAULT_WEEKS))
    others = [item for item in items if item["kind"] != "review"]
    plans, cursor = [], 0
    for index in range(weeks_count):
        begin = start_day + timedelta(days=index * 7)
        slots, minutes = [], 0
        while cursor < len(others) and minutes + others[cursor]["minutes"] <= weekly:
            item = others[cursor]
            slots.append(item)
            minutes += item["minutes"]
            cursor += 1
        if not slots and cursor < len(others):  # 单条就超过周预算，也要排进去，别卡死
            item = others[cursor]
            slots.append(item)
            minutes += item["minutes"]
            cursor += 1
        if slots:
            plans.append({"index": index + 1, "start": begin.isoformat(),
                          "end": (begin + timedelta(days=6)).isoformat(),
                          "minutes": minutes, "items": slots})
        if cursor >= len(others):
            break
    return plans


def plan_day_activity(data, student, today):
    """今天的完成证据：全部从已有事件和提交记录派生，不加新埋点。"""
    sid = student["id"]

    def same_day(stamp):
        return bool(stamp) and local_day(stamp) == today

    events = [item for item in (data.get("learning_events") or [])
              if item.get("student_id") == sid and same_day(item.get("ts"))]
    read = {item.get("ref_id") for item in events if item.get("type") == "resource_read"}
    reviewed = {item.get("ref_id") for item in events if item.get("type") == "review"}
    answers, code_ids = {}, set()
    for submission in data.get("submissions") or []:
        if submission.get("student_id") != sid or not same_day(submission.get("submitted_at")):
            continue
        if submission.get("type") == "python":
            code_ids.add(submission.get("question_id"))
        for detail in submission.get("details") or []:
            qid = detail.get("question_id")
            if qid:
                answers[qid] = bool(answers.get(qid)) or bool(detail.get("correct"))
    return {"read": read, "reviewed": reviewed, "answered": answers, "code": code_ids}


def plan_task_done(card, activity):
    """单张任务卡是否完成；按卡的类型查对应的证据。"""
    kind = card.get("kind")
    if kind == "resource":
        return card.get("ref_id") in activity["read"]
    if kind == "review":
        return card.get("ref_id") in activity["reviewed"]
    if kind == "practice":
        ids = card.get("question_ids") or []
        return bool(ids) and all(qid in activity["answered"] for qid in ids)
    if kind == "knowledge":
        ids = card.get("question_ids") or []
        if card.get("policy") == "all":
            return bool(ids) and all(activity["answered"].get(qid) for qid in ids)
        return any(activity["answered"].get(qid) for qid in ids)
    if kind == "code":
        return card.get("ref_id") in activity["code"]
    return False


def plan_today_cards(items, parsed, data, student, saved, today, days=None):
    """今日任务：先看逐日安排里今天排了什么，排不上就按日容量从剩余任务里挑；当天生成后复用。"""
    tasks = saved.setdefault("tasks", {})
    key = today.isoformat()
    cards = tasks.get(key)
    # 旧口径的卡（知识点只要求做一题、复习不带到期日）在今天重算一次，之后当天不再变
    stale = bool(cards) and any(
        (card.get("kind") == "knowledge" and card.get("policy") != "all")
        or (card.get("kind") == "review" and "due" not in card)
        for card in cards)
    if stale:
        cards = None
    activity = plan_day_activity(data, student, today)
    if cards and any(card["kind"] == "review" for card in cards):
        # A cached daily plan can predate answer-visibility checks.
        reviews = _visible_pending_reviews(data, student["id"], data.get("submissions") or [])
        visible_ids = {record["id"] for record in reviews} | activity["reviewed"]
        cards = [card for card in cards if card["kind"] != "review" or card["ref_id"] in visible_ids]
        tasks[key] = cards
    if not cards:
        scheduled = next((entry for entry in (days or []) if entry["date"] == key), None)
        picked, spent = [], 0
        if scheduled:
            picked = [{**item, "id": f"{item['kind']}:{item['ref_id']}"} for item in scheduled["items"]]
            spent = sum(int(item["minutes"]) for item in picked)
        if not picked:
            capacity = int(min((parsed.get("weekly_minutes") or PLAN_DEFAULT_WEEKLY_MINUTES)
                               / max(1, parsed.get("allowed_days") or 7), PLAN_DAILY_CAP_MINUTES))
            due_reviews = [entry for entry in items
                           if entry["kind"] == "review" and (entry.get("due") or "") <= key]
            for item in due_reviews[:3]:
                picked.append({**item, "id": f"{item['kind']}:{item['ref_id']}"})
                spent += item["minutes"]
            for item in items:
                if item["kind"] == "review":
                    continue
                if spent and spent + item["minutes"] > capacity:
                    break
                picked.append({**item, "id": f"{item['kind']}:{item['ref_id']}"})
                spent += item["minutes"]
                if spent >= capacity:
                    break
        cards = picked
        tasks[key] = cards
        for old in sorted(tasks)[:-14]:  # 只留最近 14 天，别让 study_plans 无限长
            tasks.pop(old, None)
    view = [{**card, "done": plan_task_done(card, activity)} for card in cards]
    done_count = sum(1 for card in view if card["done"])
    return {"date": key, "cards": view, "total": len(view), "done": done_count,
            "planned_minutes": sum(card["minutes"] for card in view),
            "done_minutes": sum(card["minutes"] for card in view if card["done"]),
            "rate": int(round(done_count / len(view) * 100)) if view else 0,
            "counts": plan_item_counts(view)}


def study_plan_goal_view(data, student, courses, moment=None):
    """GET /api/student/plan 的计划部分：目标、工作量、诊断、每周任务、逐日安排与今日任务。"""
    today = local_day(moment)
    saved = (data.get("study_plans") or {}).get(student["id"]) or {}
    goal = saved.get("goal")
    if not goal:
        return {"goal": None, "tasks": None, "weeks": [], "week": None,
                "workload": None, "verdict": None}
    parsed = goal.get("parsed") or {}
    scope = [course for course in (courses or [])
             if not parsed.get("course_ids") or course.get("id") in parsed["course_ids"]]
    if not scope:
        scope = list(courses or [])
    workload = estimate_plan_workload(data, student, scope)
    verdict = plan_verdict(workload["minutes"],
                           parsed.get("weekly_minutes") or PLAN_DEFAULT_WEEKLY_MINUTES,
                           parsed.get("weeks") or PLAN_DEFAULT_WEEKS)
    start = local_day(parsed.get("start")) if parsed.get("start") else today
    weeks = build_plan_weeks(workload["items"], parsed, start)
    reviews_todo = [item for item in workload["items"] if item["kind"] == "review"]
    days = build_plan_days(weeks, parsed, reviews_todo)
    current = next((plan for plan in weeks
                    if plan["start"] <= today.isoformat() <= plan["end"]), None)
    if current is None:
        current = weeks[0] if weeks else None
    tasks = plan_today_cards(workload["items"], parsed, data, student, saved, today, days)
    week_days = plan_week_calendar(current, days, parsed, data, student, saved) if current else []
    progress = plan_week_progress(week_days, today)
    # 周级统计按「实际摊到日期的卡片」聚合，复习才不会被漏算
    week_items = {plan["index"]: [item for entry in days if entry["week"] == plan["index"]
                                  for item in entry["items"]] for plan in weeks}
    return {"goal": {"text": goal.get("text"), "parsed": parsed,
                     "created_at": goal.get("created_at")},
            "workload": {"minutes": workload["minutes"], "by_kind": workload["by_kind"],
                         "counts": workload["counts"]},
            "verdict": verdict,
            "weeks": [{"index": plan["index"], "start": plan["start"], "end": plan["end"],
                       "minutes": plan_item_counts(week_items.get(plan["index"], []))["minutes"],
                       "count": len(week_items.get(plan["index"], [])),
                       "summary": plan_item_counts(week_items.get(plan["index"], []))}
                      for plan in weeks],
            "week": None if current is None else
                    {"index": current["index"], "start": current["start"], "end": current["end"],
                     "minutes": plan_item_counts(week_items.get(current["index"], []))["minutes"],
                     "count": len(week_items.get(current["index"], [])),
                     "summary": plan_item_counts(week_items.get(current["index"], [])),
                     "days": week_days, "progress": progress},
            "tasks": tasks}


def portrait_payload(data, student, own_profile=None, moment=None, courses=None):
    """学生画像：投入度四分量 + 五个维度，全部由学习事件与错题调度现算。

    缺数据的维度返回 None 而不是 0——没有抢答场次不等于"从不参与"。
    """
    if moment is None:
        now = datetime.now(timezone.utc)
    elif isinstance(moment, datetime):
        now = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    else:
        now = datetime.fromisoformat(str(moment))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    now_stamp = now.isoformat()
    sid = student["id"]
    today = local_day(now)
    events = [item for item in (data.get("learning_events") or []) if item.get("student_id") == sid]
    days = activity_days(data, sid)
    saved = (data.get("study_plans") or {}).get(sid) or {}
    goals = {"weekly_submissions": int(saved.get("weekly_submissions") or STUDY_PLAN_DEFAULT["weekly_submissions"]),
             "weekly_reviews": int(saved.get("weekly_reviews") or STUDY_PLAN_DEFAULT["weekly_reviews"])}
    week_start = today - timedelta(days=today.weekday())
    week = [week_start + timedelta(days=offset) for offset in range(7)]
    week_subs = sum(int((days.get(day.isoformat()) or {}).get("submissions", 0)) for day in week)
    week_revs = sum(int((days.get(day.isoformat()) or {}).get("reviews", 0)) for day in week)
    active_30 = 0
    for offset in range(STUDY_CALENDAR_DAYS):
        entry = days.get((today - timedelta(days=offset)).isoformat()) or {}
        if entry.get("submissions") or entry.get("reviews"):
            active_30 += 1
    streak_current, streak_longest = study_streak(days, today)

    submissions = [item for item in (data.get("submissions") or []) if item.get("student_id") == sid]
    submissions.sort(key=lambda item: item.get("submitted_at") or "")
    scores = [int(item.get("score") or 0) for item in submissions]
    low_scores = sum(1 for value in scores if value < 60)

    answered, wronged = Counter(), Counter()
    for item in submissions:
        for detail in item.get("details") or []:
            point = detail.get("knowledge_point_name") or "未分类"
            answered[point] += 1
            if not detail.get("correct"):
                wronged[point] += 1
    weak = [{"name": point, "wrong": count, "answered": answered[point],
             "rate": round(count / answered[point] * 100)}
            for point, count in wronged.items() if answered[point]]
    weak.sort(key=lambda item: (-item["rate"], -item["wrong"]))
    weak = weak[:3]
    knowledge_rows = knowledge_schedule(submissions, now)
    due_points = [item for item in knowledge_rows if item["due"]]
    rhythm = rhythm_payload(events, submissions, now)
    plan_view = study_plan_goal_view(data, student, courses)
    plan_bits = (plan_view.get("week") or {}).get("progress") or {}
    plan_active = bool(plan_view.get("goal"))
    plan_state = plan_progress(days, goals, today, plan={
        "active": plan_active, "rate": plan_bits.get("rate") or 0,
        "planned": plan_bits.get("planned") or 0,
        "done": plan_bits.get("done") or 0})
    reading = reading_payload(data, sid, courses)

    wrong_records = [item for item in (data.get("wrong_questions") or []) if item.get("student_id") == sid]
    unresolved = [item for item in wrong_records if item.get("status") != "mastered"]
    mastered = len(wrong_records) - len(unresolved)
    due = [item for item in unresolved if (item.get("next_review_at") or "") <= now_stamp]
    hard = [item for item in unresolved if int(item.get("wrong_count") or 0) >= 3]
    ease = [float(item.get("ease_factor") or 0) for item in unresolved if item.get("ease_factor")]
    cutoff = (now + timedelta(days=7)).isoformat()
    soon = [item for item in unresolved if now_stamp <= (item.get("next_review_at") or "") <= cutoff]

    quizzes = [item for item in (data.get("live_quizzes") or []) if item.get("status") == "closed"]
    joined = [item for item in quizzes
              if any(answer.get("student_id") == sid for answer in item.get("answers") or [])]

    parts = [
        {"id": "activity", "label": "活跃天数", "weight": 35,
         "value": round(active_30 / STUDY_CALENDAR_DAYS * 100),
         "detail": f"近 {STUDY_CALENDAR_DAYS} 天有 {active_30} 天学习"},
        {"id": "goal", "label": "本周计划完成", "weight": 25,
         "value": (int(plan_bits.get("rate") or 0) if plan_active and plan_bits.get("planned")
                   else round((plan_rate(week_subs, goals["weekly_submissions"]) +
                               plan_rate(week_revs, goals["weekly_reviews"])) / 2)),
         "detail": (f"计划 {plan_bits.get('done') or 0}/{plan_bits.get('planned') or 0} 项任务，按每日任务回算"
                    if plan_active else
                    f"练习 {week_subs}/{goals['weekly_submissions']} · 复习 {week_revs}/{goals['weekly_reviews']}")},
        {"id": "review", "label": "复习履约", "weight": 20,
         "value": round(100 - len(due) / len(unresolved) * 100) if unresolved else None,
         "detail": f"待复习 {len(unresolved)} 题，其中 {len(due)} 题已逾期" if unresolved else "暂无待复习题目"},
        {"id": "live", "label": "课堂参与", "weight": 20,
         "value": round(len(joined) / len(quizzes) * 100) if quizzes else None,
         "detail": f"已结束 {len(quizzes)} 场，参与 {len(joined)} 场" if quizzes else "暂无已结束的抢答"},
    ]
    known = [item for item in parts if item["value"] is not None]
    weight = sum(item["weight"] for item in known)
    investment = {"score": round(sum(item["value"] * item["weight"] for item in known) / weight) if weight else None,
                  "parts": parts}

    dimensions = [
        {"id": "score", "label": "成绩结果",
         "value": f"{(own_profile or {}).get('average_score')} 分" if (own_profile or {}).get("average_score") is not None else "暂无",
         "detail": (f"最近 {len(scores[-5:])} 次：{'、'.join(str(value) for value in scores[-5:])}；低于 60 分 {low_scores} 次"
                    if scores else "还没有正式提交")},
        {"id": "knowledge", "label": "知识结构",
         "value": f"{len(weak)} 个薄弱点" if weak else "暂无",
         "detail": "、".join(f"{item['name']}（错 {item['wrong']}/{item['answered']}，{item['rate']}%）" for item in weak)
         or "完成练习后按知识点统计错误率"},
        {"id": "review", "label": "复习健康度",
         "value": f"{len(unresolved)} 题待复习",
         "detail": (f"已掌握 {mastered} 题 · 逾期 {len(due)} 题 · 反复错 {len(hard)} 题"
                    f" · 平均倍率 {round(sum(ease) / len(ease), 2) if ease else '—'}"
                    f" · 未来 7 天 {len(soon)} 题")},
        {"id": "habit", "label": "学习习惯",
         "value": f"连续 {streak_current} 天",
         "detail": f"历史最长 {streak_longest} 天 · 近 30 天活跃 {active_30} 天"},
        {"id": "live", "label": "课堂参与",
         "value": f"{len(joined)}/{len(quizzes)} 场" if quizzes else "暂无",
         "detail": "抢答只作参与度参考，不计入课程成绩" if quizzes else "还没有已结束的抢答"},
    ]

    categories = [
        {"id": "score", "label": "成绩结果",
         "value": (f"{(own_profile or {}).get('average_score')} 分"
                   if (own_profile or {}).get("average_score") is not None else "暂无"),
         "detail": (f"最近 {len(scores[-5:])} 次：{'、'.join(str(value) for value in scores[-5:])}"
                    f"；低于 60 分 {low_scores} 次" if scores else "还没有正式提交")},
        {"id": "knowledge", "label": "知识状态",
         "value": f"{len(knowledge_rows)} 个知识点" if knowledge_rows else "暂无",
         "detail": (f"薄弱点 {len(weak)} 个 · 已掌握 {sum(1 for item in knowledge_rows if item['mastered'])} 个"
                    f" · 待复习 {len(due_points)} 个" if knowledge_rows else "完成练习后按知识点统计")},
        {"id": "investment", "label": "学习投入",
         "value": f"{investment['score']} 分" if investment["score"] is not None else "暂无",
         "detail": (f"活跃 {parts[0]['value']} · 目标 {parts[1]['value']}"
                    f" · 履约 {parts[2]['value'] if parts[2]['value'] is not None else '—'}"
                    f" · 课堂 {parts[3]['value'] if parts[3]['value'] is not None else '—'}")},
        {"id": "rhythm", "label": "学习节律",
         "value": rhythm["label"],
         "detail": (f"常学时段 {rhythm['peak']}（占 {rhythm['peak_share']}%）"
                    f" · 周末 {rhythm['weekend_ratio']}% · 迟交 {rhythm['late_count']} 次"
                    if rhythm["dated_events"] else "开始学习后自动统计时段偏好")},
        {"id": "review", "label": "复习健康",
         "value": f"{len(unresolved)} 题待复习",
         "detail": (f"已掌握 {mastered} 题 · 逾期 {len(due)} 题 · 反复错 {len(hard)} 题"
                    f" · 平均倍率 {round(sum(ease) / len(ease), 2) if ease else '—'}")},
        {"id": "meta", "label": "元认知与计划",
         "value": f"本周 {plan_state['current_rate']}%",
         "detail": (f"已结束 {plan_state['closed_count']} 周中达标 {plan_state['met_count']} 周"
                    + (f" · 本周计划 {plan_bits.get('done') or 0}/{plan_bits.get('planned') or 0} 项任务"
                       if plan_active else
                       f" · 备用目标 {goals['weekly_submissions']} 次练习 / {goals['weekly_reviews']} 次复习"))},
        {"id": "reading", "label": "资料阅读",
         "value": (f"{reading['done']}/{reading['total']} 份" if reading["total"] else "暂无资料"),
         "detail": (f"读到 {READING_THRESHOLD}% 才算读过 · 完成度 {reading['percent']}%"
                    if reading["total"] else "课程发布学习资料后开始统计")},
        {"id": "live", "label": "课堂参与",
         "value": f"{len(joined)}/{len(quizzes)} 场" if quizzes else "暂无",
         "detail": "抢答只作参与度参考，不计入课程成绩" if quizzes else "还没有已结束的抢答"},
    ]

    if not submissions:
        headline = "还没有正式练习记录。完成一份练习后，画像会从提交、复习和课堂互动一起长出来。"
    else:
        headline = f"本周计划完成率 {parts[1]['value']}%，{len(unresolved)} 题待复习（{len(due)} 题已逾期）"
        if weak:
            headline += f"，薄弱点在{'、'.join(item['name'] for item in weak[:2])}"
        headline += "。"

    last7 = [item for item in events if (item.get("ts") or "") >= (now - timedelta(days=7)).isoformat()]
    kinds = Counter(item.get("type") for item in events)
    return {
        "identity": {"student_id": sid, "name": student["name"], "major_id": student.get("major_id")},
        "headline": headline,
        "investment": investment,
        "dimensions": dimensions,
        "weak_points": weak,
        "categories": categories,
        "knowledge_points": knowledge_rows[:6],
        "rhythm": rhythm,
        "metacognition": {"current_rate": plan_state["current_rate"],
                          "met_count": plan_state["met_count"],
                          "closed_count": plan_state["closed_count"],
                          "has_plan": plan_active,
                          "plan": ({"rate": int(plan_bits.get("rate") or 0),
                                    "planned": int(plan_bits.get("planned") or 0),
                                    "done": int(plan_bits.get("done") or 0)} if plan_active else None),
                          "goals": goals, "weeks": plan_state["weeks"]},
        "reading": reading,
        "review_load": {"due": len(due), "unresolved": len(unresolved), "mastered": mastered,
                        "hard": len(hard), "next7": len(soon),
                        "avg_ease": round(sum(ease) / len(ease), 2) if ease else None},
        "events": {"total": len(events), "last7": len(last7),
                   "by_type": [{"type": kind, "label": EVENT_LABELS.get(kind, kind), "count": count}
                               for kind, count in kinds.most_common()]},
        "scope": ("只统计正式练习提交、错题复习、已结束的课堂抢答与学习资料阅读"
                  f"（读到 {READING_THRESHOLD}%）；打开页面本身不计入"),
    }


def _parse_datetime(value, label="时间"):
    """Parse browser datetime-local/ISO values into an aware UTC datetime."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HTTPException(400, f"{label}格式不正确")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"{label}格式不正确") from None
    if parsed.tzinfo is None:
        # datetime-local has no zone; interpret it in the local classroom timezone.
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat()


def _is_retired(item):
    """Retired content stays in the course for history, but is hidden from students."""
    return isinstance(item, dict) and item.get("status") == "retired"


def _active_questions(course):
    return [question for question in course.get("questions", []) if not _is_retired(question)]


def _active_programming(course):
    return [question for question in course.get("programming_questions", []) if not _is_retired(question)]


def _course_roster(course, students, _submissions, enrollments):
    """Current class members; historical submissions never imply current membership."""
    return [student for student in students if is_enrolled(course, student, enrollments)]


def _assignment_window(assignment):
    open_at = _parse_datetime(assignment.get("open_at"), "作业开始时间")
    due_at = _parse_datetime(assignment.get("due_at"), "作业截止时间")
    close_at = _parse_datetime(assignment.get("close_at"), "作业关闭时间")
    allow_late = bool(assignment.get("allow_late"))
    try:
        late_max_days = max(0, min(30, int(assignment.get("late_max_days") or 0)))
    except (TypeError, ValueError):
        late_max_days = 0
    if close_at is None and due_at is not None:
        close_at = due_at + timedelta(days=late_max_days) if allow_late else due_at
    return open_at, due_at, close_at


def _assignment_state(assignment, now=None):
    status = assignment.get("status")
    if status in {"draft", "archived"}:
        return status
    if status == "closed":
        return "closed"
    now = now or datetime.now(timezone.utc)
    open_at, due_at, close_at = _assignment_window(assignment)
    if open_at and now < open_at:
        return "upcoming"
    if due_at and now > due_at:
        if assignment.get("allow_late") and close_at and now <= close_at:
            return "late"
        return "closed"
    return "open"


def _assignment_questions(course, assignment):
    return assignment.get("questions", [])


def _pick_submission(records, mode="latest"):
    if not records:
        return None
    if mode == "best":
        return max(records, key=lambda item: (item.get("score") or 0, item.get("submitted_at", "")))
    if mode == "first":
        return min(records, key=lambda item: item.get("submitted_at", ""))
    return max(records, key=lambda item: item.get("submitted_at", ""))


def _student_record_view(record, assignments, submissions):
    if not record.get("assignment_id"):
        return record
    if not record_answers_visible(record, assignments, submissions):
        return {**record, "correct_count": None, "details": [], "answers_locked": True,
                "can_analyze": False, "pending_release": False}
    return {**record, "answers_locked": False, "can_analyze": True}


def _filter_wrong_items(items, course_id=None, knowledge_point=None, chapter=None, knowledge_point_id=None):
    """错题本按课程、章节、知识点过滤；"all" 或空值表示不过滤。"""
    if course_id and course_id != "all":
        items = [item for item in items if item.get("course_id") == course_id]
    if knowledge_point and knowledge_point != "all":
        items = [item for item in items if (item.get("knowledge_point_name") or "未分类") == knowledge_point]
    if knowledge_point_id:
        items = [item for item in items if item.get("knowledge_point_id") == knowledge_point_id]
    if chapter and chapter != "all":
        items = [item for item in items if (item.get("chapter_title") or "未分章节") == chapter]
    return items


def _wrong_answer_label(snapshot):
    options = snapshot.get("options") or []
    answer = snapshot.get("answer")
    if isinstance(answer, int) and 0 <= answer < len(options):
        return f"{chr(65 + answer)}. {options[answer]}"
    return ""




def _late_metrics(assignment, now):
    _, due_at, _ = _assignment_window(assignment)
    if not due_at or now <= due_at:
        return False, 0, 0
    days = max(1, math.ceil((now - due_at).total_seconds() / 86400))
    try:
        per_day = max(0, min(100, int(assignment.get("late_penalty_percent_per_day") or 0)))
    except (TypeError, ValueError):
        per_day = 0
    return True, days, min(100, per_day * days)


def _announcement_recipients(course, submissions, students, audience, enrollments):
    roster = [student for student in students if is_enrolled(course, student, enrollments)]
    course_records = [record for record in submissions if record.get("course_id") == course["id"]]
    if audience == "all":
        return [student["id"] for student in roster]
    submitted = {record["student_id"] for record in course_records}
    if audience == "non_submitters":
        return [student["id"] for student in roster if student["id"] not in submitted]
    if audience == "at_risk":
        result = []
        for student in roster:
            scores = [record.get("score") for record in course_records
                      if record["student_id"] == student["id"] and isinstance(record.get("score"), (int, float))]
            if not scores or sum(scores) / len(scores) < 60:
                result.append(student["id"])
        return result
    raise HTTPException(400, "请选择有效的公告受众")


class LearningStore:
    """Tiny local JSON store for the week-3 learning-flow demo.

    It deliberately lives beside the extracted-resource jobs so a fresh clone
    needs no database service, while all course and learning actions are real
    persisted writes rather than front-end mock data.
    """
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"courses": [], "submissions": [], "wrong_questions": []}
        if path.is_file():
            # Invalid existing data must fail visibly, never be replaced by demo defaults.
            self.data.update(json.loads(path.read_text(encoding="utf-8")))
            if self.data.get("independent_assignments_version") != 1:
                backup = path.with_name(f"{path.stem}.before-independent-assignments.json")
                if not backup.exists():
                    shutil.copy2(path, backup)
            if self.data.get("demo_teaching_classes_version", 0) < 2:
                backup = path.with_name(f"{path.stem}.before-professional-classes.json")
                if not backup.exists():
                    shutil.copy2(path, backup)
            if not self.data.get("demo_teaching_classes_version"):
                backup = path.with_name(f"{path.stem}.before-teaching-classes.json")
                if not backup.exists():
                    shutil.copy2(path, backup)
            if not self.data.get("course_audiences_version"):
                backup = path.with_name(f"{path.stem}.before-v0.5.json")
                if not backup.exists():
                    shutil.copy2(path, backup)
        if not self.data["courses"]:
            self.data["courses"] = demo_courses()
            self.data["demo_catalog_version"] = 2
            self.save()
        migrated = False
        if "majors" not in self.data or not isinstance(self.data.get("majors"), list) or not self.data["majors"]:
            self.data["majors"] = [dict(m) for m in DEFAULT_MAJORS]
            migrated = True
        if "wrong_questions" not in self.data:
            self.data["wrong_questions"] = []
            migrated = True
        for key in ("assignments", "assignment_attempts", "announcements"):
            if key not in self.data or not isinstance(self.data.get(key), list):
                self.data[key] = []
                migrated = True
        if not isinstance(self.data.get("study_plans"), dict):
            self.data["study_plans"] = {}
            migrated = True
        for course in self.data["courses"]:
            if "teacher_id" not in course:
                course["teacher_id"] = DEMO_TEACHER
                migrated = True
            if "major_ids" not in course:
                legacy_major = course.get("major_id")
                valid = {major["id"] for major in self.data["majors"]}
                course["major_ids"] = [legacy_major] if legacy_major in valid else list(valid)
                migrated = True
            if "major_id" in course:
                course.pop("major_id")
                migrated = True
            if "questions" not in course or not isinstance(course["questions"], list):
                course["questions"] = []
                migrated = True
            if "resources" not in course or not isinstance(course["resources"], list):
                course["resources"] = []
                migrated = True
            if "knowledge_points" not in course or not isinstance(course["knowledge_points"], list):
                course["knowledge_points"] = [dict(kp) for kp in DEFAULT_KNOWLEDGE_POINTS] if course["id"] == "course-demo" else []
                migrated = True
            # 旧课程无章节：自动建"第 1 章"，题目与资源全部归入。
            if "chapters" not in course or not isinstance(course["chapters"], list) or not course["chapters"]:
                course["chapters"] = [{"id": "ch-1", "title": "第 1 章", "order": 1}]
                for question in course["questions"]:
                    question["chapter_id"] = "ch-1"
                for resource in course["resources"]:
                    resource["chapter_id"] = "ch-1"
                migrated = True
            else:
                default_chapter = course["chapters"][0]["id"]
                for question in course["questions"]:
                    if "chapter_id" not in question:
                        question["chapter_id"] = default_chapter
                        migrated = True
                for resource in course["resources"]:
                    if "chapter_id" not in resource:
                        resource["chapter_id"] = default_chapter
                        migrated = True
            kp_ids = {kp["id"] for kp in course["knowledge_points"]}
            for question in course["questions"]:
                if "knowledge_point_id" not in question:
                    question["knowledge_point_id"] = DEFAULT_KP_MAP.get(question["id"]) if course["id"] == "course-demo" and DEFAULT_KP_MAP.get(question["id"]) in kp_ids else None
                    migrated = True
        for record in self.data["submissions"]:
            if "student_id" not in record:
                record["student_id"] = DEMO_STUDENT
                migrated = True
        for record in self.data.setdefault("wrong_questions", []):
            if "review_interval_days" not in record:
                record["review_interval_days"] = 1
                record["review_count"] = 0
                record["first_wrong_at"] = record.get("last_wrong_at", self.now())
                # 旧记录没有调度数据：默认立即进入复习队列。
                record["next_review_at"] = record.get("last_wrong_at", self.now())
                migrated = True
        # 学习打卡：首次按真实事件回填历史活动，之后由提交/复习增量累加。
        if self.data.get("study_activity_version") != 1:
            self.data["study_activity"] = {}
            for record in self.data["submissions"]:
                if record.get("submitted_at"):
                    bump_study_activity(self.data, record["student_id"], "submissions", record["submitted_at"])
            for record in self.data.setdefault("wrong_questions", []):
                # 旧记录只保留“最近一次复习时间”，更早的复习日无法还原，按一次计入。
                if record.get("review_count") and record.get("last_reviewed_at"):
                    bump_study_activity(self.data, record["student_id"], "reviews", record["last_reviewed_at"])
            self.data["study_activity_version"] = 1
        # 学习事件首次回填：从历史提交、错题复习与抢答重建明细，之后由写入点追加。
        if self.data.get("learning_events_version") != 1:
            events = []
            for record in self.data["submissions"]:
                if not record.get("submitted_at"):
                    continue
                events.append({"id": uuid.uuid4().hex[:12], "ts": record["submitted_at"],
                               "student_id": record.get("student_id"), "course_id": record.get("course_id"),
                               "type": "code_submit" if record.get("type") == "python" else "practice_submit",
                               "ref_id": record.get("id"), "correct": None,
                               "duration_seconds": int(record.get("duration_seconds") or 0),
                               "source": "course"})
            for record in self.data.setdefault("wrong_questions", []):
                if record.get("review_count") and record.get("last_reviewed_at"):
                    events.append({"id": uuid.uuid4().hex[:12], "ts": record["last_reviewed_at"],
                                   "student_id": record.get("student_id"), "course_id": record.get("course_id"),
                                   "type": "review", "ref_id": record.get("id"), "correct": None,
                                   "duration_seconds": 0, "source": "course"})
            for quiz in self.data.get("live_quizzes") or []:
                for answer in quiz.get("answers") or []:
                    events.append({"id": uuid.uuid4().hex[:12], "ts": answer.get("submitted_at"),
                                   "student_id": answer.get("student_id"), "course_id": quiz.get("course_id"),
                                   "type": "live_answer", "ref_id": quiz.get("id"),
                                   "correct": answer.get("correct"),
                                   "duration_seconds": int((answer.get("latency_ms") or 0) / 1000),
                                   "source": "live"})
            self.data["learning_events"] = sorted(events, key=lambda item: item["ts"] or "")
            self.data["learning_events_version"] = 1
            migrated = True
            migrated = True
        if upgrade_catalog(self.data):
            migrated = True
        if upgrade_programming_samples(self.data):
            migrated = True
        if upgrade_teaching_samples(self.data):
            migrated = True
        if upgrade_assignment_titles(self.data):
            migrated = True
        if upgrade_practice_solutions(self.data):
            migrated = True
        if upgrade_independent_assignments(self.data):
            migrated = True
        if self.data.get("course_audiences_version") != 1:
            self.data["course_audiences_version"] = 1
            migrated = True
        if migrated:
            self.save()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def public_course(self, course, include_answers=False, bank_answers=False):
        result = dict(course)
        result.pop("knowledge_graph", None)
        result["student_count"] = sum(row.get("course_id") == course["id"] and row.get("status", "active") == "active"
                                      for row in self.data.get("enrollments", []))
        result["resources"] = [dict(item) for item in course["resources"]]
        # Retired questions remain visible to the owning teacher for history and restore,
        # but are removed from every student-facing payload.
        result["questions"] = [dict(item) for item in course["questions"]
                               if include_answers or not _is_retired(item)]
        result["programming_questions"] = [dict(item) for item in course.get("programming_questions", [])
                                           if include_answers or not _is_retired(item)]
        if not include_answers:
            for question in result["programming_questions"]:
                question.pop("test_cases", None)
            for question in result["questions"]:
                if not bank_answers:
                    question.pop("answer", None)
                    question.pop("explanation", None)
                question.pop("ai_source", None)
        return result

    def courses(self, include_drafts=False, include_answers=False):
        with self.lock:
            items = self.data["courses"] if include_drafts else [c for c in self.data["courses"] if c["status"] == "published"]
            return [self.public_course(course, include_answers, bank_answers=not include_answers) for course in items]

    def course(self, course_id):
        return next((course for course in self.data["courses"] if course["id"] == course_id), None)


def create_app(data_dir: Path | None = None, *, seed_demo_history: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        jobs_root = data_dir or Path(os.environ.get("VIDEO_DEMO_DATA", ROOT / "data" / "jobs"))
        app.state.jobs = JobManager(jobs_root)
        app.state.summaries = SummaryManager(app.state.jobs)
        app.state.quizzes = QuizManager(app.state.jobs, app.state.summaries)
        app.state.transcriptions = TranscriptionManager(app.state.jobs)
        app.state.accounts = AccountStore(jobs_root.parent / "accounts.json")
        app.state.learning = LearningStore(jobs_root.parent / "learning-platform.json")
        app.state.analyses = AnalysisManager(jobs_root.parent / "analyses")
        app.state.code_analyses = AnalysisManager(jobs_root.parent / "programming-analyses")
        app.state.programming = ProgrammingManager(app.state.learning)
        app.state.live = LiveQuizManager(app.state.learning)
        history_enabled = seed_demo_history if seed_demo_history is not None else os.environ.get("VIDEO_DEMO_SEED_HISTORY", "1") != "0"
        if history_enabled:
            if seed_activity(app.state.learning.data, app.state.accounts.users):
                upgrade_independent_assignments(app.state.learning.data)
                app.state.learning.save()
        store = app.state.learning
        if store.data.get("enrollments_version") != 1:
            backup = store.path.with_name(f"{store.path.stem}.before-student-enrollments.json")
            if store.path.exists() and not backup.exists():
                shutil.copy2(store.path, backup)
            if upgrade_enrollments(store.data, app.state.accounts.students()):
                store.save()
        yield
        app.state.programming.close()
        app.state.code_analyses.close()
        app.state.analyses.close()
        app.state.quizzes.close()
        app.state.summaries.close()
        app.state.transcriptions.close()
        app.state.jobs.close()

    app = FastAPI(title="本地视频学习助手 Demo", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = urlparse("http://" + request.headers.get("host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "只允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "不接受其他网站发起的请求"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("x-local-token", ""), TOKEN):
                return JSONResponse({"detail": "页面会话已过期，请刷新页面"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        # 本地 demo 不做缓存：避免浏览器缓存旧版静态资源（如 MIME 修正前的 app.js）。
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/api/config")
    async def config():
        return {"token": TOKEN, "max_bytes": MAX_BYTES, "version": "0.6.0", "local_only": True,
                "features": FEATURES}

    async def json_object(request):
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "请求内容必须是 JSON 对象")
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求内容必须是 JSON 对象")
        return payload

    @app.get("/api/auth/demo-accounts")
    async def demo_accounts(request: Request):
        return request.app.state.accounts.examples()

    @app.post("/api/auth/register", status_code=201)
    async def register(request: Request):
        payload = await json_object(request)
        # 专业组唯一归属：注册的专业组必须存在于预置专业组列表中。
        majors = request.app.state.learning.data.get("majors", [])
        if payload.get("role") == "student" and (not isinstance(payload.get("major_id"), str)
                or payload["major_id"] not in {m["id"] for m in majors}):
            raise HTTPException(400, "请选择有效的专业组")
        return request.app.state.accounts.register(payload)

    @app.get("/api/majors")
    async def majors(request: Request):
        return request.app.state.learning.data.get("majors", [])

    @app.post("/api/auth/login")
    async def login(request: Request):
        return request.app.state.accounts.login(await json_object(request))

    @app.get("/api/auth/me")
    async def me(request: Request):
        return request.app.state.accounts.current(request)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        return request.app.state.accounts.logout(request)

    @app.post("/api/auth/password")
    async def password(request: Request):
        return request.app.state.accounts.change_password(request, await json_object(request))

    @app.get("/api/ai/key")
    async def api_key_status(request: Request):
        if request.app.state.accounts.api_key(request):
            return {"configured": True, "source": "session"}
        try:
            summary_service.read_api_key()
            return {"configured": True, "source": "environment"}
        except SummaryError:
            return {"configured": False, "source": None}

    @app.post("/api/ai/key")
    async def set_api_key(request: Request):
        payload = await json_object(request)
        if not isinstance(payload.get("api_key"), str):
            raise HTTPException(400, "请输入 API 密钥")
        request.app.state.accounts.api_key(request, payload["api_key"])
        return {"configured": True, "source": "session"}

    @app.get("/api/ai/xfyun/credentials")
    async def xfyun_status(request: Request):
        if request.app.state.accounts.xfyun_credentials(request):
            return {"configured": True, "source": "session"}
        try:
            transcription_service.read_credentials()
            return {"configured": True, "source": "environment"}
        except TranscriptionError:
            return {"configured": False, "source": None}

    @app.post("/api/ai/xfyun/credentials")
    async def set_xfyun_credentials(request: Request):
        request.app.state.accounts.xfyun_credentials(request, await json_object(request))
        return {"configured": True, "source": "session"}

    def accepts_student(course, student):
        return is_enrolled(course, student, app.state.learning.data.get("enrollments", []))

    def require_role(request: Request, expected: str):
        user = request.app.state.accounts.current(request)
        if user["role"] != expected:
            raise HTTPException(403, "当前身份没有此操作权限")
        return user

    def owned_course(request, course_id):
        user = require_role(request, "teacher")
        course = request.app.state.learning.course(course_id)
        if not course or course["teacher_id"] != user["id"]:
            raise HTTPException(404, "课程不存在或不属于当前教师")
        return course

    def editable_course(request, course_id):
        course = owned_course(request, course_id)
        if course.get("status") == "archived":
            raise HTTPException(409, "课程已归档，请先恢复课程后再编辑")
        return course

    @app.get("/api/courses")
    async def list_courses(request: Request):
        user = request.app.state.accounts.current(request)
        teacher = user["role"] == "teacher"
        courses = request.app.state.learning.courses(include_drafts=teacher, include_answers=teacher)
        if teacher:
            return [c for c in courses if c["teacher_id"] == user["id"]]
        # 学生只看本专业组内的已发布课程。
        return [c for c in courses if accepts_student(c, user)]

    @app.get("/api/courses/{course_id}")
    async def get_course(request: Request, course_id: str):
        user = request.app.state.accounts.current(request)
        course = request.app.state.learning.course(course_id)
        own = course and user["role"] == "teacher" and course["teacher_id"] == user["id"]
        if not course or (not own and course["status"] != "published"):
            raise HTTPException(404, "课程不存在或尚未发布")
        # 学生跨专业组不可见。
        if not own and user["role"] == "student" and not accepts_student(course, user):
            raise HTTPException(404, "课程不存在或尚未发布")
        return request.app.state.learning.public_course(course, include_answers=bool(own), bank_answers=user["role"] == "student")

    @app.get("/api/courses/{course_id}/preview")
    async def preview_course(request: Request, course_id: str):
        """预览自主练习题库及公开解答，隐藏判题用例和已停用题目。"""
        course = owned_course(request, course_id)
        return request.app.state.learning.public_course(course, include_answers=False, bank_answers=True)

    @app.get("/api/courses/{course_id}/knowledge-graph")
    async def get_knowledge_graph(request: Request, course_id: str):
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        with store.lock:
            if user["role"] == "teacher":
                course = owned_course(request, course_id)
                roster = app.state.accounts.students()
                student_ids = {student["id"] for student in
                               _course_roster(course, roster, store.data["submissions"],
                                              store.data.get("enrollments", []))}
            else:
                course = store.course(course_id)
                if not course or course["status"] != "published" or not accepts_student(course, user):
                    raise HTTPException(404, "课程不存在或尚未发布")
                student_ids = {user["id"]}
            return graph_view(course, store.data, student_ids, teacher=user["role"] == "teacher",
                              get_job=app.state.jobs.get)

    @app.put("/api/courses/{course_id}/knowledge-graph")
    async def save_knowledge_graph(request: Request, course_id: str):
        payload = await json_object(request)
        store = request.app.state.learning
        with store.lock:
            course = editable_course(request, course_id)
            previous = course.get("knowledge_graph")
            revision = previous["revision"] if previous else 0
            if type(payload.get("revision")) is not int:
                raise HTTPException(400, "图谱版本无效，请重新打开图谱")
            if revision != payload["revision"]:
                raise HTTPException(409, "图谱已更新，请刷新后重新编辑")
            try:
                graph = build_graph_update(course, payload, app.state.jobs.get)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            course["knowledge_graph"] = {**graph, "revision": revision + 1}
            try:
                store.save()
            except OSError:
                if previous is None:
                    course.pop("knowledge_graph", None)
                else:
                    course["knowledge_graph"] = previous
                raise HTTPException(507, "图谱保存失败，请检查磁盘后重试") from None
            return {"revision": course["knowledge_graph"]["revision"]}

    def enrollment_view(request, course):
        students = request.app.state.accounts.students()
        store = request.app.state.learning
        with store.lock:
            rows = store.data.get("enrollments", [])
            members = {r["student_id"]: r for r in rows if r.get("course_id") == course["id"]
                       and r.get("status", "active") == "active"}
            related = {c["id"]: c for c in store.data["courses"] if c["id"] != course["id"]
                       and c.get("status") != "archived" and subject_key(c) == subject_key(course)}
            candidates = []
            for student in students:
                if not matches_major(course, student):
                    continue
                other_classes = [{"id": related[r["course_id"]]["id"],
                                  "name": related[r["course_id"]].get("teaching_class") or related[r["course_id"]]["title"],
                                  "teacher": related[r["course_id"]].get("teacher", "")}
                                 for r in rows if r.get("student_id") == student["id"]
                                 and r.get("status", "active") == "active" and r.get("course_id") in related]
                candidates.append({**student, "enrolled": student["id"] in members,
                                   "enrollment_id": members.get(student["id"], {}).get("id"),
                                   "other_classes": other_classes})
            return {"course_id": course["id"], "student_count": len(members),
                    "student_ids": sorted(members), "students": candidates}

    @app.get("/api/courses/{course_id}/enrollments")
    async def get_enrollments(request: Request, course_id: str):
        return enrollment_view(request, owned_course(request, course_id))

    @app.put("/api/courses/{course_id}/enrollments")
    async def update_enrollments(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        students = request.app.state.accounts.students()
        store = request.app.state.learning
        with store.lock:
            before = deepcopy(store.data.get("enrollments", []))
            try:
                replace_roster(store.data, course, students, payload.get("student_ids"))
                store.save()
            except OSError:
                store.data["enrollments"] = before
                raise HTTPException(503, "班级名单未保存，请重试") from None
            except Exception:
                store.data["enrollments"] = before
                raise
        return enrollment_view(request, course)

    @app.patch("/api/courses/{course_id}")
    async def update_course(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        limits = {"title": 80, "description": 300, "category": 40, "level": 40, "duration": 40,
                  "course_code": 40, "teaching_class": 60, "term": 40}
        with request.app.state.learning.lock:
            for field, limit in limits.items():
                if field not in payload:
                    continue
                value = str(payload.get(field, "")).strip()
                if field == "title" and not value:
                    raise HTTPException(400, "请填写课程名称")
                course[field] = value[:limit]
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.delete("/api/courses/{course_id}")
    async def archive_course(request: Request, course_id: str):
        """软下架：学生不可访问，教师历史记录与提交全部保留。"""
        course = owned_course(request, course_id)
        if course.get("status") == "archived":
            return request.app.state.learning.public_course(course, include_answers=True)
        with request.app.state.learning.lock:
            course["archived_from_status"] = course.get("status", "draft")
            course["status"] = "archived"
            course["archived_at"] = LearningStore.now()
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/restore")
    async def restore_course(request: Request, course_id: str):
        course = owned_course(request, course_id)
        if course.get("status") != "archived":
            return request.app.state.learning.public_course(course, include_answers=True)
        with request.app.state.learning.lock:
            previous = course.pop("archived_from_status", "draft")
            course["status"] = previous if previous in {"draft", "published"} else "draft"
            course.pop("archived_at", None)
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/duplicate", status_code=201)
    async def duplicate_course(request: Request, course_id: str):
        source = owned_course(request, course_id)
        now = LearningStore.now()
        with request.app.state.learning.lock:
            clone = deepcopy(source)
            clone["id"] = uuid.uuid4().hex[:12]
            clone["title"] = (source["title"] + "（副本）")[:80]
            clone["course_code"] = ""
            clone["status"] = "draft"
            clone["demo"] = False
            clone["created_at"] = now
            clone["updated_at"] = now
            clone.pop("archived_from_status", None)
            clone.pop("archived_at", None)
            chapter_map = {}
            for chapter in clone.get("chapters", []):
                old_id = chapter["id"]
                chapter["id"] = uuid.uuid4().hex[:10]
                chapter_map[old_id] = chapter["id"]
            for number, chapter in enumerate(clone.get("chapters", []), 1):
                chapter["order"] = number
            kp_map = {}
            for point in clone.get("knowledge_points", []):
                old_id = point["id"]
                point["id"] = uuid.uuid4().hex[:10]
                kp_map[old_id] = point["id"]
            if not clone.get("chapters"):
                clone["chapters"] = [{"id": uuid.uuid4().hex[:10], "title": "第 1 章", "order": 1}]
            first_chapter = clone["chapters"][0]["id"]
            clone["resources"] = []
            resource_map = {}
            for resource in source.get("resources", []):
                item = deepcopy(resource)
                item["id"] = uuid.uuid4().hex[:10]
                resource_map[resource["id"]] = item["id"]
                item["chapter_id"] = chapter_map.get(resource.get("chapter_id"), first_chapter)
                clone["resources"].append(item)
            if "knowledge_graph" in clone:
                graph = clone["knowledge_graph"]
                graph["revision"] = 1
                graph["prerequisites"] = [{"before": kp_map[edge["before"]], "after": kp_map[edge["after"]]}
                                          for edge in graph["prerequisites"]]
                graph["evidence"] = [
                    {**item, "knowledge_point_id": kp_map[item["knowledge_point_id"]],
                     "resource_id": resource_map[item["resource_id"]]}
                    for item in graph["evidence"] if item["resource_id"] in resource_map
                ]
            clone["questions"] = []
            for question in source.get("questions", []):
                if _is_retired(question):
                    continue
                item = deepcopy(question)
                item["id"] = uuid.uuid4().hex[:10]
                item["chapter_id"] = chapter_map.get(question.get("chapter_id"), first_chapter)
                item["knowledge_point_id"] = kp_map.get(question.get("knowledge_point_id"))
                clone["questions"].append(item)
            clone["programming_questions"] = []
            for question in source.get("programming_questions", []):
                if _is_retired(question):
                    continue
                item = deepcopy(question)
                item["id"] = uuid.uuid4().hex[:12]
                item["chapter_id"] = chapter_map.get(question.get("chapter_id"), first_chapter)
                clone["programming_questions"].append(item)
            request.app.state.learning.data["courses"].insert(0, clone)
            request.app.state.learning.save()
        return request.app.state.learning.public_course(clone, include_answers=True)

    def course_audience(request, payload):
        valid = [major["id"] for major in request.app.state.learning.data["majors"]]
        selected = payload.get("major_ids")
        # Old clients may still submit a single major_id; teachers are no longer restricted by it.
        if "major_ids" not in payload:
            selected = [payload["major_id"]] if payload.get("major_id") else valid[:1]
        if (not isinstance(selected, list) or not selected
                or any(not isinstance(mid, str) or mid not in valid for mid in selected)):
            raise HTTPException(400, "请至少选择一个有效的学生专业")
        selected = list(dict.fromkeys(selected))
        if len(selected) != 1:
            raise HTTPException(400, "每个教学班只能对应一个学生专业，请为其他专业另建教学班")
        return selected

    @app.post("/api/courses")
    async def create_course(request: Request):
        user = require_role(request, "teacher")
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写课程名称")
        major_ids = course_audience(request, payload)
        with request.app.state.learning.lock:
            course = {"id": uuid.uuid4().hex[:12], "title": title[:80],
                      "description": str(payload.get("description", "")).strip()[:300],
                      "teacher": user["name"], "teacher_id": user["id"], "status": "draft",
                      "created_at": LearningStore.now(), "updated_at": LearningStore.now(),
                      "major_ids": major_ids,
                      "course_code": str(payload.get("course_code", "")).strip()[:40],
                      "teaching_class": str(payload.get("teaching_class", "")).strip()[:60],
                      "term": str(payload.get("term", "")).strip()[:40],
                      "chapters": [{"id": uuid.uuid4().hex[:10], "title": "第 1 章", "order": 1}],
                      "resources": [], "questions": []}
            request.app.state.learning.data["courses"].insert(0, course)
            request.app.state.learning.save()
            return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/audience")
    async def update_audience(request: Request, course_id: str):
        course = owned_course(request, course_id)
        payload = await json_object(request)
        if "major_ids" not in payload:
            raise HTTPException(400, "请提供课程面向的学生专业")
        audience = course_audience(request, payload)
        students = request.app.state.accounts.students()
        by_id = {student["id"]: student for student in students}
        with request.app.state.learning.lock:
            course["major_ids"] = audience
            for row in request.app.state.learning.data.get("enrollments", []):
                if row.get("course_id") == course_id and not matches_major(course, by_id.get(row["student_id"], {})):
                    row.update(status="removed", updated_at=LearningStore.now())
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/chapters")
    async def add_chapter(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写章节名称")
        with request.app.state.learning.lock:
            order = max((c["order"] for c in course["chapters"]), default=0) + 1
            chapter = {"id": uuid.uuid4().hex[:10], "title": title[:50], "order": order}
            course["chapters"].append(chapter)
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
            return dict(chapter)

    @app.post("/api/courses/{course_id}/chapters/reorder")
    async def reorder_chapters(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        ids = payload.get("chapter_ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise HTTPException(400, "请提供章节顺序")
        current = {chapter["id"] for chapter in course["chapters"]}
        if len(ids) != len(current) or set(ids) != current:
            raise HTTPException(400, "章节顺序必须包含且只包含当前全部章节")
        with request.app.state.learning.lock:
            by_id = {chapter["id"]: chapter for chapter in course["chapters"]}
            course["chapters"] = [by_id[chapter_id] for chapter_id in ids]
            for number, chapter in enumerate(course["chapters"], 1):
                chapter["order"] = number
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/chapters/{chapter_id}")
    async def rename_chapter(request: Request, course_id: str, chapter_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写章节名称")
        with request.app.state.learning.lock:
            chapter = next((c for c in course["chapters"] if c["id"] == chapter_id), None)
            if not chapter:
                raise HTTPException(404, "章节不存在")
            chapter["title"] = title[:50]
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
            return dict(chapter)

    @app.patch("/api/courses/{course_id}/chapters/{chapter_id}")
    async def update_chapter(request: Request, course_id: str, chapter_id: str):
        return await rename_chapter(request, course_id, chapter_id)

    @app.delete("/api/courses/{course_id}/chapters/{chapter_id}")
    async def delete_chapter(request: Request, course_id: str, chapter_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        chapter = next((item for item in course["chapters"] if item["id"] == chapter_id), None)
        if not chapter:
            raise HTTPException(404, "章节不存在")
        if len(course["chapters"]) <= 1:
            raise HTTPException(400, "课程至少需要保留一个章节")
        move_to = payload.get("move_to")
        cascade = bool(payload.get("cascade"))
        if move_to:
            if move_to == chapter_id or move_to not in {item["id"] for item in course["chapters"]}:
                raise HTTPException(400, "请选择有效的迁移章节")
        questions = [item for item in course["questions"] if item.get("chapter_id") == chapter_id]
        programming = [item for item in course.get("programming_questions", []) if item.get("chapter_id") == chapter_id]
        resources = [item for item in course["resources"] if item.get("chapter_id") == chapter_id]
        if (questions or programming or resources) and not move_to and not cascade:
            raise HTTPException(409, "该章节仍有题目或资源，请先选择迁移章节或确认级联删除")
        with request.app.state.learning.lock:
            if move_to:
                for item in questions + programming + resources:
                    item["chapter_id"] = move_to
            else:
                removed = {item["id"] for item in questions}
                course["questions"] = [item for item in course["questions"] if item["id"] not in removed]
                removed_prog = {item["id"] for item in programming}
                course["programming_questions"] = [item for item in course.get("programming_questions", [])
                                                   if item["id"] not in removed_prog]
                removed_res = {item["id"] for item in resources}
                course["resources"] = [item for item in course["resources"] if item["id"] not in removed_res]
            course["chapters"] = [item for item in course["chapters"] if item["id"] != chapter_id]
            for number, item in enumerate(course["chapters"], 1):
                item["order"] = number
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/resources")
    async def add_resource(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        name = str(payload.get("name", "")).strip()
        if not course:
            raise HTTPException(404, "课程不存在")
        if not name:
            raise HTTPException(400, "请填写资源名称")
        job_id = payload.get("job_id") or None
        if job_id:
            job = find_job(request, str(job_id), owner_only=True)
            if job["status"] != "completed":
                raise HTTPException(400, "只能关联已完成的提取任务")
        chapter_id = str(payload.get("chapter_id", "")).strip() or course["chapters"][0]["id"]
        with request.app.state.learning.lock:
            if chapter_id not in {c["id"] for c in course["chapters"]}:
                raise HTTPException(400, "章节不存在")
            course["resources"].append({"id": uuid.uuid4().hex[:10], "name": name[:100],
                                        "kind": str(payload.get("kind", "课件"))[:20], "job_id": job_id,
                                        "chapter_id": chapter_id,
                                        "content": str(payload.get("content", ""))[:8000],
                                        "code": str(payload.get("code", ""))[:8000]})
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.patch("/api/courses/{course_id}/resources/{resource_id}")
    async def update_resource(request: Request, course_id: str, resource_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        resource = next((item for item in course["resources"] if item["id"] == resource_id), None)
        if not resource:
            raise HTTPException(404, "资源不存在")
        with request.app.state.learning.lock:
            name = str(payload.get("name", resource.get("name", ""))).strip()
            if not name:
                raise HTTPException(400, "请填写资源名称")
            kind = str(payload.get("kind", resource.get("kind", "课件"))).strip()[:20] or "课件"
            chapter_id = str(payload.get("chapter_id", resource.get("chapter_id", ""))).strip()
            if chapter_id not in {item["id"] for item in course["chapters"]}:
                raise HTTPException(400, "章节不存在")
            content = str(payload.get("content", resource.get("content", "")))[:8000]
            code = str(payload.get("code", resource.get("code", "")))[:8000]
            resource["name"] = name[:100]
            resource["kind"] = kind
            resource["chapter_id"] = chapter_id
            resource["content"] = content
            resource["code"] = code
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.delete("/api/courses/{course_id}/resources/{resource_id}")
    async def delete_resource(request: Request, course_id: str, resource_id: str):
        course = editable_course(request, course_id)
        if not any(item["id"] == resource_id for item in course["resources"]):
            raise HTTPException(404, "资源不存在")
        with request.app.state.learning.lock:
            course["resources"] = [item for item in course["resources"] if item["id"] != resource_id]
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/resources/{resource_id}/reading")
    async def report_resource_reading(request: Request, course_id: str, resource_id: str):
        """学生上报资料阅读进度：只升不降；首次读到 60% 时补一条资料阅读事件。"""
        student = require_role(request, "student")
        store = request.app.state.learning
        payload = await json_object(request)
        percent = payload.get("percent")
        if (isinstance(percent, bool) or not isinstance(percent, (int, float))
                or not 0 <= float(percent) <= 100):
            raise HTTPException(400, "阅读进度需为 0 到 100 之间的数字")
        with store.lock:
            course = next((item for item in store.data["courses"] if item["id"] == course_id), None)
            if not course or course.get("status") != "published" or not accepts_student(course, student):
                raise HTTPException(404, "课程不存在或尚未发布")
            resource = next((item for item in course.get("resources") or []
                             if item["id"] == resource_id), None)
            if not resource:
                raise HTTPException(404, "资源不存在")
            table = store.data.setdefault("reading_progress", {}).setdefault(student["id"], {})
            entry = table.setdefault(resource_id, {"percent": 0, "course_id": course_id,
                                                   "name": resource.get("name"),
                                                   "first_at": None, "updated_at": None})
            entry["percent"] = max(int(entry.get("percent") or 0), int(round(float(percent))))
            entry["course_id"] = course_id
            entry["name"] = resource.get("name")
            entry["updated_at"] = LearningStore.now()
            if entry.get("first_at") is None and entry["percent"] > 0:
                entry["first_at"] = entry["updated_at"]
            if entry["percent"] >= READING_THRESHOLD and not entry.get("recorded"):
                entry["recorded"] = True
                record_event(store.data, student["id"], "resource_read", LearningStore.now(),
                             course_id=course_id, ref_id=resource_id, source="course")
            store.save()
            result = {"resource_id": resource_id, "percent": entry["percent"],
                      "recorded": bool(entry.get("recorded")), "threshold": READING_THRESHOLD}
        return result

    @app.post("/api/courses/{course_id}/questions")
    async def add_question(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        text = str(payload.get("text", "")).strip()
        options = payload.get("options", [])
        answer = payload.get("answer")
        if not course:
            raise HTTPException(404, "课程不存在")
        if not text or not isinstance(options, list) or not 2 <= len(options) <= 4 or any(not str(item).strip() for item in options):
            raise HTTPException(400, "请填写题目和 2–4 个选项")
        if type(answer) is not int or not 0 <= answer < len(options):
            raise HTTPException(400, "请选择正确答案")
        with request.app.state.learning.lock:
            # 知识点关联：可直接传 knowledge_point_id，也可传 knowledge_point 名称自动挂到课程知识点列表。
            knowledge_point_id = payload.get("knowledge_point_id")
            kp_name = str(payload.get("knowledge_point", "")).strip()
            kp_list = course.setdefault("knowledge_points", [])
            if kp_name and knowledge_point_id not in {kp["id"] for kp in kp_list}:
                existing = next((kp for kp in kp_list if kp["name"] == kp_name), None)
                if existing:
                    knowledge_point_id = existing["id"]
                else:
                    kp = {"id": uuid.uuid4().hex[:10], "name": kp_name[:50]}
                    kp_list.append(kp)
                    knowledge_point_id = kp["id"]
            chapter_id = str(payload.get("chapter_id", "")).strip() or course["chapters"][0]["id"]
            if chapter_id not in {c["id"] for c in course["chapters"]}:
                raise HTTPException(400, "章节不存在")
            course["questions"].append({"id": uuid.uuid4().hex[:10], "text": text[:3000],
                                        "options": [str(item).strip()[:1000] for item in options[:4]], "answer": answer,
                                        "knowledge_point_id": knowledge_point_id, "chapter_id": chapter_id,
                                        "explanation": str(payload.get("explanation", "")).strip()[:3000]})
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/questions/import-ai")
    async def import_ai_questions(request: Request, course_id: str):
        """Copy teacher-reviewed private AI questions into the owned course bank."""
        course = editable_course(request, course_id)
        payload = await json_object(request)
        job_id = payload.get("job_id")
        if not isinstance(job_id, str):
            raise HTTPException(400, "请选择自己的 AI 出题任务")
        find_job(request, job_id, owner_only=True)
        chapter_id = payload.get("chapter_id")
        if not isinstance(chapter_id, str) or chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
            raise HTTPException(400, "请选择有效的目标章节")
        ids = payload.get("question_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 10
                or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
            raise HTTPException(400, "请勾选 1–10 道不重复的 AI 题目")
        point_assignments = payload.get("knowledge_point_ids", {})
        if not isinstance(point_assignments, dict) or any(qid not in ids for qid in point_assignments):
            raise HTTPException(400, "只能为选中的题目设置知识点")
        store = request.app.state.learning
        with request.app.state.quizzes.lock, store.lock:
            editable_course(request, course_id)
            if chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
                raise HTTPException(400, "请选择有效的目标章节")
            point_ids = {point["id"] for point in course.get("knowledge_points", [])}
            if any(not isinstance(value, str) or value not in point_ids for value in point_assignments.values()):
                raise HTTPException(400, "请选择本班的有效知识点")
            try:
                quiz = request.app.state.quizzes.read(job_id)
            except SummaryError as error:
                raise HTTPException(error.status_code, str(error)) from None
            if payload.get("quiz_id") != quiz["id"]:
                raise HTTPException(409, "AI 题目版本已更新，请重新预览后再导入")
            questions = {question["id"]: question for question in quiz["questions"]}
            if any(qid not in questions for qid in ids):
                raise HTTPException(400, "勾选的题目不属于这份 AI 练习")
            before = deepcopy(course)
            imported, existing = [], []
            for qid in ids:
                source = {"job_id": job_id, "quiz_id": quiz["id"], "question_id": qid}
                previous = next((q for q in course["questions"] if q.get("ai_source") == source), None)
                if previous:
                    existing.append(previous["id"])
                    continue
                question = {**deepcopy(questions[qid]), "id": uuid.uuid4().hex[:10],
                            "chapter_id": chapter_id, "ai_source": source,
                            "knowledge_point_id": point_assignments.get(qid)}
                course["questions"].append(question)
                imported.append(question["id"])
            if imported:
                course["updated_at"] = LearningStore.now()
                try:
                    store.save()
                except OSError:
                    course.clear()
                    course.update(before)
                    raise HTTPException(507, "题目未导入，无法保存课程，请检查磁盘后重试") from None
            return {"imported_ids": imported, "existing_ids": existing,
                    "course": store.public_course(course, include_answers=True)}

    @app.post("/api/courses/{course_id}/questions/reorder")
    async def reorder_questions(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        ids = payload.get("question_ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise HTTPException(400, "请提供题目顺序")
        current = {question["id"] for question in course["questions"]}
        if len(ids) != len(current) or set(ids) != current:
            raise HTTPException(400, "题目顺序必须包含且只包含当前全部单选题")
        with request.app.state.learning.lock:
            by_id = {question["id"]: question for question in course["questions"]}
            course["questions"] = [by_id[question_id] for question_id in ids]
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.patch("/api/courses/{course_id}/questions/{question_id}")
    async def update_question(request: Request, course_id: str, question_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        question = next((item for item in course["questions"] if item["id"] == question_id), None)
        if not question:
            raise HTTPException(404, "题目不存在")
        with request.app.state.learning.lock:
            text = str(payload.get("text", question.get("text", ""))).strip()
            if not text:
                raise HTTPException(400, "请填写题目")
            options = question.get("options", [])
            if "options" in payload:
                options = payload.get("options")
                if not isinstance(options, list) or not 2 <= len(options) <= 4 or any(not str(item).strip() for item in options):
                    raise HTTPException(400, "请填写 2–4 个选项")
                options = [str(item).strip()[:1000] for item in options[:4]]
            answer = payload.get("answer", question.get("answer"))
            if type(answer) is not int or not 0 <= answer < len(options):
                raise HTTPException(400, "请选择正确答案")
            explanation = str(payload.get("explanation", question.get("explanation", ""))).strip()[:3000]
            chapter_id = str(payload.get("chapter_id", question.get("chapter_id", ""))).strip()
            if chapter_id not in {item["id"] for item in course["chapters"]}:
                raise HTTPException(400, "章节不存在")
            knowledge_point_id = question.get("knowledge_point_id")
            new_point = None
            if "knowledge_point" in payload or "knowledge_point_id" in payload:
                kp_list = course.setdefault("knowledge_points", [])
                knowledge_point_id = payload.get("knowledge_point_id")
                kp_name = str(payload.get("knowledge_point", "")).strip()
                if kp_name and knowledge_point_id not in {point["id"] for point in kp_list}:
                    existing = next((point for point in kp_list if point["name"] == kp_name), None)
                    if existing:
                        knowledge_point_id = existing["id"]
                    else:
                        new_point = {"id": uuid.uuid4().hex[:10], "name": kp_name[:50]}
                        knowledge_point_id = new_point["id"]
            status = payload.get("status")
            if status not in (None, "retired", "active"):
                raise HTTPException(400, "题目状态无效")
            question["text"] = text[:3000]
            question["options"] = options
            question["answer"] = answer
            question["explanation"] = explanation
            question["chapter_id"] = chapter_id
            question["knowledge_point_id"] = knowledge_point_id
            if new_point:
                course.setdefault("knowledge_points", []).append(new_point)
            if status == "retired":
                question["status"] = "retired"
                question["retired_at"] = LearningStore.now()
            elif status == "active":
                question.pop("status", None)
                question.pop("retired_at", None)
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.delete("/api/courses/{course_id}/questions/{question_id}")
    async def delete_question(request: Request, course_id: str, question_id: str):
        course = editable_course(request, course_id)
        question = next((item for item in course["questions"] if item["id"] == question_id), None)
        if not question:
            raise HTTPException(404, "题目不存在")
        with request.app.state.learning.lock:
            question["status"] = "retired"
            question["retired_at"] = LearningStore.now()
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/publish")
    async def publish_course(request: Request, course_id: str):
        course = editable_course(request, course_id)
        if not course:
            raise HTTPException(404, "课程不存在")
        if len(course.get("major_ids", [])) != 1:
            raise HTTPException(400, "发布教学班前请指定一个学生专业")
        if not course["resources"] or not (_active_questions(course) or _active_programming(course)):
            raise HTTPException(400, "发布前至少需要一份学习资源和一道练习题")
        with request.app.state.learning.lock:
            course["status"] = "published"
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/programming-questions", status_code=201)
    async def add_programming_question(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        try:
            question = validate_question(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        chapter_id = payload.get("chapter_id") or course["chapters"][0]["id"]
        if chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
            raise HTTPException(400, "章节不存在")
        question["chapter_id"] = chapter_id
        with request.app.state.learning.lock:
            course.setdefault("programming_questions", []).append(question)
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return question

    @app.post("/api/courses/{course_id}/programming-questions/reorder")
    async def reorder_programming(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        ids = payload.get("question_ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise HTTPException(400, "请提供编程题顺序")
        current = {question["id"] for question in course.get("programming_questions", [])}
        if len(ids) != len(current) or set(ids) != current:
            raise HTTPException(400, "编程题顺序必须包含且只包含当前全部编程题")
        with request.app.state.learning.lock:
            by_id = {question["id"]: question for question in course.get("programming_questions", [])}
            course["programming_questions"] = [by_id[question_id] for question_id in ids]
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.patch("/api/courses/{course_id}/programming-questions/{question_id}")
    async def update_programming_question(request: Request, course_id: str, question_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        question = next((item for item in course.get("programming_questions", []) if item["id"] == question_id), None)
        if not question:
            raise HTTPException(404, "编程题不存在")
        merged = deepcopy(question)
        for key in ("title", "description", "examples", "test_cases", "chapter_id", "reference_code", "explanation"):
            if key in payload:
                merged[key] = payload[key]
        chapter_id = payload.get("chapter_id", question.get("chapter_id"))
        if chapter_id not in {item["id"] for item in course["chapters"]}:
            raise HTTPException(400, "章节不存在")
        try:
            validated = validate_question(merged)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        with request.app.state.learning.lock:
            for key in ("title", "text", "description", "examples", "test_cases", "limits", "reference_code", "explanation"):
                question[key] = validated[key]
            question["chapter_id"] = chapter_id
            if "status" in payload:
                if payload.get("status") == "retired":
                    question["status"] = "retired"
                    question["retired_at"] = LearningStore.now()
                elif payload.get("status") == "active":
                    question.pop("status", None)
                    question.pop("retired_at", None)
                else:
                    raise HTTPException(400, "题目状态无效")
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.delete("/api/courses/{course_id}/programming-questions/{question_id}")
    async def delete_programming_question(request: Request, course_id: str, question_id: str):
        course = editable_course(request, course_id)
        question = next((item for item in course.get("programming_questions", []) if item["id"] == question_id), None)
        if not question:
            raise HTTPException(404, "编程题不存在")
        with request.app.state.learning.lock:
            question["status"] = "retired"
            question["retired_at"] = LearningStore.now()
            course["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/programming-questions/{question_id}/runs", status_code=202)
    async def run_programming(request: Request, course_id: str, question_id: str):
        user = require_role(request, "student")
        course = request.app.state.learning.course(course_id)
        if not course or course["status"] != "published" or not accepts_student(course, user):
            raise HTTPException(404, "课程不存在或尚未发布")
        question = next((q for q in course.get("programming_questions", [])
                         if q["id"] == question_id and not _is_retired(q)), None)
        if not question:
            raise HTTPException(404, "编程题不存在")
        payload = await json_object(request)
        code, mode = payload.get("code"), payload.get("mode", "sample")
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > PYTHON_LIMITS["code_bytes"]:
            raise HTTPException(400, "请输入 Python 代码，最多 32 KB")
        if mode not in {"sample", "submit"}:
            raise HTTPException(400, "请选择运行示例或提交判题")
        try:
            return request.app.state.programming.start(user, course, question, code, mode)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/programming-runs/{run_id}")
    async def programming_result(request: Request, run_id: str):
        user = require_role(request, "student")
        result = request.app.state.programming.get(run_id, user["id"])
        if result is None:
            raise HTTPException(404, "运行记录不存在或服务已重启，请查看已保存的学习记录")
        return result

    def code_advice_source(request, run_id):
        user = require_role(request, "student")
        source = request.app.state.programming.advice(run_id, user["id"])
        if source is None:
            raise HTTPException(404, "运行记录不存在或服务已重启，请返回编程题重新运行。")
        if source["kind"] is None:
            raise HTTPException(409, "请先完成运行示例或提交判题；执行环境或保存异常时请先重试运行。")
        return user, source

    @app.post("/api/programming-runs/{run_id}/advice", status_code=202)
    async def start_code_advice(request: Request, run_id: str):
        # Consume the small request before denial so Windows clients receive the HTTP error cleanly.
        body = await request.body()
        user, source = code_advice_source(request, run_id)
        if body and await json_object(request):
            raise HTTPException(400, "分析使用本次实际运行的代码与判题结果，无需上传代码或选择分析方式。")
        kind = source["kind"]
        try:
            return request.app.state.code_analyses.start_report(user, run_id, source["evidence"],
                api_key=request.app.state.accounts.api_key(request), prompt=CODE_AI_PROMPTS[kind], label=CODE_AI_LABELS[kind])
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @app.get("/api/programming-runs/{run_id}/advice")
    async def code_advice_state(request: Request, run_id: str):
        user, source = code_advice_source(request, run_id)
        return {**request.app.state.code_analyses.get(user["id"], run_id), "kind":source["kind"],
                "record":{"title":source["evidence"]["题目"]["title"], "mode":source["evidence"]["运行方式"],
                          "passed":source["run"]["passed"], "total":source["run"]["total"],
                          "course_id":source["run"]["course_id"], "question_id":source["run"]["question_id"]}}

    @app.get("/api/programming-runs/{run_id}/advice/{action}")
    async def code_advice_document(request: Request, run_id: str, action: str):
        user, source = code_advice_source(request, run_id)
        if action not in {"view", "download"}:
            raise HTTPException(404, "文件不存在")
        try:
            html = request.app.state.code_analyses.html(user["id"], run_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        label = CODE_AI_LABELS[source["kind"]]
        if action == "download":
            filename = quote(source["evidence"]["题目"]["title"] + "-" + label + ".html", safe="")
            return HTMLResponse(html, headers={"Content-Disposition":f"attachment; filename=python-advice.html; filename*=UTF-8''{filename}",
                                               "Content-Security-Policy":SUMMARY_CSP})
        account, encoded_run = quote(user["id"], safe=""), quote(run_id, safe="")
        course, question = quote(source["run"]["course_id"], safe=""), quote(source["run"]["question_id"], safe="")
        toolbar = (f'<nav style="padding:16px;background:#edf5ef;font:15px sans-serif;display:flex;gap:24px;flex-wrap:wrap">'
                   f'<a href="/api/programming-runs/{encoded_run}/advice/download?account={account}" download>下载分析 HTML</a>'
                   f'<a href="/?page=programming&amp;course={course}&amp;question={question}&amp;account={account}" target="_blank" rel="noopener">返回编程题 ↗</a></nav>')
        html = re.sub(r"(<body\b[^>]*>)", lambda match:match.group(0)+toolbar, html, count=1, flags=re.I)
        policy = SUMMARY_CSP.replace("sandbox allow-same-origin;", "sandbox allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox;")
        return HTMLResponse(html, headers={"Content-Security-Policy":policy})

    @app.post("/api/courses/{course_id}/submissions")
    async def submit_answers(request: Request, course_id: str):
        user = require_role(request, "student")
        payload = await json_object(request)
        store = request.app.state.learning
        course = store.course(course_id)
        if not course or course["status"] != "published":
            raise HTTPException(404, "课程不存在或未发布")
        if not accepts_student(course, user):
            raise HTTPException(404, "课程不存在或未发布")
        assignment_id = payload.get("assignment_id")
        assignment = None
        attempt = None
        attempt_id = payload.get("attempt_id")
        if assignment_id is not None:
            if not isinstance(assignment_id, str):
                raise HTTPException(400, "作业编号无效")
            assignment = next((item for item in store.data.get("assignments", [])
                               if item.get("id") == assignment_id and item.get("course_id") == course_id), None)
            if not assignment or assignment.get("status") != "published":
                raise HTTPException(404, "作业不存在或尚未发布")
            state = _assignment_state(assignment)
            if state == "upcoming":
                raise HTTPException(409, "作业尚未开始，请在开放时间后作答")
            if state == "closed":
                raise HTTPException(409, "作业已截止，不能再提交")
            chapter_id = assignment.get("chapter_id")
            questions = _assignment_questions(course, assignment)
            if not questions:
                raise HTTPException(409, "作业题目已更新或停用，请联系教师")
            if attempt_id is not None:
                if not isinstance(attempt_id, str):
                    raise HTTPException(400, "作答编号无效")
                attempt = next((item for item in store.data.get("assignment_attempts", [])
                                if item.get("id") == attempt_id and item.get("student_id") == user["id"]
                                and item.get("assignment_id") == assignment_id), None)
                if not attempt or attempt.get("status") != "open" or attempt.get("submitted_at"):
                    raise HTTPException(409, "作答记录已失效，请从作业入口重新进入")
                started_at = _parse_datetime(attempt.get("started_at"), "作答开始时间")
                if assignment.get("time_limit_minutes") and started_at:
                    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                    if elapsed > int(assignment["time_limit_minutes"]) * 60 + 60:
                        raise HTTPException(409, "作答时间已结束，本次提交未保存")
            elif assignment.get("time_limit_minutes"):
                raise HTTPException(409, "请从作业入口开始作答，以记录本次时限")
        else:
            # 章节练习：传 chapter_id 只判该章题目，不传则整课判分。
            chapter_id = payload.get("chapter_id")
            questions = _active_questions(course)
            if chapter_id is not None:
                if chapter_id not in {c["id"] for c in course["chapters"]}:
                    raise HTTPException(400, "章节不存在")
                questions = [question for question in questions if question.get("chapter_id") == chapter_id]
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            raise HTTPException(400, "答题数据格式不正确")
        details, correct = [], 0
        if not questions:
            raise HTTPException(400, "该章节尚未配置练习题")
        if chapter_id is None:
            question_chapters = {question.get("chapter_id") for question in questions if question.get("chapter_id")}
            if len(question_chapters) == 1:
                chapter_id = question_chapters.pop()
        point_names = {point["id"]: point["name"] for point in course.get("knowledge_points", [])}
        for question in questions:
            selected = answers.get(question["id"])
            if type(selected) is not int or not 0 <= selected < len(question["options"]):
                raise HTTPException(400, "请完成所有题目并选择有效选项")
            is_correct = selected == question["answer"]
            correct += int(is_correct)
            point_id = question.get("knowledge_point_id")
            if not isinstance(point_id, str) or point_id not in point_names:
                point_id = None
            details.append({"question_id": question["id"], "selected": selected, "correct": is_correct,
                            "answer": question["answer"], "text": question["text"], "options": question["options"],
                            "chapter_id": question.get("chapter_id"), "explanation": question.get("explanation", ""),
                            "knowledge_point_id": point_id,
                            "knowledge_point_name": point_names.get(point_id, "未分类")})
        raw_score = round(correct / len(questions) * 100)
        now = datetime.now(timezone.utc)
        late, late_days, penalty = (False, 0, 0)
        final_score = raw_score
        if assignment:
            late, late_days, penalty = _late_metrics(assignment, now)
            final_score = round(raw_score * (100 - penalty) / 100)
            attempt_started = _parse_datetime(attempt.get("started_at"), "作答开始时间") if attempt else None
            duration_seconds = max(0, round((now - attempt_started).total_seconds())) if attempt_started else 0
        else:
            duration_seconds = 0
        submission = {"id": uuid.uuid4().hex[:12], "course_id": course_id,
                      "student": user["name"], "student_id": user["id"],
                      "submitted_at": LearningStore.now(), "score": final_score,
                      "correct_count": correct, "total": len(questions), "details": details,
                      "chapter_id": chapter_id}
        if assignment:
            submission.update({"assignment_id": assignment_id, "attempt_id": attempt.get("id") if attempt else None,
                               "late": late, "late_days": late_days, "penalty_percent": penalty,
                               "raw_score": raw_score, "duration_seconds": duration_seconds})
        with store.lock:
            # 防重复提交：同一学生同一课程/作业在 5 秒内提交完全相同的答案，视为同次重复点击。
            signature = _answers_signature(answers)
            recent = next((r for r in store.data["submissions"]
                           if r["student_id"] == user["id"] and r["course_id"] == course_id
                           and r.get("type") != "python"
                           and r.get("assignment_id") == assignment_id
                           and (not assignment or not attempt or r.get("attempt_id") == attempt["id"])
                           and _within_seconds(r["submitted_at"], submission["submitted_at"], 5)
                           and _answers_signature({d["question_id"]: d["selected"] for d in r["details"]}) == signature), None)
            if recent:
                if attempt and attempt.get("status") == "open":
                    attempt["status"] = "submitted"
                    attempt["submitted_at"] = recent["submitted_at"]
                    attempt["submission_id"] = recent["id"]
                    store.save()
                return _student_record_view(recent, {item["id"]: item for item in store.data.get("assignments", [])}, store.data["submissions"])
            if assignment:
                prior = [r for r in store.data["submissions"]
                         if r.get("assignment_id") == assignment_id and r["student_id"] == user["id"]]
                try:
                    max_attempts = max(1, int(assignment.get("max_attempts") or 1))
                except (TypeError, ValueError):
                    max_attempts = 1
                if len(prior) >= max_attempts:
                    raise HTTPException(409, "已达到本次作业的最多提交次数")
            store.data["submissions"].insert(0, submission)
            # 错题自动归档：答错的题目进入错题本，同一道题不重复新增，只累加错误次数。
            kp_index = {kp["id"]: kp["name"] for kp in course.get("knowledge_points", [])}
            chapter_index = {c["id"]: c["title"] for c in course.get("chapters", [])}
            question_by_id = {question["id"]: question for question in questions}
            wrong_book = store.data.setdefault("wrong_questions", [])
            existing = {w["question_id"]: w for w in wrong_book if w["student_id"] == user["id"] and w["course_id"] == course_id}
            for detail in details:
                if not detail["correct"]:
                    question = question_by_id.get(detail["question_id"], {})
                    kp_id = detail["knowledge_point_id"]
                    detail_chapter_id = question.get("chapter_id") or detail.get("chapter_id")
                    record = existing.get(detail["question_id"])
                    if record:
                        record["wrong_count"] += 1
                        record["last_wrong_at"] = submission["submitted_at"]
                        record["status"] = "unresolved"
                        record["knowledge_point_id"] = kp_id
                        record["knowledge_point_name"] = kp_index.get(kp_id, "未分类")
                        record["chapter_id"] = detail_chapter_id
                        record["chapter_title"] = chapter_index.get(detail_chapter_id, "")
                        record["assignment_id"] = assignment_id
                        # 做题又答错：说明仍未掌握，复习调度重置为 1 天后。
                        record["review_interval_days"] = 1
                        record["next_review_at"] = _days_later(1)
                        record["correct_streak"] = 0
                    else:
                        record = {"id": uuid.uuid4().hex[:12], "student_id": user["id"], "course_id": course_id,
                                  "question_id": detail["question_id"], "knowledge_point_id": kp_id,
                                  "knowledge_point_name": kp_index.get(kp_id, "未分类"),
                                  "chapter_id": detail_chapter_id, "chapter_title": chapter_index.get(detail_chapter_id, ""),
                                  "assignment_id": assignment_id,
                                  "question_snapshot": {"text": question.get("text", detail["text"]),
                                                        "options": question.get("options", detail.get("options", [])),
                                                        "answer": question.get("answer", detail["answer"]),
                                                        "explanation": question.get("explanation", "")},
                                  "wrong_count": 1, "last_wrong_at": submission["submitted_at"],
                                  "first_wrong_at": submission["submitted_at"],
                                  "status": "unresolved",
                                  "review_interval_days": 1, "next_review_at": _days_later(1),
                                  "review_count": 0, "ease_factor": 2.5, "correct_streak": 0}
                        existing[detail["question_id"]] = record
                        wrong_book.append(record)
            if attempt:
                attempt["status"] = "submitted"
                attempt["submitted_at"] = submission["submitted_at"]
                attempt["submission_id"] = submission["id"]
            record_event(store.data, user["id"],
                         "code_submit" if submission.get("type") == "python" else "practice_submit",
                         submission["submitted_at"], course_id=course_id, ref_id=submission["id"],
                         duration_seconds=submission.get("duration_seconds", 0), source="course")
            store.save()
        return _student_record_view(submission, {item["id"]: item for item in store.data.get("assignments", [])}, store.data["submissions"])

    def assignment_payload(course, payload, existing=None):
        existing = existing or {}
        title = str(payload.get("title", existing.get("title", ""))).strip()
        if not title:
            raise HTTPException(400, "请填写作业标题")
        if len(title) > 100:
            raise HTTPException(400, "作业标题最多 100 个字符")
        description = str(payload.get("description", existing.get("description", ""))).strip()[:2000]
        if "question_ids" in payload:
            raise HTTPException(400, "作业必须单独创建题目，不能从课程题库选题")
        try:
            questions = validate_questions(payload.get("questions", existing.get("questions")), course, existing.get("questions", []))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        chapter_id = payload.get("chapter_id", existing.get("chapter_id")) or questions[0]["chapter_id"]
        if chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
            raise HTTPException(400, "章节不存在")
        open_at = _parse_datetime(payload.get("open_at", existing.get("open_at")), "作业开始时间") or datetime.now(timezone.utc)
        due_at = _parse_datetime(payload.get("due_at", existing.get("due_at")), "作业截止时间")
        close_at = _parse_datetime(payload.get("close_at", existing.get("close_at")), "作业关闭时间")
        if due_at and due_at <= open_at:
            raise HTTPException(400, "截止时间必须晚于开始时间")
        if close_at and due_at and close_at < due_at:
            raise HTTPException(400, "关闭时间不能早于截止时间")

        def integer(name, default, low, high, label):
            value = payload.get(name, existing.get(name, default))
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{label}必须是整数") from None
            if not low <= value <= high:
                raise HTTPException(400, f"{label}应在 {low}–{high} 之间")
            return value

        def boolean(name, default):
            value = payload.get(name, existing.get(name, default))
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "on", "yes"}
            return bool(value)

        max_attempts = integer("max_attempts", 1, 1, 10, "最多提交次数")
        time_limit_minutes = integer("time_limit_minutes", 0, 0, 240, "作答时限（分钟）")
        late_penalty = integer("late_penalty_percent_per_day", 0, 0, 100, "迟交每天扣分")
        late_max_days = integer("late_max_days", 0, 0, 30, "允许迟交天数")
        allow_late = boolean("allow_late", False)
        if allow_late and late_max_days == 0:
            late_max_days = 1
        grade_release = payload.get("grade_release", existing.get("grade_release", "immediate"))
        if grade_release not in {"immediate", "manual"}:
            raise HTTPException(400, "成绩发布方式无效")
        status = payload.get("status", existing.get("status", "draft"))
        if status not in {"draft", "published"}:
            raise HTTPException(400, "作业状态无效")
        if status == "published" and not due_at:
            raise HTTPException(400, "发布作业前请填写截止时间")
        return {"title": title, "description": description, "chapter_id": chapter_id,
                "questions": questions, "open_at": _iso_utc(open_at),
                "due_at": _iso_utc(due_at) if due_at else None,
                "close_at": _iso_utc(close_at) if close_at else None,
                "max_attempts": max_attempts, "time_limit_minutes": time_limit_minutes,
                "allow_late": allow_late, "late_penalty_percent_per_day": late_penalty,
                "late_max_days": late_max_days, "grade_release": "immediate", "status": status}

    def teacher_assignment_view(assignment, course, submissions, students):
        state = _assignment_state(assignment)
        roster = _course_roster(course, students, submissions, app.state.learning.data.get("enrollments", []))
        member_ids = {student["id"] for student in roster}
        records = [record for record in submissions if record.get("assignment_id") == assignment["id"]
                   and record["student_id"] in member_ids]
        submitted = {record["student_id"] for record in records}
        latest_by_student = {student_id: _pick_submission([record for record in records
                                                           if record["student_id"] == student_id], "latest")
                             for student_id in submitted}
        scores = [record["score"] for record in latest_by_student.values()
                  if record and isinstance(record.get("score"), (int, float))]
        chapter_title = next((chapter["title"] for chapter in course["chapters"]
                              if chapter["id"] == assignment.get("chapter_id")), "自选题组")
        return {**assignment, "course_name": course["title"] + (" · " + course["teaching_class"] if course.get("teaching_class") else ""), "chapter_title": chapter_title,
                "state": state, "student_count": len(roster), "submitted_count": len(submitted),
                "missing_count": len([student for student in roster if student["id"] not in submitted]),
                "late_count": len([record for record in latest_by_student.values() if record and record.get("late")]),
                "average_score": round(sum(scores) / len(scores)) if scores else None,
                "grades_released": bool(assignment.get("grades_released_at")) or assignment.get("grade_release") == "immediate",
                "missing_students": [{"id": student["id"], "name": student["name"]}
                                     for student in roster if student["id"] not in submitted]}

    def student_assignment_view(assignment, course, records):
        state = _assignment_state(assignment)
        records = sorted(records, key=lambda item: item.get("submitted_at", ""), reverse=True)
        latest = records[0] if records else None
        scores = [record["score"] for record in records if isinstance(record.get("score"), (int, float))]
        max_attempts = max(1, int(assignment.get("max_attempts") or 1))
        unlocked = len(records) >= max_attempts
        questions = [{key: deepcopy(q[key]) for key in ("id", "text", "options", "chapter_id", "answer", "explanation")
                      if key in q and (unlocked or key not in {"answer", "explanation"})} for q in _assignment_questions(course, assignment)]
        public = {key: deepcopy(value) for key, value in assignment.items() if key not in {"questions", "question_ids"}}
        return {**public, "questions": questions,
                "course_name": course["title"] + (" · " + course["teaching_class"] if course.get("teaching_class") else ""),
                "chapter_title": next((chapter["title"] for chapter in course["chapters"] if chapter["id"] == assignment.get("chapter_id")), "作业"),
                "state": state, "attempts": len(records), "remaining_attempts": max(0, max_attempts-len(records)),
                "completed": unlocked, "answers_locked": not unlocked,
                "best_score": max(scores) if scores else None, "latest_score": latest.get("score") if latest else None,
                "last_submitted_at": latest.get("submitted_at") if latest else None,
                "latest_submission_id": latest.get("id") if latest else None, "pending_release": False,
                "can_start": state in {"open", "late"} and not unlocked,
                "can_submit": state in {"open", "late"} and not unlocked, "question_count": len(questions)}

    def find_assignment(request, course, assignment_id):
        assignment = next((item for item in request.app.state.learning.data.get("assignments", [])
                           if item.get("id") == assignment_id and item.get("course_id") == course["id"]), None)
        if not assignment:
            raise HTTPException(404, "作业不存在")
        return assignment

    @app.get("/api/courses/{course_id}/assignments")
    async def list_course_assignments(request: Request, course_id: str):
        course = owned_course(request, course_id)
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        with store.lock:
            return [teacher_assignment_view(assignment, course, store.data["submissions"], students)
                    for assignment in store.data.get("assignments", [])
                    if assignment.get("course_id") == course_id and assignment.get("status") != "archived"]

    @app.post("/api/courses/{course_id}/assignments", status_code=201)
    async def create_assignment(request: Request, course_id: str):
        course = editable_course(request, course_id)
        payload = await json_object(request)
        values = assignment_payload(course, payload)
        now = LearningStore.now()
        assignment = {"id": uuid.uuid4().hex[:12], "course_id": course_id, "teacher_id": course["teacher_id"],
                      **values, "created_at": now, "updated_at": now, "grades_released_at": None}
        store = request.app.state.learning
        with store.lock:
            store.data.setdefault("assignments", []).insert(0, assignment)
            store.save()
        return teacher_assignment_view(assignment, course, store.data["submissions"],
                                       request.app.state.accounts.students())

    @app.patch("/api/courses/{course_id}/assignments/{assignment_id}")
    async def update_assignment(request: Request, course_id: str, assignment_id: str):
        course = editable_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        payload = await json_object(request)
        values = assignment_payload(course, payload, assignment)
        with request.app.state.learning.lock:
            used = any(record.get("assignment_id") == assignment_id for record in request.app.state.learning.data["submissions"])
            if used and (values["questions"] != assignment["questions"] or values["max_attempts"] != assignment["max_attempts"]):
                raise HTTPException(409, "已有学生提交，不能再修改题目或提交次数；请创建新作业")
            assignment.update(values)
            assignment["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return teacher_assignment_view(assignment, course, request.app.state.learning.data["submissions"],
                                       request.app.state.accounts.students())

    @app.delete("/api/courses/{course_id}/assignments/{assignment_id}")
    async def delete_assignment(request: Request, course_id: str, assignment_id: str):
        course = editable_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        with request.app.state.learning.lock:
            assignment["status"] = "archived"
            assignment["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return {"deleted": assignment_id}

    @app.post("/api/courses/{course_id}/assignments/{assignment_id}/publish")
    async def publish_assignment(request: Request, course_id: str, assignment_id: str):
        course = editable_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        if assignment.get("status") == "archived":
            raise HTTPException(404, "作业不存在")
        if not assignment.get("due_at"):
            raise HTTPException(400, "发布作业前请填写截止时间")
        if not _assignment_questions(course, assignment):
            raise HTTPException(400, "请先创建作业题目再发布")
        with request.app.state.learning.lock:
            assignment["status"] = "published"
            assignment["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return teacher_assignment_view(assignment, course, request.app.state.learning.data["submissions"],
                                       request.app.state.accounts.students())

    @app.post("/api/courses/{course_id}/assignments/{assignment_id}/release")
    async def release_assignment_grades(request: Request, course_id: str, assignment_id: str):
        course = editable_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        if assignment.get("status") == "archived":
            raise HTTPException(404, "作业不存在")
        with request.app.state.learning.lock:
            assignment["grades_released_at"] = LearningStore.now()
            assignment["updated_at"] = LearningStore.now()
            request.app.state.learning.save()
        return teacher_assignment_view(assignment, course, request.app.state.learning.data["submissions"],
                                       request.app.state.accounts.students())

    @app.get("/api/courses/{course_id}/assignments/{assignment_id}/submissions")
    async def assignment_submissions(request: Request, course_id: str, assignment_id: str):
        course = owned_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        with store.lock:
            roster = _course_roster(course, students, store.data["submissions"], app.state.learning.data.get("enrollments", []))
            rows = []
            for student in roster:
                records = [record for record in store.data["submissions"]
                           if record.get("assignment_id") == assignment_id and record["student_id"] == student["id"]]
                latest = _pick_submission(records, "latest")
                best = _pick_submission(records, "best")
                rows.append({"student_id": student["id"], "student": student["name"],
                             "major_id": student.get("major_id"), "status": "submitted" if records else "missing",
                             "attempts": len(records),
                             "latest_score": latest.get("score") if latest else None,
                             "best_score": best.get("score") if best else None,
                             "late": bool(latest and latest.get("late")),
                             "last_submitted_at": latest.get("submitted_at") if latest else None,
                             "submission_id": latest.get("id") if latest else None})
            return {"assignment": teacher_assignment_view(assignment, course, store.data["submissions"], students),
                    "students": rows}

    @app.get("/api/assignments")
    async def list_student_assignments(request: Request):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            courses = {course["id"]: course for course in store.data["courses"]
                       if course["status"] == "published" and accepts_student(course, student)}
            items = []
            student_records = [record for record in store.data["submissions"]
                               if record["student_id"] == student["id"]]
            for assignment in store.data.get("assignments", []):
                course = courses.get(assignment.get("course_id"))
                if not course or assignment.get("status") != "published":
                    continue
                records = [record for record in student_records if record.get("assignment_id") == assignment["id"]]
                items.append(student_assignment_view(assignment, course, records))
            items.sort(key=lambda item: (item.get("due_at") or "", item.get("created_at") or ""))
            return items

    @app.get("/api/assignments/{assignment_id}")
    async def get_student_assignment(request: Request, assignment_id: str):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            assignment = next((item for item in store.data.get("assignments", [])
                               if item.get("id") == assignment_id and item.get("status") == "published"), None)
            course = store.course(assignment.get("course_id")) if assignment else None
            if not assignment or not course or course["status"] != "published" or not accepts_student(course, student):
                raise HTTPException(404, "作业不存在或尚未发布")
            student_records = [record for record in store.data["submissions"]
                               if record["student_id"] == student["id"] and record.get("course_id") == course["id"]]
            records = [record for record in student_records if record.get("assignment_id") == assignment_id]
            return {"assignment": student_assignment_view(assignment, course, records),
                    "course": store.public_course(course, include_answers=False)}

    @app.post("/api/assignments/{assignment_id}/start")
    async def start_assignment(request: Request, assignment_id: str):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            assignment = next((item for item in store.data.get("assignments", [])
                               if item.get("id") == assignment_id and item.get("status") == "published"), None)
            course = store.course(assignment.get("course_id")) if assignment else None
            if not assignment or not course or course["status"] != "published" or not accepts_student(course, student):
                raise HTTPException(404, "作业不存在或尚未发布")
            state = _assignment_state(assignment)
            if state == "upcoming":
                raise HTTPException(409, "作业尚未开始")
            if state == "closed":
                raise HTTPException(409, "作业已截止")
            records = [record for record in store.data["submissions"]
                       if record.get("assignment_id") == assignment_id and record["student_id"] == student["id"]]
            if len(records) >= max(1, int(assignment.get("max_attempts") or 1)):
                raise HTTPException(409, "已达到本次作业的最多提交次数")
            attempts = store.data.setdefault("assignment_attempts", [])
            now = datetime.now(timezone.utc)
            for item in attempts:
                if item.get("assignment_id") != assignment_id or item.get("student_id") != student["id"]:
                    continue
                if item.get("status") != "open":
                    continue
                started = _parse_datetime(item.get("started_at"), "作答开始时间")
                limit = int(assignment.get("time_limit_minutes") or 0)
                if limit and started and now > started + timedelta(minutes=limit):
                    item["status"] = "expired"
                    item["expired_at"] = _iso_utc(now)
                    store.save()
                    continue
                return {"id": item["id"], "started_at": item["started_at"],
                        "expires_at": _iso_utc(started + timedelta(minutes=limit)) if limit and started else None,
                        "time_limit_minutes": limit}
            item = {"id": uuid.uuid4().hex[:12], "assignment_id": assignment_id, "course_id": course["id"],
                    "student_id": student["id"], "started_at": _iso_utc(now), "status": "open",
                    "submitted_at": None, "submission_id": None}
            attempts.append(item)
            store.save()
            limit = int(assignment.get("time_limit_minutes") or 0)
            return {"id": item["id"], "started_at": item["started_at"],
                    "expires_at": _iso_utc(now + timedelta(minutes=limit)) if limit else None,
                    "time_limit_minutes": limit}

    def analysis_record(request, record_id):
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        with store.lock:
            record = next((r for r in store.data["submissions"] + store.data.get("ai_quiz_submissions", [])
                           if r["id"] == record_id), None)
            course = store.course(record.get("course_id")) if record else None
            allowed = record and (record["student_id"] == user["id"] if user["role"] == "student"
                                  else record.get("type") != "ai_quiz" and course and course["teacher_id"] == user["id"])
            if not allowed:
                raise HTTPException(404, "提交记录不存在或无权查看")
            if user["role"] == "student" and record.get("assignment_id"):
                assignment = next((item for item in store.data.get("assignments", [])
                                   if item.get("id") == record["assignment_id"]), None)
                if not answers_visible(assignment, user["id"], store.data["submissions"]):
                    raise HTTPException(409, "本次作业提交次数尚未用完，暂不能查看答案、解析或 AI 分析")
            return user, deepcopy(record), deepcopy(course or {})

    @app.post("/api/learning-records/{record_id}/analysis", status_code=202)
    async def start_analysis(request: Request, record_id: str):
        user, record, course = analysis_record(request, record_id)
        if await request.body() and await json_object(request):
            raise HTTPException(400, "分析使用已保存的提交记录，无需上传题目或分数。")
        try:
            return request.app.state.analyses.start(user, record, course, api_key=request.app.state.accounts.api_key(request))
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @app.get("/api/learning-records/{record_id}/analysis")
    async def analysis_state(request: Request, record_id: str):
        user, record, course = analysis_record(request, record_id)
        return {**request.app.state.analyses.get(user["id"], record_id),
                "record": {"id":record_id, "title":record.get("course_name") or course.get("title", "历史练习"),
                           "score":record["score"], "total":record["total"], "student":record["student"],
                           "submitted_at":record["submitted_at"]}}

    @app.get("/api/learning-records/{record_id}/analysis/{action}")
    async def analysis_document(request: Request, record_id: str, action: str):
        user, record, course = analysis_record(request, record_id)
        if action not in {"view", "download"}: raise HTTPException(404, "文件不存在")
        try:
            html = request.app.state.analyses.html(user["id"], record_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        if action == "download":
            filename = quote((record.get("course_name") or course.get("title", "练习")) + "-学习分析.html", safe="")
            return HTMLResponse(html, headers={"Content-Disposition":f"attachment; filename=learning-analysis.html; filename*=UTF-8''{filename}",
                                               "Content-Security-Policy":SUMMARY_CSP})
        account = quote(user["id"], safe="")
        encoded_record = quote(record_id, safe="")
        toolbar = (f'<nav style="padding:16px;background:#edf5ef;color:#164b3e;font:15px sans-serif;display:flex;gap:24px;flex-wrap:wrap">'
                   f'<a href="/api/learning-records/{encoded_record}/analysis/download?account={account}" download>下载分析 HTML</a>'
                   f'<a href="/?page=records&amp;account={account}" target="_blank" rel="noopener">查看学习记录 ↗</a></nav>')
        html = re.sub(r"(<body\b[^>]*>)", lambda match:match.group(0)+toolbar, html, count=1, flags=re.I)
        policy = SUMMARY_CSP.replace("sandbox allow-same-origin;", "sandbox allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox;")
        return HTMLResponse(html, headers={"Content-Security-Policy":policy})

    @app.get("/api/learning-records")
    async def learning_records(request: Request, source: str | None = None):
        user = request.app.state.accounts.current(request)
        if source not in {None, "self_practice", "assignment"}:
            raise HTTPException(400, "请选择自主练习或作业记录")
        store = request.app.state.learning
        with store.lock:
            own_courses = {c["id"]: c for c in store.data["courses"] if c["teacher_id"] == user["id"]}
            courses = {c["id"]: c for c in store.data["courses"]}
            records = [r for r in store.data["submissions"] if
                       (r["course_id"] in own_courses if user["role"] == "teacher" else r["student_id"] == user["id"])]
            if user["role"] == "student":
                records += [r for r in store.data.get("ai_quiz_submissions", []) if r["student_id"] == user["id"]]
                records.sort(key=lambda r:r["submitted_at"], reverse=True)
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            if source:
                records = [r for r in records if bool(r.get("assignment_id")) == (source == "assignment")]
            result = []
            for r in records:
                course = courses.get(r["course_id"], {})
                chapter_title = ""
                if r.get("chapter_id"):
                    chapter = next((c for c in course.get("chapters", []) if c["id"] == r["chapter_id"]), None)
                    chapter_title = chapter["title"] if chapter else ""
                view = _student_record_view(r, assignments, store.data["submissions"]) if user["role"] == "student" else r
                result.append({**view, "course_name": r.get("course_name") or course.get("title", "已删除课程"),
                               "teaching_class": course.get("teaching_class", ""), "term": course.get("term", ""),
                               "assignment_title": assignments.get(r.get("assignment_id"), {}).get("title", ""),
                               "source_type": "ai_quiz" if r.get("type") == "ai_quiz" else "assignment" if r.get("assignment_id") else "self_practice",
                               "chapter_title": chapter_title})
            return result

    @app.get("/api/teacher/analytics")
    async def teacher_analytics(request: Request):
        teacher = require_role(request, "teacher")
        store = request.app.state.learning
        # Always acquire account data before the learning lock (same order as registration).
        students = request.app.state.accounts.students()
        with store.lock:
            courses = [course for course in store.data["courses"] if course["teacher_id"] == teacher["id"]]
            return aggregate_teacher(courses, store.data["submissions"], students, store.data.get("enrollments", []),
                                     radar_data=store.data)

    def gradebook_payload(request, course, mode="latest", status_filter="all", start=None, end=None, chapter_filter=None):
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        all_course_submissions = [record for record in store.data["submissions"]
                                  if record.get("course_id") == course["id"] and record.get("type") != "python"]
        roster = _course_roster(course, students, all_course_submissions, app.state.learning.data.get("enrollments", []))
        start_at = _parse_datetime(start, "开始日期")
        end_at = _parse_datetime(end, "结束日期")
        submissions = []
        for record in all_course_submissions:
            submitted = _parse_datetime(record.get("submitted_at"), "提交时间")
            if start_at and submitted and submitted < start_at:
                continue
            if end_at and submitted and submitted > end_at:
                continue
            submissions.append(record)
        assignments = [item for item in store.data.get("assignments", [])
                       if item.get("course_id") == course["id"] and item.get("status") in {"published", "closed"}]
        if chapter_filter:
            assignments = [item for item in assignments if item.get("chapter_id") == chapter_filter]
        columns = []
        for assignment in assignments:
            columns.append({"id": "assignment:" + assignment["id"], "kind": "assignment",
                            "title": assignment["title"], "assignment_id": assignment["id"],
                            "chapter_id": assignment.get("chapter_id"), "due_at": assignment.get("due_at"),
                            "max_score": 100})
        # Formal grades are backed by published assignments; practice remains in learning records.
        published_ids = {item["id"] for item in assignments}
        submissions = [record for record in submissions if record.get("assignment_id") in published_ids]
        rows = []
        for student in roster:
            cells = {}
            student_records = [record for record in submissions if record["student_id"] == student["id"]]
            for column in columns:
                records = [record for record in student_records
                           if record.get("assignment_id") == column["assignment_id"]]
                picked = _pick_submission(records, mode)
                cells[column["id"]] = {"score": picked.get("score") if picked else None,
                                       "attempts": len(records),
                                       "late": bool(picked and picked.get("late")),
                                       "submitted_at": picked.get("submitted_at") if picked else None,
                                       "submission_id": picked.get("id") if picked else None}
            scores = [cell["score"] for cell in cells.values() if isinstance(cell.get("score"), (int, float))]
            submitted_count = sum(1 for cell in cells.values() if cell.get("submission_id"))
            rows.append({"student_id": student["id"], "student": student["name"],
                         "major_id": student.get("major_id"), "cells": cells,
                         "average_score": round(sum(scores) / len(scores)) if scores else None,
                         "submitted_count": submitted_count,
                         "late_count": sum(1 for cell in cells.values() if cell.get("late"))})
        if status_filter == "submitted":
            rows = [row for row in rows if row["submitted_count"]]
        elif status_filter == "missing":
            rows = [row for row in rows if not row["submitted_count"]]
        rows.sort(key=lambda row: (row["average_score"] is None, -(row["average_score"] or 0), row["student_id"]))
        student_scores = [row["average_score"] for row in rows if row["average_score"] is not None]
        summary = {"student_count": len(rows), "submitted_students": sum(1 for row in rows if row["submitted_count"]),
                   "average_score": round(sum(student_scores) / len(student_scores)) if student_scores else None,
                   "pass_rate": round(sum(score >= 60 for score in student_scores) / len(student_scores) * 100)
                   if student_scores else None,
                   "column_count": len(columns)}
        return {"course": {"id": course["id"], "title": course["title"], "status": course["status"],
                           "major_ids": course["major_ids"]},
                "columns": columns, "students": rows, "summary": summary, "mode": mode,
                "generated_at": LearningStore.now()}

    @app.get("/api/teacher/gradebook")
    async def teacher_gradebook(request: Request, course_id: str, mode: str = "latest", status: str = "all",
                                start: str | None = None, end: str | None = None,
                                chapter_id: str | None = None):
        teacher = require_role(request, "teacher")
        course = owned_course(request, course_id)
        if mode not in {"best", "latest", "first"}:
            raise HTTPException(400, "成绩取值方式无效")
        if status not in {"all", "submitted", "missing"}:
            raise HTTPException(400, "学生筛选方式无效")
        if chapter_id and chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
            raise HTTPException(400, "章节不存在")
        with request.app.state.learning.lock:
            return gradebook_payload(request, course, mode, status, start, end, chapter_id)

    @app.get("/api/teacher/gradebook.csv")
    async def teacher_gradebook_csv(request: Request, course_id: str, mode: str = "latest", status: str = "all",
                                    start: str | None = None, end: str | None = None,
                                    chapter_id: str | None = None):
        teacher = require_role(request, "teacher")
        course = owned_course(request, course_id)
        if mode not in {"best", "latest", "first"}:
            raise HTTPException(400, "成绩取值方式无效")
        if status not in {"all", "submitted", "missing"}:
            raise HTTPException(400, "学生筛选方式无效")
        if chapter_id and chapter_id not in {chapter["id"] for chapter in course["chapters"]}:
            raise HTTPException(400, "章节不存在")
        with request.app.state.learning.lock:
            data = gradebook_payload(request, course, mode, status, start, end, chapter_id)
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\r\n")
        headers = ["学生", "专业"] + [("作业：" if column["kind"] == "assignment" else "章节：") + column["title"]
                                      for column in data["columns"]] + ["平均分", "提交项"]
        writer.writerow(headers)
        majors = {major["id"]: major["name"] for major in request.app.state.learning.data.get("majors", [])}
        for row in data["students"]:
            writer.writerow([row["student"], majors.get(row["major_id"], "未设置专业")]
                            + [row["cells"][column["id"]].get("score") if row["cells"][column["id"]].get("score") is not None else ""
                               for column in data["columns"]]
                            + [row["average_score"] if row["average_score"] is not None else "", row["submitted_count"]])
        filename = quote(course["title"] + "-成绩册.csv", safe="")
        return Response(stream.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=gradebook.csv; filename*=UTF-8''{filename}"})

    @app.get("/api/teacher/students/{student_id}")
    async def teacher_student_detail(request: Request, student_id: str, course_id: str | None = None):
        teacher = require_role(request, "teacher")
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        student = next((item for item in students if item["id"] == student_id), None)
        if not student:
            raise HTTPException(404, "学生不存在")
        selected_course = owned_course(request, course_id) if course_id else None
        with store.lock:
            if selected_course:
                course = selected_course
                course_submissions = [record for record in store.data["submissions"] if record.get("course_id") == course["id"]]
                roster = _course_roster(course, students, course_submissions, app.state.learning.data.get("enrollments", []))
                if student_id not in {item["id"] for item in roster} and not any(
                        record["student_id"] == student_id for record in course_submissions):
                    raise HTTPException(404, "该学生不在课程范围内")
                courses = [course]
            else:
                courses = []
                for course in store.data["courses"]:
                    if course["teacher_id"] != teacher["id"]:
                        continue
                    course_submissions = [record for record in store.data["submissions"] if record.get("course_id") == course["id"]]
                    roster = _course_roster(course, students, course_submissions, app.state.learning.data.get("enrollments", []))
                    if student_id in {item["id"] for item in roster} or any(
                            record["student_id"] == student_id for record in course_submissions):
                        courses.append(course)
                if not courses:
                    raise HTTPException(404, "该学生不在当前教师的课程范围内")
            course_ids = {course["id"] for course in courses}
            submissions = [record for record in store.data["submissions"]
                           if record["student_id"] == student_id and record.get("course_id") in course_ids]
            submissions.sort(key=lambda record: record.get("submitted_at", ""), reverse=True)
            course_submissions = [record for record in store.data["submissions"]
                                  if record.get("course_id") in course_ids]
            profiles = build_profiles(courses, course_submissions, students, store.data.get("enrollments", []),
                                      radar_data=store.data, include_student_id=student_id)
            profile = next(item for item in profiles if item["student_id"] == student_id)
            wrong = [record for record in store.data.get("wrong_questions", [])
                     if record["student_id"] == student_id and record.get("course_id") in course_ids]
            assignments = []
            for assignment in store.data.get("assignments", []):
                if assignment.get("course_id") not in course_ids or assignment.get("status") != "published":
                    continue
                course = next(item for item in courses if item["id"] == assignment["course_id"])
                records = [record for record in submissions if record.get("assignment_id") == assignment["id"]]
                assignments.append(student_assignment_view(assignment, course, records))
            return {"student": {key: student.get(key) for key in ("id", "name", "major_id")},
                    "courses": [{"id": course["id"], "title": course["title"]} for course in courses],
                    "profile": profile, "submissions": submissions, "wrong_questions": wrong,
                    "assignments": assignments}

    def announcement_view(announcement, course, student_id=None):
        view = {key: announcement.get(key) for key in ("id", "course_id", "title", "body", "audience",
                                                       "created_at", "pinned")}
        view.update({"course_name": course.get("title", "已删除课程"), "teacher": course.get("teacher", ""),
                     "recipient_count": len(announcement.get("recipient_ids", [])),
                     "read_count": len(announcement.get("read_by", []))})
        if student_id is not None:
            view["read"] = student_id in announcement.get("read_by", [])
        return view

    def create_announcement(request, course, payload, teacher):
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not title:
            raise HTTPException(400, "请填写公告标题")
        if len(title) > 100:
            raise HTTPException(400, "公告标题最多 100 个字符")
        if not body:
            raise HTTPException(400, "请填写公告内容")
        if len(body) > 5000:
            raise HTTPException(400, "公告内容最多 5000 个字符")
        audience = payload.get("audience", "all")
        if audience not in {"all", "non_submitters", "at_risk"}:
            raise HTTPException(400, "请选择有效的公告受众")
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        with store.lock:
            recipients = _announcement_recipients(course, store.data["submissions"], students, audience, app.state.learning.data.get("enrollments", []))
            if not recipients:
                raise HTTPException(400, "当前受众中没有符合条件的学生")
            announcement = {"id": uuid.uuid4().hex[:12], "course_id": course["id"],
                            "teacher_id": teacher["id"], "title": title, "body": body,
                            "audience": audience, "recipient_ids": recipients, "read_by": [],
                            "pinned": bool(payload.get("pinned")), "created_at": LearningStore.now()}
            store.data.setdefault("announcements", []).insert(0, announcement)
            store.save()
        return announcement_view(announcement, course)

    @app.post("/api/courses/{course_id}/announcements", status_code=201)
    async def create_course_announcement(request: Request, course_id: str):
        teacher = require_role(request, "teacher")
        course = editable_course(request, course_id)
        return create_announcement(request, course, await json_object(request), teacher)

    @app.get("/api/courses/{course_id}/announcements")
    async def list_course_announcements(request: Request, course_id: str):
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        course = store.course(course_id)
        if not course:
            raise HTTPException(404, "课程不存在")
        if user["role"] == "teacher":
            if course["teacher_id"] != user["id"]:
                raise HTTPException(404, "课程不存在或不属于当前教师")
        elif course["status"] != "published" or not accepts_student(course, user):
            raise HTTPException(404, "课程不存在或尚未发布")
        with store.lock:
            items = [announcement for announcement in store.data.get("announcements", [])
                     if announcement.get("course_id") == course_id]
            if user["role"] == "student":
                items = [item for item in items if user["id"] in item.get("recipient_ids", [])]
            items.sort(key=lambda item: (bool(item.get("pinned")), item.get("created_at", "")), reverse=True)
            return [announcement_view(item, course, user["id"] if user["role"] == "student" else None)
                    for item in items]

    @app.delete("/api/courses/{course_id}/announcements/{announcement_id}")
    async def delete_course_announcement(request: Request, course_id: str, announcement_id: str):
        course = editable_course(request, course_id)
        store = request.app.state.learning
        with store.lock:
            announcement = next((item for item in store.data.get("announcements", [])
                                 if item.get("id") == announcement_id and item.get("course_id") == course_id), None)
            if not announcement:
                raise HTTPException(404, "公告不存在")
            store.data["announcements"] = [item for item in store.data.get("announcements", [])
                                           if item.get("id") != announcement_id]
            store.save()
        return {"deleted": announcement_id}

    @app.get("/api/announcements")
    async def list_student_announcements(request: Request):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            courses = {course["id"]: course for course in store.data["courses"]
                       if course["status"] == "published" and accepts_student(course, student)}
            items = []
            for announcement in store.data.get("announcements", []):
                course = courses.get(announcement.get("course_id"))
                if not course or student["id"] not in announcement.get("recipient_ids", []):
                    continue
                items.append(announcement_view(announcement, course, student["id"]))
            items.sort(key=lambda item: (bool(item.get("pinned")), item.get("created_at", "")), reverse=True)
            unread = sum(1 for item in items if not item.get("read"))
            return {"items": items, "unread_count": unread}

    @app.post("/api/announcements/{announcement_id}/read")
    async def read_announcement(request: Request, announcement_id: str):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            announcement = next((item for item in store.data.get("announcements", [])
                                 if item.get("id") == announcement_id), None)
            if not announcement:
                raise HTTPException(404, "公告不存在")
            course = store.course(announcement.get("course_id"))
            if not course or course["status"] != "published" or not accepts_student(course, student) \
                    or student["id"] not in announcement.get("recipient_ids", []):
                raise HTTPException(404, "公告不存在")
            if student["id"] not in announcement.setdefault("read_by", []):
                announcement["read_by"].append(student["id"])
                store.save()
            return announcement_view(announcement, course, student["id"])

    @app.post("/api/courses/{course_id}/assignments/{assignment_id}/remind")
    async def remind_assignment(request: Request, course_id: str, assignment_id: str):
        teacher = require_role(request, "teacher")
        course = editable_course(request, course_id)
        assignment = find_assignment(request, course, assignment_id)
        if assignment.get("status") == "archived":
            raise HTTPException(404, "作业不存在")
        payload = await json_object(request)
        due = _parse_datetime(assignment.get("due_at"), "作业截止时间")
        due_text = due.astimezone().strftime("%Y-%m-%d %H:%M") if due else "即将截止"
        body = str(payload.get("body", "")).strip() or f"作业《{assignment['title']}》将于 {due_text} 截止，请尚未提交的同学尽快完成。"
        return create_announcement(request, course, {"title": f"作业提醒：{assignment['title']}", "body": body,
                                                      "audience": "non_submitters", "pinned": False}, teacher)

    # ---- 课堂抢答：SSE 实时同步，不计入正式成绩 ----
    def live_quiz_access(request, quiz_id):
        """校验抢答场次可见性：只能是授课教师本人，或该课程面向的学生。"""
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        manager = request.app.state.live
        try:
            quiz = manager.get(quiz_id)
        except LiveQuizError as error:
            raise HTTPException(error.status_code, error.message) from None
        course = store.course(quiz["course_id"])
        teacher = user["role"] == "teacher" and course and course["teacher_id"] == user["id"]
        student = (user["role"] == "student" and course and course["status"] == "published"
                   and accepts_student(course, user))
        if not teacher and not student:
            raise HTTPException(403 if user["role"] == "teacher" else 404, "这场抢答不属于你可见的课程")
        return user, quiz, bool(teacher)

    @app.post("/api/courses/{course_id}/live-quizzes", status_code=201)
    async def create_live_quiz(request: Request, course_id: str):
        course = owned_course(request, course_id)
        if course.get("status") != "published":
            raise HTTPException(400, "请先发布教学班，再发起课堂抢答")
        teacher = request.app.state.accounts.current(request)
        payload = await json_object(request)
        try:
            quiz = request.app.state.live.create(course, teacher, payload)
        except LiveQuizError as error:
            raise HTTPException(error.status_code, error.message) from None
        return request.app.state.live.teacher_view(quiz)

    @app.get("/api/live-quizzes/current")
    async def current_live_quiz(request: Request):
        """学生端发现机制：返回本人可见课程中最新一场等待中/进行中的抢答。"""
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        visible = {course["id"] for course in store.data["courses"]
                   if course["status"] == "published" and accepts_student(course, user)}
        quiz = request.app.state.live.newest(visible)
        return {"quiz": request.app.state.live.student_view(quiz, user) if quiz else None}

    @app.get("/api/live-quizzes/{quiz_id}")
    async def read_live_quiz(request: Request, quiz_id: str):
        user, quiz, is_teacher = live_quiz_access(request, quiz_id)
        manager = request.app.state.live
        return manager.teacher_view(quiz) if is_teacher else manager.student_view(quiz, user)

    @app.post("/api/live-quizzes/{quiz_id}/start")
    async def start_live_quiz(request: Request, quiz_id: str):
        user = require_role(request, "teacher")
        manager = request.app.state.live
        try:
            quiz = manager.get(quiz_id)
            manager.start(quiz, user["id"])
        except LiveQuizError as error:
            raise HTTPException(error.status_code, error.message) from None
        return manager.teacher_view(quiz)

    @app.post("/api/live-quizzes/{quiz_id}/answers", status_code=201)
    async def answer_live_quiz(request: Request, quiz_id: str):
        user = require_role(request, "student")
        payload = await json_object(request)
        viewer, quiz, is_teacher = live_quiz_access(request, quiz_id)
        if is_teacher:
            raise HTTPException(403, "教师端不能替学生作答")
        try:
            entry = request.app.state.live.answer(quiz, viewer, payload.get("selected"))
            store = request.app.state.learning
            with store.lock:
                record_event(store.data, viewer["id"], "live_answer", entry.get("submitted_at"),
                             course_id=quiz.get("course_id"), ref_id=quiz.get("id"),
                             correct=entry.get("correct"),
                             duration_seconds=int((entry.get("latency_ms") or 0) / 1000),
                             source="live")
                store.save()
        except LiveQuizError as error:
            raise HTTPException(error.status_code, error.message) from None
        return request.app.state.live.student_view(quiz, viewer)

    @app.post("/api/live-quizzes/{quiz_id}/close")
    async def close_live_quiz(request: Request, quiz_id: str):
        user = require_role(request, "teacher")
        manager = request.app.state.live
        try:
            quiz = manager.get(quiz_id)
            manager.close(quiz, user["id"])
        except LiveQuizError as error:
            raise HTTPException(error.status_code, error.message) from None
        return manager.teacher_view(quiz)

    @app.get("/api/live-quizzes/{quiz_id}/events")
    async def live_quiz_events(request: Request, quiz_id: str):
        """SSE：每 0.3 秒比对版本，有变化推送完整快照，空闲 15 秒发心跳。"""
        user, quiz, is_teacher = live_quiz_access(request, quiz_id)
        manager = request.app.state.live

        def snapshot():
            return manager.teacher_view(quiz) if is_teacher else manager.student_view(quiz, user)

        async def stream():
            last, idle = "", 0
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    course = request.app.state.learning.course(quiz["course_id"])
                    if not is_teacher and (not course or course.get("status") != "published" or not accepts_student(course, user)):
                        yield 'event: access_revoked\ndata: {"detail":"你已不在本教学班名单中"}\n\n'
                        break
                    body = json.dumps(snapshot(), ensure_ascii=False)
                    if body != last:
                        last, idle = body, 0
                        yield f"event: state\ndata: {body}\n\n"
                    else:
                        idle += 1
                        if idle >= 50:
                            idle = 0
                            yield ": ping\n\n"
                    await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                return

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                          "Connection": "keep-alive"})

    @app.get("/api/student/profile")
    async def student_profile(request: Request):
        student = require_role(request, "student")
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        with store.lock:
            # Include this student's historical courses even after the audience is edited.
            history = {record["course_id"] for record in store.data["submissions"] if record["student_id"] == student["id"]}
            courses = [course for course in store.data["courses"]
                       if (course["status"] == "published" and accepts_student(course, student)) or course["id"] in history]
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            visible_submissions = [_student_record_view(record, assignments, store.data["submissions"]) for record in store.data["submissions"]]
            profiles = build_profiles(courses, visible_submissions, students, store.data.get("enrollments", []),
                                      radar_data=store.data, include_student_id=student["id"])
            own = next(profile for profile in profiles if profile["student_id"] == student["id"])
            return {"profile": own, "scope": "本人可学习及已参与课程。练习和作业分别计分，复习单独统计。"}

    @app.get("/api/student/portrait")
    async def student_portrait(request: Request):
        """学生画像：投入度四分量与五个维度，全部由学习事件与错题调度现算。"""
        student = require_role(request, "student")
        store = request.app.state.learning
        students = request.app.state.accounts.students()
        with store.lock:
            history = {record["course_id"] for record in store.data["submissions"]
                       if record["student_id"] == student["id"]}
            courses = [course for course in store.data["courses"]
                       if (course["status"] == "published" and accepts_student(course, student))
                       or course["id"] in history]
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            visible = [_student_record_view(record, assignments, store.data["submissions"])
                       for record in store.data["submissions"]]
            own = next((item for item in build_profiles(courses, visible, students, store.data.get("enrollments", []))
                        if item["student_id"] == student["id"]), None)
            reading_courses = [course for course in courses
                               if course["status"] == "published" and accepts_student(course, student)]
            return portrait_payload(store.data, student, own, courses=reading_courses)

    def study_plan_payload(store, student):
        """学习计划与打卡：本周目标完成度、今日任务、连续天数、30 天打卡与成就。"""
        today = local_day()
        data = store.data
        saved = (data.get("study_plans") or {}).get(student["id"]) or {}
        goals = {
            "weekly_submissions": int(saved.get("weekly_submissions") or STUDY_PLAN_DEFAULT["weekly_submissions"]),
            "weekly_reviews": int(saved.get("weekly_reviews") or STUDY_PLAN_DEFAULT["weekly_reviews"]),
        }
        days = activity_days(data, student["id"])
        week_start = today - timedelta(days=today.weekday())
        week = [week_start + timedelta(days=offset) for offset in range(7)]
        week_submissions = sum(int((days.get(day.isoformat()) or {}).get("submissions", 0)) for day in week)
        week_reviews = sum(int((days.get(day.isoformat()) or {}).get("reviews", 0)) for day in week)
        today_entry = days.get(today.isoformat()) or {}
        assignments = {item["id"]: item for item in data.get("assignments", [])}
        wrong = [record for record in data.setdefault("wrong_questions", [])
                 if record.get("student_id") == student["id"]
                 and record_answers_visible(record, assignments, data["submissions"])]
        unresolved = [record for record in wrong if record.get("status") != "mastered"]
        now = LearningStore.now()
        due = [record for record in unresolved if (record.get("next_review_at") or "") <= now]
        total_submissions = sum(int(entry.get("submissions", 0)) for entry in days.values())
        total_reviews = sum(int(entry.get("reviews", 0)) for entry in days.values())
        perfect = any(record.get("score") == 100 for record in data["submissions"]
                      if record.get("student_id") == student["id"])
        plan_view = study_plan_goal_view(data, student,
                                         [course for course in data.get("courses") or []
                                          if accepts_student(course, student)])
        checkins = [plan_day_checkin(data, student, saved, today - timedelta(days=offset))
                    for offset in range(STUDY_CALENDAR_DAYS - 1, -1, -1)]
        checkin_days = {item["date"]: {"submissions": 1 if item["complete"] else 0, "reviews": 0}
                        for item in checkins}
        current_run, longest_run = study_streak(checkin_days, today)
        calendar = []
        for item in checkins:
            day = date.fromisoformat(item["date"])
            entry = days.get(item["date"]) or {}
            calendar.append({
                "date": item["date"], "label": f"{day.month}/{day.day}",
                "week": WEEKDAY_LABELS[(day.weekday() + 1) % 7],
                "submissions": int(entry.get("submissions", 0)),
                "reviews": int(entry.get("reviews", 0)),
                "active": bool(item["complete"]),
                "state": ("done" if item["complete"] and item["has_plan"]
                          else "partial" if item["has_plan"]
                          else "outside" if item["kind"] == "activity" else "none"),
                "has_plan": item["has_plan"],
                "plan_total": item["plan_total"], "plan_done": item["plan_done"],
                "today": day == today,
            })
        progress = (plan_view.get("week") or {}).get("progress") or {}
        planned_cards = int(progress.get("planned") or 0)
        done_cards = int(progress.get("done") or 0)
        unlocked = {
            "first_submission": total_submissions >= 1,
            "first_review": total_reviews >= 1,
            "perfect_score": perfect,
            "streak_3": longest_run >= 3,
            "streak_7": longest_run >= 7,
            "review_20": total_reviews >= 20,
            "clear_wrong": bool(wrong) and not unresolved,
            "week_goal": bool(planned_cards) and done_cards >= planned_cards,
        }
        return {
            "goals": goals,
            "updated_at": saved.get("updated_at"),
            "week": {"start": week_start.isoformat(), "end": week[-1].isoformat(),
                     "submissions": week_submissions, "reviews": week_reviews,
                     "plan_cards": planned_cards, "plan_done": done_cards,
                     "plan_rate": progress.get("rate"),
                     "submission_rate": plan_rate(week_submissions, goals["weekly_submissions"]),
                     "plan_review_cards": int(progress.get("review_planned") or 0),
                     "plan_review_done": int(progress.get("review_done") or 0),
                     "plan_review_rate": (int(round(progress["review_done"] * 100 / progress["review_planned"]))
                                          if progress.get("review_planned") else 0),
                     "review_rate": plan_rate(week_reviews, goals["weekly_reviews"])},
            "today": {"date": today.isoformat(),
                      "submissions": int(today_entry.get("submissions", 0)),
                      "reviews": int(today_entry.get("reviews", 0)),
                      "due": len(due),
                      "plan_total": int(checkins[-1]["plan_total"]),
                      "plan_done": int(checkins[-1]["plan_done"]),
                      "complete": bool(checkins[-1]["complete"]),
                      "active": bool(checkins[-1]["complete"])},
            "streak": {"current": current_run, "longest": longest_run},
            "calendar": calendar,
            "achievements": [{"id": key, "title": title, "desc": desc, "unlocked": unlocked[key]}
                             for key, title, desc in STUDY_ACHIEVEMENTS],
            "totals": {"submissions": total_submissions, "reviews": total_reviews,
                       "unresolved": len(unresolved), "mastered": len(wrong) - len(unresolved),
                       "due": len(due)},
                "plan": plan_view,
        "scope": "打卡＝当天计划里的任务全部完成；当天没排任务的日子不计入打卡，练习或复习没做完计划也不算",
        }

    @app.get("/api/student/plan")
    async def student_plan(request: Request):
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            saved = (store.data.get("study_plans") or {}).get(student["id"]) or {}
            had_today = bool((saved.get("tasks") or {}).get(local_day().isoformat()))
            payload = study_plan_payload(store, student)
            if not had_today and (payload.get("plan") or {}).get("tasks"):
                store.save()  # 今日任务卡在读取时生成，补一次落盘避免每次重算
            return payload

    @app.post("/api/student/plan")
    async def update_student_plan(request: Request):
        """保存每周目标：只接受 1–50 的整数，避免 0 目标让完成率失去意义。"""
        student = require_role(request, "student")
        payload = await json_object(request)
        goals = {}
        for key, label in (("weekly_submissions", "每周练习次数"), ("weekly_reviews", "每周复习题数")):
            value = payload.get(key)
            if type(value) is not int or not 1 <= value <= STUDY_PLAN_LIMIT:
                raise HTTPException(400, f"{label}需为 1 到 {STUDY_PLAN_LIMIT} 之间的整数")
            goals[key] = value
        store = request.app.state.learning
        with store.lock:
            plans = store.data.setdefault("study_plans", {})
            entry = plans.setdefault(student["id"], {})
            # 合并写入：这两个旧目标可以改，但目标计划与任务不能被清掉
            entry.update({**goals, "updated_at": LearningStore.now()})
            store.save()
            return study_plan_payload(store, student)

    def plan_scope_courses(courses, parsed):
        """目标里点名了课程就只算那几门，否则按全部可见课程。"""
        scope = [course for course in courses
                 if not parsed.get("course_ids") or course["id"] in parsed["course_ids"]]
        return scope or list(courses)

    def apply_plan_budget(parsed, payload):
        """Use the same budget for the preview and the saved plan."""
        for key, low, high, label in (("weeks", 1, PLAN_MAX_WEEKS, "周期（周）"),
                                      ("weekly_minutes", PLAN_MIN_WEEKLY_MINUTES,
                                       PLAN_MAX_WEEKLY_MINUTES, "每周可投入（分钟）"),
                                      ("allowed_days", 1, 7, "每周可学习天数")):
            if key not in payload:
                continue
            try:
                value = int(payload[key])
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(400, f"{label}需为整数") from None
            if not low <= value <= high:
                raise HTTPException(400, f"{label}需在 {low} 到 {high} 之间")
            parsed[key] = value
        parsed["allowed_weekdays"] = plan_weekdays_for(parsed["allowed_days"])
        parsed["daily_capacity"] = int(min(parsed["weekly_minutes"] / parsed["allowed_days"],
                                          PLAN_DAILY_CAP_MINUTES))
        parsed["deadline"] = (local_day(parsed["start"]) + timedelta(days=parsed["weeks"] * 7)).isoformat()
        return parsed

    @app.post("/api/student/plan/parse")
    async def parse_student_plan_goal(request: Request):
        """把一句话解析成目标并给出工作量诊断，不落库。"""
        student = require_role(request, "student")
        payload = await json_object(request)
        text = str(payload.get("text") or "").strip()
        if len(text) < 4:
            raise HTTPException(400, '请用一句话写清目标，例如"4 周内掌握 Python 的循环和函数"')
        store = request.app.state.learning
        with store.lock:
            courses = [course for course in store.data.get("courses") or []
                       if accepts_student(course, student)]
            parsed = apply_plan_budget(parse_plan_text(text, courses), payload)
            scope = plan_scope_courses(courses, parsed)
            workload = estimate_plan_workload(store.data, student, scope)
            verdict = plan_verdict(workload["minutes"], parsed["weekly_minutes"], parsed["weeks"])
            return {"parsed": parsed,
                    "workload": {"minutes": workload["minutes"], "by_kind": workload["by_kind"],
                                 "counts": workload["counts"]},
                    "verdict": verdict,
                    "courses": [{"id": course["id"], "title": course["title"],
                                 "teaching_class": course.get("teaching_class")} for course in scope]}

    @app.put("/api/student/plan/goal")
    async def save_student_plan_goal(request: Request):
        """确认生成：落库目标与解析结果，清空旧任务让今日卡按新目标重算。"""
        student = require_role(request, "student")
        payload = await json_object(request)
        text = str(payload.get("text") or "").strip()
        if len(text) < 4:
            raise HTTPException(400, '请用一句话写清目标，例如"4 周内掌握 Python 的循环和函数"')
        store = request.app.state.learning
        with store.lock:
            courses = [course for course in store.data.get("courses") or []
                       if accepts_student(course, student)]
            parsed = apply_plan_budget(parse_plan_text(text, courses), payload)
            plans = store.data.setdefault("study_plans", {})
            entry = plans.setdefault(student["id"], {})
            entry["goal"] = {"text": text, "parsed": parsed, "created_at": LearningStore.now()}
            entry["tasks"] = {}
            entry["updated_at"] = LearningStore.now()
            store.save()
            view = study_plan_payload(store, student)
            store.save()  # 今日任务卡在读取时才生成，这里补一次落盘
            return view

    @app.delete("/api/student/plan/goal")
    async def clear_student_plan_goal(request: Request):
        """清空目标：只删目标与任务，保留原有每周打卡目标。"""
        student = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            entry = (store.data.get("study_plans") or {}).get(student["id"]) or {}
            entry.pop("goal", None)
            entry.pop("tasks", None)
            entry["updated_at"] = LearningStore.now()
            store.save()
            return study_plan_payload(store, student)


    def student_wrong_records(request, user):
        """当前学生的可见错题；作业答案未解锁时，列表与导出均不返回。"""
        store = request.app.state.learning
        names = {course["id"]: course["title"] + (" · " + course["teaching_class"] if course.get("teaching_class") else "") for course in store.data["courses"]}
        assignments = {item["id"]: item for item in store.data.get("assignments", [])}
        return [{**record, "course_name": record.get("course_name") or names.get(record["course_id"], "历史课程")}
                for record in store.data.setdefault("wrong_questions", [])
                if record["student_id"] == user["id"] and record_answers_visible(record, assignments, store.data["submissions"])]

    @app.get("/api/wrong-questions")
    async def wrong_questions(request: Request, course_id: str | None = None,
                              knowledge_point: str | None = None, chapter: str | None = None,
                              knowledge_point_id: str | None = None):
        user = require_role(request, "student")
        with request.app.state.learning.lock:
            items = student_wrong_records(request, user)
        return _filter_wrong_items(items, course_id, knowledge_point, chapter, knowledge_point_id)

    @app.get("/api/wrong-questions/export.csv")
    async def wrong_questions_csv(request: Request, course_id: str | None = None,
                                  knowledge_point: str | None = None, chapter: str | None = None,
                                  knowledge_point_id: str | None = None):
        """导出本人错题为 CSV（UTF-8 BOM，Excel 可直接打开）。"""
        user = require_role(request, "student")
        with request.app.state.learning.lock:
            items = _filter_wrong_items(student_wrong_records(request, user),
                                        course_id, knowledge_point, chapter, knowledge_point_id)
        items.sort(key=lambda item: item.get("last_wrong_at") or "", reverse=True)
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(["课程", "章节", "知识点", "题干", "选项", "正确答案", "解析",
                         "答错次数", "首次答错", "最近答错", "状态", "下次复习", "复习次数", "最近复习结果"])
        for item in items:
            snapshot = item.get("question_snapshot") or {}
            options = snapshot.get("options") or []
            writer.writerow([
                item.get("course_name", ""), item.get("chapter_title") or "未分章节",
                item.get("knowledge_point_name") or "未分类", snapshot.get("text", ""),
                " / ".join(f"{chr(65 + index)}. {text}" for index, text in enumerate(options)),
                _wrong_answer_label(snapshot), snapshot.get("explanation") or "未填写解析",
                item.get("wrong_count", 0), item.get("first_wrong_at") or "", item.get("last_wrong_at") or "",
                "已掌握" if item.get("status") == "mastered" else "待巩固",
                item.get("next_review_at") or "", item.get("review_count", 0),
                "" if item.get("last_review_result") is None else ("答对" if item.get("last_review_result") else "答错"),
            ])
        filename = quote("错题本.csv", safe="")
        return Response(stream.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=wrong-questions.csv; filename*=UTF-8''{filename}"})

    @app.post("/api/wrong-questions/{question_id}/mastered")
    async def mark_mastered(request: Request, question_id: str):
        user = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            record = next((r for r in store.data.setdefault("wrong_questions", [])
                           if r["id"] == question_id and r["student_id"] == user["id"]), None)
            if not record:
                raise HTTPException(404, "错题记录不存在")
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            if not record_answers_visible(record, assignments, store.data["submissions"]):
                raise HTTPException(409, "本次作业提交次数尚未用完，暂不能操作错题")
            record["status"] = "mastered"
            record["mastered_at"] = LearningStore.now()
            store.save()
            return dict(record)

    @app.get("/api/review/due")
    async def review_due(request: Request):
        """复习计划引擎：返回当前学生所有待复习错题，按到期状态排序（逾期在前）。"""
        user = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            records = [dict(r) for r in store.data.setdefault("wrong_questions", [])
                       if r["student_id"] == user["id"] and r["status"] == "unresolved"
                       and record_answers_visible(r, assignments, store.data["submissions"])]
        now = LearningStore.now()
        records.sort(key=lambda r: (0 if r.get("next_review_at", "") <= now else 1, r.get("next_review_at", "")))
        return records

    @app.post("/api/wrong-questions/{question_id}/review")
    async def review_question(request: Request, question_id: str):
        """复习作答：默认走三信号 SM-2（记忆倍率驱动间隔，间隔达 60 天视为掌握）；答错重置为 1 天。

        配置 features.review_scheduler = "linear" 时回退旧版"答对间隔翻倍、封顶 14 天"逻辑。
        """
        user = require_role(request, "student")
        payload = await json_object(request)
        selected = payload.get("selected")
        store = request.app.state.learning
        with store.lock:
            record = next((r for r in store.data.setdefault("wrong_questions", [])
                           if r["id"] == question_id and r["student_id"] == user["id"]), None)
            if not record:
                raise HTTPException(404, "错题记录不存在")
            assignments = {item["id"]: item for item in store.data.get("assignments", [])}
            if not record_answers_visible(record, assignments, store.data["submissions"]):
                raise HTTPException(409, "本次作业提交次数尚未用完，暂不能复习该题")
            snapshot = record["question_snapshot"]
            options, answer = snapshot["options"], snapshot["answer"]
            if type(selected) is not int or not 0 <= selected < len(options):
                raise HTTPException(400, "请选择有效选项")
            correct = selected == answer
            now = LearningStore.now()
            scheduler = review_scheduler()
            if scheduler == "sm2":
                # 先用更新前的记录判定回忆质量：逾期比较依赖旧的 last_reviewed_at。
                quality = sm2_quality(record, correct, datetime.fromisoformat(now))
                ease = float(record.get("ease_factor", 2.5)) + SM2_QUALITY_DELTA[quality]
                record["ease_factor"] = round(min(SM2_EASE_MAX, max(SM2_EASE_MIN, ease)), 2)
                record["last_quality"] = quality
                record["correct_streak"] = (record.get("correct_streak", 0) + 1) if correct else 0
            record["last_reviewed_at"] = now
            record["review_count"] = record.get("review_count", 0) + 1
            record["last_review_result"] = correct
            if correct:
                if scheduler == "sm2":
                    interval = sm2_interval(record, record["ease_factor"])
                    mastered_threshold = SM2_MASTERED_INTERVAL
                else:
                    interval = min(record.get("review_interval_days", 1) * 2, 14)
                    mastered_threshold = 14
                record["review_interval_days"] = interval
                record["next_review_at"] = _days_later(interval)
                if interval >= mastered_threshold:
                    record["status"] = "mastered"
                    record["mastered_at"] = now
            else:
                record["wrong_count"] += 1
                if not record.get("first_wrong_at"):
                    record["first_wrong_at"] = now
                record["last_wrong_at"] = now
                record["review_interval_days"] = 1
                record["next_review_at"] = _days_later(1)
                record["status"] = "unresolved"
            record_event(store.data, user["id"], "review", now, course_id=record.get("course_id"),
                         ref_id=record.get("id"), correct=correct, source="course",
                         knowledge_point_id=record.get("knowledge_point_id") if not record.get("assignment_id") else None)
            store.save()
            return {**dict(record), "correct": correct, "answer": answer}
    @app.get("/api/jobs")
    async def list_jobs(request: Request):
        user = request.app.state.accounts.current(request)
        return [summary_options(request, job) for job in request.app.state.jobs.list(owner_id=user["id"])]

    def summary_options(request, job):
        return {**job, "summary_options": request.app.state.summaries.options(job),
                "quiz_options": request.app.state.quizzes.options(job)}

    @app.post("/api/jobs", status_code=202)
    async def upload(request: Request, profile: str = "balanced"):
        user = request.app.state.accounts.current(request)
        if profile not in PROFILES:
            raise HTTPException(400, "未知提取模式")
        filename = unquote(request.headers.get("x-filename", "video.mp4"))
        filename = re.split(r"[/\\]", filename)[-1]
        filename = "".join(c for c in filename if c.isprintable())[:180]
        suffix = Path(filename).suffix.lower()
        if suffix not in EXTENSIONS:
            raise HTTPException(400, "请选择 MP4、MOV、MKV、AVI、WebM 等视频文件")
        try:
            declared_size = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "文件大小无效")
        if declared_size < 0 or declared_size > MAX_BYTES:
            raise HTTPException(413, "Demo 单个视频上限为 8 GB")
        manager = request.app.state.jobs
        if shutil.disk_usage(manager.root).free < declared_size + 512 * 1024**2:
            raise HTTPException(507, "磁盘空间不足，请为视频副本和导出文件预留空间")
        try:
            job = manager.create(filename, profile, owner_id=user["id"])
        except BusyError as error:
            raise HTTPException(409, str(error))
        folder = manager.root / job["id"]
        temporary = folder / "input.part"
        received = 0
        try:
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    if manager.events[job["id"]].is_set():
                        raise HTTPException(409, "已取消视频传入")
                    received += len(chunk)
                    if received > MAX_BYTES:
                        raise HTTPException(413, "Demo 单个视频上限为 8 GB")
                    output.write(chunk)
            if not received or (declared_size and received != declared_size):
                raise HTTPException(400, "视频文件为空或传入不完整，请重试")
            source = folder / ("source" + suffix)
            temporary.replace(source)
            manager.start(job["id"], source)
        except ClientDisconnect:
            manager.fail_upload(job["id"], "浏览器中断了文件传入，请重新选择视频。")
            return JSONResponse({"detail": "传入已中断"}, status_code=400)
        except HTTPException as error:
            manager.fail_upload(job["id"], str(error.detail))
            raise
        except Exception:
            manager.fail_upload(job["id"], "无法写入本地文件，请检查剩余空间和目录权限。")
            raise HTTPException(500, "无法保存视频到项目目录")
        return summary_options(request, manager.get(job["id"]))

    def find_job(request: Request, job_id: str, owner_only=False):
        user = request.app.state.accounts.current(request)
        job = request.app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "找不到这个任务")
        if job.get("owner_id", DEMO_TEACHER) != user["id"]:
            published = request.app.state.learning.courses()
            shared = any(r.get("job_id") == job_id for c in published
                         if accepts_student(c, user) for r in c["resources"])
            if owner_only or not shared:
                raise HTTPException(404, "找不到这个任务或没有访问权限")
        return job

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        return summary_options(request, find_job(request, job_id, owner_only=True))

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        summaries, transcriptions, jobs, learning = (request.app.state.summaries, request.app.state.transcriptions,
                                                     request.app.state.jobs, request.app.state.learning)
        with summaries.lock, request.app.state.quizzes.lock, transcriptions.lock, learning.lock, jobs.lock:
            if summaries.active == job_id:
                raise HTTPException(409, "总结正在生成，请等待完成后再删除任务")
            if transcriptions.active == job_id:
                raise HTTPException(409, "录音正在转写，请等待完成后再删除任务")
            if job_id in jobs.pptx_exporting:
                raise HTTPException(409, "PPTX 正在导出，请等待完成后再删除任务")
            if jobs.jobs[job_id].get("quiz", {}).get("status") in QUIZ_ACTIVE:
                raise HTTPException(409, "AI 正在出题，请等待完成后再删除任务")
            affected = [(course, course["resources"]) for course in learning.data["courses"]
                        if any(r.get("job_id") == job_id for r in course["resources"])]
            removed = sum(sum(r.get("job_id") == job_id for r in resources) for _, resources in affected)
            try:
                for course, resources in affected:
                    course["resources"] = [r for r in resources if r.get("job_id") != job_id]
                if affected:
                    learning.save()
                jobs.delete(job_id)
            except (BusyError, OSError) as error:
                for course, resources in affected:
                    course["resources"] = resources
                if affected:
                    learning.save()
                raise HTTPException(409 if isinstance(error, BusyError) else 507,
                                    str(error) if isinstance(error, BusyError) else "无法删除任务，请检查目录权限和磁盘状态后重试") from None
        return {"deleted": job_id, "removed_resources": removed}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        return summary_options(request, request.app.state.jobs.cancel(job_id))

    @app.post("/api/jobs/{job_id}/transcription", status_code=202)
    async def start_transcription(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        try:
            return request.app.state.transcriptions.start(
                job_id, credentials=request.app.state.accounts.xfyun_credentials(request))
        except TranscriptionError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @app.get("/api/jobs/{job_id}/transcription/view")
    async def view_transcription(request: Request, job_id: str):
        job = find_job(request, job_id, owner_only=True)
        try:
            path = request.app.state.transcriptions.file(job_id, "html")
            html = path.read_text(encoding="utf-8")
        except TranscriptionError as error:
            raise HTTPException(error.status_code, str(error)) from None
        except OSError:
            raise HTTPException(507, "无法读取文字稿文件") from None
        account = quote(request.app.state.accounts.current(request)["id"], safe="")
        toolbar = (f'<nav class="download-bar"><a href="/api/jobs/{job_id}/transcription/download/html?account={account}">下载 HTML</a>'
                   f'<a href="/api/jobs/{job_id}/transcription/download/txt?account={account}">下载 TXT</a></nav>')
        html = html.replace("<main aria-label=", toolbar + "<main aria-label=", 1)
        return HTMLResponse(html, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})

    @app.get("/api/jobs/{job_id}/transcription/download/{kind}")
    async def download_transcription(request: Request, job_id: str, kind: str):
        job = find_job(request, job_id, owner_only=True)
        try:
            path = request.app.state.transcriptions.file(job_id, kind)
        except TranscriptionError as error:
            raise HTTPException(error.status_code, str(error)) from None
        return FileResponse(path, media_type="text/html" if kind == "html" else "text/plain",
                            filename=Path(job["filename"]).stem + "-课程录音文字稿." + kind)

    @app.get("/api/jobs/{job_id}/quiz/wrong-questions")
    async def ai_quiz_wrong_membership(request: Request, job_id: str):
        user = require_role(request, "student")
        find_job(request, job_id, owner_only=True)
        try:
            quiz = request.app.state.quizzes.read(job_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        with request.app.state.learning.lock:
            ids = [r["ai_source"]["question_id"] for r in request.app.state.learning.data["wrong_questions"]
                   if r["student_id"] == user["id"] and r.get("ai_source", {}).get("job_id") == job_id
                   and r["ai_source"].get("quiz_id") == quiz["id"]]
        return {"quiz_id":quiz["id"], "question_ids":ids}

    @app.post("/api/jobs/{job_id}/quiz/wrong-questions")
    async def add_ai_quiz_wrong_question(request: Request, job_id: str):
        payload = await json_object(request)
        user = require_role(request, "student")
        find_job(request, job_id, owner_only=True)
        store = request.app.state.learning
        with request.app.state.quizzes.lock, store.lock:
            try:
                quiz = request.app.state.quizzes.read(job_id)
            except SummaryError as error:
                raise HTTPException(error.status_code, str(error)) from None
            if payload.get("quiz_id") != quiz["id"]:
                raise HTTPException(409, "AI 题目版本已更新，请刷新练习后再加入错题本。")
            question = next((q for q in quiz["questions"] if q["id"] == payload.get("question_id")), None)
            if question is None:
                raise HTTPException(400, "请选择这份 AI 练习中的有效题目。")
            source = {"job_id":job_id, "quiz_id":quiz["id"], "question_id":question["id"]}
            records = store.data["wrong_questions"]
            previous = next((r for r in records if r["student_id"] == user["id"] and r.get("ai_source") == source), None)
            if previous:
                return {"added":False, "record":deepcopy(previous)}
            now = LearningStore.now()
            record = {"id":uuid.uuid4().hex[:12], "student_id":user["id"], "course_id":f"ai-video:{job_id}",
                      "course_name":"AI 自测 · " + quiz["title"], "source":"ai_quiz_manual", "ai_source":source,
                      "question_id":f"ai-{quiz['id']}-{question['id']}", "knowledge_point_id":None,
                      "knowledge_point_name":"AI 课程自测", "chapter_id":"ai-practice", "chapter_title":"AI 生成练习",
                      "question_snapshot":{k:deepcopy(question[k]) for k in ("text","options","answer","explanation")},
                      "added_at":now, "wrong_count":0, "first_wrong_at":None, "last_wrong_at":None,
                      "status":"unresolved", "review_interval_days":1, "next_review_at":now, "review_count":0,
                      "ease_factor":2.5, "correct_streak":0}
            record_event(store.data, user["id"], "ai_question_add", now,
                         course_id=record["course_id"], ref_id=record["question_id"], source="ai")
            records.append(record)
            try:
                store.save()
            except OSError:
                records.remove(record)
                raise HTTPException(507, "题目未加入错题本，无法保存数据，请检查磁盘后重试。") from None
            return {"added":True, "record":deepcopy(record)}

    @app.post("/api/jobs/{job_id}/quiz/submissions", status_code=201)
    async def submit_ai_quiz(request: Request, job_id: str):
        user = require_role(request, "student")
        find_job(request, job_id, owner_only=True)
        payload = await json_object(request)
        try:
            quiz = request.app.state.quizzes.read(job_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        if payload.get("quiz_id") != quiz["id"]:
            raise HTTPException(409, "这套 AI 练习已更新，请刷新后作答，避免记录与题目不一致。")
        answers = payload.get("answers")
        if not isinstance(answers, dict) or set(answers) != {q["id"] for q in quiz["questions"]}:
            raise HTTPException(400, "请完成全部题目后提交。")
        details = []
        for question in quiz["questions"]:
            selected = answers[question["id"]]
            if type(selected) is not int or not 0 <= selected < len(question["options"]):
                raise HTTPException(400, "存在无效答案，请重新选择。")
            details.append({"question_id":question["id"], "text":question["text"], "options":list(question["options"]),
                            "answer":question["answer"], "selected":selected, "correct":selected == question["answer"],
                            "explanation":question["explanation"], "knowledge_point_name":"AI 课程自测"})
        correct = sum(d["correct"] for d in details)
        record = {"id":uuid.uuid4().hex[:12], "type":"ai_quiz", "course_id":f"ai-video:{job_id}",
                  "course_name":quiz["title"], "job_id":job_id, "quiz_id":quiz["id"], "source_mode":quiz["source_mode"],
                  "student_id":user["id"], "student":user["name"], "submitted_at":LearningStore.now(),
                  "total":len(details), "correct_count":correct, "score":round(correct/len(details)*100), "details":details}
        store = request.app.state.learning
        with store.lock:
            records = store.data.setdefault("ai_quiz_submissions", [])
            recent = next((r for r in records if r["student_id"] == user["id"] and r["quiz_id"] == quiz["id"]
                           and _within_seconds(r["submitted_at"], record["submitted_at"], 5)
                           and {d["question_id"]:d["selected"] for d in r["details"]} == answers), None)
            if recent: return recent
            records.insert(0, record)
            try: store.save()
            except OSError:
                records.remove(record)
                raise HTTPException(507, "无法保存作答记录，请检查磁盘空间和权限后重试。") from None
        return record

    @app.post("/api/jobs/{job_id}/quiz", status_code=202)
    async def generate_quiz(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        if await request.body() and await json_object(request):
            raise HTTPException(400, "出题来源由已保存的总结决定，无需提交额外参数。")
        try:
            return request.app.state.quizzes.start(job_id, api_key=request.app.state.accounts.api_key(request))
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        except OSError:
            raise HTTPException(507, "无法保存出题状态，请检查磁盘空间和目录权限。") from None

    @app.get("/api/jobs/{job_id}/quiz")
    async def get_quiz(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        try:
            return request.app.state.quizzes.read(job_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @app.post("/api/jobs/{job_id}/summary", status_code=202)
    async def generate_summary(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        payload = await json_object(request) if await request.body() else {}
        regenerate = payload.get("regenerate", False)
        if not isinstance(regenerate, bool):
            raise HTTPException(400, "regenerate 必须为布尔值")
        try:
            return request.app.state.summaries.start(job_id, api_key=request.app.state.accounts.api_key(request),
                                                     regenerate=regenerate, mode=payload.get("mode", "slides"))
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @app.delete("/api/jobs/{job_id}/summary")
    async def delete_summary(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        try:
            request.app.state.summaries.delete_summary(job_id)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        return summary_options(request, request.app.state.jobs.get(job_id))

    def summary_html(request, job_id, offline=False):
        try:
            return request.app.state.summaries.read_html(job_id, offline=offline)
        except SummaryError as error:
            raise HTTPException(error.status_code, str(error)) from None
        except OSError:
            raise HTTPException(507, "无法读取总结文件，请检查任务目录") from None

    @app.get("/api/jobs/{job_id}/summary/download")
    async def download_summary(request: Request, job_id: str):
        job = find_job(request, job_id, owner_only=True)
        html = summary_html(request, job_id, offline=True)
        filename = quote(Path(job["filename"]).stem + "-课程总结.html", safe="")
        return HTMLResponse(html, headers={"Content-Disposition": f"attachment; filename=course-summary.html; filename*=UTF-8''{filename}",
                                           "Content-Security-Policy": SUMMARY_CSP})

    @app.get("/api/jobs/{job_id}/summary/view")
    async def view_summary(request: Request, job_id: str):
        job = find_job(request, job_id, owner_only=True)
        html = summary_html(request, job_id)
        user = request.app.state.accounts.current(request)
        # Only adapt local image paths for authenticated HTTP viewing; prose stays unchanged.
        saved_mode = job.get("summary", {}).get("available_mode", job.get("summary", {}).get("mode", "slides"))
        for page in (job["result"].get("pages", []) if saved_mode != "transcript" else []):
            html = html.replace("slides/" + page["filename"], f"/api/jobs/{job_id}/pages/{page['number']}?account={user['id']}")
        account = quote(user["id"], safe="")
        toolbar = ('<nav aria-label="总结操作" style="padding:16px;background:#edf5ef;color:#164b3e;font:15px sans-serif;display:flex;gap:24px;flex-wrap:wrap">'
                   f'<span>来源：{escape(summary_service.MODES[saved_mode])}</span>'
                   f'<a href="/api/jobs/{job_id}/summary/download?account={account}" download>下载总结 HTML</a>'
                   f'<a href="{escape(f"/?page=video&account={account}&job={job_id}")}" target="_blank" rel="noopener">管理总结：删除 / 重新生成 ↗</a></nav>')
        html = re.sub(r"(<body\b[^>]*>)", lambda match: match.group(0) + toolbar, html, count=1, flags=re.I)
        policy = SUMMARY_CSP.replace("sandbox allow-same-origin;", "sandbox allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox;")
        return HTMLResponse(html, headers={"Content-Security-Policy": policy})

    @app.post("/api/jobs/{job_id}/pptx")
    async def export_pptx(request: Request, job_id: str):
        job = find_job(request, job_id, owner_only=True)
        try:
            await run_in_threadpool(request.app.state.jobs.export_pptx, job_id)
        except BusyError as error:
            raise HTTPException(409, str(error))
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        except (ValueError, OSError):
            raise HTTPException(400, "无法导出 PPTX，请检查课件图片是否完整，以及磁盘空间和目录权限，然后重试。")
        except ImportError:
            raise HTTPException(503, "PPTX 导出依赖尚未安装，请停止服务后重新运行 run.py，自动安装依赖。")
        except Exception:
            raise HTTPException(500, "PPTX 导出失败，原课件已保留，请稍后重试。")
        return {"url": f"/api/jobs/{job_id}/files/pptx", "pages": len(job["result"]["pages"])}

    @app.get("/api/jobs/{job_id}/files/{kind}")
    async def download(request: Request, job_id: str, kind: str, preview: bool = False):
        job = find_job(request, job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "任务尚未完成")
        files = {"pdf": ("slides.pdf", "application/pdf"), "zip": ("slides.zip", "application/zip"),
                 "pptx": ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
                 "audio": ("audio.mp3", "audio/mpeg")}
        if kind not in files or (kind == "audio" and not job["result"].get("audio")):
            raise HTTPException(404, "没有这个导出文件")
        name, media_type = files[kind]
        path = request.app.state.jobs.root / job_id / name
        if not path.is_file():
            raise HTTPException(404, "导出文件已被移走或删除")
        return FileResponse(path, media_type=media_type, filename=Path(job["filename"]).stem + "-" + name,
                            content_disposition_type="inline" if preview and kind in {"pdf", "audio"} else "attachment")

    @app.get("/api/jobs/{job_id}/pages/{number}")
    async def page(request: Request, job_id: str, number: int):
        job = find_job(request, job_id)
        if job["status"] != "completed" or not 1 <= number <= len(job["result"]["pages"]):
            raise HTTPException(404, "页面不存在")
        path = request.app.state.jobs.root / job_id / "slides" / f"slide-{number:04d}.jpg"
        if not path.is_file():
            raise HTTPException(404, "图片已被移走或删除")
        return FileResponse(path, media_type="image/jpeg")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
