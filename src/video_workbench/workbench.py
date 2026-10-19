from __future__ import annotations
import base64, hashlib, html, json, math, os, re, shutil, statistics, sys, threading, time, uuid
import unicodedata
from pathlib import Path
from .core import normalize_segments, parse_srt, parse_json_object, validate_suggestions, keep_ranges, remap_segments, subtitle_cues, write_srt
from . import services

ASR_DEFAULT_COMMAND=["python","{asr_adapter}","--input","{audio}","--output","{output}",
                     "--model","{asr_model}","--aligner","{asr_aligner}"]
DEFAULTS={"ffmpeg":"ffmpeg","ffprobe":"ffprobe","asr_command":ASR_DEFAULT_COMMAND,
          "asr_config_version":"1",
          "qwen3_asr_model":"",
          "qwen3_aligner_model":"",
          "ocr_command":[],"scene_command":[],
          "model_provider":"local","base_url":"http://127.0.0.1:8787/v1","local_model_lifecycle":"gateway","text_model":"","summary_model":"","vision_model":"",
          "model_profiles":[],"model_roles":{"editing":[],"summary":[],"vision":[]},
          "model_config_version":"2",
          "output_mode":"data","output_dir":"","video_output_dir":"","notes_output_dir":"","video_container":"source","video_fps":"source",
          "video_resolution":"source","video_resolution_width":"1920","video_resolution_height":"1080",
          "video_bitrate_mbps":"source","video_bitrate_custom_mbps":"5","video_encoder":"h264_nvenc",
          "html_include_images":"true","html_include_image_notes":"true"}
VIDEO_SUFFIXES={".mp4",".mov",".mkv",".webm"}
ACTIONS=("transcribe","analyze","export","summary","notes","document","timestamp_document","punctuate")
DOCUMENT_ARTIFACT_KEYS=("subtitle","html","full_html","markdown","full_markdown","timestamp_markdown","transcript")
TEMPORARY_FILE_NAMES=("audio.wav","asr.json","scenes.json")
SUMMARY_MODES={
    "detailed":"详细模式：保留重要背景、过程、依据、结论、专有名词和数字；每章用 2–4 句说明，并列出 3–5 条关键细节。",
    "concise":"简约模式：只保留每个主题最核心的结论和必要信息；每章用 1 句概括，最多列出 2 条要点。",
    "medium":"中档模式：兼顾核心结论和主要依据；每章用 1–2 句概括，并列出 2–3 条要点。",
}

def safe_name(value):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]',"_",str(value)).strip(" .")
    return name[:80] or "video"

def select_frame_times(start,end,scene_times):
    """Choose up to three spread-out frames, nudging toward nearby scene cuts."""
    span=end-start
    if span<=0: return []
    fractions=(0.5,) if span<90 else (0.25,0.5,0.75)
    chosen=[]
    for fraction in fractions:
        target=start+span*fraction
        nearby=[t for t in scene_times if start<t<end and abs(t-target)<=span/8]
        candidate=min(nearby,key=lambda t:abs(t-target)) if nearby else target
        candidate=round(min(end-0.05,max(start+0.05,candidate)),3)
        if candidate not in chosen: chosen.append(candidate)
    return chosen

def transcript_paragraphs(segments,max_chars=180,max_gap=1.8,layout=None,highlight_intervals=None):
    paragraphs=[]; chars=[]; highlighted=[]; start=None; end=None
    intervals=sorted((float(row["start"]),float(row["end"]),str(row.get("category","other")))
                     for row in (highlight_intervals or []) if isinstance(row,dict)
                     and float(row.get("start",0))<float(row.get("end",0)))
    def highlight_class(char_start,char_end):
        for start_time,end_time,category in intervals:
            if start_time < char_end and end_time > char_start:
                safe_category=category.lower()
                if any(word in safe_category for word in ("manual","手动")): return "deleted-manual"
                if any(word in safe_category for word in ("filler","discourse","语气","口头禅")): return "deleted-filler"
                if any(word in safe_category for word in ("repeat","repetition","duplicate","重复")): return "deleted-repeat"
                if any(word in safe_category for word in ("restart","rephrase","false_start","重新起句")): return "deleted-restart"
                return "deleted-other"
        return ""
    def flush():
        nonlocal start,end
        text="".join(chars).strip()
        if text:
            paragraph={"id":f"p{len(paragraphs)+1:06d}","start":round(start,3),"end":round(end,3),"text":text}
            if highlight_intervals is not None:
                parts=[]; active_class=""; active_text=[]
                def flush_part():
                    if not active_text: return
                    escaped=html.escape("".join(active_text))
                    parts.append(f'<mark class="{active_class}">{escaped}</mark>' if active_class else escaped)
                    active_text.clear()
                for char,char_class in highlighted:
                    if char_class!=active_class:
                        flush_part(); active_class=char_class
                    active_text.append(char)
                flush_part()
                paragraph["html"]="".join(parts)
            paragraphs.append(paragraph)
        chars.clear(); highlighted.clear(); start=end=None
    def append(char,char_start,char_end,split_on_sentence=False,category=""):
        nonlocal start,end
        if chars and char_start-float(end)>max_gap: flush()
        if start is None: start=char_start
        chars.append(char); highlighted.append((char,category)); end=char_end
        if len(chars)>=max_chars or (split_on_sentence and char in "。！？!?；;"): flush()
    active=bool(layout and isinstance(layout.get("markers"),list) and layout.get("markers"))
    markers={(str(row.get("segment_id","")),int(row.get("offset",0))):row
             for row in (layout or {}).get("markers",[]) if isinstance(row,dict)}
    for row in segments:
        value=str(row.get("text",""))
        if not value: continue
        row_start,row_end=float(row["start"]),float(row["end"])
        duration=max(0.,row_end-row_start)
        for index,char in enumerate(value):
            char_start=row_start+duration*index/len(value)
            char_end=row_start+duration*(index+1)/len(value)
            marker=markers.get((str(row.get("id","")),index),{}) if active else {}
            for punctuation in marker.get("before",""):
                append(punctuation,char_start,char_start)
            if not active or not unicodedata.category(char).startswith("P"):
                append(char,char_start,char_end,split_on_sentence=not active,
                       category=highlight_class(char_start,char_end) if intervals else "")
            after=markers.get((str(row.get("id","")),index+1),{}) if active else {}
            for punctuation in after.get("after",""):
                append(punctuation,char_end,char_end)
            if after.get("paragraph"): flush()
    flush()
    return paragraphs

def paragraph_batches(paragraphs,max_chars=14000,max_rows=140):
    batches=[]; current=[]; size=0
    for row in paragraphs:
        row_size=len(row["text"])+32
        if current and (size+row_size>max_chars or len(current)>=max_rows):
            batches.append(current); current=[]; size=0
        current.append(row); size+=row_size
    if current: batches.append(current)
    return batches

def transcript_content_char(char):
    return not char.isspace() and not unicodedata.category(char).startswith("P")

def transcript_layout_from_text(segments,formatted):
    positions=[]; original=[]
    for row in segments:
        value=str(row.get("text","")); original.append(value)
        for index,char in enumerate(value):
            if transcript_content_char(char): positions.append((str(row["id"]),index))
    source="".join(char for char in "".join(original) if transcript_content_char(char))
    result="".join(char for char in formatted if transcript_content_char(char))
    if source!=result:
        raise ValueError("模型改动了字幕文字，未应用标点结果；原字幕保持不变")
    markers={}; cursor=0; previous=None; pending_before=""
    opening=set("“‘（([{《「『【〈〔［｛")
    def marker_at(segment_id,offset):
        key=(segment_id,offset)
        return markers.setdefault(key,{"segment_id":segment_id,"offset":offset,"before":"","after":"","paragraph":False})
    for char in formatted:
        if transcript_content_char(char):
            if cursor>=len(positions): raise ValueError("标点结果与字幕字符对齐失败")
            segment_id,index=positions[cursor]
            if pending_before: marker_at(segment_id,index)["before"]+=pending_before; pending_before=""
            cursor+=1; previous=(segment_id,index+1)
        elif unicodedata.category(char).startswith("P"):
            if char in opening or previous is None: pending_before+=char
            else: marker_at(*previous)["after"]+=char
        elif char in "\r\n" and previous:
            marker_at(*previous)["paragraph"]=True
    for row in segments:
        value=str(row.get("text","")); utf16=0
        offsets={0:0}
        for index,char in enumerate(value):
            utf16+=len(char.encode("utf-16-le"))//2
            offsets[index+1]=utf16
        segment_id=str(row["id"])
        for offset,utf16_offset in offsets.items():
            marker=markers.get((segment_id,offset))
            if marker: marker["offset_utf16"]=utf16_offset
    return {"markers":list(markers.values()),"source_chars":len(source)}

def format_transcript_text(segments,layout=None):
    active=bool(layout and isinstance(layout.get("markers"),list) and layout.get("markers"))
    markers={(row.get("segment_id"),int(row.get("offset",0))):row for row in (layout or {}).get("markers",[])}
    output=[]
    for segment in segments:
        value=str(segment.get("text","")); sid=str(segment.get("id",""))
        for index,char in enumerate(value):
            before=markers.get((sid,index),{}) if active else {}
            after=markers.get((sid,index+1),{}) if active else {}
            if before.get("before"): output.append(before["before"])
            if not active or not unicodedata.category(char).startswith("P"): output.append(char)
            if after.get("after"): output.append(after["after"])
            if after.get("paragraph"): output.append("\n\n")
    return "".join(output).strip()

def clean_summary_text(value,limit=3000):
    text=str(value or "").strip()
    text=re.sub(r"(?:本段|这一段|这段|该|本)?视频(?:开场)?(?:确认|强调|讲解|分析|介绍|回顾|提到|指出|展示)(?:了)?", "", text)
    text=re.sub(r"\s+"," ",text).strip(" ，,、：:；;")
    return text[:limit]

def topic_frame_times(chapter,anchor,scene_times,duration):
    if not anchor: return []
    low,high=float(chapter["start"]),min(float(chapter["end"]),float(duration))
    if high-low<=.1: return []
    target=(float(anchor["start"])+float(anchor["end"]))/2
    target=max(low+.05,min(high-.05,target))
    candidates=[target]
    nearby=sorted((float(t) for t in scene_times if low<t<high and abs(float(t)-target)<=6),key=lambda t:abs(t-target))
    if nearby:
        candidates.extend(nearby[:2])
    else:
        delta=min(1.5,max(.25,(float(anchor["end"])-float(anchor["start"]))/2))
        candidates.extend(t for t in (target-delta,target+delta) if low+.05<t<high-.05)
    output=[]
    for value in candidates:
        value=round(max(0.,min(duration-.05,value)),3)
        if value not in output: output.append(value)
    return output

def chapters_from_ranges(rows,paragraphs):
    if not isinstance(rows,list) or not rows: raise ValueError("模型没有返回主题章节")
    indices={row["id"]:index for index,row in enumerate(paragraphs)}
    chapters=[]; cursor=0
    for row in rows:
        if not isinstance(row,dict): raise ValueError("主题章节格式无效")
        first=indices.get(str(row.get("start_id",""))); last=indices.get(str(row.get("end_id","")))
        if first is None or last is None or first!=cursor or last<first:
            raise ValueError("主题章节时间范围不连续")
        units=paragraphs[first:last+1]; valid_ids={item["id"] for item in units}
        visual_id=str(row.get("visual_id") or "")
        if visual_id not in valid_ids: visual_id=""
        title=clean_summary_text(row.get("title",""),120) or clean_summary_text(units[0]["text"],32) or "相关主题"
        bullets=row.get("bullets",[])
        if not isinstance(bullets,list): bullets=[]
        chapters.append({"start":units[0]["start"],"end":units[-1]["end"],"start_id":units[0]["id"],
            "end_id":units[-1]["id"],"title":title,"summary":clean_summary_text(row.get("summary","")),
            "bullets":[clean_summary_text(item,500) for item in bullets[:8] if clean_summary_text(item,500)],
            "visual_id":visual_id,"visual_description":clean_summary_text(row.get("visual_description",""),300),
            "paragraphs":units})
        cursor=last+1
    if cursor!=len(paragraphs): raise ValueError("模型返回的主题章节没有覆盖完整字幕")
    return chapters

