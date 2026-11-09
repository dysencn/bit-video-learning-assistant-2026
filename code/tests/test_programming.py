"""Real Python processes and HTTP integration, using the project venv."""
from copy import deepcopy
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
from demo.app import create_app, LearningStore
from demo.programming import run_case, normalize, validate_question
from demo.demo_catalog import demo_programming_questions


class ProgrammingSampleMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_edits_records_and_is_persisted_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            store = LearningStore(path)
            course = store.course("course-demo")
            edited = deepcopy(course["programming_questions"][0])
            edited["description"] = "教师修改的内容"
            custom = {**deepcopy(edited), "id": "teacher-custom"}
            course["programming_questions"] = [edited, custom]
            store.data.pop("demo_programming_version")
            store.data["submissions"].append({"id": "keep", "student_id": "student-demo-1", "score": 80})
            before = deepcopy(store.data)
            store.save()
            migrated = LearningStore(path)
            questions = migrated.course("course-demo")["programming_questions"]
            self.assertEqual(len(questions), 7)
            self.assertEqual(questions[:2], [edited, custom])
            unchanged = deepcopy(migrated.data)
            unchanged.pop("demo_programming_version")
            next(c for c in unchanged["courses"] if c["id"] == "course-demo")["programming_questions"] = [edited, custom]
            self.assertEqual(unchanged, before)
            self.assertEqual(LearningStore(path).data, migrated.data)
            # Do not recreate a sample intentionally removed after initialization.
            questions.pop()
            migrated.save()
            self.assertEqual(LearningStore(path).data, migrated.data)

    def test_upgrade_respects_reorganized_chapters_and_deleted_course(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning-platform.json"
            store = LearningStore(path)
            course = store.course("course-demo")
            course["chapters"] = [{"id": "custom-chapter", "title": "自定义章节", "order": 1}]
            course.pop("programming_questions")
            store.data.pop("demo_programming_version")
            store.save()
            migrated = LearningStore(path)
            self.assertEqual({q["chapter_id"] for q in migrated.course("course-demo")["programming_questions"]}, {"custom-chapter"})
            migrated.data["courses"] = [c for c in migrated.data["courses"] if c["id"] != "course-demo"]
            migrated.data.pop("demo_programming_version")
            migrated.save()
            self.assertIsNone(LearningStore(path).course("course-demo"))


class RunnerTests(unittest.TestCase):
    def check(self, code, expected_status, output="", input_text=""):
        result = run_case(code, {"input": input_text, "output": output})
        self.assertEqual(result["status"], expected_status, result)
        self.assertLess(result["seconds"], 7)
        return result

    def test_correct_wrong_and_unicode(self):
        self.check("a,b=map(int,input().split());print(a+b)", "accepted", "5", "2 3")
        self.check("print('你好')", "accepted", "你好\n")
        self.check("print(4)", "wrong_answer", "5")
        self.assertEqual(normalize("a  \r\nb\r\n"), "a\nb")
        self.assertNotEqual(normalize(" a"), normalize("a"))

    def test_timeout(self):
        self.check("while True: pass", "time_limit")

    def test_sleep_timeout(self):
        self.check("import time;time.sleep(30)", "time_limit")

    def test_infinite_output(self):
        result = self.check("while True: print('x'*4096)", "output_limit")
        self.assertLessEqual(len(result["stdout"]), 4096)

    def test_stderr_limit(self):
        self.check("import sys\nwhile True: sys.stderr.write('x'*4096)", "output_limit")

    def test_memory_limit(self):
        self.check("x=[]\nwhile True: x.append(bytearray(8*1024*1024))", "memory_limit")

    def test_runtime_syntax_and_eof(self):
        for code in ["print(1/0)", "if", "input();input()"]:
            self.check(code, "runtime_error")

    def test_environment_and_basic_guards(self):
        self.check("import os;print(os.environ.get('DEEPSEEK_API_KEY','absent'))", "accepted", "absent")
        for code in ["import socket;socket.socket()", "open('file.txt','w')", "import subprocess;subprocess.run(['whoami'])"]:
            result = self.check(code, "runtime_error")
            self.assertIn("PermissionError", result["stderr"])

    def test_standard_library_and_after_failure(self):
        self.check("from collections import Counter\nimport math\nprint(math.factorial(5))", "accepted", "120")

    def test_question_validation(self):
        base = {"title":"sum", "description":"sum", "examples":[{"input":"", "output":""}], "test_cases":[{"input":"", "output":""}]}
        self.assertEqual(validate_question(base)["type"], "python")
        for value in [[], {}, None, [{"input":1,"output":"a"}], [{"input":"x"*8193,"output":""}]]:
            with self.assertRaises(ValueError): validate_question({**base,"test_cases":value})


class ProgrammingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="programming-http-")
        cls.folder = Path(cls.temp.name)
        cls.app = create_app(cls.folder / "jobs", seed_demo_history=False)
        cls.sock = socket.socket(); cls.sock.bind(("127.0.0.1",0))
        cls.url = f"http://127.0.0.1:{cls.sock.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, log_level="error"))
        cls.thread = threading.Thread(target=lambda: cls.server.run(sockets=[cls.sock]), daemon=True)
        cls.thread.start()
        deadline = time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline: raise RuntimeError("server did not start")
            time.sleep(.02)
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        with cls.client.open(cls.url+"/api/config") as response: cls.token=json.load(response)["token"]
        for name,pw in [("teacher1","Teach123!"),("teacher2","Teach456!"),("student1","Learn123!"),("student2","Learn456!")]:
            cls.request("/api/auth/login",{"username":name,"password":pw})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True; cls.thread.join(40); cls.sock.close(); cls.temp.cleanup()

    @classmethod
    def request(cls,path,payload=None,account="teacher-demo-1"):
        req=Request(cls.url+path, data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Content-Type":"application/json","X-Local-Token":cls.token,"X-Account-ID":account})
        with cls.client.open(req,timeout=10) as response: return json.load(response)

    def error(self,status,path,payload=None,account="teacher-demo-1"):
        with self.assertRaises(HTTPError) as caught: self.request(path,payload,account)
        self.assertEqual(caught.exception.code,status); caught.exception.close()

    def make_question(self):
        course=self.request("/api/courses",{"title":"Python 编程验收","major_ids":["m-soft"]})
        cid=course["id"]
        self.request(f"/api/courses/{cid}/resources",{"name":"输入输出说明"})
        question=self.request(f"/api/courses/{cid}/programming-questions",{
            "title":"整数求和", "description":"输入两个整数，输出和。", "chapter_id":course["chapters"][0]["id"],
            "examples":[{"input":"2 3\n","output":"5"}],
            "test_cases":[{"input":"2 3","output":"5"},{"input":"-8 1","output":"-7"}]})
        return course,question

    def wait_run(self,run):
        deadline=time.monotonic()+15
        while run["status"] != "completed":
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.05)
            run=self.request('/api/programming-runs/'+run['id'],account="student-demo-1")
        self.assertNotIn("error",run,run)
        return run

    def test_publish_execute_record_and_teacher_analysis(self):
        course,q=self.make_question();cid=course["id"]
        endpoint=f"/api/courses/{cid}/programming-questions/{q['id']}/runs"
        self.error(404,endpoint,{"code":"print(5)"},"student-demo-1")
        self.request(f"/api/courses/{cid}/publish",{})
        student=self.request(f"/api/courses/{cid}",account="student-demo-1")
        self.assertNotIn("test_cases",student["programming_questions"][0])
        self.assertIn("test_cases",self.request(f"/api/courses/{cid}")["programming_questions"][0])
        self.error(403,endpoint,{"code":"print(5)"})
        self.error(404,endpoint,{"code":"print(5)"},"student-demo-2")
        self.error(404,f"/api/courses/{cid}/programming-questions",{},"teacher-demo-2")
        for payload in [{"code":""},{"code":"x"*32769},{"code":"print(5)","mode":"invalid"}]:
            self.error(400,endpoint,payload,"student-demo-1")
        sample=self.wait_run(self.request(endpoint,{"code":"print(5)","mode":"sample"},"student-demo-1"))
        self.assertEqual(sample["score"],100);self.assertNotIn("submission_id",sample)
        self.assertEqual(sample["results"][0]["stdout"].strip(),"5")
        partial=self.wait_run(self.request(endpoint,{"code":"print(5)","mode":"submit"},"student-demo-1"))
        self.assertEqual(partial["score"],50)
        for case in partial["results"]:
            for field in ["input","expected","stdout","stderr"]: self.assertNotIn(field,case)
        complete=self.wait_run(self.request(endpoint,{"code":"a,b=map(int,input().split());print(a+b)","mode":"submit"},"student-demo-1"))
        self.assertEqual(complete["score"],100)
        self.error(404,'/api/programming-runs/'+complete['id'],account="student-demo-2")
        records=self.request('/api/learning-records',account="student-demo-1")
        own=[r for r in records if r['course_id']==cid]
        self.assertEqual(len(own),2);self.assertIn("code",own[0])
        self.assertNotIn(complete['submission_id'],[r['id'] for r in self.request('/api/learning-records',account="student-demo-2")])
        self.assertIn(complete['submission_id'],[r['id'] for r in self.request('/api/learning-records')])
        analysis=self.request('/api/teacher/analytics')
        row=next(c for c in analysis['courses'] if c['course_id']==cid)
        self.assertEqual(row['average_score'],75);self.assertEqual(row['weak_questions'][0]['question_id'],q['id'])
        # Existing MCQ submission still works after Python records in the same course.
        updated=self.request(f'/api/courses/{cid}/questions',{'text':'1+1?','options':['2','3'],'answer':0})
        answer=self.request(f'/api/courses/{cid}/submissions',{'answers':{updated['questions'][0]['id']:0}},'student-demo-1')
        self.assertEqual(answer['score'],100)
        reopened=LearningStore(self.folder/'learning-platform.json')
        self.assertEqual(reopened.course(cid)['programming_questions'][0]['id'],q['id'])
        self.assertIn(complete['submission_id'],[r['id'] for r in reopened.data['submissions']])

    def test_concurrency_failure_then_recovery(self):
        course,q=self.make_question();cid=course['id'];self.request(f'/api/courses/{cid}/publish',{})
        url=f"/api/courses/{cid}/programming-questions/{q['id']}/runs"
        run=self.request(url,{'code':'while True: pass','mode':'submit'},'student-demo-1')
        self.error(409,url,{'code':'print(5)'},'student-demo-1')
        result=self.wait_run(run)
        self.assertEqual(result['results'][0]['status'],'time_limit')
        self.assertEqual(result['score'],0);self.assertEqual(result['completed'],1)
        result=self.wait_run(self.request(url,{'code':'print(5)'},'student-demo-1'))
        self.assertEqual(result['score'],100)

    def test_demo_student_can_run_and_submit_all_six_samples(self):
        # Validation solutions are test fixtures only; no solution is stored in the course.
        solutions = {
            "sum": "a,b=map(int,input().split());print(a+b)",
            "length": "print(len(input()))",
            "parity": "n=int(input());print('even' if n%2==0 else 'odd')",
            "accumulate": "n=int(input());total=0\nfor i in range(1,n+1): total+=i\nprint(total)",
            "maximum": "n=int(input());numbers=list(map(int,input().split()));print(max(numbers))",
            "frequency": "n=int(input());words=input().split();target=input();print(words.count(target))",
        }
        course = self.request('/api/courses/course-demo', account='student-demo-1')
        self.assertEqual(len(course['questions']), 12)
        self.assertEqual(len(course['programming_questions']), 6)
        expected = {q['id']: q for q in demo_programming_questions()}
        for q in course['programming_questions']:
            with self.subTest(question=q['title']):
                self.assertIn(q['chapter_id'], [c['id'] for c in course['chapters']])
                self.assertNotIn('test_cases', q)
                self.assertNotIn('solution', q)
                validate_question(expected[q['id']])
                code = solutions[q['id'].removeprefix('py-demo-')]
                url = f"/api/courses/course-demo/programming-questions/{q['id']}/runs"
                sample = self.wait_run(self.request(url, {'code': code, 'mode': 'sample'}, 'student-demo-1'))
                self.assertEqual(sample['score'], 100, sample)
                result = self.wait_run(self.request(url, {'code': code, 'mode': 'submit'}, 'student-demo-1'))
                self.assertEqual(result['score'], 100, result)
                self.assertEqual(result['total'], 5)
                self.assertIn('submission_id', result)
        records = self.request('/api/learning-records', account='student-demo-1')
        self.assertEqual({r['question_id'] for r in records if r['course_id'] == 'course-demo' and r.get('type') == 'python'}, set(expected))


if __name__ == '__main__': unittest.main()
