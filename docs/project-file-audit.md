# Video Workbench 本机文件盘点与清理建议

盘点日期：2026-11-02。盘点范围为 `D:\1_PAN\WORK\smart`，只读取文件、目录、Git 状态和程序配置；**没有删除或移动任何文件**。容量是文件大小合计的约数，不等于磁盘占用空间。目录会继续变化，清理前请重新核对。

## 先看结论

1. **最新的源码工作目录**是 `github-upload-staging\`。本机提交 `14d054d` 已含“多文件夹每小时扫描”和“任务记录”功能；盘点时 GitHub `main` 仍指向 `045bf94`，而已发布的 `v0.2.13` EXE/源码 ZIP 也早于这个功能。迁移新功能应使用最新源码或在此源码上重新构建 EXE，不能把旧 EXE 当成最新版本。
2. `video-workbench\` 是**正在使用过的旧工作目录**，含大量未提交的代码修改，以及约 4.55 GB 的运行数据。即使代码较旧，也**不能整目录删除**。
3. 真正需要优先保护的是 `video-workbench\data\`、`D:\1_PAN\HOME\WORK\`、`D:\1_PAN\HOME\VEDIO\`、`D:\1_PAN\HOME\DOC\`、`D:\1_PAN\HOME\HTML\`、原始视频所在目录、模型和凭据。它们并未全部包含在 Git 仓库里。
4. `.venv\`、`asr-env\`、`build\`、`__pycache__\`、下载缓存和日志大多可以在**停止服务、完成备份并确认用途后**清理；它们不能替代源码、项目数据和模型。

## 顶层目录：它们是什么

| 路径（均在 `D:\1_PAN\WORK\smart\` 下） | 约大小 | 分类 | 迁移/清理结论 |
| --- | ---: | --- | --- |
| `github-upload-staging\` | 0.69 GB | 最新源码检出；其中大部分是构建产物 | 迁移其 Git 跟踪的源码和文档；`.venv`、`build`、`dist` 可重建 |
| `video-workbench\` | 13.69 GB | 旧源码检出 + 运行数据 + 大型环境 | **先备份并拆分**；不要整体当作垃圾 |
| `ASRModels\` | 6.09 GB | Qwen3-ASR 和 ForcedAligner 模型权重 | 若新主机也承担 ASR，复制或重新下载；不是源码 |
| `asr-env\` | 5.10 GB | ASR 独立 Python 虚拟环境 | 建议在新主机重新创建，按 GPU/驱动安装依赖；确认无需本机 ASR 后才清理旧环境 |
| `pip-cache\` | 0.23 GB | pip 下载缓存 | 可删除，之后安装依赖会重新下载 |
| `ASRTemp\` | 0.06 GB | ASR 临时 WAV、服务日志 | 停止 ASR 并确认无待处理任务后可清理 |
| `logs\` | 很小 | 启停和服务日志 | 故障排查后可归档或清理 |
| `modelscope-cache\`、`modelscope-home\` | 很小 | ModelScope 缓存/配置 | `modelscope-home\credentials` 可能含登录凭据，**按机密文件处理**；不要公开上传 |
| `github\` | 空 | 空目录 | 确认仍为空即可删除 |
| `__pycache__\` | 很小 | Python 字节码缓存 | 可删除，运行时会重建 |

根目录还包括 `workbench_control.py`（本机自定义启停**源码脚本**）、`start-workbench.bat`、`stop-workbench.bat`（启动入口）、`asr-token.txt`（**机密令牌**）、安装说明 HTML、多个历史 `*-source.zip`、五个历史 Windows EXE。ZIP 是源码快照；EXE 是已经编译好的可运行程序，并非可编辑源码。根目录的旧包在确认已有可靠备份和目标版本后可按版本清理。`video-workbench-v0.2.13-windows-x64.exe` 也**不含本机最新未发布功能**。

## 两份源码目录的区别

| 项目 | `github-upload-staging\` | `video-workbench\` |
| --- | --- | --- |
| 用途 | 准备上传的较新源码 | 原本运行的旧目录 |
| Git 状态 | 本机 `main` 有一个待上传提交 `14d054d` | 许多已修改和未跟踪文件，不能凭版本号直接删 |
| `src\video_workbench\` | 应用核心源码、HTTP 服务、前端页面与样式 | 较早版本及本机修改 |
| `examples\` | ASR/OCR/场景识别适配脚本 | 旧版适配脚本 |
| `tests\`、`.github\` | 测试与自动化配置 | 测试与自动化配置 |
| `docs\`、`README*`、`CHANGELOG.md` | 文档 | 旧文档和本机文档 |
| `pyproject.toml`、`run.py`、`build_release.py` | 包配置、启动与构建脚本 | 同类文件；内容版本不同 |
| `.git\` | Git 历史 | 独立的 Git 历史 |
| `.venv\` | 约 35 MB，可重建 | 约 8.65 GB，可重建，但重新装深度学习依赖耗时 |
| `build\`、`dist\` | 约 349 MB / 317 MB，历史构建结果 | 约 30 MB / 20 MB，历史构建结果 |
| `data\` | 无 | 约 4.55 GB，**真实运行数据** |
| `test-data\` | 无 | 约 0.24 GB，测试素材/结果；确认不需复现测试后可归档或清理 |
| `video-workbench.rar` | 无 | 约 205 MB，旧打包归档；核对内容后可作为历史包清理 |

源码的最小集合是 `src\`、`examples\`、`pyproject.toml`、`run.py`，加上所需文档和 `build_release.py`；若要保留开发与测试能力，再带 `tests\`、`.github\` 和 `.git\`。`src\video_workbench\static\` 是浏览器页面的 **HTML/CSS/JS 源文件**，不是缓存。`document_style.json` 是随程序发布的默认样式资源，也不是临时文件。

## 模型、外部软件和 Python 环境

- `ASRModels\Qwen3-ASR-1.7B\` 约 **4.38 GB**，包含 `.safetensors` 权重、分词器和配置；`ASRModels\Qwen3-ForcedAligner-0.6B\` 约 **1.71 GB**。两者都不是 EXE 的一部分。
- 本机旧 `data\settings.json` 另指向 `D:\1_PAN\TOOLS\AI\MODEL\` 下的同名模型目录。这些目录也存在。迁移前确认当前 ASR 服务实际用的是哪一份；根目录 `workbench_control.py` 指向 `smart\ASRModels\`，而网页设置指向 `TOOLS\AI\MODEL\`。无需把两份都复制到新主机，但**不要在未核对服务配置前删任一份**。
- FFmpeg 与 ffprobe 配在 `D:\1_PAN\TOOLS\USE\toolchain\portable\...\bin\`；该目录位于本盘点根目录之外。新主机需单独安装/复制并重新填写路径。
- 当前 `asr_command` 指向旧源码目录的 `examples\asr_remote_client.py`、旧机的 Python 3.13 和 `smart\asr-token.txt`，连接旧机 `192.168.31.221:8877`。换主机后这四处都应核对；远程 ASR 服务及 GPU 主机可能需要另行迁移。
- `asr-env\` 与两份 `.venv\` 是安装好的 Python 依赖，不是原始源码；搬动虚拟环境常因解释器路径、系统、CUDA 和驱动差异失效。应在目标机器重建。
- 文本/视觉模型使用本地 `127.0.0.1:8787/v1` 服务；本仓库不包含该服务的软件及模型权重。它们必须另行盘点。

## 运行数据：不能误删的部分

旧运行目录是 `video-workbench\data\`，实际内容约 **4.55 GB**：`downloads\` 1.99 GB（B 站等下载的原视频，项目可能引用）、`projects\` 1.72 GB（工作台项目与可能的上传原视频）、`exports\` 0.82 GB（导出成品）、`summary-revisions\` 很小（摘要修订）。根目录的 `catalog.json`（项目索引）、`batches.json`（批次）、`settings.json`（设置）、`timing_history.json`（历史记录）、`document_style.json`（用户样式）、`access.json`（访问保护状态）也应保留。新版使用后还会生成 `task_history.json`（任务导入与结束时间）。

旧设置另外指定了以下**仓库外目录**，共约 **5.60 GB**：

| 目录 | 约大小 | 作用 |
| --- | ---: | --- |
| `D:\1_PAN\HOME\WORK\` | 4.20 GB | 项目保存目录；`catalog.json` 的 92 条记录中有 **69 条**指向这里 |
| `D:\1_PAN\HOME\VEDIO\` | 1.35 GB | 导出视频目录，拼写就是 `VEDIO` |
| `D:\1_PAN\HOME\DOC\` | 0.02 GB | 笔记导出目录 |
| `D:\1_PAN\HOME\HTML\` | 0.03 GB | HTML 导出目录 |

其余 23 条项目索引指向旧 `data\`。**重要现状：92 条索引中，当前只有 18 条对应目录及 `project.json` 实际存在**（`data\` 6 条、外部 `WORK\` 12 条）；另外 74 条目前是失效路径，迁移本身不能恢复已经缺失的项目文件。若这些项目还需要使用，须从其他备份查找。`catalog.json` 的路径均为**绝对路径**；一些 `project.json` 中还保存 `source_path` 和 `output_path`。通过“选择工作区”或自动扫描导入的项目，可能只引用原视频而没有复制它。迁移时还要单独盘点、复制原视频所在文件夹。删除 `downloads\`、外部 `WORK\` 或原视频，可能导致项目打开、重跑或自动去重失效。

机密数据包括 `asr-token.txt`、`data\.workbench-password.txt`、可能的 `data\.bilibili-cookies.txt`、`modelscope-home\credentials`，以及其他目录中的 API 密钥。迁移时走私密渠道，限制文件权限，**不要放进 Git、公开 ZIP、共享文档或聊天截图**。进程状态文件（`data\.workbench-process.json`、`data\.asr-process.json`）和日志是旧主机特有的；迁移后不应沿用旧 PID，但应先停止服务再处理。

默认 `C:\Users\Administrator\VideoWorkbenchData\` 也存在，目前只发现默认样式和空导出目录，容量可忽略；这是不带 `--data` 参数启动时的默认位置。若今后改了启动方式，仍需重新盘点它。

## 清理分级与建议顺序

| 等级 | 对象 | 操作条件 |
| --- | --- | --- |
| 优先保留并备份 | 两份工作目录中的**未提交代码**、`data\`、`HOME\WORK/ VEDIO/ DOC/ HTML`、原视频、模型、机密凭据 | 完成目标机验收前不要删除 |
| 可重新生成 | `.venv\`、`asr-env\`、`build\`、`dist\`、`__pycache__\`、`.pytest_cache\`、`*.pyc` | 停止服务；确认安装/构建方式与目标版本；需重装或重构建 |
| 可按需清理 | `pip-cache\`、`ASRTemp\`、`logs\`、空 `github\`、多余历史 ZIP/EXE/RAR | 核对没有正在运行的任务，保留需要的日志和版本后再清理 |
| 必须逐项确认 | `downloads\`、`test-data\`、重复模型目录、旧 `video-workbench\` 整体 | 可能是项目源文件或唯一副本，不能按“临时”字样直接删除 |

建议先按 [迁移手册](host-migration-guide.md)做完整备份和新主机验证，最后才清理旧机。**本报告只给判断，不执行删除。**