class Workbench:
    def __init__(self,root:Path):
        self.root=root.resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.document_style_path=self.root/"document_style.json"
        packaged_style=Path(__file__).with_name("document_style.json")
        if not self.document_style_path.exists() and packaged_style.is_file():
            shutil.copyfile(packaged_style,self.document_style_path)
        self.settings_path=self.root/"settings.json"
        self.bilibili_cookies_path=self.root/".bilibili-cookies.txt"
        self.bilibili_qr_logins={}
        self.bilibili_qr_lock=threading.RLock()
        self.timing_path=self.root/"timing_history.json"
        self.timing_history=json.loads(self.timing_path.read_text(encoding="utf-8")) if self.timing_path.exists() else {}
        saved_output_settings={}
        if self.settings_path.exists():
            saved_output_settings=json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.folder_setup_required=not all(str(saved_output_settings.get(key,"")).strip()
            for key in ("video_output_dir","notes_output_dir"))
        self.settings={**DEFAULTS,"model_profiles":[],"model_roles":{"editing":[],"summary":[],"vision":[]}}
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        # Upgrade old installations whose ASR template was intentionally left
        # empty by earlier releases. Once upgraded, users can still clear it.
        if str(saved_output_settings.get("asr_config_version",""))!="1":
            if not saved_output_settings.get("asr_command"):
                self.settings["asr_command"]=ASR_DEFAULT_COMMAND.copy()
            self.settings["asr_config_version"]="1"
        if "local_model_lifecycle" not in saved_output_settings:
            # Preserve safe automatic cleanup for the default localhost gateway;
            # unknown or remote local servers require an explicit choice.
            from urllib.parse import urlsplit
            try:
                endpoint=urlsplit(str(self.settings.get("base_url",DEFAULTS["base_url"])))
                is_default_gateway=endpoint.hostname in ("127.0.0.1","localhost","::1") and endpoint.port==services.GATEWAY_API_PORT
            except ValueError:
                is_default_gateway=False
            self.settings["local_model_lifecycle"]="gateway" if is_default_gateway else "manual"
        if not str(self.settings.get("video_encoder") or "").strip():
            self.settings["video_encoder"]="h264_nvenc"
        # Migrate the previous three model fields once, while preserving a
        # deliberately empty model list after the user removes all profiles.
        if str(saved_output_settings.get("model_config_version","")) not in ("1","2"):
            profiles=[]; roles={"editing":[],"summary":[],"vision":[]}; ids_by_model={}
            legacy_models={"editing":self.settings.get("text_model",""),
                           "summary":self.settings.get("summary_model") or self.settings.get("text_model",""),
                           "vision":self.settings.get("vision_model","")}
            if self.settings.get("model_provider")=="online" and legacy_models["vision"]==DEFAULTS["vision_model"]:
                legacy_models["vision"]=""
            for role,model in legacy_models.items():
                model=str(model or "").strip()
                if not model: continue
                profile_id=ids_by_model.get(model)
                if profile_id is None:
                    profile_id="legacy-"+uuid.uuid4().hex[:12]
                    ids_by_model[model]=profile_id
                    profiles.append({"id":profile_id,"name":model,"model":model})
                roles[role].append(profile_id)
            self.settings["model_profiles"]=profiles
            self.settings["model_roles"]=roles
        else:
            self.settings["model_roles"]=self.settings.get("model_roles") or {"editing":[],"summary":[],"vision":[]}
        # Version 2 stores a complete service definition on every model entry.
        # Existing profiles inherit the previously shared service settings.
        inherited_provider=self.settings.get("model_provider","local")
        inherited_base=self.settings.get("base_url",DEFAULTS["base_url"])
        inherited_lifecycle=self.settings.get("local_model_lifecycle","manual")
        migrated_profiles=[]
        for row in self.settings.get("model_profiles",[]):
            if not isinstance(row,dict): continue
            migrated_profiles.append({**row,
                "provider":row.get("provider",inherited_provider),
                "base_url":row.get("base_url",inherited_base),
                "local_model_lifecycle":row.get("local_model_lifecycle",inherited_lifecycle)})
        self.settings["model_profiles"]=migrated_profiles
        self.settings["model_config_version"]="2"
        self.settings["video_output_dir"] = self.settings.get("video_output_dir") or str(self.root/"exports"/"videos")
        self.settings["notes_output_dir"] = self.settings.get("notes_output_dir") or str(self.root/"exports"/"notes")
        Path(self.settings["video_output_dir"]).expanduser().mkdir(parents=True,exist_ok=True)
        Path(self.settings["notes_output_dir"]).expanduser().mkdir(parents=True,exist_ok=True)
        # Ignore options removed from the current settings UI.
        self.settings.pop("finish_mode",None)
        self.settings.pop("model_stop_command",None)
        self.api_key=""
        self.model_profile_keys={}
        self.active_local_models={}
        self.busy=threading.Lock()
        self.cancel_event=threading.Event()
        self.task_resume_event=threading.Event()
        self.task_resume_event.set()
        self.task_control=services.TaskControl(self.cancel_event,self.task_resume_event)
        self.active_pid=None
        self.active_operation=None
        self.active_batch_id=None
        self.model_session_active=False
        self.active_model_id=None
        self.active_model_base=None
        self.active_model_key=""
        self.remote_model_used=False
        self.write_lock=threading.RLock()
        self.index_lock=threading.RLock()
        self.catalog_path=self.root/"catalog.json"
        self.batches_path=self.root/"batches.json"
        self.catalog=json.loads(self.catalog_path.read_text(encoding="utf-8")) if self.catalog_path.exists() else {}
        self.batches=json.loads(self.batches_path.read_text(encoding="utf-8")) if self.batches_path.exists() else {}
        for pid in list(self.catalog):
            try: project=self.read(pid,"project.json")
            except (FileNotFoundError,ValueError,OSError): continue
            if project.get("status")=="running":
                project.update(status="interrupted",step="上次任务中断，可重新运行")
                for row in project.get("progress",{}).values():
                    if row.get("state")=="running": row.update(state="interrupted",label="任务中断，可重新运行")
                self.write(pid,"project.json",project)
        interrupted=False
        for batch in self.batches.values():
            if batch.get("status")=="running":
                batch.update(status="interrupted",step="上次批量任务被中断，可重新运行")
                interrupted=True
        if interrupted: self._save_json(self.batches_path,self.batches)
    def _save_json(self,path,value):
        payload=json.dumps(value,ensure_ascii=False,indent=2)
        with self.write_lock:
            for attempt in range(8):
                temp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temp.write_text(payload,encoding="utf-8")
                    temp.replace(path)
                    return
                except PermissionError:
                    if attempt==7: raise
                    time.sleep(.08*(attempt+1))
                finally:
                    try:
                        temp.unlink(missing_ok=True)
                    except PermissionError:
                        pass
    def _read_project_json(self,path):
        with self.write_lock:
            for attempt in range(8):
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except PermissionError:
                    if attempt==7: raise
                    time.sleep(.08*(attempt+1))
    def save_bilibili_cookies(self,value):
        payload=services.normalize_bilibili_cookie_file(value)
        temporary=self.bilibili_cookies_path.with_name(f".{self.bilibili_cookies_path.name}.{uuid.uuid4().hex}.tmp")
        with self.write_lock:
            try:
                temporary.write_bytes(payload)
                if os.name!="nt": temporary.chmod(0o600)
                temporary.replace(self.bilibili_cookies_path)
            finally:
                temporary.unlink(missing_ok=True)
    def clear_bilibili_cookies(self):
        with self.write_lock:
            self.bilibili_cookies_path.unlink(missing_ok=True)
    def start_bilibili_qr_login(self):
        attempt=services.bilibili_qr_login_start()
        login_id=uuid.uuid4().hex
        now=time.monotonic()
        with self.bilibili_qr_lock:
            self.bilibili_qr_logins={key:value for key,value in self.bilibili_qr_logins.items()
                if value["expires"]>now}
            self.bilibili_qr_logins[login_id]={"attempt":attempt,"expires":now+180,
                "poll_lock":threading.Lock()}
        return {"login_id":login_id,"image":attempt["image"],"expires_in":180}
    def poll_bilibili_qr_login(self,login_id):
        if not re.fullmatch(r"[0-9a-f]{32}",str(login_id or "")):
            raise ValueError("B 站扫码会话无效，请重新生成二维码")
        with self.bilibili_qr_lock:
            state=self.bilibili_qr_logins.get(login_id)
            if not state:
                return {"status":"expired"}
            if state["expires"]<=time.monotonic():
                self.bilibili_qr_logins.pop(login_id,None)
                return {"status":"expired"}
            if not state["poll_lock"].acquire(blocking=False):
                return {"status":"waiting"}
        try:
            result=services.bilibili_qr_login_poll(state["attempt"])
            if result["status"]=="success":
                self.save_bilibili_cookies(result["cookies"])
            if result["status"] in ("success","expired"):
                with self.bilibili_qr_lock:
                    self.bilibili_qr_logins.pop(login_id,None)
            return {"status":result["status"]}
        finally:
            state["poll_lock"].release()
    def save_settings(self,settings):
        previous_provider=self.settings.get("model_provider","local")
        previous_base=self.settings.get("base_url","")
        allowed=set(DEFAULTS)
        changes={}
        profile_keys_update=None
        for key in allowed:
            if key in settings:
                value=settings[key]
                if key=="model_profiles":
                    if not isinstance(value,list) or len(value)>40: raise ValueError("模型配置最多支持 40 条")
                    cleaned=[]; seen=set(); profile_keys={}
                    old_profiles={str(row.get("id","")):row for row in self.settings.get("model_profiles",[]) if isinstance(row,dict)}
                    for row in value:
                        if not isinstance(row,dict): raise ValueError("模型配置格式错误")
                        profile_id=str(row.get("id","")).strip()
                        name=str(row.get("name","")).strip()[:100]
                        model=str(row.get("model","")).strip()[:300]
                        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",profile_id) or profile_id in seen:
                            raise ValueError("模型配置 ID 无效或重复")
                        provider=str(row.get("provider","local")).strip()
                        base_url=str(row.get("base_url","")).strip()
                        lifecycle=str(row.get("local_model_lifecycle","manual")).strip()
                        if provider not in ("local","online"): raise ValueError("模型服务类型无效")
                        if lifecycle not in ("gateway","manual"): raise ValueError("本地模型生命周期设置无效")
                        if not base_url: raise ValueError("请为每条模型配置填写模型 API 地址")
                        services.model_url(base_url,provider)
                        supplied_key=row.get("api_key","")
                        if not isinstance(supplied_key,str) or len(supplied_key)>4096: raise ValueError("API Key 格式无效")
                        seen.add(profile_id)
                        cleaned.append({"id":profile_id,"name":name or model,"model":model,"provider":provider,
                                        "base_url":base_url.rstrip("/"),"local_model_lifecycle":lifecycle})
                        old=old_profiles.get(profile_id,{})
                        same_endpoint=(old.get("provider","local")==provider and old.get("base_url","").rstrip("/")==base_url.rstrip("/"))
                        profile_keys[profile_id]=supplied_key or (self.model_profile_keys.get(profile_id,"") if same_endpoint else "")
                    changes[key]=cleaned
                    profile_keys_update=profile_keys
                    continue
                if key=="model_roles":
                    if not isinstance(value,dict): raise ValueError("任务模型选择格式错误")
                    changes[key]=value
                    continue
                if key.endswith("_command") and (not isinstance(value,list) or len(value)>30): raise ValueError("外部工具命令须为参数数组")
                if not key.endswith("_command") and not isinstance(value,str): raise ValueError("配置格式错误")
                if key=="output_mode" and value not in ("data","source","custom"): raise ValueError("项目保存方式无效")
                if key=="model_provider" and value not in ("local","online"): raise ValueError("模型服务类型无效")
                if key=="local_model_lifecycle" and value not in ("gateway","manual"): raise ValueError("本地模型生命周期设置无效")
                if key=="output_dir" and value and not Path(value).is_absolute(): raise ValueError("指定保存目录必须是绝对路径")
                if key in ("video_output_dir","notes_output_dir") and value and not Path(value).is_absolute():
                    raise ValueError("视频和笔记保存目录必须是绝对路径")
                if key=="video_container" and value not in ("source","mp4","mkv","mov","webm"): raise ValueError("视频封装格式无效")
                if key=="video_fps" and value not in ("source","23.976","24","25","29.97","30","50","59.94","60"):
                    raise ValueError("视频帧率无效")
                if key=="video_resolution" and value not in ("source","2160p","1440p","1080p","720p","480p","custom"):
                    raise ValueError("视频分辨率无效")
                if key in ("video_resolution_width","video_resolution_height") and value:
                    try: dimension=int(value)
                    except ValueError: raise ValueError("自定义分辨率须填写整数宽度和高度")
                    limit=7680 if key.endswith("width") else 4320
                    if not 160<=dimension<=limit: raise ValueError("自定义分辨率宽度须为 160–7680，高度须为 160–4320")
                if key=="video_encoder" and value not in ("x264_medium","x264_fast","x264_slow","x264_ultrafast","x265_medium","x265_fast","x265_slow","h264_nvenc","hevc_nvenc"):
                    raise ValueError("视频编码器无效")
                if key=="video_bitrate_mbps" and value not in ("source","custom","0.5","1","2","3","5","8","12","20","30"):
                    # Retain compatibility with settings saved by older versions,
                    # which accepted a numeric custom bitrate in this field.
                    try: bitrate=float(value)
                    except ValueError: raise ValueError("请选择码率预设或自定义")
                    if not math.isfinite(bitrate) or not .1<=bitrate<=100: raise ValueError("自定义码率范围为 0.1–100 Mbps")
                if key=="video_bitrate_custom_mbps" and value:
                    try: bitrate=float(value)
                    except ValueError: raise ValueError("请填写有效的自定义码率")
                    if not math.isfinite(bitrate) or not .1<=bitrate<=100: raise ValueError("自定义码率范围为 0.1–100 Mbps")
                changes[key]=value
        resolved={**self.settings,**changes}
        if "model_profiles" in changes or "model_roles" in changes:
            profiles=resolved.get("model_profiles",[])
            valid_ids={row["id"] for row in profiles}
            role_source=changes.get("model_roles",resolved.get("model_roles",{}))
            if not isinstance(role_source,dict): raise ValueError("任务模型选择格式错误")
            profile_by_id={row["id"]:row for row in profiles}
            cleaned_roles={}
            for role in ("editing","summary","vision"):
                selected=role_source.get(role,[])
                if not isinstance(selected,list) or len(selected)>40:
                    raise ValueError("任务候选模型格式错误")
                refs=[]; seen_refs=set()
                for selected_model in selected:
                    if isinstance(selected_model,dict):
                        profile_id=str(selected_model.get("profile_id",selected_model.get("id",""))).strip()
                        model=str(selected_model.get("model","")).strip()[:300]
                    else:
                        # Older settings stored only the profile ID; keep them working.
                        profile_id=str(selected_model).strip()
                        model=str(profile_by_id.get(profile_id,{}).get("model","")).strip()
                    if profile_id not in valid_ids: raise ValueError("任务选择了不存在的模型配置")
                    if not model: raise ValueError("任务候选模型缺少模型 ID")
                    ref_key=(profile_id,model)
                    if ref_key in seen_refs: continue
                    seen_refs.add(ref_key)
                    refs.append({"profile_id":profile_id,"model":model})
                # Preserve the explicit order shown in each task's model list.
                cleaned_roles[role]=refs
            changes["model_roles"]=cleaned_roles
        if any(key in changes for key in ("video_output_dir","notes_output_dir")) and not all(
                str(resolved.get(key,"")).strip() for key in ("video_output_dir","notes_output_dir")):
            raise ValueError("请同时设置视频保存文件夹和笔记保存文件夹")
        if resolved.get("video_resolution")=="custom":
            for key,limit in (("video_resolution_width",7680),("video_resolution_height",4320)):
                try: dimension=int(resolved[key])
                except (KeyError,ValueError): raise ValueError("自定义分辨率须填写整数宽度和高度")
                if not 160<=dimension<=limit: raise ValueError("自定义分辨率宽度须为 160–7680，高度须为 160–4320")
        if resolved.get("video_bitrate_mbps")=="custom":
            try: bitrate=float(resolved.get("video_bitrate_custom_mbps",""))
            except ValueError: raise ValueError("请填写自定义目标码率")
            if not math.isfinite(bitrate) or not .1<=bitrate<=100: raise ValueError("自定义码率范围为 0.1–100 Mbps")
        if changes.get("output_mode",self.settings["output_mode"])=="custom" and not changes.get("output_dir",self.settings["output_dir"]):
            raise ValueError("请先选择指定保存文件夹")
        self.settings.update(changes)
        if profile_keys_update is not None:
            self.model_profile_keys=profile_keys_update
        if all(str(self.settings.get(key,"")).strip() for key in ("video_output_dir","notes_output_dir")):
            self.folder_setup_required=False
        if "api_key" in settings: self.api_key=str(settings["api_key"])
        elif (self.settings.get("model_provider","local")!=previous_provider
              or self.settings.get("base_url","")!=previous_base):
            self.api_key=""
        self._save_json(self.settings_path,self.settings)

    def public_settings(self):
        result=self.settings.copy()
        result.pop("api_key",None)
        result["model_profiles"]=[{"id":str(row.get("id","")),"name":str(row.get("name","")),
            "model":str(row.get("model","")),"provider":str(row.get("provider","local")),
            "base_url":str(row.get("base_url","")),
            "local_model_lifecycle":str(row.get("local_model_lifecycle","manual")),
            "api_key_set":bool(self.model_profile_keys.get(str(row.get("id","")),""))}
            for row in result.get("model_profiles",[]) if isinstance(row,dict)]
        result["api_key_set"]=bool(self.api_key)
        result["bilibili_login_configured"]=self.bilibili_cookies_path.is_file()
        result["folder_setup_required"]=self.folder_setup_required
        return result
    def folder(self,pid):
        uuid.UUID(pid)
        path=Path(self.catalog.get(pid,self.root/pid)).resolve()
        project_path=path/"project.json"
        if not path.is_dir() or not project_path.is_file(): raise FileNotFoundError("项目不存在")
        project=self._read_project_json(project_path)
        if not isinstance(project,dict) or project.get("id")!=pid:
            raise FileNotFoundError("项目索引与目录不匹配")
        return path
    def read(self,pid,name,default=None):
        path=self.folder(pid)/name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    def write(self,pid,name,value):
        target=self.folder(pid)/name
        self._save_json(target,value)
    def create_batch(self,name,source_folder="",group_output=True):
        bid=str(uuid.uuid4())
        safe_batch_name=safe_name(name)
        with self.index_lock:
            self.batches[bid]={"id":bid,"name":safe_batch_name,"output_group_name":safe_batch_name,
                               "group_output":bool(group_output),"source_folder":source_folder,
                               "created":time.strftime("%Y-%m-%d %H:%M:%S"),"projects":[],
                               "status":"imported","step":"等待处理","completed":0,"failed":0,"skipped":0}
            self._save_json(self.batches_path,self.batches)
        return bid
    def list_batches(self):
        with self.index_lock:
            return sorted((dict(row) for row in self.batches.values()),key=lambda x:x["created"],reverse=True)
    def _target_folder(self,pid,filename,source_path,batch_id):
        mode=self.settings["output_mode"]
        if mode=="source" and source_path is None:
            raise ValueError("原目录保存需要通过“选择工作区”导入，浏览器上传无法读取视频原路径")
        batch=self.batches.get(batch_id) if batch_id else None
        if mode=="source":
            parent=source_path.parent
            base=parent/(safe_name(parent.name)+"_workbench") if batch else parent
        elif mode=="custom":
            configured=self.settings["output_dir"]
            if not configured: raise ValueError("请先设置项目保存目录")
            base=Path(configured).expanduser()
            if batch: base=base/safe_name(batch["name"])
        else:
            base=self.root/"projects"
            if batch: base=base/safe_name(batch["name"])
        return (base/(safe_name(Path(filename).stem)+"_"+pid[:8])).resolve()
    def create(self,filename,stream,size,source_path=None,batch_id=None):
        suffix=Path(filename).suffix.lower()
        if suffix not in VIDEO_SUFFIXES: raise ValueError("支持 MP4、MOV、MKV、WEBM")
        if size<=0 or size>25*1024**3: raise ValueError("文件大小无效或超过 25GB")
        if batch_id and batch_id not in self.batches: raise ValueError("批次不存在")
        pid=str(uuid.uuid4()); folder=self._target_folder(pid,filename,source_path,batch_id)
        folder.mkdir(parents=True,exist_ok=False)
        source=folder/("source"+suffix) if source_path is None else source_path.resolve()
        try:
            if source_path is None:
                with source.open("wb") as target:
                    remaining=size
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk: raise RuntimeError("上传中断")
                        target.write(chunk); remaining-=len(chunk)
            now=time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_json(folder/"project.json",{"id":pid,"name":Path(filename).name,"source":source.name,
                "source_path":str(source) if source_path else "","output_path":str(folder),"batch_id":batch_id or "",
                "created":now,"updated":now,"status":"imported","step":"等待分析","error":""})
            with self.index_lock:
                self.catalog[pid]=str(folder)
                self._save_json(self.catalog_path,self.catalog)
                if batch_id:
                    self.batches[batch_id]["projects"].append(pid)
                    self._save_json(self.batches_path,self.batches)
        except Exception:
            if source_path is None: source.unlink(missing_ok=True)
            (folder/"project.json").unlink(missing_ok=True)
            try: folder.rmdir()
            except OSError: pass
            with self.index_lock:
                self.catalog.pop(pid,None)
                self._save_json(self.catalog_path,self.catalog)
                if batch_id:
                    if pid in self.batches[batch_id]["projects"]: self.batches[batch_id]["projects"].remove(pid)
                    self._save_json(self.batches_path,self.batches)
            raise
        return pid
    def import_folder(self,directory):
        raw=str(directory or "").strip().replace(r"\:",":")
        link_text=raw.replace(r"\(","(").replace(r"\)",")")
        if link_text.lower().startswith(("https://","http://","<https://","<http://")) or re.search(r"\]\(https?://",link_text,re.I):
            return self.import_bilibili(raw)
        source_dir=Path(directory).expanduser().resolve()
        if not source_dir.is_dir(): raise ValueError("工作区文件夹不存在")
        videos=sorted((p for p in source_dir.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in VIDEO_SUFFIXES),key=lambda p:p.name.lower())
        if not videos: raise ValueError("该文件夹内没有支持的视频文件")
        if len(videos)>500: raise ValueError("一次最多导入 500 个视频")
        bid=self.create_batch(source_dir.name,str(source_dir),group_output=True)
        skipped=[]
        for path in videos:
            try:
                pid=self.create(path.name,None,path.stat().st_size,path,bid)
                sidecar=next((path.with_suffix(suffix) for suffix in (".srt",".vtt") if path.with_suffix(suffix).is_file()),None)
                if sidecar:
                    try: self.subtitles(pid,sidecar.read_text(encoding="utf-8-sig"))
                    except Exception as exc: self.status(pid,"error","同名字幕导入失败",str(exc)[:500])
            except Exception as exc: skipped.append({"name":path.name,"error":str(exc)})
        self.batches[bid]["step"]=f"已导入 {len(self.batches[bid]['projects'])} 个视频"
        self._save_json(self.batches_path,self.batches)
        return {"batch_id":bid,"count":len(self.batches[bid]["projects"]),"skipped":skipped}
    def import_bilibili(self,value):
        url=services.normalize_bilibili_url(value)
        download_id=uuid.uuid4().hex[:8]
        downloads=self.root/"downloads"
        downloads.mkdir(parents=True,exist_ok=True)
        result=services.download_bilibili(url,downloads/("bilibili_"+download_id),
            self.settings.get("ffmpeg","ffmpeg"),
            self.bilibili_cookies_path if self.bilibili_cookies_path.is_file() else None)
        video=result["video"]
        filename=safe_name(result["title"])+video.suffix.lower()
        named_video=video.with_name(filename)
        if named_video!=video: video.rename(named_video)
        bid=self.create_batch(result["title"],str(named_video.parent),group_output=False)
        pid=self.create(named_video.name,None,named_video.stat().st_size,named_video,bid)
        project=self.read(pid,"project.json")
        project["source_url"]=url
        self.write(pid,"project.json",project)
        captions=result.get("captions") or ([result["caption"]] if result.get("caption") else [])
        caption_imported=False
        caption_errors=[]
        for caption in captions:
            if not caption.is_file(): continue
            try:
                self.subtitles(pid,caption.read_text(encoding="utf-8-sig"))
                caption_imported=bool(self.read(pid,"segments.json",[]))
                if caption_imported: break
            except Exception as exc:
                caption_errors.append(f"{caption.name}: {exc}")
        if not caption_imported and caption_errors:
            self.status(pid,"error","B 站字幕无法解析，将使用语音转写","；".join(caption_errors)[:500])
        self.batches[bid]["step"]=("已导入 B 站视频和字幕" if caption_imported else
            "已导入 B 站视频，字幕无法解析，等待语音转写" if captions else
            "已导入 B 站视频，未找到字幕，等待语音转写")
        self._save_json(self.batches_path,self.batches)
        return {"batch_id":bid,"project_id":pid,"count":1,"skipped":[],"subtitle":caption_imported,
                "subtitle_available":bool(captions),"name":named_video.name}
    def list_projects(self):
        out=[]
        paths=[Path(p) for p in self.catalog.values()]
        paths.extend(p for p in self.root.iterdir() if p.is_dir() and (p/"project.json").exists())
        seen=set()
        for path in paths:
            if path in seen: continue
            seen.add(path)
            if path.is_dir() and (path/"project.json").exists():
                try:
                    row=json.loads((path/"project.json").read_text(encoding="utf-8"))
                    row.setdefault("output_path",str(path.resolve()))
                    segments_path=path/"segments.json"
                    row["has_segments"]=segments_path.is_file() and segments_path.stat().st_size>2
                    out.append(row)
                except (ValueError,OSError): pass
        return sorted(out,key=lambda p:(p.get("updated",p["created"]),p["id"]),reverse=True)
    def status(self,pid,status,step,error=""):
        p=self.read(pid,"project.json"); p.update(status=status,step=step,error=error,updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.write(pid,"project.json",p)
    def check_cancelled(self):
        self.task_control.wait_if_paused()
        if self.cancel_event.is_set(): raise services.OperationCancelled("用户已停止任务")
    def task_control_status(self):
        running=self.busy.locked()
        paused=running and not self.task_resume_event.is_set()
        parked=paused and self.task_control.parked_event.is_set()
        return {"running":running,"paused":paused,"pause_pending":paused and not parked,
                "operation":self.active_operation,"project_id":self.active_pid,"batch_id":self.active_batch_id}
    def set_task_paused(self,paused):
        if not isinstance(paused,bool): raise ValueError("暂停状态无效")
        if not self.busy.locked(): raise RuntimeError("当前没有运行中的任务")
        if paused: self.task_resume_event.clear()
        else: self.task_resume_event.set()
        return self.task_control_status()
    def begin_operation(self,pid,operation):
        self.cancel_event.clear()
        self.task_resume_event.set()
        self.active_pid=pid
        self.active_operation=operation
        project=self.read(pid,"project.json")
        project["active_operation"]=operation
        project.pop("stop_requested",None)
        self.write(pid,"project.json",project)
    def end_operation(self,pid):
        try:
            project=self.read(pid,"project.json")
            project.pop("active_operation",None)
            project.pop("stop_requested",None)
            self.write(pid,"project.json",project)
        except Exception: pass
        self.active_pid=None
        self.active_operation=None
        self.cancel_event.clear()
        self.task_resume_event.set()
    def cancel(self,pid):
        if not self.busy.locked() or self.active_pid!=pid:
            raise RuntimeError("这个项目当前没有可停止的任务")
        project=self.read(pid,"project.json")
        if project.get("status")!="running": raise RuntimeError("当前操作已经结束")
        self.cancel_event.set()
        project["stop_requested"]=True
        project["step"]="正在停止当前操作…"
        project["updated"]=time.strftime("%Y-%m-%d %H:%M:%S")
        for row in project.get("progress",{}).values():
            if row.get("state")=="running": row["label"]="正在停止…"
        self.write(pid,"project.json",project)
    def cancel_batch(self,bid):
        batch=self.batches.get(bid)
        if not batch or batch.get("status")!="running" or self.active_batch_id!=bid or not self.busy.locked():
            raise RuntimeError("这个批次当前没有可停止的任务")
        self.cancel_event.set()
        pid=self.active_pid
        if pid:
            project=self.read(pid,"project.json")
            project["stop_requested"]=True
            project["step"]="正在停止当前批量任务…"
            project["updated"]=time.strftime("%Y-%m-%d %H:%M:%S")
            self.write(pid,"project.json",project)
    def _timing_profile(self,action):
        if action=="transcribe": value=self.settings.get("asr_command",[])
        elif action in ("analyze","punctuate"): value=[self.settings.get("model_provider","local"),self.settings.get("base_url",""),self.settings.get("model_profiles",[]),self.settings.get("model_roles",{}).get("editing",[])]
        elif action in ("summary","notes"): value=[self.settings.get("model_provider","local"),self.settings.get("base_url",""),self.settings.get("model_profiles",[]),self.settings.get("model_roles",{})]
        else: value=self.settings.get("ffmpeg","")
        serialized=json.dumps(value,ensure_ascii=False,separators=(",",":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    def _task_workload(self,pid,action):
        media=self.read(pid,"media.json",{}) or {}
        duration=max(0.,float(media.get("duration",0) or 0))
        segments=self.read(pid,"segments.json",[])
        segment_count=len(segments)
        text_chars=sum(len(str(row.get("text",""))) for row in segments)
        if action=="transcribe":
            units=duration
        elif action=="analyze":
            units=max(1.,math.ceil(segment_count/80)+text_chars/5000)
        elif action=="export":
            suggestions=self.read(pid,"suggestions.json",[])
            deletes=[(row["start"],row["end"]) for row in suggestions if row.get("selected")]
            kept=sum(end-start for start,end in keep_ranges(deletes,duration)) if duration else 0.
            units=max(1.,kept or duration)
        else:
            paragraphs=transcript_paragraphs(segments,layout=self.read(pid,"transcript_layout.json"))
            units=max(1.,len(paragraphs)+text_chars/2500)
        return {"workload":units,"video_seconds":duration,"segment_count":segment_count,"text_characters":text_chars}
    def _timing_rate(self,action,profile):
        rows=self.timing_history.get(action,[])
        rates=[float(row["elapsed_seconds"])/float(row["workload"]) for row in rows
               if row.get("profile")==profile and float(row.get("elapsed_seconds",0))>=1
               and float(row.get("workload",0))>0]
        return statistics.median(rates[-12:]) if rates else None
    def _record_timing(self,action,row,elapsed,metrics):
        if elapsed<1 or not metrics or metrics["workload"]<=0: return
        rows=self.timing_history.setdefault(action,[])
        rows.append({"elapsed_seconds":round(elapsed,2),**metrics,"profile":self._timing_profile(action),
                     "recorded_at":time.strftime("%Y-%m-%d %H:%M:%S")})
        del rows[:-12]
        self._save_json(self.timing_path,self.timing_history)
    def progress(self,pid,action,state,percent=None,label="",units_done=None,units_total=None):
        if action not in ACTIONS: raise ValueError("未知处理步骤")
        if state=="running" and self.cancel_event.is_set(): label="正在停止…"
        p=self.read(pid,"project.json")
        progress=p.setdefault("progress",{})
        old=progress.get(action,{})
        if state=="running" and old.get("state")!="running":
            old={key:value for key,value in old.items() if key not in
                 ("started_at","elapsed_seconds","estimated_seconds","estimate_source","workload",
                  "video_seconds","segment_count","text_characters","units_done","units_total")}
        row={**old,"state":state,"percent":percent,"label":label}
        if units_done is not None: row["units_done"]=float(units_done)
        if units_total is not None: row["units_total"]=float(units_total)
        now=time.time()
        profile=self._timing_profile(action)
        if state=="running":
            started=float(old.get("started_at",now)) if old.get("state")=="running" else now
            elapsed=max(0.,now-started)
            row["started_at"]=started
            row["elapsed_seconds"]=round(elapsed,1)
            metrics=None
            if old.get("state")!="running" or not row.get("workload"):
                metrics=self._task_workload(pid,action)
                row.update(metrics)
            else:
                metrics={key:row.get(key,0) for key in ("workload","video_seconds","segment_count","text_characters")}
            rate=self._timing_rate(action,profile)
            estimate=rate*float(row["workload"]) if rate and row.get("workload") else None
            source="历史项目" if estimate else ""
            done_units=float(row.get("units_done",0) or 0)
            total_units=float(row.get("units_total",row.get("workload",0)) or 0)
            if not estimate and done_units>0 and total_units>=done_units:
                estimate=elapsed*total_units/done_units
                source="本次速度"
            if not estimate and percent is not None and percent>0:
                estimate=elapsed*100/percent
                source="本次速度"
            if estimate:
                row["estimated_seconds"]=round(max(elapsed,estimate),1)
                row["estimate_source"]=source
            else:
                row.pop("estimated_seconds",None); row.pop("estimate_source",None)
        elif state=="done":
            started=old.get("started_at") if old.get("state")=="running" else None
            if started:
                elapsed=max(0.,now-float(started))
                metrics=self._task_workload(pid,action)
                self._record_timing(action,old,elapsed,metrics)
                row.update({**metrics,"started_at":float(started),"elapsed_seconds":round(elapsed,1),
                            "estimated_seconds":round(elapsed,1),"estimate_source":"实际耗时"})
        elif state=="pending":
            for key in ("started_at","elapsed_seconds","estimated_seconds","estimate_source","workload",
                        "video_seconds","segment_count","text_characters","units_done","units_total"):
                row.pop(key,None)
        progress[action]=row
        self.write(pid,"project.json",p)
    def detail(self,pid):
        project=self.read(pid,"project.json"); project.setdefault("output_path",str(self.folder(pid)))
        artifacts=self.available_artifacts(pid)
        project["artifacts"]=artifacts
        progress=project.setdefault("progress",{})
        for action,filename in (("transcribe","segments.json"),("analyze","suggestions.json"),
                                ("export",artifacts["video"]),("summary",artifacts["html"]),
                                ("notes",artifacts["markdown"]),
                                ("document",artifacts["full_markdown"]),
                                ("timestamp_document",artifacts["timestamp_markdown"])):
            if action not in progress and (self.folder(pid)/filename).is_file():
                progress[action]={"state":"done","percent":100,"label":"已完成"}
        segments=self.read(pid,"segments.json",[])
        layout=self.read(pid,"transcript_layout.json")
        transcript=self.folder(pid)/"transcript.txt"
        if segments and not transcript.exists():
            transcript.write_text(format_transcript_text(segments,layout),encoding="utf-8")
        return {"project":project,"media":self.read(pid,"media.json"),
            "segments":segments,
            "transcript_layout":layout,
            "suggestions":self.read(pid,"suggestions.json",[]),
            "summary":self.read(pid,"summary.json")}
    def transcript_text(self,pid,segments=None,layout=None):
        if segments is None: segments=self.read(pid,"segments.json",[])
        if layout is None: layout=self.read(pid,"transcript_layout.json")
        text=format_transcript_text(segments,layout)
        (self.folder(pid)/"transcript.txt").write_text(text,encoding="utf-8")
        self.deliver_artifact(pid,"transcript")
        return text
    def model_chat(self,base,model,key,messages,max_tokens=1600,provider=None,lifecycle="manual",**kwargs):
        """Track local model use for cleanup after the workbench task finishes."""
        self.check_cancelled()
        provider=provider or self.settings.get("model_provider","local")
        if not model: raise RuntimeError("请先在设置中选择或填写模型 ID")
        if provider=="local":
            self.model_session_active=True
            self.active_local_models[(base,key,lifecycle)]={"base":base,"key":key,"lifecycle":lifecycle}
            self.active_model_id=model
        else:
            if self.model_session_active:
                self.finish_model_session()
            self.remote_model_used=True
            kwargs.pop("chat_template_kwargs",None)
        result=services.chat(base,model,key,messages,max_tokens,provider=provider,
                             cancel_event=self.task_control,**kwargs)
        self.check_cancelled()
        return result

    def model_candidates(self,role):
        return [str(row.get("model","")) for row in self.model_candidate_profiles(role) if str(row.get("model",""))]

    def model_candidate_profiles(self,role):
        profiles=self.settings.get("model_profiles",[])
        roles=self.settings.get("model_roles",{})
        selected=roles.get(role,[]) if isinstance(roles,dict) else []
        if isinstance(roles,dict) and role in roles:
            profiles_by_id={str(row.get("id","")):row for row in profiles if isinstance(row,dict)}
            candidates=[]
            for item in selected if isinstance(selected,list) else []:
                if isinstance(item,dict):
                    profile_id=str(item.get("profile_id",item.get("id","")))
                    model=str(item.get("model","")).strip()
                else:
                    # Compatibility with model_roles saved before model IDs were
                    # stored alongside their service profile IDs.
                    profile_id=str(item)
                    model=str(profiles_by_id.get(profile_id,{}).get("model","")).strip()
                profile=profiles_by_id.get(profile_id)
                if not profile or not model: continue
                candidates.append({**profile,"model":model})
            return candidates
        legacy={"editing":self.settings.get("text_model",""),
                "summary":self.settings.get("summary_model") or self.settings.get("text_model",""),
                "vision":self.settings.get("vision_model","")}
        model=str(legacy.get(role,"")).strip()
        if not model: return []
        return [{"id":"legacy","model":model,"provider":self.settings.get("model_provider","local"),
            "base_url":self.settings.get("base_url",""),
            "local_model_lifecycle":self.settings.get("local_model_lifecycle","manual")}]

    def model_chat_role(self,role,messages,max_tokens=1600,**kwargs):
        candidates=self.model_candidate_profiles(role)
        if not candidates:
            labels={"editing":"剪辑建议模型","summary":"章节总结模型","vision":"画面识别模型"}
            raise RuntimeError(f"请在设置中为此任务选择{labels.get(role,'模型')}")
        errors=[]
        for profile in candidates:
            model=str(profile.get("model",""))
            provider=str(profile.get("provider","local"))
            base=str(profile.get("base_url",self.settings.get("base_url","")))
            key=self.model_profile_keys.get(str(profile.get("id","")),self.api_key if profile.get("id")=="legacy" else "")
            try:
                return self.model_chat(base,model,key,messages,max_tokens,provider=provider,
                    lifecycle=profile.get("local_model_lifecycle","manual"),**kwargs)
            except services.OperationCancelled:
                raise
            except Exception as exc:
                errors.append(f"{profile.get('name') or model}: {str(exc)[:300]}")
        raise RuntimeError(("按优先级尝试的模型均失败："+"；".join(errors))[-1800:])

    def finish_model_session(self,pid=None):
        if not self.model_session_active and not self.remote_model_used: return ""
        if self.model_session_active:
            results=[]
            for config in self.active_local_models.values():
                try:
                    result=services.stop_local_model(config["base"],config["key"],mode=config["lifecycle"])
                    if result=="manual": results.append("请到模型服务界面手动卸载模型/停止引擎")
                    elif result=="stopped": results.append("自建网关推理引擎已停止")
                    else: results.append("自建网关推理引擎原本未加载")
                except Exception as exc:
                    results.append("本地推理引擎停止失败："+str(exc)[:220])
            self.active_local_models.clear()
            self.model_session_active=False
            self.active_model_id=None
            self.active_model_base=None
            self.active_model_key=""
            status="本地模型任务已结束；"+"；".join(dict.fromkeys(results))+"；API 服务保持运行"
        elif self.remote_model_used: status="在线模型无需停止本地推理引擎"
        else: status=""
        self.remote_model_used=False
        if pid:
            try:
                project=self.read(pid,"project.json")
                project.pop("model_stop_status",None)
                project["model_runtime_status"]=status
                self.write(pid,"project.json",project)
            except Exception:
                pass
        return status
    def subtitles(self,pid,text):
        rows=parse_srt(text)
        if not rows: raise ValueError("未找到有效 SRT 字幕")
        self.write(pid,"segments.json",rows)
        self.write(pid,"suggestions.json",[])
        (self.folder(pid)/"transcript_layout.json").unlink(missing_ok=True)
        self.transcript_text(pid,rows)
        project=self.read(pid,"project.json")
        for key in ("reviewed_at","review_mode","auto_selected_count"):
            project.pop(key,None)
        self.write(pid,"project.json",project)
        self.status(pid,"ready","已导入字幕")
        self.progress(pid,"transcribe","done",100,"已导入字幕")
        for action in ("analyze","export","summary","notes","document","timestamp_document","punctuate"):
            self.progress(pid,action,"pending",0,"字幕已更新，等待重新处理")
        return len(rows)
    def ensure_transcript_layout(self,pid):
        """Apply punctuation and semantic paragraphs once before downstream work."""
        segments=self.read(pid,"segments.json",[])
        if not segments or self.read(pid,"transcript_layout.json"):
            return bool(segments)
        if not self.model_candidates("editing"):
            self.progress(pid,"punctuate","pending",0,"未配置文本模型；当前按语音停顿分段，可在设置中配置后补标点")
            return False
        try:
            self.task_punctuate(pid)
        except services.OperationCancelled:
            raise
        except Exception as exc:
            self.progress(pid,"punctuate","error",None,"自动整理失败，可手动重试："+str(exc)[:90])
            return False
        self.progress(pid,"punctuate","done",100,"已自动补充标点并按语意分段")
        return True
    def completion_label(self,pid,action):
        if action=="analyze":
            return f"已完成，生成 {len(self.read(pid,'suggestions.json',[]))} 条建议"
        if action=="summary":
            summary=self.read(pid,"summary.json",{})
            if summary.get("mode")=="transcript_only":
                reason=str(summary.get("fallback_reason") or "").strip()
                if reason:
                    return "已生成字幕版 HTML（模型未返回可用章节内容）"
                return "未配置总结模型，仅生成字幕版 HTML"
            chapters=summary.get("chapters",[])
            return f"已生成图文总结（{len(chapters)} 个主题章节）"
        return "已完成"
    def launch(self,pid,action,options=None):
        if action not in ACTIONS: raise ValueError("未知处理步骤")
        options=options if isinstance(options,dict) else {}
        summary_mode=str(options.get("summary_mode") or "detailed") if action=="summary" else "detailed"
        if action=="summary" and summary_mode not in SUMMARY_MODES:
            raise ValueError("总结模式无效")
        if not self.busy.acquire(blocking=False): raise RuntimeError("另一任务正在运行，请稍后")
        try:
            self.status(pid,"running",action)
            self.progress(pid,action,"running",None,"正在启动")
            self.begin_operation(pid,action)
        except Exception:
            self.busy.release()
            raise
        def target():
            try:
                self.check_cancelled()
                if action not in ("transcribe","punctuate"):
                    self.ensure_transcript_layout(pid)
                if action=="export":
                    self.finish_model_session(pid)
                if action=="summary": self.task_summary(pid,summary_mode=summary_mode)
                else: getattr(self,"task_"+action)(pid)
                if action=="transcribe":
                    self.ensure_transcript_layout(pid)
                self.check_cancelled()
                if action in ("export","summary","notes","document","timestamp_document"):
                    self.cleanup_temporary_files(pid)
                label=self.completion_label(pid,action)
                self.progress(pid,action,"done",100,label)
                self.status(pid,"ready",action+"完成")
            except services.OperationCancelled:
                self.progress(pid,action,"stopped",None,"已停止")
                self.status(pid,"stopped","已由用户停止")
            except Exception as exc:
                self.progress(pid,action,"error",None,"失败："+str(exc)[:120])
                self.status(pid,"error",action+"失败",str(exc)[:1500])
            finally:
                try:
                    self.finish_model_session(pid)
                finally:
                    self.end_operation(pid)
                    self.busy.release()
        threading.Thread(target=target,daemon=True).start()
    def launch_batch(self,bid,mode):
        if bid not in self.batches: raise ValueError("批次不存在")
        if mode not in ("prepare","export","summary","document","timestamp_document","finish_all"):
            raise ValueError("批量任务类型无效")
        batch=self.batches[bid]
        all_projects=list(batch["projects"])
        if mode=="export":
            targets=[pid for pid in all_projects if self.read(pid,"project.json").get("reviewed_at")]
        elif mode in ("document","timestamp_document"):
            targets=[pid for pid in all_projects if self.read(pid,"segments.json",[])]
        else:
            targets=all_projects
        if not targets:
            message=("该批次没有可处理的项目；批量导出前请逐项保存审核结果" if mode=="export"
                     else "该批次没有字幕；请先转写视频或导入字幕")
            raise ValueError(message)
        if not self.busy.acquire(blocking=False): raise RuntimeError("另一任务正在运行，请稍后")
        try:
            self.cancel_event.clear()
            self.task_resume_event.set()
            self.active_pid=None
            self.active_operation="batch"
            self.active_batch_id=bid
            batch.update(status="running",mode=mode,step="开始批量处理 · 按顺序逐个视频制作",completed=0,failed=0,skipped=len(all_projects)-len(targets))
            self._save_json(self.batches_path,self.batches)
        except Exception:
            self.active_pid=None
            self.active_operation=None
            self.active_batch_id=None
            self.busy.release()
            raise
        def target():
            active_pid=None
            stopped=False
            try:
                for index,pid in enumerate(targets,1):
                    action=None
                    try:
                        self.check_cancelled()
                        active_pid=pid
                        self.active_pid=pid
                        project=self.read(pid,"project.json")
                        project["active_operation"]="batch"
                        project.pop("stop_requested",None)
                        self.write(pid,"project.json",project)
                        self.status(pid,"running",f"批次 {index}/{len(targets)}")
                        if mode == "prepare":
                            actions = (["transcribe"] if not self.read(pid,"segments.json",[]) else []) + ["analyze", "summary"]
                        elif mode == "finish_all":
                            has_segments = bool(self.read(pid,"segments.json",[]))
                            if has_segments: self.progress(pid,"transcribe","done",100,"已有字幕，跳过转写")
                            actions = (["transcribe"] if not has_segments else []) + ["analyze", "summary", "export"]
                        else:
                            actions = [mode]
                        for action in actions:
                            self.check_cancelled()
                            self.status(pid,"running",action)
                            self.active_operation=action
                            project=self.read(pid,"project.json")
                            project["active_operation"]=action
                            self.write(pid,"project.json",project)
                            self.progress(pid,action,"running",None,"正在启动")
                            batch["step"]=f"{index}/{len(targets)} · {self.read(pid,'project.json')['name']} · {action}"
                            self._save_json(self.batches_path,self.batches)
                            if action not in ("transcribe","punctuate") and (mode!="finish_all" or action=="analyze"):
                                self.ensure_transcript_layout(pid)
                            if action=="export":
                                if mode=="finish_all":
                                    rows=self.read(pid,"suggestions.json",[])
                                    selected_count=sum(bool(row.get("selected")) for row in rows)
                                    self.save_decisions(pid,[{"id":row["id"],"selected":bool(row.get("selected"))} for row in rows],
                                                        mode="automatic")
                                    project=self.read(pid,"project.json")
                                    project["auto_selected_count"]=selected_count
                                    self.write(pid,"project.json",project)
                                self.finish_model_session(pid)
                            if action=="summary" and mode=="finish_all":
                                self.task_summary(pid,summary_mode="detailed")
                            else:
                                getattr(self,"task_"+action)(pid)
                            if action=="transcribe":
                                self.ensure_transcript_layout(pid)
                            self.check_cancelled()
                            if action in ("export","summary","notes","document","timestamp_document"):
                                self.cleanup_temporary_files(pid)
                            label=self.completion_label(pid,action)
                            self.progress(pid,action,"done",100,label)
                        batch_label={"finish_all":"一键完成"}.get(mode,mode)
                        self.status(pid,"ready","批量"+batch_label+"完成")
                        batch["completed"]+=1
                    except services.OperationCancelled:
                        if action in ACTIONS:
                            self.progress(pid,action,"stopped",None,"已停止")
                        self.status(pid,"stopped","批量处理已停止")
                        stopped=True
                        batch.update(status="stopped",step=(
                            f"已停止 · 完成 {batch['completed']} · 失败 {batch['failed']} · "
                            f"剩余 {len(targets)-batch['completed']-batch['failed']}"
                        ))
                        self._save_json(self.batches_path,self.batches)
                        break
                    except Exception as exc:
                        if action in ACTIONS:
                            self.progress(pid,action,"error",None,"失败："+str(exc)[:120])
                        self.status(pid,"error","批量"+mode+"失败",str(exc)[:1500])
                        batch["failed"]+=1
                    try:
                        project=self.read(pid,"project.json")
                        project.pop("active_operation",None)
                        project.pop("stop_requested",None)
                        self.write(pid,"project.json",project)
                    except Exception:
                        pass
                    self._save_json(self.batches_path,self.batches)
                if not stopped:
                    batch.update(status="ready" if not batch["failed"] else "partial",step="批量处理完成")
            except Exception as exc:
                batch.update(status="error",step="批量处理失败："+str(exc)[:300])
            finally:
                batch.pop("model_stop_status",None)
                try:
                    batch["model_runtime_status"]=self.finish_model_session(active_pid or targets[-1])
                    if active_pid:
                        self.end_operation(active_pid)
                    else:
                        self.cancel_event.clear()
                        self.task_resume_event.set()
                        self.active_pid=None
                        self.active_operation=None
                    self.active_batch_id=None
                    self._save_json(self.batches_path,self.batches)
                finally:
                    self.busy.release()
        threading.Thread(target=target,daemon=True).start()
    def launch_all(self,pid):
        if not self.busy.acquire(blocking=False): raise RuntimeError("另一任务正在运行，请稍后")
        try: self.status(pid,"running","一键处理中")
        except Exception:
            self.busy.release()
            raise
        try: self.begin_operation(pid,"finish_all")
        except Exception:
            self.busy.release()
            raise
        def target():
            action=None
            try:
                self.check_cancelled()
                has_segments=bool(self.read(pid,"segments.json",[]))
                if has_segments: self.progress(pid,"transcribe","done",100,"已有字幕，跳过转写")
                # One-click must use the currently selected editing model. Existing
                # suggestions have no model/transcript fingerprint, so they may be stale.
                actions=(["transcribe"] if not has_segments else [])+["analyze","summary","export"]
                for action in actions:
                    self.check_cancelled()
                    # Transcription already performs one automatic layout attempt.
                    # Retry once before analysis, as a separate Analyze click would,
                    # but do not repeat the same failed punctuation work before every
                    # later output step.
                    if action=="analyze":
                        self.ensure_transcript_layout(pid)
                    if action=="export":
                        rows=self.read(pid,"suggestions.json",[])
                        selected_count=sum(bool(row.get("selected")) for row in rows)
                        self.save_decisions(pid,[{"id":row["id"],"selected":bool(row.get("selected"))} for row in rows],
                                            mode="automatic")
                        project=self.read(pid,"project.json")
                        project["auto_selected_count"]=selected_count
                        self.write(pid,"project.json",project)
                    self.status(pid,"running",action)
                    self.progress(pid,action,"running",None,"正在启动")
                    if action=="export":
                        self.finish_model_session(pid)
                    if action=="summary":
                        self.task_summary(pid,summary_mode="detailed")
                    else:
                        getattr(self,"task_"+action)(pid)
                    if action=="transcribe":
                        self.ensure_transcript_layout(pid)
                    self.check_cancelled()
                    label=self.completion_label(pid,action)
                    self.progress(pid,action,"done",100,label)
                self.status(pid,"ready","一键处理完成")
            except services.OperationCancelled:
                if action in ACTIONS: self.progress(pid,action,"stopped",None,"已停止")
                self.status(pid,"stopped","已由用户停止")
            except Exception as exc:
                if action in ACTIONS: self.progress(pid,action,"error",None,"失败："+str(exc)[:120])
                self.status(pid,"error","一键处理失败",str(exc)[:1500])
            finally:
                try: self.finish_model_session(pid)
                finally:
                    self.end_operation(pid)
                    self.busy.release()
        threading.Thread(target=target,daemon=True).start()
    def source(self,pid):
        p=self.read(pid,"project.json")
        return Path(p["source_path"]) if p.get("source_path") else self.folder(pid)/p["source"]
    def task_document(self,pid,update_progress=True):
        """Generate a neatly formatted transcript Markdown without timestamps."""
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先转写音频或导入字幕，再生成完整文档")
        layout=self.read(pid,"transcript_layout.json")
        paragraphs=transcript_paragraphs(segments,layout=layout)
        if not paragraphs: raise RuntimeError("没有可写入文档的字幕")
        project=self.read(pid,"project.json")
        source=Path(project.get("name","视频")).name
        title=clean_summary_text(Path(source).stem,160) or "视频内容"
        characters=sum(len(row["text"]) for row in paragraphs)
        format_note=("已按语意补充标点并划分自然段；识别文字未经纠错。" if layout else
                     "按字幕和长语音停顿分段；尚未补充标点。")
        markdown=[f"# {title}_smart","","## 完整转写","",
            f"> 来源：{source}　·　{len(paragraphs):,} 段　·　{characters:,} 字",
            "> "+format_note,"","---",""]
        for index,row in enumerate(paragraphs,1):
            markdown.extend([row["text"],""])
            if index % 10 == 0 and index < len(paragraphs): markdown.extend(["---",""])
        target=self.folder(pid)/self.output_names(pid)["full_markdown"]
        target.write_text("\n".join(markdown).rstrip()+"\n",encoding="utf-8")
        self.deliver_artifact(pid,"full_markdown")
        self.progress(pid,"document","running" if update_progress else "done",100,"无时间戳完整 Markdown 文档已生成")
    def task_timestamp_document(self,pid):
        """Generate a separate, paragraph-aligned timestamped transcript Markdown."""
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先转写音频或导入字幕，再生成时间戳文档")
        paragraphs=transcript_paragraphs(segments,layout=self.read(pid,"transcript_layout.json"))
        if not paragraphs: raise RuntimeError("没有可写入文档的字幕")
        project=self.read(pid,"project.json")
        source=Path(project.get("name","视频")).name
        title=clean_summary_text(Path(source).stem,160) or "视频内容"
        clock=lambda value:f"{int(value//3600):02d}:{int(value//60)%60:02d}:{int(value)%60:02d}"
        characters=sum(len(row["text"]) for row in paragraphs)
        markdown=[f"# {title}_smart · 时间戳文稿","",
            f"> 来源：{source}　·　{len(paragraphs):,} 段　·　{characters:,} 字",
            "> 时间范围按字幕对齐结果标注。","","---","","## 完整转写",""]
        for row in paragraphs:
            markdown.extend([f"### {clock(row['start'])} — {clock(row['end'])}","",row["text"],""])
        target=self.folder(pid)/self.output_names(pid)["timestamp_markdown"]
        target.write_text("\n".join(markdown).rstrip()+"\n",encoding="utf-8")
        self.deliver_artifact(pid,"timestamp_markdown")
        self.progress(pid,"timestamp_document","running",100,"时间戳 Markdown 文档已生成")
    def deliver_artifact(self,pid,key):
        """Copy completed artifacts into the configured video or document output tree."""
        names=self.output_names(pid)
        filename=names.get(key)
        if (key not in DOCUMENT_ARTIFACT_KEYS and key!="video") or not filename: return
        source=self.folder(pid)/filename
        if not source.is_file(): return
        manifest=self.read(pid,".delivered-artifacts.json",{})
        if not isinstance(manifest,dict): manifest={}
        if key=="video":
            configured=str(self.settings.get("video_output_dir") or "").strip()
            if not configured: return
            output_root=Path(configured).expanduser().resolve()
            group_name,group_output=self._batch_output_group(pid)
            destination_dir=output_root/group_name if group_output else output_root
            self._copy_delivered_file(pid,key,source,destination_dir,output_root,manifest)
        else:
            configured=str(self.settings.get("notes_output_dir") or "").strip()
            if not configured: return
            output_root=Path(configured).expanduser().resolve()
            group_name,group_output=self._batch_output_group(pid)
            generated=sum((self.folder(pid)/names[item]).is_file() for item in DOCUMENT_ARTIFACT_KEYS)
            if group_output:
                destination_dir=output_root/group_name
            elif generated>=2:
                project=self.read(pid,"project.json")
                destination_dir=output_root/safe_name(Path(project.get("name","视频")).stem)
            else:
                destination_dir=output_root
            for artifact_key in DOCUMENT_ARTIFACT_KEYS:
                artifact_source=self.folder(pid)/names[artifact_key]
                if artifact_source.is_file():
                    self._copy_delivered_file(pid,artifact_key,artifact_source,destination_dir,output_root,manifest)
        self.write(pid,".delivered-artifacts.json",manifest)
    def _batch_output_group(self,pid):
        project=self.read(pid,"project.json")
        batch=self.batches.get(project.get("batch_id")) if project.get("batch_id") else None
        if not isinstance(batch,dict): return "",False
        batch_name=str(batch.get("name") or "")
        output_name=str(batch.get("output_group_name") or batch_name)
        project_name=Path(project.get("name","视频")).stem
        source_folder_name=Path(batch.get("source_folder") or "").name
        group_output=batch.get("group_output")
        if group_output is None:
            group_output=(len(batch.get("projects",[]))>1 or batch_name.endswith("_批次") or
                          bool(source_folder_name and batch_name==source_folder_name))
            if batch_name.endswith("_批次"):
                output_name=batch_name[:-3]
            elif batch_name.startswith("上传批次_") and batch.get("projects"):
                try: output_name=Path(self.read(batch["projects"][0],"project.json").get("name","视频")).stem
                except (FileNotFoundError,ValueError,OSError): pass
        return safe_name(output_name or source_folder_name or "视频批次"),bool(group_output)
    @staticmethod
    def _path_within(path,root):
        try: return Path(path).resolve().is_relative_to(Path(root).resolve())
        except (OSError,ValueError): return False
    def _copy_delivered_file(self,pid,key,source,destination_dir,output_root,manifest):
        destination_dir=Path(destination_dir).resolve()
        destination_dir.mkdir(parents=True,exist_ok=True)
        previous=Path(manifest[key]).expanduser().resolve() if manifest.get(key) else None
        if previous and previous.parent==destination_dir and previous.is_file():
            destination=previous
        else:
            destination=destination_dir/Path(source).name
            if destination.exists():
                suffix=f"_{pid[:8]}"
                candidate=destination.with_name(destination.stem+suffix+destination.suffix)
                index=2
                while candidate.exists() and candidate!=previous:
                    candidate=destination.with_name(destination.stem+suffix+f"_{index}"+destination.suffix)
                    index+=1
                destination=candidate
        destination=destination.resolve()
        if Path(source).resolve()!=destination:
            shutil.copy2(source,destination)
        if previous and previous!=destination and previous.is_file() and self._path_within(previous,output_root):
            previous.unlink(missing_ok=True)
        manifest[key]=str(destination)
    def cleanup_temporary_files(self,pid):
        """Remove reproducible processing intermediates while keeping source and review data."""
        folder=self.folder(pid)
        for name in TEMPORARY_FILE_NAMES:
            try: (folder/name).unlink(missing_ok=True)
            except OSError: pass
        for pattern in (".frame_preview_*.jpg","frame_topic_*.jpg","ocr_topic_*.json"):
            for path in folder.glob(pattern):
                if path.is_file():
                    try: path.unlink(missing_ok=True)
                    except OSError: pass
    def output_names(self,pid):
        project=self.read(pid,"project.json")
        stem=safe_name(Path(project.get("name",self.source(pid).name)).stem)+"_smart"
        container=self.settings.get("video_container","source")
        if container=="source": container=self.source(pid).suffix.lower().lstrip(".")
        if container not in ("mp4","mkv","mov","webm"): container="mp4"
        return {"video":stem+"."+container,"subtitle":stem+".srt","html":stem+".html",
                "full_html":stem+"_全文.html",
                "markdown":stem+".md","full_markdown":stem+"_完整文稿.md",
                "timestamp_markdown":stem+"_时间戳文稿.md","transcript":"transcript.txt"}
    def available_artifacts(self,pid):
        names=self.output_names(pid); folder=self.folder(pid)
        legacy={"video":"edited.mp4","subtitle":"edited.srt","html":"summary.html","full_html":"transcript_full.html","markdown":"summary.md",
                "full_markdown":"transcript_full.md","timestamp_markdown":"transcript_timestamps.md","transcript":"transcript.txt"}
        project=self.read(pid,"project.json")
        stem=safe_name(Path(project.get("name",self.source(pid).name)).stem)+"_smart"
        result={}
        for key,name in names.items():
            if (folder/name).is_file(): result[key]=name; continue
            if key=="video":
                previous=next((stem+"."+ext for ext in ("mp4","mkv","mov","webm") if (folder/(stem+"."+ext)).is_file()),None)
                result[key]=previous or (legacy[key] if (folder/legacy[key]).is_file() else name)
            else: result[key]=legacy[key] if (folder/legacy[key]).is_file() else name
        return result
    def reveal_project(self,pid):
        return services.reveal_path(self.folder(pid))
    def media(self,pid):
        value=self.read(pid,"media.json")
        if value:
            if "video_bitrate" not in value:
                refreshed=services.probe(self.settings["ffprobe"],self.source(pid),self.settings["ffmpeg"])
                value.update(refreshed); self.write(pid,"media.json",value)
            return value
        value=services.probe(self.settings["ffprobe"],self.source(pid),self.settings["ffmpeg"])
        if value["duration"]>3*3600+5: raise ValueError("视频超过 3 小时限制")
        self.write(pid,"media.json",value)
        return value
    def task_transcribe(self,pid):
        self.check_cancelled()
        media=self.media(pid)
        if not media["has_audio"]: raise RuntimeError("视频没有音轨，请导入字幕")
        folder=self.folder(pid); audio=folder/"audio.wav"
        self.status(pid,"running","提取音频")
        self.progress(pid,"transcribe","running",None,"正在提取音频")
        if not audio.exists(): services.extract_audio(self.settings["ffmpeg"],self.source(pid),audio,self.task_control)
        self.check_cancelled()
        self.status(pid,"running","运行外部 ASR")
        self.progress(pid,"transcribe","running",None,"语音识别中")
        if getattr(sys,"frozen",False):
            adapter=Path(getattr(sys,"_MEIPASS",Path(sys.executable).parent))/"asr_adapter"/"asr_qwen3.py"
        else:
            adapter=Path(__file__).with_name("asr_qwen3.py")
        result=services.run_asr(self.settings["asr_command"],audio,folder/"asr.json",self.task_control,
            {"asr_adapter":adapter,
             "asr_model":self.settings.get("qwen3_asr_model","").strip() or "Qwen/Qwen3-ASR-1.7B",
             "asr_aligner":self.settings.get("qwen3_aligner_model","").strip() or "Qwen/Qwen3-ForcedAligner-0.6B"})
        self.check_cancelled()
        segments=normalize_segments(result,media["duration"])
        if not segments: raise RuntimeError("ASR 没有返回有效片段")
        self.write(pid,"segments.json",segments)
        self.write(pid,"suggestions.json",[])
        (folder/"transcript_layout.json").unlink(missing_ok=True)
        self.transcript_text(pid,segments)
        project=self.read(pid,"project.json")
        for key in ("reviewed_at","review_mode","auto_selected_count"):
            project.pop(key,None)
        self.write(pid,"project.json",project)
        self.progress(pid,"punctuate","pending",0,"转写已更新，等待整理标点")
        for action in ("analyze","export","summary","notes","document","timestamp_document"):
            self.progress(pid,action,"pending",0,"转写已更新，等待重新处理")
    def task_punctuate(self,pid):
        self.check_cancelled()
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先完成转写或导入 SRT")
        if not self.model_candidates("editing"): raise RuntimeError("请先在设置中选择剪辑建议模型")
        text="".join(str(row.get("text","")) for row in segments)
        if not text.strip(): raise RuntimeError("字幕内容为空")
        chunks=[text[index:index+3000] for index in range(0,len(text),3000)]
        formatted=[]
        for index,chunk in enumerate(chunks):
            self.check_cancelled()
            self.status(pid,"running",f"整理字幕标点与段落 {index+1}/{len(chunks)}")
            self.progress(pid,"punctuate","running",round(index/len(chunks)*95),
                          f"正在整理第 {index+1}/{len(chunks)} 段字幕",index,len(chunks))
            prompt=("请给语音识别稿补充自然、准确的中文标点，并按完整语意划分自然段。"
                    "必须逐字保留原文中的汉字、英文、数字和顺序，只能插入标点符号与换行；"
                    "不得删除、改写、纠错、补充或概括任何文字。只输出整理后的原文，不要标题、解释或代码框。\n\n原文：\n"+chunk)
            result=self.model_chat_role("editing",
                [{"role":"user","content":prompt}],min(8000,max(2400,len(chunk)*2+400)),
                chat_template_kwargs={"enable_thinking":False},retry_unavailable=False)
            result=re.sub(r"^\s*```(?:text|markdown)?\s*|\s*```\s*$","",result.strip(),flags=re.I)
            if "".join(char for char in chunk if transcript_content_char(char)) != "".join(
                    char for char in result if transcript_content_char(char)):
                raise RuntimeError(f"第 {index+1} 段结果改动了字幕文字，未应用；请重试")
            formatted.append(result.strip())
            self.progress(pid,"punctuate","running",round((index+1)/len(chunks)*95),
                          f"已整理 {index+1}/{len(chunks)} 段字幕",index+1,len(chunks))
        layout=transcript_layout_from_text(segments,"\n\n".join(formatted))
        if not layout["markers"]:
            raise RuntimeError("模型没有补充标点或段落，原字幕保持不变；可重试或更换文本模型")
        self.write(pid,"transcript_layout.json",layout)
        self.transcript_text(pid,segments,layout)
        keeps=self.read(pid,"keep_intervals.json")
        target=self.folder(pid)/self.output_names(pid)["subtitle"]
        if keeps and target.is_file():
            remapped=remap_segments(segments,keeps)
            target.write_text(write_srt(subtitle_cues(remapped,15,10,layout)),encoding="utf-8")
        self.progress(pid,"punctuate","running",100,"标点与段落已整理")
    def task_analyze(self,pid):
        self.check_cancelled()
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先转写或导入 SRT")
        media=self.media(pid)
        if not self.model_candidates("editing"): raise RuntimeError("请先设置剪辑建议模型")
        suggestions=[]; total_chars=sum(len(str(row.get("text",""))) for row in segments)
        total_units=max(1.,math.ceil(len(segments)/80)+total_chars/5000); completed_units=0.
        # 分块，避免长视频一次输入；模型仅能引用真实 ID。
        for start in range(0,len(segments),80):
            self.check_cancelled()
            chunk=segments[start:start+80]
            chunk_units=1+sum(len(str(row.get("text",""))) for row in chunk)/5000
            self.status(pid,"running",f"分析片段 {start+1}—{start+len(chunk)} / {len(segments)}")
            self.progress(pid,"analyze","running",round(start/len(segments)*100),
                          f"分析 {start+1}—{start+len(chunk)} / {len(segments)} 段",
                          units_done=completed_units,units_total=total_units)
            lines="\n".join(f"{s['id']} [{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in chunk)
            prompt=("从原样转写中找无意义语气词、相邻重复、明确放弃的重新起句。"
                    "强调、数字、否定、专有名词和意义不确定处不要建议删除。"
                    "category 必须是 filler（语气词）、repeat（重复）、restart（重新起句）或 other。"
                    "只返回 JSON: {\"suggestions\":[{\"start_id\":\"s000001\",\"end_id\":\"s000002\","
                    "\"category\":\"repeat\",\"reason\":\"简短理由\"}]}。ID 必须来自输入。"
                    "理由须简短且不要包含双引号；无建议时返回 {\"suggestions\":[]}。\n"+lines)
            rows=None; parse_error=None
            for attempt in range(2):
                request_prompt=prompt
                if attempt:
                    request_prompt+=("\n上一次输出无法解析为 JSON。请重新分析以上字幕，并严格返回完整、有效的 JSON 对象；"
                                     "不要使用 Markdown 代码块，reason 不超过 20 字且不要包含双引号。")
                result=self.model_chat_role("editing",[{"role":"user","content":request_prompt}],2000,
                                     chat_template_kwargs={"enable_thinking":False})
                try:
                    parsed=parse_json_object(result)
                except ValueError as exc:
                    parse_error=exc
                    continue
                rows=validate_suggestions(parsed,chunk,media["duration"])
                break
            if rows is None:
                raise RuntimeError("模型连续两次返回无效 JSON，剪辑建议未生成；请重试或更换剪辑建议模型") from parse_error
            for row in rows:
                row["id"]=f"d{len(suggestions):05d}"
                suggestions.append(row)
            completed_units+=chunk_units
            self.progress(pid,"analyze","running",round((start+len(chunk))/len(segments)*100),
                          f"已分析 {start+len(chunk)} / {len(segments)} 段",
                          units_done=completed_units,units_total=total_units)
        # Gaps over one second are valid cut candidates. Keep their exact time
        # range so the transcript can show the pause and export can remove it.
        pause_index=0
        for previous,following in zip(segments,segments[1:]):
            gap=round(following["start"]-previous["end"],3)
            if gap>1.0:
                suggestions.append({"id":f"p{pause_index:05d}","start":previous["end"],"end":following["start"],
                    "reason":f"说话间停顿 {gap:.2f} 秒，可考虑删除","category":"pause","selected":True})
                pause_index+=1
        suggestions.sort(key=lambda row:(row["start"],row["end"],row["id"]))
        self.write(pid,"suggestions.json",suggestions)
        project=self.read(pid,"project.json")
        project.pop("reviewed_at",None)
        project.pop("review_mode",None)
        project.pop("auto_selected_count",None)
        self.write(pid,"project.json",project)
        self.progress(pid,"export","pending",0,"建议已更新，等待审核导出")
    def add_manual_suggestion(self,pid,start_id=None,end_id=None,start=None,end=None):
        segments=self.read(pid,"segments.json",[])
        if start is None or end is None:
            by_id={str(item.get("id")):item for item in segments}
            first,last=by_id.get(str(start_id)),by_id.get(str(end_id))
            if not first or not last or (first["start"],first["end"])>(last["start"],last["end"]):
                raise ValueError("所选文本无法对应到有效的转写时间")
            start,end=first["start"],last["end"]
        start,end=float(start),float(end)
        duration=self.media(pid)["duration"]
        if not 0<=start<end<=duration or not any(row["start"]<end and row["end"]>start for row in segments):
            raise ValueError("所选文本无法对应到有效的转写时间")
        row={"id":"m"+uuid.uuid4().hex[:12],"start":round(start,3),"end":round(end,3),
             "reason":"手动选中的文字","category":"manual","selected":True}
        suggestions=self.read(pid,"suggestions.json",[])
        suggestions.append(row)
        suggestions.sort(key=lambda item:(item["start"],item["end"],item["id"]))
        self.write(pid,"suggestions.json",suggestions)
        project=self.read(pid,"project.json")
        project["reviewed_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
        project["review_mode"]="manual"
        project.pop("auto_selected_count",None)
        project["updated"]=project["reviewed_at"]
        self.write(pid,"project.json",project)
        self.progress(pid,"export","pending",0,"手动删除标记已保存，等待导出")
        return row
    def restore_manual_selection(self,pid,start,end):
        start,end=float(start),float(end)
        duration=self.media(pid)["duration"]
        if not 0<=start<end<=duration: raise ValueError("所选文本无法对应到有效的转写时间")
        rows=self.read(pid,"suggestions.json",[]); updated=[]; restored=False
        for original in rows:
            row=dict(original)
            if (not row.get("selected") or row.get("category")=="pause" or
                    row["start"]>=end or row["end"]<=start):
                updated.append(row); continue
            left_end=min(float(row["end"]),start)
            right_start=max(float(row["start"]),end)
            has_left=left_end-float(row["start"])>.001
            has_right=float(row["end"])-right_start>.001
            group_id=row.get("group_id") or row["id"]
            restored=True
            if not has_left and not has_right:
                row["selected"]=False
                updated.append(row)
                continue
            row["group_id"]=group_id
            if has_left:
                row["end"]=round(left_end,3)
                updated.append(row)
            if has_right:
                if has_left:
                    remainder=dict(row)
                    remainder["id"]="m"+uuid.uuid4().hex[:12]
                    remainder["start"]=round(right_start,3)
                    remainder["end"]=round(float(original["end"]),3)
                    updated.append(remainder)
                else:
                    row["start"]=round(right_start,3)
                    updated.append(row)
        if not restored: raise ValueError("选中的文字没有删除标记")
        updated.sort(key=lambda item:(float(item["start"]),float(item["end"]),item["id"]))
        self.write(pid,"suggestions.json",updated)
        project=self.read(pid,"project.json")
        project["reviewed_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
        project["review_mode"]="manual"
        project.pop("auto_selected_count",None)
        project["updated"]=project["reviewed_at"]
        self.write(pid,"project.json",project)
        self.progress(pid,"export","pending",0,"已恢复选中文字，等待导出")
        return {"ok":True}
    def add_pause_suggestion(self,pid,start,end):
        start,end=float(start),float(end)
        segments=self.read(pid,"segments.json",[])
        for previous,following in zip(segments,segments[1:]):
            gap=float(following["start"])-float(previous["end"])
            if (gap>1 and abs(float(previous["end"])-start)<.01 and
                    abs(float(following["start"])-end)<.01):
                suggestions=self.read(pid,"suggestions.json",[])
                existing=next((row for row in suggestions if row.get("category")=="pause" and
                               abs(float(row["start"])-start)<.01 and abs(float(row["end"])-end)<.01),None)
                if existing:
                    existing["selected"]=True
                    row=existing
                else:
                    row={"id":"p"+uuid.uuid4().hex[:12],"start":round(start,3),"end":round(end,3),
                         "reason":"说话停顿","category":"pause","selected":True}
                    suggestions.append(row)
                suggestions.sort(key=lambda item:(item["start"],item["end"],item["id"]))
                self.write(pid,"suggestions.json",suggestions)
                project=self.read(pid,"project.json")
                project["reviewed_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
                project["review_mode"]="manual"
                project.pop("auto_selected_count",None)
                project["updated"]=project["reviewed_at"]
                self.write(pid,"project.json",project)
                self.progress(pid,"export","pending",0,"停顿删除标记已保存，等待导出")
                return row
        raise ValueError("此处没有超过 1 秒的可删除停顿")
    def save_decisions(self,pid,rows,mode="manual"):
        current={r["id"]:r for r in self.read(pid,"suggestions.json",[])}
        duration=self.media(pid)["duration"]
        for row in rows:
            old=current.get(str(row.get("id")))
            if not old: continue
            a,b=float(row.get("start",old["start"])),float(row.get("end",old["end"]))
            if 0<=a<b<=duration: old.update(start=round(a,3),end=round(b,3),selected=bool(row.get("selected")))
        self.write(pid,"suggestions.json",list(current.values()))
        project=self.read(pid,"project.json")
        project["reviewed_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
        project["review_mode"]=mode
        if mode=="manual": project.pop("auto_selected_count",None)
        project["updated"]=project["reviewed_at"]
        self.write(pid,"project.json",project)
        self.progress(pid,"export","pending",0,"审核已保存，等待导出")
    def task_export(self,pid):
        self.check_cancelled()
        media=self.media(pid); folder=self.folder(pid)
        rows=self.read(pid,"suggestions.json",[])
        deletes=[(r["start"],r["end"]) for r in rows if r.get("selected")]
        keeps=keep_ranges(deletes,media["duration"])
        if not keeps: raise RuntimeError("全部内容被删除")
        names=self.output_names(pid)
        target=folder/names["video"]
        encoder=self.settings.get("video_encoder","h264_nvenc")
        encoder_labels={"h264_nvenc":"NVIDIA GPU · H.264 NVENC p5 平衡",
            "hevc_nvenc":"NVIDIA GPU · H.265 NVENC p5 平衡",
            "x264_medium":"CPU · H.264 x264 medium","x264_fast":"CPU · H.264 x264 fast",
            "x264_slow":"CPU · H.264 x264 slow","x264_ultrafast":"CPU · H.264 x264 ultrafast",
            "x265_medium":"CPU · H.265 x265 medium","x265_fast":"CPU · H.265 x265 fast",
            "x265_slow":"CPU · H.265 x265 slow"}
        effective_encoder="CPU · VP9（WEBM 格式限制）" if target.suffix.lower()==".webm" else encoder_labels.get(encoder,"CPU · H.264 x264 medium")
        project=self.read(pid,"project.json"); project["export_encoder"]=effective_encoder; self.write(pid,"project.json",project)
        self.status(pid,"running","FFmpeg 导出成片 · "+effective_encoder)
        self.progress(pid,"export","running",0,"FFmpeg 编码中 · "+effective_encoder)
        resolution=self.settings.get("video_resolution","source")
        resolution_sizes={"2160p":(3840,2160),"1440p":(2560,1440),"1080p":(1920,1080),"720p":(1280,720),"480p":(854,480)}
        if resolution in resolution_sizes:
            resolution="%dx%d"%resolution_sizes[resolution]
        elif resolution=="custom":
            resolution="%sx%s"%(self.settings.get("video_resolution_width","1920"),self.settings.get("video_resolution_height","1080"))
        segments=self.read(pid,"segments.json",[])
        remapped=remap_segments(segments,keeps)
        layout=self.read(pid,"transcript_layout.json")
        subtitle_path=folder/names["subtitle"]
        subtitle_path.write_text(write_srt(subtitle_cues(remapped,15,10,layout)),encoding="utf-8")
        services.export_video(self.settings["ffmpeg"],self.source(pid),target,keeps,media["has_audio"],
                              progress=lambda value:self.progress(pid,"export","running",value,"FFmpeg 编码中 · "+effective_encoder),
                              fps=self.settings.get("video_fps","source"),
                              bitrate_mbps=(self.settings.get("video_bitrate_custom_mbps","5") if self.settings.get("video_bitrate_mbps")=="custom" else self.settings.get("video_bitrate_mbps","source")),
                              source_bitrate=media.get("video_bitrate"),
                              source_duration=media.get("duration"),
                              encoder=encoder,
                              resolution=resolution,cancel_event=self.task_control,
                              subtitle_path=subtitle_path)
        self.check_cancelled()
        self.deliver_artifact(pid,"subtitle")
        self.write(pid,"keep_intervals.json",keeps)
        self.deliver_artifact(pid,"video")
    def task_summary(self,pid,summary_mode="detailed"):
        self.check_cancelled()
        if summary_mode not in SUMMARY_MODES: raise ValueError("总结模式无效")
        project=self.read(pid,"project.json")
        analysis_state=project.get("progress",{}).get("analyze",{}).get("state")
        suggestions_path=self.folder(pid)/"suggestions.json"
        if (self.model_candidates("editing") and project.get("review_mode")!="manual" and
                (not suggestions_path.is_file() or analysis_state!="done")):
            try:
                self.progress(pid,"analyze","running",0,"生成全文删除标记")
                self.task_analyze(pid)
                self.progress(pid,"analyze","done",100,"全文删除标记已生成")
            except services.OperationCancelled:
                raise
            except Exception as exc:
                self.progress(pid,"analyze","error",None,"删除标记生成失败："+str(exc)[:100])
        self._generate_topic_outputs(pid,include_html=True,progress_action="summary",summary_mode=summary_mode)
    def task_notes(self,pid):
        self.check_cancelled()
        self._generate_topic_outputs(pid,include_html=False,progress_action="notes")
    def _generate_topic_outputs(self,pid,include_html,progress_action,summary_mode="detailed"):
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先转写或导入 SRT")
        media=self.media(pid)
        try:
            self._task_summary_with_model(pid,include_html=include_html,progress_action=progress_action,
                                          summary_mode=summary_mode)
        except services.OperationCancelled:
            raise
        except Exception as exc:
            detail=str(exc)
            if isinstance(exc,ValueError) or "无效 JSON" in detail or "无效格式" in detail:
                reason="模型返回内容格式异常"
            else:
                reason="图文总结未能生成可用章节内容"
            self.render_transcript_article(pid,segments,media,fallback_reason=reason,
                                           include_html=include_html,progress_action=progress_action,
                                           summary_mode=summary_mode)
        self.deliver_artifact(pid,"markdown")
        if include_html:
            self.task_document(pid,update_progress=False)
            self.render_full_transcript_html(pid,segments,media)
            for key in ("html","full_html","markdown","full_markdown"):
                self.deliver_artifact(pid,key)
    def _task_summary_with_model(self,pid,include_html=True,progress_action="summary",summary_mode="detailed"):
        self.check_cancelled()
        segments=self.read(pid,"segments.json",[])
        if not segments: raise RuntimeError("请先转写或导入 SRT")
        media=self.media(pid); cfg=self.settings
        if not self.model_candidates("summary"):
            return self.render_transcript_article(pid,segments,media,include_html=include_html,
                                                  progress_action=progress_action,summary_mode=summary_mode)
        vision_models=self.model_candidates("vision")
        scene_times=[]
        if cfg["scene_command"]:
            self.check_cancelled()
            self.status(pid,"running","外部场景检测")
            target=self.folder(pid)/"scenes.json"
            data=services.external_json(cfg["scene_command"],{"video":str(self.source(pid)),"output":str(target)},target,14400,self.task_control)
            raw=data.get("times",[]) if isinstance(data,dict) else []
            scene_times=sorted(float(x) for x in raw if isinstance(x,(int,float)) and 0<=x<media["duration"])
        paragraphs=transcript_paragraphs(segments,layout=self.read(pid,"transcript_layout.json"))
        if not paragraphs: raise RuntimeError("没有可总结的字幕")
        batches=paragraph_batches(paragraphs); topic_chapters=[]

        def summarize_batch(batch,split_depth=0):
            lines="\n".join(f"[{row['id']}] {row['start']:.2f}-{row['end']:.2f} 秒：{row['text']}" for row in batch)
            prompt=("你是严谨的中文内容编辑。请依据字幕段落本身，把连续段落按实际主题划分章节；"
                "不要按固定分钟数切章，同一主题应合并，主题变化时才另起一章。每条段落必须且只能属于一个章节，"
                "按原顺序完整覆盖。标题直接写具体主题，不要写视频强调、视频开场确认、视频讲解了、视频分析了等套话，"
                "摘要直接说明内容和结论，不以‘视频’或‘本视频’作主语，不写开场白。不要补造信息。"
                "每章返回 start_id、end_id、title、summary、bullets、visual_id、visual_description。"
                "visual_id 从本章段落 ID 中选一个确实适合配图的段落；visual_description 是可供画面检索的具体中文描述，"
                "优先选择字幕提到的图表、数字、物品、人物动作、屏幕演示或场景；无法从内容推断具体画面时，visual_id 设为 null、描述留空。"
                "只返回 JSON：{\"chapters\":[{\"start_id\":\"p000001\",\"end_id\":\"p000003\","
                "\"title\":\"主题\",\"summary\":\"摘要\",\"bullets\":[\"要点\"],"
                "\"visual_id\":\"p000002\",\"visual_description\":\"画面检索描述\"}]}\n"
                +SUMMARY_MODES[summary_mode]+"\n字幕段落：\n"+lines)
            last_error=None
            for attempt in range(2):
                request_prompt=prompt
                if attempt:
                    self.status(pid,"running",f"章节 JSON 格式异常，正在重试（{len(batch)} 段字幕）")
                    request_prompt+=("\n上次响应无法解析或范围不完整。请重新返回一个完整、有效的 JSON 对象，不要使用 Markdown；"
                        "字符串中的双引号必须转义，数组和对象末尾不要留逗号。请从头到尾完整覆盖以下段落，"
                        "每章最多 3 条要点，每条要点不超过 40 字，摘要不超过 180 字，画面描述不超过 80 字。"
                        f"段落范围必须从 {batch[0]['id']} 到 {batch[-1]['id']}。")
                result=self.model_chat_role("summary",[{"role":"user","content":request_prompt}],5000,
                                     chat_template_kwargs={"enable_thinking":False})
                try:
                    data=parse_json_object(result)
                    return chapters_from_ranges(data.get("chapters"),batch)
                except ValueError as exc:
                    last_error=exc
            if len(batch)>1 and split_depth<2:
                midpoint=len(batch)//2
                self.status(pid,"running",f"模型 JSON 连续无效，自动拆分字幕批次（{len(batch)} 段）")
                return summarize_batch(batch[:midpoint],split_depth+1)+summarize_batch(batch[midpoint:],split_depth+1)
            raise RuntimeError("章节总结模型多次返回无效格式") from last_error

        for batch_index,batch in enumerate(batches):
            self.check_cancelled()
            self.status(pid,"running",f"识别字幕主题 {batch_index+1}/{len(batches)}")
            grouped=summarize_batch(batch)
            topic_chapters.extend(grouped)
            self.progress(pid,progress_action,"running",round((batch_index+1)/len(batches)*30),
                          f"已整理 {batch_index+1}/{len(batches)} 组字幕主题")
        chapters=topic_chapters
        if len(topic_chapters)>1:
            self.status(pid,"running","合并相邻的同主题段落")
            outline="\n".join(f"{i+1}. {chapter['start_id']}—{chapter['end_id']}｜{chapter['title']}｜{chapter['summary'][:220]}｜画面线索 {chapter['visual_description']}"
                               for i,chapter in enumerate(topic_chapters))
            prompt=("请整理以下按时间顺序排列的主题章节。只把相邻且实际属于同一主题的章节合并；"
                "不同主题保持分开，不按时间长度合并。所有来源段落必须连续、完整、无重叠地覆盖。"
                "为合并后的章节给出具体标题、精炼摘要、最多 5 条要点，并从对应主题的来源段落中挑一个最适合配图的 visual_id 和具体 visual_description。"
                "避免模板句，不要出现‘视频强调’‘视频开场确认’‘视频讲解了’‘视频分析了’等表述。"
                "返回 JSON：{\"chapters\":[{\"start_id\":\"p000001\",\"end_id\":\"p000006\","
                "\"title\":\"主题\",\"summary\":\"摘要\",\"bullets\":[\"要点\"],"
                "\"visual_id\":\"p000003\",\"visual_description\":\"画面检索描述\"}]}\n"
                +SUMMARY_MODES[summary_mode]+"\n章节清单：\n"+outline)
            try:
                merged=parse_json_object(self.model_chat_role("summary",
                    [{"role":"user","content":prompt}],6000,chat_template_kwargs={"enable_thinking":False}))
                chapters=chapters_from_ranges(merged.get("chapters"),paragraphs)
            except services.OperationCancelled:
                raise
            except Exception:
                chapters=topic_chapters
        # If the merging pass omits a usable image cue, keep the best cue selected
        # during the paragraph-level topic analysis.
        for chapter in chapters:
            allowed={row["id"] for row in chapter["paragraphs"]}
            if not chapter["visual_id"] or not chapter["visual_description"]:
                alternatives=[row for row in topic_chapters if row["start_id"] in allowed or row["end_id"] in allowed]
                cue=next((row for row in alternatives if row["visual_id"] in allowed and row["visual_description"]),None)
                if cue:
                    chapter["visual_id"]=cue["visual_id"]
                    chapter["visual_description"]=cue["visual_description"]
        paragraph_by_id={row["id"]:row for row in paragraphs}
        completed=[]
        for index,chapter in enumerate(chapters):
            self.check_cancelled()
            self.status(pid,"running",f"整理主题：{chapter['title']}")
            pictures=[]; visual=""; ocr=""
            anchor=paragraph_by_id.get(chapter.get("visual_id"))
            visual_description=chapter.get("visual_description","")
            is_title_page="标题页" in visual_description
            if visual_description and is_title_page and float(chapter["end"])-float(chapter["start"])>.1:
                # A title card usually appears as a chapter begins. Sample it
                # directly so the vision model cannot discard the requested cover.
                title_time=min(float(chapter["end"])-.05,float(chapter["start"])+.25)
                times=[round(max(0.,title_time),3)]
            else:
                times=topic_frame_times(chapter,anchor,scene_times,media["duration"]) if visual_description else []
            if times:
                candidate_paths=[]
                needs_vision=bool(vision_models) and not is_title_page
                for candidate_index,second in enumerate(times):
                    picture=f"frame_topic_{index:03d}_{candidate_index}.jpg"
                    path=self.folder(pid)/picture
                    preview_path=None
                    if needs_vision:
                        preview_path=self.folder(pid)/f".frame_preview_{index:03d}_{candidate_index}.jpg"
                        services.frame(cfg["ffmpeg"],self.source(pid),second,preview_path,self.task_control)
                    elif candidate_index==0:
                        services.frame(cfg["ffmpeg"],self.source(pid),second,path,self.task_control,max_width=None)
                    candidate_paths.append((picture,path,second,preview_path))
                selected_index=0
                if is_title_page:
                    visual=visual_description
                elif vision_models:
                    vision_prompt=(f"章节主题：{chapter['title']}。目标画面描述：{chapter['visual_description']}。"
                        "后续图片按顺序编号 1 到 N。选择最能直接体现目标描述的一张；如果所有画面都无关，relevant=false。"
                        "只描述图中确实可见的内容，不从字幕猜测画面。返回 JSON："
                        "{\"best_image\":1,\"relevant\":true,\"caption\":\"简短画面说明\"}。")
                    try:
                        answer=self.model_chat_role("vision",
                            [services.image_message([row[3] for row in candidate_paths],vision_prompt)],700,
                            chat_template_kwargs={"enable_thinking":False})
                        choice=parse_json_object(answer)
                        raw_index=choice.get("best_image",1)
                        selected_index=int(raw_index)-1
                        if not 0<=selected_index<len(candidate_paths): selected_index=0
                        relevant=choice.get("relevant",True)
                        if isinstance(relevant,str): relevant=relevant.strip().lower() in ("true","yes","1","是")
                        if not relevant:
                            # Keep a representative chapter frame even when the
                            # vision model rejects the topic-specific candidates.
                            # The caption still describes what the fallback frame shows.
                            selected_index=0
                        visual=clean_summary_text(choice.get("caption",""),500)
                    except services.OperationCancelled:
                        raise
                    except Exception:
                        selected_index=0
                        visual=chapter["visual_description"]
                else:
                    visual=visual_description
                if selected_index>=0:
                    picture,path,second,preview_path=candidate_paths[selected_index]
                    if preview_path:
                        services.frame(cfg["ffmpeg"],self.source(pid),second,path,self.task_control,max_width=None)
                    pictures=[picture]
                    if cfg["ocr_command"]:
                        self.check_cancelled()
                        ocr_path=self.folder(pid)/f"ocr_topic_{index:03d}.json"
                        data=services.external_json(cfg["ocr_command"],{"image":str(path),"output":str(ocr_path)},ocr_path,120,self.task_control)
                        if isinstance(data,dict) and data.get("text"): ocr=str(data["text"])[:1200]
                for candidate_index,(_,path,_,preview_path) in enumerate(candidate_paths):
                    if candidate_index!=selected_index: path.unlink(missing_ok=True)
                    if preview_path: preview_path.unlink(missing_ok=True)
            stored={"start":chapter["start"],"end":chapter["end"],"title":clean_summary_text(chapter["title"],120),
                "summary":clean_summary_text(chapter["summary"]),"bullets":[clean_summary_text(x,500) for x in chapter["bullets"][:8]],
                "picture":pictures[0] if pictures else None,"pictures":pictures,"visual":clean_summary_text(visual,500),"ocr":ocr}
            completed.append(stored)
            self.write(pid,"summary.json",{"chapters":completed,"complete":False})
            self.progress(pid,progress_action,"running",40+round((index+1)/len(chapters)*59),
                          f"已生成 {index+1}/{len(chapters)} 个主题章节")
        if not completed: raise RuntimeError("没有可总结的字幕主题")
        self.write(pid,"summary.json",{"chapters":completed,"complete":True,"summary_mode":summary_mode})
        self.render_summary(pid,completed,include_html=include_html,summary_mode=summary_mode)
    def document_theme(self):
        """Load editable Apple-dark document design tokens with safe defaults."""
        defaults={"colors":{"background":"#0b0b0d","surface":"#151517","raised":"#1c1c1e",
            "text":"#f5f5f7","muted":"#a1a1a6","accent":"#2997ff","border":"#333336",
            "note":"#171c24"},"font_stack":"-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif",
            "content_width":"900px","page_padding":"48px"}
        try:
            stored=json.loads(self.document_style_path.read_text(encoding="utf-8"))
            if not isinstance(stored,dict): stored={}
        except (OSError,ValueError): stored={}
        colors=stored.get("colors",{}) if isinstance(stored.get("colors",{}),dict) else {}
        for key,fallback in defaults["colors"].items():
            value=str(colors.get(key,fallback))
            defaults["colors"][key]=value if re.fullmatch(r"#[0-9a-fA-F]{6}",value) else fallback
        for key,pattern in (("content_width",r"\d{3,4}px"),("page_padding",r"\d{2,3}px")):
            value=str(stored.get(key,defaults[key]))
            if re.fullmatch(pattern,value): defaults[key]=value
        font_stack=str(stored.get("font_stack",defaults["font_stack"]))
        if re.fullmatch(r"[A-Za-z0-9 ,_'\"-]+",font_stack): defaults["font_stack"]=font_stack
        return defaults
    def document_css(self):
        theme=self.document_theme(); colors=theme["colors"]
        return ("*{box-sizing:border-box}html{color-scheme:dark;scroll-behavior:smooth}"
            f"body{{margin:0;background:{colors['background']};color:{colors['text']};font:16px/1.8 {theme['font_stack']}}}"
            f".page{{max-width:{theme['content_width']};margin:0 auto;padding:{theme['page_padding']} 24px 80px}}"
            f".hero{{padding:42px 46px;border:1px solid {colors['border']};border-radius:28px;"
            f"background:linear-gradient(145deg,{colors['raised']},{colors['surface']});box-shadow:0 24px 80px #0006}}"
            f".eyebrow{{color:{colors['accent']};font-size:12px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}"
            f"h1{{font-size:clamp(17px,2.1vw,20px);line-height:1.4;letter-spacing:-.02em;margin:14px 0 18px}}"
            f"h2{{font-size:25px;line-height:1.35;letter-spacing:-.025em;margin:0 0 18px}}"
            f"h3{{font-size:18px;line-height:1.45;margin:0 0 10px}}"
            f".meta,.caption,.muted,footer{{color:{colors['muted']}}}.meta{{font-size:14px}}"
            f".note{{margin:20px 0;padding:17px 20px;border:1px solid {colors['border']};"
            f"border-radius:16px;background:{colors['note']};color:{colors['muted']}}}"
            f"nav{{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}}"
            f"nav a{{display:inline-block;padding:7px 12px;border:1px solid {colors['border']};border-radius:999px;"
            f"color:{colors['accent']};text-decoration:none;font-size:13px;line-height:1.4}}"
            f".card,.article-body{{margin:16px 0;padding:26px 30px;border:1px solid {colors['border']};"
            f"border-radius:22px;background:{colors['surface']};box-shadow:0 12px 40px #0003}}"
            f".time{{display:inline-block;margin-bottom:10px;color:{colors['accent']};font-size:12px;"
            f"font-weight:700;letter-spacing:.04em}}.summary{{font-size:17px}}"
            f".card ul{{padding-left:1.25em;color:{colors['text']}}}.card li{{padding:3px 0}}"
            f"img{{display:block;width:100%;height:auto;margin:18px 0;border:1px solid {colors['border']};"
            f"border-radius:16px}}.caption{{font-size:13px}}"
            f".transcript{{margin:0;padding:17px 0;border-bottom:1px solid {colors['border']}}}"
            ".transcript:last-child{border-bottom:0}.transcript .time{display:block;margin:0 0 5px}"
            ".transcript .text{display:block;white-space:pre-wrap;overflow-wrap:anywhere}"
            ".transcript mark{padding:0 .1em;border-radius:4px;color:inherit;-webkit-box-decoration-break:clone;box-decoration-break:clone}"
            ".deleted-filler{background:#674c2e;color:#ffe7bc}.deleted-repeat{background:#633943;color:#ffd7df}"
            ".deleted-restart{background:#51436d;color:#e8dcff}.deleted-manual{background:#31516a;color:#d8f0ff}"
            ".deleted-other{background:#5c542d;color:#fff0a8}.deletion-legend{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 22px}"
            ".deletion-legend span{padding:3px 9px;border-radius:999px;font-size:12px}"
            f"footer{{padding:24px 4px;font-size:12px}}::selection{{background:{colors['accent']}55}}"
            "@media(max-width:640px){.page{padding:18px 12px 42px}.hero,.card,.article-body{padding:23px 19px;border-radius:18px}h1{font-size:17px}}")
    def render_full_transcript_html(self,pid,segments,media):
        layout=self.read(pid,"transcript_layout.json")
        suggestions=self.read(pid,"suggestions.json",[])
        deletions=[row for row in suggestions if row.get("selected") and row.get("category")!="pause"]
        paragraphs=transcript_paragraphs(segments,layout=layout,highlight_intervals=deletions)
        if not paragraphs: raise RuntimeError("没有可写入 HTML 文档的字幕")
        project=self.read(pid,"project.json"); source=Path(project.get("name","视频")).name
        title=clean_summary_text(Path(source).stem,160) or "视频内容"
        clock=lambda value:f"{int(value//3600):02d}:{int(value//60)%60:02d}:{int(value)%60:02d}"
        content="".join(f'<p class="transcript" id="p{i}"><span class="time">{clock(row["start"])} — {clock(row["end"])}</span><span class="text">{row.get("html",html.escape(row["text"]))}</span></p>'
                         for i,row in enumerate(paragraphs,1))
        characters=sum(len(row["text"]) for row in paragraphs)
        format_note="已按语意补充标点并划分自然段；识别文字未经纠错。" if layout else "按字幕和长语音停顿分段；尚未补充标点。"
        legend_classes={item for row in paragraphs for item in re.findall(r'class="(deleted-[a-z]+)"',row.get("html",""))}
        legend_labels={"deleted-filler":"语气词","deleted-repeat":"重复语句","deleted-restart":"重新起句",
                       "deleted-manual":"手动标记","deleted-other":"模型建议"}
        legend=("<div class=\"deletion-legend\" aria-label=\"删除标记说明\">"+"".join(
            f'<span class="{item}">{legend_labels[item]}</span>' for item in sorted(legend_classes))+"</div>") if legend_classes else ""
        if deletions:
            deletion_note="已按类型给已选删除建议标注底色；建议来自剪辑建议模型，请结合语境复核。"
        elif project.get("progress",{}).get("analyze",{}).get("state")=="error":
            deletion_note="剪辑建议模型未能完成删除分析，正文未添加删除标记。"
        elif project.get("progress",{}).get("analyze",{}).get("state")=="done":
            deletion_note="剪辑建议模型没有给出需删除文字，或相关建议已取消选择。"
        else:
            deletion_note="配置剪辑建议模型并生成剪辑建议后，已选中的删除文字会按类型显示底色。"
        doc=(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · 完整文稿</title>'
            f'<style>{self.document_css()}</style></head><body><main class="page">'
            f'<header class="hero"><div class="eyebrow">Video Workbench · Full Transcript</div>'
            f'<h1>{html.escape(title)}<br>完整文稿</h1><div class="meta">来源：{html.escape(source)}　·　'
            f'{clock(float(media.get("duration",paragraphs[-1]["end"])))}</div></header>'
            f'<aside class="note">全文按字幕顺序逐段保留，并标出对应时间范围。{html.escape(format_note)}{deletion_note}</aside>'
            f'<section class="article-body"><h2>完整转写</h2><p class="muted">{len(paragraphs):,} 段 · {characters:,} 字</p>{legend}{content}</section>'
            '<footer>由 Video Workbench 根据原始字幕生成</footer></main></body></html>')
        (self.folder(pid)/self.output_names(pid)["full_html"]).write_text(doc,encoding="utf-8")
    def render_transcript_article(self,pid,segments,media,fallback_reason="",include_html=True,progress_action="summary",summary_mode="detailed"):
        """Create a polished transcript article when no text model is configured."""
        layout=self.read(pid,"transcript_layout.json")
        paragraphs=transcript_paragraphs(segments,layout=layout)
        if not paragraphs: raise RuntimeError("没有可排版的字幕")
        project=self.read(pid,"project.json")
        source_path=Path(project.get("name","视频"))
        title=clean_summary_text(source_path.stem,160) or "视频内容"
        source_name=source_path.name
        duration=float(media.get("duration",paragraphs[-1]["end"]))
        clock=lambda value:f"{int(value//3600):02d}:{int(value//60)%60:02d}:{int(value)%60:02d}"
        character_count=sum(len(row["text"]) for row in paragraphs)
        folder=self.folder(pid)
        names=self.output_names(pid)
        format_note=("正文已按语意补充标点并划分自然段；识别文字未经纠错。" if layout else
                     "正文按字幕和长语音停顿分段；未补充标点。")
        markdown=[f"# {title}_smart", "",
            f"> **语音转写稿**　·　{len(paragraphs):,} 段　·　{character_count:,} 字",
            f"> 来源：`{source_name}`", "> "+format_note, "",
            "---", "", "## 阅读说明", "",
            "以下内容来自语音识别结果，按自然段分行。本文没有生成主题归纳、摘要或配图说明；识别文字未经纠错。", "",
            "## 转写正文", ""]
        html_paragraphs=[]
        for index,row in enumerate(paragraphs,1):
            start,end=clock(row["start"]),clock(row["end"])
            markdown.extend([row["text"], ""])
            html_paragraphs.append(
                f'<p class="transcript"><span class="time">{start} — {end}</span>'
                f'<span class="text">{html.escape(row["text"])}</span></p>')
            if index % 10 == 0 and index < len(paragraphs):
                markdown.extend(["---", ""])
        markdown_path=folder/names["markdown"]
        markdown_path.write_text("\n".join(markdown).rstrip()+"\n",encoding="utf-8")
        escaped_title=html.escape(title)
        escaped_source=html.escape(source_name)
        document=(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escaped_title} · 字幕排版</title><style>{self.document_css()}</style></head><body><main class="page">'
            f'<header class="hero"><div class="eyebrow">语音转写 · 无模型排版</div>'
            f'<h1>{escaped_title}</h1><div class="meta">来源：{escaped_source}　·　'
            f'{clock(duration)}　·　{len(paragraphs):,} 段　·　{character_count:,} 字</div></header>'
            f'<aside class="note">本文按语音识别字幕整理，并附有原视频时间范围。'
            f'{"模型未返回可用章节内容，因此未包含主题归纳、摘要或配图说明；" if fallback_reason else "未配置文本模型，因此未包含主题归纳、摘要或配图说明；"}'
            f'识别文字未经模型校对。</aside>'
            f'<section class="article-body"><h2>转写正文</h2>{"".join(html_paragraphs)}</section>'
            f'<footer>由 Video Workbench 根据字幕自动排版 · 原始语音识别内容</footer>'
            f'</main></body></html>')
        if include_html: (folder/names["html"]).write_text(document,encoding="utf-8")
        self.write(pid,"summary.json",{"chapters":[],"complete":True,"mode":"transcript_only",
            "paragraph_count":len(paragraphs),"character_count":character_count,
            "summary_mode":summary_mode,
            "fallback_reason":str(fallback_reason)[:500] if fallback_reason else "",
            "html_generated":bool(include_html)})
        if fallback_reason:
            label=("已生成字幕版 Markdown 与 HTML（模型未返回可用章节内容）" if include_html
                   else "已生成字幕版总结 Markdown（模型未返回可用章节内容）")
        else:
            label=("未配置文本模型，已生成字幕排版版 Markdown 与 HTML" if include_html
                   else "未配置文本模型，已生成字幕版总结 Markdown")
        self.progress(pid,progress_action,"running",100,label)
    def render_summary(self,pid,chapters,include_html=True,summary_mode="detailed"):
        esc=html.escape; project=self.read(pid,"project.json")
        include_images=self.settings.get("html_include_images","true")!="false"
        include_image_notes=self.settings.get("html_include_image_notes","true")!="false"
        source_title=clean_summary_text(Path(project.get("name","视频")).stem,160) or "视频内容"
        title=esc(source_title); source_label=esc(clean_summary_text(Path(project.get("name","视频")).name,180))
        nav="".join(f'<a href="#c{i}">{esc(clean_summary_text(c["title"],120))}</a>' for i,c in enumerate(chapters))
        cards=[]
        clock=lambda value:f"{int(value//3600):02d}:{int(value//60)%60:02d}:{int(value)%60:02d}"
        for i,c in enumerate(chapters):
            points="".join(f"<li>{esc(clean_summary_text(p,500))}</li>" for p in c["bullets"])
            images=[]
            if include_images:
                for picture in c.get("pictures") or ([c["picture"]] if c.get("picture") else []):
                    payload=base64.b64encode((self.folder(pid)/picture).read_bytes()).decode("ascii")
                    images.append('<img src="data:image/jpeg;base64,{}" alt="{}">'.format(payload,esc(clean_summary_text(c.get("visual") or c["title"],500))))
            image="".join(images)
            caption=esc(clean_summary_text(c.get("visual",""),500)) if include_image_notes else ""
            ocr=esc(clean_summary_text(c.get("ocr",""),1200)) if include_image_notes else ""
            notes=(f'<p class="caption">{caption}{" · " if caption and ocr else ""}{ocr}</p>'
                   if caption or ocr else "")
            cards.append(f'<article class="card" id="c{i}"><small class="time">{clock(c["start"])} — {clock(c["end"])}</small><h2>{esc(clean_summary_text(c["title"],120))}</h2><p class="summary">{esc(clean_summary_text(c["summary"]))}</p><ul>{points}</ul>{image}{notes}</article>')
        mode_label={"detailed":"详细","concise":"简约","medium":"中档"}.get(summary_mode,"详细")
        doc=(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · 图文总结</title>'
            f'<style>{self.document_css()}</style></head><body><main class="page">'
            f'<header class="hero"><h1>{title}</h1><div class="meta">图文总结 · {mode_label}模式 · 来源：{source_label}</div></header>'
            f'<nav aria-label="章节目录">{nav}</nav>{"".join(cards)}'
            '<footer>由 Video Workbench 根据字幕与可用画面生成</footer></main></body></html>')
        if include_html:
            (self.folder(pid)/self.output_names(pid)["html"]).write_text(doc,encoding="utf-8")
        markdown=[f"# {source_title}_smart", "", "> 图文总结笔记", "",
                  f"> 来源：{Path(project.get('name','视频')).name}", "", "## 内容摘要", ""]
        overall="；".join(clean_summary_text(chapter.get("summary",""),500) for chapter in chapters if chapter.get("summary"))
        if overall: markdown.extend([overall, ""])
        for index,chapter in enumerate(chapters,1):
            markdown.extend([f"## {index:02d} · {clean_summary_text(chapter.get('title','相关主题'),120)}", ""])
            summary=clean_summary_text(chapter.get("summary",""))
            if summary: markdown.extend([summary, ""])
            for point in chapter.get("bullets",[]):
                point=clean_summary_text(point,500)
                if point: markdown.append(f"- {point}")
            if chapter.get("bullets"): markdown.append("")
            if index < len(chapters): markdown.extend(["---",""])
        (self.folder(pid)/self.output_names(pid)["markdown"]).write_text("\n".join(markdown).rstrip()+"\n",encoding="utf-8")
