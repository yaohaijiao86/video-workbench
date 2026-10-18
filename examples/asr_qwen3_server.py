"""Run separately on the Windows GPU machine; no model is bundled here."""

import argparse
import hmac
import ipaddress
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from asr_qwen3 import transcribe_audio

MAX_AUDIO_BYTES = 512 * 1024 * 1024  # 3 h of 16 kHz mono PCM16 is about 346 MB.


def make_handler(token, transcribe, temp_dir):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # A client may cancel a long transcription. The worker should
                # keep serving other requests instead of logging a second error
                # while trying to send a response to the disconnected client.
                return False
            return True

        def do_GET(self):
            if self.path == "/health":
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, "Bearer " + token):
                    return self.respond(401, {"error": "unauthorized"})
                self.respond(200, {"ok": True})
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/transcribe":
                return self.respond(404, {"error": "not found"})
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, "Bearer " + token):
                return self.respond(401, {"error": "unauthorized"})
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError:
                size = 0
            if not 0 < size <= MAX_AUDIO_BYTES:
                return self.respond(413, {"error": "audio size invalid or over 512 MB"})
            path = None
            try:
                self.connection.settimeout(600)
                with tempfile.NamedTemporaryFile(suffix=".wav", dir=temp_dir, delete=False) as target:
                    path = Path(target.name)
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("audio upload interrupted")
                        target.write(chunk)
                        remaining -= len(chunk)
                rows = transcribe(path)
                self.respond(200, {"segments": rows})
            except Exception as exc:
                self.respond(500, {"error": str(exc)[:500]})
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Qwen3-ASR LAN worker")
    parser.add_argument("--host", default="127.0.0.1", help="GPU 机器的局域网 IP")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--aligner", default="Qwen/Qwen3-ForcedAligner-0.6B")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    try:
        local_bind = ipaddress.ip_address(args.host).is_private and args.host != "0.0.0.0"
    except ValueError:
        local_bind = args.host == "localhost"
    if not local_bind:
        parser.error("只能绑定本机或局域网 IP；不要暴露到公网")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        parser.error("token 文件至少需要 32 个字符")
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(args.cache_dir.resolve())
    temp_dir = args.temp_dir or Path(tempfile.gettempdir())
    temp_dir.mkdir(parents=True, exist_ok=True)

    model_lock = Lock()

    def transcribe(path):
        # Keep GPU model loads/inference serialized while the HTTP server can
        # still answer health checks and accept other uploads concurrently.
        with model_lock:
            return transcribe_audio(path, args.model, args.aligner, args.device)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(token, transcribe, temp_dir))
    server.daemon_threads = True
    print(f"Qwen3-ASR worker listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
