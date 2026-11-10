"""Verify exported slide content, legacy jobs, HTTP permissions and recoverable failures."""
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import io
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

from PIL import Image
from pptx import Presentation
import uvicorn

from demo.app import create_app
from demo.pptx_export import create_pptx


class PptxFileTests(unittest.TestCase):
    def test_image_order_original_bytes_and_portrait_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            images = [root / 'portrait.jpg', root / 'landscape.jpg']
            Image.new('RGB', (360, 640), 'red').save(images[0])
            Image.new('RGB', (640, 360), 'blue').save(images[1])
            path = root / 'slides.pptx'
            create_pptx(images, path)
            deck = Presentation(path)
            self.assertAlmostEqual(deck.slide_width / deck.slide_height, 360/640, places=6)
            self.assertEqual(len(deck.slides), 2)
            for slide, source in zip(deck.slides, images):
                self.assertEqual(len(slide.shapes), 1)
                picture = slide.shapes[0]
                self.assertEqual(picture.image.blob, source.read_bytes())
                self.assertGreaterEqual(picture.left, 0)
                self.assertGreaterEqual(picture.top, 0)
                self.assertLessEqual(picture.left + picture.width, deck.slide_width)
                self.assertLessEqual(picture.top + picture.height, deck.slide_height)
                with Image.open(source) as original:
                    self.assertAlmostEqual(picture.width/picture.height, original.width/original.height, places=6)

    def test_failed_save_removes_partial_file_and_keeps_previous_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'image.jpg'
            Image.new('RGB', (40, 30), 'white').save(image)
            target = root / 'slides.pptx'
            create_pptx([image], target)
            before = target.read_bytes()
            def fail_save(_presentation, filename):
                Path(filename).write_bytes(b'partial')
                raise OSError('disk full')
            with patch('pptx.presentation.Presentation.save', fail_save), self.assertRaises(OSError):
                create_pptx([image], target)
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(target.with_suffix('.pptx.tmp').exists())
            with self.assertRaises(ValueError):
                create_pptx([], root / 'empty.pptx')


class PptxHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='pptx-http-')
        cls.root = Path(cls.temp.name)
        cls.app = create_app(cls.root / 'jobs', seed_demo_history=False)
        cls.sock = socket.socket(); cls.sock.bind(('127.0.0.1', 0))
        cls.url = f'http://127.0.0.1:{cls.sock.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, log_level='error'))
        cls.thread = threading.Thread(target=lambda: cls.server.run(sockets=[cls.sock]), daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started:
            if time.monotonic() > deadline: raise RuntimeError('server startup timed out')
            time.sleep(.02)
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        cls.token = ''
        cls.token = cls.request('/api/config')['token']
        for name, password in [('student1', 'Learn123!'), ('student2', 'Learn456!')]:
            cls.request('/api/auth/login', {'username': name, 'password': password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10); cls.sock.close(); cls.temp.cleanup()

    @classmethod
    def request(cls, path, payload=None, account='student-demo-1', method=None):
        request = Request(cls.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers={'X-Local-Token': cls.token, 'X-Account-ID': account,
                                                  'Content-Type': 'application/json'})
        with cls.client.open(request, timeout=10) as response:
            body = response.read()
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else (body, response.headers)

    def setUp(self):
        manager = self.app.state.jobs
        job = manager.create('中文 课程.mp4', 'balanced', owner_id='student-demo-1')
        self.job_id = job['id']; self.prefix = '/api/jobs/' + self.job_id
        self.folder = manager.root / self.job_id
        (self.folder / 'slides').mkdir()
        for number, color in [(1, 'red'), (2, 'blue')]:
            Image.new('RGB', (640, 360), color).save(self.folder / 'slides' / f'slide-{number:04d}.jpg')
        # A completed legacy job has no PPTX state. Saved page order is authoritative.
        manager.update(self.job_id, status='completed', result={'pages': [
            {'number': n, 'filename': f'slide-{n:04d}.jpg', 'timestamp': n} for n in [2, 1]]})
        manager.active = None

    def error(self, code, suffix, payload=None, **kwargs):
        with self.assertRaises(HTTPError) as caught:
            self.request(self.prefix + suffix, payload, **kwargs)
        self.assertEqual(caught.exception.code, code)
        caught.exception.close()

    def test_old_job_export_download_cache_permissions_and_delete(self):
        self.error(404, '/pptx', {}, account='student-demo-2')
        self.error(404, '/files/pptx')
        exported = self.request(self.prefix + '/pptx', {})
        self.assertEqual(exported['pages'], 2)
        body, headers = self.request(exported['url'])
        self.assertEqual(headers['Content-Type'], 'application/vnd.openxmlformats-officedocument.presentationml.presentation')
        self.assertIn('attachment;', headers['Content-Disposition'])
        self.assertIn('filename*=utf-8', headers['Content-Disposition'])
        deck = Presentation(io.BytesIO(body))
        self.assertEqual(len(deck.slides), 2)
        for slide, number in zip(deck.slides, [2, 1]):
            self.assertEqual(slide.shapes[0].image.blob, (self.folder / 'slides' / f'slide-{number:04d}.jpg').read_bytes())
        with patch('demo.pptx_export.create_pptx', side_effect=AssertionError('should reuse file')):
            self.request(self.prefix + '/pptx', {})
        self.assertEqual(self.request(exported['url'])[0], body)
        self.error(404, '/files/pptx', account='student-demo-2')
        self.request(self.prefix, method='DELETE')
        self.assertFalse(self.folder.exists())
        self.error(404, '/files/pptx')

    def test_unfinished_missing_or_broken_image_and_retry(self):
        jobs = self.app.state.jobs
        jobs.update(self.job_id, status='running')
        self.error(409, '/pptx', {})
        jobs.update(self.job_id, status='completed')
        image = self.folder / 'slides/slide-0001.jpg'; original = image.read_bytes()
        image.unlink(); self.error(404, '/pptx', {})
        image.write_bytes(b'not an image'); self.error(400, '/pptx', {})
        self.assertFalse((self.folder / 'slides.pptx').exists())
        self.assertNotIn(self.job_id, jobs.pptx_exporting)
        image.write_bytes(original)
        self.assertEqual(self.request(self.prefix + '/pptx', {})['pages'], 2)

    def test_export_does_not_block_api_or_allow_duplicate_export_and_delete(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(images, target):
            entered.set()
            if not release.wait(5): raise RuntimeError('test release timed out')
            create_pptx(images, target)
        with patch('demo.pptx_export.create_pptx', delayed), ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(self.request, self.prefix + '/pptx', {})
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.request(self.prefix)['status'], 'completed')
                self.error(409, '/pptx', {})
                self.error(409, '', method='DELETE')
            finally:
                release.set()
            self.assertEqual(result.result(timeout=5)['pages'], 2)
        self.assertNotIn(self.job_id, self.app.state.jobs.pptx_exporting)


if __name__ == '__main__':
    unittest.main()
