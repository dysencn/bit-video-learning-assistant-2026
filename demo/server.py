from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.request
import zipfile
import webbrowser
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
GENERATED_DIR = DATA_DIR / "generated"
STATE_FILE = DATA_DIR / "state.json"
for directory in (UPLOAD_DIR, GENERATED_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    value = Path(value or "upload.bin").name
    value = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", value)
    return value[:100] or "upload.bin"


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def ppt_text_box(text: str, x: int, y: int, width: int, height: int, size: int, bold: bool = False) -> str:
    lines = "".join(
        f'<a:p><a:r><a:rPr lang="zh-CN" sz="{size * 100}" b="{"1" if bold else "0"}"/><a:t>{xml_escape(line)}</a:t></a:r><a:endParaRPr lang="zh-CN"/></a:p>'
        for line in text.splitlines()
    )
    return f'''<p:sp><p:nvSpPr><p:cNvPr id="{abs(hash((text, x, y))) % 100000 + 10}" name="text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{lines}</p:txBody></p:sp>'''


def make_pptx(path: Path, title: str, summary: str, quiz: list[dict]) -> None:
    """Create a small, editable OOXML deck with three text slides."""
    ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    slides = [
        (title, "智慧课程平台\n视频转换原型"),
        ("课程要点", summary),
        ("课后练习", "\n".join(f"{i + 1}. {item['question']}" for i, item in enumerate(quiz))),
    ]
    presentation = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{ns}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>{''.join(f'<p:sldId id="{255 + i}" r:id="rId{i + 2}"/>' for i in range(len(slides)))}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle/></p:presentation>'''
    master = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{ns}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/></p:sldMaster>'''
    layout = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{ns}" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'''
    theme = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="智慧课程平台"><a:themeElements><a:clrScheme name="Default"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:accent1><a:srgbClr val="006B3C"/></a:accent1><a:accent2><a:srgbClr val="B14A0B"/></a:accent2><a:accent3><a:srgbClr val="2F6AB3"/></a:accent3><a:accent4><a:srgbClr val="E8B04A"/></a:accent4><a:accent5><a:srgbClr val="7F7F7F"/></a:accent5><a:accent6><a:srgbClr val="C0504D"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Default"><a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface="Microsoft YaHei"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/><a:ea typeface="Microsoft YaHei"/></a:minorFont></a:fontScheme><a:fmtScheme name="Default"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>''' + "".join(f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(len(slides))) + "</Types>"
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>'''
    pres_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>''' + "".join(f'<Relationship Id="rId{i + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i + 1}.xml"/>' for i in range(len(slides))) + "</Relationships>"
    master_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'''
    layout_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        entries = {
            "[Content_Types].xml": content_types,
            "_rels/.rels": rels,
            "ppt/presentation.xml": presentation,
            "ppt/_rels/presentation.xml.rels": pres_rels,
            "ppt/slideMasters/slideMaster1.xml": master,
            "ppt/slideMasters/_rels/slideMaster1.xml.rels": master_rels,
            "ppt/slideLayouts/slideLayout1.xml": layout,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": layout_rels,
            "ppt/theme/theme1.xml": theme,
            "docProps/core.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">{xml_escape(title)}</dc:title><dcterms:created xmlns:dcterms="http://purl.org/dc/terms/"/></cp:coreProperties>''',
        }
        for name, content in entries.items():
            archive.writestr(name, content)
        for index, (slide_title, body) in enumerate(slides, start=1):
            body_size = 24 if index == 1 else 20
            slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{ns}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{ppt_text_box(slide_title, 700000, 500000, 10800000, 900000, 32, True)}{ppt_text_box(body, 900000, 1600000, 10400000, 4300000, body_size)}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'''
            archive.writestr(f"ppt/slides/slide{index}.xml", slide_xml)
            archive.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>''')


COURSES = [
    {"id": "course-001", "title": "大数据分析基础", "teacher": "李老师", "progress": 42, "status": "已发布", "tag": "数据科学"},
    {"id": "course-002", "title": "Python 程序设计", "teacher": "周老师", "progress": 68, "status": "已发布", "tag": "编程基础"},
    {"id": "course-003", "title": "人工智能导论", "teacher": "王老师", "progress": 15, "status": "草稿预览", "tag": "AI 基础"},
]


