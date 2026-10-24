from __future__ import annotations
import html, json, re, unicodedata

def _loads_json_repairing_escapes(text):
    """Parse model JSON while preserving literal backslashes in returned text."""
    repaired=text
    repairs=0
    while True:
        try:
            return json.loads(repaired,strict=False)
        except json.JSONDecodeError as exc:
            if exc.msg not in ("Invalid \\escape", "Invalid \\uXXXX escape"):
                raise
            if exc.pos>=len(repaired) or repaired[exc.pos]!="\\" or repairs>=10000:
                raise
            repaired=repaired[:exc.pos]+"\\"+repaired[exc.pos:]
            repairs+=1

def parse_json_object(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError("模型未返回文本内容，无法解析 JSON")
    value=value.strip()
    if value.startswith(chr(96)*3):
        value=re.sub(r"^.{3}(?:json)?\s*|\s*.{3}$","",value,flags=re.I)
    def loads(text):
        return _loads_json_repairing_escapes(text)
    try: result=loads(value)
    except json.JSONDecodeError:
        a,b=value.find("{"),value.rfind("}")
        if a<0 or b<a: raise ValueError("模型未返回 JSON 对象")
        result=loads(value[a:b+1])
    if not isinstance(result,dict): raise ValueError("预期 JSON 对象")
    return result

def normalize_segments(raw,duration=None):
    if isinstance(raw,dict): raw=raw.get("segments",raw.get("words",[]))
    if not isinstance(raw,list): raise ValueError("转写结果须为 segments 数组")
    out=[]
    for i,row in enumerate(raw):
        if not isinstance(row,dict): continue
        try: a,b=float(row["start"]),float(row["end"])
        except (KeyError,TypeError,ValueError): raise ValueError(f"第 {i+1} 段缺少有效时间")
        value=str(row.get("text",row.get("word",""))).strip()
        if a<0 or b<=a or (duration and b>duration+1): raise ValueError(f"第 {i+1} 段时间越界")
        if value: out.append({"id":f"s{i:06d}","start":round(a,3),"end":round(b,3),"text":value})
    return sorted(out,key=lambda r:(r["start"],r["end"]))

def parse_srt(text):
    def seconds(value):
        pieces=value.replace(",",".").split(":")
        if len(pieces)==2: hours=0; minutes=int(pieces[0]); seconds=float(pieces[1])
        else: hours=int(pieces[0]); minutes=int(pieces[1]); seconds=float(pieces[2])
        return hours*3600+minutes*60+seconds
    rows=[]
    timestamp=r"(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}"
    pattern=rf"({timestamp})\s*-->\s*({timestamp})[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
    for m in re.finditer(pattern,text.replace("\r\n","\n"),re.S):
        rows.append({"start":seconds(m[1]),"end":seconds(m[2]),"text":html.unescape(re.sub(r"<[^>]+>","",m[3])).strip().replace("\n"," ")})
    return normalize_segments(rows)

def merge_ranges(ranges,duration):
    clipped=sorted((max(0.,float(a)),min(duration,float(b))) for a,b in ranges if float(b)>float(a))
    out=[]
    for a,b in clipped:
        if a>=b: continue
        if out and a<=out[-1][1]+.001: out[-1][1]=max(out[-1][1],b)
        else: out.append([a,b])
    return [(a,b) for a,b in out]

def keep_ranges(deletions,duration):
    cursor,keep=0.,[]
    for a,b in merge_ranges(deletions,duration):
        if a>cursor+.02: keep.append((cursor,a))
        cursor=max(cursor,b)
    if duration>cursor+.02: keep.append((cursor,duration))
    return keep

def remap_segments(segments,keeps):
    output,offset=[],0.
    for a,b in keeps:
        for s in segments:
            left,right=max(a,s["start"]),min(b,s["end"])
            if right>left:
                row={"start":round(offset+left-a,3),"end":round(offset+right-a,3),"text":s["text"]}
                if "id" in s: row["id"]=s["id"]
                output.append(row)
        offset+=b-a
    return output

def subtitle_cues(segments,max_chars=15,min_chars=10,layout=None):
    """Group timed ASR characters into readable, sentence-oriented subtitle cues."""
    hard_stops=set("。！？!?；;")
    soft_stops=set("，,、：:")
    closers=set("\"'”’）)]}】》」』〉〕］｝")
    atoms=[]
    markers={(row.get("segment_id"),int(row.get("offset",0))):row for row in (layout or {}).get("markers",[])}
    for row in segments:
        value=str(row.get("text", "")); segment_id=str(row.get("id", ""))
        if not value: continue
        start,end=float(row["start"]),float(row["end"])
        duration=max(0.,end-start)
        # ASR usually emits one character per row. For imported phrase-level SRT,
        # spread the phrase over its interval so long sentences can also be split.
        for index,char in enumerate(value):
            a=start+duration*index/len(value)
            b=start+duration*(index+1)/len(value)
            before=markers.get((segment_id,index),{})
            after=markers.get((segment_id,index+1),{})
            if before.get("before"):
                for mark in before["before"]: atoms.append({"start":a,"end":a,"text":mark})
            if not layout or not unicodedata.category(char).startswith("P"):
                atoms.append({"start":a,"end":b,"text":char})
            if after.get("after"):
                for mark in after["after"]: atoms.append({"start":b,"end":b,"text":mark})
            if after.get("paragraph"):
                atoms.append({"start":b,"end":b,"text":"\n","paragraph":True})
    cues=[]; current=[]; pending_stop=False
    def flush():
        if current:
            text="".join(atom["text"] for atom in current).strip()
            if text:
                cues.append({"start":round(current[0]["start"],3),"end":round(current[-1]["end"],3),"text":text})
            current.clear()
    for atom in atoms:
        if atom.get("paragraph"):
            flush(); pending_stop=False
            continue
        char=atom["text"]
        if current and atom["start"]-current[-1]["end"]>1.0:
            flush(); pending_stop=False
        if pending_stop:
            if char in closers or char in hard_stops or char in soft_stops:
                current.append(atom); flush(); pending_stop=False
                continue
            flush(); pending_stop=False
        visible=sum(not item["text"].isspace() for item in current)
        if current and visible>=max_chars and not char.isspace():
            flush(); visible=0
        current.append(atom)
        visible=sum(not item["text"].isspace() for item in current)
        if char in hard_stops:
            pending_stop=True
        elif char in soft_stops and visible>=min_chars:
            pending_stop=True
        elif visible>=max_chars:
            pending_stop=True
    flush()
    return cues

def stamp(value):
    ms=max(0,round(value*1000)); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def write_srt(rows):
    return "\n\n".join(f"{i}\n{stamp(s['start'])} --> {stamp(s['end'])}\n{s['text']}" for i,s in enumerate(rows,1))+"\n"

def validate_suggestions(raw,segments,duration):
    if isinstance(raw,dict): raw=raw.get("suggestions",[])
    if not isinstance(raw,list): return []
    by_id={s["id"]:s for s in segments}; out=[]
    for i,row in enumerate(raw[:500]):
        if not isinstance(row,dict): continue
        first,last=by_id.get(str(row.get("start_id",""))),by_id.get(str(row.get("end_id","")))
        if not first or not last or first["start"]>=last["end"]: continue
        out.append({"id":f"d{i:04d}","start":first["start"],"end":min(duration,last["end"]),
            "reason":str(row.get("reason",""))[:240],"category":str(row.get("category","other"))[:40],"selected":True})
    return out
