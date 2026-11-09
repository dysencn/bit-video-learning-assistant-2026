"""Child entry point. Resource limits are installed by the parent before this runs."""
import os
from pathlib import Path
import sys

folder = Path.cwd().resolve()
runtime = Path(sys.base_prefix).resolve()
source = (folder / "main.py").read_text(encoding="utf-8")
sys.stdin = open(folder / "input.txt", encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def guard(event, args):
    # Defense in depth for classroom mistakes, not an adversarial Python sandbox.
    if event.startswith(("socket.", "subprocess.", "ctypes.", "winreg.")) or event in {
        "os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.spawn",
        "os.startfile", "os.kill", "os.killpg", "os.link", "os.symlink", "os.truncate",
        "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chdir", "os.chmod",
    }:
        raise PermissionError("编程练习不允许网络、子进程或系统修改操作")
    if event == "open":
        filename, mode, flags = args
        if not isinstance(filename, (str, bytes, os.PathLike)):
            raise PermissionError("不允许直接访问文件描述符")
        target = Path(os.fsdecode(filename)).resolve()
        writable = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if writable or not (target.is_relative_to(folder) or target.is_relative_to(runtime)):
            raise PermissionError("本题仅支持标准输入输出，不允许读写项目或个人文件")


sys.addaudithook(guard)
try:
    exec(compile(source, "main.py", "exec"), {"__name__": "__main__", "__file__": "main.py"})
except MemoryError:
    os._exit(120)
