# 外部软件下载与配置

本程序不打包下列外部软件、模型权重或服务。Windows 64 位程序可[直接下载 EXE](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.5/video-workbench-v0.2.5-windows-x64.exe)；也可在[所有版本页面](https://github.com/yaohaijiao86/video-workbench/releases)下载源码包。EXE 内含 Qwen3-ASR 适配脚本和通用命令模板，但不含 Qwen 推理依赖或模型权重；其他外部软件仍需按需配置。

建议为不同模型准备独立 Python 环境。显存有限时不要同时加载多个大模型；模型效果、识别错误和切点质量应使用你自己的真实视频样本评估。

## 视频与媒体探测：FFmpeg / ffprobe（导出必需）

- 从 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)选择适用于 Windows 的构建包，并在程序设置中填写 `ffmpeg.exe` 的完整路径。
- 可运行 `ffmpeg -version` 验证安装。若找不到 `ffprobe`，程序会尝试用 FFmpeg 读取常见视频的时长和音轨信息；复杂封装建议同时配置同一构建包中的 `ffprobe.exe`。
- CPU x264 编码需要逐帧处理。可在“精剪视频输出”中选择较快的 x264 预设；若所用 FFmpeg 构建包含 NVENC 且 NVIDIA 驱动可用，也可选择 NVIDIA GPU 编码。建议先用短片比较速度和画质。

## 语音识别与对齐：Qwen3-ASR（中文优先）

请参照 [Qwen3-ASR 官方仓库](https://github.com/QwenLM/Qwen3-ASR)准备受支持的 Python、推理依赖和模型文件。建议在独立环境安装：

```powershell
python -m pip install -U qwen-asr
```

安装 `qwen-asr` 后，设置页已预置以下通用命令并随 EXE 附带适配脚本。若使用独立环境，将 `python` 替换成该环境的 Python 可执行文件完整路径；模型标识可使用默认官方 ID，也可填本地模型目录：

```json
["python", "{asr_adapter}", "--input", "{audio}", "--output", "{output}", "--model", "{asr_model}", "--aligner", "{asr_aligner}"]
```

适配器支持传入模型标识或本地模型目录。优先中文、方言和字词级时间戳时，可先评估 1.7B 模型加对齐器；较小模型资源需求更低。长音频处理方式和安装限制请以官方文档为准。

### 将 ASR 放在另一台局域网电脑

可在有 GPU 的服务器上运行 `examples/asr_qwen3_server.py`，在工作电脑上调用 `examples/asr_remote_client.py`。具体占位符命令见[Windows 双机 ASR 部署说明](remote-asr-windows.md)。两端需要使用同一份强随机 token 文件；将 token 与模型目录放在仓库之外，只允许可信局域网或 VPN 访问服务端口，不要将该 HTTP 服务直接暴露到公网。

## ASR 备选：faster-whisper / FunASR

- [faster-whisper 官方仓库](https://github.com/SYSTRAN/faster-whisper)：独立环境安装后可使用 `examples/asr_faster_whisper.py`。多语种识别可评估 large-v3；更重视速度时可评估 turbo。请按官方说明选择与设备匹配的运行依赖。
- [FunASR 官方仓库](https://github.com/modelscope/FunASR)：可评估 SenseVoice 或 Paraformer。要接入本程序，适配命令需输出带时间戳的 JSON；字段格式可参考仓库中的 ASR 示例。

建议用 10–20 分钟真实素材比较字错率和切点误差。口音、噪声和录音条件不同，模型表现也会变化。

## 语义判断与总结：本地或在线模型

剪辑建议、章节总结等 AI 功能需要可调用的模型服务。可配置本地/局域网模型，或配置服务商提供的 HTTPS OpenAI 兼容 API 地址和 API Key；扫描模型后，在“模型配置优先级”中选择要启用的模型，再将可调用模型加入任务列表。API Key 仅填写在本机程序设置中，不要写进 README、命令示例或仓库文件。

字幕文本会发送给所选模型服务；启用画面识别时，抽取的视频截图也会发送给视觉模型服务。请根据自己的隐私要求选择服务商，并人工核对数字、否定、专有名词和重复句。删除建议默认以删除线显示，应在字幕框复核后再导出。

运行本地或局域网任务前，确认模型所在机器和推理服务在线，并确认模型已加载或服务支持按需加载。自建网关模式会在任务结束时通过管理接口停止推理引擎；如果同机有其他应用共用该引擎，这可能影响它们。其他推理服务请选择手动管理，并在服务端按需卸载模型或停止引擎以释放内存和显存。请只使用中文理解和 JSON 输出表现稳定的模型。

## 视觉理解：Qwen3-VL（可选）

参照 [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)部署视觉推理服务。选择适合显存的模型版本，并确认服务提供兼容图片输入的 Chat Completions 接口；在程序中填写视觉模型 ID 后，先用一张图片验证请求。较大的模型可能更稳定，但通常也更慢、占用更多资源。本程序逐章抽取画面辅助分析，单帧不能代表整个视频片段。

## 画面文字识别：PaddleOCR（可选）

参照 [PaddleOCR 官方仓库](https://github.com/PaddlePaddle/PaddleOCR)安装与当前平台匹配的 PaddlePaddle 和 `paddleocr`。OCR 命令调用示例：

```powershell
python examples/ocr_paddle.py --image {image} --output {output}
```

完整 JSON 输出示例见仓库中的适配器说明。幻灯片或屏幕录制适合开启 OCR；纯口播通常收益较低。识别小字时，专用 OCR 可能比通用视觉模型更合适。

## 镜头检测：PySceneDetect / TransNetV2（可选）

- [PySceneDetect 官方仓库](https://github.com/Breakthrough/PySceneDetect)：在独立环境安装 `scenedetect`，并配置 FFmpeg。适配器调用示例：

  ```powershell
  python examples/scene_pyscenedetect.py --video {video} --output {output}
  ```

- [TransNetV2 官方仓库](https://github.com/soCzech/TransNetV2)可作为神经网络方案；接入时需适配场景时间点 JSON 协议，例如 `{"times":[12.5,25.0]}`。

运动镜头可先尝试 PySceneDetect 的 AdaptiveDetector，再用真实素材核对漏检和误检。固定机位口播可以跳过镜头检测。场景切点用于挑选截图，不会自动决定删除内容。

## 通用部署建议

- 先以短视频验证依赖、模型路径、输出 JSON 和视频导出，再处理长素材或批次。
- 为 API Key、token、模型文件和运行数据使用仓库之外的位置；公开仓库只提交不含个人或机器配置的示例。
- 不要把服务密钥、私有服务器地址、用户目录或本机配置贴入公开 issue 或提交记录。
