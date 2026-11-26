"""Python advice uses real judging and HTTP; AI responses are explicit fixtures."""
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

import uvicorn
from demo.app import create_app
from demo.programming import advice_kind
from demo.analyses import ACTIVE, AnalysisManager
from demo.summaries import SummaryError

HTML = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>TEST CODE ADVICE</title></head><body><h1>TEST CODE ADVICE ONLY</h1></body></html>'
REPLY = {"choices":[{"finish_reason":"stop", "message":{"content":HTML}}]}


class AdviceEligibilityTests(unittest.TestCase):
    def test_run_states_and_partial_success(self):
        run = {"status":"completed", "completed":2, "total":2, "results":[{"status":"accepted"}]*2}
        self.assertEqual(advice_kind(run), "complexity")
        for status in ["runtime_error", "time_limit", "wrong_answer", "memory_limit", "output_limit"]:
            self.assertEqual(advice_kind({**run, "results":[{"status":"accepted"},{"status":status}]}), "debug")
        for change in [{"status":"running"},{"error":"save failed"},{"results":[]},
                       {"results":[{"status":"system_error"}]},{"completed":1}]:
            self.assertIsNone(advice_kind({**run, **change}))


class ProgrammingAdviceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="code-ai-http-");cls.root=Path(cls.temp.name)
        cls.app=create_app(cls.root/"jobs",seed_demo_history=False)
        cls.sock=socket.socket();cls.sock.bind(("127.0.0.1",0));cls.url=f"http://127.0.0.1:{cls.sock.getsockname()[1]}"
        cls.server=uvicorn.Server(uvicorn.Config(cls.app,log_level="error"))
        cls.thread=threading.Thread(target=lambda:cls.server.run(sockets=[cls.sock]),daemon=True);cls.thread.start()
        deadline=time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline:raise RuntimeError("server startup timeout")
            time.sleep(.02)
        cls.client=build_opener(HTTPCookieProcessor(CookieJar()));cls.token=""
        cls.token=cls.request("/api/config")["token"]
        for name,password in [("student1","Learn123!"),("student2","Learn456!"),("teacher1","Teach123!")]:
            cls.request("/api/auth/login",{"username":name,"password":password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(25);cls.sock.close();cls.temp.cleanup()

    @classmethod
    def request(cls,path,payload=None,account="student-demo-1"):
        req=Request(cls.url+path,data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Content-Type":"application/json","X-Local-Token":cls.token,"X-Account-ID":account})
        with cls.client.open(req,timeout=10) as response:
            data=response.read()
            return json.loads(data) if "application/json" in response.headers.get("Content-Type","") else (data.decode(),response.headers)

    def setUp(self):
        key=patch("demo.summaries.read_api_key",return_value="fixture-code-key");key.start();self.addCleanup(key.stop)

    def run_code(self,code="print(0)",mode="sample",question="py-demo-sum"):
        run=self.request(f"/api/courses/course-demo/programming-questions/{question}/runs",{"code":code,"mode":mode})
        endpoint=f"/api/programming-runs/{run['id']}"
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            run=self.request(endpoint)
            if run["status"]=="completed":return endpoint,run
            time.sleep(.03)
        self.fail("judge timeout")

    def wait(self,endpoint):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=self.request(endpoint)
            if state["status"] not in ACTIVE and self.app.state.code_analyses.active is None:return state
            time.sleep(.02)
        self.fail("advice timeout")

    def error(self,status,path,payload=None,account="student-demo-1"):
        with self.assertRaises(HTTPError) as caught:self.request(path,payload,account)
        self.assertEqual(caught.exception.code,status);caught.exception.close()

    def generate(self,endpoint,reply=REPLY):
        with patch("demo.summaries.call_deepseek",return_value=reply) as call:
            self.request(endpoint,{});state=self.wait(endpoint)
        return state,json.loads(call.call_args.args[0])["messages"][0]["content"]

    def test_sample_wrong_answer_snapshot_and_cached_html_download(self):
        code="a,b=map(int,input().split());print(a-b)"
        endpoint,run=self.run_code(code);self.assertEqual(run["advice_kind"],"debug")
        self.assertNotIn("_code",run);self.assertNotIn("_question",run)
        question=self.app.state.learning.course("course-demo")["programming_questions"][0]
        original=deepcopy(question)
        try:
            question["description"]="EDIT AFTER RUN";question["examples"][0]["input"]="EDIT INPUT"
            state,prompt=self.generate(endpoint+"/advice")
        finally:question.update(original)
        self.assertEqual(state["status"],"completed",state)
        self.assertIn("任务：AI Debug",prompt);self.assertIn(code,prompt)
        self.assertNotIn("EDIT AFTER RUN",prompt);self.assertNotIn("EDIT INPUT",prompt)
        evidence=json.loads(prompt.split("以下为本次提交数据（索引从0开始）：\n")[1])
        self.assertIn("stdout",evidence["判题反馈"][0]);self.assertIn("expected",evidence["判题反馈"][0])
        self.assertNotIn("reference_code",evidence["题目"]);self.assertNotIn("test_cases",evidence["题目"])
        html,headers=self.request(endpoint+"/advice/view");self.assertIn("TEST CODE ADVICE ONLY",html)
        self.assertIn("sandbox",headers["Content-Security-Policy"])
        download,headers=self.request(endpoint+"/advice/download");self.assertEqual(download,HTML)
        self.assertIn("attachment",headers["Content-Disposition"])
        with patch("demo.summaries.call_deepseek",side_effect=AssertionError("duplicate request")):
            self.assertEqual(self.request(endpoint+"/advice",{})["status"],"completed")
        self.assertNotIn("fixture-code-key",(self.root/"programming-analyses"/state["id"]/"state.json").read_text(encoding="utf-8"))
        manager=AnalysisManager(self.root/"programming-analyses")
        self.assertEqual(manager.html("student-demo-1",run["id"]),HTML);manager.close()

    def test_runtime_and_timeout_debug_include_real_public_feedback(self):
        for code,status in [("print(1/0)","runtime_error"),("while True: pass","time_limit")]:
            endpoint,run=self.run_code(code)
            self.assertEqual(run["results"][0]["status"],status);self.assertEqual(run["advice_kind"],"debug")
            state,prompt=self.generate(endpoint+"/advice");self.assertEqual(state["status"],"completed")
            self.assertIn(status,prompt)
            if status=="runtime_error":self.assertIn("ZeroDivisionError",prompt)

    def test_accepted_sample_and_submit_get_complexity_prompt(self):
        code="n=int(input());print(n*(n+1)//2)"
        for mode in ["sample","submit"]:
            endpoint,run=self.run_code(code,mode,"py-demo-accumulate")
            self.assertEqual(run["advice_kind"],"complexity")
            state,prompt=self.generate(endpoint+"/advice");self.assertEqual(state["status"],"completed")
            self.assertIn("任务：智能复杂度分析",prompt);self.assertIn("额外空间",prompt)
            self.assertIn("Python大整数",prompt);self.assertNotIn("任务：AI Debug",prompt)
            self.assertEqual(self.request(endpoint+"/advice")["kind"],"complexity")
            self.assertEqual("submission_id" in run,mode=="submit")

    def test_formal_debug_never_sends_hidden_input_output_or_stderr(self):
        endpoint,run=self.run_code("print(input())","submit")
        self.assertEqual(run["advice_kind"],"debug")
        state,prompt=self.generate(endpoint+"/advice")
        data=json.loads(prompt.split("以下为本次提交数据（索引从0开始）：\n")[1])
        for result in data["判题反馈"]:
            self.assertFalse(set(result)&{"input","stdout","expected","stderr"})
        self.assertNotIn("test_cases",json.dumps(data));self.assertEqual(state["status"],"completed")

    def test_permissions_client_tampering_and_incomplete_runs(self):
        endpoint,run=self.run_code()
        for account,status in [("student-demo-2",404),("teacher-demo-1",403)]:
            for suffix,payload in [("",{}),("",None),("/view",None),("/download",None)]:
                self.error(status,endpoint+"/advice"+suffix,payload,account)
        self.error(400,endpoint+"/advice",{"code":"fake","kind":"complexity"})
        self.error(404,"/api/programming-runs/missing/advice",{})
        stored=self.app.state.programming.runs[run["id"]]
        for change in [{"status":"running"},{"error":"save failed"},{"results":[{"status":"system_error"}]}]:
            original=deepcopy(stored)
            try:
                stored.update(change);self.error(409,endpoint+"/advice",{})
            finally:stored.clear();stored.update(original)

    def test_missing_key_failure_retry_and_concurrent_deduplication(self):
        endpoint,_=self.run_code();endpoint+="/advice"
        with patch("demo.summaries.read_api_key",side_effect=SummaryError("未找到密钥",400)):
            self.error(400,endpoint,{})
        for reply in [{"choices":[{"finish_reason":"length","message":{"content":HTML}}]},
                      {"choices":[{"finish_reason":"stop","message":{"content":"not html"}}]}]:
            self.assertEqual(self.generate(endpoint,reply)[0]["status"],"failed");self.error(404,endpoint+"/view")
        with patch("demo.summaries.call_deepseek",side_effect=SummaryError("网络失败")):
            self.request(endpoint,{});self.assertIn("网络",self.wait(endpoint)["message"])
        entered,release=threading.Event(),threading.Event()
        def respond(*args):entered.set();release.wait(4);return REPLY
        with patch("demo.summaries.call_deepseek",side_effect=respond) as call:
            first=self.request(endpoint,{});self.assertTrue(entered.wait(3))
            try:self.assertEqual(self.request(endpoint,{})["id"],first["id"])
            finally:release.set()
            self.assertEqual(self.wait(endpoint)["status"],"completed");call.assert_called_once()


if __name__=="__main__":unittest.main()
