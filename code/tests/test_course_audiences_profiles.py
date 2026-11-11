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

    def request(self, path, payload=None, account="teacher-demo-1", method=None):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method,
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
            self.assertEqual(sum(c.get("course_code") == "PY101" for c in visible), 1)
            self.assertTrue(all(c["major_ids"] == [{"student-demo-1":"m-soft", "student-demo-2":"m-ai", "student-demo-3":"m-cs"}[student]] for c in visible))
            self.assertEqual("course-deep-learning" in [course["id"] for course in visible], student == "student-demo-2")
            for course in visible:
                self.assertEqual(len(course["chapters"]), 3)
                self.assertEqual(len(course["questions"]), 12)
                self.assertTrue(all(resource.get("content") for resource in course["resources"]))
                self.assertTrue(all("answer" not in question and "explanation" not in question for question in course["questions"]))
        self.error(404, "/api/courses/course-deep-learning", account="student-demo-1")
        self.error(404, "/api/courses/course-deep-learning/submissions", {"answers": {}}, "student-demo-3")

    def test_cross_major_teaching_and_audience_edits_preserve_history(self):
        course = self.make_course(["m-soft"], "teacher-demo-2")
        ai_class = self.make_course(["m-ai"], "teacher-demo-2")
        cid = course["id"]
        result = self.submit(course, "student-demo-1")
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["details"][0]["explanation"], "第一项符合题意。")
        self.submit(ai_class, "student-demo-2", (0, 0))
        self.error(404, f"/api/courses/{cid}", account="student-demo-2")
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
        for audience in [[], None, "m-ai", ["m-unknown"], [True], [{"id": "m-ai"}], ["m-soft","m-ai"]]:
            self.error(400, "/api/courses", {"title": "无效课程", "major_ids": audience})
        self.assertEqual(len(self.app.state.learning.data["courses"]), count)
        self.error(400, "/api/courses/course-demo/audience", {})
        self.assertEqual(self.request("/api/courses/course-demo")["major_ids"], ["m-soft"])

    def test_teacher_roster_follows_course_audience_not_teacher_major(self):
        for student, major, answers in [("student-demo-1","m-soft",(1,1)), ("student-demo-2","m-ai",(0,1)), ("student-demo-3","m-cs",(0,0))]:
            course = self.make_course([major])
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
        self.assertEqual(profile["available_course_count"], 2)

    def test_each_teacher_has_two_subjects_and_three_independent_teaching_classes(self):
        for teacher in ("teacher-demo-1", "teacher-demo-2"):
            courses = self.request("/api/courses", account=teacher)
            self.assertEqual(len(courses), 3)
            codes = {course["course_code"] for course in courses}
            self.assertEqual(len(codes), 2)
            grouped = [[course for course in courses if course["course_code"] == code] for code in codes]
            self.assertEqual(sorted(map(len, grouped)), [1, 2])
            pair = next(group for group in grouped if len(group) == 2)
            self.assertEqual(pair[0]["title"], pair[1]["title"])
            self.assertEqual(len({c["teaching_class"] for c in pair}), 2)
            self.assertTrue(all(len(c["major_ids"]) == 1 for c in courses))
            self.assertTrue({q["id"] for q in pair[0]["questions"]}.isdisjoint(q["id"] for q in pair[1]["questions"]))
            self.assertTrue({ch["id"] for ch in pair[0]["chapters"]}.isdisjoint(ch["id"] for ch in pair[1]["chapters"]))
            self.assertEqual(sum(c["status"] == "draft" for c in courses), 0 if teacher == "teacher-demo-1" else 1)
        soft = self.request("/api/courses", account="student-demo-1")
        ai = self.request("/api/courses", account="student-demo-2")
        self.assertEqual({c["id"] for c in soft}, {"course-demo", "course-web-foundations"})
        self.assertEqual({c["id"] for c in ai}, {"course-python-02", "course-deep-learning"})
        self.error(404, "/api/courses/course-python-cs", account="student-demo-1")

    def test_same_subject_classes_keep_scores_and_course_metadata_separate(self):
        first = self.request("/api/courses/course-demo")
        second = self.request("/api/courses/course-python-02")
        self.request("/api/courses/course-python-02/publish", {})
        for course, correct in ((first, True), (second, False)):
            answers = {q["id"]: q["answer"] if correct else (q["answer"]+1) % len(q["options"]) for q in course["questions"]}
            self.request(f"/api/courses/{course['id']}/submissions", {"answers":answers}, "student-demo-1" if correct else "student-demo-2")
        records = self.request("/api/learning-records")
        self.assertEqual({r["course_id"]:r["score"] for r in records}, {first["id"]:100, second["id"]:0})
        self.assertEqual({r["teaching_class"] for r in records}, {"软件工程 01班", "人工智能 01班"})
        analytics = self.request("/api/teacher/analytics")
        averages = {c["course_id"]:c["average_score"] for c in analytics["courses"]}
        self.assertEqual(averages[first["id"]], 100)
        self.assertEqual(averages[second["id"]], 0)
        changed = self.request("/api/courses/course-python-02", {"course_code":"PY101", "teaching_class":"2026级 软件02班", "term":"2026年秋季"}, method="PATCH")
        self.assertEqual(changed["teaching_class"], "2026级 软件02班")
        self.assertEqual(self.request("/api/courses/course-demo")["teaching_class"], "软件工程 01班")

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

    def test_demo_grades_link_to_published_assignments_and_matching_professional_class(self):
        store = self.app.state.learning
        seed_activity(store.data, self.app.state.accounts.users)
        store.save()
        for teacher in ("teacher-demo-1", "teacher-demo-2"):
            courses = self.request("/api/courses", account=teacher)
            for course in courses:
                assignments = self.request(f"/api/courses/{course['id']}/assignments", account=teacher)
                self.assertEqual(len(assignments), 3 if course["status"] == "published" else 0)
                for item in assignments:
                    self.assertEqual(item["status"], "published")
                    self.assertNotIn("（演示）", item["title"])
                    overview = self.request(f"/api/courses/{course['id']}/assignments/{item['id']}/submissions", account=teacher)
                    self.assertTrue(all(s["major_id"] == course["major_ids"][0] for s in overview["students"]))
        for student in ("student-demo-1", "student-demo-2", "student-demo-3"):
            records = self.request("/api/learning-records", account=student)
            assignments = {a["id"]:a for a in self.request("/api/assignments", account=student)}
            self.assertTrue(records)
            for record in records:
                self.assertEqual(record["source_type"], "assignment")
                self.assertTrue(record["assignment_title"])
                self.assertEqual(assignments[record["assignment_id"]]["course_id"], record["course_id"])
                self.assertEqual(assignments[record["assignment_id"]]["latest_submission_id"], record["id"])
        self.error(400, "/api/courses/course-demo/audience", {"major_ids":["m-soft","m-ai"]})


