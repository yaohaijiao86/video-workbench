"""PySceneDetect adapter; install scenedetect in a separate environment."""

import argparse
import json
from pathlib import Path


def scene_starts(scenes):
    return [round(float(start.get_seconds()),3) for start,_ in scenes][1:]


def main():
    parser=argparse.ArgumentParser(description="PySceneDetect timestamp adapter")
    parser.add_argument("--video",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--detector",choices=("adaptive","content"),default="adaptive")
    args=parser.parse_args()

    from scenedetect import AdaptiveDetector, ContentDetector, detect
    detector=AdaptiveDetector() if args.detector=="adaptive" else ContentDetector()
    scenes=detect(str(args.video),detector)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"times":scene_starts(scenes)},indent=2),encoding="utf-8")


if __name__=="__main__": main()
