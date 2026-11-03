# 讯飞录音转写实验

这是独立的 Python 实验，尚未接入网页按钮、账号任务列表或课程资源。调用项目现有的 `demo.media.process_video`，从视频生成课件和 MP3，再通过同一模块的 FFmpeg 工具转为 16kHz、16bit、单声道 WAV，提交讯飞极速录音转写接口。

官方文档：<https://www.xfyun.cn/doc/asr/speedTranscription/API.html>

## 真实实验结果（2026-11-03）

- 输入：工作区根目录的 `example.mp4`。
- 音频：412.549 秒（约 6 分 53 秒），WAV 文件 13,201,632 字节。
- 提取：复用项目完整流程，生成 9 张 JPG、PDF、ZIP 和 MP3。
- 讯飞：文件上传、创建任务均返回 HTTP 200、业务码 0；第 3 次查询返回 `task_status=4`，任务完成。
- 文字稿：41 个分段，2,064 个字符（包含换行）；同时导出分句时间戳和 SRT 字幕。
- 本次本机端到端耗时：16.573 秒，包含视频处理、上传、转写和轮询；不是服务性能保证。
- 任务 ID：`261103200335151489588747`。
- 结果：`../.demo-test/xfyun-asr-20261103/`。此目录被 Git 忽略，保存在本机，不随代码推送。

文字稿仅由讯飞返回内容拼接而成，没有使用其他模型生成、补写或润色。实际识别准确率尚未逐字对照人工稿评估。

## 复现

在 `code` 目录使用项目已有虚拟环境运行。先在当前 PowerShell 进程设置下面三个环境变量，用自己的实际凭据替换占位内容；不要把实际密钥保存到代码或提交 Git。

```powershell
$env:XFYUN_APP_ID = "你的 APPID"
$env:XFYUN_API_KEY = "你的 APIKey"
$env:XFYUN_API_SECRET = "你的 APISecret"
& .\.venv\Scripts\python.exe -B experiments/xfyun_speed_transcription.py ../../example.mp4
```

默认创建独立的 `.demo-test/xfyun-asr-年月日-时分秒/` 目录。也可以指定 `--output`，目标目录必须尚不存在，防止覆盖旧结果。每次重新运行会提交新的真实转写任务，可能消耗服务额度。

凭据即使在控制台标为“Websocket 服务接口认证信息”，本实验实际调用的仍是文档指定的 HTTPS 文件转写接口。是否可用取决于该应用的实际服务权限，本次提供的凭据已通过真实调用验证。

## 产物与失败处理

| 文件 | 内容 |
| --- | --- |
| `audio.mp3` | 项目原有流程提取的录音 |
| `audio-16k.wav` | 提交给讯飞的标准化录音 |
| `transcript.txt` | UTF-8 BOM 文字稿，便于 Windows 查看 |
| `transcript.srt` | 使用讯飞分句时间戳生成的字幕 |
| `segments.json` | 分段文字及起止毫秒数 |
| `query-*.json` | 讯飞原始识别响应，包括词级信息 |
| `report.json` | 成败、阶段、任务 ID、音频属性和耗时 |
| `upload.json`、`create-task.json` | 上传和创建任务的响应；音频下载 URL 已隐藏 |

每次请求重新计算 GMT 时间和 HMAC-SHA256 签名；不记录认证请求头或密钥。上传使用文档中的固定空摘要，JSON 任务请求签名使用实际请求体摘要。仅通过 HTTPS 调用官方接口，不自动跟随重定向。

网络失败、认证失败、业务错误或空识别结果会记录在 `report.json` 中，并以非零状态退出，不生成替代文字稿。提交成功后每 3 秒查询，默认等待上限 600 秒，可通过 `--max-wait` 调整；等待超时会保留任务 ID，不自动再次创建收费任务。