def openai_answer(question: str) -> tuple[str, str, bool, str | None]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return (
            "MapReduce 把大规模数据处理拆成 Map 和 Reduce 两个阶段。Map 生成键值对，Reduce 按键分组并聚合结果。当前未配置 API key，这是本地模拟回答。",
            "mock",
            True,
            None,
        )
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    payload = json.dumps({"model": model, "messages": [{"role": "system", "content": "你是智慧课程平台的课程助教，请用中文简明回答。"}, {"role": "user", "content": question}], "temperature": 0.2}).encode("utf-8")
    request = urllib.request.Request(f"{base}/chat/completions", data=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = result["choices"][0]["message"]["content"]
        return answer, "live", False, None
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        return "AI 服务暂时不可用。已切换到本地课程知识：MapReduce 由 Map 和 Reduce 两个阶段组成。", "fallback", True, str(exc)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self.send_json({"status": "ok", "service": "smart-course-demo", "time": now()})
            return
        if path == "/api/courses":
            self.send_json({"courses": COURSES})
            return
        if path == "/api/state":
            state = read_json(STATE_FILE, {"user": None, "progress": {}, "last_job": None, "ai_logs": []})
            self.send_json(state)
            return
        if path.startswith("/files/"):
            relative = Path(path.removeprefix("/files/")).as_posix()
            target = (DATA_DIR / relative).resolve()
            if DATA_DIR.resolve() not in target.parents or not target.is_file():
                self.send_error(404, "file not found")
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def json_body(self) -> dict:
        try:
            return json.loads(self.read_body().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/login":
            payload = self.json_body()
            role = payload.get("role", "student")
            role_name = {"student": "学生", "teacher": "教师", "admin": "管理员"}.get(role, "学生")
            state = read_json(STATE_FILE, {"user": None, "progress": {}, "last_job": None, "ai_logs": []})
            state["user"] = {"id": f"demo-{role}", "name": f"演示{role_name}", "role": role, "role_name": role_name}
            write_json(STATE_FILE, state)
            self.send_json({"ok": True, "user": state["user"]})
            return
        if path == "/api/quiz/submit":
            payload = self.json_body()
            expected = ["B", "B", "C"]
            answers = payload.get("answers", [])
            score = sum(a == b for a, b in zip(answers, expected))
            result = {"score": score, "total": len(expected), "percent": round(score / len(expected) * 100), "feedback": ["第 1 题：Map 阶段负责生成键值对。", "第 2 题：Reduce 阶段按键分组并聚合。", "第 3 题：分而治之有助于并行处理。"]}
            self.send_json(result)
            return
        if path == "/api/progress":
            payload = self.json_body()
            state = read_json(STATE_FILE, {"user": None, "progress": {}, "last_job": None, "ai_logs": []})
            state.setdefault("progress", {})[payload.get("course_id", "course-001")] = {"percent": int(payload.get("percent", 0)), "updated_at": now()}
            write_json(STATE_FILE, state)
            self.send_json({"ok": True, "progress": state["progress"]})
            return
        if path == "/api/ai/ask":
            payload = self.json_body()
            question = str(payload.get("question", "")).strip()
            if not question:
                self.send_json({"error": "请输入问题"}, 400)
                return
            started = time.perf_counter()
            answer, provider, fallback, error = openai_answer(question)
            latency = round((time.perf_counter() - started) * 1000)
            state = read_json(STATE_FILE, {"user": None, "progress": {}, "last_job": None, "ai_logs": []})
            state.setdefault("ai_logs", []).append({"request_id": str(uuid.uuid4()), "provider": provider, "latency_ms": latency, "status": "ok" if not error else "degraded", "fallback": fallback, "error": error, "created_at": now()})
            state["ai_logs"] = state["ai_logs"][-20:]
            write_json(STATE_FILE, state)
            self.send_json({"answer": answer, "provider": provider, "fallback": fallback, "latency_ms": latency, "error": error})
            return
        if path == "/api/video":
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if "multipart/form-data" not in content_type:
                self.send_json({"error": "请使用 multipart/form-data 上传视频或演示文件"}, 400)
                return
            message = BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
            file_part = next((part for part in message.walk() if part.get_filename()), None)
            if file_part is None:
                self.send_json({"error": "没有找到上传文件"}, 400)
                return
            job_id = uuid.uuid4().hex[:10]
            original_name = safe_name(file_part.get_filename())
            upload_path = UPLOAD_DIR / f"{job_id}_{original_name}"
            upload_path.write_bytes(file_part.get_payload(decode=True) or b"")
            lesson_title = Path(original_name).stem or "未命名课程"
            job_dir = GENERATED_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            summary = f"# {lesson_title}\n\n## 本次生成\n\n- 输入文件：{original_name}\n- 处理状态：已完成（第 2 周本地原型）\n- 生成时间：{now()}\n\n## 核心要点\n\n1. Map 阶段读取输入并生成键值对。\n2. 系统按键分组中间结果，Reduce 阶段执行聚合。\n3. 并行处理与任务重试提升大规模数据处理的效率和容错性。\n\n## 学习目标\n\n能够解释 Map 与 Reduce 的职责，并描述一次词频统计的处理过程。\n"
            (job_dir / "summary.md").write_text(summary, encoding="utf-8")
            quiz = [{"question": "Map 阶段的主要输出是什么？", "options": ["网页", "键值对", "数据库表", "图片"], "answer": "B"}, {"question": "Reduce 阶段通常做什么？", "options": ["采集视频", "按键分组并聚合", "创建用户", "发布课程"], "answer": "B"}, {"question": "MapReduce 的价值更接近哪一项？", "options": ["只支持单机", "取消容错", "支持分而治之的并行处理", "只生成 PPT"], "answer": "C"}]
            write_json(job_dir / "quiz.json", {"title": lesson_title, "questions": quiz})
            make_pptx(job_dir / "lesson.pptx", lesson_title, "Map 阶段生成键值对\nReduce 阶段分组聚合\n并行处理提升效率\n任务重试提高容错", quiz)
            state = read_json(STATE_FILE, {"user": None, "progress": {}, "last_job": None, "ai_logs": []})
            state["last_job"] = {"id": job_id, "title": lesson_title, "created_at": now(), "status": "completed"}
            write_json(STATE_FILE, state)
            self.send_json({"ok": True, "job_id": job_id, "status": "completed", "artifacts": [{"name": "总结文档", "path": f"/files/generated/{job_id}/summary.md"}, {"name": "题目 JSON", "path": f"/files/generated/{job_id}/quiz.json"}, {"name": "课程 PPTX", "path": f"/files/generated/{job_id}/lesson.pptx"}]})
            return
        self.send_json({"error": "unknown endpoint"}, 404)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Smart Course Platform demo running at {url}")
    if os.environ.get("OPEN_BROWSER", "").lower() in {"1", "true", "yes"}:
        # The server is already bound before opening the page, so the browser does not race startup.
        threading.Timer(0.6, lambda: webbrowser.open_new_tab(url)).start()
        print("Opening the demo page in your default browser...")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

