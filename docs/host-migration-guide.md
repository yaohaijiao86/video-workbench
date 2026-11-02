# Video Workbench 整机迁移手册（Windows）

适用范围：把当前 `D:\1_PAN\WORK\smart` 的 Video Workbench、已有项目、导出文件和可选 ASR 服务迁到另一台 Windows 主机。盘点依据为 2026-11-02 的本机文件和配置。路径只是本机实测值；新机器若路径不同，按本文替换。先阅读 [本机文件盘点](project-file-audit.md)。

## 1. 明确要迁走的东西

把它想成四部分：**程序源码**、**工作台数据**、**原始视频/导出成品**、**外部工具和模型**。其中任一部分漏掉，都可能出现“程序能打开，但旧项目打不开或无法重新处理”。

| 部分 | 本机位置 | 是否必须 |
| --- | --- | --- |
| 最新程序源码 | `D:\1_PAN\WORK\smart\github-upload-staging\` 的 Git 跟踪文件 | 要使用最新功能则必须；不要用旧 `v0.2.13` EXE 代替 |
| 项目数据库与内部素材 | `D:\1_PAN\WORK\smart\video-workbench\data\`，约 4.55 GB | 保留旧项目必须 |
| 外部项目目录 | `D:\1_PAN\HOME\WORK\`，约 4.20 GB | 必须；92 条项目索引中 69 条指向这里 |
| 导出成品 | `D:\1_PAN\HOME\VEDIO\`、`DOC\`、`HTML\`，合计约 1.40 GB | 想保留成品则必须；`VEDIO` 是现有目录拼写 |
| 其他原始视频目录 | 由项目的 `source_path`、未来的 `watch_folders` 指定 | 需要重跑任务或继续自动扫描时必须 |
| Qwen3-ASR 权重 | `smart\ASRModels\`，约 6.09 GB；另有 `D:\1_PAN\TOOLS\AI\MODEL\` 中的同名目录 | 新主机承担本地 ASR 时选一套复制，或重新下载 |
| 启动密码、ASR token、Cookie | `data\.workbench-password.txt`、`smart\asr-token.txt`、可能的 `data\.bilibili-cookies.txt` | 对应功能继续使用时必须；私密复制 |
| FFmpeg、Python、ASR 环境、本地模型 API | 本机其他目录/程序 | 在新主机重新安装或重新连接；虚拟环境一般不直接复制 |

`data\catalog.json` 的 92 个项目路径是绝对路径：23 个指向旧 `data\`，69 个指向外部 `HOME\WORK\`。但盘点时只有 **18 个项目目录实际存在**（内部 6 个、外部 12 个），另外 74 条索引已失效；需要从其他备份找回缺失项目，迁移不会自动恢复。每个现存项目的 `project.json` 还可能引用视频原路径。**只复制源码仓库或只复制 `data\` 都不算完成迁移。**

## 2. 选择目标目录方案

### 方案 A：新主机保留原有盘符和路径（最省事）

在新主机建立与旧主机完全相同的 `D:\1_PAN\WORK\smart\video-workbench\data`、`D:\1_PAN\HOME\WORK`、`VEDIO`、`DOC`、`HTML` 等路径，并把原视频也放回原绝对路径。这样旧项目索引通常不需要改。**程序源码可以放到新的 `github-upload-staging`，但不要继续用旧启动脚本**；旧脚本固定指向 `smart\video-workbench`。

### 方案 B：改用新目录（例如 `E:\VideoWorkbench\`）

建议新目录分成 `app\`（最新源码）、`data\`、`work\`、`exports\videos\`、`exports\notes\`、`exports\html\`、`models\`、`tools\`。复制完后必须修改设置和项目里的旧绝对路径；见第 6 节。迁移前不要在目标机启动工作台，以免自动扫描导入尚未搬好的目录。

## 3. 旧主机：停服务并制作备份

1. 等待当前视频任务结束。在网页确认没有“处理中”任务，记录设置页的 FFmpeg、ASR 命令、模型服务地址、输出目录和自动扫描目录。涉及 API Key 的内容只保存在安全的私密记录中。
2. 在旧主机运行 `stop-workbench.bat`，或通过现有管理方式停止工作台和 ASR 服务。确认没有 Python、FFmpeg 正在写这些目录。停止后再复制，可避免 `catalog.json` 与项目文件时间点不一致。
3. 准备足够空间的**加密**移动硬盘或可信内网共享。仅已确认的 `data`、`HOME` 输出和模型约需 16 GB 以上；原始视频、外部模型和备份冗余还会增加空间。
4. 拷贝源码、`data`、四个外部目录、所有仍被引用的原视频目录、需要的模型和私密凭据。可用资源管理器，也可用下面的 PowerShell 示例。把 `X:\VWBackup` 换成实际备份盘；`robocopy` 返回码 0–7 通常表示成功或有差异，8 及以上表示失败。

```powershell
$backup = 'X:\VWBackup'
robocopy 'D:\1_PAN\WORK\smart\github-upload-staging' "$backup\app" /E /XD .git .venv build dist __pycache__ /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\WORK\smart\video-workbench\data' "$backup\data" /E /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\HOME\WORK' "$backup\work" /E /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\HOME\VEDIO' "$backup\videos" /E /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\HOME\DOC' "$backup\notes" /E /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\HOME\HTML' "$backup\html" /E /R:2 /W:2 /XJ
robocopy 'D:\1_PAN\WORK\smart\ASRModels' "$backup\models\qwen" /E /R:2 /W:2 /XJ
Copy-Item 'D:\1_PAN\WORK\smart\asr-token.txt' "$backup\asr-token.txt"
```

`data` 的复制已经包含工作台密码文件、可能的 B 站 Cookie 和设置，请保护整块备份盘。`robocopy` 命令不会自动发现**其他原视频目录、FFmpeg、LM Studio/模型服务文件**；按第 1 节补齐。旧 `video-workbench\` 中还有未提交的代码修改；若要保留旧开发历史，请单独完整备份该目录，切勿把它混同为最新源码。

5. 核对备份中存在 `app\run.py`、`app\src\video_workbench\app.py`、`data\catalog.json`、`data\settings.json`、`work\` 下的项目，以及模型权重文件。不要仅凭拷贝程序显示“完成”就清理旧主机。

## 4. 新主机：安装程序和依赖

下面以方案 B 的 `E:\VideoWorkbench` 为例；方案 A 则把目录换成旧路径。

1. 安装 **64 位 Python 3.11 或更高**；若要用 GPU ASR，按显卡驱动、CUDA 和 PyTorch 的实际兼容版本安装。浏览器用现代版本。
2. 将备份的 `app`、`data`、`work`、`videos`、`notes`、`html`、`models` 分别复制到目标目录。若用 Git 克隆源码，必须先确认仓库 `main` 已包含本机新功能提交 `14d054d`；旧 Release `v0.2.13` 早于该提交。备份中的 `app` 可直接作为该版本源码。
3. 在 `app` 目录创建新的工作台虚拟环境：

```powershell
cd E:\VideoWorkbench\app
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e .
```

若电脑没有 `py -3.12`，请先安装该 Python 版本，或使用实际安装的 `python.exe` 创建环境。B 站下载和二维码登录需要额外执行 `& '.\.venv\Scripts\python.exe' -m pip install -e '.[bilibili]'`。**不要直接拷贝旧 `.venv` 或 `asr-env` 当成安装。**

4. 安装 FFmpeg，并确认 `ffmpeg.exe`、`ffprobe.exe` 可运行。按需重建独立 ASR 环境，安装 `qwen-asr`、匹配新 GPU 的 PyTorch；将模型放在新主机固定目录。外部工具与模型安装说明见 [external-tools.md](external-tools.md)，双机 ASR 见 [remote-asr-windows.md](remote-asr-windows.md)。
5. 若使用本地文本/视觉模型，单独安装或启动原有 OpenAI 兼容模型服务，并确认新主机的地址、端口、模型 ID 和权重。工作台源码包与 EXE 不包含该服务或模型。

## 5. 路径不变时如何启动

如果新主机保留了所有旧绝对路径，先从最新源码手动启动，使用**旧数据目录**：

```powershell
cd D:\1_PAN\WORK\smart\github-upload-staging
& '.\.venv\Scripts\python.exe' run.py --data 'D:\1_PAN\WORK\smart\video-workbench\data'
```

然后访问 `http://127.0.0.1:8765`。工作台启动时会自动扫描已设置的监控文件夹；确认文件夹和依赖都就绪后再启用它们。已有 `start-workbench.bat` 和 `workbench_control.py` 把 `PROJECT` 写死为旧 `video-workbench`，并写死本机 Python、IP、网段、ASR 路径；在新机使用前必须逐项修改。手动启动能先排除旧脚本的干扰。

