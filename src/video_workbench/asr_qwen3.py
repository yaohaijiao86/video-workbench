"""Adapter for a separately installed Qwen3-ASR + ForcedAligner environment.

The official qwen-asr package handles long-audio chunking. This script only
converts its word/character timestamps into Video Workbench's JSON protocol.
No model weights or third-party packages are bundled with this repository.
"""

import argparse
import gc
import json
import os
from pathlib import Path


def convert_timestamps(items):
    rows=[]
    for item in items or []:
        start=float(item.start_time)
        end=float(item.end_time)
        text=str(item.text).strip()
        if text and 0<=start<end:
            rows.append({"start":round(start,3),"end":round(end,3),"text":text})
    return rows


def transcribe_audio(audio,model_name,aligner_name,device):
    import torch
    from qwen_asr import Qwen3ASRModel

    dtype=torch.bfloat16 if device.startswith("cuda") else torch.float32
    model=Qwen3ASRModel.from_pretrained(
        model_name,dtype=dtype,device_map=device,
        max_inference_batch_size=8,max_new_tokens=256,
        forced_aligner=aligner_name,
        forced_aligner_kwargs={"dtype":dtype,"device_map":device},
    )
    try:
        result=model.transcribe(audio=str(audio),language=None,return_time_stamps=True)[0]
        return convert_timestamps(result.time_stamps)
    finally:
        # Release VRAM before the workbench asks its text/vision model to run.
        del model
        gc.collect()
        if device.startswith("cuda"): torch.cuda.empty_cache()


def main():
    parser=argparse.ArgumentParser(description="Qwen3-ASR timestamp adapter")
    parser.add_argument("--input",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--model",default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--aligner",default="Qwen/Qwen3-ForcedAligner-0.6B")
    parser.add_argument("--device",default="cuda:0")
    parser.add_argument("--cache-dir",type=Path,help="将单独下载的模型缓存放在指定目录")
    args=parser.parse_args()

    if args.cache_dir:
        args.cache_dir.mkdir(parents=True,exist_ok=True)
        os.environ["HF_HOME"]=str(args.cache_dir.resolve())

    rows=transcribe_audio(args.input,args.model,args.aligner,args.device)
    if not rows: raise RuntimeError("Qwen3-ASR 未返回有效时间戳，请确认 ForcedAligner 已正确加载")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"segments":rows},ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__": main()
