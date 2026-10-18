"""Stream a workbench WAV to a separately installed LAN ASR worker."""

import argparse
import ipaddress
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def validate_url(value):
    parsed = urlparse(value)
    try:
        private = ipaddress.ip_address(parsed.hostname or "").is_private
    except ValueError:
        private = parsed.hostname == "localhost"
    if (parsed.scheme != "http" or not private or parsed.path != "/transcribe"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("ASR URL 必须是局域网 HTTP 地址，路径为 /transcribe")
    return value


def transcribe(url, token, audio):
    url = validate_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    health = urllib.request.Request(url.rsplit("/", 1)[0] + "/health",
                                    headers={"Authorization": "Bearer " + token})
    try:
        with opener.open(health, timeout=10) as response:
            if not json.load(response).get("ok"):
                raise RuntimeError("远程 ASR 健康检查失败")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"远程 ASR 健康检查 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"远程 ASR 健康检查连接失败：{reason}") from exc
    with audio.open("rb") as stream:
        request = urllib.request.Request(
            url, data=stream, method="POST",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "audio/wav",
                     "Content-Length": str(audio.stat().st_size)},
        )
        try:
            with opener.open(request, timeout=14400) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"远程 ASR HTTP {exc.code}: {exc.read(500).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"远程 ASR 转写连接中断：{reason}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise RuntimeError("远程 ASR 未返回 segments 数组")
    return data


def main():
    parser = argparse.ArgumentParser(description="Video Workbench remote ASR client")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        parser.error("token 文件至少需要 32 个字符")
    try:
        result = transcribe(args.url, token, args.input)
    except RuntimeError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
