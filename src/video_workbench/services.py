from __future__ import annotations
import base64, http.cookiejar, ipaddress, io, json, os, queue, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
from .core import parse_srt, write_srt

GATEWAY_API_PORT=8787
GATEWAY_MANAGE_PORT=9990

class OperationCancelled(RuntimeError):
    """Raised when the user asks the current workbench operation to stop."""

def _terminate_process(process):
    if process.poll() is not None: return
    process.terminate()
    try: process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait()

def executable(value):
    resolved=shutil.which(value) if value else None
    if resolved: return resolved
    if value and Path(value).is_file(): return str(Path(value).resolve())
    raise RuntimeError(f"找不到工具：{value or '未配置'}")

def list_directories(path=""):
    """List local folders for the workbench's in-page folder picker."""
    if sys.platform=="win32":
        roots=[f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{letter}:\\").is_dir()]
    else:
        roots=[str(Path.home()),"/"]
    if not path:
        return {"current":"","parent":"","directories":[{"name":root,"path":root} for root in roots]}
    target=Path(path).expanduser()
    if not target.exists(): raise FileNotFoundError(f"文件夹不存在：{target}")
    if not target.is_dir(): raise ValueError(f"目标不是文件夹：{target}")
    target=target.resolve()
    rows=[]
    try:
        children=target.iterdir()
        for child in children:
            try:
                if child.is_dir(): rows.append({"name":child.name,"path":str(child)})
            except OSError:
                continue
    except PermissionError as exc:
        raise PermissionError(f"没有权限读取文件夹：{target}") from exc
    rows.sort(key=lambda row:row["name"].casefold())
    parent=target.parent
    parent_path=str(parent) if parent!=target else ""
    return {"current":str(target),"parent":parent_path,"directories":rows}

def _directory_name(value):
    name=str(value or "").strip()
    if (not name or name in (".","..") or name[-1:] in ("."," ")
            or re.search(r'[<>:"/\\|?*\x00-\x1f]',name)):
        raise ValueError("文件夹名称无效；请勿使用路径分隔符或 Windows 保留字符")
    if name.split(".",1)[0].upper() in {"CON","PRN","AUX","NUL",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}:
        raise ValueError("文件夹名称不能使用 Windows 保留设备名")
    return name

def create_directory(parent,name):
    """Create one child folder inside the selected server-side directory."""
    if not str(parent or "").strip(): raise ValueError("请先选择当前文件夹")
    parent_path=Path(parent).expanduser().resolve(strict=True)
    if not parent_path.is_dir(): raise ValueError("当前路径不是文件夹")
    target=parent_path/_directory_name(name)
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink():
            raise FileExistsError(f"文件夹已存在：{target}；请直接选择该文件夹或使用其他名称")
        raise FileExistsError(f"同名文件或链接已存在：{target}；请使用其他名称")
    try:
        target.mkdir()
    except FileExistsError as exc:
        # Another request may have created the name after the check above.
        raise FileExistsError(f"目标名称已被占用：{target}；请直接选择该文件夹或使用其他名称") from exc
    return str(target.resolve())

def rename_directory(parent,old_name,new_name):
    """Rename one direct child folder without accepting arbitrary paths."""
    if not str(parent or "").strip(): raise ValueError("请先选择当前文件夹")
    parent_path=Path(parent).expanduser().resolve(strict=True)
    if not parent_path.is_dir(): raise ValueError("当前路径不是文件夹")
    source=parent_path/_directory_name(old_name)
    target=parent_path/_directory_name(new_name)
    if source.is_symlink() or not source.is_dir() or source.resolve().parent!=parent_path:
        raise FileNotFoundError("要重命名的子文件夹不存在")
    if target.exists() or target.is_symlink(): raise FileExistsError("目标文件夹名称已存在")
    source.rename(target)
    return str(target.resolve())

def reveal_path(path):
    """Open Windows Explorer at a folder or select a file in its parent folder."""
    if sys.platform!="win32": raise RuntimeError("定位文件需要 Windows 资源管理器")
    target=Path(path).expanduser().resolve()
    if not target.exists(): raise FileNotFoundError(f"文件或文件夹不存在：{target}")
    if target.is_dir():
        os.startfile(str(target))
        powershell=shutil.which("powershell.exe")
        if not powershell: raise RuntimeError("项目文件夹已打开，但找不到 PowerShell，无法将资源管理器切到前台")
        env=os.environ.copy(); env["VIDEO_WORKBENCH_REVEAL_PATH"]=str(target)
        script=("$target=$env:VIDEO_WORKBENCH_REVEAL_PATH; "+
                "$deadline=[DateTime]::UtcNow.AddSeconds(4); $shell=New-Object -ComObject WScript.Shell; "+
                "do { $window=Get-Process explorer -ErrorAction SilentlyContinue | "+
                "Where-Object { $_.MainWindowTitle -and $_.MainWindowTitle.IndexOf($target,[StringComparison]::OrdinalIgnoreCase) -ge 0 } | "+
                "Select-Object -First 1; if ($window -and $shell.AppActivate([int]$window.Id)) { exit 0 }; "+
                "Start-Sleep -Milliseconds 100 } while ([DateTime]::UtcNow -lt $deadline); exit 1")
        try:
            result=subprocess.run([powershell,"-NoProfile","-NonInteractive","-WindowStyle","Hidden","-Command",script],
                env=env,capture_output=True,text=True,timeout=6,creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"项目文件夹已打开，但资源管理器未能切到前台：{exc}") from exc
        if result.returncode:
            raise RuntimeError("项目文件夹已打开，但资源管理器未能切到前台")
    else:
        subprocess.Popen(["explorer.exe","/select,",str(target)],close_fds=True)
    return target

def run(argv,timeout=3600,cancel_event=None):
    if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
    process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors="replace",shell=False)
    deadline=time.monotonic()+timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(process)
                raise OperationCancelled("用户已停止任务")
            remaining=deadline-time.monotonic()
            if remaining<=0:
                _terminate_process(process)
                raise subprocess.TimeoutExpired(argv,timeout)
            try: stdout,stderr=process.communicate(timeout=min(.25,remaining))
            except subprocess.TimeoutExpired: continue
            if process.returncode: raise RuntimeError((stderr or stdout or "外部程序失败")[-1800:])
            return stdout
    finally:
        if process.poll() is None: _terminate_process(process)