## 6. 路径改变时修正设置和项目索引

**先备份，再改路径，最后启动。** 至少核对这些位置：

| 文件 | 需要改的字段 |
| --- | --- |
| `data\settings.json` | `ffmpeg`、`ffprobe`、`asr_command` 中的 Python/适配器路径、`qwen3_asr_model`、`qwen3_aligner_model`、`output_dir`、`video_output_dir`、`notes_output_dir`、`html_output_dir`、`watch_folders`；模型服务地址也需按新网络修改 |
| `data\catalog.json` | 每个项目 ID 对应的**绝对项目目录**，包括原 `data\projects` 和外部 `HOME\WORK` |
| `data\batches.json` | 若有 `source_folder`，改为原视频的新目录 |
| 每个项目的 `project.json` | `source_path`（原视频位置）和 `output_path`（项目目录） |
| `data\task_history.json` | 任务历史本身应复制；通常不需要改路径 |

如果路径很多，可以在新主机**副本**上运行下列一次性脚本。先把所有旧/新路径映射改成实际值，将代码另存为 `E:\VideoWorkbench\remap_paths.py`，不要放到要公开上传的仓库。它只修改 `settings.json`、`catalog.json`、`batches.json` 和项目 `project.json` 中**以旧路径开头**的字符串；每个被改的 JSON 会留下 `.pre-migration.bak` 备份。原视频来自其他目录时，继续往 `MAP` 加映射。

