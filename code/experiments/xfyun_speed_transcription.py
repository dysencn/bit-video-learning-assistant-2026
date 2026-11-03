"""Real XFYun speed-transcription experiment using the project's video pipeline.

Credentials are read from XFYUN_APP_ID, XFYUN_API_KEY, XFYUN_API_SECRET.
Only speech returned by XFYun is written to transcript.txt / transcript.srt.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.utils import formatdate
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from demo.media import process_video, run_ffmpeg

UPLOAD_BASE = "https://upload-ost-api.xfyun.cn/file"
TASK_BASE = "https://ost-api.xfyun.cn/v2/ost"
DOC_URL = "https://www.xfyun.cn/doc/asr/speedTranscription/API.html"


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class TranscriptionError(Exception):
    pass


class Client:
    def __init__(self, directory):
        self.app_id = os.environ.get("XFYUN_APP_ID", "").strip()
        self.key = os.environ.get("XFYUN_API_KEY", "").strip()
        self.secret = os.environ.get("XFYUN_API_SECRET", "").strip()
        if not all((self.app_id, self.key, self.secret)):
            raise TranscriptionError("Missing XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET")
        self.directory = directory
        self.request_id = uuid.uuid4().hex
        # Never follow redirects carrying authentication to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.http = urllib.request.build_opener(NoRedirect)

    def safe(self, value):
        if isinstance(value, dict):
            return {k: ("[redacted]" if k in {"url", "audio_url"} else self.safe(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in (self.key, self.secret):
                value = value.replace(secret, "[redacted]")
        return value

    def post(self, url, body, content_type, label):
        parsed = urllib.parse.urlsplit(url)
        date = formatdate(usegmt=True)
        # The documented upload signature uses the digest of an empty body;
        # JSON task requests sign the actual bytes, as in the official Python demo.
        digest_body = b"" if parsed.hostname == "upload-ost-api.xfyun.cn" else body
        digest = "SHA-256=" + base64.b64encode(hashlib.sha256(digest_body).digest()).decode()
        origin = f"host: {parsed.netloc}\ndate: {date}\nPOST {parsed.path} HTTP/1.1\ndigest: {digest}"
        signature = base64.b64encode(hmac.new(
            self.secret.encode(), origin.encode(), hashlib.sha256).digest()).decode()
        headers = {
            "Host": parsed.netloc, "Date": date, "Digest": digest,
            "Content-Type": content_type, "Accept": "application/json",
            "Authorization": (f'api_key="{self.key}", algorithm="hmac-sha256", '
                              f'headers="host date request-line digest", signature="{signature}"'),
        }
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        started = time.monotonic()
        try:
            with self.http.open(request, timeout=60) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TranscriptionError(f"{label}: network failure: {self.safe(str(error))}") from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            payload = {"message": raw[:2000].decode("utf-8", errors="replace")}
        safe_payload = self.safe(payload)
        save_json(self.directory / f"{label}.json", {
            "http_status": status, "elapsed_seconds": round(time.monotonic() - started, 3),
            "response": safe_payload,
        })
        if status != 200 or not isinstance(payload, dict) or payload.get("code") != 0:
            raise TranscriptionError(f"{label}: HTTP {status}, response={json.dumps(safe_payload, ensure_ascii=False)}")
        return payload

    def post_json(self, url, value, label):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.post(url, body, "application/json", label)

    def multipart(self, path, audio, fields, label):
        boundary = uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                           f'\r\n\r\n{value}\r\n').encode())
        chunks.extend([
            (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; '
             'filename="audio-16k.wav"\r\nContent-Type: application/octet-stream\r\n\r\n').encode(),
            audio, f"\r\n--{boundary}--\r\n".encode(),
        ])
        return self.post(UPLOAD_BASE + path, b"".join(chunks),
                         f"multipart/form-data; boundary={boundary}", label)

    def upload(self, audio):
        fields = {"app_id": self.app_id, "request_id": self.request_id}
        if audio.stat().st_size < 30 * 1024 * 1024:
            result = self.multipart("/upload", audio.read_bytes(), fields, "upload")
        else:
            init = self.post_json(UPLOAD_BASE + "/mpupload/init", fields, "upload-init")
            fields["upload_id"] = init["data"]["upload_id"]
            with audio.open("rb") as stream:
                part = 0
                while chunk := stream.read(5 * 1024 * 1024):
                    part += 1
                    self.multipart("/mpupload/upload", chunk,
                                   {**fields, "slice_id": part}, f"upload-part-{part}")
                    print(f"Uploaded audio chunk {part}", flush=True)
            result = self.post_json(UPLOAD_BASE + "/mpupload/complete", fields, "upload-complete")
        return result["data"]["url"]


def timestamp(milliseconds):
    seconds, millis = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def decode_segments(result):
    if isinstance(result, str):
        result = json.loads(result)
    segments = []
    for item in result.get("lattice", []):
        best = item["json_1best"]
        if isinstance(best, str):
            best = json.loads(best)
        sentence = best["st"]
        words = []
        for alternative in sentence.get("rt", [])[:1]:
            for word in alternative.get("ws", []):
                candidates = word.get("cw", [])
                if candidates:
                    words.append(candidates[0].get("w", ""))
        text = "".join(words).strip()
        if text:
            segments.append({"start_ms": int(sentence["bg"]), "end_ms": int(sentence["ed"]),
                             "text": text, "speaker": sentence.get("rl")})
    return segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-wait", type=float, default=600)
    args = parser.parse_args()
    directory = args.output or CODE_ROOT / ".demo-test" / ("xfyun-asr-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "source": str(args.video.resolve()), "documentation": DOC_URL,
              "started_at": datetime.now(timezone.utc).isoformat(), "stage": "credentials"}
    started = time.monotonic()
    try:
        client = Client(directory)
        report["request_id"] = client.request_id
        report["stage"] = "video_extraction"
        cancel = threading.Event()
        last_stage = None

        def progress(stage, percent, message):
            nonlocal last_stage
            if stage != last_stage:
                print(f"Extraction {percent:.0f}%: {message}", flush=True)
                last_stage = stage

        extraction = process_video(args.video.resolve(), directory, "balanced", cancel, progress)
        save_json(directory / "extraction.json", extraction)
        if not extraction.get("audio"):
            raise TranscriptionError("The project could not extract an audio track")
        report["stage"] = "audio_conversion"
        audio = directory / "audio-16k.wav"
        run_ffmpeg(["-loglevel", "error", "-i", str(directory / extraction["audio"]),
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)], cancel)
        with wave.open(str(audio), "rb") as stream:
            report["audio"] = {"sample_rate": stream.getframerate(), "channels": stream.getnchannels(),
                               "bits_per_sample": stream.getsampwidth() * 8,
                               "duration_seconds": stream.getnframes() / stream.getframerate(),
                               "bytes": audio.stat().st_size}
        if report["audio"]["duration_seconds"] > 5 * 3600 or audio.stat().st_size > 500 * 1024 * 1024:
            raise TranscriptionError("Audio exceeds this API's 5-hour / 500 MB limit")
        print("Audio ready: " + json.dumps(report["audio"]), flush=True)
        report["stage"] = "upload"
        print("Uploading audio to XFYun...", flush=True)
        audio_url = client.upload(audio)
        report["stage"] = "create_task"
        print("Audio uploaded. Creating transcription task...", flush=True)
        response = client.post_json(TASK_BASE + "/pro_create", {
            "common": {"app_id": client.app_id},
            "business": {"request_id": client.request_id, "language": "zh_cn",
                         "domain": "pro_ost_ed", "accent": "mandarin"},
            "data": {"audio_url": audio_url, "audio_src": "http", "audio_size": audio.stat().st_size,
                     "format": "audio/L16;rate=16000", "encoding": "raw"},
        }, "create-task")
        report["task_id"] = response["data"]["task_id"]
        report["stage"] = "poll_task"
        save_json(directory / "report.json", report)
        deadline = time.monotonic() + args.max_wait
        count = 0
        while time.monotonic() < deadline:
            count += 1
            response = client.post_json(TASK_BASE + "/query", {
                "common": {"app_id": client.app_id}, "business": {"task_id": report["task_id"]},
            }, f"query-{count:03}")
            status = str(response["data"]["task_status"])
            print(f"Transcription poll {count}: task_status={status}", flush=True)
            if status in {"3", "4"}:
                segments = decode_segments(response["data"]["result"])
                if not segments:
                    raise TranscriptionError("XFYun completed the task but returned no recognized text")
                text = "\n".join(segment["text"] for segment in segments) + "\n"
                (directory / "transcript.txt").write_text(text, encoding="utf-8-sig")
                srt = "\n\n".join(
                    f"{i}\n{timestamp(s['start_ms'])} --> {timestamp(s['end_ms'])}\n{s['text']}"
                    for i, s in enumerate(segments, 1)) + "\n"
                (directory / "transcript.srt").write_text(srt, encoding="utf-8-sig")
                save_json(directory / "segments.json", segments)
                report.update(status="success", stage="complete", segment_count=len(segments), character_count=len(text))
                print(f"SUCCESS: {len(segments)} segments, {len(text)} characters", flush=True)
                break
            if status not in {"1", "2"}:
                raise TranscriptionError(f"Unexpected task status: {status}; inspect saved query response")
            time.sleep(3)
        else:
            raise TranscriptionError("Polling timed out; task ID is saved. Do not resubmit a billable task blindly.")
    except Exception as error:
        report.update(status="failed", error=str(error))
        print(f"FAILED at {report['stage']}: {error}", flush=True)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_json(directory / "report.json", report)
        print(f"Experiment output: {directory}", flush=True)
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