def normalize_bilibili_url(value):
    """Accept a plain or Markdown-escaped Bilibili video URL, rejecting other hosts."""
    from urllib.parse import parse_qs,urlsplit,urlunsplit
    text=str(value or "").strip()
    for escaped,plain in ((r"\:",":"),(r"\.","."),(r"\(","("),(r"\)",")"),
                          (r"\[","["),(r"\]","]")):
        text=text.replace(escaped,plain)
    markdown=re.search(r"\]\((https?://[^)]+)\)",text,re.I)
    if markdown: text=markdown[1].strip()
    if text.startswith("<") and text.endswith(">"): text=text[1:-1].strip()
    try:
        parsed=urlsplit(text)
        port=parsed.port
    except ValueError as exc: raise ValueError("B 站链接格式无效") from exc
    host=(parsed.hostname or "").lower()
    allowed=host in ("bilibili.com","www.bilibili.com","m.bilibili.com","b23.tv") or host.endswith(".bilibili.com")
    if parsed.scheme.lower()!="https" or not allowed or parsed.username or parsed.password or port not in (None,443):
        raise ValueError("请输入 HTTPS 的 bilibili.com 或 b23.tv 视频链接")
    if host!="b23.tv" and not re.search(r"/video/(?:BV[0-9A-Za-z]+|av\d+)(?:/|$)",parsed.path,re.I):
        raise ValueError("请输入 B 站视频播放页链接")
    # Keep only the multipart selector; tracking parameters are not needed to fetch the video.
    part=parse_qs(parsed.query).get("p",[""])[0]
    query=f"p={part}" if part.isdigit() and part else ""
    return urlunsplit(("https",parsed.netloc,parsed.path,query,""))

def normalize_bilibili_cookie_file(value):
    """Keep only Bilibili cookies from a Netscape-format browser export."""
    try:
        text=value.decode("utf-8-sig") if isinstance(value,(bytes,bytearray)) else str(value or "").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise ValueError("Cookie 文件必须是 UTF-8 编码的 Netscape 格式") from exc
    if len(text.encode("utf-8"))>2*1024*1024: raise ValueError("Cookie 文件不能超过 2MB")
    lines=text.replace("\r\n","\n").replace("\r","\n").split("\n")
    header=lines[0].strip() if lines else ""
    if header not in ("# HTTP Cookie File","# Netscape HTTP Cookie File"):
        raise ValueError("请选择 Netscape 格式的 cookies.txt 文件")
    kept=[header]; has_sessdata=False
    for line in lines[1:]:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")): continue
        cookie_line=line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
        fields=cookie_line.split("\t")
        if len(fields)!=7: raise ValueError("Cookie 文件格式无效，请重新导出 Netscape 格式 cookies.txt")
        domain=fields[0].lstrip(".").lower()
        if domain!="bilibili.com" and not domain.endswith(".bilibili.com"): continue
        kept.append(line)
        if fields[5]=="SESSDATA" and fields[6]: has_sessdata=True
    if not has_sessdata:
        raise ValueError("Cookie 文件中没有找到 B 站 SESSDATA 登录信息，请先在浏览器登录 B 站后重新导出")
    return ("\r\n".join(kept)+"\r\n").encode("utf-8")

