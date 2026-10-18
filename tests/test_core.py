import io, tempfile, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
from video_workbench.core import parse_srt, keep_ranges, remap_segments, write_srt, validate_suggestions, parse_json_object
from video_workbench.workbench import Workbench
from video_workbench import services

class CoreTests(unittest.TestCase):
    def test_chat_waits_for_gateway_model_switch(self):
        temporary=urllib.error.HTTPError("http://127.0.0.1:3000/v1/chat/completions",503,"busy",{},
                                         io.BytesIO(b'{"error":{"type":"not_ready"}}'))
        ready=io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        with patch("video_workbench.services.urllib.request.urlopen",side_effect=[temporary,ready]) as call, \
             patch("video_workbench.services.time.sleep") as sleep:
            self.assertEqual(services.chat("http://127.0.0.1:3000/v1","test","",[{"role":"user","content":"hi"}]),"ok")
        self.assertEqual(call.call_count,2)
        sleep.assert_called_once()
    def test_timeline_and_srt(self):
        rows=parse_srt("1\n00:00:00,000 --> 00:00:03,000\n你好\n\n2\n00:00:12,000 --> 00:00:16,000\n世界\n")
        keep=keep_ranges([(10,12)],20)
        self.assertEqual(keep,[(0.,10.),(12.,20.)])
        mapped=remap_segments(rows,keep)
        self.assertEqual(mapped[-1]["start"],10)
        self.assertIn("00:00:10,000 --> 00:00:14,000",write_srt(mapped))
    def test_suggestion_ids_only(self):
        segments=[{"id":"s1","start":1,"end":2,"text":"嗯"}]
        rows=validate_suggestions({"suggestions":[{"start_id":"s1","end_id":"s1","reason":"语气词"},
                    {"start_id":"fake","end_id":"fake"}]},segments,10)
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]["selected"])
    def test_json_fence(self):
        fence=chr(96)*3
        self.assertEqual(parse_json_object(fence+'json\n{"title":"测试"}\n'+fence)["title"],"测试")
    def test_workbench_subtitle_and_html_escape(self):
        with tempfile.TemporaryDirectory() as d:
            wb=Workbench(Path(d))
            pid=wb.create("test.mp4",io.BytesIO(b"video"),5)
            wb.write(pid,"media.json",{"duration":20,"has_audio":True})
            wb.subtitles(pid,"1\n00:00:00,000 --> 00:00:01,000\n测试\n")
            self.assertEqual(len(wb.detail(pid)["segments"]),1)
            wb.render_summary(pid,[{"start":0,"end":10,"title":"<script>","summary":"安全","bullets":[],"picture":None,"visual":""}])
            content=(wb.folder(pid)/"summary.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>",content)
            self.assertIn("&lt;script&gt;",content)
    def test_summary_and_export_workflow_with_external_services_stubbed(self):
        with tempfile.TemporaryDirectory() as d:
            wb=Workbench(Path(d))
            pid=wb.create("lesson.mp4",io.BytesIO(b"video"),5)
            wb.write(pid,"media.json",{"duration":20,"has_audio":True})
            wb.subtitles(pid,"1\n00:00:00,000 --> 00:00:04,000\n这个这个功能\n\n2\n00:00:10,000 --> 00:00:15,000\n讲解结束\n")
            wb.settings["text_model"]="test-text"
            with patch("video_workbench.workbench.services.chat",return_value='{"title":"第一章","summary":"内容","bullets":["要点"]}'):
                wb.task_summary(pid)
            self.assertIn("第一章",(wb.folder(pid)/"summary.html").read_text(encoding="utf-8"))
            wb.write(pid,"suggestions.json",[{"id":"d1","start":4,"end":10,"selected":True}])
            def fake_export(tool,source,target,keeps,has_audio,progress=None):
                self.assertEqual(keeps,[(0.,4.),(10.,20.)])
                if progress: progress(75)
                target.write_bytes(b"fake mp4")
            with patch("video_workbench.workbench.services.export_video",side_effect=fake_export):
                wb.task_export(pid)
            self.assertIn("00:00:04,000 --> 00:00:09,000",(wb.folder(pid)/"edited.srt").read_text(encoding="utf-8"))
if __name__=="__main__": unittest.main()
