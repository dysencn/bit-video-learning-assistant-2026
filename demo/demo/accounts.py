"""Project-local demo accounts and server-side login sessions.

Passwords intentionally remain plaintext, as required for this classroom demo.
Only the explicitly seeded demo credentials are exposed on the login page.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

DEMO_TEACHER = "teacher-demo-1"
DEMO_STUDENT = "student-demo-1"
SESSION_SECONDS = 12 * 60 * 60
# 演示账号按专业组归属：teacher1/student1 软件工程，teacher2/student2 人工智能（与预置课程同组，便于演示隔离）。
DEMO_ACCOUNTS = [
    {"id": DEMO_TEACHER, "username": "teacher1", "password": "Teach123!", "name": "王老师", "role": "teacher", "demo": True, "major_id": "m-soft"},
    {"id": "teacher-demo-2", "username": "teacher2", "password": "Teach456!", "name": "李老师", "role": "teacher", "demo": True, "major_id": "m-ai"},
    {"id": DEMO_STUDENT, "username": "student1", "password": "Learn123!", "name": "张同学", "role": "student", "demo": True, "major_id": "m-soft"},
    {"id": "student-demo-2", "username": "student2", "password": "Learn456!", "name": "陈同学", "role": "student", "demo": True, "major_id": "m-ai"},
]


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.sessions: dict[str, tuple[str, float]] = {}
        # Do not overwrite an unreadable account file with fresh demo accounts.
        if path.is_file():
            self.users = json.loads(path.read_text(encoding="utf-8"))["users"]
            # 旧账号补专业组归属：演示账号按预置映射补，其他账号默认补软件工程组。
            demo_major = {u["username"]: u["major_id"] for u in DEMO_ACCOUNTS}
            migrated = False
            for user in self.users:
                if not user.get("major_id"):
                    user["major_id"] = demo_major.get(user["username"], "m-soft")
                    migrated = True
            if migrated:
                self.save()
        else:
            self.users = [dict(user) for user in DEMO_ACCOUNTS]
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"users": self.users}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def public(user):
        return {key: user.get(key) for key in ("id", "username", "name", "role", "major_id")}

    def examples(self):
        with self.lock:
            return [{**self.public(user), "password": user["password"]} for user in self.users if user.get("demo")]

    def register(self, payload):
        username = payload.get("username", "")
        name = payload.get("name", "")
        password = payload.get("password", "")
        role = payload.get("role")
        major_id = payload.get("major_id", "")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username.strip()):
            raise HTTPException(400, "账号须为 3–32 位字母、数字或下划线")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 30:
            raise HTTPException(400, "姓名须为 1–30 个字符")
        self.validate_password(password)
        if not isinstance(role, str) or role not in {"teacher", "student"}:
            raise HTTPException(400, "请选择学生或教师身份")
        if not isinstance(major_id, str) or not major_id.strip():
            raise HTTPException(400, "请选择专业组")
        with self.lock:
            if any(u["username"].lower() == username.strip().lower() for u in self.users):
                raise HTTPException(409, "该账号已注册，请使用其他账号或直接登录")
            user = {"id": uuid.uuid4().hex, "username": username.strip(), "name": name.strip(),
                    "password": password, "role": role, "demo": False, "major_id": major_id.strip(),
                    "created_at": datetime.now(timezone.utc).isoformat()}
            self.users.append(user)
            self.save()
            return self.public(user)

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not 6 <= len(password) <= 128 or not password.strip():
            raise HTTPException(400, "密码须为 6–128 个字符，且不能全部为空格")

    def login(self, payload):
        username, password = payload.get("username", ""), payload.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            raise HTTPException(400, "请填写账号和密码")
        with self.lock:
            user = next((u for u in self.users if u["username"].lower() == username.strip().lower()), None)
            if not user or not secrets.compare_digest(user["password"].encode(), password.encode()):
                raise HTTPException(401, "账号或密码不正确")
            return self.login_response(user)

    def login_response(self, user):
        now = time.time()
        self.sessions = {token: value for token, value in self.sessions.items() if value[1] > now}
        token = secrets.token_urlsafe(32)
        self.sessions[token] = (user["id"], now + SESSION_SECONDS)
        response = JSONResponse(self.public(user))
        # One HttpOnly cookie per account allows teacher/student tabs to coexist.
        # The public account ID in a page URL selects a cookie, never authenticates it.
        response.set_cookie(f"learning_session_{user['id']}", token, max_age=SESSION_SECONDS,
                            httponly=True, samesite="strict", path="/")
        return response

    def current(self, request: Request):
        account_id = request.headers.get("x-account-id") or request.query_params.get("account", "")
        token = request.cookies.get(f"learning_session_{account_id}", "")
        with self.lock:
            session = self.sessions.get(token)
            if session and session[0] == account_id and session[1] > time.time():
                user = next((u for u in self.users if u["id"] == account_id), None)
                if user:
                    return self.public(user)
        raise HTTPException(401, "请先登录；服务重启或登录过期后需重新登录")

    def logout(self, request):
        user = self.current(request)
        key = f"learning_session_{user['id']}"
        with self.lock:
            self.sessions.pop(request.cookies.get(key, ""), None)
        response = JSONResponse({"message": "已退出登录"})
        response.delete_cookie(key, path="/", httponly=True, samesite="strict")
        return response

    def change_password(self, request, payload):
        current = self.current(request)
        old, new = payload.get("old_password", ""), payload.get("new_password", "")
        self.validate_password(new)
        with self.lock:
            user = next(u for u in self.users if u["id"] == current["id"])
            if not isinstance(old, str) or not secrets.compare_digest(user["password"].encode(), old.encode()):
                raise HTTPException(400, "原密码不正确")
            user["password"] = new
            self.save()
            self.sessions = {token: value for token, value in self.sessions.items() if value[0] != user["id"]}
            return self.login_response(user)
