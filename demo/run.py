"""Run with `python run.py`, or double-click run.bat on Windows."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"


def bootstrap() -> Path:
    environment = ROOT / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    created = not executable.is_file()
    if created:
        print("[1/3] 正在创建项目专用 Python 环境…", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = environment / ".demo-requirements"
    if created or not stamp.exists() or stamp.read_text() != digest:
        print(f"[2/3] 正在通过清华大学镜像准备依赖和 FFmpeg：{PIP_INDEX_URL}", flush=True)
        subprocess.check_call([str(executable), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(requirements), "-i", PIP_INDEX_URL])
        stamp.write_text(digest)
    return executable


def serve(args):
    import uvicorn
    from demo.app import create_app
    from demo.media import ffmpeg_path

    ffmpeg = ffmpeg_path()
    subprocess.run([ffmpeg, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=True, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    if args.check:
        print("环境检查通过：Web 服务、图片处理、PDF 和 FFmpeg 均可用。")
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for port in range(args.port, min(args.port + 20, 65536)):
        try:
            sock.bind(("127.0.0.1", port))
            break
        except OSError:
            continue
    else:
        raise RuntimeError("本地端口不可用，请使用 --port 指定其他端口")
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    print(f"\n[3/3] 已启动：{url}\n文件只在本机处理。请保留此窗口，按 Ctrl+C 停止服务。\n", flush=True)
    if not args.no_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="info"))
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="本地视频 → JPG / PDF / 音频 Demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="只安装并检查依赖")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口应在 1024–65535 之间")
    if sys.version_info < (3, 10):
        raise RuntimeError("请使用 Python 3.10 或更高版本")
    if args.serve:
        serve(args)
    else:
        executable = bootstrap()
        command = [str(executable), str(ROOT / "run.py"), "--serve", *sys.argv[1:]]
        child = subprocess.Popen(command, cwd=ROOT)
        try:
            raise SystemExit(child.wait())
        except KeyboardInterrupt:
            # The console also delivers Ctrl+C to the child server.
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait()


if __name__ == "__main__":
    try:
        main()
    except (Exception, subprocess.CalledProcessError) as error:
        print(f"\n启动失败：{error}\n依赖安装中断时，可联网后再次运行 run.py。", file=sys.stderr)
        raise SystemExit(1)
