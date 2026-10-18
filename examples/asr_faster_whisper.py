"""Optional adapter. Install faster-whisper and model weights separately."""
import argparse, json
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--model",default="large-v3")
    p.add_argument("--device",default="cuda")
    p.add_argument("--compute-type",default="float16")
    args=p.parse_args()
    from faster_whisper import WhisperModel
    model=WhisperModel(args.model,device=args.device,compute_type=args.compute_type)
    segments,_=model.transcribe(args.input,word_timestamps=True,vad_filter=False,beam_size=5)
    rows=[]
    for segment in segments:
        if segment.words:
            rows.extend({"start":w.start,"end":w.end,"text":w.word.strip()} for w in segment.words if w.word.strip())
        else:
            rows.append({"start":segment.start,"end":segment.end,"text":segment.text.strip()})
    Path(args.output).write_text(json.dumps({"segments":rows},ensure_ascii=False),encoding="utf-8")
if __name__=="__main__": main()
