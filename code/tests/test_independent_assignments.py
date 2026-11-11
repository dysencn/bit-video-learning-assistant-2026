"""Independent papers, disclosure gates, record sources and legacy evidence."""
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
from urllib.request import build_opener, HTTPCookieProcessor, Request
import uvicorn
from demo.app import create_app, LearningStore

TEACHER='teacher-demo-1'
STUDENT='student-demo-1'
OTHER='student-demo-4'

class IndependentAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='independent-papers-')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.app=create_app(self.folder/'jobs',seed_demo_history=False)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.url=f'http://127.0.0.1:{port}'
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error'))
        self.thread=threading.Thread(target=self.server.run,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        deadline=time.monotonic()+10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic()>deadline:self.fail('server start failed')
            time.sleep(.02)
        with self.client.open(self.url+'/api/config') as response:self.token=json.load(response)['token']
        for username,password in [('teacher1','Teach123!'),('teacher2','Teach456!'),('student1','Learn123!'),('student4','Learn123!'),('student2','Learn456!')]:
            self.req('/api/auth/login',{'username':username,'password':password})

    def stop(self):
        self.server.should_exit=True;self.thread.join(10)

    def req(self,path,data=None,account=TEACHER,method=None):
        request=Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,method=method,
            headers={'X-Local-Token':self.token,'X-Account-ID':account,'Content-Type':'application/json'})
        with self.client.open(request,timeout=5) as response:return json.load(response)

    def error(self,status,path,data=None,account=TEACHER,method=None):
        with self.assertRaises(HTTPError) as caught:self.req(path,data,account,method)
        self.assertEqual(caught.exception.code,status,caught.exception.read().decode())
        caught.exception.close()

    def paper(self,attempts=3):
        return self.req('/api/courses/course-demo/assignments',{'title':'独立作业','status':'published',
            'due_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'max_attempts':attempts,
            'questions':[{'text':'作业私有题：下面哪个数等于九？','options':['6','9','12','18'],'answer':1,
            'explanation':'作业私有解析：三的平方是九。','chapter_id':'ch-1'}]})

    def submit(self,paper,selected=0,account=STUDENT):
        attempt=self.req(f"/api/assignments/{paper['id']}/start",{},account)
        return self.req('/api/courses/course-demo/submissions',{'assignment_id':paper['id'],'attempt_id':attempt['id'],
            'answers':{paper['questions'][0]['id']:selected}},account)

    def test_bank_solutions_public_and_paper_questions_private(self):
        bank=self.req('/api/courses/course-demo',account=STUDENT)
        self.assertTrue(all('answer' in q and 'explanation' in q for q in bank['questions']))
        self.assertTrue(all('ai_source' not in q for q in bank['questions']))
        paper=self.paper()
        endpoint='/api/courses/course-demo/assignments'
        self.error(400,endpoint,{'title':'不能选题库','question_ids':[bank['questions'][0]['id']]})
        self.error(400,endpoint,{'title':'没有创建题目','chapter_id':'ch-1'})
        self.error(400,endpoint,{'title':'不能引用题库 ID','questions':[{**bank['questions'][0]}]})
        self.assertEqual(self.req('/api/courses/course-demo',account=STUDENT),bank)
        self.assertNotIn(paper['questions'][0]['id'],{q['id'] for q in bank['questions']})
        for path in ('/api/assignments',f"/api/assignments/{paper['id']}"):
            response=self.req(path,account=STUDENT)
            self.assertNotIn('作业私有解析',json.dumps(response,ensure_ascii=False))
        self.error(404,f"/api/assignments/{paper['id']}",account='student-demo-2')
        self.error(404,endpoint,account='teacher-demo-2')

    def test_only_scores_until_last_attempt_across_all_student_routes(self):
        paper=self.paper(3);endpoint=f"/api/assignments/{paper['id']}"
        for number in (1,2):
            result=self.submit(paper)
            self.assertEqual(result['score'],0);self.assertEqual(result['details'],[])
            self.assertIsNone(result['correct_count']);self.assertTrue(result['answers_locked'])
            detail=self.req(endpoint,account=STUDENT)['assignment']
            self.assertEqual(detail['attempts'],number)
            self.assertNotIn('answer',detail['questions'][0]);self.assertNotIn('explanation',detail['questions'][0])
            self.assertFalse(self.req('/api/wrong-questions',account=STUDENT))
            wrong = next(w for w in self.app.state.learning.data['wrong_questions'] if w.get('assignment_id') == paper['id'])
            self.error(409,f"/api/wrong-questions/{wrong['id']}/review",{'selected':1},STUDENT)
            self.error(409,f"/api/wrong-questions/{wrong['id']}/mastered",{},STUDENT)
            for suffix,method in (('',None),('', 'POST'),('/view',None),('/download',None)):
                self.error(409,f"/api/learning-records/{result['id']}/analysis{suffix}",{} if method=='POST' else None,STUDENT,method)
            rows=self.req('/api/learning-records?source=assignment',account=STUDENT)
            self.assertTrue(all(r['details']==[] and r['can_analyze'] is False for r in rows))
            self.req(f"/api/courses/course-demo/assignments/{paper['id']}/release",{})
            self.assertTrue(self.req(endpoint,account=STUDENT)['assignment']['answers_locked'])
        last=self.submit(paper)
        self.assertFalse(last['answers_locked']);self.assertEqual(last['details'][0]['answer'],1)
        self.assertIn('作业私有解析',last['details'][0]['explanation'])
        self.assertEqual(self.req(endpoint,account=STUDENT)['assignment']['questions'][0]['answer'],1)
        self.assertTrue(self.req('/api/wrong-questions',account=STUDENT))
        self.assertTrue(all(r['details'] for r in self.req('/api/learning-records?source=assignment',account=STUDENT)))
        self.assertEqual(self.req(f"/api/learning-records/{last['id']}/analysis",account=STUDENT)['status'],'idle')
        self.assertTrue(self.req(endpoint,account=OTHER)['assignment']['answers_locked'])
        self.error(409,endpoint+'/start',{},STUDENT)

    def test_sources_separate_and_papers_cannot_change_after_submission(self):
        bank=self.req('/api/courses/course-demo')
        chapter=[q for q in bank['questions'] if q['chapter_id']=='ch-1']
        practice=self.req('/api/courses/course-demo/submissions',{'chapter_id':'ch-1','answers':{q['id']:q['answer'] for q in chapter}},STUDENT)
        self.assertFalse(practice.get('answers_locked',False));self.assertTrue(practice['details'])
        paper=self.paper(2);result=self.submit(paper)
        self.assertEqual([r['id'] for r in self.req('/api/learning-records?source=self_practice',account=STUDENT)],[practice['id']])
        self.assertEqual([r['id'] for r in self.req('/api/learning-records?source=assignment',account=STUDENT)],[result['id']])
        endpoint=f"/api/courses/course-demo/assignments/{paper['id']}"
        self.error(409,endpoint,{'questions':[{**paper['questions'][0],'answer':2}]},method='PATCH')
        self.error(409,endpoint,{'max_attempts':1},method='PATCH')
        changed=self.req(endpoint,{'title':'仅修改标题'},method='PATCH')
        self.assertEqual(changed['questions'],paper['questions'])
        self.assertEqual(changed['title'],'仅修改标题')
        self.assertTrue(self.req(f"/api/learning-records/{result['id']}/analysis")['record'])

    def test_programming_practice_reference_solution_is_public_without_judge_cases(self):
        course=self.req('/api/courses/course-demo',account=STUDENT)
        self.assertTrue(all(q.get('reference_code') and q.get('explanation') for q in course['programming_questions']))
        self.assertTrue(all('test_cases' not in q for q in course['programming_questions']))
        created=self.req('/api/courses/course-demo/programming-questions',{
            'title':'参考代码示例','description':'输出 1','chapter_id':'ch-1',
            'reference_code':'print(1)','explanation':'print() 输出整数。',
            'examples':[{'input':'','output':'1\n'}],'test_cases':[{'input':'','output':'1\n'}]})
        self.req(f"/api/courses/course-demo/programming-questions/{created['id']}",
                 {'explanation':'更新后的解析'},method='PATCH')
        student=self.req('/api/courses/course-demo',account=STUDENT)
        question=next(q for q in student['programming_questions'] if q['id']==created['id'])
        self.assertEqual(question['reference_code'],'print(1)')
        self.assertEqual(question['explanation'],'更新后的解析')
        self.assertNotIn('test_cases',question)

    def test_wrong_question_export_respects_answer_lock_and_student_scope(self):
        def exported(account=STUDENT):
            request=Request(self.url+'/api/wrong-questions/export.csv?course_id=course-demo',
                headers={'X-Local-Token':self.token,'X-Account-ID':account})
            with self.client.open(request,timeout=5) as response:
                content=response.read()
                self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
                self.assertIn('attachment',response.headers['Content-Disposition'])
                return content.decode('utf-8-sig')
        paper=self.paper(2)
        self.submit(paper)
        self.assertNotIn('作业私有题',exported())
        self.assertNotIn('作业私有解析',exported())
        self.submit(paper)
        visible=exported()
        self.assertIn('作业私有题',visible)
        self.assertIn('作业私有解析',visible)
        self.assertIn('B. 9',visible)
        self.assertNotIn('作业私有题',exported(OTHER))
        self.error(403,'/api/wrong-questions/export.csv',account=TEACHER)