def bilibili_qr_login_start():
    """Create a Bilibili QR login and render its URL locally as an SVG data URI."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError as exc:
        raise RuntimeError("扫码登录需要 qrcode 组件，请在工作台 Python 环境执行：python -m pip install qrcode") from exc

    cookies=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    endpoint="https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    request=urllib.request.Request(endpoint,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Accept":"application/json, text/plain, */*",
        "Referer":"https://www.bilibili.com/"})
    try:
        with opener.open(request,timeout=15) as response:
            payload=json.loads(response.read(1024*1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"申请 B 站登录二维码失败：{exc}") from exc
    data=payload.get("data") if isinstance(payload,dict) else None
    if not isinstance(data,dict) or payload.get("code")!=0:
        message=payload.get("message","B 站没有返回二维码") if isinstance(payload,dict) else "B 站返回格式无效"
        raise RuntimeError(f"申请 B 站登录二维码失败：{message}")
    url=str(data.get("url") or "").strip()
    key=str(data.get("qrcode_key") or "").strip()
    try:
        parsed=urllib.parse.urlsplit(url)
        host=(parsed.hostname or "").lower()
        port=parsed.port
    except ValueError as exc:
        raise RuntimeError("B 站返回的登录二维码链接无效") from exc
    if (parsed.scheme.lower() not in ("http","https") or
            (host!="bilibili.com" and not host.endswith(".bilibili.com")) or
            parsed.username or parsed.password or port not in (None,443,80)):
        raise RuntimeError("B 站返回的登录二维码链接无效")
    if not key:
        key=(urllib.parse.parse_qs(parsed.query).get("qrcode_key") or [""])[0].strip()
    if not 16<=len(key)<=256 or not key.isascii() or any(ord(char)<33 or ord(char)>126 for char in key):
        raise RuntimeError("B 站返回的扫码凭据无效")
    # The QR link comes from Bilibili, but normalize it to TLS before showing it.
    url=urllib.parse.urlunsplit(("https",host,parsed.path,parsed.query,""))
    qr=qrcode.QRCode(version=None,error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image=qr.make_image(image_factory=SvgPathImage)
    stream=io.BytesIO()
    image.save(stream)
    return {"key":key,"opener":opener,"cookies":cookies,
            "image":"data:image/svg+xml;base64,"+base64.b64encode(stream.getvalue()).decode("ascii")}

def bilibili_qr_login_poll(login):
    """Poll one QR login, returning its state and Netscape cookies after success."""
    endpoint="https://passport.bilibili.com/x/passport-login/web/qrcode/poll?"
    endpoint+=urllib.parse.urlencode({"qrcode_key":login["key"]})
    request=urllib.request.Request(endpoint,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Accept":"application/json, text/plain, */*",
        "Referer":"https://www.bilibili.com/"})
    try:
        with login["opener"].open(request,timeout=15) as response:
            payload=json.loads(response.read(1024*1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"查询 B 站扫码状态失败：{exc}") from exc
    if not isinstance(payload,dict):
        raise RuntimeError("查询 B 站扫码状态失败：返回格式无效")
    data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
    code=data.get("code",payload.get("code"))
    if code==86101: return {"status":"waiting"}
    if code==86090: return {"status":"scanned"}
    if code==86038: return {"status":"expired"}
    if code!=0:
        raise RuntimeError(f"查询 B 站扫码状态失败：{data.get('message') or payload.get('message') or code}")
    rows=["# Netscape HTTP Cookie File"]
    for cookie in login["cookies"]:
        domain=(cookie.domain or "").lower()
        normalized=domain.lstrip(".")
        if normalized!="bilibili.com" and not normalized.endswith(".bilibili.com"):
            continue
        domain_field=("#HttpOnly_" if cookie.has_nonstandard_attr("HttpOnly") else "")+domain
        fields=(domain_field,"TRUE" if cookie.domain_initial_dot else "FALSE",cookie.path or "/",
                "TRUE" if cookie.secure else "FALSE",str(cookie.expires or 0),cookie.name,cookie.value)
        rows.append("\t".join(fields))
    try:
        cookie_file=normalize_bilibili_cookie_file("\n".join(rows)+"\n")
    except ValueError as exc:
        raise RuntimeError("B 站已确认扫码，但没有收到有效登录 Cookie，请重新扫码") from exc
    return {"status":"success","cookies":cookie_file}

def download_bilibili(url,directory,ffmpeg="ffmpeg",cookiefile=None):
    """Download one Bilibili video and available captions using yt-dlp."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("B 站链接导入需要 yt-dlp。请在工作台 Python 环境执行：python -m pip install yt-dlp") from exc
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    options={"outtmpl":str(directory/"%(id)s.%(ext)s"),
             "format":"bestvideo*+bestaudio/best","merge_output_format":"mkv",
             "writesubtitles":True,"writeautomaticsub":True,"subtitleslangs":["all"],
             "subtitlesformat":"srt/vtt/best","noplaylist":True,"max_filesize":25*1024**3,
             "retries":3,"fragment_retries":3,"socket_timeout":30,"quiet":True,"no_warnings":True}
    if ffmpeg and ffmpeg!="ffmpeg": options["ffmpeg_location"]=ffmpeg
    if cookiefile: options["cookiefile"]=str(cookiefile)
    try:
        with YoutubeDL(options) as downloader:
            info=downloader.extract_info(url,download=True)
        if isinstance(info,dict) and info.get("entries"):
            info=next((entry for entry in info["entries"] if entry),None)
        if not isinstance(info,dict): raise RuntimeError("B 站没有返回可导入的视频信息")
        candidates=[directory/str(info.get("filepath",""))] if info.get("filepath") else []
        videos=[path for path in candidates if path.is_file()]
        if not videos:
            videos=[path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".mp4",".mov",".mkv",".webm"}
                    and not re.search(r"\.f\d+(?:\.|$)",path.name)]
        if len(videos)!=1: raise RuntimeError("下载完成，但无法唯一确定合并后的视频文件")
        video=videos[0]
        if video.stat().st_size>25*1024**3: raise RuntimeError("视频超过 25GB 导入上限")
        captions=[path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in (".srt",".vtt")]
        def caption_rank(path):
            name=path.name.lower()
            for index,marker in enumerate(("zh-hans","zh-cn","ai-zh","zh-hant","zh","en")):
                if re.search(rf"(?:^|[._-]){re.escape(marker)}(?:[._-]|$)",name): return index
            return 100
        captions.sort(key=lambda path:(caption_rank(path),path.name.lower()))
        return {"video":video,"caption":captions[0] if captions else None,"captions":captions,
                "title":str(info.get("title") or info.get("id") or video.stem)}
    except Exception as exc:
        shutil.rmtree(directory,ignore_errors=True)
        raise RuntimeError(f"下载 B 站视频失败：{exc}") from exc

