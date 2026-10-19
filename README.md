# Video Workbench

English: [README.en.md](README.en.md)。

**Video Workbench 0.2.10 预览版**

[直接下载 Windows 64 位 EXE](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.10/video-workbench-v0.2.10-windows-x64.exe) · [下载源码包](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.10/video-workbench-v0.2.10-source.zip) · [所有版本](https://github.com/yaohaijiao86/video-workbench/releases)

## 项目目标

Video Workbench 是一个本地优先的视频整理与剪辑辅助工具。它把转录、剪辑建议、人工审核、导出和图文笔记放在同一套工作流程中，同时让视频、项目文件和模型服务由使用者自行管理。

## 功能

- 导入 MP4、MOV、MKV、WEBM 视频，也可导入文件夹并批量处理。
- 导入 SRT、WebVTT 字幕，或连接能输出带时间戳 JSON 的 ASR 命令。
- 可选下载 Bilibili 视频和字幕，并通过二维码登录。
- 在浏览器预览视频，按时间戳查看和校对转录文本。
- 生成口头语、重复内容等剪辑建议；由使用者审核和调整后再导出。
- 使用 FFmpeg 导出剪辑视频和同步调整时间轴的 SRT 字幕。
- 输出 HTML 和 Markdown 格式的主题摘要与全文转录；可选连接视觉模型、OCR 和场景检测工具。
- 支持单个项目或批次处理，并显示处理进度。

核心应用使用 Python 标准库。Bilibili 下载和二维码登录需要可选的 `yt-dlp` 与 `qrcode`。FFmpeg、ASR、OCR、场景检测、模型服务和模型文件需由使用者自行准备。

## 下载

- **Windows 64 位执行文件：**[点击直接下载 EXE](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.10/video-workbench-v0.2.10-windows-x64.exe)
- **源码包：**[video-workbench-v0.2.10-source.zip](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.10/video-workbench-v0.2.10-source.zip)
- **外部工具安装与配置：**[查看中文部署说明](docs/external-tools.md)

执行文件包含 Video Workbench 本身，不包含 FFmpeg、ASR/OCR 工具、场景检测器、模型文件、账号凭据或运行数据。

## Qwen3-ASR 命令模板

设置页的 ASR 命令数组已预置通用 Qwen3-ASR 模板；EXE 内含适配脚本，但不包含 Qwen 推理依赖或模型权重。请在独立 Python 环境中按[官方说明](https://github.com/QwenLM/Qwen3-ASR)安装 `qwen-asr`，再把设置页数组第一项 `python` 改成该环境的 `python.exe` 完整路径。模型字段留空时使用 `Qwen/Qwen3-ASR-1.7B` 与 `Qwen/Qwen3-ForcedAligner-0.6B` 官方模型 ID；也可以填写本机已有的模型目录。

模板示例：

```json
["python", "{asr_adapter}", "--input", "{audio}", "--output", "{output}", "--model", "{asr_model}", "--aligner", "{asr_aligner}"]
```

## 从源码运行

源码运行需要 Python 3.11 或更高版本和现代浏览器。视频导出需要 FFmpeg；转录可导入字幕，或安装 `qwen-asr` 后使用预置命令模板。

在项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run.py
```

然后在浏览器打开 `http://127.0.0.1:8765`。如需启用 Bilibili 下载与二维码登录，在相同 Python 环境安装可选依赖：

```powershell
python -m pip install -e ".[bilibili]"
```

## 局域网部署

默认只监听本机回环接口。如需在可信局域网内使用，请为服务器地址、密码文件位置和允许访问的客户端网段填写你自己的值：

```powershell
python run.py --host SERVER_PRIVATE_IP --port 8765 --password-file PASSWORD_FILE --allowed-client-cidr TRUSTED_CLIENT_CIDR --data DATA_DIRECTORY
```

请将防火墙入站范围限制在可信网段，不要把 HTTP 服务直接暴露到公网。密码文件、运行数据、模型设置、API 凭据和 ASR token 文件应放在源码目录之外。远程 ASR 部署方式见 [Windows 远程 ASR 说明](docs/remote-asr-windows.md)。

## 生成源码包和 Windows 执行文件

在 64 位 Windows 和 Python 3.11 或更高版本上运行：

```powershell
python -m pip install -e ".[build,bilibili]"
python build_release.py
```

脚本会在项目父目录生成源码 ZIP 和单文件 Windows x64 执行文件。可通过 `--output-dir` 指定其他输出目录。

## 项目结构

- `src/video_workbench/`：应用、媒体工作流、服务连接和浏览器界面。
- `examples/`：ASR、OCR 和场景检测适配示例。
- `docs/`：部署说明。
- `tests/`：自动化检查。
- `build_release.py`：源码包与 Windows 执行文件构建脚本。

## 数据与安全

项目和运行设置保存在你选择的数据或输出目录中。请勿将真实凭据、token 文件、服务器地址或部署设置提交到仓库。模型请求可能包含转录文本，以及在启用相关功能时选取的视频帧；请只使用你信任的服务。剪辑建议应由人工审核。

项目使用 MIT 许可证。外部工具和模型遵循各自的许可协议。
