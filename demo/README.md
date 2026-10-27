# 第 2 周智慧课程平台 demo

这是一个零外部依赖的本地原型，用于展示第 2 周的需求确认、整体流程和接口可行性。服务端使用 Python 标准库，启动脚本会在项目目录内创建 `.venv`，不会改动系统 Python。

启动脚本会先检查 `demo/.venv/Scripts/python.exe`。如果虚拟环境已存在，就直接复用并跳过创建和依赖下载；只有首次创建虚拟环境且目录中存在 `requirements.txt` 时，才会执行依赖安装，安装源固定为清华大学 PyPI 镜像：`https://pypi.tuna.tsinghua.edu.cn/simple`。当前 demo 没有外部依赖，因此不会下载第三方包。

## 启动

PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\run_demo.ps1
```

Windows 命令提示符：

```bat
run_demo.bat
```

浏览器打开 <http://127.0.0.1:8000>。停止服务按 `Ctrl+C`。

运行 `run_demo.bat` 或 `run_demo.ps1` 后，服务启动完成会自动打开默认浏览器并进入上述地址，无需手工输入。

## 演示路径

1. 点击“登录”，选择学生角色并查看课程列表。
2. 在“视频转课程”选择 `sample-lecture.txt`，点击“开始转换”。页面会返回任务 ID、总结、题目和 PPTX 下载链接。
3. 在“练习与进度”提交答案，查看分数和保存后的进度。
4. 在“AI 助教”输入“MapReduce 的核心思想是什么？”。没有 API key 时显示 `mock`；配置 `OPENAI_API_KEY` 后会尝试调用 OpenAI 兼容的 Chat Completions 接口。

## 可选 AI 配置

PowerShell 示例：

```powershell
$env:OPENAI_API_KEY = "你的密钥"
$env:OPENAI_MODEL = "gpt-4o-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
.\run_demo.ps1
```

调用失败时服务会返回本地降级回答，并在响应中标记 `fallback: true`，便于课堂演示和验收。

## API 入口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/courses` | 课程列表 |
| GET | `/api/state` | 当前 demo 状态 |
| POST | `/api/login` | 模拟登录 |
| POST | `/api/video` | 上传并生成三类课程产物 |
| POST | `/api/quiz/submit` | 提交练习并评分 |
| POST | `/api/progress` | 保存学习进度 |
| POST | `/api/ai/ask` | AI 助教，支持真实 API 或本地降级 |

## 目录说明

- `server.py`：本地服务端与最小 PPTX 生成器。
- `web/`：原生前端页面。
- `sample-lecture.txt`：可直接上传的演示输入。
- `data/`：运行时生成，包含上传文件、课程产物和进度文件。

