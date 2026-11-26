"""Real HTTP checks for same-major sections, migration and every classroom access gate."""
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
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

import uvicorn

from demo.app import create_app
from demo.enrollments import is_enrolled, upgrade_enrollments

T1, T2, S1, S2 = "teacher-demo-1", "teacher-demo-2", "student-demo-1", "student-demo-4"


class EnrollmentHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="class-enrollments-")
        cls.root = Path(cls.temp.name)
        cls.app = create_app(cls.root / "jobs", seed_demo_history=False)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", port=port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True); cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started:
            if not cls.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("server start failed")
            time.sleep(.02)
        with cls.client.open(cls.url + "/api/config") as response:
            cls.token = json.load(response)["token"]
        for username, password in [("teacher1", "Teach123!"), ("teacher2", "Teach456!"),
                                   ("student1", "Learn123!"), ("student4", "Learn123!"),
                                   ("student2", "Learn456!"), ("student7", "Learn123!")]:
            cls.request("/api/auth/login", {"username": username, "password": password})
        cls.initial = deepcopy(cls.app.state.learning.data)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True; cls.thread.join(10); cls.temp.cleanup()

    def setUp(self):
        self.store = self.app.state.learning
        with self.store.lock:
            self.store.data = deepcopy(self.initial); self.store.save()

    @classmethod
    def request(cls, path, data=None, account=T1, method=None):
        request = Request(cls.url + path, data=json.dumps(data).encode() if data is not None else None,
                          method=method, headers={"X-Local-Token": cls.token, "X-Account-ID": account,
                                                 "Content-Type": "application/json"})
        with cls.client.open(request, timeout=8) as response:
            return json.load(response)

    def error(self, status, *args, **kwargs):
        with self.assertRaises(HTTPError) as caught:
            self.request(*args, **kwargs)
        self.assertEqual(caught.exception.code, status, caught.exception.read().decode())
        caught.exception.close()

    def members(self, cid, ids, teacher=T1):
        return self.request(f"/api/courses/{cid}/enrollments", {"student_ids": ids}, teacher, "PUT")

    def classroom(self, name="01班", teacher=T1, members=None, code="ENROLL101"):
        course = self.request("/api/courses", {"title": "同专业分班验收", "course_code": code,
                              "term": "2026年秋季", "teaching_class": name, "major_ids": ["m-soft"]}, teacher)
        cid = course["id"]
        self.request(f"/api/courses/{cid}/resources", {"name": name + "独立讲义"}, teacher)
        course = self.request(f"/api/courses/{cid}/questions", {"text": name + "独立题目", "options": ["对", "错"],
                              "answer": 0, "explanation": name + "解析"}, teacher)
        if members is not None:
            self.members(cid, members, teacher)
        return self.request(f"/api/courses/{cid}/publish", {}, teacher)

    def paper(self, course, teacher=T1):
        return self.request(f"/api/courses/{course['id']}/assignments", {"title": "本班作业", "status": "published",
                            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "max_attempts": 2,
                            "questions": [{"text": "独立作业题", "options": ["A", "B"], "answer": 0,
                                           "explanation": "解答", "chapter_id": course["chapters"][0]["id"]}]}, teacher)

    def test_same_code_same_major_two_teachers_have_distinct_student_content(self):
        a = self.classroom(members=[S1]); b = self.classroom("02班", T2, [S2])
        for student, own, other in [(S1, a, b), (S2, b, a)]:
            visible = {c["id"] for c in self.request("/api/courses", account=student)}
            self.assertIn(own["id"], visible); self.assertNotIn(other["id"], visible)
            self.assertEqual(self.request(f"/api/courses/{own['id']}", account=student)["id"], own["id"])
            self.error(404, f"/api/courses/{other['id']}", account=student)
            self.error(404, f"/api/courses/{other['id']}/submissions", {"answers": {}}, student)

    def test_new_class_is_empty_and_major_alone_cannot_enter(self):
        course = self.classroom()
        self.assertEqual(course["student_count"], 0)
        self.error(404, f"/api/courses/{course['id']}", account=S1)
        self.members(course["id"], [S1])
        self.assertEqual(self.request(f"/api/courses/{course['id']}", account=S1)["id"], course["id"])

    def test_roster_owner_only_and_rejects_unknown_cross_major_and_repeated_ids(self):
        course = self.classroom(members=[S1]); path = f"/api/courses/{course['id']}/enrollments"
        self.error(404, path, account=T2); self.error(403, path, account=S1)
        self.error(404, path, {"student_ids": [S2]}, T2, "PUT")
        self.error(403, path, {"student_ids": [S2]}, S1, "PUT")
        for ids in [[S1, S1], ["not-a-user"], ["student-demo-2"], [T1], "all", None]:
            self.error(400, path, {"student_ids": ids}, T1, "PUT")
        self.assertEqual(self.request(path)["student_ids"], [S1])

    def test_transfer_requires_removal_and_cannot_silently_remove_other_teacher_members(self):
        a = self.classroom(members=[S1]); b = self.classroom("02班", T2, [S2])
        self.error(409, f"/api/courses/{b['id']}/enrollments", {"student_ids": [S1, S2]}, T2, "PUT")
        self.assertEqual(self.request(f"/api/courses/{a['id']}/enrollments")["student_ids"], [S1])
        self.members(a["id"], [])
        self.members(b["id"], [S1, S2], T2)
        self.error(404, f"/api/courses/{a['id']}", account=S1)
        self.request(f"/api/courses/{b['id']}", account=S1)

    def test_membership_pair_is_reused_after_remove_rejoin_and_survives_reload(self):
        course = self.classroom(members=[S1]); cid = course["id"]
        old = self.request(f"/api/courses/{cid}/enrollments")["students"][0]["enrollment_id"]
        self.members(cid, []); self.members(cid, [S1]); self.members(cid, [S1])
        rows = [r for r in self.store.data["enrollments"] if r["course_id"] == cid and r["student_id"] == S1]
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["id"], old)
        disk = json.loads(self.store.path.read_text())
        self.assertTrue(is_enrolled(course, {"id": S1, "role": "student", "major_id": "m-soft"}, disk["enrollments"]))

    def test_gradebook_analytics_and_assignment_missing_counts_use_members_not_whole_major(self):
        a = self.classroom(members=[S1]); b = self.classroom("02班", T2, [S2])
        paper = self.paper(a)
        self.assertEqual(paper["student_count"], 1)
        self.assertEqual([s["id"] for s in paper["missing_students"]], [S1])
        book = self.request(f"/api/teacher/gradebook?course_id={a['id']}")
        self.assertEqual({row["student_id"] for row in book["students"]}, {S1})
        detail = self.request(f"/api/teacher/students/{S1}?course_id={a['id']}")
        self.assertEqual(detail["student"]["id"], S1)
        self.error(404, f"/api/teacher/students/{S2}?course_id={a['id']}")

    def test_assignments_and_live_quizzes_are_not_discovered_by_other_section(self):
        a = self.classroom(members=[S1]); b = self.classroom("02班", T2, [S2]); paper = self.paper(b, T2)
        self.assertNotIn(paper["id"], {p["id"] for p in self.request("/api/assignments", account=S1)})
        self.error(404, f"/api/assignments/{paper['id']}", account=S1)
        self.error(404, f"/api/assignments/{paper['id']}/start", {}, S1)
        quiz = self.request(f"/api/courses/{b['id']}/live-quizzes", {"question_id": b["questions"][0]["id"], "duration": 30}, T2)
        self.assertIsNone(self.request("/api/live-quizzes/current", account=S1)["quiz"])
        self.assertEqual(self.request("/api/live-quizzes/current", account=S2)["quiz"]["id"], quiz["id"])
        self.error(404, f"/api/live-quizzes/{quiz['id']}", account=S1)
        self.error(404, f"/api/live-quizzes/{quiz['id']}/events", account=S1)

    def test_announcements_only_target_explicit_members(self):
        course = self.classroom(members=[S1])
        announcement = self.request(f"/api/courses/{course['id']}/announcements", {"title": "本班通知", "body": "只发本班", "audience": "all"})
        self.assertEqual(announcement["recipient_count"], 1)
        for sid, expected in [(S1, True), (S2, False)]:
            ids = {a["id"] for a in self.request("/api/announcements", account=sid)["items"]}
            self.assertEqual(announcement["id"] in ids, expected)

    def test_removing_member_revokes_existing_live_event_stream(self):
        course = self.classroom(members=[S1])
        quiz = self.request(f"/api/courses/{course['id']}/live-quizzes", {"question_id": course["questions"][0]["id"]})
        request = Request(self.url + f"/api/live-quizzes/{quiz['id']}/events",
                          headers={"X-Local-Token": self.token, "X-Account-ID": S1})
        with self.client.open(request, timeout=5) as response:
            self.assertEqual(response.readline().decode().strip(), "event: state")
            self.members(course["id"], [])
            messages = []
            for line in response:
                messages.append(line.decode())
                if "access_revoked" in messages[-1]:
                    break
            self.assertIn("access_revoked", "".join(messages))

    def test_removal_revokes_access_but_keeps_student_history_and_scores(self):
        course = self.classroom(members=[S1]); cid = course["id"]
        result = self.request(f"/api/courses/{cid}/submissions", {"answers": {course["questions"][0]["id"]: 0}}, S1)
        self.members(cid, [])
        self.error(404, f"/api/courses/{cid}", account=S1)
        records = self.request("/api/learning-records", account=S1)
        self.assertEqual(next(r["score"] for r in records if r["id"] == result["id"]), 100)
        self.assertEqual(self.request("/api/student/profile", account=S1)["profile"]["average_score"], 100)
        book = self.request(f"/api/teacher/gradebook?course_id={cid}")
        self.assertEqual(book["students"], [])
        new_paper = self.paper(course)
        self.assertEqual(new_paper["student_count"], 0)
        self.assertEqual(new_paper["missing_count"], 0)

    def test_nine_axis_radar_uses_scoped_homework_practice_reading_and_review(self):
        idle = "student-demo-8"
        with self.store.lock:
            self.store.data["enrollments"] = []
        course = self.classroom(members=[S1, S2, idle])
        base = f"/api/courses/{course['id']}"
        worksheet = self.request(f"{base}/resources", {"name": "可跟踪讲义", "content": "一段可阅读的内容"})
        resource = worksheet["resources"][-1]["id"]
        now = datetime.now(timezone.utc)
        due = now - timedelta(hours=1)
        first_time = now - timedelta(hours=2)

        def create_paper(title, open_at, due_at, status="published"):
            return self.request(f"{base}/assignments", {
                "title": title, "status": status, "open_at": open_at.isoformat(),
                "due_at": due_at.isoformat(), "max_attempts": 3, "allow_late": True,
                "late_max_days": 2, "late_penalty_percent_per_day": 20,
                "questions": [{"text": "作业专用题干", "options": ["A", "B"], "answer": 0,
                               "explanation": "未解锁解析", "chapter_id": course["chapters"][0]["id"]}],
            })

        paper = create_paper("允许重交", now - timedelta(days=2), due)
        create_paper("已经缺交", now - timedelta(days=2), due)
        create_paper("尚未到期", now - timedelta(days=1), now + timedelta(days=1))
        create_paper("尚未开放", now + timedelta(days=1), now + timedelta(days=2))
        create_paper("草稿作业", now - timedelta(days=2), due, status="draft")
        payload = {"assignment_id": paper["id"], "answers": {paper["questions"][0]["id"]: 0}}

        class EarlierDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return first_time.astimezone(tz) if tz else first_time.astimezone().replace(tzinfo=None)

        with patch("demo.app.datetime", EarlierDateTime):
            first = self.request(f"{base}/submissions", payload, S1)
        self.assertEqual(first["score"], 100)
        self.assertTrue(first["answers_locked"])
        self.assertEqual(first["details"], [])
        latest = self.request(f"{base}/submissions", payload, S1)
        self.assertEqual(latest["score"], 80)
        self.assertTrue(latest["late"])
        self.assertTrue(latest["answers_locked"])
        self.request(f"{base}/resources/{resource}/reading", {"percent": 60}, S1)

        practice = {"answers": {course["questions"][0]["id"]: 0}}
        self.request(f"{base}/submissions", practice, S2)
        details_path = f"/api/teacher/students/{S1}?course_id={course['id']}"

        def profile(student):
            result = self.request(f"/api/student/profile?student_id={S2}", account=student)
            self.assertEqual(result["profile"]["student_id"], student)
            self.assertNotIn("未解锁解析", json.dumps(result, ensure_ascii=False))
            return result["profile"]["radar_dimensions"]

        homework_only = profile(S1)
        self.assertEqual(len(homework_only), 9)
        self.assertEqual(homework_only["course_participation"]["value"], 100)
        self.assertEqual(homework_only["practice_activity"]["value"], 0)
        self.assertIsNone(homework_only["practice_average"]["value"])
        self.assertIsNone(homework_only["practice_accuracy"]["value"])
        self.assertEqual(homework_only["assignment_completion"],
                         {"value": 50, "numerator": 1, "denominator": 2, "pending_count": 1})
        self.assertEqual(homework_only["assignment_on_time"], {"value": 100, "numerator": 1, "denominator": 1})
        self.assertEqual(homework_only["assignment_average"], {"value": 80, "denominator": 1})
        self.assertEqual(homework_only["reading_completion"], {"value": 100, "numerator": 1, "denominator": 1})
        self.assertIsNone(homework_only["review_accuracy"]["value"])
        practice_only = profile(S2)
        self.assertEqual(practice_only["practice_average"]["value"], 100)
        self.assertEqual(practice_only["assignment_completion"]["value"], 0)
        self.assertIsNone(practice_only["assignment_average"]["value"])

        self.request(f"{base}/submissions", {"answers": {course["questions"][0]["id"]: 1}}, S1)
        wrong = self.request(f"/api/wrong-questions?course_id={course['id']}", account=S1)[0]
        self.request(f"/api/wrong-questions/{wrong['id']}/review", {"selected": 0}, S1)
        combined = profile(S1)
        self.assertEqual(combined["practice_average"], {"value": 0, "denominator": 1})
        self.assertEqual(combined["practice_accuracy"], {"value": 0, "numerator": 0, "denominator": 1})
        self.assertEqual(combined["review_accuracy"], {"value": 100, "numerator": 1, "denominator": 1})
        self.assertEqual(combined["assignment_average"], homework_only["assignment_average"])
        profiles = {row["student_id"]: row["radar_dimensions"]
                    for row in self.request("/api/teacher/analytics")["students"]}
        self.assertEqual(profiles[S1], combined)
        self.assertEqual(profiles[idle]["assignment_completion"]["value"], 0)
        self.assertIsNone(profiles[idle]["practice_average"]["value"])
        self.assertIsNone(profiles[idle]["review_accuracy"]["value"])
        self.assertEqual(self.request(details_path)["profile"]["radar_dimensions"], combined)
        self.error(404, details_path, account=T2)
        self.error(404, f"/api/teacher/students/{S1}", account=T2)
        self.error(403, details_path, account=S2)

        self.request("/api/auth/login", {"username": "student8", "password": "Learn123!"})
        late_first = self.request(f"{base}/submissions", payload, idle)
        self.assertTrue(late_first["late"])
        self.assertEqual(profile(idle)["assignment_on_time"], {"value": 0, "numerator": 0, "denominator": 1})

        self.members(course["id"], [S2, idle])
        historical = profile(S1)
        self.assertEqual(historical["assignment_completion"],
                         {"value": 100, "numerator": 1, "denominator": 1, "pending_count": 0})
        self.assertEqual(historical["assignment_average"], combined["assignment_average"])
        self.assertIsNone(historical["reading_completion"]["value"])
        self.assertIsNone(self.request("/api/student/portrait", account=S1)["reading"]["percent"])
        self.assertEqual(historical["practice_average"], combined["practice_average"])
        self.assertNotIn(S1, {row["student_id"] for row in self.request("/api/teacher/analytics")["students"]})

    def test_roster_save_failure_rolls_back_memory_and_disk(self):
        course = self.classroom(members=[S1]); before = deepcopy(self.store.data["enrollments"])
        disk = self.store.path.read_bytes()
        with patch.object(self.store, "save", side_effect=OSError("simulated disk failure")):
            self.error(503, f"/api/courses/{course['id']}/enrollments", {"student_ids": [S2]}, T1, "PUT")
        self.assertEqual(self.store.data["enrollments"], before); self.assertEqual(self.store.path.read_bytes(), disk)

    def test_major_change_removes_incompatible_roster_and_requires_explicit_new_members(self):
        course = self.classroom(members=[S1]); cid = course["id"]
        self.request(f"/api/courses/{cid}/audience", {"major_ids": ["m-ai"]})
        self.assertEqual(self.request(f"/api/courses/{cid}/enrollments")["student_count"], 0)
        self.error(404, f"/api/courses/{cid}", account="student-demo-2")
        self.members(cid, ["student-demo-2"])
        self.request(f"/api/courses/{cid}", account="student-demo-2")

    def test_builtin_same_major_sections_and_migration_backup(self):
        self.assertTrue((self.root / "learning-platform.before-student-enrollments.json").is_file())
        first = {c["id"] for c in self.request("/api/courses", account="student-demo-2")}
        second = {c["id"] for c in self.request("/api/courses", account="student-demo-7")}
        self.assertIn("course-deep-learning", first); self.assertNotIn("course-deep-learning-02", first)
        self.assertIn("course-deep-learning-02", second); self.assertNotIn("course-deep-learning", second)


