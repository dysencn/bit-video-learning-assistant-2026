"""Teacher analytics over real HTTP, isolated data and account sessions."""
from http.cookiejar import CookieJar
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn

from demo.app import create_app
from demo.summaries import SummaryError


class TeacherAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="teacher-analytics-test-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "jobs"
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(create_app(self.data, seed_demo_history=False), host="127.0.0.1", port=port,
                                                   log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.fail("Analytics test server did not start")
            time.sleep(.02)
        with self.client.open(self.url + "/api/config", timeout=5) as response:
            self.token = json.load(response)["token"]

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(10)

    def request(self, path, payload=None, account="teacher-demo-1", method=None, **headers):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method,
                          headers={"X-Local-Token": self.token, "X-Account-ID": account,
                                   "Content-Type": "application/json", **headers})
        with self.client.open(request, timeout=5) as response:
            return json.load(response)

    def login(self, username, password):
        return self.request("/api/auth/login", {"username": username, "password": password})["id"]

    def register(self, username, role="student", major="m-soft"):
        self.request("/api/auth/register", {"username": username, "password": "Analytics123!",
                                            "name": username, "role": role, "major_id": major})
        return self.login(username, "Analytics123!")

    def course(self, teacher, title, publish=True, major_ids=None):
        course = self.request("/api/courses", {"title": title, "major_ids": major_ids or ["m-soft"]}, account=teacher)
        cid = course["id"]
        self.request(f"/api/courses/{cid}/resources", {"name": "Analytics fixture"}, account=teacher)
        for number in range(3):
            course = self.request(f"/api/courses/{cid}/questions",
                                  {"text": f"Question {number}", "options": ["correct", "wrong"],
                                   "answer": 0, "knowledge_point": f"Point {number}"}, account=teacher)
        if publish:
            self.request(f"/api/courses/{cid}/publish", {}, account=teacher)
        return course

    def submit(self, student, course, correct):
        answers = {q["id"]: (0 if index < correct else 1) for index, q in enumerate(course["questions"])}
        return self.request(f"/api/courses/{course['id']}/submissions",
                            {"chapter_id": course["chapters"][0]["id"], "answers": answers}, account=student)

    def test_analytics_requires_teacher_session_even_with_forged_role(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/teacher/analytics", account="")
        self.assertEqual(caught.exception.code, 401)
        student = self.login("student1", "Learn123!")
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/teacher/analytics?role=teacher&major_id=m-ai", account=student,
                         **{"X-Demo-Role": "teacher"})
        self.assertEqual(caught.exception.code, 403)

    def test_statistics_use_owned_courses_and_course_audiences(self):
        teacher = self.login("teacher1", "Teach123!")
        # Keep this statistics fixture limited to one audience across all built-in classes.
        baseline = self.request("/api/courses")
        baseline_titles = {course["title"] for course in baseline}
        for item in baseline:
            self.request(f"/api/courses/{item['id']}/audience", {"major_ids": ["m-soft"]})
        student_zero = self.login("student1", "Learn123!")
        student_low = self.register("analytics_low")
        student_pass = self.register("analytics_pass")
        student_idle = self.register("analytics_idle")
        course = self.course(teacher, "Owned published course")
        self.course(teacher, "Owned draft", publish=False)
        for student, correct in [(student_zero, 0), (student_low, 1), (student_pass, 2)]:
            self.submit(student, course, correct)

        peer_teacher = self.register("analytics_peer", role="teacher")
        self.submit(student_zero, self.course(peer_teacher, "Peer course"), 3)
        other_teacher = self.login("teacher2", "Teach456!")
        other_student = self.login("student2", "Learn456!")
        self.submit(other_student, self.course(other_teacher, "Other major", major_ids=["m-ai"]), 3)

        result = self.request("/api/teacher/analytics?major_id=m-ai&teacher_id=" + other_teacher)
        overview = result["overview"]
        self.assertEqual(overview["student_count"], 6)
        self.assertEqual(overview["participant_count"], 3)
        self.assertEqual(overview["submission_count"], 3)
        self.assertEqual(overview["average_score"], 33)
        self.assertEqual(overview["pass_rate"], 33)
        self.assertEqual(overview["attention_count"], 5)
        self.assertEqual({c["title"] for c in result["courses"]},
                         baseline_titles | {"Owned published course", "Owned draft"})
        self.assertNotIn(other_student, [s["student_id"] for s in result["students"]])
        self.assertEqual({s["student_id"] for s in result["students"] if s["not_started"]},
                         {student_idle, "student-demo-4", "student-demo-8"})
        self.assertEqual([s["student_id"] for s in result["students"] if not s["not_started"]],
                         [student_zero, student_low, student_pass])
        profiles = {s["student_id"]: s for s in result["students"]}
        self.assertEqual(profiles[student_zero]["average_score"], 0)
        self.assertEqual(profiles[student_zero]["mastery_score"], 0)
        self.assertEqual(profiles[student_zero]["weak_points"], ["Point 0", "Point 1"])
        self.assertEqual(profiles[student_pass]["mastery_score"], 67)
        self.assertIsNone(profiles[student_idle]["average_score"])
        self.assertTrue(profiles[student_idle]["not_started"])
        self.assertNotIn("password", json.dumps(result))

        other_result = self.request("/api/teacher/analytics", account=other_teacher)
        self.assertEqual(other_result["overview"]["submission_count"], 1)
        self.assertEqual(other_result["overview"]["average_score"], 100)
        self.assertEqual({s["student_id"] for s in other_result["students"]}, {other_student, "student-demo-5", "student-demo-7", "student-demo-3", "student-demo-6"})

    def test_empty_group_has_no_fabricated_scores(self):
        teacher = self.register("analytics_empty", role="teacher", major="m-cs")
        result = self.request("/api/teacher/analytics", account=teacher)
        self.assertEqual(result["students"], [])
        self.assertEqual(result["courses"], [])
        self.assertEqual(result["overview"]["student_count"], 0)
        self.assertEqual(result["overview"]["participant_count"], 0)
        self.assertIsNone(result["overview"]["average_score"])
        self.assertIsNone(result["overview"]["pass_rate"])

    def test_analytics_restores_persisted_submissions_after_restart(self):
        teacher = self.login("teacher1", "Teach123!")
        student = self.login("student1", "Learn123!")
        self.submit(student, self.course(teacher, "Persisted course"), 2)
        before = self.request("/api/teacher/analytics")
        self.stop_server()
        self.start_server()
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/teacher/analytics")
        self.assertEqual(caught.exception.code, 401)
        self.login("teacher1", "Teach123!")
        self.assertEqual(self.request("/api/teacher/analytics"), before)

    def test_video_and_summary_deletion_preserve_grades_profiles_and_review(self):
        """Resource cleanup must not remove a teammate's persisted learning evidence."""
        teacher = self.login("teacher1", "Teach123!")
        student = self.login("student1", "Learn123!")
        course = self.course(teacher, "Video cleanup regression")
        self.submit(student, course, 1)
        app = self.server.config.app
        jobs = app.state.jobs
        job = jobs.create("test.mp4", "balanced", teacher)
        job_id = job["id"]
        folder = jobs.root / job_id
        (folder / "slides").mkdir()
        (folder / "slides" / "slide-0001.jpg").write_bytes(b"image fixture")
        (folder / "slides.pdf").write_bytes(b"%PDF-test fixture")
        (folder / "summary.html").write_text("test-only summary fixture", encoding="utf-8")
        jobs.update(job_id, status="completed", result={"pages": [{"number": 1, "filename": "slide-0001.jpg", "timestamp": 0}]},
                    summary={"status": "completed", "available": True})
        jobs.active = None
        self.request(f"/api/courses/{course['id']}/resources", {"name": "Video slides", "job_id": job_id}, account=teacher)

        analytics = self.request("/api/teacher/analytics")
        records = self.request("/api/learning-records", account=student)
        wrong = self.request("/api/wrong-questions", account=student)
        self.assertTrue(wrong)
        # Published resources are readable in the course; private task state is not.
        request = Request(self.url + f"/api/jobs/{job_id}/files/pdf", headers={"X-Account-ID": student})
        with self.client.open(request, timeout=5) as response:
            self.assertTrue(response.read().startswith(b"%PDF"))
        for suffix, method in [("", "GET"), ("", "DELETE"), ("/summary/view", "GET"), ("/summary", "DELETE")]:
            with self.subTest(suffix=suffix, method=method), self.assertRaises(HTTPError) as caught:
                self.request(f"/api/jobs/{job_id}{suffix}", account=student, method=method)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()
        self.assertEqual(self.request("/api/teacher/analytics"), analytics)

        self.request(f"/api/jobs/{job_id}/summary", method="DELETE")
        self.assertFalse((folder / "summary.html").exists())
        self.assertTrue((folder / "slides.pdf").is_file())
        self.assertEqual(self.request("/api/teacher/analytics"), analytics)
        self.assertEqual(self.request(f"/api/jobs/{job_id}")["summary"], {})

        deleted = self.request(f"/api/jobs/{job_id}", method="DELETE")
        self.assertEqual(deleted["removed_resources"], 1)
        self.assertFalse(folder.exists())
        saved_course = self.request(f"/api/courses/{course['id']}")
        self.assertEqual(len(saved_course["resources"]), 1)
        self.assertEqual(len(saved_course["questions"]), 3)
        self.assertEqual(self.request("/api/teacher/analytics"), analytics)
        self.assertEqual(self.request("/api/learning-records", account=student), records)
        self.assertEqual(self.request("/api/wrong-questions", account=student), wrong)
        self.stop_server()
        self.start_server()
        self.login("teacher1", "Teach123!")
        self.login("student1", "Learn123!")
        self.assertEqual(self.request("/api/jobs"), [])
        self.assertEqual(self.request("/api/teacher/analytics"), analytics)
        self.assertEqual(self.request("/api/learning-records", account=student), records)
        self.assertEqual(self.request("/api/wrong-questions", account=student), wrong)

    def test_session_api_keys_never_leak_into_teacher_profiles_or_persist(self):
        teacher = self.login("teacher1", "Teach123!")
        student = self.login("student1", "Learn123!")
        self.submit(student, self.course(teacher, "Key isolation regression"), 2)
        before = self.request("/api/teacher/analytics")
        fixture_key = "test-only-session-secret"
        with patch("demo.summaries.read_api_key", side_effect=SummaryError("No environment key", 400)):
            self.request("/api/ai/key", {"api_key": fixture_key}, account=student)
            self.assertTrue(self.request("/api/ai/key", account=student)["configured"])
            self.assertFalse(self.request("/api/ai/key", account=teacher)["configured"])
            self.assertEqual(self.request("/api/teacher/analytics"), before)
            for file in self.data.parent.rglob("*.json"):
                self.assertNotIn(fixture_key, file.read_text(encoding="utf-8"))
            self.request("/api/auth/logout", {}, account=student)
            self.login("student1", "Learn123!")
            self.assertFalse(self.request("/api/ai/key", account=student)["configured"])
        self.assertEqual(self.request("/api/teacher/analytics"), before)


if __name__ == "__main__":
    unittest.main()
