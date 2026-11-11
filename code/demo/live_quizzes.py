"""课堂抢答（SSE 实时同步）的内存会话管理。

口径：
- 抢答只做课堂互动，不创建 submission，因此不计入教师端平均分与及格率。
- 一人一题只能作答一次；正确答案只在服务端判定，学生提交后或本场结束后才下发。
- 会话保存在内存，结束时整体写入 learning-platform.json 的 live_quizzes 字段，便于教师回看。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

DEFAULT_SECONDS = 30
MIN_SECONDS = 10
MAX_SECONDS = 300
MAX_OPTIONS = 6
MAX_TEXT = 200
MAX_OPTION = 120
MAX_EXPLANATION = 400
MAX_STORED = 40


class LiveQuizError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now():
    return datetime.now(timezone.utc)


def _seconds(payload):
    try:
        value = int(payload.get("duration_seconds", DEFAULT_SECONDS))
    except (TypeError, ValueError):
        raise LiveQuizError("抢答时长必须是整数秒") from None
    if not MIN_SECONDS <= value <= MAX_SECONDS:
        raise LiveQuizError(f"抢答时长需在 {MIN_SECONDS}-{MAX_SECONDS} 秒之间")
    return value


def clean_question(payload, course):
    """题目来源：课程已有题目（question_id）或教师现场手填（question）。"""
    question_id = payload.get("question_id")
    if question_id:
        if not isinstance(question_id, str):
            raise LiveQuizError("题目编号无效")
        question = next((item for item in course.get("questions", [])
                         if item.get("id") == question_id and item.get("status") != "retired"), None)
        if not question:
            raise LiveQuizError("所选题目不存在或已停用", 404)
        point = next((item["name"] for item in course.get("knowledge_points", [])
                      if item.get("id") == question.get("knowledge_point_id")), "")
        return {"text": question["text"][:MAX_TEXT],
                "options": [str(option)[:MAX_OPTION] for option in question["options"]],
                "answer": question["answer"],
                "explanation": str(question.get("explanation") or "")[:MAX_EXPLANATION],
                "knowledge_point_name": point,
                "chapter_id": question.get("chapter_id"),
                "source_question_id": question["id"]}
    raw = payload.get("question")
    if not isinstance(raw, dict):
        raise LiveQuizError("请选择课程题目，或填写抢答题干")
    text = str(raw.get("text", "")).strip()
    options = raw.get("options")
    answer = raw.get("answer")
    if not text:
        raise LiveQuizError("请填写抢答题干")
    if len(text) > MAX_TEXT:
        raise LiveQuizError(f"题干最多 {MAX_TEXT} 字")
    if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
        raise LiveQuizError(f"抢答题需要 2-{MAX_OPTIONS} 个选项")
    options = [str(item).strip()[:MAX_OPTION] for item in options]
    if any(not item for item in options):
        raise LiveQuizError("选项不能为空")
    if type(answer) is not int or not 0 <= answer < len(options):
        raise LiveQuizError("请选择一个有效选项作为答案")
    return {"text": text, "options": options, "answer": answer,
            "explanation": str(raw.get("explanation", "")).strip()[:MAX_EXPLANATION],
            "knowledge_point_name": str(raw.get("knowledge_point_name", "")).strip()[:40],
            "chapter_id": None, "source_question_id": None}


class LiveQuizManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.sessions = {}
        with store.lock:
            if not isinstance(store.data.get("live_quizzes"), list):
                store.data["live_quizzes"] = []
                store.save()

    # ---- 会话 ----
    def get(self, quiz_id):
        with self.lock:
            quiz = self.sessions.get(quiz_id)
            if not quiz:
                raise LiveQuizError("抢答场次不存在或已结束", 404)
            return quiz

    def create(self, course, teacher, payload):
        question = clean_question(payload, course)
        duration = _seconds(payload)
        with self.lock:
            busy = next((item for item in self.sessions.values()
                         if item["course_id"] == course["id"] and item["status"] in {"waiting", "active"}), None)
            if busy:
                raise LiveQuizError("本课程已有一场进行中的抢答，请先结束后再发起", 409)
            quiz = {"id": "live-" + uuid.uuid4().hex[:10], "course_id": course["id"],
                    "course_title": course["title"], "teacher_id": teacher["id"], "teacher": teacher["name"],
                    "question": question, "status": "waiting", "created_at": _now().isoformat(),
                    "started_at": None, "closes_at": None, "closed_at": None,
                    "duration_seconds": duration, "answers": {}, "first_correct": None, "version": 0}
            self.sessions[quiz["id"]] = quiz
            return quiz

    def newest(self, course_ids):
        """学生端发现机制：找到自己可见课程里最新一场等待中/进行中的抢答。"""
        with self.lock:
            for quiz in list(self.sessions.values()):
                self._expire(quiz)
            candidates = [item for item in self.sessions.values()
                          if item["course_id"] in course_ids and item["status"] in {"waiting", "active"}]
            if not candidates:
                return None
            return max(candidates, key=lambda item: item["created_at"])

    # ---- 状态流转 ----
    def start(self, quiz, teacher_id):
        if quiz["teacher_id"] != teacher_id:
            raise LiveQuizError("这场抢答不属于你的课程", 403)
        with self.lock:
            self._expire(quiz)
            if quiz["status"] == "active":
                raise LiveQuizError("本场抢答已经开始", 409)
            if quiz["status"] != "waiting":
                raise LiveQuizError("本场抢答已结束，请重新发起", 409)
            now = _now()
            quiz["status"] = "active"
            quiz["started_at"] = now.isoformat()
            quiz["closes_at"] = (now + timedelta(seconds=quiz["duration_seconds"])).isoformat()
            quiz["version"] += 1
            return quiz

    def answer(self, quiz, student, selected):
        now = _now()
        with self.lock:
            self._expire(quiz)
            if quiz["status"] != "active":
                raise LiveQuizError("本场抢答未在作答时间内，不能提交", 409)
            if student["id"] in quiz["answers"]:
                raise LiveQuizError("本题你已经作答，同一题只记录一次", 409)
            options = quiz["question"]["options"]
            if type(selected) is not int or not 0 <= selected < len(options):
                raise LiveQuizError("请选择一个有效选项")
            try:
                started = datetime.fromisoformat(quiz["started_at"])
                latency = max(0, int((now - started).total_seconds() * 1000))
            except (TypeError, ValueError):
                latency = 0
            correct = selected == quiz["question"]["answer"]
            quiz["answers"][student["id"]] = {"student_id": student["id"], "student": student["name"],
                                              "selected": selected, "correct": correct,
                                              "latency_ms": latency, "submitted_at": now.isoformat()}
            if correct and quiz["first_correct"] is None:
                quiz["first_correct"] = {"student": student["name"], "latency_ms": latency}
            quiz["version"] += 1
            return quiz["answers"][student["id"]]

    def close(self, quiz, teacher_id):
        if quiz["teacher_id"] != teacher_id:
            raise LiveQuizError("这场抢答不属于你的课程", 403)
        with self.lock:
            if quiz["status"] == "closed":
                return quiz
            quiz["status"] = "closed"
            quiz["closed_at"] = _now().isoformat()
            quiz["version"] += 1
            self._persist(quiz)
            return quiz

    def _expire(self, quiz):
        """服务端时间为准：到时自动封场，并落库一次。"""
        if quiz["status"] != "active" or not quiz["closes_at"]:
            return False
        try:
            closes = datetime.fromisoformat(quiz["closes_at"])
        except ValueError:
            return False
        if _now() < closes:
            return False
        quiz["status"] = "closed"
        quiz["closed_at"] = _now().isoformat()
        quiz["version"] += 1
        self._persist(quiz)
        return True

    def _persist(self, quiz):
        record = {"id": quiz["id"], "course_id": quiz["course_id"], "course_title": quiz["course_title"],
                  "teacher": quiz["teacher"], "teacher_id": quiz["teacher_id"], "question": quiz["question"],
                  "status": quiz["status"], "created_at": quiz["created_at"], "started_at": quiz["started_at"],
                  "closes_at": quiz["closes_at"], "closed_at": quiz["closed_at"],
                  "duration_seconds": quiz["duration_seconds"],
                  "answers": list(quiz["answers"].values()), "first_correct": quiz["first_correct"]}
        with self.store.lock:
            items = self.store.data.setdefault("live_quizzes", [])
            index = next((position for position, item in enumerate(items) if item.get("id") == record["id"]), None)
            if index is None:
                items.insert(0, record)
                del items[MAX_STORED:]
            else:
                items[index] = record
            self.store.save()

    # ---- 视图 ----
    def teacher_view(self, quiz):
        with self.lock:
            self._expire(quiz)
            return self._view(quiz, include_answers=True, include_results=True)

    def student_view(self, quiz, student):
        with self.lock:
            self._expire(quiz)
            mine = quiz["answers"].get(student["id"])
            # 已作答或本场结束后，学生才看到正确答案与解析。
            return self._view(quiz, include_answers=bool(mine) or quiz["status"] == "closed",
                              include_results=False, mine=mine)

    def _view(self, quiz, *, include_answers, include_results, mine=None):
        answers = list(quiz["answers"].values())
        options = quiz["question"]["options"]
        votes = [0] * len(options)
        for item in answers:
            if 0 <= item["selected"] < len(votes):
                votes[item["selected"]] += 1
        payload = {"id": quiz["id"], "course_id": quiz["course_id"], "course_title": quiz["course_title"],
                   "teacher": quiz["teacher"], "status": quiz["status"],
                   "duration_seconds": quiz["duration_seconds"], "created_at": quiz["created_at"],
                   "started_at": quiz["started_at"], "closes_at": quiz["closes_at"], "closed_at": quiz["closed_at"],
                   "server_now": _now().isoformat(),
                   "participant_count": len(answers), "votes": votes,
                   "correct_count": sum(1 for item in answers if item["correct"]),
                   "question": {"text": quiz["question"]["text"], "options": options,
                                "knowledge_point_name": quiz["question"]["knowledge_point_name"]},
                   "version": quiz["version"]}
        if include_answers:
            payload["question"]["answer"] = quiz["question"]["answer"]
            payload["question"]["explanation"] = quiz["question"]["explanation"]
        if include_results:
            payload["answers"] = sorted(answers, key=lambda item: item["submitted_at"])
            payload["correct_rate"] = round(payload["correct_count"] / len(answers) * 100) if answers else None
            payload["first_correct"] = quiz["first_correct"]
        else:
            if mine:
                payload["my_answer"] = mine
            # 揭晓后学生也能看到全场正确率与首个答对者，但不拿到他人明细。
            if quiz["status"] == "closed" and answers:
                payload["correct_rate"] = round(payload["correct_count"] / len(answers) * 100)
                payload["first_correct"] = quiz["first_correct"]
        return payload
