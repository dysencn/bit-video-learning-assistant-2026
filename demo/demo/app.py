"""Loopback-only HTTP API. Video bytes are streamed to this project's data folder."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import uuid
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from .jobs import BusyError, JobManager
from .media import PROFILES
from .accounts import AccountStore, DEMO_STUDENT, DEMO_TEACHER

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 8 * 1024**3
EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts"}
TOKEN = secrets.token_urlsafe(32)


class LearningStore:
    """Tiny local JSON store for the week-3 learning-flow demo.

    It deliberately lives beside the extracted-resource jobs so a fresh clone
    needs no database service, while all course and learning actions are real
    persisted writes rather than front-end mock data.
    """
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"courses": [], "submissions": []}
        if path.is_file():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        if not self.data["courses"]:
            self.data["courses"] = [{
                "id": "course-demo", "title": "Python 程序设计入门", "description": "认识变量、条件判断与基本程序结构。",
                "teacher": "王老师", "status": "published", "created_at": self.now(),
                "resources": [{"id": "res-demo", "name": "第一讲：变量与数据类型", "kind": "课件", "job_id": None}],
                "questions": [
                    {"id": "q1", "text": "Python 中用于保存数据的命名空间通常称为？", "options": ["变量", "函数", "循环", "模块"], "answer": 0},
                    {"id": "q2", "text": "下面哪个是 Python 的字符串？", "options": ["123", "True", "\"学习平台\"", "3.14"], "answer": 2},
                    {"id": "q3", "text": "print() 的主要作用是？", "options": ["输出内容", "删除文件", "创建数据库", "安装软件"], "answer": 0},
                ],
            }]
            self.save()
        migrated = False
        for course in self.data["courses"]:
            if "teacher_id" not in course:
                course["teacher_id"] = DEMO_TEACHER
                migrated = True
        for record in self.data["submissions"]:
            if "student_id" not in record:
                record["student_id"] = DEMO_STUDENT
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
        return result

    def courses(self, include_drafts=False, include_answers=False):
        with self.lock:
            items = self.data["courses"] if include_drafts else [c for c in self.data["courses"] if c["status"] == "published"]
            return [self.public_course(course, include_answers) for course in items]

    def course(self, course_id):
        return next((course for course in self.data["courses"] if course["id"] == course_id), None)


def create_app(data_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        jobs_root = data_dir or Path(os.environ.get("VIDEO_DEMO_DATA", ROOT / "data" / "jobs"))
        app.state.jobs = JobManager(jobs_root)
        app.state.accounts = AccountStore(jobs_root.parent / "accounts.json")
        app.state.learning = LearningStore(jobs_root.parent / "learning-platform.json")
        yield
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
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/api/config")
    async def config():
        return {"token": TOKEN, "max_bytes": MAX_BYTES, "version": "0.3.0", "local_only": True}

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
        return request.app.state.accounts.register(await json_object(request))

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
        return [c for c in courses if c["teacher_id"] == user["id"]] if teacher else courses

    @app.get("/api/courses/{course_id}")
    async def get_course(request: Request, course_id: str):
        user = request.app.state.accounts.current(request)
        course = request.app.state.learning.course(course_id)
        own = course and user["role"] == "teacher" and course["teacher_id"] == user["id"]
        if not course or (not own and course["status"] != "published"):
            raise HTTPException(404, "课程不存在或尚未发布")
        return request.app.state.learning.public_course(course, include_answers=bool(own))

    @app.post("/api/courses")
    async def create_course(request: Request):
        user = require_role(request, "teacher")
        payload = await json_object(request)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "请填写课程名称")
        with request.app.state.learning.lock:
            course = {"id": uuid.uuid4().hex[:12], "title": title[:80],
                      "description": str(payload.get("description", "")).strip()[:300],
                      "teacher": user["name"], "teacher_id": user["id"], "status": "draft", "created_at": LearningStore.now(),
                      "resources": [], "questions": []}
            request.app.state.learning.data["courses"].insert(0, course)
            request.app.state.learning.save()
            return request.app.state.learning.public_course(course, include_answers=True)

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
        with request.app.state.learning.lock:
            course["resources"].append({"id": uuid.uuid4().hex[:10], "name": name[:100],
                                        "kind": str(payload.get("kind", "课件"))[:20], "job_id": job_id})
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
            course["questions"].append({"id": uuid.uuid4().hex[:10], "text": text[:300],
                                        "options": [str(item).strip()[:100] for item in options[:4]], "answer": answer})
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
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            raise HTTPException(400, "答题数据格式不正确")
        details, correct = [], 0
        if not course["questions"]:
            raise HTTPException(400, "该课程尚未配置练习题")
        for question in course["questions"]:
            selected = answers.get(question["id"])
            if type(selected) is not int or not 0 <= selected < len(question["options"]):
                raise HTTPException(400, "请完成所有题目并选择有效选项")
            is_correct = selected == question["answer"]
            correct += int(is_correct)
            details.append({"question_id": question["id"], "selected": selected, "correct": is_correct,
                            "answer": question["answer"], "text": question["text"], "options": question["options"]})
        submission = {"id": uuid.uuid4().hex[:12], "course_id": course_id,
                      "student": user["name"], "student_id": user["id"],
                      "submitted_at": LearningStore.now(), "score": round(correct / len(course["questions"]) * 100),
                      "correct_count": correct, "total": len(course["questions"]), "details": details}
        with request.app.state.learning.lock:
            request.app.state.learning.data["submissions"].insert(0, submission)
            request.app.state.learning.save()
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
            return [{**r, "course_name": courses.get(r["course_id"], {}).get("title", "已删除课程")} for r in records]

    @app.get("/api/jobs")
    async def list_jobs(request: Request):
        user = request.app.state.accounts.current(request)
        return request.app.state.jobs.list(owner_id=user["id"])

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
        return manager.get(job["id"])

    def find_job(request: Request, job_id: str, owner_only=False):
        user = request.app.state.accounts.current(request)
        job = request.app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "找不到这个任务")
        if job.get("owner_id", DEMO_TEACHER) != user["id"]:
            published = request.app.state.learning.courses()
            shared = any(r.get("job_id") == job_id for c in published for r in c["resources"])
            if owner_only or not shared:
                raise HTTPException(404, "找不到这个任务或没有访问权限")
        return job

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        return find_job(request, job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(request: Request, job_id: str):
        find_job(request, job_id, owner_only=True)
        return request.app.state.jobs.cancel(job_id)

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
