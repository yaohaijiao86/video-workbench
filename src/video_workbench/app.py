from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import mimetypes
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import services
from .workbench import Workbench

STATIC = Path(__file__).parent / "static"
MAX_JSON = 2 * 1024 * 1024
SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_FAILURE_LIMIT = 5


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    wb: Workbench
    auth_password: str | None = None
    auth_secret: bytes = b""
    auth_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    bound_host: str = "127.0.0.1"
    lan_mode: bool = False
    password_required: bool = False
    auth_supported: bool = False
    access_path: Path | None = None
    login_failures: dict[str, list[float]] = {}
    login_lock = threading.Lock()

    def respond(self, code, data, ctype="application/json; charset=utf-8", extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode() if ctype.startswith("application/json") else data
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'self'")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def data(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError(400, "无效的请求长度") from exc
        if size < 0 or size > MAX_JSON:
            raise RequestError(413, "请求内容过大")
        try:
            return json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RequestError(400, "请求 JSON 格式无效") from exc

    def parts(self):
        return [unquote(x) for x in urlsplit(self.path).path.strip("/").split("/")]

    def _client_ip(self):
        try:
            return ipaddress.ip_address(self.client_address[0])
        except ValueError as exc:
            raise RequestError(403, "无法识别客户端地址") from exc

    def _is_server_local_client(self):
        """Treat loopback and the server's own bound address as local access."""
        address = self._client_ip()
        if address.is_loopback:
            return True
        try:
            return address == ipaddress.ip_address(self.bound_host)
        except ValueError:
            return False

    def _password_required_for_client(self):
        return self.password_required and not self._is_server_local_client()

    def _check_client(self):
        address = self._client_ip()
        if self.lan_mode and not address.is_loopback and not any(address in network for network in self.auth_networks):
            raise RequestError(403, "仅允许配置的局域网网段访问")

    def _check_host(self):
        host = self.headers.get("Host", "").lower()
        port = self.server.server_port
        listening_host = str(self.server.server_address[0]).lower()
        expected = f"{listening_host}:{port}"
        if host == expected:
            return
        if listening_host in ("127.0.0.1", "localhost") and host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return
        raise RequestError(400, "拒绝未知主机名")

    def _check_origin(self, required=False):
        origin = self.headers.get("Origin", "")
        if not origin:
            if required:
                raise RequestError(403, "缺少来源校验信息")
            return
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.netloc.lower() != self.headers.get("Host", "").lower() or parsed.path or parsed.query or parsed.fragment:
            raise RequestError(403, "拒绝跨来源请求")

    def _session_cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            key, separator, value = part.strip().partition("=")
            if separator and key == "vw_session":
                return value
        return ""

    def _has_session(self):
        if not self._password_required_for_client():
            return True
        value = self._session_cookie()
        pieces = value.split(".")
        if len(pieces) != 3:
            return False
        expiry_text, nonce, signature = pieces
        if not expiry_text.isdigit() or int(expiry_text) < int(time.time()) or not nonce:
            return False
        payload = f"{expiry_text}.{nonce}".encode("ascii", "ignore")
        expected = hmac.digest(self.auth_secret, payload, "sha256").hex()
        return hmac.compare_digest(signature, expected)

    def _require_session(self):
        if not self._has_session():
            raise RequestError(401, "登录已失效，请重新登录")

    def _record_login_failure(self, client: str):
        now = time.monotonic()
        with self.login_lock:
            recent = [stamp for stamp in self.login_failures.get(client, []) if now - stamp < LOGIN_WINDOW_SECONDS]
            recent.append(now)
            self.login_failures[client] = recent
            return len(recent)

    def _login_blocked(self, client: str):
        now = time.monotonic()
        with self.login_lock:
            recent = [stamp for stamp in self.login_failures.get(client, []) if now - stamp < LOGIN_WINDOW_SECONDS]
            if recent:
                self.login_failures[client] = recent
            else:
                self.login_failures.pop(client, None)
            return len(recent) >= LOGIN_FAILURE_LIMIT

    def _clear_login_failures(self, client: str):
        with self.login_lock:
            self.login_failures.pop(client, None)

    def _public_page(self, path):
        return path in ("", "login", "login.css", "login.js", "health")

    def do_GET(self):
        try:
            self._check_client()
            self._check_host()
            parts = self.parts()
            route = "/".join(parts)
            if route == "health":
                return self.respond(200, {"status": "ok"})
            if route in ("", "login"):
                if self._password_required_for_client() and not self._has_session():
                    return self.file(STATIC / "login.html", "text/html; charset=utf-8")
                return self.file(STATIC / "index.html", "text/html; charset=utf-8")
            if route in ("login.css", "login.js"):
                return self.file(STATIC / route, "text/css; charset=utf-8" if route.endswith(".css") else "text/javascript; charset=utf-8")
            self._require_session()
            if parts == ["app.css"]:
                return self.file(STATIC / "app.css", "text/css; charset=utf-8")
            if parts == ["app.js"]:
                return self.file(STATIC / "app.js", "text/javascript; charset=utf-8")
            if parts == ["api", "session"]:
                return self.respond(200, {"auth_enabled": self._password_required_for_client()})
            if parts == ["api", "access"]:
                return self.respond(200, {"supported": self.auth_supported, "password_required": self.password_required})
            if parts == ["api", "settings"]:
                return self.respond(200, self.wb.public_settings())
            if parts == ["api", "task-control"]:
                return self.respond(200, self.wb.task_control_status())
            if parts == ["api", "bilibili", "download-progress"]:
                return self.respond(200, self.wb.bilibili_download_status())
            if parts == ["api", "models"]:
                return self.respond(200, services.models(self.wb.settings["base_url"], self.wb.api_key,
                                                          self.wb.settings.get("model_provider","local")))
            if parts == ["api", "directories"]:
                path = parse_qs(urlsplit(self.path).query).get("path", [""])[0]
                return self.respond(200, services.list_directories(path))
            if parts == ["api", "projects"]:
                return self.respond(200, self.wb.list_projects())
            if parts == ["api", "batches"]:
                return self.respond(200, self.wb.list_batches())
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "status":
                return self.respond(200, self.wb.project_status(parts[2]))
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                return self.respond(200, self.wb.detail(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "source":
                path = self.wb.source(parts[2])
                return self.file(path, mimetypes.guess_type(path.name)[0] or "application/octet-stream", range_enabled=True)
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "files":
                name = parts[4]
                frames = bool(re.fullmatch(r"frame_(?:\d{5}(?:_\d)?|topic_\d{3}_\d)\.jpg", name))
                allowed = set(self.wb.available_artifacts(parts[2]).values()) | {"edited.mp4", "edited.srt", "summary.html", "summary.md", "transcript.txt", "transcript_full.md", "transcript_full.html"}
                if name not in allowed and not frames:
                    raise FileNotFoundError()
                path = self.wb.folder(parts[2]) / name
                content_type="text/markdown; charset=utf-8" if name.lower().endswith(".md") else mimetypes.guess_type(name)[0] or "application/octet-stream"
                return self.file(path, content_type,
                                 attachment=name != "transcript.txt" and not frames)
            self.respond(404, {"error": "未找到"})
        except ConnectionError:
            return
        except Exception as exc:
            self.error(exc)

    def file(self, path, ctype, attachment=False, range_enabled=False):
        if not path.is_file():
            raise FileNotFoundError()
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        if range_enabled:
            header = self.headers.get("Range", "")
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
            if match:
                start = int(match.group(1))
                end = min(size - 1, int(match.group(2))) if match.group(2) else size - 1
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        if range_enabled:
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name, safe=""))
        self.end_headers()
        if end < start:
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_POST(self):
        try:
            self._check_client()
            self._check_host()
            self._check_origin(required=self.lan_mode)
            parts = self.parts()
            if parts == ["auth", "login"]:
                if not self._password_required_for_client():
                    return self.respond(200, {"ok": True})
                client = str(self._client_ip())
                if self._login_blocked(client):
                    return self.respond(429, {"error": "尝试次数过多，请 5 分钟后再试"}, extra_headers={"Retry-After": "300"})
                value = self.data()
                supplied = value.get("password", "") if isinstance(value, dict) else ""
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self.auth_password):
                    failures = self._record_login_failure(client)
                    if failures >= LOGIN_FAILURE_LIMIT:
                        return self.respond(429, {"error": "尝试次数过多，请 5 分钟后再试"}, extra_headers={"Retry-After": "300"})
                    return self.respond(401, {"error": "密码不正确"})
                self._clear_login_failures(client)
                expiry = str(int(time.time()) + SESSION_SECONDS)
                nonce = secrets.token_urlsafe(18)
                payload = f"{expiry}.{nonce}".encode("ascii")
                signature = hmac.digest(self.auth_secret, payload, "sha256").hex()
                cookie = f"vw_session={expiry}.{nonce}.{signature}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
                return self.respond(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            if parts == ["auth", "logout"]:
                return self.respond(200, {"ok": True}, extra_headers={"Set-Cookie": "vw_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            self._require_session()
            if parts == ["api", "access"]:
                if not self.auth_supported or self.access_path is None:
                    raise RequestError(400, "当前本机回环模式未配置访问密码")
                value = self.data()
                enabled = value.get("password_required") if isinstance(value, dict) else None
                if not isinstance(enabled, bool):
                    raise RequestError(400, "密码验证状态无效")
                self.access_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.access_path.with_name(self.access_path.name + ".tmp")
                temporary.write_text(json.dumps({"password_required": enabled}, ensure_ascii=False) + "\n", encoding="utf-8")
                temporary.replace(self.access_path)
                type(self).password_required = enabled
                return self.respond(200, {"ok": True, "password_required": enabled})
            if parts == ["api", "settings"]:
                self.wb.save_settings(self.data())
                return self.respond(200, {"ok": True})
            if parts == ["api", "task-control"]:
                value=self.data()
                action=value.get("action") if isinstance(value,dict) else None
                if action not in ("pause","resume"):
                    raise RequestError(400,"任务控制操作无效")
                return self.respond(200,self.wb.set_task_paused(action=="pause"))
            if parts == ["api", "bilibili", "qr"]:
                self.data()
                return self.respond(200,self.wb.start_bilibili_qr_login())
            if parts == ["api", "bilibili", "qr", "poll"]:
                value=self.data()
                return self.respond(200,self.wb.poll_bilibili_qr_login(value.get("login_id","")))
            if parts == ["api", "bilibili", "cookies"]:
                try:
                    size=int(self.headers.get("Content-Length","0"))
                except ValueError as exc:
                    raise RequestError(400,"无效的 Cookie 文件长度") from exc
                if size<=0 or size>2*1024*1024:
                    raise RequestError(413,"Cookie 文件不能为空且不能超过 2MB")
                payload=self.rfile.read(size)
                if len(payload)!=size: raise RequestError(400,"Cookie 文件上传不完整")
                self.wb.save_bilibili_cookies(payload)
                return self.respond(200,{"configured":True})
            if parts == ["api", "bilibili", "cookies", "clear"]:
                self.wb.clear_bilibili_cookies()
                return self.respond(200,{"configured":False})
            if parts == ["api", "models", "scan"]:
                value=self.data()
                if not isinstance(value,dict): raise ValueError("模型扫描配置格式无效")
                provider=str(value.get("provider","local"))
                base=str(value.get("base_url","")).strip()
                profile_id=str(value.get("profile_id",""))
                key=value.get("api_key","")
                if not isinstance(key,str): raise ValueError("API Key 格式无效")
                if not key and profile_id:
                    existing=next((row for row in self.wb.settings.get("model_profiles",[])
                        if isinstance(row,dict) and str(row.get("id",""))==profile_id),None)
                    if existing and existing.get("provider")==provider and str(existing.get("base_url","")).rstrip("/")==base.rstrip("/"):
                        key=self.wb.model_profile_keys.get(profile_id,"")
                return self.respond(200,services.models(base,key,provider))
            if parts == ["api", "directories", "create"]:
                value=self.data()
                return self.respond(200,{"path":services.create_directory(value.get("parent",""),value.get("name",""))})
            if parts == ["api", "directories", "rename"]:
                value=self.data()
                return self.respond(200,{"path":services.rename_directory(value.get("parent",""),
                    value.get("old_name",""),value.get("new_name",""))})
            if parts == ["api", "dialog", "folder"]:
                raise ValueError("文件夹选择器已更新，请刷新工作台页面后重试")
            if parts == ["api", "batches"]:
                value = self.data()
                return self.respond(200, {"id": self.wb.create_batch(value.get("name", "上传批次"),
                    group_output=value.get("group_output", True))})
            if parts == ["api", "batches", "import"]:
                value=self.data()
                if not isinstance(value,dict): raise ValueError("导入请求格式无效")
                if "urls" in value:
                    return self.respond(200,self.wb.import_bilibili_batch(value.get("urls")))
                return self.respond(200,self.wb.import_folder(value.get("path", "")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "cancel":
                self.data()
                self.wb.cancel_batch(parts[2])
                return self.respond(202, {"ok": True})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "run":
                self.wb.launch_batch(parts[2], self.data().get("mode", "prepare"))
                return self.respond(202, {"ok": True})
            if parts == ["api", "projects"]:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise RequestError(400, "无效的上传长度") from exc
                name = unquote(self.headers.get("X-Filename", "source.mp4"))
                pid = self.wb.create(name, self.rfile, size, batch_id=self.headers.get("X-Batch-ID") or None)
                return self.respond(200, {"id": pid})
            if len(parts) == 4 and parts[:2] == ["api", "projects"]:
                pid, action = parts[2], parts[3]
                if action == "reveal":
                    self.data()
                    if not self._is_server_local_client():
                        folder = self.wb.folder(pid)
                        raise RequestError(409, f"你正在从另一台设备访问工作台，项目文件夹保存在服务器上，无法在这台设备上直接打开资源管理器。服务器路径：{folder}")
                    target = self.wb.reveal_project(pid)
                    return self.respond(200, {"name": target.name})
                if action == "finish":
                    self.data()
                    self.wb.launch_all(pid)
                    return self.respond(202, {"ok": True})
                if action == "cancel":
                    self.data()
                    self.wb.cancel(pid)
                    return self.respond(202, {"ok": True, "stopping": True})
                if action == "subtitles":
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                    except ValueError as exc:
                        raise RequestError(400, "无效的字幕长度") from exc
                    if size <= 0 or size > 10 * 1024 * 1024:
                        raise RequestError(413, "字幕不得超过 10MB")
                    value = self.rfile.read(size).decode("utf-8-sig")
                    return self.respond(200, {"count": self.wb.subtitles(pid, value)})
                if action == "decisions":
                    value = self.data()
                    if not isinstance(value, list):
                        raise ValueError("审核结果须为数组")
                    self.wb.save_decisions(pid, value)
                    return self.respond(200, {"ok": True})
                if action == "manual-suggestion":
                    value = self.data()
                    row = self.wb.add_manual_suggestion(pid, value.get("start_id"), value.get("end_id"), value.get("start"), value.get("end"))
                    return self.respond(201, row)
                if action == "manual-restore":
                    value = self.data()
                    return self.respond(200, self.wb.restore_manual_selection(pid, value.get("start"), value.get("end")))
                if action == "manual-pause-suggestion":
                    value = self.data()
                    row = self.wb.add_pause_suggestion(pid, value.get("start"), value.get("end"))
                    return self.respond(201, row)
                if action in ("transcribe", "analyze", "export", "summary", "notes", "document", "timestamp_document", "punctuate"):
                    self.wb.launch(pid, action, self.data())
                    return self.respond(202, {"ok": True})
            self.respond(404, {"error": "未找到"})
        except ConnectionError:
            return
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        if isinstance(exc, RequestError):
            code = exc.status
            message = str(exc)
        elif isinstance(exc, FileNotFoundError):
            code, message = 404, "未找到"
        else:
            code, message = 400, str(exc) or "请求失败"
        try:
            headers = {"WWW-Authenticate": "Session"} if code == 401 else None
            self.respond(code, {"error": message}, extra_headers=headers)
        except ConnectionError:
            pass

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), file=sys.stderr)


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Video Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=Path.home() / "VideoWorkbenchData")
    parser.add_argument("--password-file", type=Path, help="启用网页登录保护的密码文件")
    parser.add_argument("--allowed-client-cidr", action="append", default=[], help="允许访问的客户端网段；可重复指定")
    args = parser.parse_args()

    try:
        bind_address = ipaddress.ip_address(args.host)
    except ValueError:
        if args.host != "localhost":
            parser.error("绑定地址必须是明确的 IP 地址；不允许使用 0.0.0.0")
        bind_address = ipaddress.ip_address("127.0.0.1")
    is_loopback = bind_address.is_loopback
    if not is_loopback and (not bind_address.is_private or bind_address.is_unspecified or bind_address.is_multicast):
        parser.error("局域网服务只允许绑定明确的私有 IP 地址")

    Handler.bound_host = args.host
    Handler.auth_password = None
    Handler.auth_secret = b""
    Handler.auth_networks = ()
    Handler.lan_mode = not is_loopback
    Handler.password_required = False
    Handler.auth_supported = False
    Handler.access_path = args.data.resolve() / "access.json"
    Handler.login_failures = {}
    if not is_loopback or args.password_file:
        if not args.password_file:
            parser.error("绑定局域网地址时必须提供 --password-file")
        try:
            password = args.password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.error(f"无法读取密码文件：{exc}")
        if len(password) < 20:
            parser.error("登录密码至少需要 20 个字符")
        try:
            networks = tuple(ipaddress.ip_network(value, strict=False) for value in args.allowed_client_cidr)
        except ValueError as exc:
            parser.error(f"客户端网段无效：{exc}")
        if not networks:
            parser.error("局域网服务必须至少配置一个 --allowed-client-cidr")
        Handler.auth_password = password
        Handler.auth_secret = secrets.token_bytes(32)
        Handler.auth_networks = networks
        Handler.auth_supported = True
        Handler.password_required = True
        if Handler.access_path.exists():
            try:
                access = json.loads(Handler.access_path.read_text(encoding="utf-8"))
                configured = access.get("password_required") if isinstance(access, dict) else None
                if not isinstance(configured, bool):
                    parser.error("访问保护配置无效；请检查数据目录中的 access.json")
                Handler.password_required = configured
            except (OSError, json.JSONDecodeError) as exc:
                parser.error(f"无法读取访问保护配置：{exc}")
        else:
            Handler.access_path.parent.mkdir(parents=True, exist_ok=True)
            Handler.access_path.write_text(json.dumps({"password_required": True}) + "\n", encoding="utf-8")

    Handler.wb = Workbench(args.data)
    try:
        server = WorkbenchHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        parser.error(f"无法在 {args.host}:{args.port} 启动工作台：{exc}")
    local_server = None
    if not is_loopback:
        try:
            local_server = WorkbenchHTTPServer(("127.0.0.1", args.port), Handler)
        except OSError as exc:
            server.server_close()
            parser.error(f"无法启动本机回环地址 127.0.0.1:{args.port}：{exc}")
        threading.Thread(target=local_server.serve_forever, name="WorkbenchLoopback", daemon=True).start()
    print(f"Video Workbench: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if local_server is not None:
            local_server.shutdown()
            local_server.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
