"""Bounded local Python judging. This is not a hostile-code security sandbox."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
LIMITS = {"seconds": 3, "memory_mb": 256, "output_bytes": 65536, "code_bytes": 32768, "max_cases": 10}
LABELS = {"accepted": "通过", "wrong_answer": "输出不正确", "time_limit": "运行超时，已停止",
          "memory_limit": "内存超限，已停止", "output_limit": "输出超过 64 KB，已停止",
          "runtime_error": "运行错误", "system_error": "执行环境异常，请重试"}


def normalize(value):
    # Preserve meaningful leading/interior spaces; tolerate line endings and trailing spaces.
    return "\n".join(line.rstrip(" \t") for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")).rstrip("\n")


def validate_question(payload):
    def string(key, limit, required=True):
        value = payload.get(key, "")
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            label = {"title": "题目标题", "description": "题目描述"}.get(key, key)
            raise ValueError(f"请填写有效的{label}（最多 {limit} 字符）")
        return value
    title = string("title", 100)
    description = string("description", 5000)
    examples = payload.get("examples")
    cases = payload.get("test_cases")
    for name, items in [("示例", examples), ("判题用例", cases)]:
        if not isinstance(items, list) or not 1 <= len(items) <= LIMITS["max_cases"]:
            raise ValueError(f"{name}需要 1–10 组输入和输出")
        for item in items:
            if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or len(item[k].encode("utf-8")) > 8192 for k in ("input", "output")):
                raise ValueError(f"{name}的输入、输出必须是文本，每项最多 8 KB（允许空文本）")
    return {"id": uuid.uuid4().hex[:12], "type": "python", "title": title.strip(), "text": title.strip(),
            "description": description, "examples": [{k: c[k] for k in ("input", "output")} for c in examples],
            "test_cases": [{k: c[k] for k in ("input", "output")} for c in cases],
            "limits": dict(LIMITS)}


class WindowsJob:
    """Install kernel job limits before resuming a suspended interpreter."""
    def __init__(self, process, memory):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        k = self.k = c.WinDLL("kernel32", use_last_error=True)
        class Basic(c.Structure):
            _fields_ = [("per_process", c.c_int64), ("per_job", c.c_int64), ("flags", w.DWORD),
                        ("min_ws", c.c_size_t), ("max_ws", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]
        class Extended(c.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]; k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended()
        # Kill on close, job/process memory cap; two processes allow the Windows venv launcher.
        limits.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8
        limits.basic.active = 2
        limits.process_memory = limits.job_memory = memory
        try:
            if not k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)) or not k.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
                raise c.WinError(c.get_last_error())
            class ThreadEntry(c.Structure):
                _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD), ("pid", w.DWORD),
                            ("base", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
            k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]; k.CreateToolhelp32Snapshot.restype = w.HANDLE
            k.Thread32First.argtypes = k.Thread32Next.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
            k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]; k.OpenThread.restype = w.HANDLE
            k.ResumeThread.argtypes = [w.HANDLE]; k.ResumeThread.restype = w.DWORD
            snapshot = k.CreateToolhelp32Snapshot(4, 0)
            if snapshot == w.HANDLE(-1).value:
                raise c.WinError(c.get_last_error())
            resumed = False
            try:
                entry = ThreadEntry(); entry.size = c.sizeof(entry)
                more = k.Thread32First(snapshot, c.byref(entry))
                while more:
                    if entry.pid == process.pid:
                        handle = k.OpenThread(2, False, entry.tid)
                        if handle:
                            try:
                                resumed = k.ResumeThread(handle) != 0xffffffff
                            finally:
                                k.CloseHandle(handle)
                        break
                    more = k.Thread32Next(snapshot, c.byref(entry))
            finally:
                k.CloseHandle(snapshot)
            if not resumed:
                raise RuntimeError("无法启动受限 Python 进程")
        except BaseException:
            self.close()
            raise

    def kill(self):
        self.k.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def run_case(code, case, *, seconds=None):
    executable = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        raise RuntimeError("项目 .venv 不存在，请先运行 run.py 创建虚拟环境")
    seconds = seconds or LIMITS["seconds"]
    memory = LIMITS["memory_mb"] * 1024**2
    with tempfile.TemporaryDirectory(prefix="zhixue-python-") as folder:
        folder = Path(folder)
        (folder / "main.py").write_text(code, encoding="utf-8")
        (folder / "input.txt").write_text(case["input"], encoding="utf-8")
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE") if key in os.environ}
        env.update({"TEMP": str(folder), "TMP": str(folder)})
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | 4  # CREATE_SUSPENDED
        elif os.name == "posix":
            # Limits installed by a tiny wrapper before evaluating student code (no preexec in threads).
            kwargs["start_new_session"] = True
        else:
            raise RuntimeError("当前系统没有可用的资源限制执行器")
        worker = str(ROOT / "demo" / "python_worker.py")
        command = [str(executable), "-I", "-S", "-u", "-X", "utf8", worker]
        if os.name == "posix":
            setup = f"import resource,runpy;resource.setrlimit(resource.RLIMIT_AS,({memory},{memory}));resource.setrlimit(resource.RLIMIT_CPU,({int(seconds)+1},{int(seconds)+1}));resource.setrlimit(resource.RLIMIT_FSIZE,(0,0));runpy.run_path({worker!r},run_name='__main__')"
            command = command[:-1] + ["-c", setup]
        proc = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        job = None
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        lock = threading.Lock()
        def read(pipe, index):
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                with lock:
                    remaining = LIMITS["output_bytes"] - sum(map(len, buffers))
                    buffers[index].extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining:
                        overflow.set()
        readers = []
        started = time.monotonic()
        status = None
        try:
            if os.name == "nt":
                job = WindowsJob(proc, memory)
            for index, pipe in enumerate((proc.stdout, proc.stderr)):
                thread = threading.Thread(target=read, args=(pipe, index), daemon=True)
                thread.start(); readers.append(thread)
            while proc.poll() is None:
                if overflow.is_set():
                    status = "output_limit"; break
                if time.monotonic() - started > seconds:
                    status = "time_limit"; break
                time.sleep(.01)
        finally:
            # Also clean up descendants after normal parent exit.
            if job:
                job.kill()
            elif os.name == "posix":
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            elif proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for thread in readers: thread.join(timeout=5)
            proc.stdout.close(); proc.stderr.close()
            if job: job.close()
        output, error = [bytes(buf).decode("utf-8", errors="replace") for buf in buffers]
        if overflow.is_set(): status = "output_limit"
        if status is None:
            if proc.returncode == 120 or "MemoryError" in error or proc.returncode in (0xc0000017, 0xc000012d):
                status = "memory_limit"
            elif proc.returncode:
                status = "runtime_error"
            else:
                status = "accepted" if normalize(output) == normalize(case["output"]) else "wrong_answer"
        return {"status": status, "message": LABELS[status], "stdout": output[:4096], "stderr": error[-4096:],
                "seconds": round(time.monotonic() - started, 3)}


class ProgrammingManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.runs = {}
        self.active = set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="python-judge")

    def start(self, user, course, question, code, mode):
        with self.lock:
            if user["id"] in self.active or len(self.active) >= 2:
                raise ValueError("已有判题任务正在运行，请等待完成后再试")
            # Finished responses are bounded in memory; formal submissions are persisted separately.
            for key in list(self.runs):
                if len(self.runs) < 100: break
                if self.runs[key]["status"] == "completed": del self.runs[key]
            cases = question["examples"] if mode == "sample" else question["test_cases"]
            run = {"id": uuid.uuid4().hex, "owner_id": user["id"], "status": "running", "mode": mode,
                   "completed": 0, "total": len(cases), "results": [], "question_id": question["id"]}
            self.runs[run["id"]] = run
            self.active.add(user["id"])
            self.pool.submit(self.work, run, user, course, question, code, cases)
            return self.get(run["id"], user["id"])

    def get(self, run_id, owner):
        with self.lock:
            item = self.runs.get(run_id)
            if not item or item["owner_id"] != owner: return None
            return {**item, "results": [dict(result) for result in item["results"]]}

    def work(self, run, user, course, question, code, cases):
        try:
            for index, case in enumerate(cases):
                result = run_case(code, case)
                result["case"] = index + 1
                if run["mode"] == "sample":
                    result.update({"input": case["input"], "expected": case["output"]})
                else:
                    # Hidden cases: never disclose input/expected/output (code can echo hidden input).
                    result.pop("stdout"); result.pop("stderr")
                with self.lock:
                    run["results"].append(result); run["completed"] += 1
                if result["status"] in {"time_limit", "output_limit", "memory_limit", "runtime_error"}: break
            passed = sum(r["status"] == "accepted" for r in run["results"])
            score = round(passed / len(cases) * 100)
            if run["mode"] == "submit":
                record = {"id": uuid.uuid4().hex[:12], "type": "python", "course_id": course["id"],
                          "chapter_id": question["chapter_id"], "question_id": question["id"], "code": code,
                          "student": user["name"], "student_id": user["id"],
                          "submitted_at": datetime.now(timezone.utc).isoformat(), "score": score,
                          "correct_count": int(passed == len(cases)), "total": 1,
                          "passed_cases": passed, "total_cases": len(cases), "results": list(run["results"]),
                          "details": [{"question_id": question["id"], "text": question["title"],
                                       "correct": passed == len(cases), "knowledge_point_name": "Python 编程"}]}
                with self.store.lock:
                    self.store.data["submissions"].insert(0, record)
                    try:
                        self.store.save()
                    except Exception:
                        self.store.data["submissions"].remove(record)
                        raise
                with self.lock: run["submission_id"] = record["id"]
            with self.lock: run.update(score=score, passed=passed)
        except Exception:
            with self.lock: run["error"] = "判题执行或保存失败，请确认项目 .venv 可用后重试。"
        finally:
            with self.lock:
                run["status"] = "completed"
                self.active.discard(user["id"])

    def close(self):
        self.pool.shutdown(wait=True)
