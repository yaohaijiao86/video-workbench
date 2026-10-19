# 外部软件下载与配置

本程序不打包第三方软件、推理依赖或模型权重。可从 [Release 页面下载当前 EXE](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.11/video-workbench-v0.2.11-windows-x64.exe)，也可下载[源码与适配器](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.11/video-workbench-v0.2.11-source.zip)。EXE 内含 Qwen3-ASR 适配脚本；OCR、faster-whisper、PySceneDetect 等示例适配器放在源码包的 `examples` 目录。

## 推荐目录

先在磁盘上创建一个长期使用的根目录，例如 `D:\VideoWorkbench`。下面是建议布局；路径可按你的磁盘空间调整，重要的是使用固定位置并在设置中填完整路径。

```text
D:\VideoWorkbench\
├─ tools\ffmpeg\bin\ffmpeg.exe
├─ tools\ffmpeg\bin\ffprobe.exe
├─ envs\qwen3-asr\Scripts\python.exe
├─ envs\faster-whisper\Scripts\python.exe       # 可选
├─ envs\paddleocr\Scripts\python.exe             # 可选
├─ envs\pyscenedetect\Scripts\python.exe          # 可选
├─ models\qwen3-asr\Qwen3-ASR-1.7B\
├─ models\qwen3-asr\Qwen3-ForcedAligner-0.6B\
├─ models\llm\                                  # LM Studio 等本地模型，可选
├─ models\paddleocr\                             # OCR 模型缓存，可选
├─ adapters\video-workbench\examples\           # 从源码包解压，可选
└─ data\                                          # 项目和导出文件
```

单文件 EXE 运行时会在临时目录解包应用自身。不要把外部软件、Python 环境或模型放进临时解包目录；也不要放在仓库的 `.git`、`src` 或 `build` 目录。程序设置中的目录选择器或命令字段应指向上面稳定的路径。

## 视频处理：FFmpeg / ffprobe（必需）

