import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from asr_qwen3_server import make_handler
from asr_remote_client import transcribe, validate_url


class RemoteASRTests(unittest.TestCase):
    def test_streams_audio_and_requires_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            payload = b"RIFF" + b"x" * (2 * 1024 * 1024)
            audio.write_bytes(payload)
            sizes = []

            def recognize(path):
                sizes.append(path.stat().st_size)
                return [{"start": 0.2, "end": 0.5, "text": "测试"}]

            server = HTTPServer(("127.0.0.1", 0), make_handler("a" * 32, recognize, root))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/transcribe"
                result = transcribe(url, "a" * 32, audio)
                self.assertEqual(result["segments"][0]["text"], "测试")
                self.assertEqual(sizes, [len(payload)])
                self.assertFalse(list(root.glob("tmp*.wav")))
                with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                    transcribe(url, "b" * 32, audio)
            finally:
                server.shutdown()
                server.server_close()

    def test_rejects_public_or_wrong_endpoint(self):
        for url in ("http://example.invalid:8877/transcribe",
                    "https://example.invalid:8877/transcribe",
                    "http://example.invalid:8877/other"):
            with self.assertRaises(ValueError):
                validate_url(url)


if __name__ == "__main__":
    unittest.main()
