"""Recording transcription contracts; API responses are fixtures, never billable calls."""
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
import wave

import uvicorn

from demo.app import create_app
from demo.jobs import JobManager
from demo.transcriptions import (TranscriptionManager, TranscriptionError, XfyunClient,
                                 read_credentials, decode_segments, render_html)

CREDS = ("test-app", "test-api-key", "test-api-secret")
FIXTURE_TEXT = '<script>alert("test only")</script>课程测试文字。'
RESULT = {"lattice": [{"json_1best": {"st": {"bg": "100", "ed": "900",
          "rt": [{"ws": [{"cw": [{"w": FIXTURE_TEXT}]}]}]}}}]}
COMPLETE = {"code": 0, "data": {"task_id": "fixture-task", "task_status": "4", "result": RESULT}}


def fake_conversion(args, cancel):
    with wave.open(str(args[-1]), "wb") as audio:
        audio.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 16000)


class TranscriptionUnitTests(unittest.TestCase):
    def test_three_environment_variables_and_legacy_aliases(self):
        for prefix in ("XUNFEI", "XFYUN"):
            env = {f"{prefix}_{suffix}": value for suffix, value in zip(("APP_ID", "API_KEY", "API_SECRET"), CREDS)}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(read_credentials(), CREDS)
        with patch.dict(os.environ, {"XUNFEI_API_KEY": "only-one-key"}, clear=True), patch("demo.transcriptions.os.name", "posix"):
            with self.assertRaisesRegex(TranscriptionError, "三项|三|APP_ID"):
                read_credentials()

    @unittest.skipUnless(os.name == "nt", "Windows registry")
    def test_user_variables_can_be_found_after_server_starts(self):
        values = dict(zip(("XUNFEI_APP_ID", "XUNFEI_API_KEY", "XUNFEI_API_SECRET"), CREDS))
        with patch.dict(os.environ, {}, clear=True), patch("winreg.OpenKey"), patch("winreg.QueryValueEx", side_effect=lambda key, name: (values[name], 1)):
            self.assertEqual(read_credentials(), CREDS)

    def test_html_escapes_all_provider_text_and_filename(self):
        segments = decode_segments(json.dumps(RESULT))
        self.assertEqual(segments[0]["text"], FIXTURE_TEXT)
        html = render_html('<img src=x onerror="bad">', segments, "2026-11-03")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("00:00:00", html)
        self.assertIn("@media print", html)

    def test_network_and_provider_errors_do_not_expose_credentials(self):
        client = XfyunClient(CREDS)
        for failure in (URLError(CREDS[2]), TimeoutError(CREDS[2])):
            with patch.object(client.http, "open", side_effect=failure):
                with self.assertRaises(TranscriptionError) as caught:
                    client.post_json("https://ost-api.xfyun.cn/v2/ost/query", {})
                self.assertNotIn(CREDS[2], str(caught.exception))
        error = HTTPError("url", 401, CREDS[2], {}, io.BytesIO(b"{}"))
        with patch.object(client.http, "open", side_effect=error), self.assertRaisesRegex(TranscriptionError, "认证失败"):
            client.post_json("https://ost-api.xfyun.cn/v2/ost/query", {})
        with patch.object(client.http, "open") as opened:
            response = opened.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps({"code": 10101, "message": CREDS[2]}).encode()
            with self.assertRaises(TranscriptionError) as caught:
                client.post_json("https://ost-api.xfyun.cn/v2/ost/query", {})
            self.assertNotIn(CREDS[2], str(caught.exception))

    def test_restart_marks_running_task_interrupted_and_keeps_task_id(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = JobManager(Path(directory))
            job = jobs.create("fixture.mp4", "balanced")
            jobs.update(job["id"], status="completed", transcription={"status": "recognizing", "task_id": "existing"})
            jobs.close()
            restored = JobManager(Path(directory))
            manager = TranscriptionManager(restored)
            state = restored.get(job["id"])["transcription"]
            self.assertEqual((state["status"], state["task_id"]), ("interrupted", "existing"))
            manager.close()
            restored.close()


class TranscriptionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.temp.name) / "jobs", seed_demo_history=False)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", port=port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(.05)
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        with urlopen(cls.url + "/api/config") as response:
            cls.token = json.load(response)["token"]
        for username, password in (("student1", "Learn123!"), ("student2", "Learn456!")):
            cls.request("/api/auth/login", {"username": username, "password": password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.temp.cleanup()

    @classmethod
    def request(cls, path, payload=None, account="student-demo-1", method=None):
        request = Request(cls.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers={"X-Local-Token": cls.token, "X-Account-ID": account,
                                                  "Content-Type": "application/json"})
        with cls.client.open(request, timeout=10) as response:
            raw = response.read()
            if "application/json" in response.headers.get("Content-Type", ""):
                return json.loads(raw)
            return raw.decode("utf-8-sig"), response.headers

    def setUp(self):
        jobs = self.app.state.jobs
        job = jobs.create("课程 <fixture>.mp4", "balanced", owner_id="student-demo-1")
        self.job_id = job["id"]
        self.prefix = f"/api/jobs/{self.job_id}"
        self.folder = jobs.root / self.job_id
        (self.folder / "audio.mp3").write_bytes(b"test-only audio placeholder")
        jobs.update(self.job_id, status="completed", result={"audio": "audio.mp3", "pages": [], "metadata": {"duration": 1}})
        jobs.active = None
        self.app.state.accounts.xfyun_keys.clear()

    def wait(self):
        for _ in range(150):
            state = self.request(self.prefix).get("transcription", {})
            if state.get("status") in {"failed", "completed"} and self.app.state.transcriptions.active is None:
                return state
            time.sleep(.02)
        self.fail("Transcription did not finish")

    def install_fixture(self, client):
        client.return_value.app_id = CREDS[0]
        client.return_value.upload.return_value = "https://example.invalid/test-only-audio"
        client.return_value.post_json.side_effect = [
            {"code": 0, "data": {"task_id": "fixture-task"}}, COMPLETE]

    def test_missing_credentials_and_audio_do_not_call_api(self):
        with patch("demo.transcriptions.read_credentials", side_effect=TranscriptionError("需要三项凭据", 400)), patch("demo.transcriptions.XfyunClient") as client:
            self.assertFalse(self.request("/api/ai/xfyun/credentials")["configured"])
            with self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + "/transcription", {})
            self.assertEqual(caught.exception.code, 400)
            client.assert_not_called()
        self.app.state.jobs.update(self.job_id, result={"audio": None})
        with self.assertRaises(HTTPError) as caught:
            self.request(self.prefix + "/transcription", {})
        self.assertEqual(caught.exception.code, 409)

    def test_real_http_flow_output_permissions_and_idempotence(self):
        with patch("demo.transcriptions.read_credentials", return_value=CREDS), patch("demo.transcriptions.run_ffmpeg", side_effect=fake_conversion), patch("demo.transcriptions.XfyunClient") as client:
            self.install_fixture(client)
            self.request(self.prefix + "/transcription", {})
            self.assertEqual(self.wait()["status"], "completed")
            self.request(self.prefix + "/transcription", {})
            client.return_value.upload.assert_called_once()
        html, headers = self.request(self.prefix + "/transcription/view")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("下载 TXT", html)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        downloaded, headers = self.request(self.prefix + "/transcription/download/html")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertNotIn('href="/api/', downloaded)
        plain, headers = self.request(self.prefix + "/transcription/download/txt")
        self.assertEqual(plain.replace("\r\n", "\n"), FIXTURE_TEXT + "\n")
        for path, method in (("/transcription", "POST"), ("/transcription/view", "GET"),
                             ("/transcription/download/html", "GET"), ("/transcription/download/txt", "GET")):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + path, account="student-demo-2", method=method)
            self.assertEqual(caught.exception.code, 404)
        self.assertFalse((self.folder / "transcript-audio.wav.tmp").exists())

    def test_session_credentials_are_private_validated_and_removed_on_logout(self):
        with patch("demo.transcriptions.read_credentials", side_effect=TranscriptionError("missing", 400)):
            value = dict(zip(("app_id", "api_key", "api_secret"), CREDS))
            for invalid in ({"api_key": "one"}, {**value, "api_secret": "bad\nkey"}):
                with self.assertRaises(HTTPError) as caught:
                    self.request("/api/ai/xfyun/credentials", invalid)
                self.assertEqual(caught.exception.code, 400)
            self.request("/api/ai/xfyun/credentials", value)
            self.assertEqual(self.request("/api/ai/xfyun/credentials"), {"configured": True, "source": "session"})
            self.assertFalse(self.request("/api/ai/xfyun/credentials", account="student-demo-2")["configured"])
            with patch("demo.transcriptions.run_ffmpeg", side_effect=fake_conversion), patch("demo.transcriptions.XfyunClient") as client:
                self.install_fixture(client)
                self.request(self.prefix + "/transcription", {})
                self.assertEqual(self.wait()["status"], "completed")
                client.assert_called_once_with(CREDS)
            for path in Path(self.temp.name).rglob("*.json"):
                self.assertNotIn(CREDS[2], path.read_text(encoding="utf-8"))
            self.request("/api/auth/logout", {})
            self.assertEqual(self.app.state.accounts.xfyun_keys, {})
            self.request("/api/auth/login", {"username": "student1", "password": "Learn123!"})

    def test_resume_queries_existing_task_without_another_upload(self):
        self.app.state.jobs.update(self.job_id, transcription={"status": "failed", "task_id": "fixture-task"})
        with patch("demo.transcriptions.read_credentials", return_value=CREDS), patch("demo.transcriptions.XfyunClient") as client:
            client.return_value.post_json.return_value = COMPLETE
            self.request(self.prefix + "/transcription", {})
            self.assertEqual(self.wait()["status"], "completed")
            client.return_value.upload.assert_not_called()
            self.assertEqual(client.return_value.post_json.call_count, 1)
            self.assertTrue(client.return_value.post_json.call_args.args[0].endswith("/query"))

    def test_failed_or_empty_response_has_no_download_and_preserves_mp3(self):
        with patch("demo.transcriptions.read_credentials", return_value=CREDS), patch("demo.transcriptions.run_ffmpeg", side_effect=fake_conversion), patch("demo.transcriptions.XfyunClient") as client:
            self.install_fixture(client)
            client.return_value.post_json.side_effect = [{"data": {"task_id": "empty"}},
                {"data": {"task_status": "4", "result": {"lattice": []}}}]
            self.request(self.prefix + "/transcription", {})
            self.assertEqual(self.wait()["status"], "failed")
            self.assertTrue((self.folder / "audio.mp3").exists())
            with self.assertRaises(HTTPError):
                self.request(self.prefix + "/transcription/download/html")

    def test_active_transcription_blocks_job_delete_then_cleanup_removes_outputs(self):
        entered, release = threading.Event(), threading.Event()
        def response(url, payload):
            if url.endswith("pro_create"):
                return {"data": {"task_id": "fixture-task"}}
            entered.set()
            release.wait(5)
            return COMPLETE
        with patch("demo.transcriptions.read_credentials", return_value=CREDS), patch("demo.transcriptions.run_ffmpeg", side_effect=fake_conversion), patch("demo.transcriptions.XfyunClient") as client:
            self.install_fixture(client)
            client.return_value.post_json.side_effect = response
            self.request(self.prefix + "/transcription", {})
            self.assertTrue(entered.wait(3))
            try:
                self.request(self.prefix + "/transcription", {})
                with self.assertRaises(HTTPError) as caught:
                    self.request(self.prefix, method="DELETE")
                self.assertEqual(caught.exception.code, 409)
            finally:
                release.set()
            self.assertEqual(self.wait()["status"], "completed")
            client.return_value.upload.assert_called_once()
        self.request(self.prefix, method="DELETE")
        self.assertFalse(self.folder.exists())

    def test_disk_error_returns_terminal_state_and_releases_worker(self):
        with patch("demo.transcriptions.read_credentials", return_value=CREDS), patch.object(self.app.state.jobs, "_save", side_effect=OSError("full")):
            with self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + "/transcription", {})
            self.assertEqual(caught.exception.code, 507)
        self.assertIsNone(self.app.state.transcriptions.active)
        self.assertEqual(self.request(self.prefix)["transcription"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