class DemoMigrationTests(unittest.TestCase):
    def test_assignment_title_upgrade_keeps_learning_evidence_and_custom_titles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            store = LearningStore(path)
            accounts = AccountStore(Path(folder) / "accounts.json")
            seed_activity(store.data, accounts.users)
            for assignment in store.data["assignments"]:
                assignment["title"] += "（演示）"
            custom = {**deepcopy(store.data["assignments"][0]), "id": "custom-assignment", "demo": False,
                      "title": "教师自定义（演示）"}
            store.data["assignments"].append(custom)
            renamed = store.data["assignments"][1]
            renamed["title"] = "教师已改过的作业名"
            store.data.pop("demo_assignment_titles_version", None)
            store.save()
            before = deepcopy(store.data)
            upgraded = LearningStore(path)
            self.assertEqual(upgraded.data["submissions"], before["submissions"])
            self.assertEqual(upgraded.data["assignment_attempts"], before["assignment_attempts"])
            self.assertEqual(upgraded.data["wrong_questions"], before["wrong_questions"])
            self.assertEqual(upgraded.data["assignments"][-1], custom)
            self.assertEqual(upgraded.data["assignments"][1]["title"], "教师已改过的作业名")
            self.assertTrue(all("（演示）" not in assignment["title"] for assignment in upgraded.data["assignments"][:-1]))
            self.assertEqual([a["id"] for a in upgraded.data["assignments"]], [a["id"] for a in before["assignments"]])
            self.assertEqual(LearningStore(path).data, upgraded.data)

    def test_old_orphan_demo_grades_move_to_major_classes_and_keep_real_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            accounts = AccountStore(root / "accounts.json")
            store = LearningStore(root / "learning-platform.json")
            source = store.course("course-demo")
            q = source["questions"][0]
            stamp = (datetime.now(timezone.utc)-timedelta(days=20)).isoformat()
            for n in (1,2,3):
                store.data["submissions"].append({"id":f"demo-sub-{n}-course-demo-0", "demo":True,
                    "student_id":f"student-demo-{n}", "student":f"演示学生{n}", "course_id":"course-demo",
                    "chapter_id":q["chapter_id"], "submitted_at":stamp, "score":100, "correct_count":1, "total":1,
                    "details":[{"question_id":q["id"], "chapter_id":q["chapter_id"], "text":q["text"],
                    "options":q["options"], "answer":q["answer"], "selected":q["answer"], "correct":True}]})
            real = {"id":"real-preserved", "student_id":"student-demo-3", "course_id":"course-demo",
                    "score":33, "submitted_at":stamp, "details":[], "total":0}
            store.data["submissions"].append(deepcopy(real))
            store.data["wrong_questions"].append({"id":"demo-wrong-2-q1", "demo":True,
                "student_id":"student-demo-2", "course_id":"course-demo", "question_id":q["id"],
                "chapter_id":q["chapter_id"], "status":"mastered", "review_count":3, "review_interval_days":8,
                "next_review_at":stamp, "question_snapshot":{"text":q["text"], "options":q["options"], "answer":q["answer"]}})
            store.data["demo_activity_version"] = 1
            seed_activity(store.data, accounts.users)
            assignments = {a["id"]:a for a in store.data["assignments"]}
            attempts = {a["id"]:a for a in store.data["assignment_attempts"]}
            expected = {1:"course-demo",2:"course-python-02",3:"course-python-cs"}
            for n,cid in expected.items():
                record = next(r for r in store.data["submissions"] if r["id"] == f"demo-sub-{n}-course-demo-0")
                self.assertEqual(record["course_id"], cid)
                self.assertEqual(record["score"], 100)
                assignment = assignments[record["assignment_id"]]
                self.assertEqual(assignment["status"], "published")
                self.assertEqual(assignment["course_id"], cid)
                self.assertLessEqual(datetime.fromisoformat(assignment["open_at"]), datetime.fromisoformat(stamp))
                self.assertLessEqual(datetime.fromisoformat(stamp), datetime.fromisoformat(assignment["due_at"]))
                self.assertEqual(attempts[record["attempt_id"]]["submission_id"], record["id"])
            self.assertEqual(next(r for r in store.data["submissions"] if r["id"] == real["id"]), real)
            wrong = next(r for r in store.data["wrong_questions"] if r["id"] == "demo-wrong-2-q1")
            self.assertEqual((wrong["course_id"], wrong["status"], wrong["review_count"], wrong["review_interval_days"]),
                             ("course-python-02", "mastered", 3, 8))
            before = deepcopy(store.data)
            self.assertFalse(seed_activity(store.data, accounts.users))
            self.assertEqual(store.data, before)

    def test_professional_upgrade_keeps_used_machine_learning_history_archived(self):
        from demo.teaching_catalog import _previous_samples
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            real = {"id":"real-ml-grade", "student_id":"student-demo-2", "course_id":"course-machine-learning",
                    "score":81, "submitted_at":datetime.now(timezone.utc).isoformat(), "details":[]}
            legacy = {"courses":_previous_samples(), "submissions":[real], "wrong_questions":[],
                      "demo_teaching_classes_version":1, "demo_catalog_version":2, "course_audiences_version":1}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = LearningStore(path)
            self.assertEqual(migrated.course("course-machine-learning")["status"], "archived")
            self.assertEqual(migrated.data["submissions"], [real])
            active = [c for c in migrated.data["courses"] if c["status"] != "archived"]
            self.assertEqual(len(active), 6)
            self.assertEqual(sum(c["teacher_id"] == "teacher-demo-2" for c in active), 3)
            self.assertEqual(json.loads(path.with_name("learning-platform.before-professional-classes.json").read_text(encoding="utf-8")), legacy)

    def test_teaching_class_upgrade_is_additive_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            original = LearningStore(path)
            keep = [deepcopy(c) for c in original.data["courses"] if c["id"] in {"course-demo", "course-deep-learning"}]
            keep[0]["title"] = "教师已改过的 Python 名称"
            keep[0]["teaching_class"] = "保留原班名"
            keep[0]["questions"][0]["text"] = "教师自己的题目"
            custom = {"id":"custom-course", "teacher_id":"teacher-demo-1", "title":"自建课程",
                      "status":"draft", "major_ids":["m-soft"], "chapters":[], "questions":[], "resources":[]}
            original.data["courses"] = keep + [custom]
            original.data["submissions"] = [{"id":"saved-grade", "student_id":"student-demo-1", "course_id":"course-demo", "score":0}]
            original.data.pop("demo_teaching_classes_version")
            original.save()
            before = deepcopy(original.data)
            migrated = LearningStore(path)
            self.assertEqual(len(migrated.data["courses"]), 7)
            self.assertEqual(migrated.course("course-demo")["title"], keep[0]["title"])
            self.assertEqual(migrated.course("course-demo")["teaching_class"], "保留原班名")
            self.assertEqual(migrated.course("course-demo")["questions"][0]["text"], "教师自己的题目")
            self.assertEqual(migrated.data["submissions"], before["submissions"])
            self.assertEqual(json.loads(path.with_name("learning-platform.before-teaching-classes.json").read_text(encoding="utf-8")), before)
            self.assertEqual(LearningStore(path).data, migrated.data)
            migrated.data["courses"] = [c for c in migrated.data["courses"] if c["id"] != "course-python-02"]
            migrated.save()
            self.assertIsNone(LearningStore(path).course("course-python-02"))

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
            self.assertEqual(migrated.course("course-demo")["major_ids"], ["m-soft"])
            seed_activity(migrated.data, accounts.users)
            self.assertEqual([record for record in migrated.data["submissions"] if record["student_id"] == "student-demo-1"], [real])
