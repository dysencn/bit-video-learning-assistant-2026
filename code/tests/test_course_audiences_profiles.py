"""Course audiences, private profiles, demo migration and review integration over real HTTP."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn

from demo.accounts import AccountStore
from demo.app import create_app, LearningStore
from demo.demo_catalog import seed_activity


class AudienceProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audience-profile-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.app = create_app(self.folder / "jobs", seed_demo_history=False)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.fail("Test server did not start")
            time.sleep(.02)
        with self.client.open(self.url + "/api/config", timeout=5) as response:
            self.token = json.load(response)["token"]
        for username, password in [("teacher1", "Teach123!"), ("teacher2", "Teach456!"),
                                   ("student1", "Learn123!"), ("student2", "Learn456!"), ("student3", "Learn123!")]:
            self.request("/api/auth/login", {"username": username, "password": password})

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(10)

    def request(self, path, payload=None, account="teacher-demo-1"):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json", "X-Local-Token": self.token,
                                   "X-Account-ID": account})
        with self.client.open(request, timeout=5) as response:
            return json.load(response)

    def error(self, status, path, payload=None, account="teacher-demo-1"):
        with self.assertRaises(HTTPError) as caught:
            self.request(path, payload, account)
        self.assertEqual(caught.exception.code, status)
        caught.exception.close()

    def make_course(self, audience, teacher="teacher-demo-1"):
        course = self.request("/api/courses", {"title": "跨专业小班测试", "major_ids": audience}, teacher)
        cid = course["id"]
        self.request(f"/api/courses/{cid}/resources", {"name": "测试讲义"}, teacher)
        for n in range(2):
            course = self.request(f"/api/courses/{cid}/questions", {"text": f"测试题 {n}",
                                  "options": ["正确", "错误"], "answer": 0, "knowledge_point": "基础概念",
                                  "explanation": "第一项符合题意。"}, teacher)
        self.request(f"/api/courses/{cid}/publish", {}, teacher)
        return course

    def submit(self, course, student, values=(0, 1)):
        return self.request(f"/api/courses/{course['id']}/submissions",
                            {"answers": {q["id"]: value for q, value in zip(course["questions"], values)}}, student)

    def test_teachers_register_without_major_students_require_valid_major(self):
        teacher = self.request("/api/auth/register", {"username": "cross_teacher", "password": "Teach123!",
                                                     "name": "跨专业老师", "role": "teacher"})
        self.assertIsNone(teacher["major_id"])
        self.assertNotIn("password", teacher)
        for major in [None, "m-missing", [], 1]:
            self.error(400, "/api/auth/register", {"username": "invalid_student", "password": "Learn123!",
                                                 "name": "学生", "role": "student", "major_id": major})
        for teacher_id in ["teacher-demo-1", "teacher-demo-2"]:
            self.assertIsNone(self.request("/api/auth/me", account=teacher_id)["major_id"])

    def test_python_is_shared_and_deep_learning_only_ai_without_answer_leaks(self):
        for student in ["student-demo-1", "student-demo-2", "student-demo-3"]:
            visible = self.request("/api/courses", account=student)
            self.assertIn("course-demo", [course["id"] for course in visible])
            self.assertEqual("course-deep-learning" in [course["id"] for course in visible], student == "student-demo-2")
            for course in visible:
                self.assertEqual(len(course["chapters"]), 3)
                self.assertEqual(len(course["questions"]), 12)
                self.assertTrue(all(resource.get("content") for resource in course["resources"]))
                self.assertTrue(all("answer" not in question and "explanation" not in question for question in course["questions"]))
        self.error(404, "/api/courses/course-deep-learning", account="student-demo-1")
        self.error(404, "/api/courses/course-deep-learning/submissions", {"answers": {}}, "student-demo-3")

    def test_cross_major_teaching_and_audience_edits_preserve_history(self):
        course = self.make_course(["m-soft", "m-ai"], "teacher-demo-2")
        cid = course["id"]
        result = self.submit(course, "student-demo-1")
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["details"][0]["explanation"], "第一项符合题意。")
        self.submit(course, "student-demo-2", (0, 0))
        self.error(404, f"/api/courses/{cid}", account="student-demo-3")
        self.error(404, f"/api/courses/{cid}/audience", {"major_ids": ["m-cs"]})
        self.error(403, f"/api/courses/{cid}/audience", {"major_ids": ["m-cs"]}, "student-demo-2")
        self.request(f"/api/courses/{cid}/audience", {"major_ids": ["m-cs"]}, "teacher-demo-2")
        self.request(f"/api/courses/{cid}", account="student-demo-3")
        self.error(404, f"/api/courses/{cid}/submissions", {"answers": {}}, "student-demo-1")
        records = self.request("/api/learning-records", account="student-demo-1")
        self.assertIn(result["id"], [record["id"] for record in records])
        self.assertEqual(self.request("/api/student/profile", account="student-demo-1")["profile"]["average_score"], 50)
        self.assertEqual(self.request("/api/wrong-questions", account="student-demo-1")[0]["question_snapshot"]["explanation"], "第一项符合题意。")

    def test_bad_audience_does_not_create_or_change_courses(self):
        count = len(self.app.state.learning.data["courses"])
        for audience in [[], None, "m-ai", ["m-unknown"], [True], [{"id": "m-ai"}]]:
            self.error(400, "/api/courses", {"title": "无效课程", "major_ids": audience})
        self.assertEqual(len(self.app.state.learning.data["courses"]), count)
        self.error(400, "/api/courses/course-demo/audience", {})
        self.assertEqual(self.request("/api/courses/course-demo")["major_ids"], ["m-soft", "m-ai", "m-cs"])

    def test_teacher_roster_follows_course_audience_not_teacher_major(self):
        course = self.make_course(["m-soft", "m-ai", "m-cs"])
        for student, answers in [("student-demo-1", (1,1)), ("student-demo-2", (0,1)), ("student-demo-3", (0,0))]:
            self.submit(course, student, answers)
        result = self.request("/api/teacher/analytics?major_id=m-soft&teacher_id=teacher-demo-2")
        profiles = {student["student_id"]: student for student in result["students"]}
        self.assertEqual({student["major_id"] for student in profiles.values()}, {"m-soft", "m-ai", "m-cs"})
        self.assertEqual(result["overview"]["participant_count"], 3)
        self.assertEqual(result["overview"]["average_score"], 50)
        self.assertEqual(profiles["student-demo-1"]["average_score"], 0)
        self.assertTrue(profiles["student-demo-1"]["needs_attention"])
        self.assertIsNone(profiles["student-demo-7"]["average_score"])
        self.assertTrue(profiles["student-demo-7"]["not_started"])
        self.assertTrue(all(0 <= student["course_participation"] <= 100 for student in profiles.values()))
        own = self.request("/api/student/profile?student_id=student-demo-3", account="student-demo-2")
        self.assertEqual(own["profile"]["student_id"], "student-demo-2")
        for field in ["average_score", "mastery_score", "submission_count"]:
            self.assertEqual(own["profile"][field], profiles["student-demo-2"][field])
        self.assertNotIn("student-demo-3", json.dumps(own))
        self.assertNotIn("password", json.dumps(result))

    def test_student_profile_is_private_and_empty_data_stays_empty(self):
        self.error(401, "/api/student/profile", account="")
        self.error(403, "/api/student/profile", account="teacher-demo-1")
        profile = self.request("/api/student/profile?role=teacher", account="student-demo-1")["profile"]
        self.assertEqual(profile["student_id"], "student-demo-1")
        self.assertIsNone(profile["average_score"])
        self.assertTrue(profile["not_started"])
        self.assertEqual(profile["practice_activity"], 0)
        self.assertEqual(profile["available_course_count"], 1)

    def test_review_updates_schedule_without_changing_profile_or_grades(self):
        course = self.make_course(["m-soft"])
        self.submit(course, "student-demo-1")
        before = self.request("/api/student/profile", account="student-demo-1")
        grades = self.request("/api/learning-records", account="student-demo-1")
        item = self.request("/api/wrong-questions", account="student-demo-1")[0]
        self.assertEqual(item["course_name"], course["title"])
        self.error(404, f"/api/wrong-questions/{item['id']}/review", {"selected": 0}, "student-demo-2")
        result = self.request(f"/api/wrong-questions/{item['id']}/review", {"selected": 0}, "student-demo-1")
        self.assertTrue(result["correct"])
        self.assertEqual(result["review_interval_days"], 2)
        self.assertEqual(self.request("/api/student/profile", account="student-demo-1"), before)
        self.assertEqual(self.request("/api/learning-records", account="student-demo-1"), grades)
        self.request(f"/api/wrong-questions/{item['id']}/mastered", {}, "student-demo-1")
        self.assertEqual(self.request("/api/review/due", account="student-demo-1"), [])


class DemoMigrationTests(unittest.TestCase):
    def test_legacy_audience_and_history_survive_repeated_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "accounts.json").write_text(json.dumps({"users": [{"id": "legacy-teacher", "username": "legacy_t",
                "name": "旧教师", "password": "Keep123!", "role": "teacher", "major_id": "m-ai", "demo": False}]}))
            legacy = {"courses": [{"id": "legacy-course", "title": "旧课程", "teacher_id": "legacy-teacher", "major_id": "m-ai",
                "status": "published", "resources": [], "questions": []}], "submissions": [{"id": "legacy-record",
                "course_id": "legacy-course", "student_id": "student-demo-2", "score": 88, "submitted_at": "2026-10-01T00:00:00+00:00"}]}
            path = root / "learning-platform.json"
            path.write_text(json.dumps(legacy))
            accounts = AccountStore(root / "accounts.json")
            self.assertIsNone(next(user for user in accounts.users if user["id"] == "legacy-teacher")["major_id"])
            self.assertEqual(next(user for user in accounts.users if user["id"] == "legacy-teacher")["password"], "Keep123!")
            store = LearningStore(path)
            self.assertEqual(store.course("legacy-course")["major_ids"], ["m-ai"])
            self.assertNotIn("major_id", store.course("legacy-course"))
            before = deepcopy(store.data)
            self.assertEqual(LearningStore(path).data, before)
            self.assertEqual(store.data["submissions"], legacy["submissions"])
            self.assertEqual(json.loads((root / "learning-platform.before-v0.5.json").read_text()), legacy)
            self.assertEqual(json.loads((root / "accounts.before-v0.5.json").read_text())["users"][0]["major_id"], "m-ai")

    def test_corrupt_learning_file_is_not_replaced_by_demo_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            content = '{"courses": [ broken data'
            path.write_text(content)
            with self.assertRaises(ValueError):
                LearningStore(path)
            self.assertEqual(path.read_text(), content)

    def test_seed_activity_is_graded_labelled_and_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            accounts = AccountStore(root / "accounts.json")
            store = LearningStore(root / "learning-platform.json")
            self.assertTrue(seed_activity(store.data, accounts.users))
            records = store.data["submissions"]
            self.assertGreaterEqual(len(records), 24)
            for record in records:
                self.assertTrue(record["demo"])
                self.assertEqual(record["score"], round(sum(detail["correct"] for detail in record["details"]) / record["total"]*100))
                student = next(user for user in accounts.users if user["id"] == record["student_id"])
                self.assertIn(student["major_id"], store.course(record["course_id"])["major_ids"])
            keys = [(item["student_id"],item["course_id"],item["question_id"]) for item in store.data["wrong_questions"]]
            self.assertEqual(len(keys), len(set(keys)))
            for item in store.data["wrong_questions"]:
                wrong = sum(not detail["correct"] for record in records
                            if record["student_id"] == item["student_id"] and record["course_id"] == item["course_id"]
                            for detail in record["details"] if detail["question_id"] == item["question_id"])
                self.assertEqual(item["wrong_count"], wrong)
                origin = item.get("last_reviewed_at", item["first_wrong_at"])
                self.assertEqual(datetime.fromisoformat(item["next_review_at"]) - datetime.fromisoformat(origin),
                                 timedelta(days=item["review_interval_days"]))
            self.assertFalse(any(record["student_id"] == "student-demo-7" for record in records))
            before = deepcopy(store.data)
            self.assertFalse(seed_activity(store.data, accounts.users))
            self.assertEqual(store.data, before)

    def test_catalog_upgrade_keeps_edited_questions_and_real_submissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            accounts = AccountStore(root / "accounts.json")
            store = LearningStore(root / "learning-platform.json")
            course = store.course("course-demo")
            course["questions"][0]["text"] = "教师自定义的题目"
            course["questions"][1].pop("explanation")
            course["major_ids"] = ["m-soft"]
            store.data.pop("demo_catalog_version")
            real = {"id": "real-record", "student_id": "student-demo-1", "course_id": "course-demo", "score": 100,
                    "submitted_at": datetime.now(timezone.utc).isoformat(), "details": [], "total": 0}
            store.data["submissions"].append(real)
            store.save()
            migrated = LearningStore(store.path)
            self.assertEqual(migrated.course("course-demo")["questions"][0]["text"], "教师自定义的题目")
            self.assertTrue(migrated.course("course-demo")["questions"][1]["explanation"])
            self.assertEqual(migrated.course("course-demo")["major_ids"], ["m-soft", "m-ai", "m-cs"])
            seed_activity(migrated.data, accounts.users)
            self.assertEqual([record for record in migrated.data["submissions"] if record["student_id"] == "student-demo-1"], [real])