def stop_local_model(base,key,mode="gateway",timeout=240):
    """Stop only an explicitly selected self-hosted gateway; other servers are manual."""
    if mode=="manual": return "manual"
    if mode!="gateway": raise ValueError("本地模型生命周期设置无效")
    from urllib.parse import urlsplit,urlunsplit
    base=local_url(base)
    parsed=urlsplit(base)
    if parsed.port!=GATEWAY_API_PORT:
        raise RuntimeError("自建网关自动停止仅支持 API 8787；其他本地服务请切换为手动管理")
    host=parsed.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["): host="["+host+"]"
    url=urlunsplit((parsed.scheme,f"{host}:{GATEWAY_MANAGE_PORT}","/api/stop","",""))
    request=urllib.request.Request(url,data=b"",headers={"Accept":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            result=json.load(response)
    except urllib.error.HTTPError as exc:
        detail=exc.read(1200).decode("utf-8","replace")
        raise RuntimeError(f"网关停止推理引擎 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError,TimeoutError) as exc:
        raise RuntimeError(f"无法连接网关管理接口 {GATEWAY_MANAGE_PORT}：{exc}") from exc
    if not isinstance(result,dict) or result.get("ok") is not True:
        detail=result.get("error","") if isinstance(result,dict) else ""
        raise RuntimeError("网关未确认停止推理引擎"+(f"：{detail}" if detail else ""))
    try:
        killed=int(result.get("killed",0) or 0)
    except (TypeError,ValueError):
        killed=0
    if result.get("released") is not True:
        raise RuntimeError(f"网关未确认推理后端端口已释放（清理进程数：{killed}）")
    return "stopped" if killed>0 else "not loaded"

def run_ffmpeg_progress(argv,duration,callback,cancel_event=None):
    """Read FFmpeg's real output timestamp, not an estimated clock-based percent."""
    if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
    command=argv[:1]+["-progress","pipe:1","-nostats"]+argv[1:]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,errors="replace",bufsize=1,shell=False)
    messages=queue.Queue(); errors=[]
    def read_stdout():
        try:
            for line in process.stdout: messages.put(line)
        finally: messages.put(None)
    def read_stderr(): errors.append(process.stderr.read())
    stdout_thread=threading.Thread(target=read_stdout,daemon=True); stdout_thread.start()
    stderr_thread=threading.Thread(target=read_stderr,daemon=True); stderr_thread.start()
    try:
        stdout_done=False
        while not stdout_done:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(process)
                raise OperationCancelled("用户已停止任务")
            try: line=messages.get(timeout=.2)
            except queue.Empty:
                if process.poll() is not None and not stdout_thread.is_alive(): break
                continue
            if line is None:
                stdout_done=True
                continue
            if line.startswith(("out_time_us=","out_time_ms=")):
                try: seconds=int(line.split("=",1)[1])/1_000_000
                except ValueError: continue
                callback(min(99,max(0,round(seconds/max(duration,.001)*100))))
        deadline=time.monotonic()+21600
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(process)
                raise OperationCancelled("用户已停止任务")
            if time.monotonic()>=deadline:
                _terminate_process(process)
                raise subprocess.TimeoutExpired(command,21600)
            time.sleep(.2)
        return_code=process.returncode
        stderr_thread.join(timeout=2)
        if return_code: raise RuntimeError(((errors[0] if errors else "") or "FFmpeg 导出失败")[-1800:])
    finally:
        if process.poll() is None: _terminate_process(process)
        stdout_thread.join(timeout=1); stderr_thread.join(timeout=1)

def probe(tool,source,ffmpeg="ffmpeg"):
    try: probe_exe=executable(tool)
    except RuntimeError: probe_exe=None
    if probe_exe:
        data=json.loads(run([probe_exe,"-v","error","-show_format","-show_streams","-of","json",str(source)],60))
        duration=float(data.get("format",{}).get("duration",0)); streams=data.get("streams",[])
        if duration<=0 or not any(s.get("codec_type")=="video" for s in streams): raise RuntimeError("无法读取有效视频")
        video=next(s for s in streams if s.get("codec_type")=="video")
        audio=next((s for s in streams if s.get("codec_type")=="audio"),{})
        def number(value):
            try:
                pieces=str(value).split("/",1)
                result=float(pieces[0])/(float(pieces[1]) if len(pieces)>1 else 1)
                return result if result>0 else None
            except (TypeError,ValueError,ZeroDivisionError): return None
        bitrate=number(video.get("bit_rate"))
        if bitrate is None:
            total=number(data.get("format",{}).get("bit_rate")); audio_rate=number(audio.get("bit_rate")) or 0
            if total is not None: bitrate=max(250_000,total-audio_rate)
        audio_bitrate=number(audio.get("bit_rate"))
        return {"duration":duration,"has_audio":bool(audio),"video_bitrate":round(bitrate) if bitrate else None,
                "audio_bitrate":round(audio_bitrate) if audio_bitrate else None,
                "fps":number(video.get("avg_frame_rate")) or number(video.get("r_frame_rate")),
                "width":video.get("width"),"height":video.get("height"),"video_codec":video.get("codec_name"),
                "audio_codec":audio.get("codec_name"),"format_name":data.get("format",{}).get("format_name")}
    # Some user-provided FFmpeg bundles omit ffprobe. Reading ffmpeg's input
    # metadata lets those bundles run without copying another executable.
    result=subprocess.run([executable(ffmpeg),"-hide_banner","-i",str(source)],
                          capture_output=True,text=True,errors="replace",timeout=60,shell=False)
    output=result.stderr+"\n"+result.stdout
    match=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",output)
    if not match or not re.search(r"Stream\s+#\S+.*?Video:",output):
        raise RuntimeError("无法读取有效视频；建议配置完整的 ffprobe 路径")
    duration=int(match[1])*3600+int(match[2])*60+float(match[3])
    return {"duration":duration,"has_audio":bool(re.search(r"Stream\s+#\S+.*?Audio:",output)),
            "video_bitrate":None,"audio_bitrate":None,"fps":None,"width":None,"height":None,
            "video_codec":None,"audio_codec":None,"format_name":None}

def extract_audio(tool,source,target,cancel_event=None):
    temporary=target.with_name(f".{target.stem}.{uuid.uuid4().hex}.partial{target.suffix}")
    try:
        run([executable(tool),"-hide_banner","-loglevel","error","-y","-i",str(source),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(temporary)],14400,cancel_event)
        if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
        temporary.replace(target)
    finally: temporary.unlink(missing_ok=True)

def run_asr(template,audio,target,cancel_event=None,substitutions=None):
    if not isinstance(template,list) or not template: raise RuntimeError("请配置 ASR 命令模板，或导入 SRT")
    replacements={"audio":audio,"output":target,**(substitutions or {})}
    argv=[]
    for value in template:
        arg=str(value)
        for key,replacement in replacements.items(): arg=arg.replace("{"+key+"}",str(replacement))
        argv.append(arg)
    argv[0]=executable(argv[0]); run(argv,14400,cancel_event)
    if not target.is_file(): raise RuntimeError("ASR 未生成 JSON 文件")
    return json.loads(target.read_text(encoding="utf-8"))

def external_json(template, substitutions, target, timeout=3600,cancel_event=None):
    if not isinstance(template,list) or not template: return None
    argv=[str(x).format(**substitutions) for x in template]
    argv[0]=executable(argv[0]); run(argv,timeout,cancel_event)
    if not target.is_file(): raise RuntimeError("外部工具未生成 JSON 文件")
    return json.loads(target.read_text(encoding="utf-8"))

def model_url(base,provider="local"):
    from urllib.parse import urlparse
    value=str(base or "").strip()
    u=urlparse(value)
    host=u.hostname or ""
    try: private=ipaddress.ip_address(host).is_private
    except ValueError: private=host.lower() in ("localhost","localhost.localdomain")
    if u.username or u.password or u.query or u.fragment or not host:
        raise RuntimeError("模型 API 地址格式无效")
    try:
        if u.port is not None and not 1<=u.port<=65535: raise RuntimeError("模型 API 端口无效")
    except ValueError as exc:
        raise RuntimeError("模型 API 端口无效") from exc
    if provider=="online":
        if u.scheme!="https": raise RuntimeError("在线模型必须使用 HTTPS 地址")
    elif provider!="local":
        raise RuntimeError("模型服务类型无效")
    elif u.scheme!="http" or not private:
        raise RuntimeError("本地/局域网模型须使用本机或内网 HTTP 地址；在线模型请选择 HTTPS")
    return value.rstrip("/")

def local_url(base):
    """Validate and normalize a local or LAN HTTP endpoint."""
    return model_url(base,"local")

def chat(base,model,key,messages,max_tokens=1600,chat_template_kwargs=None,retry_unavailable=True,provider="local",cancel_event=None):
    url=model_url(base,provider)+"/chat/completions"
    payload={"model":model,"messages":messages,"temperature":0.2,"max_tokens":max_tokens}
    if chat_template_kwargs:
        payload["chat_template_kwargs"]=chat_template_kwargs
    body=json.dumps(payload).encode()
    headers={"Content-Type":"application/json"}
    if key: headers["Authorization"]="Bearer "+key
    req=urllib.request.Request(url,data=body,headers=headers,method="POST")
    for attempt in range(12):
        if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
        try:
            with urllib.request.urlopen(req,timeout=240) as response: data=json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail=exc.read(600).decode("utf-8","replace")
            try: error_type=json.loads(detail).get("error",{}).get("type")
            except (ValueError,AttributeError): error_type=None
            transient_gateway_error=(exc.code==503 and error_type in ("not_ready","host_offline","switch_stopping"))
            transient_backend_error=(exc.code==502 and error_type=="backend_unreachable")
            if retry_unavailable and (transient_gateway_error or transient_backend_error) and attempt<11:
                delay=min(5+attempt*2,20); deadline=time.monotonic()+delay
                while time.monotonic()<deadline:
                    if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
                    time.sleep(min(.2,deadline-time.monotonic()))
                continue
            raise RuntimeError(f"模型服务 HTTP {exc.code}: "+detail) from exc
    if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
    try:
        choice=data["choices"][0]
        content=choice["message"].get("content")
    except (KeyError,IndexError,TypeError):
        raise RuntimeError("服务未返回 Chat Completions 内容")
    if not isinstance(content,str) or not content.strip():
        reason=choice.get("finish_reason","unknown")
        raise RuntimeError(f"模型没有返回最终文本（finish_reason={reason}）；请关闭思考模式或增加输出长度后重试")
    return content

def models(base,key,provider="local"):
    req=urllib.request.Request(model_url(base,provider)+"/models")
    if key: req.add_header("Authorization","Bearer "+key)
    with urllib.request.urlopen(req,timeout=12) as response: value=json.load(response)
    return [{"id":m.get("id"),"active":m.get("active")} for m in value.get("data",[]) if m.get("id")]

def frame(tool,source,second,target,cancel_event=None,max_width=960):
    argv=[executable(tool),"-hide_banner","-loglevel","error","-y","-ss",str(second),"-i",str(source)]
    if max_width: argv +=["-vf",f"scale=w='min(iw,{int(max_width)})':h=-2"]
    argv +=["-frames:v","1","-q:v","2",str(target)]
    run(argv,120,cancel_event)

def image_message(path,prompt):
    paths=list(path) if isinstance(path,(list,tuple)) else [path]
    content=[{"type":"text","text":prompt}]
    for image_path in paths:
        image=base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append({"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+image}})
    return {"role":"user","content":content}

def _subtitle_filter_path(path):
    value=Path(path).resolve().as_posix()
    return value.replace("\\","\\\\").replace(":","\\:").replace("'","\\'").replace("[","\\[").replace("]","\\]")

def _export_batch(tool,source,target,keeps,has_audio,progress=None,fps="source",bitrate=None,encoder="h264_nvenc",resolution="source",cancel_event=None,seek=0,intermediate=False,webm=False,subtitle_path=None):
    if not keeps: raise RuntimeError("没有可保留的视频片段")
    filters=[]; inputs=[]
    for i,(a,b) in enumerate(keeps):
        a-=seek; b-=seek
        filters.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]"); inputs.append(f"[v{i}]")
        if has_audio:
            filters.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"); inputs.append(f"[a{i}]")
    size=None
    if resolution!="source":
        match=re.fullmatch(r"(\d{3,5})x(\d{3,5})",str(resolution))
        if not match: raise ValueError("输出分辨率无效")
        width,height=(int(value) for value in match.groups())
        if not 160<=width<=7680 or not 160<=height<=4320: raise ValueError("输出分辨率超出支持范围")
        size=(width,height)
    filters.append("".join(inputs)+f"concat=n={len(keeps)}:v=1:a={int(has_audio)}[vconcat]"+("[aout]" if has_audio else ""))
    video_label="vconcat"
    if size:
        width,height=size
        # Fit within the chosen bounding box, preserve aspect ratio, avoid
        # upscaling, and round dimensions down to values accepted by codecs.
        filters.append(f"[vconcat]scale=w='min(iw,{width})':h='min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2[vscaled]")
        video_label="vscaled"
    if subtitle_path:
        style="FontName=Microsoft YaHei,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=30"
        filters.append(f"[{video_label}]subtitles=filename='{_subtitle_filter_path(subtitle_path)}':force_style='{style}'[vout]")
        video_label="vout"
    filter_script=target.with_suffix(".filter_complex.txt")
    filter_script.write_text(";".join(filters),encoding="utf-8")
    # The generic option-file form keeps long filter graphs out of Windows command lines.
    argv=[executable(tool),"-hide_banner","-loglevel","error","-y"]
    if seek: argv +=["-ss",f"{seek:.3f}"]
    argv +=["-i",str(source),"-/filter_complex",str(filter_script),"-map",f"[{video_label}]"]
    webm=webm or target.suffix.lower()==".webm"
    if has_audio:
        argv +=["-map","[aout]"]
        if intermediate: argv +=["-c:a","pcm_s16le"]
        else: argv +=["-c:a","libopus","-b:a","128k"] if webm else ["-c:a","aac","-b:a","192k"]
    if fps!="source": argv +=["-r",str(fps)]
    if webm:
        argv +=["-c:v","libvpx-vp9","-deadline","good","-cpu-used","4"]
    else:
        codecs={
            "x264_medium":("libx264","medium"),"x264_fast":("libx264","veryfast"),
            "x264_slow":("libx264","slow"),"x264_ultrafast":("libx264","ultrafast"),
            "x265_medium":("libx265","medium"),"x265_fast":("libx265","fast"),
            "x265_slow":("libx265","slow"),"h264_nvenc":("h264_nvenc","p5"),
            "hevc_nvenc":("hevc_nvenc","p5")}
        codec,preset=codecs.get(encoder,codecs["h264_nvenc"])
        argv +=["-c:v",codec,"-preset",preset]
    if bitrate:
        target_bitrate=int(bitrate)
        argv +=["-b:v",str(target_bitrate)]
        if not webm: argv +=["-maxrate",str(round(target_bitrate*1.5)),"-bufsize",str(target_bitrate*2)]
        if encoder in ("h264_nvenc","hevc_nvenc") and not webm:
            argv +=["-rc","vbr","-cq","19" if encoder=="h264_nvenc" else "21"]
    else:
        if encoder in ("h264_nvenc","hevc_nvenc") and not webm:
            argv +=["-rc","vbr","-cq","19" if encoder=="h264_nvenc" else "21"]
        else:
            argv +=["-crf","32" if webm else ("26" if encoder.startswith("x265") else "20")]
    if target.suffix.lower() in (".mp4",".mov"): argv +=["-movflags","+faststart"]
    argv +=[str(target)]
    try:
        run_ffmpeg_progress(argv,sum(b-a for a,b in keeps),progress or (lambda _percent: None),cancel_event)
    finally: filter_script.unlink(missing_ok=True)

def _ffconcat_path(path):
    # ffconcat parses its own quoting, independently of subprocess argv quoting.
    return "'"+path.resolve().as_posix().replace("'","'\\''")+"'"

def _export_one(tool,source,target,keeps,has_audio,progress=None,fps="source",bitrate=None,encoder="h264_nvenc",resolution="source",cancel_event=None,subtitle_path=None):
    if not keeps: raise RuntimeError("没有可保留的视频片段")
    batch_size=60
    webm=target.suffix.lower()==".webm"
    if len(keeps)<=batch_size:
        return _export_batch(tool,source,target,keeps,has_audio,progress,fps,bitrate,encoder,resolution,cancel_event,subtitle_path=subtitle_path)

    # A single graph with hundreds of trim branches feeds every decoded frame
    # through every branch. Keep exact trim/atrim cuts, but bound each graph and
    # seek near its first interval. PCM avoids AAC/Opus encoder delay at joins.
    work_dir=target.with_name(target.name+".parts")
    work_dir.mkdir()
    chunks=[]; subtitle_chunks=[]; manifest=work_dir/"join.ffconcat"
    subtitle_rows=parse_srt(Path(subtitle_path).read_text(encoding="utf-8")) if subtitle_path else []
    total=sum(b-a for a,b in keeps); completed=0.
    try:
        for offset in range(0,len(keeps),batch_size):
            batch=keeps[offset:offset+batch_size]
            batch_duration=sum(b-a for a,b in batch)
            seek=max(0,int(batch[0][0])-2)
            chunk=work_dir/f"part_{len(chunks):05d}.mkv"
            chunks.append(chunk)
            chunk_subtitle=None
            if subtitle_rows:
                batch_end=completed+batch_duration
                cues=[]
                for row in subtitle_rows:
                    start=max(float(row["start"]),completed)
                    end=min(float(row["end"]),batch_end)
                    if end>start:
                        cues.append({"start":round(start-completed,3),"end":round(end-completed,3),"text":row["text"]})
                if cues:
                    chunk_subtitle=work_dir/f"part_{len(chunks)-1:05d}.srt"
                    chunk_subtitle.write_text(write_srt(cues),encoding="utf-8")
                    subtitle_chunks.append(chunk_subtitle)
            def batch_progress(percent,done=completed,length=batch_duration):
                if progress: progress(min(90,round(90*(done+length*percent/100)/total)))
            _export_batch(tool,source,chunk,batch,has_audio,batch_progress,fps,bitrate,encoder,resolution,cancel_event,seek,True,webm,chunk_subtitle)
            completed+=batch_duration
        manifest.write_text("ffconcat version 1.0\n"+"".join(f"file {_ffconcat_path(chunk)}\n" for chunk in chunks),encoding="utf-8")
        argv=[executable(tool),"-hide_banner","-loglevel","error","-y","-safe","0","-i",str(manifest),"-map","0:v:0","-c:v","copy"]
        if has_audio:
            argv +=["-map","0:a:0"]
            argv +=["-c:a","libopus","-b:a","128k"] if webm else ["-c:a","aac","-b:a","192k"]
        if target.suffix.lower() in (".mp4",".mov"): argv +=["-movflags","+faststart"]
        argv +=[str(target)]
        def join_progress(percent):
            if progress: progress(min(99,90+round(9*percent/100)))
        run_ffmpeg_progress(argv,total,join_progress,cancel_event)
    finally:
        manifest.unlink(missing_ok=True)
        for chunk in chunks: chunk.unlink(missing_ok=True)
        for chunk_subtitle in subtitle_chunks: chunk_subtitle.unlink(missing_ok=True)
        work_dir.rmdir()

def export_video(tool,source,target,keeps,has_audio,progress=None,fps="source",bitrate_mbps="source",source_bitrate=None,source_duration=None,encoder="h264_nvenc",resolution="source",cancel_event=None,subtitle_path=None):
    if bitrate_mbps=="source":
        bitrate=source_bitrate
        if not bitrate:
            duration=max(float(source_duration or 0),1.)
            audio_rate=192_000 if has_audio else 0
            bitrate=max(250_000,round(source.stat().st_size*8/duration-audio_rate))
    else: bitrate=round(float(bitrate_mbps)*1_000_000)
    partial=target.with_name(f".{target.stem}.{uuid.uuid4().hex}.partial{target.suffix}")
    try:
        _export_one(tool,source,partial,keeps,has_audio,progress,fps,bitrate,encoder,resolution,cancel_event,subtitle_path)
        if cancel_event is not None and cancel_event.is_set(): raise OperationCancelled("用户已停止任务")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    if progress: progress(100)
