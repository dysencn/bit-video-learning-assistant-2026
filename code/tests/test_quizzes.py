"""AI responses below are isolated contract fixtures, never production quiz fallbacks."""
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

from PIL import Image
import uvicorn

from demo.app import create_app
from demo.jobs import JobManager
from demo.summaries import SummaryManager, SummaryError
from demo.quizzes import QuizManager, QuizError, parse_quiz, validate_quiz, ACTIVE

FIXTURE = {"title":"TEST ONLY 课程练习", "questions":[
    {"id":f"ai-q{i}", "chapter_id":"ai-practice", "knowledge_point_id":None,
     "text":f"TEST ONLY 第 {i} 题", "options":["选项一", "选项二", "选项三", "选项四"],
     "answer":i % 4, "explanation":"TEST ONLY 解析：参见课件第1张。"} for i in range(1,11)]}
REPLY = {"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(FIXTURE)}}], "usage":{"total_tokens":10}}


class QuizValidationTests(unittest.TestCase):
    def test_valid_schema_and_invalid_count_fields_answers_options(self):
        self.assertEqual(parse_quiz('```json\n'+json.dumps(FIXTURE)+'\n```'), FIXTURE)
        for transform in [lambda d:d["questions"].pop(), lambda d:d["questions"].append(deepcopy(d["questions"][0])),
                          lambda d:d["questions"][0].update(answer=True), lambda d:d["questions"][0].update(answer="A"),
                          lambda d:d["questions"][0].update(answer=4), lambda d:d["questions"][0].update(answer=[0]),
                          lambda d:d["questions"][0].update(options=["1","1","2","3"]),
                          lambda d:d["questions"][0].update(options=["1","2"]),
                          lambda d:d["questions"][0].update(explanation=""),
                          lambda d:d["questions"][0].update(id=d["questions"][1]["id"]),
                          lambda d:d["questions"][0].update(text=d["questions"][1]["text"]),
                          lambda d:d["questions"][0].update(extra="unexpected"),
                          lambda d:d["questions"][0].update(chapter_id="unknown")]:
            data = deepcopy(FIXTURE); transform(data)
            with self.assertRaises(QuizError): validate_quiz(data)

    def test_malformed_truncated_empty_and_duplicate_json_are_rejected(self):
        for content in [None, " ", "{} trailing", "{", "[]", '{"title":"a","title":"b","questions":[]}',
                        '{"title":NaN,"questions":[]}', "x" * (256*1024+1)]:
            with self.subTest(content=str(content)[:30]), self.assertRaises(QuizError): parse_quiz(content)

    def test_restart_marks_running_quiz_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            jobs = JobManager(Path(folder)); job = jobs.create('test.mp4','balanced')
            jobs.update(job['id'],status='completed',quiz={'status':'generating','generation_id':'test'})
            jobs.close()
            restored = JobManager(Path(folder)); summaries = SummaryManager(restored); quizzes = QuizManager(restored,summaries)
            self.assertEqual(restored.get(job['id'])['quiz']['status'],'interrupted')
            quizzes.close(); summaries.close(); restored.close()


class QuizHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='quiz-http-')
        cls.root = Path(cls.temp.name); cls.app = create_app(cls.root/'jobs',seed_demo_history=False)
        cls.sock=socket.socket();cls.sock.bind(('127.0.0.1',0));cls.url=f'http://127.0.0.1:{cls.sock.getsockname()[1]}'
        cls.server=uvicorn.Server(uvicorn.Config(cls.app,log_level='error'))
        cls.thread=threading.Thread(target=lambda:cls.server.run(sockets=[cls.sock]),daemon=True);cls.thread.start()
        deadline=time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline: raise RuntimeError('server failed to start')
            time.sleep(.02)
        cls.client=build_opener(HTTPCookieProcessor(CookieJar()));cls.token=''
        cls.token=cls.request('/api/config')['token']
        for name,password in [('student1','Learn123!'),('student2','Learn456!')]:
            cls.request('/api/auth/login',{'username':name,'password':password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(20);cls.sock.close();cls.temp.cleanup()

    @classmethod
    def request(cls,path,payload=None,account='student-demo-1',method=None):
        req=Request(cls.url+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,
                    headers={'Content-Type':'application/json','X-Local-Token':cls.token,'X-Account-ID':account})
        with cls.client.open(req,timeout=10) as response: return json.load(response)

    def setUp(self):
        self.key=patch('demo.summaries.read_api_key',return_value='fixture-only-key');self.key.start();self.addCleanup(self.key.stop)
        jobs=self.app.state.jobs;job=jobs.create('fixture.mp4','balanced',owner_id='student-demo-1')
        self.job_id=job['id'];self.prefix='/api/jobs/'+self.job_id;self.folder=jobs.root/self.job_id
        (self.folder/'slides').mkdir();Image.new('RGB',(100,60),'white').save(self.folder/'slides/slide-0001.jpg')
        (self.folder/'transcript.txt').write_text('TRANSCRIPT_ONLY_MARKER 转写材料。',encoding='utf-8-sig')
        jobs.update(self.job_id,status='completed',result={'audio':'audio.mp3','pages':[{'number':1,'timestamp':1,'filename':'slide-0001.jpg'}]},
                    transcription={'status':'completed','available':True})
        jobs.active=None;self.set_summary('slides')

    def set_summary(self,saved_mode,**overrides):
        (self.folder/'summary.html').write_text('<!DOCTYPE html><html>'+saved_mode+'</html>',encoding='utf-8')
        self.app.state.jobs.update(self.job_id,summary={'status':'completed','available':True,'mode':saved_mode,'available_mode':saved_mode,**overrides})

    def wait(self):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=self.request(self.prefix).get('quiz',{})
            if state.get('status') not in ACTIVE and self.app.state.quizzes.active is None:return state
            time.sleep(.02)
        self.fail('quiz did not finish')

    def error(self,status,suffix,payload=None,**kwargs):
        with self.assertRaises(HTTPError) as caught:self.request(self.prefix+suffix,payload,**kwargs)
        self.assertEqual(caught.exception.code,status);caught.exception.close()

    def generate(self,response=REPLY):
        with patch('demo.summaries.call_deepseek',return_value=response) as call:
            self.request(self.prefix+'/quiz',{});state=self.wait()
        return state,call

    def test_three_modes_send_only_saved_summary_sources_and_preserve_schema(self):
        prompts=[]
        for mode in ('slides','transcript','combined'):
            self.set_summary(mode)
            state,call=self.generate();self.assertEqual(state['status'],'completed',state)
            body=json.loads(call.call_args.args[0]);content=body['messages'][0]['content']
            self.assertEqual(body['response_format'],{'type':'json_object'})
            self.assertEqual(sum(item['type']=='image_url' for item in content),0 if mode=='transcript' else 1)
            self.assertEqual('TRANSCRIPT_ONLY_MARKER' in json.dumps(content),mode!='slides')
            prompts.append(content[0]['text'])
            quiz=self.request(self.prefix+'/quiz');self.assertEqual(quiz['source_mode'],mode)
            self.assertEqual(quiz['questions'],FIXTURE['questions'])
            self.assertNotIn('fixture-only-key',(self.folder/'job.json').read_text(encoding='utf-8'))
        self.assertEqual(len(set(prompts)),3)

    def test_failed_summary_regeneration_uses_previous_successful_mode(self):
        self.set_summary('slides',status='failed',mode='combined')
        (self.folder/'transcript.txt').write_bytes(b'\xff'*100)
        state,call=self.generate();self.assertEqual(state['mode'],'slides');self.assertEqual(state['status'],'completed')
        self.assertNotIn('TRANSCRIPT_ONLY_MARKER',str(call.call_args))
        self.error(400,'/quiz',{'mode':'transcript'})

    def test_text_only_does_not_require_or_send_images(self):
        self.set_summary('transcript');(self.folder/'slides/slide-0001.jpg').unlink()
        state,call=self.generate();self.assertEqual(state['status'],'completed')
        self.assertNotIn('image_url',call.call_args.args[0].decode())

    def test_prerequisites_credentials_and_private_access(self):
        self.error(404,'/quiz',{},account='student-demo-2');self.error(404,'/quiz',account='student-demo-2')
        (self.folder/'summary.html').unlink();self.error(409,'/quiz',{})
        self.set_summary('slides',status='generating');self.error(409,'/quiz',{})
        self.set_summary('slides')
        with patch('demo.summaries.read_api_key',side_effect=SummaryError('missing key',400)), patch('demo.summaries.call_deepseek') as call:
            self.error(400,'/quiz',{});call.assert_not_called()
        self.generate()
        # Sharing PDF resources must never share the task's generated practice JSON.
        self.app.state.learning.data['courses'][0]['resources'].append({'id':'shared','job_id':self.job_id})
        self.error(404,'/quiz',account='student-demo-2')

    def test_duplicate_requests_cache_and_deletion_are_safe(self):
        entered,release=threading.Event(),threading.Event()
        def delayed(*args):entered.set();release.wait(5);return REPLY
        with patch('demo.summaries.call_deepseek',side_effect=delayed) as call:
            first=self.request(self.prefix+'/quiz',{});self.assertTrue(entered.wait(3))
            try:
                second=self.request(self.prefix+'/quiz',{});self.assertEqual(first['generation_id'],second['generation_id'])
                self.error(409,'',method='DELETE');self.error(409,'/summary',method='DELETE')
                self.error(409,'/summary',{'regenerate':True,'mode':'slides'})
            finally:release.set()
            self.assertEqual(self.wait()['status'],'completed');call.assert_called_once()
        with patch('demo.summaries.call_deepseek',side_effect=AssertionError('paid duplicate')):
            self.assertEqual(self.request(self.prefix+'/quiz',{})['generation_id'],first['generation_id'])
        self.request(self.prefix,method='DELETE');self.assertFalse(self.folder.exists())

    def test_invalid_response_and_network_failure_keep_previous_valid_quiz(self):
        self.generate();saved=(self.folder/'quiz.json').read_bytes();self.set_summary('combined')
        for response in [{'choices':[{'finish_reason':'length','message':{'content':'{'}}]},
                         {'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]},
                         {'choices':[{'finish_reason':'stop','message':{'content':'{"title":"empty","questions":[]}'}}]},{}]:
            state,_=self.generate(response);self.assertEqual(state['status'],'failed',state)
            self.assertEqual((self.folder/'quiz.json').read_bytes(),saved)
        with patch('demo.summaries.call_deepseek',side_effect=SummaryError('DeepSeek 网络连接失败')):
            self.request(self.prefix+'/quiz',{});self.assertIn('网络',self.wait()['message'])
        self.assertEqual(self.generate()[0]['status'],'completed')
        self.assertEqual(self.request(self.prefix+'/quiz')['source_mode'],'combined')

    def test_first_invalid_response_is_not_exposed_and_disk_failure_releases_worker(self):
        bad=deepcopy(REPLY);bad['choices'][0]['message']['content']='{}'
        self.assertEqual(self.generate(bad)[0]['status'],'failed');self.error(404,'/quiz')
        with patch.object(self.app.state.jobs,'_save',side_effect=OSError('disk full')):
            self.error(507,'/quiz',{})
        self.assertIsNone(self.app.state.quizzes.active)
        self.assertEqual(self.generate()[0]['status'],'completed')


if __name__=='__main__':unittest.main()
