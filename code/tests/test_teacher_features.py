"""Teacher content, assignment, gradebook and announcement integration tests."""
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
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn

from demo.app import create_app

TEACHER = "teacher-demo-1"
OTHER_TEACHER = "teacher-demo-2"
STUDENT = "student-demo-1"
OTHER_STUDENT = "student-demo-2"
SOFT_STUDENT = "student-demo-4"


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


class TeacherFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="teacher-features-")
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
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(self.data, seed_demo_history=False), host="127.0.0.1", port=port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.fail("Test server did not start")
            time.sleep(.02)
        with self.client.open(self.url + "/api/config", timeout=5) as response:
            self.token = json.load(response)["token"]
        for username, password in (("teacher1", "Teach123!"), ("teacher2", "Teach456!"),
                                   ("student1", "Learn123!"), ("student2", "Learn456!"),
                                   ("student4", "Learn123!")):
            self.login(username, password)

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(10)

    def request(self, path, payload=None, account=TEACHER, method=None):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method,
                          headers={"X-Local-Token": self.token, "X-Account-ID": account,
                                   "Content-Type": "application/json"})
        with self.client.open(request, timeout=10) as response:
            return json.load(response)

    def raw(self, path, account=TEACHER):
        request = Request(self.url + path, headers={"X-Local-Token": self.token, "X-Account-ID": account})
        with self.client.open(request, timeout=10) as response:
            return response.read(), dict(response.headers)

    def error(self, path, payload=None, account=TEACHER, method=None, status=400):
        with self.assertRaises(HTTPError) as raised:
            self.request(path, payload, account=account, method=method)
        self.assertEqual(raised.exception.code, status, raised.exception.read().decode("utf-8", "replace"))
        return raised.exception

    def login(self, username, password):
        return self.request("/api/auth/login", {"username": username, "password": password})["id"]

    def course(self, title="测试课程", major_ids=None, publish=True):
        course = self.request("/api/courses", {"title": title, "major_ids": major_ids or ["m-soft"]})
        cid = course["id"]
        self.request(f"/api/courses/{cid}/resources", {"name": "课程讲义", "kind": "图文讲义"}, account=TEACHER)
        question = self.request(f"/api/courses/{cid}/questions", {
            "text": "1 + 1 等于几？", "options": ["2", "3", "4", "5"], "answer": 0,
            "explanation": "基础加法"}, account=TEACHER)["questions"][0]
        if publish:
            self.request(f"/api/courses/{cid}/publish", {}, account=TEACHER)
        return cid, question

    # ------------------------------------------------------------------ content
    def ai_quiz(self, owner=TEACHER):
        """Explicit offline fixture; no AI request or user data is involved."""
        jobs = self.server.config.app.state.jobs
        job = jobs.create("TEST_ONLY_teacher_material.mp4", "balanced", owner_id=owner)
        jobs.update(job["id"], status="completed")
        jobs.active = None
        quiz = {"id": uuid.uuid4().hex, "source_mode": "slides", "source_summary": "a" * 64,
                "title": "TEST ONLY 教师出题", "questions": [
                    {"id": f"ai-q{i}", "chapter_id": "ai-practice", "knowledge_point_id": None,
                     "text": f"TEST ONLY 第 {i} 题", "options": ["A | B", "C", "D", "E"],
                     "answer": 0, "explanation": "TEST ONLY 课件第1张。"} for i in range(1, 11)]}
        (jobs.root / job["id"] / "quiz.json").write_text(json.dumps(quiz), encoding="utf-8")
        return job["id"], quiz

    def test_ai_import_selected_questions_feed_assignment_and_hide_private_source(self):
        cid, original = self.course()
        course = self.request(f"/api/courses/{cid}")
        job_id, quiz = self.ai_quiz()
        payload = {"job_id": job_id, "quiz_id": quiz["id"], "chapter_id": original["chapter_id"],
                   "question_ids": ["ai-q1", "ai-q3"]}
        result = self.request(f"/api/courses/{cid}/questions/import-ai", payload)
        self.assertEqual(len(result["imported_ids"]), 2)
        self.assertEqual(len(result["course"]["questions"]), len(course["questions"]) + 2)
        self.assertEqual(result["course"]["questions"][-1]["options"][0], "A | B")
        student = self.request(f"/api/courses/{cid}", account=STUDENT)
        for question in student["questions"]:
            self.assertNotIn("answer", question)
            self.assertNotIn("explanation", question)
            self.assertNotIn("ai_source", question)
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "AI 审核题作业", "chapter_id": original["chapter_id"],
            "question_ids": result["imported_ids"], "due_at": _iso(timedelta(days=1))})
        self.request(f"/api/courses/{cid}/assignments/{assignment['id']}/publish", {})
        detail = self.request(f"/api/assignments/{assignment['id']}", account=STUDENT)
        self.assertEqual(detail["assignment"]["question_count"], 2)
        attempt = self.request(f"/api/assignments/{assignment['id']}/start", {}, account=STUDENT)
        submission = self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": assignment["id"], "attempt_id": attempt["id"],
            "answers": {qid: 0 for qid in result["imported_ids"]}}, account=STUDENT)
        self.assertEqual(submission["total"], 2)
        self.assertEqual(submission["score"], 100)
        overview = self.request(f"/api/courses/{cid}/assignments/{assignment['id']}/submissions")
        row = next(s for s in overview["students"] if s["student_id"] == STUDENT)
        self.assertEqual(row["submission_id"], submission["id"])

    def test_ai_import_owner_scope_versions_and_input_validation(self):
        cid, question = self.course()
        job_id, quiz = self.ai_quiz()
        endpoint = f"/api/courses/{cid}/questions/import-ai"
        payload = {"job_id": job_id, "quiz_id": quiz["id"], "chapter_id": question["chapter_id"],
                   "question_ids": ["ai-q1"]}
        self.error(endpoint, payload, account=STUDENT, status=403)
        self.error(endpoint, payload, account=OTHER_TEACHER, status=404)
        other_job, other_quiz = self.ai_quiz(OTHER_TEACHER)
        self.error(endpoint, {**payload, "job_id": other_job, "quiz_id": other_quiz["id"]}, status=404)
        self.error(endpoint, {**payload, "quiz_id": "old-version"}, status=409)
        for change in ({"question_ids": []}, {"question_ids": "ai-q1"},
                       {"question_ids": ["ai-q1", "ai-q1"]}, {"question_ids": [1]},
                       {"question_ids": ["unknown"]}, {"chapter_id": "other-course-chapter"},
                       {"chapter_id": []}):
            self.error(endpoint, {**payload, **change})
        self.assertEqual(len(self.request(f"/api/courses/{cid}")["questions"]), 1)
        self.request(f"/api/courses/{cid}", method="DELETE")
        self.error(endpoint, payload, status=409)

    def test_publish_requires_current_active_selected_questions(self):
        cid, question = self.course()
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "发布前检查题目", "chapter_id": question["chapter_id"],
            "question_ids": [question["id"]], "due_at": _iso(timedelta(days=1))})
        self.request(f"/api/courses/{cid}/questions/{question['id']}", method="DELETE")
        self.error(f"/api/courses/{cid}/assignments/{assignment['id']}/publish", {})

    def test_ai_import_duplicate_retry_disk_rollback_and_restart(self):
        cid, question = self.course()
        job_id, quiz = self.ai_quiz()
        endpoint = f"/api/courses/{cid}/questions/import-ai"
        payload = {"job_id": job_id, "quiz_id": quiz["id"], "chapter_id": question["chapter_id"],
                   "question_ids": ["ai-q1"]}
        first = self.request(endpoint, payload)
        again = self.request(endpoint, payload)
        self.assertEqual(again["imported_ids"], [])
        self.assertEqual(again["existing_ids"], first["imported_ids"])
        before = self.request(f"/api/courses/{cid}")
        with patch.object(self.server.config.app.state.learning, "save", side_effect=OSError("TEST ONLY disk full")):
            self.error(endpoint, {**payload, "question_ids": ["ai-q2", "ai-q3"]}, status=507)
        self.assertEqual(self.request(f"/api/courses/{cid}"), before)
        updated = self.request(f"/api/courses/{cid}/questions/{first['imported_ids'][0]}", {
            "text": "长题干" * 150, "options": ["A | B" * 50, "第二项", "第三项", "第四项"],
            "explanation": "解析" * 1200}, method="PATCH")
        edited = updated["questions"][-1]
        self.assertEqual(len(edited["text"]), 450)
        self.assertEqual(edited["options"][0], "A | B" * 50)
        self.assertEqual(len(edited["explanation"]), 2400)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request(endpoint, payload)["existing_ids"], first["imported_ids"])

    def test_content_edit_order_retire_duplicate_archive_preview(self):
        cid, question = self.course()
        updated = self.request(f"/api/courses/{cid}", {"title": "修改后的课程", "description": "新简介"},
                               method="PATCH")
        self.assertEqual(updated["title"], "修改后的课程")
        self.assertEqual(updated["description"], "新简介")

        updated = self.request(f"/api/courses/{cid}/questions/{question['id']}", {
            "text": "2 + 2 等于几？", "options": ["4", "3", "2"], "answer": 0,
            "explanation": "二加二"}, method="PATCH")
        edited = updated["questions"][0]
        self.assertEqual(edited["text"], "2 + 2 等于几？")
        self.assertEqual(edited["options"], ["4", "3", "2"])

        second = self.request(f"/api/courses/{cid}/chapters", {"title": "第二章"}, account=TEACHER)
        updated = self.request(f"/api/courses/{cid}/chapters/reorder",
                               {"chapter_ids": [second["id"], updated["chapters"][0]["id"]]}, account=TEACHER)
        self.assertEqual(updated["chapters"][0]["id"], second["id"])

        updated = self.request(f"/api/courses/{cid}/questions",
                               {"text": "第二题", "options": ["A", "B"], "answer": 1,
                                "chapter_id": second["id"]}, account=TEACHER)
        first_chapter = updated["chapters"][1]["id"]
        question_ids = [item["id"] for item in updated["questions"]]
        reversed_ids = list(reversed(question_ids))
        updated = self.request(f"/api/courses/{cid}/questions/reorder",
                               {"question_ids": reversed_ids}, account=TEACHER)
        self.assertEqual([item["id"] for item in updated["questions"]], reversed_ids)

        # 软删除：教师仍能看到，学生与预览都看不到。
        updated = self.request(f"/api/courses/{cid}/questions/{question['id']}", None,
                               account=TEACHER, method="DELETE")
        retired = next(item for item in updated["questions"] if item["id"] == question["id"])
        self.assertEqual(retired["status"], "retired")
        student_course = self.request(f"/api/courses/{cid}", account=STUDENT)
        self.assertNotIn(question["id"], [item["id"] for item in student_course["questions"]])
        preview = self.request(f"/api/courses/{cid}/preview")
        self.assertNotIn(question["id"], [item["id"] for item in preview["questions"]])
        self.assertNotIn("answer", preview["questions"][0])
        self.assertNotIn("explanation", preview["questions"][0])

        # 删除章节时迁移题目。
        updated = self.request(f"/api/courses/{cid}/chapters/{second['id']}", {"move_to": first_chapter},
                               account=TEACHER, method="DELETE")
        moved = next(item for item in updated["questions"] if item["text"] == "第二题")
        self.assertEqual(moved["chapter_id"], first_chapter)

        # 复制课程：新 ID、草稿、不复制提交。
        clone = self.request(f"/api/courses/{cid}/duplicate", {}, method="POST")
        self.assertNotEqual(clone["id"], cid)
        self.assertEqual(clone["status"], "draft")
        self.assertEqual(clone["title"], "修改后的课程（副本）")
        self.assertTrue(all(item["id"] != question["id"] for item in clone["questions"]))

        # 归档后学生不可访问，恢复后恢复原状态。
        self.request(f"/api/courses/{cid}", {}, method="DELETE")
        archived = self.request(f"/api/courses/{cid}")
        self.assertEqual(archived["status"], "archived")
        self.error(f"/api/courses/{cid}", account=STUDENT, status=404)
        restored = self.request(f"/api/courses/{cid}/restore", {}, method="POST")
        self.assertEqual(restored["status"], "published")
        self.request(f"/api/courses/{cid}", account=STUDENT)

        # 其他教师/学生不能修改。
        self.error(f"/api/courses/{cid}", {"title": "越权"}, account=OTHER_TEACHER, method="PATCH", status=404)
        self.error(f"/api/courses/{cid}", {"title": "越权"}, account=STUDENT, method="PATCH", status=403)

    # --------------------------------------------------------------- assignments
    def test_assignment_start_attempts_late_and_manual_release(self):
        cid, question = self.course()
        future = self.request(f"/api/courses/{cid}/assignments", {
            "title": "第一次作业", "chapter_id": question["chapter_id"],
            "open_at": _iso(timedelta(days=-1)), "due_at": _iso(timedelta(days=1)),
            "max_attempts": 2, "time_limit_minutes": 0, "grade_release": "immediate",
            "status": "published"}, account=TEACHER)
        self.assertEqual(future["state"], "open")
        listed = self.request("/api/assignments", account=STUDENT)
        self.assertEqual([item["id"] for item in listed], [future["id"]])
        detail = self.request(f"/api/assignments/{future['id']}", account=STUDENT)
        self.assertNotIn("answer", detail["course"]["questions"][0])
        attempt = self.request(f"/api/assignments/{future['id']}/start", {}, account=STUDENT)
        submission = self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": future["id"], "attempt_id": attempt["id"],
            "answers": {question["id"]: 0}}, account=STUDENT)
        self.assertEqual(submission["score"], 100)
        self.assertFalse(submission["late"])
        second = self.request(f"/api/assignments/{future['id']}/start", {}, account=STUDENT)
        self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": future["id"], "attempt_id": second["id"],
            "answers": {question["id"]: 1}}, account=STUDENT)
        self.error(f"/api/assignments/{future['id']}/start", {}, account=STUDENT, status=409)
        teacher_view = next(item for item in self.request(f"/api/courses/{cid}/assignments")
                            if item["id"] == future["id"])
        self.assertEqual(teacher_view["submitted_count"], 1)
        self.assertEqual(teacher_view["student_count"], 3)

        late = self.request(f"/api/courses/{cid}/assignments", {
            "title": "迟交作业", "chapter_id": question["chapter_id"],
            "open_at": _iso(timedelta(days=-3)), "due_at": _iso(timedelta(days=-1)),
            "allow_late": True, "late_max_days": 3, "late_penalty_percent_per_day": 10,
            "max_attempts": 1, "grade_release": "immediate", "status": "published"}, account=TEACHER)
        self.assertEqual(late["state"], "late")
        attempt = self.request(f"/api/assignments/{late['id']}/start", {}, account=STUDENT)
        submission = self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": late["id"], "attempt_id": attempt["id"],
            "answers": {question["id"]: 0}}, account=STUDENT)
        self.assertTrue(submission["late"])
        self.assertGreater(submission["penalty_percent"], 0)
        self.assertLess(submission["score"], 100)

        closed = self.request(f"/api/courses/{cid}/assignments", {
            "title": "已截止作业", "chapter_id": question["chapter_id"],
            "open_at": _iso(timedelta(days=-3)), "due_at": _iso(timedelta(days=-1)),
            "max_attempts": 1, "status": "published"}, account=TEACHER)
        self.assertEqual(closed["state"], "closed")
        self.error(f"/api/assignments/{closed['id']}/start", {}, account=STUDENT, status=409)

        manual = self.request(f"/api/courses/{cid}/assignments", {
            "title": "手动发布成绩", "chapter_id": question["chapter_id"],
            "open_at": _iso(timedelta(days=-1)), "due_at": _iso(timedelta(days=1)),
            "max_attempts": 1, "grade_release": "manual", "status": "published"}, account=TEACHER)
        attempt = self.request(f"/api/assignments/{manual['id']}/start", {}, account=STUDENT)
        hidden = self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": manual["id"], "attempt_id": attempt["id"],
            "answers": {question["id"]: 1}}, account=STUDENT)
        self.assertTrue(hidden["pending_release"])
        self.assertIsNone(hidden["score"])
        records = self.request("/api/learning-records", account=STUDENT)
        hidden_record = next(item for item in records if item["id"] == hidden["id"])
        self.assertTrue(hidden_record["pending_release"])
        self.assertIsNone(hidden_record["score"])
        self.assertNotIn(question["id"], [item["question_id"] for item in
                                          self.request("/api/wrong-questions", account=STUDENT)])
        self.error(f"/api/learning-records/{hidden['id']}/analysis", {}, account=STUDENT, status=409)
        teacher_gradebook = self.request(f"/api/teacher/gradebook?course_id={cid}")
        self.assertTrue(teacher_gradebook["columns"])
        self.request(f"/api/courses/{cid}/assignments/{manual['id']}/release", {}, method="POST")
        records = self.request("/api/learning-records", account=STUDENT)
        visible = next(item for item in records if item["id"] == hidden["id"])
        self.assertEqual(visible["score"], 0)
        self.assertIn(question["id"], [item["question_id"] for item in
                                       self.request("/api/wrong-questions", account=STUDENT)])

    def test_assignment_validation_permissions_and_reminder(self):
        cid, question = self.course()
        self.error(f"/api/courses/{cid}/assignments", {
            "title": "没有截止时间", "chapter_id": question["chapter_id"], "status": "published"}, status=400)
        self.error(f"/api/courses/{cid}/assignments", {
            "title": "错误章节", "chapter_id": "missing", "due_at": _iso(timedelta(days=1)),
            "status": "published"}, status=400)
        self.error(f"/api/courses/{cid}/assignments", {
            "title": "越权", "chapter_id": question["chapter_id"], "due_at": _iso(timedelta(days=1)),
            "status": "published"}, account=OTHER_TEACHER, status=404)
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "提醒作业", "chapter_id": question["chapter_id"],
            "open_at": _iso(timedelta(days=-1)), "due_at": _iso(timedelta(days=1)),
            "status": "published"}, account=TEACHER)
        reminder = self.request(f"/api/courses/{cid}/assignments/{assignment['id']}/remind", {},
                                account=TEACHER, method="POST")
        self.assertEqual(reminder["audience"], "non_submitters")
        self.assertGreater(reminder["recipient_count"], 0)

    # ------------------------------------------------------------------ gradebook
    def test_student_assignment_progress_separates_matching_practice_and_formal_submission(self):
        cid, question = self.course()
        chapter = self.request(f"/api/courses/{cid}/chapters", {"title": "另一个章节"})
        second = self.request(f"/api/courses/{cid}/questions", {
            "text": "另一道题", "options": ["正确", "错误"], "answer": 0,
            "chapter_id": chapter["id"]})["questions"][-1]
        practice = self.request(f"/api/courses/{cid}/submissions", {
            "chapter_id": question["chapter_id"], "answers": {question["id"]: 0}}, account=STUDENT)
        self.request(f"/api/courses/{cid}/submissions", {
            "chapter_id": question["chapter_id"], "answers": {question["id"]: 0}}, account=SOFT_STUDENT)
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "对应题目作业", "question_ids": [question["id"]],
            "due_at": _iso(timedelta(days=1)), "status": "published"})
        other = self.request(f"/api/courses/{cid}/assignments", {
            "title": "其他题目作业", "question_ids": [second["id"]],
            "due_at": _iso(timedelta(days=1)), "status": "published"})
        items = {item["id"]: item for item in self.request("/api/assignments", account=STUDENT)}
        self.assertEqual((items[assignment["id"]]["attempts"], items[assignment["id"]]["practice_count"]), (0, 1))
        self.assertEqual(items[other["id"]]["practice_count"], 0)
        endpoint = f"/api/assignments/{assignment['id']}"
        detail = self.request(endpoint, account=STUDENT)["assignment"]
        self.assertEqual((detail["attempts"], detail["practice_count"]), (0, 1))
        submission = self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": assignment["id"], "answers": {question["id"]: 0}}, account=STUDENT)
        detail = self.request(endpoint, account=STUDENT)["assignment"]
        self.assertEqual((detail["attempts"], detail["practice_count"]), (1, 1))
        self.assertEqual(detail["latest_submission_id"], submission["id"])
        records = {record["id"]: record for record in self.request("/api/learning-records", account=STUDENT)}
        self.assertEqual(records[practice["id"]]["source_type"], "self_practice")
        self.assertEqual(records[submission["id"]]["source_type"], "assignment")

    def test_gradebook_csv_permissions_and_student_drilldown(self):
        cid, question = self.course()
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "成绩册作业", "chapter_id": question["chapter_id"],
            "due_at": _iso(timedelta(days=1)), "status": "published"})
        self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": assignment["id"], "answers": {question["id"]: 0}}, account=STUDENT)
        gradebook = self.request(f"/api/teacher/gradebook?course_id={cid}&mode=best")
        self.assertTrue(gradebook["columns"])
        row = next(item for item in gradebook["students"] if item["student_id"] == STUDENT)
        self.assertEqual(row["average_score"], 100)
        self.assertEqual(gradebook["summary"]["submitted_students"], 1)
        csv_bytes, headers = self.raw(f"/api/teacher/gradebook.csv?course_id={cid}")
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertIn("张同学", csv_bytes.decode("utf-8-sig"))
        disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
        self.assertIn("attachment", disposition)
        self.error(f"/api/teacher/gradebook?course_id={cid}", account=OTHER_TEACHER, status=404)
        self.error(f"/api/teacher/gradebook?course_id={cid}", account=STUDENT, status=403)
        detail = self.request(f"/api/teacher/students/{STUDENT}?course_id={cid}")
        self.assertEqual(detail["student"]["id"], STUDENT)
        self.assertTrue(detail["submissions"])
        self.assertEqual(detail["profile"]["average_score"], 100)
        self.error(f"/api/teacher/students/{STUDENT}?course_id={cid}", account=OTHER_TEACHER, status=404)

    def test_gradebook_filters_and_empty_course(self):
        cid, question = self.course()
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "成绩册作业", "chapter_id": question["chapter_id"],
            "due_at": _iso(timedelta(days=1)), "status": "published"})
        self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": assignment["id"], "answers": {question["id"]: 0}}, account=STUDENT)
        only_missing = self.request(f"/api/teacher/gradebook?course_id={cid}&status=missing")
        self.assertNotIn(STUDENT, [row["student_id"] for row in only_missing["students"]])
        only_submitted = self.request(f"/api/teacher/gradebook?course_id={cid}&status=submitted")
        self.assertEqual([row["student_id"] for row in only_submitted["students"]], [STUDENT])
        self.error(f"/api/teacher/gradebook?course_id={cid}&mode=bad", status=400)
        self.error(f"/api/teacher/gradebook?course_id={cid}&chapter_id=missing", status=400)

    def test_gradebook_has_no_grades_without_published_assignment(self):
        cid, question = self.course()
        practice = self.request(f"/api/courses/{cid}/submissions", {
            "answers": {question["id"]: 0}}, account=STUDENT)
        assignment = self.request(f"/api/courses/{cid}/assignments", {
            "title": "草稿作业", "chapter_id": question["chapter_id"],
            "due_at": _iso(timedelta(days=1))})
        gradebook = self.request(f"/api/teacher/gradebook?course_id={cid}")
        self.assertEqual(gradebook["columns"], [])
        self.assertIsNone(gradebook["summary"]["average_score"])
        self.assertEqual(gradebook["summary"]["submitted_students"], 0)
        records = self.request("/api/learning-records", account=STUDENT)
        record = next(item for item in records if item["id"] == practice["id"])
        self.assertEqual(record["source_type"], "self_practice")
        self.request(f"/api/courses/{cid}/assignments/{assignment['id']}/publish", {})
        published = self.request(f"/api/teacher/gradebook?course_id={cid}")
        self.assertEqual(len(published["columns"]), 1)
        self.assertIsNone(published["summary"]["average_score"])
        self.request(f"/api/courses/{cid}/submissions", {
            "assignment_id": assignment["id"], "answers": {question["id"]: 0}}, account=STUDENT)
        submitted = self.request(f"/api/teacher/gradebook?course_id={cid}")
        self.assertEqual(submitted["summary"]["average_score"], 100)
        self.assertTrue(all(column["kind"] == "assignment" for column in submitted["columns"]))

    # --------------------------------------------------------------- announcements
    def test_announcement_audience_read_and_permissions(self):
        cid, question = self.course()
        announcement = self.request(f"/api/courses/{cid}/announcements", {
            "title": "课程通知", "body": "周五交作业", "audience": "all", "pinned": True}, account=TEACHER)
        self.assertEqual(announcement["recipient_count"], 3)  # 软件工程预置学生
        inbox = self.request("/api/announcements", account=STUDENT)
        self.assertEqual(inbox["unread_count"], 1)
        self.assertEqual(inbox["items"][0]["title"], "课程通知")
        self.assertFalse(inbox["items"][0]["read"])
        self.assertEqual(self.request("/api/announcements", account=OTHER_STUDENT)["items"], [])
        read = self.request(f"/api/announcements/{announcement['id']}/read", {}, account=STUDENT)
        self.assertTrue(read["read"])
        self.assertEqual(self.request("/api/announcements", account=STUDENT)["unread_count"], 0)
        self.error(f"/api/courses/{cid}/announcements", {"title": "越权", "body": "x"},
                   account=OTHER_TEACHER, status=404)
        self.error(f"/api/courses/{cid}/announcements", {"title": "越权", "body": "x"},
                   account=STUDENT, status=403)
        self.request(f"/api/courses/{cid}/announcements/{announcement['id']}", {}, method="DELETE")
        self.assertEqual(self.request("/api/announcements", account=STUDENT)["items"], [])

    def test_non_submitters_and_at_risk_audiences(self):
        cid, question = self.course()
        self.request(f"/api/courses/{cid}/submissions", {"answers": {question["id"]: 0}}, account=STUDENT)
        non_submitters = self.request(f"/api/courses/{cid}/announcements", {
            "title": "请交作业", "body": "尚未提交", "audience": "non_submitters"}, account=TEACHER)
        self.assertNotIn(non_submitters["id"], [item["id"] for item in self.request("/api/announcements", account=STUDENT)["items"]])
        self.assertIn(non_submitters["id"], [item["id"] for item in self.request("/api/announcements", account=SOFT_STUDENT)["items"]])
        at_risk = self.request(f"/api/courses/{cid}/announcements", {
            "title": "学习提醒", "body": "请关注", "audience": "at_risk"}, account=TEACHER)
        self.assertGreater(at_risk["recipient_count"], 0)
        self.error(f"/api/courses/{cid}/announcements", {
            "title": "无效受众", "body": "x", "audience": "bad"}, status=400)


if __name__ == "__main__":
    unittest.main()
