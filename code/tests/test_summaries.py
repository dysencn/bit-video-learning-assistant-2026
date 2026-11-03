"""Offline contract/error tests. The tiny HTML below is a test fixture, not a course report."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

from PIL import Image
import uvicorn

from demo.app import create_app
from demo.jobs import JobManager
from demo.summaries import SummaryManager, SummaryError, read_api_key, call_deepseek, validate_html

FIXTURE = '<!DOCTYPE html><html><head><title>TEST FIXTURE</title><style>body{color:black}</style></head><body><h1>TEST ONLY</h1><img src="slides/slide-0001.jpg"><details><summary>Test</summary>Fixture</details></body></html>'
PAGES = [{"number": 1, "timestamp": 1.2, "filename": "slide-0001.jpg"}]
REPLY = {"choices": [{"finish_reason": "stop", "message": {"content": FIXTURE}}], "usage": {"total_tokens": 12}}
TEXT_FIXTURE = FIXTURE.replace('<img src="slides/slide-0001.jpg">', '')
TEXT_REPLY = {"choices": [{"finish_reason": "stop", "message": {"content": TEXT_FIXTURE}}]}
TRANSCRIPT = "TRANSCRIPT_TEST_ONLY_讲解中的独有例子。\n第二段转写文字。"


class SummaryUnitTests(unittest.TestCase):
    def test_environment_key_precedes_registry_and_is_trimmed(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": " test-key "}):
            self.assertEqual(read_api_key(), "test-key")

    @unittest.skipUnless(os.name == "nt", "Windows user-variable lookup")
    def test_user_and_system_variables_without_process_variable(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), patch("winreg.OpenKey") as opened, patch("winreg.QueryValueEx", return_value=("registry-test", 1)):
            self.assertEqual(read_api_key(), "registry-test")
            import winreg
            self.assertEqual(opened.call_args.args[0], winreg.HKEY_CURRENT_USER)
            opened.reset_mock()
            opened.side_effect = [FileNotFoundError(), MagicMock()]
            self.assertEqual(read_api_key(), "registry-test")
            self.assertEqual(opened.call_args.args[0], winreg.HKEY_LOCAL_MACHINE)
            opened.side_effect = FileNotFoundError()
            with self.assertRaisesRegex(SummaryError, "未找到 DEEPSEEK_API_KEY"):
                read_api_key()

    def test_api_errors_are_actionable_and_never_echo_credentials(self):
        for code, expected in [(400, "截图"), (401, "密钥"), (402, "余额"), (403, "权限"), (429, "频繁"), (503, "暂不可用")]:
            with self.subTest(code=code), patch("demo.summaries.urlopen", side_effect=HTTPError("url", code, "secret-must-not-leak", {}, None)):
                with self.assertRaisesRegex(SummaryError, expected) as caught:
                    call_deepseek(b"{}", "secret-must-not-leak")
                self.assertNotIn("secret-must-not-leak", str(caught.exception))
        for error, expected in [(TimeoutError(), "超时"), (URLError("secret-must-not-leak"), "网络|互联网"), (ConnectionResetError(), "连接")]:
            with patch("demo.summaries.urlopen", side_effect=error), self.assertRaisesRegex(SummaryError, expected):
                call_deepseek(b"{}", "test")
        with patch("demo.summaries.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"not JSON"
            with self.assertRaisesRegex(SummaryError, "解析"):
                call_deepseek(b"{}", "test")

    def test_active_external_and_incomplete_html_is_rejected(self):
        self.assertEqual(validate_html(FIXTURE, PAGES), FIXTURE)
        for snippet in ['<script>alert(1)</script>', '<img src="https://example.org/a">', '<img src="slides/slide-0001.jpg" onerror="alert(1)">', '<meta http-equiv="refresh" content="0;url=https://example.org">', '<style>@import "https://example.org";</style>', '<a href="javascript:alert(1)">x</a>']:
            with self.subTest(snippet=snippet), self.assertRaises(SummaryError):
                validate_html(FIXTURE.replace("</body>", snippet + "</body>"), PAGES)
        with self.assertRaises(SummaryError):
            validate_html(FIXTURE[:-8], PAGES)

    def test_restart_marks_inflight_summary_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = JobManager(Path(directory))
            job = jobs.create("test.mp4", "balanced")
            jobs.update(job["id"], status="completed", summary={"status": "generating"})
            jobs.close()
            restored = JobManager(Path(directory))
            manager = SummaryManager(restored)
            self.assertEqual(restored.get(job["id"])["summary"]["status"], "interrupted")
            manager.close()
            restored.close()


class SummaryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.temp.name) / "jobs")
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
        for username, password in [("student1", "Learn123!"), ("student2", "Learn456!")]:
            cls.request("/api/auth/login", {"username": username, "password": password})

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.temp.cleanup()

    @classmethod
    def request(cls, path, payload=None, account="student-demo-1", method=None):
        request = Request(cls.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers={"X-Local-Token": cls.token, "X-Account-ID": account, "Content-Type": "application/json"})
        with cls.client.open(request, timeout=10) as response:
            raw = response.read().decode()
            return (raw, response.headers) if "text/html" in response.headers.get("Content-Type", "") else json.loads(raw)

    def setUp(self):
        jobs = self.app.state.jobs
        job = jobs.create("test.mp4", "balanced", owner_id="student-demo-1")
        self.job_id = job["id"]
        self.prefix = f"/api/jobs/{self.job_id}"
        self.folder = jobs.root / self.job_id
        (self.folder / "slides").mkdir()
        Image.new("RGB", (20, 20), "white").save(self.folder / "slides" / "slide-0001.jpg")
        jobs.update(self.job_id, status="completed", result={"pages": PAGES})
        jobs.active = None

    def wait_summary(self):
        for _ in range(150):
            state = self.request(self.prefix)["summary"]
            if state["status"] in {"failed", "completed"} and self.app.state.summaries.active is None:
                return state
            time.sleep(.02)
        self.fail("Summary did not finish")

    def test_missing_key_fails_before_any_api_call(self):
        with patch("demo.summaries.read_api_key", side_effect=SummaryError("未找到 DEEPSEEK_API_KEY", 400)), patch("demo.summaries.call_deepseek") as call:
            with self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + "/summary", {})
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("DEEPSEEK_API_KEY", caught.exception.read().decode())
            call.assert_not_called()

    def test_owner_required_and_extraction_must_finish(self):
        for suffix, payload in [("/summary", {}), ("/summary/view", None)]:
            with self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + suffix, payload, account="student-demo-2")
            self.assertEqual(caught.exception.code, 404)
        self.app.state.jobs.update(self.job_id, status="running")
        with self.assertRaises(HTTPError) as caught:
            self.request(self.prefix + "/summary", {})
        self.assertEqual(caught.exception.code, 409)

    def test_success_saves_only_api_content_and_is_idempotent(self):
        entered, release = threading.Event(), threading.Event()
        def reply(body, key):
            payload = json.loads(body)
            self.assertTrue(payload["messages"][0]["content"][2]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            entered.set()
            release.wait(5)
            return REPLY
        with patch("demo.summaries.read_api_key", return_value="fixture-key"), patch("demo.summaries.call_deepseek", side_effect=reply) as call:
            self.request(self.prefix + "/summary", {})
            self.assertTrue(entered.wait(5))
            self.request(self.prefix + "/summary", {})
            release.set()
            self.assertEqual(self.wait_summary()["status"], "completed")
            self.request(self.prefix + "/summary", {})
            self.assertEqual(call.call_count, 1)
        self.assertEqual((self.folder / "summary.html").read_text(encoding="utf-8"), FIXTURE)
        html, headers = self.request(self.prefix + "/summary/view")
        self.assertIn(self.prefix + "/pages/1?account=student-demo-1", html)
        self.assertIn("sandbox allow-same-origin", headers["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("fixture-key", (self.folder / "job.json").read_text(encoding="utf-8"))

    def test_api_failure_retry_and_truncated_output(self):
        with patch("demo.summaries.read_api_key", return_value="fixture-key"), patch("demo.summaries.call_deepseek", side_effect=SummaryError("网络连接失败")):
            self.request(self.prefix + "/summary", {})
            self.assertEqual(self.wait_summary()["status"], "failed")
        self.assertFalse((self.folder / "summary.html").exists())
        with patch("demo.summaries.read_api_key", return_value="fixture-key"), patch("demo.summaries.call_deepseek", return_value={"choices": [{"finish_reason": "length"}]}):
            self.request(self.prefix + "/summary", {})
            self.assertIn("截断", self.wait_summary()["message"])
        with patch("demo.summaries.read_api_key", return_value="fixture-key"), patch("demo.summaries.call_deepseek", return_value=REPLY):
            self.request(self.prefix + "/summary", {})
            self.assertEqual(self.wait_summary()["status"], "completed")

    def test_disk_failure_still_reports_terminal_state_and_releases_slot(self):
        with patch("demo.summaries.read_api_key", return_value="fixture-key"), patch.object(self.app.state.jobs, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + "/summary", {})
            self.assertEqual(caught.exception.code, 507)
            self.assertEqual(self.request(self.prefix)["summary"]["status"], "failed")
            self.assertIsNone(self.app.state.summaries.active)

    def seed_summary(self):
        (self.folder / "summary.html").write_text(FIXTURE, encoding="utf-8")
        self.app.state.jobs.update(self.job_id, summary={"status": "completed", "progress": 100, "available": True})

    def test_history_and_summary_are_private_even_for_shared_course(self):
        self.seed_summary()
        # Published course resources never confer access to another account's task.
        course = self.app.state.learning.data["courses"][0]
        course["resources"].append({"id": "shared-test", "job_id": self.job_id})
        course["major_id"] = "m-ai"
        self.app.state.learning.save()
        self.assertIn(self.job_id, [j["id"] for j in self.request("/api/jobs")])
        self.assertNotIn(self.job_id, [j["id"] for j in self.request("/api/jobs", account="student-demo-2")])
        for suffix, method in [("", "GET"), ("", "DELETE"), ("/summary/view", "GET"),
                               ("/summary/download", "GET"), ("/summary", "DELETE"), ("/summary", "POST")]:
            with self.subTest(suffix=suffix, method=method), self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + suffix, {} if method == "POST" else None, account="student-demo-2", method=method)
            self.assertEqual(caught.exception.code, 404)
        self.assertTrue((self.folder / "summary.html").is_file())

    def test_download_is_self_contained_and_delete_summary_preserves_extraction(self):
        import base64
        self.seed_summary()
        html, headers = self.request(self.prefix + "/summary/download")
        encoded = base64.b64encode((self.folder / "slides/slide-0001.jpg").read_bytes()).decode()
        self.assertIn("data:image/jpeg;base64," + encoded, html)
        self.assertNotIn("slides/slide-", html)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("filename*=UTF-8", headers["Content-Disposition"])
        html, _ = self.request(self.prefix + "/summary/view")
        self.assertIn("下载总结 HTML", html)
        result = self.request(self.prefix + "/summary", method="DELETE")
        self.assertEqual(result["summary"], {})
        self.assertFalse((self.folder / "summary.html").exists())
        self.assertTrue((self.folder / "slides/slide-0001.jpg").is_file())
        self.assertEqual(json.loads((self.folder / "job.json").read_text(encoding="utf-8"))["summary"], {})
        with self.assertRaises(HTTPError) as caught:
            self.request(self.prefix + "/summary/download")
        self.assertEqual(caught.exception.code, 404)

    def test_regeneration_preserves_previous_success_until_replacement(self):
        self.seed_summary()
        with patch("demo.summaries.read_api_key", return_value="test"), patch("demo.summaries.call_deepseek", side_effect=SummaryError("网络故障")):
            self.request(self.prefix + "/summary", {"regenerate": True})
            state = self.wait_summary()
            self.assertEqual(state["status"], "failed")
            self.assertTrue(state["available"])
        self.assertEqual((self.folder / "summary.html").read_text(encoding="utf-8"), FIXTURE)
        self.assertIn("TEST ONLY", self.request(self.prefix + "/summary/view")[0])
        updated = FIXTURE.replace("TEST ONLY", "UPDATED TEST FIXTURE")
        with patch("demo.summaries.read_api_key", return_value="test"), patch("demo.summaries.call_deepseek", return_value={"choices": [{"finish_reason": "stop", "message": {"content": updated}}]}):
            self.request(self.prefix + "/summary", {"regenerate": True})
            self.assertEqual(self.wait_summary()["status"], "completed")
        self.assertEqual((self.folder / "summary.html").read_text(encoding="utf-8"), updated)

    def test_delete_task_removes_files_metadata_and_course_references(self):
        self.seed_summary()
        for filename in ["slides.pdf", "slides.zip", "audio.mp3"]:
            (self.folder / filename).write_bytes(b"test fixture")
        course = self.app.state.learning.data["courses"][0]
        course["resources"].append({"id": "delete-test", "job_id": self.job_id})
        self.app.state.learning.save()
        result = self.request(self.prefix, method="DELETE")
        self.assertEqual(result["removed_resources"], 1)
        self.assertFalse(self.folder.exists())
        self.assertNotIn(self.job_id, [j["id"] for j in self.request("/api/jobs")])
        stored = json.loads(self.app.state.learning.path.read_text(encoding="utf-8"))
        self.assertFalse(any(r.get("job_id") == self.job_id for c in stored["courses"] for r in c["resources"]))
        restored = JobManager(self.app.state.jobs.root)
        self.assertIsNone(restored.get(self.job_id))
        restored.close()

    def test_deletion_rejects_workers_that_can_still_write(self):
        entered, release = threading.Event(), threading.Event()
        def reply(*args):
            entered.set(); release.wait(5); return REPLY
        with patch("demo.summaries.read_api_key", return_value="test"), patch("demo.summaries.call_deepseek", side_effect=reply):
            self.request(self.prefix + "/summary", {})
            self.assertTrue(entered.wait(5))
            try:
                for suffix in ["", "/summary"]:
                    with self.assertRaises(HTTPError) as caught:
                        self.request(self.prefix + suffix, method="DELETE")
                    self.assertEqual(caught.exception.code, 409)
                    self.assertTrue(self.folder.is_dir())
            finally:
                release.set()
            self.wait_summary()

    def test_manual_key_is_used_session_private_and_never_persisted(self):
        key = "test-browser-key-not-real"
        with patch("demo.summaries.read_api_key", side_effect=SummaryError("未找到 DEEPSEEK_API_KEY", 400)):
            self.assertFalse(self.request("/api/ai/key")["configured"])
            result = self.request("/api/ai/key", {"api_key": key})
            self.assertEqual(result, {"configured": True, "source": "session"})
            self.assertFalse(self.request("/api/ai/key", account="student-demo-2")["configured"])
            with patch("demo.summaries.call_deepseek", return_value=REPLY) as call:
                self.request(self.prefix + "/summary", {})
                self.assertEqual(self.wait_summary()["status"], "completed")
                self.assertEqual(call.call_args.args[1], key)
            for filename in Path(self.temp.name).rglob("*.json"):
                self.assertNotIn(key, filename.read_text(encoding="utf-8"))
            self.request("/api/auth/logout", {})
            self.assertNotIn(key, self.app.state.accounts.api_keys.values())
            self.request("/api/auth/login", {"username": "student1", "password": "Learn123!"})
            self.assertFalse(self.request("/api/ai/key")["configured"])

    def test_manual_key_rejects_empty_or_header_injection(self):
        for value in [None, "", " ", "abc\r\nInjected: value", "x" * 513]:
            with self.subTest(value=value), self.assertRaises(HTTPError) as caught:
                self.request("/api/ai/key", {"api_key": value})
            self.assertEqual(caught.exception.code, 400)

    def add_transcript(self):
        (self.folder / "transcript.txt").write_text(TRANSCRIPT, encoding="utf-8-sig")
        self.app.state.jobs.update(self.job_id, transcription={"status": "completed", "available": True})

    def test_three_modes_send_only_selected_material_and_distinct_prompts(self):
        self.add_transcript()
        prompts = []
        for mode in ("slides", "transcript", "combined"):
            with self.subTest(mode=mode), patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", return_value=TEXT_REPLY if mode == "transcript" else REPLY) as call:
                self.request(self.prefix + "/summary", {"mode": mode, "regenerate": True})
                state = self.wait_summary()
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["available_mode"], mode)
                payload = json.loads(call.call_args.args[0])
                content = payload["messages"][0]["content"]
                images = [item for item in content if item["type"] == "image_url"]
                text = "\n".join(item["text"] for item in content if item["type"] == "text")
                self.assertEqual(len(images), 0 if mode == "transcript" else 1)
                self.assertEqual("TRANSCRIPT_TEST_ONLY" in text, mode != "slides")
                self.assertEqual(state["image_count"], len(images))
                if mode == "transcript":
                    self.assertNotIn("slide-0001.jpg", json.dumps(payload))
                    self.assertIn("文字稿第1段", text)
                    self.assertNotIn("data:image", self.request(self.prefix + "/summary/download")[0])
                prompts.append(content[0]["text"])
        self.assertEqual(len(set(prompts)), 3)

    def test_modes_cannot_bypass_required_preprocessing_and_transcription(self):
        with patch("demo.summaries.call_deepseek") as call:
            for mode in ("transcript", "combined"):
                with self.assertRaises(HTTPError) as caught:
                    self.request(self.prefix + "/summary", {"mode": mode})
                self.assertEqual(caught.exception.code, 409)
                self.assertIn("转文字", caught.exception.read().decode())
            self.add_transcript()
            self.app.state.jobs.update(self.job_id, status="running")
            for mode in ("slides", "transcript", "combined"):
                with self.assertRaises(HTTPError) as caught:
                    self.request(self.prefix + "/summary", {"mode": mode})
                self.assertEqual(caught.exception.code, 409)
            call.assert_not_called()

    def test_options_follow_real_files_and_live_transcription_status(self):
        options = self.request(self.prefix)["summary_options"]
        self.assertTrue(options["slides"]["ready"])
        self.assertFalse(options["combined"]["ready"])
        self.app.state.jobs.update(self.job_id, transcription={"status": "recognizing"})
        self.assertIn("等待", self.request(self.prefix)["summary_options"]["transcript"]["reason"])
        self.add_transcript()
        self.assertTrue(all(option["ready"] for option in self.request(self.prefix)["summary_options"].values()))
        (self.folder / "slides/slide-0001.jpg").unlink()
        options = self.request(self.prefix)["summary_options"]
        self.assertTrue(options["transcript"]["ready"])
        self.assertFalse(options["slides"]["ready"])
        self.assertFalse(options["combined"]["ready"])
        (self.folder / "transcript.txt").unlink()
        self.assertFalse(self.request(self.prefix)["summary_options"]["transcript"]["ready"])

    def test_text_only_works_without_slides_and_disallows_fabricated_images(self):
        self.add_transcript()
        (self.folder / "slides/slide-0001.jpg").unlink()
        self.app.state.jobs.update(self.job_id, result={"pages": []})
        with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", return_value=REPLY):
            self.request(self.prefix + "/summary", {"mode": "transcript"})
            self.assertEqual(self.wait_summary()["status"], "failed")
        with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", return_value=TEXT_REPLY):
            self.request(self.prefix + "/summary", {"mode": "transcript"})
            self.assertEqual(self.wait_summary()["status"], "completed")
        self.assertIn("仅演讲文字", self.request(self.prefix + "/summary/view")[0])
        self.assertEqual(self.request(self.prefix + "/summary/download")[0], TEXT_FIXTURE)

    def test_slides_only_never_reads_invalid_or_oversized_transcript(self):
        self.add_transcript()
        (self.folder / "transcript.txt").write_bytes(b"\xff" * (1024**2 + 1))
        with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", return_value=REPLY) as call:
            self.request(self.prefix + "/summary", {"mode": "slides"})
            self.assertEqual(self.wait_summary()["status"], "completed")
            call.assert_called_once()

    def test_empty_or_oversized_text_is_not_sent_to_deepseek(self):
        self.add_transcript()
        for body in ("  \n   ", "文" * (1024**2)):
            (self.folder / "transcript.txt").write_text(body, encoding="utf-8")
            with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek") as call:
                self.request(self.prefix + "/summary", {"mode": "transcript"})
                self.assertEqual(self.wait_summary()["status"], "failed")
                call.assert_not_called()

    def test_switch_mode_requires_confirmation_and_failed_replacement_keeps_source(self):
        self.seed_summary()
        self.add_transcript()
        with self.assertRaises(HTTPError) as caught:
            self.request(self.prefix + "/summary", {"mode": "transcript"})
        self.assertEqual(caught.exception.code, 409)
        with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", side_effect=SummaryError("network fixture")):
            self.request(self.prefix + "/summary", {"mode": "transcript", "regenerate": True})
            state = self.wait_summary()
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["mode"], "transcript")
            self.assertEqual(state["available_mode"], "slides")
        self.assertIn("data:image/jpeg;base64,", self.request(self.prefix + "/summary/download")[0])
        self.assertIn("来源：仅 PPT", self.request(self.prefix + "/summary/view")[0])

    def test_invalid_modes_and_conflicting_active_mode_do_not_start_another_request(self):
        for mode in (None, "audio", {}, [], True):
            with self.subTest(mode=mode), self.assertRaises(HTTPError) as caught:
                self.request(self.prefix + "/summary", {"mode": mode})
            self.assertEqual(caught.exception.code, 400)
        self.add_transcript()
        entered, release = threading.Event(), threading.Event()
        def reply(*args):
            entered.set(); release.wait(5); return REPLY
        with patch("demo.summaries.read_api_key", return_value="fixture"), patch("demo.summaries.call_deepseek", side_effect=reply) as call:
            self.request(self.prefix + "/summary", {"mode": "combined"})
            self.assertTrue(entered.wait(3))
            try:
                self.request(self.prefix + "/summary", {"mode": "combined"})
                with self.assertRaises(HTTPError) as caught:
                    self.request(self.prefix + "/summary", {"mode": "slides"})
                self.assertEqual(caught.exception.code, 409)
            finally:
                release.set()
            self.assertEqual(self.wait_summary()["status"], "completed")
            call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
