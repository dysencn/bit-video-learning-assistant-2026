"""Submission-based learning analysis: real HTTP, mocked provider responses."""
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
from demo.app import create_app, LearningStore
from demo.analyses import AnalysisManager, AnalysisError, submission_evidence, ACTIVE
from demo.summaries import SummaryError
from test_quizzes import FIXTURE as QUIZ_FIXTURE

HTML = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>TEST ANALYSIS</title><style>body{color:#164b3e}</style></head><body><h1>TEST ANALYSIS ONLY</h1><p>基于提交记录的测试报告。</p></body></html>'
REPLY = {'choices':[{'finish_reason':'stop','message':{'content':HTML}}], 'usage':{'total_tokens':10}}


class AnalysisEvidenceTests(unittest.TestCase):
    def test_python_evidence_keeps_snapshot_without_hidden_tests(self):
        record={'type':'python','score':50,'correct_count':0,'total':1,'question_id':'p1','code':'print(5)',
                'passed_cases':1,'total_cases':2,'results':[{'case':1,'status':'wrong_answer','stdout':'hidden-output'}],
                'question_snapshot':{'title':'题目','description':'原题','examples':[{'input':'2 3','output':'5'}],
                                     'test_cases':[{'input':'secret','output':'hidden'}]}}
        result=submission_evidence(record,{'title':'课程'})
        text=json.dumps(result);self.assertNotIn('secret',text);self.assertNotIn('hidden-output',text)
        self.assertEqual(result['题目']['description'],'原题');self.assertEqual(result['学生代码'],'print(5)')
        record.pop('question_snapshot')
        with self.assertRaises(AnalysisError):submission_evidence(record,{})
        result=submission_evidence(record,{'programming_questions':[{'id':'p1','title':'现存题'}]})
        self.assertIn('旧记录',result['资料限制'])

    def test_incomplete_history_fails_and_restart_marks_interrupted(self):
        with self.assertRaises(AnalysisError):submission_evidence({'score':80,'total':1,'correct_count':0,'details':[]},{})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);manager=AnalysisManager(root)
            report_id=manager.identity('student','record')
            manager._update(report_id,status='generating',owner_id='student',record_id='record')
            manager.close();restored=AnalysisManager(root)
            self.assertEqual(restored.get('student','record')['status'],'interrupted');restored.close()


class AnalysisHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='analysis-http-');cls.root=Path(cls.temp.name)
        cls.app=create_app(cls.root/'jobs',seed_demo_history=False)
        cls.sock=socket.socket();cls.sock.bind(('127.0.0.1',0));cls.url=f'http://127.0.0.1:{cls.sock.getsockname()[1]}'
        cls.server=uvicorn.Server(uvicorn.Config(cls.app,log_level='error'))
        cls.thread=threading.Thread(target=lambda:cls.server.run(sockets=[cls.sock]),daemon=True);cls.thread.start()
        deadline=time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline:raise RuntimeError('server startup timeout')
            time.sleep(.02)
        cls.client=build_opener(HTTPCookieProcessor(CookieJar()));cls.token=''
        cls.token=cls.request('/api/config')['token']
        for name,password in [('student1','Learn123!'),('student2','Learn456!'),('teacher1','Teach123!'),('teacher2','Teach456!')]:
            cls.request('/api/auth/login',{'username':name,'password':password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(20);cls.sock.close();cls.temp.cleanup()

    @classmethod
    def request(cls,path,payload=None,account='student-demo-1',method=None):
        req=Request(cls.url+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,
                    headers={'Content-Type':'application/json','X-Local-Token':cls.token,'X-Account-ID':account})
        with cls.client.open(req,timeout=10) as response:
            body=response.read()
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type','') else (body.decode(),response.headers)

    def setUp(self):
        self.key=patch('demo.summaries.read_api_key',return_value='fixture-secret');self.key.start();self.addCleanup(self.key.stop)
        course=self.request('/api/courses',{'title':'分析测试课程','major_ids':['m-soft']},'teacher-demo-1');self.cid=course['id']
        self.request(f'/api/courses/{self.cid}/resources',{'name':'学习说明'},'teacher-demo-1')
        for i in range(2):
            course=self.request(f'/api/courses/{self.cid}/questions',{'text':f'原题 {i+1}','options':['甲','乙','丙','丁'],'answer':i,'explanation':f'原解析 {i+1}'},'teacher-demo-1')
        self.request(f'/api/courses/{self.cid}/publish',{},'teacher-demo-1')
        self.questions=course['questions']
        self.record=self.request(f'/api/courses/{self.cid}/submissions',{'answers':{self.questions[0]['id']:0,self.questions[1]['id']:3}})
        self.endpoint=f"/api/learning-records/{self.record['id']}/analysis"

    def wait(self,endpoint=None,account='student-demo-1'):
        endpoint=endpoint or self.endpoint;deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=self.request(endpoint,account=account)
            if state['status'] not in ACTIVE and self.app.state.analyses.active is None:return state
            time.sleep(.02)
        self.fail('analysis timeout')

    def error(self,status,path=None,payload=None,**kwargs):
        with self.assertRaises(HTTPError) as caught:self.request(path or self.endpoint,payload,**kwargs)
        self.assertEqual(caught.exception.code,status);caught.exception.close()

    def generate(self,endpoint=None,account='student-demo-1',reply=REPLY):
        with patch('demo.summaries.call_deepseek',return_value=reply) as call:
            self.request(endpoint or self.endpoint,{},account);state=self.wait(endpoint,account)
        return state,call

    def test_complete_snapshot_is_sent_and_report_download_cache_persist(self):
        original=deepcopy(self.record)
        self.app.state.learning.course(self.cid)['questions'][0]['text']='修改后的题目'
        state,call=self.generate();self.assertEqual(state['status'],'completed',state)
        prompt=json.loads(call.call_args.args[0])['messages'][0]['content']
        self.assertIn('原题 1',prompt);self.assertIn('原题 2',prompt);self.assertNotIn('修改后的题目',prompt)
        self.assertIn('学生所选内容',prompt);self.assertIn('正确答案内容',prompt);self.assertNotIn('fixture-secret',prompt)
        html,headers=self.request(self.endpoint+'/view');self.assertIn('TEST ANALYSIS ONLY',html)
        self.assertIn('下载分析 HTML',html);self.assertIn('sandbox',headers['Content-Security-Policy'])
        downloaded,headers=self.request(self.endpoint+'/download');self.assertEqual(downloaded,HTML);self.assertIn('attachment',headers['Content-Disposition'])
        with patch('demo.summaries.call_deepseek',side_effect=AssertionError('duplicate paid request')):
            self.assertEqual(self.request(self.endpoint,{})['status'],'completed')
        stored=next(r for r in self.app.state.learning.data['submissions'] if r['id']==self.record['id']);self.assertEqual(stored,original)
        restored=AnalysisManager(self.root/'analyses')
        self.assertEqual(restored.html('student-demo-1',self.record['id']),HTML);restored.close()
        self.assertNotIn('fixture-secret',(self.root/'analyses'/state['id']/'state.json').read_text(encoding='utf-8'))

    def test_teacher_and_student_permissions_checked_on_every_report_route(self):
        self.generate()
        for account in ['student-demo-2','teacher-demo-2']:
            for suffix,payload in [('',{}),('',None),('/view',None),('/download',None)]:
                self.error(404,self.endpoint+suffix,payload,account=account)
        # Teacher may analyze only their own course, even after course audience changes.
        self.app.state.learning.course(self.cid)['major_ids']=['m-cs']
        self.assertEqual(self.generate(account='teacher-demo-1')[0]['status'],'completed')
        self.assertIn('TEST ANALYSIS',self.request(self.endpoint+'/view',account='teacher-demo-1')[0])
        self.app.state.learning.course(self.cid)['teacher_id']='teacher-demo-2'
        self.error(404,self.endpoint+'/view',account='teacher-demo-1')
        self.assertIn('TEST ANALYSIS',self.request(self.endpoint+'/view')[0])

    def test_missing_credentials_and_no_client_replacement_of_records(self):
        with patch('demo.summaries.read_api_key',side_effect=SummaryError('未找到 DEEPSEEK_API_KEY',400)),patch('demo.summaries.call_deepseek') as call:
            self.error(400,payload={});call.assert_not_called()
        self.error(400,payload={'score':100,'details':[]})
        self.error(404,'/api/learning-records/missing/analysis',{})

    def test_invalid_html_truncation_network_error_and_retry(self):
        for body in ['not html',HTML.replace('</body>','<script>alert(1)</script></body>'),HTML.replace('</body>','<img src="x"></body>')]:
            reply={'choices':[{'finish_reason':'stop','message':{'content':body}}]}
            self.assertEqual(self.generate(reply=reply)[0]['status'],'failed');self.error(404,self.endpoint+'/view')
        reply={'choices':[{'finish_reason':'length','message':{'content':HTML}}]}
        self.assertIn('截断',self.generate(reply=reply)[0]['message'])
        with patch('demo.summaries.call_deepseek',side_effect=SummaryError('网络失败')):
            self.request(self.endpoint,{});self.assertIn('网络',self.wait()['message'])
        self.assertEqual(self.generate()[0]['status'],'completed')

    def test_duplicate_concurrent_call_only_sends_once_and_disk_failure_recovers(self):
        with patch.object(self.app.state.analyses,'_update',side_effect=[OSError('disk full'),{}]):
            self.error(507,payload={})
        self.assertIsNone(self.app.state.analyses.active)
        entered,release=threading.Event(),threading.Event()
        def reply(*args):entered.set();release.wait(5);return REPLY
        with patch('demo.summaries.call_deepseek',side_effect=reply) as call:
            first=self.request(self.endpoint,{});self.assertTrue(entered.wait(3))
            try:
                self.assertEqual(self.request(self.endpoint,{})['id'],first['id'])
                self.error(409,payload={},account='teacher-demo-1')
            finally:release.set()
            self.assertEqual(self.wait()['status'],'completed');call.assert_called_once()

    def test_ai_quiz_attempt_saved_graded_snapshot_and_analyzed_after_job_delete(self):
        jobs=self.app.state.jobs;job=jobs.create('ai.mp4','balanced',owner_id='student-demo-1');jid=job['id']
        jobs.update(jid,status='completed',result={'pages':[]});jobs.active=None
        quiz={**deepcopy(QUIZ_FIXTURE),'id':'a'*32,'source_mode':'slides','source_summary':'b'*64}
        path=jobs.root/jid/'quiz.json';path.write_text(json.dumps(quiz),encoding='utf-8')
        endpoint=f'/api/jobs/{jid}/quiz/submissions'
        payload={'quiz_id':quiz['id'],'answers':{q['id']:q['answer'] if i<5 else (q['answer']+1)%4 for i,q in enumerate(quiz['questions'])},'score':100}
        self.error(404,endpoint,payload,account='student-demo-2')
        self.error(409,endpoint,{**payload,'quiz_id':'old-version'})
        self.error(400,endpoint,{**payload,'answers':{}})
        record=self.request(endpoint,payload);self.assertEqual(record['score'],50)
        self.assertEqual(self.request(endpoint,payload)['id'],record['id'])
        self.assertEqual(len(record['details']),10)
        self.assertIn(record['id'],[r['id'] for r in self.request('/api/learning-records')])
        self.assertNotIn(record['id'],[r['id'] for r in self.request('/api/learning-records',account='teacher-demo-1')])
        self.assertNotIn(record['id'],[r['id'] for r in self.app.state.learning.data['submissions']])
        self.request('/api/jobs/'+jid,method='DELETE');self.assertFalse(path.exists())
        report=f"/api/learning-records/{record['id']}/analysis"
        state,call=self.generate(report);self.assertEqual(state['status'],'completed')
        prompt=json.loads(call.call_args.args[0])['messages'][0]['content'];self.assertIn('TEST ONLY 第 10 题',prompt)
        self.error(404,report,{},account='teacher-demo-1')
        reloaded=LearningStore(self.root/'learning-platform.json')
        self.assertEqual(next(r for r in reloaded.data['ai_quiz_submissions'] if r['id']==record['id']),record)


if __name__=='__main__':unittest.main()