class EnrollmentMigrationTests(unittest.TestCase):
    def test_legacy_duplicate_classes_preserve_history_and_upgrade_only_once(self):
        students = [{"id": "s1", "role": "student", "major_id": "m-soft"}, {"id": "s2", "role": "student", "major_id": "m-soft"}]
        classes = [{"id": cid, "major_ids": ["m-soft"], "course_code": "PY101", "term": "秋季", "status": "published"} for cid in ["c1", "c2"]]
        data = {"courses": classes, "submissions": [{"id": "record", "student_id": "s1", "course_id": "c2", "score": 83}], "wrong_questions": [{"id": "wrong", "status": "mastered"}]}
        history, wrong = deepcopy(data["submissions"]), deepcopy(data["wrong_questions"])
        self.assertTrue(upgrade_enrollments(data, students))
        self.assertTrue(is_enrolled(classes[1], students[0], data["enrollments"]))
        self.assertFalse(is_enrolled(classes[0], students[0], data["enrollments"]))
        self.assertEqual(data["submissions"], history); self.assertEqual(data["wrong_questions"], wrong)
        rows = deepcopy(data["enrollments"])
        self.assertFalse(upgrade_enrollments(data, students + [{"id": "new", "role": "student", "major_id": "m-soft"}]))
        self.assertEqual(data["enrollments"], rows)