```python
from pathlib import Path
import json
import os
import shutil

BASE = Path(r"E:\VideoWorkbench")
MAP = [
    (r"D:\1_PAN\WORK\smart\video-workbench\data", r"E:\VideoWorkbench\data"),
    (r"D:\1_PAN\HOME\WORK", r"E:\VideoWorkbench\work"),
    (r"D:\1_PAN\HOME\VEDIO", r"E:\VideoWorkbench\videos"),
    (r"D:\1_PAN\HOME\DOC", r"E:\VideoWorkbench\notes"),
    (r"D:\1_PAN\HOME\HTML", r"E:\VideoWorkbench\html"),
    (r"D:\1_PAN\WORK\smart\ASRModels", r"E:\VideoWorkbench\models\qwen"),
    (r"D:\1_PAN\WORK\smart\video-workbench", r"E:\VideoWorkbench\app"),
    (r"D:\1_PAN\WORK\smart\asr-token.txt", r"E:\VideoWorkbench\asr-token.txt"),
    # 继续添加 FFmpeg、旧 Python、其他原视频目录等映射
]

def change(value):
    if isinstance(value, str):
        for old, new in sorted(MAP, key=lambda pair: len(pair[0]), reverse=True):
            if value.casefold() == old.casefold() or value.casefold().startswith((old + '\\').casefold()):
                return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [change(item) for item in value]
    if isinstance(value, dict):
        return {key: change(item) for key, item in value.items()}
    return value

files = [BASE / 'data' / name for name in ('settings.json', 'catalog.json', 'batches.json')]
files += list((BASE / 'data').rglob('project.json'))
files += list((BASE / 'work').rglob('project.json'))
for path in files:
    if not path.is_file():
        continue
    before = json.loads(path.read_text(encoding='utf-8'))
    after = change(before)
    if before == after:
        continue
    backup = path.with_name(path.name + '.pre-migration.bak')
    if backup.exists():
        raise RuntimeError(f'备份已存在，请先检查：{backup}')
    shutil.copy2(path, backup)
    path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('已更新：', path)
```

执行：`py -3.12 E:\VideoWorkbench\remap_paths.py`。若旧 `settings.json` 仍指向 `D:\1_PAN\TOOLS\AI\MODEL\...`，应再添加该模型路径映射，或在网页设置里手动更改。本机当前 ASR 命令还引用旧 Python 3.13、旧 `asr_remote_client.py`、`asr-token.txt` 和 `192.168.31.221:8877`；迁移后应把 Python、脚本、token 和服务 URL 全部核对。映射不会凭空复制原视频，也不会修复失效的服务地址；这些需逐项核对。**不要做全盘无条件字符串替换**，以免改坏转录文本和项目内容。

路径修正后，用下面的只读检查找出仍不存在的项目目录：

