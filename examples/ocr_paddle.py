"""PaddleOCR adapter; install PaddlePaddle/PaddleOCR in a separate environment."""

import argparse
import json
import os
from pathlib import Path


def extract_text(results):
    lines=[]
    for result in results:
        data=result if isinstance(result,dict) else getattr(result,"json",{})
        if callable(data): data=data()
        if isinstance(data,dict): data=data.get("res",data)
        for value in data.get("rec_texts",[]) if isinstance(data,dict) else []:
            text=str(value).strip()
            if text: lines.append(text)
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description="PaddleOCR image adapter")
    parser.add_argument("--image",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--version",default="PP-OCRv5")
    parser.add_argument("--lang",default="ch")
    parser.add_argument("--device",default="cpu")
    parser.add_argument("--cache-dir",type=Path,help="将单独下载的 OCR 模型缓存放在指定目录")
    args=parser.parse_args()

    if args.cache_dir:
        args.cache_dir.mkdir(parents=True,exist_ok=True)
        cache=str(args.cache_dir.resolve())
        os.environ["PADDLE_PDX_CACHE_HOME"]=cache
        # Paddle's legacy dataset import writes under expanduser('~') even
        # though OCR does not use datasets. Keep that import inside this
        # adapter's writable cache on restricted Windows installations.
        if os.name=="nt": os.environ["USERPROFILE"]=cache
    if args.device=="cpu":
        # Paddle 3.3 on Windows can fail to run PP-OCRv5 with its default
        # oneDNN backend; the plain Paddle backend is compatible.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT","False")

    from paddleocr import PaddleOCR
    engine=PaddleOCR(ocr_version=args.version,lang=args.lang,device=args.device,
                     use_doc_orientation_classify=False,use_doc_unwarping=False,
                     use_textline_orientation=False)
    text=extract_text(engine.predict(input=str(args.image)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"text":text},ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__": main()
