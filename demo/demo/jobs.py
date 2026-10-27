"""Single-worker job lifecycle with atomic, persistent status snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from .media import Cancelled, process_video
from .accounts import DEMO_TEACHER

log = logging.getLogger(__name__)
ACTIVE_STATES = {"uploading", "queued", "running", "cancelling"}


class BusyError(Exception):
    pass


class JobManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.active = None
        self.last_save: dict[str, float] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video")
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["id"] != path.parent.name:
                    continue
                self.jobs[job["id"]] = job
                if "owner_id" not in job:
                    job["owner_id"] = DEMO_TEACHER
                    self._save(job)
                if job["status"] in ACTIVE_STATES:
                    job.update(status="interrupted", message="上次服务已退出，请重新选择视频处理。")
                    self._save(job)
                    (path.parent / "input.part").unlink(missing_ok=True)
            except (OSError, ValueError, KeyError):
                log.warning("Skipped unreadable job: %s", path)

    def _save(self, job: dict):
        folder = self.root / job["id"]
        folder.mkdir(exist_ok=True)
        temporary = folder / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(folder / "job.json")
        self.last_save[job["id"]] = time.monotonic()

    def create(self, name: str, profile: str, owner_id: str = DEMO_TEACHER) -> dict:
        with self.lock:
            if self.active:
                raise BusyError("已有视频正在处理，请等待完成或先取消当前任务。")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "filename": name, "profile": profile, "status": "uploading",
                   "stage": "uploading", "progress": 0, "message": "正在接收本地视频",
                   "created_at": datetime.now(timezone.utc).isoformat(), "owner_id": owner_id, "result": None}
            self.jobs[job_id] = job
            self.events[job_id] = threading.Event()
            self.active = job_id
            self._save(job)
            return copy.deepcopy(job)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return copy.deepcopy(self.jobs.get(job_id))

    def list(self, owner_id: str | None = None) -> list[dict]:
        with self.lock:
            items = [j for j in self.jobs.values() if owner_id is None or j["owner_id"] == owner_id]
            return [copy.deepcopy(job) for job in sorted(items, key=lambda j: j["created_at"], reverse=True)[:20]]

    def update(self, job_id: str, *, persist=True, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            if persist:
                self._save(self.jobs[job_id])

    def fail_upload(self, job_id: str, message: str):
        with self.lock:
            self.update(job_id, status="failed", message=message)
            self.events.pop(job_id, None)
            if self.active == job_id:
                self.active = None
            (self.root / job_id / "input.part").unlink(missing_ok=True)

    def start(self, job_id: str, source: Path):
        self.update(job_id, status="queued", stage="queued", message="视频已就绪，准备处理")
        self.executor.submit(self._work, job_id, source)

    def _work(self, job_id: str, source: Path):
        def progress(stage, percent, message):
            with self.lock:
                if self.jobs[job_id]["status"] == "cancelling":
                    return
                self.update(job_id, status="running", stage=stage, progress=round(percent, 1),
                            message=message, persist=time.monotonic() - self.last_save.get(job_id, 0) > 1)

        try:
            result = process_video(source, self.root / job_id, self.jobs[job_id]["profile"],
                                   self.events[job_id], progress)
            with self.lock:
                if self.events[job_id].is_set():
                    raise Cancelled()
                self.update(job_id, status="completed", stage="completed", progress=100,
                            message=f"处理完成，共提取 {len(result['pages'])} 页", result=result)
        except Cancelled:
            self.update(job_id, status="cancelled", message="任务已取消，可以重新选择视频。")
        except Exception as error:
            log.exception("Job %s failed", job_id)
            self.update(job_id, status="failed", message=str(error) or "处理失败，请查看终端日志。")
        finally:
            # The source is a temporary local copy; generated files remain available.
            source.unlink(missing_ok=True)
            with self.lock:
                if self.active == job_id:
                    self.active = None
                self.events.pop(job_id, None)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job["status"] in ACTIVE_STATES and job_id in self.events:
                self.events[job_id].set()
                self.update(job_id, status="cancelling", message="正在停止当前处理…")
            return self.get(job_id)

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
