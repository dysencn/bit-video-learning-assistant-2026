"""Opt-in XFYun recording transcription for completed video jobs."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import formatdate
from html import escape
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import wave

from .media import Cancelled, run_ffmpeg

UPLOAD_BASE = "https://upload-ost-api.xfyun.cn/file"
TASK_BASE = "https://ost-api.xfyun.cn/v2/ost"
ACTIVE = {"converting", "uploading", "submitting", "recognizing", "saving"}
MAX_AUDIO_BYTES = 500 * 1024 * 1024
MAX_SECONDS = 5 * 3600
MAX_WAIT = 600


class TranscriptionError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def read_credentials():
    """Read process, Windows user, then Windows system variables on every request."""
    names = (("XUNFEI_APP_ID", "XFYUN_APP_ID"),
             ("XUNFEI_API_KEY", "XFYUN_API_KEY"),
             ("XUNFEI_API_SECRET", "XFYUN_API_SECRET"))
    result = []
    for aliases in names:
        value = next((os.environ[name].strip() for name in aliases if os.environ.get(name, "").strip()), "")
        if not value and os.name == "nt":
            import winreg
            for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, path) as key:
                        for name in aliases:
                            try:
                                candidate, _ = winreg.QueryValueEx(key, name)
                                if isinstance(candidate, str) and candidate.strip():
                                    value = candidate.strip()
                                    break
                            except FileNotFoundError:
                                pass
                    if value:
                        break
                except OSError:
                    pass
        result.append(value)
    if not all(result):
        raise TranscriptionError("未找到完整的讯飞凭据：需要 XUNFEI_APP_ID、XUNFEI_API_KEY、XUNFEI_API_SECRET。可设置环境变量，或在弹窗中输入三项。", 400)
    return tuple(result)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _api_error(status, payload):
    if status in {401, 403}:
        return TranscriptionError("讯飞认证失败或当前应用未开通极速录音转写，请检查 APPID、APIKey、APISecret 和服务权限。")
    if status == 429:
        return TranscriptionError("讯飞请求过于频繁，请稍后重试。")
    code = payload.get("code") if isinstance(payload, dict) else None
    if code is not None:
        code = str(code) if str(code).isdigit() and len(str(code)) < 12 else "未知"
        return TranscriptionError(f"讯飞转写失败（错误码 {code}）。请检查音频格式、额度和服务权限。")
    return TranscriptionError(f"讯飞服务暂不可用（HTTP {status}），请稍后重试。")


class XfyunClient:
    def __init__(self, credentials):
        self.app_id, self.key, self.secret = credentials
        self.http = build_opener(_NoRedirect)

    def post(self, url, body, content_type):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"upload-ost-api.xfyun.cn", "ost-api.xfyun.cn"}:
            raise TranscriptionError("讯飞接口地址无效。")
        date = formatdate(usegmt=True)
        # XFYun upload documentation signs an empty digest; task JSON signs its actual body.
        digest_body = b"" if parsed.hostname == "upload-ost-api.xfyun.cn" else body
        digest = "SHA-256=" + base64.b64encode(hashlib.sha256(digest_body).digest()).decode("ascii")
        origin = f"host: {parsed.netloc}\ndate: {date}\nPOST {parsed.path} HTTP/1.1\ndigest: {digest}"
        signature = base64.b64encode(hmac.new(self.secret.encode(), origin.encode(), hashlib.sha256).digest()).decode("ascii")
        authorization = (f'api_key="{self.key}", algorithm="hmac-sha256", '
                         f'headers="host date request-line digest", signature="{signature}"')
        request = Request(url, data=body, method="POST", headers={
            "Host": parsed.netloc, "Date": date, "Digest": digest,
            "Content-Type": content_type, "Accept": "application/json", "Authorization": authorization,
        })
        try:
            with self.http.open(request, timeout=90) as response:
                status, raw = response.status, response.read(16 * 1024 * 1024 + 1)
        except HTTPError as error:
            status, raw = error.code, error.read(4096)
            error.close()
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
            raise TranscriptionError("无法连接讯飞服务或等待响应超时，请检查网络后重试。") from None
        if len(raw) > 16 * 1024 * 1024:
            raise TranscriptionError("讯飞返回内容过大，请拆分音频后重试。")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise TranscriptionError("讯飞返回的数据无法解析，请稍后重试。") from None
        if status != 200 or not isinstance(payload, dict) or payload.get("code") != 0:
            raise _api_error(status, payload)
        return payload

    def post_json(self, url, value):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.post(url, body, "application/json")

    def multipart(self, path, data, fields):
        boundary = uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        chunks += [(f'--{boundary}\r\nContent-Disposition: form-data; name="data"; '
                    'filename="audio-16k.wav"\r\nContent-Type: application/octet-stream\r\n\r\n').encode(),
                   data, f"\r\n--{boundary}--\r\n".encode()]
        return self.post(UPLOAD_BASE + path, b"".join(chunks), f"multipart/form-data; boundary={boundary}")

    def upload(self, audio, request_id, progress):
        fields = {"app_id": self.app_id, "request_id": request_id}
        if audio.stat().st_size < 30 * 1024 * 1024:
            result = self.multipart("/upload", audio.read_bytes(), fields)
        else:
            init = self.post_json(UPLOAD_BASE + "/mpupload/init", fields)
            upload_id = init["data"]["upload_id"]
            with audio.open("rb") as stream:
                index = 0
                while chunk := stream.read(5 * 1024 * 1024):
                    index += 1
                    self.multipart("/mpupload/upload", chunk,
                                   {**fields, "upload_id": upload_id, "slice_id": index})
                    progress(min(60, round(15 + stream.tell() / audio.stat().st_size * 45)),
                             f"正在上传录音分片 {index}…")
            result = self.post_json(UPLOAD_BASE + "/mpupload/complete",
                                    {**fields, "upload_id": upload_id})
        return result["data"]["url"]


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
            segments.append({"start_ms": int(sentence["bg"]), "end_ms": int(sentence["ed"]), "text": text})
    return segments


def timecode(milliseconds):
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def render_html(title, segments, created_at):
    rows = "\n".join(
        f'<article class="line"><time datetime="PT{item["start_ms"] / 1000:g}S">'
        f'{timecode(item["start_ms"])}</time><p>{escape(item["text"])}</p></article>'
        for item in segments)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · 课程录音文字稿</title><style>
:root{{--ink:#18322d;--green:#16715d;--muted:#657c73;--line:#dbe5de;--paper:#f7f8f4}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.8 "Segoe UI","Microsoft YaHei",sans-serif}}
header{{background:#fff;border-bottom:1px solid var(--line)}}.wrap{{max-width:860px;margin:auto;padding:24px}}
.brand{{color:var(--green);font-size:13px;font-weight:800}}h1{{font-size:clamp(22px,3vw,32px);line-height:1.35;margin:12px 0;overflow-wrap:anywhere}}
.meta{{color:var(--muted);font-size:13px}}main{{max-width:860px;margin:28px auto 72px;padding:0 24px}}
.line{{display:grid;grid-template-columns:92px minmax(0,1fr);gap:20px;padding:17px 0;border-bottom:1px solid var(--line)}}
.line time{{color:var(--green);font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;padding-top:4px}}
.line p{{margin:0;overflow-wrap:anywhere}}.note{{margin-top:34px;color:var(--muted);font-size:12px}}
.download-bar{{max-width:860px;margin:18px auto;padding:0 24px;display:flex;gap:12px}}.download-bar a{{color:var(--green);background:#e8f4ee;text-decoration:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:700}}
@media(max-width:550px){{.line{{grid-template-columns:1fr;gap:3px}}.wrap,main{{padding-left:18px;padding-right:18px}}}}
@media print{{body{{background:white}}main{{margin-top:10px}}.line{{break-inside:avoid}}.download-bar{{display:none}}}}
</style></head><body><header><div class="wrap"><div class="brand">知学 / 课程录音</div>
<h1>{escape(title)}</h1><p class="meta">{len(segments)} 段 · 生成于 {escape(created_at)}</p></div></header>
<main aria-label="课程录音文字稿">{rows}<p class="note">由科大讯飞根据录音识别，未经人工校对；请结合原视频核对。</p></main></body></html>'''


class TranscriptionManager:
    def __init__(self, jobs):
        self.jobs = jobs
        self.lock = threading.RLock()
        self.active = None
        self.stopping = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xfyun-asr")
        for job in list(jobs.jobs.values()):
            if job.get("transcription", {}).get("status") in ACTIVE:
                self._update(job["id"], status="interrupted", progress=0,
                             message="服务退出，转写已中断；点击重试可继续已有的识别任务。")

    def _update(self, job_id, **values):
        with self.jobs.lock:
            state = dict(self.jobs.jobs[job_id].get("transcription", {}))
            state.update(values)
            try:
                self.jobs.update(job_id, transcription=state)
            except OSError:
                if values.get("status") not in {"failed", "interrupted"}:
                    raise
            return state

    def start(self, job_id, credentials=None):
        with self.lock:
            job = self.jobs.get(job_id)
            if job["status"] != "completed" or not job.get("result", {}).get("audio"):
                raise TranscriptionError("请先完成含音轨的视频提取。", 409)
            state = job.get("transcription", {})
            if state.get("status") in ACTIVE:
                return state
            folder = self.jobs.root / job_id
            if state.get("status") == "completed" and (folder / "transcript.txt").is_file() and (folder / "transcript.html").is_file():
                return state
            if self.stopping.is_set():
                raise TranscriptionError("服务正在退出，请重新启动后重试。", 503)
            if self.active:
                raise TranscriptionError("已有录音正在转写，请等待完成。", 409)
            credentials = credentials or read_credentials()
            self.active = job_id
            try:
                state = self._update(job_id, status="recognizing" if state.get("task_id") else "converting", progress=3,
                                     message="正在恢复识别任务…" if state.get("task_id") else "正在准备录音格式…",
                                     available=False, started_at=datetime.now(timezone.utc).isoformat())
                self.executor.submit(self._work, job, credentials)
            except (OSError, RuntimeError):
                self.active = None
                self._update(job_id, status="failed", progress=0, available=False,
                             message="无法启动转写任务，请检查磁盘空间或重启服务。")
                raise TranscriptionError("无法启动转写任务，请检查磁盘空间或重启服务。", 507) from None
            return state

    def _work(self, job, credentials):
        job_id = job["id"]
        folder = self.jobs.root / job_id
        temporary_audio = folder / "transcript-audio.wav.tmp"
        started = time.monotonic()
        try:
            client = XfyunClient(credentials)
            task_id = job.get("transcription", {}).get("task_id")
            if not task_id:
                source = folder / "audio.mp3"
                if not source.is_file():
                    raise TranscriptionError("提取的 MP3 已丢失，请重新上传视频。", 404)
                duration = (job.get("result", {}).get("metadata") or {}).get("duration")
                if duration and duration > MAX_SECONDS:
                    raise TranscriptionError("录音超过 5 小时，请拆分视频。", 400)
                run_ffmpeg(["-loglevel", "error", "-i", str(source), "-vn", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(temporary_audio)], self.stopping)
                with wave.open(str(temporary_audio), "rb") as stream:
                    duration = stream.getnframes() / stream.getframerate()
                if duration > MAX_SECONDS or temporary_audio.stat().st_size > MAX_AUDIO_BYTES:
                    raise TranscriptionError("录音超过讯飞接口的 5 小时或 500MB 上限，请拆分视频。", 400)
                request_id = uuid.uuid4().hex
                self._update(job_id, status="uploading", progress=15, message="录音已转换，正在上传到讯飞…")
                audio_url = client.upload(temporary_audio, request_id,
                                          lambda percent, message: self._update(job_id, progress=percent, message=message))
                if self.stopping.is_set():
                    raise Cancelled()
                self._update(job_id, status="submitting", progress=65, message="上传完成，正在创建识别任务…")
                response = client.post_json(TASK_BASE + "/pro_create", {
                    "common": {"app_id": client.app_id},
                    "business": {"request_id": request_id, "language": "zh_cn",
                                 "domain": "pro_ost_ed", "accent": "mandarin"},
                    "data": {"audio_url": audio_url, "audio_src": "http", "audio_size": temporary_audio.stat().st_size,
                             "format": "audio/L16;rate=16000", "encoding": "raw"},
                })
                task_id = response["data"]["task_id"]
            self._update(job_id, status="recognizing", progress=None,
                         message="讯飞正在识别录音，请保持本地服务运行…", task_id=task_id)
            deadline = time.monotonic() + MAX_WAIT
            while time.monotonic() < deadline:
                if self.stopping.is_set():
                    raise Cancelled()
                response = client.post_json(TASK_BASE + "/query", {
                    "common": {"app_id": client.app_id}, "business": {"task_id": task_id},
                })
                status = str(response["data"]["task_status"])
                if status in {"3", "4"}:
                    segments = decode_segments(response["data"]["result"])
                    if not segments:
                        raise TranscriptionError("讯飞完成识别，但未返回可用文字。")
                    self._update(job_id, status="saving", progress=90, message="已收到文字，正在排版和保存…")
                    plain = "\n".join(item["text"] for item in segments) + "\n"
                    title = Path(job["filename"]).stem
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    html = render_html(title, segments, created_at)
                    txt_path, html_path = folder / "transcript.txt", folder / "transcript.html"
                    txt_temp, html_temp = folder / "transcript.txt.tmp", folder / "transcript.html.tmp"
                    try:
                        txt_temp.write_text(plain, encoding="utf-8-sig")
                        html_temp.write_text(html, encoding="utf-8")
                        txt_temp.replace(txt_path)
                        html_temp.replace(html_path)
                    finally:
                        txt_temp.unlink(missing_ok=True)
                        html_temp.unlink(missing_ok=True)
                    self._update(job_id, status="completed", progress=100,
                                 message="录音文字稿已保存，可以查看或下载。", available=True,
                                 segment_count=len(segments), elapsed_seconds=round(time.monotonic() - started, 1))
                    return
                if status not in {"1", "2"}:
                    raise TranscriptionError("讯飞任务状态异常，请稍后重试。")
                self.stopping.wait(3)
            raise TranscriptionError("等待讯飞识别超时；任务 ID 已保存，请稍后检查，避免重复消耗额度。")
        except Cancelled:
            self._update(job_id, status="interrupted", progress=0, available=False,
                         message="服务正在退出，转写已中断；重启后可点击重试继续。")
        except TranscriptionError as error:
            self._update(job_id, status="failed", progress=0, message=str(error), available=False)
        except (OSError, ValueError, KeyError, TypeError):
            self._update(job_id, status="failed", progress=0,
                         message="音频转换、识别结果解析或文件保存失败，请检查任务文件和磁盘空间。", available=False)
        except Exception:
            self._update(job_id, status="failed", progress=0,
                         message="转写时遇到异常，请检查音频文件后重试。", available=False)
        finally:
            try:
                temporary_audio.unlink(missing_ok=True)
            except OSError:
                pass
            finally:
                with self.lock:
                    self.active = None

    def file(self, job_id, kind):
        with self.lock:
            if kind not in {"html", "txt"}:
                raise TranscriptionError("没有这个文字稿文件。", 404)
            path = self.jobs.root / job_id / f"transcript.{kind}"
            state = self.jobs.get(job_id).get("transcription", {})
            if not path.is_file() or state.get("status") != "completed" or not state.get("available"):
                raise TranscriptionError("文字稿尚未生成或文件已丢失。", 404)
            return path

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
