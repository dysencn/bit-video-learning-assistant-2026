"""Streaming slide detection inspired by BIT_yanhe_download_2026.

The implementation here is independent: FFmpeg thumbnail stream, stable runs,
pixel differences + DCT perceptual hashes, then full-resolution frame extraction.
No OpenCV, GPU, model weights, or network services are required.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable
import zipfile

import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas


PROFILES = {
    "detailed": {"fps": 5, "stable": 0.4, "mad": 2.0, "fraction": 0.009},
    "balanced": {"fps": 5, "stable": 0.8, "mad": 3.5, "fraction": 0.018},
    "compact": {"fps": 2, "stable": 1.2, "mad": 5.0, "fraction": 0.035},
}
THUMB_W, THUMB_H = 320, 180
MAX_PAGES = 600
HIDDEN_PROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
Progress = Callable[[str, float, str], None]


class Cancelled(Exception):
    pass


class MediaError(Exception):
    pass


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("任务已取消")


def ffmpeg_path() -> str:
    """Prefer an explicit/local executable; wheel supplies a portable fallback."""
    configured = os.environ.get("VIDEO_DEMO_FFMPEG")
    if configured:
        if not Path(configured).is_file():
            raise MediaError("VIDEO_DEMO_FFMPEG 指向的文件不存在")
        return configured
    root = Path(__file__).resolve().parent.parent
    bundled = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if bundled.is_file():
        return str(bundled)
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], cancel: threading.Event, timeout: float = 3600) -> str:
    """Drain diagnostics to disk so pipes cannot deadlock or grow with duration."""
    check_cancel(cancel)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [ffmpeg_path(), "-nostdin", "-hide_banner", "-y", *map(str, args)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
            **HIDDEN_PROCESS,
        )
        start = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() - start > timeout:
                    raise MediaError("视频处理超时，请尝试较短的视频")
                cancel.wait(0.1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        errors.seek(max(0, errors.tell() - 12000))
        output = errors.read().decode("utf-8", errors="replace")
        if process.returncode:
            raise MediaError("FFmpeg 无法处理此文件：\n" + output[-1800:])
        return output


def probe_video(source: Path, cancel: threading.Event) -> dict:
    # This avoids a second FFprobe binary. Decode one frame to validate the input.
    output = run_ffmpeg([
        "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-an", "-sn",
        "-f", "null", "-",
    ], cancel, timeout=90)
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    seconds = (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else None
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        seconds = None
    # Only inspect input streams, before FFmpeg's output/mapping section.
    inputs = output.split("Stream mapping:")[0]
    has_audio = bool(re.search(r"Stream #0:\d+[^\n]*Audio:", inputs))
    return {"duration": seconds, "has_audio": has_audio}


_indices = np.arange(32, dtype=np.float32)
_dct = np.cos(np.pi * (2 * _indices[None, :] + 1) * _indices[:, None] / 64)
_dct[0] *= 1 / math.sqrt(2)
_dct *= math.sqrt(2 / 32)


def perceptual_hash(gray: np.ndarray) -> np.ndarray:
    small = np.asarray(Image.fromarray(gray).resize((32, 32), Image.Resampling.BOX), dtype=np.float32)
    coefficients = (_dct @ small @ _dct.T)[:16, :16].reshape(-1)[1:]
    return coefficients > np.median(coefficients)


def differences(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    changed = delta > 24
    # A local change (e.g. one equation) should not disappear in a global average.
    tiles = changed.reshape(6, 30, 8, 40).mean(axis=(1, 3))
    return float(delta.mean()), float(changed.mean()), float(tiles.max())


def same_slide(a: np.ndarray, b: np.ndarray, ha: np.ndarray, hb: np.ndarray) -> bool:
    if np.count_nonzero(ha != hb) > 18:
        return False
    mad, fraction, tile = differences(a, b)
    return mad < 2.6 and fraction < 0.014 and tile < 0.20


def detect_pages(source: Path, metadata: dict, profile: str, cancel: threading.Event, progress: Progress) -> tuple[list[float], list[str]]:
    cfg = PROFILES[profile]
    fps = cfg["fps"]
    frame_bytes = THUMB_W * THUMB_H
    pages: list[float] = []
    thumbnails: list[np.ndarray] = []
    hashes: list[np.ndarray] = []
    warnings: list[str] = []
    anchor = latest = None
    run_start = last_t = 0.0
    count = 0
    last_report = 0.0
    duration = metadata.get("duration")

    def commit(gray, start, end, force=False):
        if gray is None or (not force and end - start + 1 / fps < cfg["stable"]):
            return
        if gray.mean() < 5 and gray.std() < 4:  # blank black intro / transition
            return
        phash = perceptual_hash(gray)
        if any(same_slide(gray, old, phash, old_hash) for old, old_hash in zip(thumbnails, hashes)):
            return
        if len(pages) >= MAX_PAGES:
            raise MediaError(f"检测到的页面超过 {MAX_PAGES} 张。请选择精简模式或截短视频后重试。")
        pages.append(round((start + end) / 2, 3))
        thumbnails.append(gray.copy())
        hashes.append(phash)

    # Uniform fps timestamps start at zero; retain the same timeline for seeks.
    filters = f"setpts=PTS-STARTPTS,fps={fps}:start_time=0:round=near,scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2,format=gray"
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
             "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
             "-vf", filters, "-threads", "1", "-f", "rawvideo", "pipe:1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, **HIDDEN_PROCESS,
        )
        finished = threading.Event()

        def watch_cancel():
            while not finished.wait(0.15):
                if cancel.is_set():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            while True:
                check_cancel(cancel)
                # BufferedReader.read(n) fills n unless EOF, even with split pipe writes.
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise MediaError("视频解码不完整，请检查视频文件")
                gray = np.frombuffer(raw, dtype=np.uint8).reshape(THUMB_H, THUMB_W)
                t = count / fps
                count += 1
                if anchor is None:
                    anchor = gray
                    run_start = t
                else:
                    mad, fraction, tile = differences(anchor, gray)
                    if mad >= cfg["mad"] or fraction >= cfg["fraction"] or tile >= 0.26:
                        commit(latest, run_start, last_t)
                        anchor, run_start = gray, t
                latest, last_t = gray, t
                if time.monotonic() - last_report > 0.4:
                    part = min(t / duration, 1) if duration else 0
                    progress("scanning", 8 + 55 * part, f"正在检测页面 · 已扫描 {int(t)} 秒，找到 {len(pages)} 页")
                    last_report = time.monotonic()
            check_cancel(cancel)
            if process.wait(timeout=30):
                errors.seek(0)
                raise MediaError("视频解码失败：" + errors.read().decode("utf-8", errors="replace")[-1200:])
            if not count:
                raise MediaError("视频中没有可读取的画面")
            commit(latest, run_start, last_t, force=count / fps < cfg["stable"])
        finally:
            finished.set()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            watcher.join(timeout=1)
    if not pages:
        # Honest, visible fallback for a movie / pure animation with no stable slides.
        end = max(0.0, last_t)
        interval = max(10.0, end / 199)
        pages = [round(t, 3) for t in np.arange(0, end + 0.001, interval)] or [0.0]
        warnings.append(f"未发现持续稳定的课件画面，已改为约每 {interval:.0f} 秒抽取一帧。")
    return pages, warnings


def create_pdf(images: list[Path], destination: Path, cancel: threading.Event):
    """One JPEG per page, preserve aspect ratio, embed JPEG without recompression."""
    pdf = canvas.Canvas(str(destination), pageCompression=1)
    pdf.setTitle("Extracted course slides")
    pdf.setAuthor("Local Video Learning Demo")
    for image in images:
        check_cancel(cancel)
        with Image.open(image) as source:
            width, height = source.size
        # 96 dpi equivalent; flexible page sizes also support portrait videos.
        page_w, page_h = width * 0.75, height * 0.75
        pdf.setPageSize((page_w, page_h))
        pdf.drawImage(str(image), 0, 0, width=page_w, height=page_h)
        pdf.showPage()
    pdf.save()


def process_video(source: Path, workdir: Path, profile: str, cancel: threading.Event, progress: Progress) -> dict:
    progress("probing", 3, "正在读取视频信息")
    metadata = probe_video(source, cancel)
    progress("scanning", 8, "正在扫描缩略图并检测稳定页面")
    times, warnings = detect_pages(source, metadata, profile, cancel, progress)
    images_dir = workdir / "slides"
    images_dir.mkdir(exist_ok=True)
    images = []
    pages = []
    for index, timestamp in enumerate(times, 1):
        check_cancel(cancel)
        if metadata["duration"]:
            timestamp = min(timestamp, max(0.0, metadata["duration"] - 0.05))
        image = images_dir / f"slide-{index:04d}.jpg"
        run_ffmpeg(["-loglevel", "error", "-ss", f"{timestamp:.3f}", "-i", str(source),
                    "-map", "0:v:0", "-frames:v", "1", "-q:v", "2", "-update", "1", str(image)], cancel, timeout=90)
        if not image.is_file() or image.stat().st_size == 0:
            raise MediaError(f"第 {index} 页提取失败，请检查视频时间轴")
        with Image.open(image) as decoded:
            decoded.verify()
        images.append(image)
        pages.append({"number": index, "timestamp": round(timestamp, 3), "filename": image.name})
        progress("extracting", 63 + 19 * index / len(times), f"正在提取清晰 JPG · {index} / {len(times)}")
    progress("pdf", 84, "正在将 JPG 合并为 PDF")
    create_pdf(images, workdir / "slides.pdf", cancel)
    with zipfile.ZipFile(workdir / "slides.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for image in images:
            check_cancel(cancel)
            archive.write(image, image.name)
    audio = None
    if metadata["has_audio"]:
        progress("audio", 91, "正在提取音频为 MP3")
        try:
            run_ffmpeg(["-loglevel", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                        "-c:a", "libmp3lame", "-b:a", "128k", str(workdir / "audio.mp3")], cancel)
            audio = "audio.mp3"
        except MediaError as error:
            warnings.append("课件已生成，但音频导出失败。" + str(error))
    else:
        warnings.append("这个视频没有音轨，已生成课件 PDF 和 JPG。")
    check_cancel(cancel)
    return {"metadata": metadata, "pages": pages, "audio": audio, "warnings": warnings,
            "pdf": "slides.pdf", "zip": "slides.zip"}
