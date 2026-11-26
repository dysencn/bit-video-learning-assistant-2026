"""Saved AI questions can feed owned course banks and private review books."""
from copy import deepcopy
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
from urllib.request import Request, build_opener, HTTPCookieProcessor
import uuid

import uvicorn
from demo.app import create_app, knowledge_schedule, LearningStore
from test_quizzes import FIXTURE


class QuizActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="quiz-actions-");cls.root=Path(cls.temp.name)
        cls.app=create_app(cls.root/"jobs",seed_demo_history=False)
        cls.sock=socket.socket();cls.sock.bind(("127.0.0.1",0));cls.url=f"http://127.0.0.1:{cls.sock.getsockname()[1]}"
        cls.server=uvicorn.Server(uvicorn.Config(cls.app,log_level="error"))
        cls.thread=threading.Thread(target=lambda:cls.server.run(sockets=[cls.sock]),daemon=True);cls.thread.start()
        deadline=time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline:raise RuntimeError("startup timeout")
            time.sleep(.02)
        cls.client=build_opener(HTTPCookieProcessor(CookieJar()));cls.token=""
        cls.token=cls.req("/api/config")["token"]
        for name,password in [("student1","Learn123!"),("student2","Learn456!"),("student4","Learn123!"),
                              ("teacher1","Teach123!"),("teacher2","Teach456!")]:
            cls.req("/api/auth/login",{"username":name,"password":password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(20);cls.sock.close();cls.temp.cleanup()

    @classmethod
    def req(cls,path,payload=None,account="student-demo-1",method=None):
        request=Request(cls.url+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,
                        headers={"Content-Type":"application/json","X-Local-Token":cls.token,"X-Account-ID":account})
        with cls.client.open(request,timeout=10) as response:
            body=response.read()
            return json.loads(body) if "application/json" in response.headers.get("Content-Type","") else body.decode("utf-8-sig")

    def error(self,status,path,payload=None,account="student-demo-1",method=None):
        with self.assertRaises(HTTPError) as caught:self.req(path,payload,account,method)
        self.assertEqual(caught.exception.code,status);caught.exception.close()

    def setUp(self):
        self.jobs=self.app.state.jobs;self.store=self.app.state.learning
        self.jid,self.quiz=self.make_quiz()
        self.endpoint=f"/api/jobs/{self.jid}/quiz/wrong-questions"
        self.payload={"quiz_id":self.quiz["id"],"question_id":self.quiz["questions"][0]["id"]}

    def make_quiz(self,owner="student-demo-1"):
        job=self.jobs.create("TEST ONLY AI questions.mp4","balanced",owner_id=owner);jid=job["id"]
        self.jobs.update(jid,status="completed");self.jobs.active=None
        quiz={**deepcopy(FIXTURE),"id":uuid.uuid4().hex,"source_mode":"slides","source_summary":"a"*64}
        (self.jobs.root/jid/"quiz.json").write_text(json.dumps(quiz),encoding="utf-8")
        return jid,quiz

    def test_manual_add_before_attempt_snapshot_dedup_and_persistence(self):
        before=deepcopy(self.store.data["submissions"])
        result=self.req(self.endpoint,self.payload);record=result["record"]
        self.assertTrue(result["added"]);self.assertEqual(record["wrong_count"],0)
        self.assertIsNone(record["last_wrong_at"]);self.assertEqual(record["source"],"ai_quiz_manual")
        self.assertEqual(record["question_snapshot"]["text"],self.quiz["questions"][0]["text"])
        self.assertIn(self.payload["question_id"],self.req(self.endpoint)["question_ids"])
        self.req(f"/api/wrong-questions/{record['id']}/mastered",{})
        again=self.req(self.endpoint,self.payload)
        self.assertFalse(again["added"]);self.assertEqual(again["record"]["id"],record["id"])
        self.assertEqual(again["record"]["status"],"mastered")
        self.assertEqual(self.store.data["submissions"],before)
        reloaded=LearningStore(self.root/"learning-platform.json")
        self.assertEqual(next(r for r in reloaded.data["wrong_questions"] if r["id"]==record["id"]),again["record"])

    def test_review_export_and_deleted_video_keep_independent_question(self):
        record=self.req(self.endpoint,self.payload)["record"]
        self.req(f"/api/jobs/{self.jid}",method="DELETE")
        item=next(r for r in self.req("/api/wrong-questions") if r["id"]==record["id"])
        self.assertIn(self.quiz["title"],item["course_name"])
        due=self.req("/api/review/due");self.assertIn(record["id"],[r["id"] for r in due])
        exported=self.req("/api/wrong-questions/export.csv?course_id=ai-video:"+self.jid)
        self.assertIn(self.quiz["title"],exported);self.assertIn(record["question_snapshot"]["text"],exported)
        answer=record["question_snapshot"]["answer"]
        wrong=self.req(f"/api/wrong-questions/{record['id']}/review",{"selected":(answer+1)%4})
        self.assertFalse(wrong["correct"]);self.assertEqual(wrong["wrong_count"],1)
        self.assertTrue(wrong["first_wrong_at"])
        correct=self.req(f"/api/wrong-questions/{record['id']}/review",{"selected":answer})
        self.assertTrue(correct["correct"]);self.assertEqual(correct["review_count"],2)
        self.assertEqual(correct["question_snapshot"],record["question_snapshot"])

    def test_permissions_invalid_inputs_and_no_client_replacement(self):
        for account,status in [("student-demo-2",404),("teacher-demo-1",403)]:
            self.error(status,self.endpoint,self.payload,account)
            self.error(status,self.endpoint,account=account)
        self.error(409,self.endpoint,{**self.payload,"quiz_id":"stale"})
        for bad in [None,[],"missing"]:
            self.error(400,self.endpoint,{**self.payload,"question_id":bad})
        record=self.req(self.endpoint,{**self.payload,"answer":3,"text":"tampered","student_id":"student-demo-2"})["record"]
        self.assertEqual(record["student_id"],"student-demo-1")
        self.assertNotEqual(record["question_snapshot"]["text"],"tampered")
        self.assertNotIn(record["id"],[r["id"] for r in self.req("/api/wrong-questions",account="student-demo-2")])
        self.error(404,f"/api/wrong-questions/{record['id']}/review",{"selected":0},"student-demo-2")

    def test_new_quiz_version_does_not_overwrite_saved_question(self):
        first=self.req(self.endpoint,self.payload)["record"]
        quiz=deepcopy(self.quiz);quiz["id"]=uuid.uuid4().hex;quiz["questions"][0]["text"]="TEST ONLY changed question"
        (self.jobs.root/self.jid/"quiz.json").write_text(json.dumps(quiz),encoding="utf-8")
        self.assertEqual(self.req(self.endpoint)["question_ids"],[])
        self.error(409,self.endpoint,self.payload)
        second=self.req(self.endpoint,{**self.payload,"quiz_id":quiz["id"]})["record"]
        self.assertNotEqual(first["id"],second["id"])
        saved=next(r for r in self.req("/api/wrong-questions") if r["id"]==first["id"])
        self.assertEqual(saved["question_snapshot"],first["question_snapshot"])

    def test_save_failure_rolls_back_and_retry_works(self):
        before=deepcopy(self.store.data["wrong_questions"])
        with patch.object(self.store,"save",side_effect=OSError("TEST disk full")):
            self.error(507,self.endpoint,self.payload)
        self.assertEqual(self.store.data["wrong_questions"],before)
        self.assertEqual(self.req(self.endpoint)["question_ids"],[])
        self.assertTrue(self.req(self.endpoint,self.payload)["added"])

    def test_teacher_publishes_saved_questions_to_own_course_without_ai_call(self):
        jid,quiz=self.make_quiz("teacher-demo-1")
        before=deepcopy(self.store.course("course-demo"));chapter=before["chapters"][0]["id"]
        payload={"job_id":jid,"quiz_id":quiz["id"],"chapter_id":chapter,"question_ids":[q["id"] for q in quiz["questions"][:2]]}
        with patch("demo.summaries.call_deepseek",side_effect=AssertionError("should not call AI")):
            result=self.req("/api/courses/course-demo/questions/import-ai",payload,"teacher-demo-1")
        self.assertEqual(len(result["imported_ids"]),2)
        student=self.req("/api/courses/course-demo")
        copied=[q for q in student["questions"] if q["id"] in result["imported_ids"]]
        self.assertEqual([q["text"] for q in copied],[q["text"] for q in quiz["questions"][:2]])
        self.assertTrue(all(q["chapter_id"]==chapter for q in copied))
        self.assertTrue(all("ai_source" not in q for q in copied))
        again=self.req("/api/courses/course-demo/questions/import-ai",payload,"teacher-demo-1")
        self.assertEqual(again["imported_ids"],[]);self.assertEqual(len(again["existing_ids"]),2)
        self.req(f"/api/jobs/{jid}",account="teacher-demo-1",method="DELETE")
        self.assertEqual(len(self.req("/api/courses/course-demo")["questions"]),len(before["questions"])+2)

    def test_knowledge_graph_traces_video_to_practice_review_and_class_feedback(self):
        teacher = "teacher-demo-1"
        student = "student-demo-1"
        course = self.req("/api/courses", {"title": "知识图谱验收", "major_ids": ["m-soft"]}, teacher)
        cid = course["id"]
        base = f"/api/courses/{cid}"
        endpoint = f"{base}/knowledge-graph"
        chapter = course["chapters"][0]["id"]
        original = {"text": "本章已有题目", "options": ["正确", "错误"], "answer": 0, "chapter_id": chapter}
        for name in ("变量", "循环", "列表"):
            course = self.req(f"{base}/questions", {**original, "knowledge_point": name}, teacher)
        variable, loop, sequence = [point["id"] for point in course["knowledge_points"]]
        self.assertEqual(self.req(endpoint, account=teacher)["revision"], 0)
        self.error(404, endpoint)

        jid, quiz = self.make_quiz(teacher)
        self.jobs.update(jid, result={"pages": [{"number": 1, "timestamp": 72.5}],
                                      "metadata": {"duration": 90}})
        slides = self.jobs.root / jid / "slides"
        slides.mkdir()
        (slides / "slide-0001.jpg").write_bytes(b"page fixture")
        course = self.req(f"{base}/resources", {"name": "循环课件", "job_id": jid}, teacher)
        resource_id = course["resources"][0]["id"]
        qid = quiz["questions"][0]["id"]
        payload = {"job_id": jid, "quiz_id": quiz["id"], "chapter_id": chapter,
                   "question_ids": [qid], "knowledge_point_ids": {qid: loop}}
        self.error(400, f"{base}/questions/import-ai", {**payload, "knowledge_point_ids": {qid: "kp1"}}, teacher)
        imported = self.req(f"{base}/questions/import-ai", payload, teacher)
        imported_id = imported["imported_ids"][0]
        repeat = self.req(f"{base}/questions/import-ai", {**payload, "knowledge_point_ids": {qid: variable}}, teacher)
        self.assertEqual(repeat["existing_ids"], [imported_id])
        self.assertEqual(next(q for q in repeat["course"]["questions"] if q["id"] == imported_id)["knowledge_point_id"], loop)

        edges = [{"before": variable, "after": loop}, {"before": loop, "after": sequence}]
        source = {"knowledge_point_id": loop, "resource_id": resource_id, "page": 1}
        update = {"revision": 0, "prerequisites": edges, "evidence": [source]}
        self.error(400, endpoint, {**update, "prerequisites": edges + [{"before": sequence, "after": variable}]},
                   teacher, "PUT")
        self.error(400, endpoint, {**update, "evidence": [{**source, "page": 2}]}, teacher, "PUT")
        self.error(400, endpoint, {**update, "evidence": [{**source, "resource_id": "res-demo"}]}, teacher, "PUT")
        self.error(403, endpoint, update, student, "PUT")
        self.error(404, endpoint, update, "teacher-demo-2", "PUT")
        with patch.object(self.store, "save", side_effect=OSError("TEST disk full")):
            self.error(507, endpoint, update, teacher, "PUT")
        self.assertEqual(self.req(endpoint, account=teacher)["revision"], 0)
        self.assertEqual(self.req(endpoint, update, teacher, "PUT"), {"revision": 1})
        self.error(409, endpoint, update, teacher, "PUT")

        self.req(f"{base}/enrollments", {"student_ids": [student, "student-demo-4"]}, teacher, "PUT")
        self.req(f"{base}/publish", {}, teacher)
        self.error(404, endpoint, account="teacher-demo-2")
        self.error(404, endpoint, account="student-demo-2")
        def node(graph):
            return next(item for item in graph["nodes"] if item["id"] == loop)

        initial = node(self.req(endpoint))
        self.assertIsNone(initial["accuracy"])
        self.assertEqual(initial["evidence"][0]["timestamp"], 72.5)
        self.assertEqual(self.req(f"/api/jobs/{jid}/pages/1"), "page fixture")
        self.assertNotIn("knowledge_graph", self.req(base))

        # Make the wrong answer due on the test day without changing the scheduler.
        answers = {q["id"]: q["answer"] for q in self.req(base)["questions"]}
        correct = answers[imported_id]
        answers[imported_id] = (correct + 1) % len(quiz["questions"][0]["options"])
        with patch("demo.app._days_later", return_value="2000-01-01T00:00:00+00:00"):
            submission = self.req(f"{base}/submissions", {"chapter_id": chapter, "answers": answers})
        detail = next(item for item in submission["details"] if item["question_id"] == imported_id)
        self.assertEqual(detail["knowledge_point_id"], loop)
        legacy = deepcopy(submission)
        for legacy_detail in legacy["details"]:
            legacy_detail.pop("knowledge_point_id")
        schedule = knowledge_schedule([legacy, submission])
        self.assertEqual([item["answered"] for item in schedule if item["id"] == loop], [4])
        wrongs = self.req(f"/api/wrong-questions?course_id={cid}&knowledge_point_id={loop}")
        self.assertEqual(len(wrongs), 1)
        wrong = wrongs[0]
        self.assertEqual(wrong["question_id"], imported_id)
        self.assertEqual(node(self.req(endpoint, account="student-demo-4"))["answered"], 0)
        self.assertEqual(node(self.req(endpoint, account=teacher))["accuracy"], 50)
        self.assertEqual(node(self.req(endpoint))["next_review_at"], wrong["next_review_at"])
        self.assertIn(wrong["id"], {item["id"] for item in self.req("/api/review/due")})

        review = self.req(f"/api/wrong-questions/{wrong['id']}/review", {"selected": correct})
        self.assertTrue(review["correct"])
        self.assertNotEqual(review["next_review_at"], wrong["next_review_at"])
        self.assertEqual(node(self.req(endpoint))["next_review_at"], review["next_review_at"])
        summary = node(self.req(endpoint, account=teacher))
        self.assertEqual((summary["answered"], summary["correct"], summary["review_count"], summary["review_correct"]),
                         (2, 1, 1, 1))
        self.assertNotIn("next_review_at", summary)
        self.assertEqual(self.req("/api/learning-records")[0]["score"], submission["score"])

        copied = self.req(f"{base}/duplicate", {}, teacher)
        copied_graph = self.req(f"/api/courses/{copied['id']}/knowledge-graph", account=teacher)
        self.assertEqual(len(copied_graph["prerequisites"]), 2)
        copied_loop = next(item for item in copied_graph["nodes"] if item["name"] == "循环")
        self.assertNotEqual(copied_loop["id"], loop)
        self.assertNotEqual(copied_loop["evidence"][0]["resource_id"], resource_id)
        self.assertEqual(copied_loop["answered"], 0)
        self.error(404, f"/api/courses/{copied['id']}/knowledge-graph", account="student-demo-4")

        self.req(f"{base}/enrollments", {"student_ids": ["student-demo-4"]}, teacher, "PUT")
        self.error(404, endpoint)
        self.assertEqual(node(self.req(endpoint, account=teacher))["answered"], 0)
        self.req(f"/api/jobs/{jid}", account=teacher, method="DELETE")
        unavailable = node(self.req(endpoint, account=teacher))["evidence"][0]
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["job_id"])
        self.assertEqual(self.req(endpoint, {**update, "revision": 1}, teacher, "PUT"), {"revision": 2})
        loaded = LearningStore(self.root / "learning-platform.json")
        self.assertEqual(loaded.course(cid)["knowledge_graph"]["revision"], 2)
        self.assertEqual(next(event for event in loaded.data["learning_events"] if event["ref_id"] == wrong["id"])
                         ["knowledge_point_id"], loop)
        self.assertEqual(self.req(f"/api/wrong-questions?course_id={cid}&knowledge_point_id={loop}")[0]["id"], wrong["id"])
        self.assertEqual(self.req("/api/learning-records")[0]["score"], submission["score"])


if __name__=="__main__":unittest.main()
