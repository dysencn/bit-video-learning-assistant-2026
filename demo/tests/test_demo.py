"""End-to-end tests using real FFmpeg and a loopback HTTP server; no AI services."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile
import io
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor

from PIL import Image, ImageDraw

from demo.jobs import JobManager
from demo.media import ffmpeg_path, run_ffmpeg
from demo.accounts import AccountStore, DEMO_TEACHER, DEMO_STUDENT
from demo.app import LearningStore

ROOT = Path(__file__).resolve().parent.parent


class DemoIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="video-demo-test-")
        cls.folder = Path(cls.temp.name)
        cls.data = cls.folder / "jobs"
        cls.frames = cls.folder / "frames"
        cls.frames.mkdir()
        a = Image.new("RGB", (640, 360), "#f7f5e9")
        draw = ImageDraw.Draw(a)
        draw.rectangle((40, 35, 580, 75), fill="#174f50")
        draw.text((55, 100), "SLIDE A - INTRODUCTION", fill="black", font_size=26)
        draw.rectangle((55, 160, 180, 290), fill="#659a8a")
        draw.ellipse((260, 160, 420, 300), fill="#eaa143")
        b = Image.new("RGB", (640, 360), "#edf0f9")
        draw = ImageDraw.Draw(b)
        draw.text((45, 45), "SLIDE B - COMPARISON", fill="#19317b", font_size=28)
        for i in range(4):
            draw.rectangle((60 + i * 135, 125, 140 + i * 135, 300 - i * 30), fill="#456bba")
        c = a.copy()
        draw = ImageDraw.Draw(c)
        draw.rectangle((440, 160, 590, 230), fill="#af3652")
        draw.text((445, 180), "NEW", fill="white", font_size=26)
        # Repeated A must disappear; a localized stable addition must survive.
        for index, image in enumerate([a, a, b, b, a, a, c, c], 1):
            image.save(cls.frames / f"{index:03d}.png")
        cls.video = cls.folder / "中文 课程.mp4"
        cls.silent = cls.folder / "silent.mp4"
        run_ffmpeg(["-loglevel", "error", "-framerate", "1", "-i", str(cls.frames / "%03d.png"),
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "10", "-c:a", "aac",
                    "-shortest", str(cls.video)], threading.Event())
        run_ffmpeg(["-loglevel", "error", "-i", str(cls.video), "-an", "-c:v", "copy", str(cls.silent)], threading.Event())
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.logs = (cls.folder / "server.log").open("wb")
        env = {**os.environ, "VIDEO_DEMO_DATA": str(cls.data), "PYTHONUTF8": "1"}
        cls.process = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--serve", "--no-browser", "--port", str(cls.port)],
                                       cwd=ROOT, env=env, stdout=cls.logs, stderr=cls.logs)
        for _ in range(100):
            try:
                with urlopen(cls.url + "/api/config", timeout=1) as response:
                    cls.token = json.load(response)["token"]
                break
            except (URLError, ConnectionError):
                time.sleep(0.1)
        else:
            cls.process.terminate()
            raise RuntimeError("Test server did not start")
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        cls.default_account = DEMO_TEACHER
        for username, password in [("teacher1", "Teach123!"), ("teacher2", "Teach456!"),
                                   ("student1", "Learn123!"), ("student2", "Learn456!")]:
            request = Request(cls.url + "/api/auth/login", data=json.dumps({"username": username, "password": password}).encode(),
                              headers={"X-Local-Token": cls.token, "Content-Type": "application/json"})
            with cls.client.open(request, timeout=10) as response:
                json.load(response)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.logs.close()
        cls.temp.cleanup()

    def request(self, path, data=None, **headers):
        request = Request(self.url + path, data=data,
                          headers={"X-Local-Token": self.token, "X-Account-ID": self.default_account, **headers})
        return self.client.open(request, timeout=30)

    def json_request(self, path, payload=None, account=None, **headers):
        if account is not None:
            headers["X-Account-ID"] = account
        data = json.dumps(payload).encode() if payload is not None else None
        with self.request(path, data=data, **{"Content-Type": "application/json", **headers}) as response:
            return json.load(response)

    def submit(self, source):
        with self.request("/api/jobs", data=source.read_bytes(), **{"X-Filename": quote(source.name), "Content-Type": "application/octet-stream"}) as response:
            return json.load(response)

    def wait_job(self, job_id):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            with self.request("/api/jobs/" + job_id) as response:
                job = json.load(response)
            if job["status"] not in {"uploading", "queued", "running", "cancelling"}:
                # Let the worker finish file cleanup and release its slot.
                time.sleep(0.15)
                return job
            time.sleep(0.1)
        self.fail("Job did not finish")

    def test_01_video_pdf_jpg_audio_roundtrip(self):
        job = self.wait_job(self.submit(self.video)["id"])
        self.assertEqual(job["status"], "completed", job["message"])
        self.assertEqual(len(job["result"]["pages"]), 3, job["result"])
        self.assertTrue(job["result"]["metadata"]["has_audio"])
        self.assertEqual(job["filename"], self.video.name)
        prefix = f"/api/jobs/{job['id']}"
        with self.request(prefix + "/files/pdf") as response:
            pdf = response.read()
            self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 3)
        with self.request(prefix + "/files/zip") as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
            self.assertEqual(len(archive.namelist()), 3)
            for name in archive.namelist():
                image = Image.open(io.BytesIO(archive.read(name)))
                self.assertEqual(image.size, (640, 360))
                image.verify()
        with self.request(prefix + "/files/audio") as response:
            audio = response.read()
        self.assertGreater(len(audio), 10000)
        audio_path = self.folder / "saved.mp3"
        audio_path.write_bytes(audio)
        output = run_ffmpeg(["-i", str(audio_path), "-f", "null", "-"], threading.Event())
        self.assertIn("Duration: 00:00:08.", output)
        with self.request(prefix + "/pages/1") as response:
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertFalse(any((self.data / job["id"]).glob("source.*")))

    def test_02_silent_video_still_exports_pdf(self):
        job = self.wait_job(self.submit(self.silent)["id"])
        self.assertEqual(job["status"], "completed", job["message"])
        self.assertIsNone(job["result"]["audio"])
        self.assertTrue(any("没有音轨" in item for item in job["result"]["warnings"]))
        with self.assertRaises(HTTPError) as caught:
            self.request(f"/api/jobs/{job['id']}/files/audio")
        self.assertEqual(caught.exception.code, 404)

    def test_03_invalid_video_fails_and_releases_slot(self):
        invalid = self.folder / "broken.mp4"
        invalid.write_bytes(b"this is not a video")
        job = self.wait_job(self.submit(invalid)["id"])
        self.assertEqual(job["status"], "failed")
        self.assertFalse(any((self.data / job["id"]).glob("source.*")))

    def test_04_cancel_job(self):
        job = self.submit(self.video)
        with self.request(f"/api/jobs/{job['id']}/cancel", data=b"") as response:
            self.assertIn(json.load(response)["status"], {"cancelling", "cancelled"})
        stopped = self.wait_job(job["id"])
        self.assertEqual(stopped["status"], "cancelled")
        self.assertFalse(any((self.data / job["id"]).glob("source.*")))

    def test_05_reject_foreign_origin_and_unknown_files(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/jobs", data=b"x", Origin="https://example.org")
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/jobs", data=b"x", **{"X-Local-Token": "wrong"})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/jobs", data=b"x", **{"X-Filename": "notes.txt"})
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/jobs/missing/files/pdf")
        self.assertEqual(caught.exception.code, 404)

    def test_06_restart_marks_incomplete_task(self):
        root = self.folder / "restart-jobs"
        manager = JobManager(root)
        job = manager.create("test.mp4", "balanced")
        (root / job["id"] / "input.part").write_bytes(b"partial")
        manager.close()
        restored = JobManager(root)
        try:
            self.assertEqual(restored.get(job["id"])["status"], "interrupted")
            self.assertFalse((root / job["id"] / "input.part").exists())
        finally:
            restored.close()

    def test_07_register_login_validation_and_plaintext_persistence(self):
        payload = {"username": "new_student", "password": "测试密码123", "name": "新同学", "role": "student"}
        user = self.json_request("/api/auth/register", payload)
        self.assertNotIn("password", user)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/auth/register", {**payload, "username": "NEW_STUDENT"})
        self.assertEqual(caught.exception.code, 409)
        for invalid in [{**payload, "role": "admin"}, {**payload, "role": []},
                        {**payload, "username": "x"}, {**payload, "password": "123"}]:
            with self.assertRaises(HTTPError) as caught:
                self.json_request("/api/auth/register", invalid)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/auth/login", {"username": "new_student", "password": "incorrect"})
        self.assertEqual(caught.exception.code, 401)
        signed_in = self.json_request("/api/auth/login", {"username": "NEW_STUDENT", "password": payload["password"]})
        self.assertEqual(signed_in["id"], user["id"])
        self.assertEqual(self.json_request("/api/auth/me", account=user["id"])["name"], "新同学")
        disk = json.loads((self.folder / "accounts.json").read_text(encoding="utf-8"))
        self.assertEqual(next(u for u in disk["users"] if u["id"] == user["id"])["password"], payload["password"])
        self.assertEqual(AccountStore(self.folder / "accounts.json").users, disk["users"])
        examples = self.json_request("/api/auth/demo-accounts")
        self.assertEqual(len(examples), 4)
        self.assertNotIn(user["id"], [u["id"] for u in examples])

    def test_08_real_roles_and_learning_acceptance_flow(self):
        for path in ["/api/courses?role=teacher", "/api/learning-records", "/api/jobs"]:
            with self.assertRaises(HTTPError) as caught:
                self.json_request(path, account="")
            self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/courses", {"title": "forbidden"}, account=DEMO_STUDENT, **{"X-Demo-Role": "teacher"})
        self.assertEqual(caught.exception.code, 403)
        course = self.json_request("/api/courses", {"title": "端到端测试课程", "description": "发布到学习反馈"})
        cid = course["id"]
        self.assertEqual(course["teacher_id"], DEMO_TEACHER)
        self.assertEqual(self.json_request("/api/courses", account="teacher-demo-2"), [])
        for viewer in [DEMO_STUDENT, "teacher-demo-2"]:
            with self.assertRaises(HTTPError) as caught:
                self.json_request(f"/api/courses/{cid}?role=teacher", account=viewer)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            self.json_request(f"/api/courses/{cid}/publish", {})
        self.assertEqual(caught.exception.code, 400)
        job = self.wait_job(self.submit(self.video)["id"])
        self.assertEqual(job["status"], "completed")
        self.json_request(f"/api/courses/{cid}/resources", {"name": "课件", "kind": "课件", "job_id": job["id"]})
        course = self.json_request(f"/api/courses/{cid}/questions", {"text": "1+1=?", "options": ["1", "2"], "answer": 1})
        qid = course["questions"][0]["id"]
        self.json_request(f"/api/courses/{cid}/publish", {})
        student_course = self.json_request(f"/api/courses/{cid}?role=teacher", account=DEMO_STUDENT)
        self.assertNotIn("answer", student_course["questions"][0])
        with self.request(f"/api/jobs/{job['id']}/files/pdf?account={DEMO_STUDENT}&preview=true", **{"X-Account-ID": ""}) as response:
            self.assertIn("inline", response.headers["Content-Disposition"])
            self.assertTrue(response.read().startswith(b"%PDF"))
        for answers in [{}, {qid: 99}, {qid: True}]:
            with self.assertRaises(HTTPError) as caught:
                self.json_request(f"/api/courses/{cid}/submissions", {"answers": answers}, account=DEMO_STUDENT)
            self.assertEqual(caught.exception.code, 400)
        result = self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 1}}, account=DEMO_STUDENT)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["student_id"], DEMO_STUDENT)
        self.assertEqual(result["details"][0]["answer"], 1)
        self.assertIn(result["id"], [r["id"] for r in self.json_request("/api/learning-records", account=DEMO_STUDENT)])
        self.assertEqual(self.json_request("/api/learning-records?role=teacher", account="student-demo-2"), [])
        self.assertEqual(self.json_request("/api/learning-records", account="teacher-demo-2"), [])
        self.assertIn(result["id"], [r["id"] for r in self.json_request("/api/learning-records")])
        self.assertIn(result["id"], [r["id"] for r in LearningStore(self.folder / "learning-platform.json").data["submissions"]])
        with self.assertRaises(HTTPError) as caught:
            self.json_request(f"/api/courses/{cid}/resources", {"name": "x"}, account="teacher-demo-2")
        self.assertEqual(caught.exception.code, 404)

    def test_09_student_video_ownership(self):
        with self.request("/api/jobs", data=self.video.read_bytes(), **{"X-Account-ID": DEMO_STUDENT,
                          "X-Filename": quote(self.video.name), "Content-Type": "application/octet-stream"}) as response:
            job = json.load(response)
        self.assertEqual(job["owner_id"], DEMO_STUDENT)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = self.json_request(f"/api/jobs/{job['id']}", account=DEMO_STUDENT)
            if job["status"] not in {"uploading", "queued", "running", "cancelling"}:
                break
            time.sleep(.1)
        self.assertEqual(job["status"], "completed")
        self.assertIn(job["id"], [j["id"] for j in self.json_request("/api/jobs", account=DEMO_STUDENT)])
        self.assertNotIn(job["id"], [j["id"] for j in self.json_request("/api/jobs")])
        for viewer in [DEMO_TEACHER, "student-demo-2"]:
            for suffix, payload in [("", None), ("/files/pdf", None), ("/pages/1", None), ("/cancel", {})]:
                with self.assertRaises(HTTPError) as caught:
                    self.json_request(f"/api/jobs/{job['id']}{suffix}", payload, account=viewer)
                self.assertEqual(caught.exception.code, 404)

    def test_10_password_change_logout_and_multiple_accounts(self):
        registered = self.json_request("/api/auth/register", {"username": "new_teacher", "password": "initial123", "name": "新教师", "role": "teacher"})
        uid = registered["id"]
        self.json_request("/api/auth/login", {"username": "new_teacher", "password": "initial123"})
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/auth/password", {"old_password": "incorrect", "new_password": "changed123"}, account=uid)
        self.assertEqual(caught.exception.code, 400)
        self.json_request("/api/auth/password", {"old_password": "initial123", "new_password": "changed123"}, account=uid)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/auth/login", {"username": "new_teacher", "password": "initial123"})
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.json_request("/api/auth/me", account=uid)["role"], "teacher")
        self.assertEqual(self.json_request("/api/auth/me", account=DEMO_STUDENT)["role"], "student")
        self.assertEqual(self.json_request("/api/auth/me", account=DEMO_TEACHER)["role"], "teacher")
        self.json_request("/api/auth/logout", {}, account=uid)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/auth/me", account=uid)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.json_request("/api/auth/me", account=DEMO_STUDENT)["role"], "student")
        self.json_request("/api/auth/login", {"username": "new_teacher", "password": "changed123"})
        self.assertEqual(self.json_request("/api/auth/me", account=uid)["id"], uid)

    def test_11_migrate_legacy_learning_data(self):
        path = self.folder / "legacy-learning.json"
        legacy = {"courses": [{"id": "legacy-course", "title": "旧课程"}],
                  "submissions": [{"id": "legacy-record", "course_id": "legacy-course", "student": "张同学", "score": 88}]}
        path.write_text(json.dumps(legacy), encoding="utf-8")
        store = LearningStore(path)
        self.assertEqual(store.data["courses"][0]["teacher_id"], DEMO_TEACHER)
        self.assertEqual(store.data["submissions"][0]["student_id"], DEMO_STUDENT)
        self.assertEqual(store.data["submissions"][0]["score"], 88)
        self.assertEqual(LearningStore(path).data, store.data)

    def test_12_wrong_questions_auto_archive_and_mastered(self):
        course = self.json_request("/api/courses", {"title": "错题本测试课程", "description": "验证错题归档"})
        cid = course["id"]
        course = self.json_request(f"/api/courses/{cid}/questions", {"text": "Python 中变量用于？",
                                                                     "options": ["保存数据", "删除文件", "打印输出"],
                                                                     "answer": 0, "knowledge_point": "变量概念"})
        qid = course["questions"][0]["id"]
        self.assertEqual(course["questions"][0]["knowledge_point_id"],
                         next(kp["id"] for kp in course["knowledge_points"] if kp["name"] == "变量概念"))
        self.json_request(f"/api/courses/{cid}/resources", {"name": "课件", "kind": "课件"})
        self.json_request(f"/api/courses/{cid}/publish", {})
        self.assertEqual(self.json_request("/api/wrong-questions", account=DEMO_STUDENT), [])
        result = self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 1}}, account=DEMO_STUDENT)
        self.assertEqual(result["score"], 0)
        wrong = self.json_request("/api/wrong-questions", account=DEMO_STUDENT)
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0]["question_id"], qid)
        self.assertEqual(wrong[0]["knowledge_point_name"], "变量概念")
        self.assertEqual(wrong[0]["wrong_count"], 1)
        self.assertEqual(wrong[0]["status"], "unresolved")
        # 再次答错同一题：不新增记录，只累加次数
        result = self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 2}}, account=DEMO_STUDENT)
        wrong = self.json_request("/api/wrong-questions", account=DEMO_STUDENT)
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0]["wrong_count"], 2)
        # 标记已掌握
        self.assertEqual(self.json_request(f"/api/wrong-questions/{wrong[0]['id']}/mastered", {}, account=DEMO_STUDENT)["status"], "mastered")
        self.assertEqual(self.json_request("/api/wrong-questions", account=DEMO_STUDENT)[0]["status"], "mastered")
        # 答对后不再归档
        result = self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 0}}, account=DEMO_STUDENT)
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(self.json_request("/api/wrong-questions", account=DEMO_STUDENT)), 1)
        # 教师无权访问错题本
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/wrong-questions", account=DEMO_TEACHER)
        self.assertEqual(caught.exception.code, 403)

    def test_13_duplicate_submission_does_not_duplicate_record(self):
        course = self.json_request("/api/courses", {"title": "防重复提交测试课程", "description": "验证同次重复点击"})
        cid = course["id"]
        course = self.json_request(f"/api/courses/{cid}/questions", {"text": "1+1=?", "options": ["1", "2"], "answer": 1})
        qid = course["questions"][0]["id"]
        self.json_request(f"/api/courses/{cid}/resources", {"name": "课件", "kind": "课件"})
        self.json_request(f"/api/courses/{cid}/publish", {})
        answers = {qid: 1}
        first = self.json_request(f"/api/courses/{cid}/submissions", {"answers": answers}, account=DEMO_STUDENT)
        # 同一答案 5 秒内再次提交：返回同一条记录，不新增
        second = self.json_request(f"/api/courses/{cid}/submissions", {"answers": answers}, account=DEMO_STUDENT)
        self.assertEqual(second["id"], first["id"])
        records = self.json_request("/api/learning-records", account=DEMO_STUDENT)
        self.assertEqual([r["id"] for r in records].count(first["id"]), 1)
        # 不同答案视为新提交，5 秒内也应新增
        third = self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 0}}, account=DEMO_STUDENT)
        self.assertNotEqual(third["id"], first["id"])
        records = self.json_request("/api/learning-records", account=DEMO_STUDENT)
        self.assertIn(first["id"], [r["id"] for r in records])
        self.assertIn(third["id"], [r["id"] for r in records])

    def test_14_spaced_repetition_review_schedule(self):
        course = self.json_request("/api/courses", {"title": "复习引擎测试课程", "description": "验证间隔重复调度"})
        cid = course["id"]
        course = self.json_request(f"/api/courses/{cid}/questions", {"text": "2+2=?", "options": ["3", "4", "5"],
                                                                     "answer": 1, "knowledge_point": "算术"})
        qid = course["questions"][0]["id"]
        self.json_request(f"/api/courses/{cid}/resources", {"name": "课件", "kind": "课件"})
        self.json_request(f"/api/courses/{cid}/publish", {})
        # 首次答错归档：1 天后到期，进入复习队列（按课程过滤，避免其它测试课程残留数据干扰）
        self.json_request(f"/api/courses/{cid}/submissions", {"answers": {qid: 0}}, account=DEMO_STUDENT)
        due = [r for r in self.json_request("/api/review/due", account=DEMO_STUDENT) if r["course_id"] == cid]
        self.assertEqual(len(due), 1)
        record = due[0]
        self.assertEqual(record["review_interval_days"], 1)
        self.assertEqual(record["review_count"], 0)
        self.assertEqual(record["knowledge_point_name"], "算术")
        self.assertGreater(record["next_review_at"], LearningStore.now())
        # 复习答对：间隔翻倍为 2 天，复习次数 +1
        result = self.json_request(f"/api/wrong-questions/{record['id']}/review", {"selected": 1}, account=DEMO_STUDENT)
        self.assertTrue(result["correct"])
        self.assertEqual(result["review_interval_days"], 2)
        self.assertEqual(result["review_count"], 1)
        # 复习答错：间隔重置为 1 天，wrong_count 累加
        result = self.json_request(f"/api/wrong-questions/{record['id']}/review", {"selected": 2}, account=DEMO_STUDENT)
        self.assertFalse(result["correct"])
        self.assertEqual(result["review_interval_days"], 1)
        self.assertEqual(result["wrong_count"], 2)
        # 连续答对至间隔 14 天自动视为掌握，移出复习队列
        record = [r for r in self.json_request("/api/review/due", account=DEMO_STUDENT) if r["course_id"] == cid][0]
        for _ in range(4):
            record = self.json_request(f"/api/wrong-questions/{record['id']}/review", {"selected": 1}, account=DEMO_STUDENT)
        self.assertEqual(record["review_interval_days"], 14)
        self.assertEqual(record["status"], "mastered")
        self.assertFalse(any(r["course_id"] == cid for r in self.json_request("/api/review/due", account=DEMO_STUDENT)))
        # 无效选项校验与教师权限
        with self.assertRaises(HTTPError) as caught:
            self.json_request(f"/api/wrong-questions/{record['id']}/review", {"selected": 9}, account=DEMO_STUDENT)
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.json_request("/api/review/due", account=DEMO_TEACHER)
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