class IndependentAssignmentMigrationTests(unittest.TestCase):
    def test_snapshot_preserves_legacy_submission_evidence_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'learning-platform.json';store=LearningStore(path)
            q=store.course('course-demo')['questions'][0]
            assignment={'id':'old-paper','course_id':'course-demo','teacher_id':TEACHER,'title':'旧作业','chapter_id':'ch-1',
                        'question_ids':[q['id']],'max_attempts':3,'status':'published','grade_release':'manual'}
            record={'id':'old-grade','student_id':STUDENT,'course_id':'course-demo','assignment_id':'old-paper','score':100,
                    'submitted_at':datetime.now(timezone.utc).isoformat(),'details':[{'question_id':q['id'],'text':'原始题干',
                    'options':['原选项A','原选项B'],'answer':1,'selected':1,'correct':True,'explanation':'原解析','chapter_id':'ch-1'}]}
            store.data['assignments']=[assignment];store.data['submissions']=[record]
            store.data.pop('independent_assignments_version',None);store.save();before=deepcopy(store.data)
            migrated=LearningStore(path)
            private=migrated.data['assignments'][0]['questions'][0]
            self.assertEqual(private['text'],'原始题干');self.assertNotEqual(private['id'],q['id'])
            result=migrated.data['submissions'][0]
            self.assertEqual(result['score'],100);self.assertEqual(result['id'],'old-grade')
            self.assertEqual({k:v for k,v in result['details'][0].items() if k!='question_id'},
                             {k:v for k,v in record['details'][0].items() if k!='question_id'})
            self.assertEqual(json.loads(path.with_name('learning-platform.before-independent-assignments.json').read_text()),before)
            self.assertEqual(LearningStore(path).data,migrated.data)