```powershell
$data = 'E:\VideoWorkbench\data'
$catalog = Get-Content -Raw (Join-Path $data 'catalog.json') | ConvertFrom-Json -AsHashtable
$missing = @($catalog.Values | Where-Object { -not (Test-Path -LiteralPath $_) })
"项目总数：$($catalog.Count)；目录缺失：$($missing.Count)"
$missing | Select-Object -First 10
```

本机盘点时原本有 **74 条目录缺失**；若没有找到额外备份，新主机显示相同数量说明复制未额外丢失，但这些旧索引仍不可用。如果新主机缺失数**超过 74**，先核对复制目录与路径映射；如找到旧备份，应把缺失数尽量降到 0。再抽查几个现存 `project.json` 的 `source_path` 是否指向真实原视频；已有项目可显示但源视频不在时，重跑和自动去重会受影响。新版本的自动扫描只处理监控目录**当前层级**的 MP4/MOV/MKV/WEBM，跳过刚修改不足 60 秒的文件；旧项目的 `source_path` 索引用于防止重复导入。

## 7. 首次启动与验收

路径方案 B 在新主机使用：

```powershell
cd E:\VideoWorkbench\app
& '.\.venv\Scripts\python.exe' run.py --data 'E:\VideoWorkbench\data'
```

在本机打开 `http://127.0.0.1:8765`，依次确认：

1. 项目列表与旧机现存项目一致；随机打开几个早期与近期项目，能看到字幕和项目状态。索引缺失数不应比旧机的 74 条更多；若能从备份恢复旧项目，再逐步减少缺失数。
2. 设置中的视频、笔记、HTML 目录指向新位置；FFmpeg/ffprobe 可用；ASR 命令、模型文件和文本/视觉模型服务可用。
3. 用一个**短视频**完成“导入 → 转录/分析 → 导出”，确认目标目录有视频和笔记。
4. “任务记录”页出现新任务的导入时间和结束时间。旧版从未生成 `task_history.json` 时，历史页不会凭空补录旧任务；从新版本完成的任务开始累计。
5. 逐个检查“设置 → 自动扫描文件夹”；目录全部存在且无待处理的历史视频后再启用，以免首次启动把整批视频自动导入。
6. 若经局域网访问，使用新主机**实际私有 IP** 启动，并提供至少 20 字符密码文件与可信客户端网段，例如：

```powershell
& '.\.venv\Scripts\python.exe' run.py --host 192.168.1.20 --port 8765 --data 'E:\VideoWorkbench\data' --password-file 'E:\VideoWorkbench\data\.workbench-password.txt' --allowed-client-cidr 192.168.1.0/24
```

将示例 IP/网段换成真实网络，限制防火墙入站范围。不要将 HTTP 端口直接暴露到公网。若还要使用旧一键启停脚本，最后再编辑 `start-workbench.bat`、`stop-workbench.bat` 和 `workbench_control.py` 中写死的 Python、项目、数据、IP、网段、ASR 服务和模型路径，验证启停行为后才把它们设为开机自启。

## 8. 常见问题与回退

| 现象 | 优先检查 |
| --- | --- |
| 网页能打开但项目列表少了很多 | `catalog.json` 中旧绝对路径；外部 `WORK` 是否复制；`project.json` 是否在索引目录 |
| 项目能打开但重跑失败 | `project.json` 的 `source_path`；原视频或 `downloads` 是否复制 |
| 转录失败 | FFmpeg、独立 ASR 环境、模型路径、GPU 驱动、远程 ASR token/地址 |
| 导出失败或导出到旧盘符 | `settings.json` 的输出目录、FFmpeg 路径、目录写权限 |
| 自动扫描没有动作 | 工作台是否运行、`watch_folders` 是否存在、视频是否已导入或刚复制不到 60 秒；网页设置显示的最近扫描状态 |
| 登录失败 | 密码文件路径及内容、`access.json` 状态、客户端网段、防火墙 |
| 旧启动脚本启动了旧版 | `workbench_control.py` 的 `PROJECT` 固定为旧 `video-workbench`，改脚本或继续从最新源码手动启动 |

回退方法：停止新主机服务，把迁移前的备份重新复制到原目录；若只改坏 JSON，可用同目录的 `.pre-migration.bak` 恢复。**在新主机连续完成几个实际任务、确认导出与历史记录正常之前，不要删除旧主机数据或备份。**
