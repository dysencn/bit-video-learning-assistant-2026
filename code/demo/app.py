"""Loopback-only HTTP API. Video bytes are streamed to this project's data folder."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import uuid
from urllib.parse import quote, unquote, urlparse
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from .jobs import BusyError, JobManager
from .media import PROFILES
from .demo_catalog import demo_courses, upgrade_catalog, seed_activity
from .learning_metrics import accepts_student, build_profiles, teacher_analytics as aggregate_teacher
from .accounts import AccountStore, DEMO_STUDENT, DEMO_TEACHER
from .summaries import SummaryManager, SummaryError, SUMMARY_CSP
from . import summaries as summary_service
from .transcriptions import TranscriptionManager, TranscriptionError
from . import transcriptions as transcription_service

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
        if upgrade_catalog(self.data):
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

    def public_course(self, course, include_answers=False):
        result = dict(course)
        result["resources"] = [dict(item) for item in course["resources"]]
        result["questions"] = [dict(item) for item in course["questions"]]
        if not include_answers:
            for question in result["questions"]:
                question.pop("answer", None)
                question.pop("explanation", None)
        return result

    def courses(self, include_drafts=False, include_answers=False):
        with self.lock:
            items = self.data["courses"] if include_drafts else [c for c in self.data["courses"] if c["status"] == "published"]
            return [self.public_course(course, include_answers) for course in items]

    def course(self, course_id):
        return next((course for course in self.data["courses"] if course["id"] == course_id), None)


def create_app(data_dir: Path | None = None, *, seed_demo_history: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        jobs_root = data_dir or Path(os.environ.get("VIDEO_DEMO_DATA", ROOT / "data" / "jobs"))
        app.state.jobs = JobManager(jobs_root)
        app.state.summaries = SummaryManager(app.state.jobs)
        app.state.transcriptions = TranscriptionManager(app.state.jobs)
        app.state.accounts = AccountStore(jobs_root.parent / "accounts.json")
        app.state.learning = LearningStore(jobs_root.parent / "learning-platform.json")
        history_enabled = seed_demo_history if seed_demo_history is not None else os.environ.get("VIDEO_DEMO_SEED_HISTORY", "1") != "0"
        if history_enabled:
            if seed_activity(app.state.learning.data, app.state.accounts.users):
                app.state.learning.save()
        yield
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
        return {"token": TOKEN, "max_bytes": MAX_BYTES, "version": "0.5.0", "local_only": True,
                "features": {"course_audiences": True, "student_profiles": True}}

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
        return request.app.state.learning.public_course(course, include_answers=bool(own))

    def course_audience(request, payload):
        valid = [major["id"] for major in request.app.state.learning.data["majors"]]
        selected = payload.get("major_ids")
        # Old clients may still submit a single major_id; teachers are no longer restricted by it.
        if "major_ids" not in payload:
            selected = [payload["major_id"]] if payload.get("major_id") else valid
        if (not isinstance(selected, list) or not selected
                or any(not isinstance(mid, str) or mid not in valid for mid in selected)):
            raise HTTPException(400, "请至少选择一个有效的学生专业")
        return list(dict.fromkeys(selected))

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
                      "teacher": user["name"], "teacher_id": user["id"], "status": "draft", "created_at": LearningStore.now(),
                      "major_ids": major_ids,
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
        with request.app.state.learning.lock:
            course["major_ids"] = audience
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/chapters")
    async def add_chapter(request: Request, course_id: str):
        course = owned_course(request, course_id)
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写章节名称")
        with request.app.state.learning.lock:
            order = max((c["order"] for c in course["chapters"]), default=0) + 1
            chapter = {"id": uuid.uuid4().hex[:10], "title": title[:50], "order": order}
            course["chapters"].append(chapter)
            request.app.state.learning.save()
            return dict(chapter)

    @app.post("/api/courses/{course_id}/chapters/{chapter_id}")
    async def rename_chapter(request: Request, course_id: str, chapter_id: str):
        course = owned_course(request, course_id)
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写章节名称")
        with request.app.state.learning.lock:
            chapter = next((c for c in course["chapters"] if c["id"] == chapter_id), None)
            if not chapter:
                raise HTTPException(404, "章节不存在")
            chapter["title"] = title[:50]
            request.app.state.learning.save()
            return dict(chapter)

    @app.post("/api/courses/{course_id}/resources")
    async def add_resource(request: Request, course_id: str):
        course = owned_course(request, course_id)
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
                                        "chapter_id": chapter_id})
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/questions")
    async def add_question(request: Request, course_id: str):
        course = owned_course(request, course_id)
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
            course["questions"].append({"id": uuid.uuid4().hex[:10], "text": text[:300],
                                        "options": [str(item).strip()[:100] for item in options[:4]], "answer": answer,
                                        "knowledge_point_id": knowledge_point_id, "chapter_id": chapter_id,
                                        "explanation": str(payload.get("explanation", "")).strip()[:2000]})
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/publish")
    async def publish_course(request: Request, course_id: str):
        course = owned_course(request, course_id)
        if not course:
            raise HTTPException(404, "课程不存在")
        if not course["resources"] or not course["questions"]:
            raise HTTPException(400, "发布前至少需要一份学习资源和一道练习题")
        with request.app.state.learning.lock:
            course["status"] = "published"
            request.app.state.learning.save()
        return request.app.state.learning.public_course(course, include_answers=True)

    @app.post("/api/courses/{course_id}/submissions")
    async def submit_answers(request: Request, course_id: str):
        user = require_role(request, "student")
        payload = await json_object(request)
        course = request.app.state.learning.course(course_id)
        if not course or course["status"] != "published":
            raise HTTPException(404, "课程不存在或未发布")
        if not accepts_student(course, user):
            raise HTTPException(404, "课程不存在或未发布")
        # 章节练习：传 chapter_id 只判该章题目，不传则整课判分。
        chapter_id = payload.get("chapter_id")
        questions = course["questions"]
        if chapter_id is not None:
            if chapter_id not in {c["id"] for c in course["chapters"]}:
                raise HTTPException(400, "章节不存在")
            questions = [q for q in course["questions"] if q.get("chapter_id") == chapter_id]
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            raise HTTPException(400, "答题数据格式不正确")
        details, correct = [], 0
        if not questions:
            raise HTTPException(400, "该章节尚未配置练习题")
        for question in questions:
            selected = answers.get(question["id"])
            if type(selected) is not int or not 0 <= selected < len(question["options"]):
                raise HTTPException(400, "请完成所有题目并选择有效选项")
            is_correct = selected == question["answer"]
            correct += int(is_correct)
            details.append({"question_id": question["id"], "selected": selected, "correct": is_correct,
                            "answer": question["answer"], "text": question["text"], "options": question["options"],
                            "chapter_id": question.get("chapter_id"), "explanation": question.get("explanation", ""),
                            "knowledge_point_name": next((point["name"] for point in course.get("knowledge_points", [])
                                                          if point["id"] == question.get("knowledge_point_id")), "未分类")})
        submission = {"id": uuid.uuid4().hex[:12], "course_id": course_id,
                      "student": user["name"], "student_id": user["id"],
                      "submitted_at": LearningStore.now(), "score": round(correct / len(questions) * 100),
                      "correct_count": correct, "total": len(questions), "details": details,
                      "chapter_id": chapter_id}
        store = request.app.state.learning
        with store.lock:
            # 防重复提交：同一学生同一课程在 5 秒内提交了完全相同的答案，视为同次重复点击，返回最近一次记录，不新增。
            signature = _answers_signature(answers)
            recent = next((r for r in store.data["submissions"]
                           if r["student_id"] == user["id"] and r["course_id"] == course_id
                           and _within_seconds(r["submitted_at"], submission["submitted_at"], 5)
                           and _answers_signature({d["question_id"]: d["selected"] for d in r["details"]}) == signature), None)
            if recent:
                return recent
            store.data["submissions"].insert(0, submission)
            # 错题自动归档：答错的题目进入错题本，同一道题不重复新增，只累加错误次数。
            kp_index = {kp["id"]: kp["name"] for kp in course.get("knowledge_points", [])}
            chapter_index = {c["id"]: c["title"] for c in course.get("chapters", [])}
            question_by_id = {question["id"]: question for question in course["questions"]}
            wrong_book = store.data.setdefault("wrong_questions", [])
            existing = {w["question_id"]: w for w in wrong_book if w["student_id"] == user["id"] and w["course_id"] == course_id}
            for detail in details:
                if not detail["correct"]:
                    question = question_by_id.get(detail["question_id"], {})
                    kp_id = question.get("knowledge_point_id")
                    chapter_id = question.get("chapter_id") or detail.get("chapter_id")
                    record = existing.get(detail["question_id"])
                    if record:
                        record["wrong_count"] += 1
                        record["last_wrong_at"] = submission["submitted_at"]
                        record["status"] = "unresolved"
                        record["knowledge_point_id"] = kp_id
                        record["knowledge_point_name"] = kp_index.get(kp_id, "未分类")
                        record["chapter_id"] = chapter_id
                        record["chapter_title"] = chapter_index.get(chapter_id, "")
                        # 做题又答错：说明仍未掌握，复习调度重置为 1 天后。
                        record["review_interval_days"] = 1
                        record["next_review_at"] = _days_later(1)
                    else:
                        record = {"id": uuid.uuid4().hex[:12], "student_id": user["id"], "course_id": course_id,
                                  "question_id": detail["question_id"], "knowledge_point_id": kp_id,
                                  "knowledge_point_name": kp_index.get(kp_id, "未分类"),
                                  "chapter_id": chapter_id, "chapter_title": chapter_index.get(chapter_id, ""),
                                  "question_snapshot": {"text": question.get("text", detail["text"]),
                                                        "options": question.get("options", detail.get("options", [])),
                                                        "answer": question.get("answer", detail["answer"]),
                                                        "explanation": question.get("explanation", "")},
                                  "wrong_count": 1, "last_wrong_at": submission["submitted_at"],
                                  "first_wrong_at": submission["submitted_at"],
                                  "status": "unresolved",
                                  "review_interval_days": 1, "next_review_at": _days_later(1),
                                  "review_count": 0}
                        existing[detail["question_id"]] = record
                        wrong_book.append(record)
            store.save()
        return submission

    @app.get("/api/learning-records")
    async def learning_records(request: Request):
        user = request.app.state.accounts.current(request)
        store = request.app.state.learning
        with store.lock:
            own_courses = {c["id"]: c for c in store.data["courses"] if c["teacher_id"] == user["id"]}
            courses = {c["id"]: c for c in store.data["courses"]}
            records = [r for r in store.data["submissions"] if
                       (r["course_id"] in own_courses if user["role"] == "teacher" else r["student_id"] == user["id"])]
            result = []
            for r in records:
                course = courses.get(r["course_id"], {})
                chapter_title = ""
                if r.get("chapter_id"):
                    chapter = next((c for c in course.get("chapters", []) if c["id"] == r["chapter_id"]), None)
                    chapter_title = chapter["title"] if chapter else ""
                result.append({**r, "course_name": course.get("title", "已删除课程"), "chapter_title": chapter_title})
            return result

    @app.get("/api/teacher/analytics")
    async def teacher_analytics(request: Request):
        teacher = require_role(request, "teacher")
        store = request.app.state.learning
        # Always acquire account data before the learning lock (same order as registration).
        students = request.app.state.accounts.students()
        with store.lock:
            courses = [course for course in store.data["courses"] if course["teacher_id"] == teacher["id"]]
            return aggregate_teacher(courses, store.data["submissions"], students)

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
            profiles = build_profiles(courses, store.data["submissions"], students)
            own = next((profile for profile in profiles if profile["student_id"] == student["id"]), None)
            if own is None:
                own = {"student_id": student["id"], "student": student["name"], "major_id": student.get("major_id"),
                       "submission_count": 0, "course_count": 0, "available_course_count": 0,
                       "average_score": None, "latest_submitted_at": None, "weak_points": [],
                       "course_participation": 0, "practice_activity": 0, "mastery_score": 0,
                       "needs_attention": False, "not_started": True}
            return {"profile": own, "scope": "本人可学习及已参与课程；仅统计正式练习提交，复习不计分"}

    @app.get("/api/wrong-questions")
    async def wrong_questions(request: Request):
        user = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            names = {course["id"]: course["title"] for course in store.data["courses"]}
            return [{**record, "course_name": names.get(record["course_id"], "历史课程")}
                    for record in store.data.setdefault("wrong_questions", [])
                    if record["student_id"] == user["id"]]

    @app.post("/api/wrong-questions/{question_id}/mastered")
    async def mark_mastered(request: Request, question_id: str):
        user = require_role(request, "student")
        store = request.app.state.learning
        with store.lock:
            record = next((r for r in store.data.setdefault("wrong_questions", [])
                           if r["id"] == question_id and r["student_id"] == user["id"]), None)
            if not record:
                raise HTTPException(404, "错题记录不存在")
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
            records = [dict(r) for r in store.data.setdefault("wrong_questions", [])
                       if r["student_id"] == user["id"] and r["status"] == "unresolved"]
        now = LearningStore.now()
        records.sort(key=lambda r: (0 if r.get("next_review_at", "") <= now else 1, r.get("next_review_at", "")))
        return records

    @app.post("/api/wrong-questions/{question_id}/review")
    async def review_question(request: Request, question_id: str):
        """复习作答：答对则间隔翻倍（封顶 14 天，连续间隔满 14 天自动视为掌握）；答错则重置为 1 天。"""
        user = require_role(request, "student")
        payload = await json_object(request)
        selected = payload.get("selected")
        store = request.app.state.learning
        with store.lock:
            record = next((r for r in store.data.setdefault("wrong_questions", [])
                           if r["id"] == question_id and r["student_id"] == user["id"]), None)
            if not record:
                raise HTTPException(404, "错题记录不存在")
            snapshot = record["question_snapshot"]
            options, answer = snapshot["options"], snapshot["answer"]
            if type(selected) is not int or not 0 <= selected < len(options):
                raise HTTPException(400, "请选择有效选项")
            correct = selected == answer
            now = LearningStore.now()
            record["last_reviewed_at"] = now
            record["review_count"] = record.get("review_count", 0) + 1
            record["last_review_result"] = correct
            if correct:
                interval = min(record.get("review_interval_days", 1) * 2, 14)
                record["review_interval_days"] = interval
                record["next_review_at"] = _days_later(interval)
                if interval >= 14:
                    record["status"] = "mastered"
                    record["mastered_at"] = now
            else:
                record["wrong_count"] += 1
                record["last_wrong_at"] = now
                record["review_interval_days"] = 1
                record["next_review_at"] = _days_later(1)
                record["status"] = "unresolved"
            store.save()
            return {**dict(record), "correct": correct, "answer": answer}

    @app.get("/api/jobs")
    async def list_jobs(request: Request):
        user = request.app.state.accounts.current(request)
        return [summary_options(request, job) for job in request.app.state.jobs.list(owner_id=user["id"])]

    def summary_options(request, job):
        return {**job, "summary_options": request.app.state.summaries.options(job)}

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
        with summaries.lock, transcriptions.lock, learning.lock, jobs.lock:
            if summaries.active == job_id:
                raise HTTPException(409, "总结正在生成，请等待完成后再删除任务")
            if transcriptions.active == job_id:
                raise HTTPException(409, "录音正在转写，请等待完成后再删除任务")
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

    @app.get("/api/jobs/{job_id}/files/{kind}")
    async def download(request: Request, job_id: str, kind: str, preview: bool = False):
        job = find_job(request, job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "任务尚未完成")
        files = {"pdf": ("slides.pdf", "application/pdf"), "zip": ("slides.zip", "application/zip"),
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
