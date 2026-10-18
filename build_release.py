"""Build the source ZIP and one-file Windows x64 release executable."""

from __future__ import annotations

import argparse
import platform
import shutil
import struct
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
EXCLUDED_DIRECTORIES = {
    ".cache",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "test-data",
}
EXCLUDED_SUFFIXES = {".exe", ".log", ".pyc", ".rar", ".zip"}
EXCLUDED_FILES = {
    "docs/development-architecture-deployment.html",
    "docs/installation-migration-guide.html",
    "docs/video-workbench-guide.html",
    "docs/video-workbench-guide.md",
}


def project_version() -> str:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["version"]


def source_files():
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT)
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if relative.as_posix() in EXCLUDED_FILES:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name == "config.local.json":
            continue
        yield path, relative


def create_source_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path, relative in source_files():
            archive_name = Path("video-workbench", *relative.parts).as_posix()
            archive.write(file_path, archive_name)


def build_executable(version: str) -> Path:
    if platform.system() != "Windows" or struct.calcsize("P") != 8:
        raise RuntimeError("Windows x64 executable must be built on 64-bit Windows.")

    stem = f"video-workbench-v{version}-windows-x64"
    static_dir = PROJECT_ROOT / "src" / "video_workbench" / "static"
    style_file = PROJECT_ROOT / "src" / "video_workbench" / "document_style.json"
    build_dir = PROJECT_ROOT / "build" / "pyinstaller"
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        stem,
        "--paths",
        str(PROJECT_ROOT / "src"),
        "--add-data",
        f"{static_dir};video_workbench/static",
        "--add-data",
        f"{style_file};video_workbench",
        "--collect-all",
        "yt_dlp",
        "--collect-all",
        "qrcode",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir / "work"),
        "--specpath",
        str(build_dir),
        str(PROJECT_ROOT / "run.py"),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    return dist_dir / f"{stem}.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Video Workbench source and Windows executable releases.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WORKSPACE_ROOT,
        help="Release output directory (default: the parent of this project directory).",
    )
    args = parser.parse_args()

    version = project_version()
    output_dir = args.output_dir.expanduser().resolve()
    archive_path = output_dir / f"video-workbench-v{version}-source.zip"
    try:
        create_source_archive(archive_path)
        executable = build_executable(version)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1

    published_executable = output_dir / executable.name
    shutil.copy2(executable, published_executable)
    print(f"Source archive: {archive_path} ({archive_path.stat().st_size:,} bytes)")
    print(f"Windows x64 executable: {published_executable} ({published_executable.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
