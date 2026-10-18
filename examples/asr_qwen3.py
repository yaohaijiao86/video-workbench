"""Run the packaged Qwen3-ASR adapter from a source checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_workbench.asr_qwen3 import convert_timestamps, main, transcribe_audio


if __name__ == "__main__":
    main()
