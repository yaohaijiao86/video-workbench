# Video Workbench

简体中文说明：[README.md](README.md)。Windows downloads: [executable](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.11/video-workbench-v0.2.11-windows-x64.exe) · [source archive](https://github.com/yaohaijiao86/video-workbench/releases/download/v0.2.11/video-workbench-v0.2.11-source.zip).

Video Workbench is a local-first tool for reviewing video transcripts, preparing precise edits, and producing illustrated notes. Version 0.2.11 is a preview release.

## Project goals

- Keep source video and project files under the user's control.
- Organize transcription, edit suggestions, human review, export, and notes in one workflow.
- Let users choose their own media tools and language or vision models instead of bundling large external runtimes.
- Keep suggested cuts reviewable and require a person to approve the final edit.

## Features

- Import MP4, MOV, MKV, and WEBM files; import local folders; and process multiple videos in batches.
- Import SRT or WebVTT subtitles, or call a configured ASR command that returns timestamped JSON.
- The settings page includes a generic Qwen3-ASR command template and its adapter script; install `qwen-asr` separately and point the first command item to that environment's Python executable.
- Optionally download a Bilibili video and available subtitles; scan a Bilibili app QR code to authenticate.
- Preview source video in a browser and review transcript segments with timestamp navigation.
- Request filler-word and repetition suggestions from an OpenAI-compatible chat API. Review and adjust suggested deletions in the transcript editor.
- Export a trimmed video and retimed SRT subtitles through FFmpeg.
- Create topic-based summaries and full transcripts as HTML and Markdown. Optional vision, OCR, and scene-detection commands can help select relevant frames.
- Save projects in the workbench data directory, beside local source files, or in a chosen output directory.
- Run one project or a batch through transcription, analysis, summaries, and export with visible progress.

The core application uses the Python standard library. Bilibili download and QR login require the optional `yt-dlp` and `qrcode` packages. FFmpeg, ASR engines, OCR, scene detection, model servers, and model weights are separate user-managed components.

## Requirements

- Python 3.11 or later for a source deployment.
- A modern browser.
- FFmpeg for video export. The selected FFmpeg build must include the codecs required by the chosen output settings.
- An ASR command or imported subtitles for transcription. An OpenAI-compatible chat service is needed for model-generated edit suggestions and summaries.

## Run from source

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run.py
```

Open `http://127.0.0.1:8765` in a browser. The default project data location is `VideoWorkbenchData` in the current user's home directory. Set a different location with `python run.py --data <data-directory>`.

To enable Bilibili downloads and QR login, install the optional components in the same Python environment:

```powershell
python -m pip install -e ".[bilibili]"
```

## LAN deployment

The default server listens on the loopback interface. For trusted LAN access, bind to the server's private address and provide a password file and an allow-listed client CIDR. Replace the uppercase placeholders with values for the deployment:

```powershell
python run.py --host SERVER_PRIVATE_IP --port 8765 --password-file PASSWORD_FILE --allowed-client-cidr TRUSTED_CLIENT_CIDR --data DATA_DIRECTORY
```

Restrict inbound firewall access to the same trusted subnet. Do not expose the HTTP service directly to the public internet. Remote browser uploads, processing, and project storage run on the server. Keep the password file, runtime data, model settings, API credentials, and ASR token files outside the source repository.

For a separate ASR host, see [Remote ASR deployment](docs/remote-asr-windows.md). Configure the model API, ASR command, FFmpeg, and project output location in the workbench settings for each deployment. API credentials are not included in the source package.

## Build releases

On 64-bit Windows with Python 3.11 or later, install the build and optional Bilibili dependencies and run the release script:

```powershell
python -m pip install -e ".[build,bilibili]"
python build_release.py
```

The script creates a source ZIP and a single-file Windows x64 executable in the parent directory. The executable starts the local web application and defaults to loopback access. It does not include FFmpeg, ASR/OCR software, scene detectors, model weights, API credentials, or runtime data. Use `--output-dir` to choose a different release destination.

## Repository layout

- `src/video_workbench/` — application, media workflow, integrations, and browser UI.
- `examples/` — adapters for ASR, OCR, and scene detection.
- `docs/` — deployment guidance.
- `tests/` — automated checks.
- `build_release.py` — source archive and Windows executable builder.

## Data and safety

Runtime settings and project files belong in the selected data or output directories. Do not commit real credentials, token files, host addresses, or deployment settings. Model requests may include transcript text and, when enabled, selected video frames; use services that you trust. Review suggested cuts and listen to the exported video before using it.

The project is licensed under MIT. External tools and models have their own licenses and must be installed and reviewed separately.