1. 打开 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)，进入 Windows 构建入口；官方页面提供 [gyan.dev Windows builds](https://www.gyan.dev/ffmpeg/builds/) 和 [BtbN Windows builds](https://github.com/BtbN/FFmpeg-Builds/releases)。下载 Windows x64 构建包并解压到 `D:\VideoWorkbench\tools\ffmpeg`。若压缩包内多一层版本目录，可将它重命名为 `ffmpeg`，最终确认 `bin` 目录中能看到 `ffmpeg.exe` 和 `ffprobe.exe`。
2. 打开 Video Workbench「设置 → 外部程序」，填写：
   - FFmpeg：`D:\VideoWorkbench\tools\ffmpeg\bin\ffmpeg.exe`
   - ffprobe：`D:\VideoWorkbench\tools\ffmpeg\bin\ffprobe.exe`
3. 在 PowerShell 运行 `& 'D:\VideoWorkbench\tools\ffmpeg\bin\ffmpeg.exe' -version`。设置完整路径后不必另行修改系统 PATH。

CPU x264 编码会逐帧处理。若 FFmpeg 构建含 NVENC 且 NVIDIA 驱动可用，可在「精剪视频输出」选 NVIDIA GPU；先用短片比较速度和画质。

## 中文语音识别：Qwen3-ASR（推荐）

从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/)安装 Python 3.12（Python 本体可保留安装器默认位置，不要手动搬动 `python.exe`），再参照 [Qwen3-ASR 官方仓库](https://github.com/QwenLM/Qwen3-ASR)建立单独环境。虚拟环境放在 `D:\VideoWorkbench\envs\qwen3-asr`；模型可自动下载，也可按官方说明使用 ModelScope 下载到你选择的固定目录。

```powershell
py -3.12 -m venv D:\VideoWorkbench\envs\qwen3-asr
& 'D:\VideoWorkbench\envs\qwen3-asr\Scripts\python.exe' -m pip install -U qwen-asr modelscope
& 'D:\VideoWorkbench\envs\qwen3-asr\Scripts\modelscope.exe' download --model Qwen/Qwen3-ASR-1.7B --local_dir D:\VideoWorkbench\models\qwen3-asr\Qwen3-ASR-1.7B
& 'D:\VideoWorkbench\envs\qwen3-asr\Scripts\modelscope.exe' download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir D:\VideoWorkbench\models\qwen3-asr\Qwen3-ForcedAligner-0.6B
```

在「设置 → 语音识别模型」中，把预置命令数组第一项 `python` 改为 `D:\VideoWorkbench\envs\qwen3-asr\Scripts\python.exe`。如果已手动下载权重，将两个模型文件夹分别填入 ASR 和 ForcedAligner 路径框；如果留空，程序会使用官方模型 ID 并让 qwen-asr 自行下载。适配器和权重都不需要复制到 EXE 旁边。4090D 等 GPU 若在另一台电脑，可按[双机 ASR 部署说明](remote-asr-windows.md)把环境和模型放在 GPU 服务器上。

## 语音识别备选：faster-whisper / FunASR

- [faster-whisper 官方仓库](https://github.com/SYSTRAN/faster-whisper)：在 `D:\VideoWorkbench\envs\faster-whisper` 建独立环境并安装 `faster-whisper`。示例适配器位于源码包 `examples\asr_faster_whisper.py`；可把源码包解压到 `D:\VideoWorkbench\adapters\video-workbench`，然后在 ASR 命令数组中用该环境的 `python.exe` 和该脚本的完整路径。模型首次加载时可由框架下载；也可用模型目录替换示例命令中的模型 ID。
- [FunASR 官方安装说明](https://github.com/modelscope/FunASR)：单独放入 `D:\VideoWorkbench\envs\funasr` 环境。先通过 [PyTorch 官方选择器](https://pytorch.org/get-started/locally/)安装与设备匹配的 PyTorch/torchaudio，再安装 FunASR。模型缓存或手动下载目录建议放在 `D:\VideoWorkbench\models\funasr`；接入前需要一个输出本程序时间戳 JSON 格式的适配器。

不要把 Qwen、faster-whisper 和 FunASR 依赖全部装进同一个环境；深度学习依赖之间可能冲突。可用 10–20 分钟真实素材比较字错率和切点误差。

## 剪辑建议与章节总结：本地模型或在线 API

- 本地桌面方案可从 [LM Studio 官方下载页](https://lmstudio.ai/download)安装 LM Studio。程序本身可安装在默认应用目录；模型在 LM Studio「My Models」中管理，建议将模型根目录改为 `D:\VideoWorkbench\models\llm`，不要手动把单个权重散放在程序安装目录。LM Studio 的模型目录结构和导入方式见[官方说明](https://lmstudio.ai/docs/app/advanced/import-model)。
- 也可使用其他提供 OpenAI 兼容 Chat Completions 接口的本地/局域网推理服务；推理软件和模型权重放在实际运行推理的那台电脑，客户端只填写服务地址及模型 ID。
- 在线服务不需要下载模型到本机；在「设置 → 模型服务配置」填写服务商 HTTPS API 地址、模型 ID 和 API Key。API Key 仅在本机设置中填写，不要写进公开 README、源码或命令示例。

程序通过所选服务接收字幕文本；启用视觉分析时还会发送抽取的截图。请按自己的隐私要求选择服务商并人工复核输出。

## 视觉理解：Qwen3-VL（可选）

从 [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)选择适合显存的模型和部署方式；例如可查看 [Qwen 官方 Hugging Face 模型页](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)。推理服务若运行在另一台 GPU 服务器，仓库和推理环境都放在服务器上；模型权重建议放在服务器的 `D:\VideoWorkbench\models\qwen3-vl`（Linux 服务器可改为 `/data/models/qwen3-vl`）。客户端 EXE 不需要保存这些权重，只需配置服务器提供的兼容图片输入的 Chat Completions 地址和视觉模型 ID。先用一张图片验证接口，再开启逐章抽帧分析。

## 画面文字：PaddleOCR（可选）

按 [PaddleOCR 官方安装文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/installation.md)和 [PaddlePaddle 官方安装选择器](https://www.paddlepaddle.org.cn/install/quick)为系统、Python 和 CPU/GPU 选匹配版本。建议单独建立 `D:\VideoWorkbench\envs\paddleocr`，OCR 模型缓存放在 `D:\VideoWorkbench\models\paddleocr`。示例适配器 `examples\ocr_paddle.py` 在源码包中；该脚本支持 `--cache-dir D:\VideoWorkbench\models\paddleocr`。在「设置 → 外部程序」中配置该环境的 `python.exe`、适配器脚本完整路径和图片/JSON 参数。

幻灯片、屏幕录制适合开启 OCR；纯口播收益低。先用单张图片确认 JSON 输出正常。

## 镜头检测：PySceneDetect / TransNetV2（可选）

- [PySceneDetect 官方下载页](https://www.scenedetect.com/download/)提供 Windows 安装包及 Python 安装方式。若希望独立管理，建立 `D:\VideoWorkbench\envs\pyscenedetect`，安装 `scenedetect`；它依赖 FFmpeg 做视频拆分。把源码包的 `examples\scene_pyscenedetect.py` 放在适配器目录，命令数组使用该环境 `python.exe`、脚本完整路径，以及 `--video {video} --output {output}` 参数。
- [TransNetV2 官方仓库](https://github.com/soCzech/TransNetV2)的代码可放在 `D:\VideoWorkbench\tools\TransNetV2`，权重放在 `D:\VideoWorkbench\models\TransNetV2`。它需自行配置 Python 依赖和适配命令，并输出 `{"times":[12.5,25.0]}` 形式的场景时间点 JSON。

运动镜头可先尝试 PySceneDetect AdaptiveDetector，再用真实素材核对漏检和误检；固定机位口播可以跳过。场景切点只用于选择截图，不自动决定删除内容。

## 项目、模型和运行数据

建议将项目输出路径设置为 `D:\VideoWorkbench\data`，模型、Python 环境、API 密钥文件及双机 ASR token 则分别放在独立目录，不要放进 Git 仓库或源码包。局域网服务器只从可信网络访问，不要直接暴露到公网。首次先处理短视频，确认依赖、模型路径、适配器 JSON 和导出正常，再处理长素材或批次。
