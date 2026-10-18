import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from video_workbench.workbench import Workbench, select_frame_times
from video_workbench import services
from examples.asr_qwen3 import convert_timestamps
from examples.ocr_paddle import extract_text
from examples.scene_pyscenedetect import scene_starts


class WorkspaceTests(unittest.TestCase):
    def test_ffmpeg_can_probe_when_bundle_omits_ffprobe(self):
        stderr="Duration: 00:03:04.50, start: 0.000000, bitrate: 300 kb/s\nStream #0:0: Video: h264\nStream #0:1: Audio: aac"
        with patch.object(services,"executable",side_effect=[RuntimeError("missing"),"ffmpeg"]), patch.object(services.subprocess,"run",return_value=SimpleNamespace(stderr=stderr,stdout="")):
            self.assertEqual(services.probe("missing","video.mp4","ffmpeg"),{"duration":184.5,"has_audio":True})

    def test_qwen_timestamp_adapter_preserves_word_times(self):
        items=[SimpleNamespace(text="你好",start_time=0.1,end_time=0.45),
               SimpleNamespace(text=" ",start_time=0.46,end_time=0.5)]
        self.assertEqual(convert_timestamps(items),[{"start":0.1,"end":0.45,"text":"你好"}])

    def test_optional_adapter_output_formats(self):
        self.assertEqual(extract_text([{"rec_texts":[" 标题 ","", "正文"]}]),"标题\n正文")
        points=[(SimpleNamespace(get_seconds=lambda value=value: value),None) for value in (0,12.5,25)]
        self.assertEqual(scene_starts(points),[12.5,25.0])

    def test_visual_frames_are_spread_across_long_chapter(self):
        times=select_frame_times(0,600,[151,299,452])
        self.assertEqual(times,[151,299,452])
        self.assertEqual(select_frame_times(0,20,[]),[10.0])

    def test_long_chapter_embeds_three_spread_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            wb=Workbench(Path(directory))
            pid=wb.create("lesson.mp4",io.BytesIO(b"video"),5)
            wb.write(pid,"media.json",{"duration":600,"has_audio":False})
            wb.write(pid,"segments.json",[{"id":"s1","start":0,"end":2,"text":"课程开始"}])
            wb.save_settings({"text_model":"text","vision_model":"vision"})
            times=[]
            def frame(ffmpeg,source,second,target):
                times.append(second)
                target.write_bytes(b"jpeg")
            def chat(url,model,key,messages,max_tokens):
                return '{"title":"章节","summary":"内容","bullets":[]}' if model=="text" else "讲师在画面中"
            with patch("video_workbench.workbench.services.frame",side_effect=frame), patch("video_workbench.workbench.services.chat",side_effect=chat):
                wb.task_summary(pid)
            self.assertEqual(times,[150.0,300.0,450.0])
            self.assertEqual(len(wb.read(pid,"summary.json")["chapters"][0]["pictures"]),3)
            self.assertEqual((wb.folder(pid)/"summary.html").read_text(encoding="utf-8").count("data:image/jpeg;base64,"),3)

    def test_folder_import_keeps_original_and_restores_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/"讲座素材"
            source.mkdir()
            (source/"a.mp4").write_bytes(b"video-a")
            (source/"b.mkv").write_bytes(b"video-b")
            (source/"a.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n",encoding="utf-8")
            wb=Workbench(root/"data")
            wb.save_settings({"output_mode":"source"})
            result=wb.import_folder(source)
            self.assertEqual(result["count"],2)
            self.assertFalse(result["skipped"])
            self.assertEqual((source/"a.mp4").read_bytes(),b"video-a")
            projects=Workbench(root/"data").list_projects()
            self.assertEqual(len(projects),2)
            self.assertTrue(all(Path(p["output_path"]).parent==(source/"讲座素材_workbench").resolve() for p in projects))
            first=next(p for p in projects if p["name"]=="a.mp4")
            self.assertEqual(Workbench(root/"data").source(first["id"]),(source/"a.mp4").resolve())
            self.assertEqual(len(Workbench(root/"data").detail(first["id"])["segments"]),1)

    def test_custom_destination_and_browser_source_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            wb=Workbench(root/"data")
            output=root/"results"
            wb.save_settings({"output_mode":"custom","output_dir":str(output)})
            bid=wb.create_batch("课程")
            pid=wb.create("lesson.mp4",io.BytesIO(b"video"),5,batch_id=bid)
            self.assertEqual(wb.folder(pid).parent,(output/"课程").resolve())
            self.assertEqual(wb.source(pid).read_bytes(),b"video")
            wb.save_settings({"output_mode":"source"})
            with self.assertRaisesRegex(ValueError,"浏览器上传"):
                wb.create("browser.mp4",io.BytesIO(b"video"),5)

    def test_batch_runs_sequentially_and_requires_review_for_export(self):
        with tempfile.TemporaryDirectory() as directory:
            wb=Workbench(Path(directory))
            bid=wb.create_batch("batch")
            first=wb.create("one.mp4",io.BytesIO(b"one"),3,batch_id=bid)
            second=wb.create("two.mp4",io.BytesIO(b"two"),3,batch_id=bid)
            wb.write(first,"segments.json",[{"id":"s1","start":0,"end":1,"text":"hello"}])
            calls=[]
            def transcribe(pid):
                calls.append((pid,"transcribe"))
                wb.write(pid,"segments.json",[{"id":"s1","start":0,"end":1,"text":"hello"}])
            def analyze(pid): calls.append((pid,"analyze"))
            def summary(pid): calls.append((pid,"summary"))
            with patch.object(wb,"task_transcribe",side_effect=transcribe), patch.object(wb,"task_analyze",side_effect=analyze), patch.object(wb,"task_summary",side_effect=summary):
                wb.launch_batch(bid,"prepare")
                self._wait(wb)
            self.assertEqual(calls,[(first,"analyze"),(first,"summary"),(second,"transcribe"),(second,"analyze"),(second,"summary")])
            self.assertEqual(wb.batches[bid]["completed"],2)
            with self.assertRaisesRegex(ValueError,"没有可处理的项目"):
                wb.launch_batch(bid,"export")
            for pid in (first,second): wb.write(pid,"media.json",{"duration":2,"has_audio":False})
            wb.save_decisions(first,[])
            with patch.object(wb,"task_export") as exporter:
                wb.launch_batch(bid,"export")
                self._wait(wb)
                exporter.assert_called_once_with(first)
            self.assertEqual(wb.batches[bid]["skipped"],1)
            wb.save_decisions(second,[])
            with patch.object(wb,"task_export") as exporter:
                wb.launch_batch(bid,"export")
                self._wait(wb)
                self.assertEqual(exporter.call_count,2)
            self.assertEqual(wb.batches[bid]["status"],"ready")
            self.assertEqual(wb.batches[bid]["skipped"],0)

    def test_one_click_runs_default_modules_and_exports_all_analysis_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            wb=Workbench(Path(directory))
            pid=wb.create("lesson.mp4",io.BytesIO(b"video"),5)
            wb.write(pid,"media.json",{"duration":8,"has_audio":True})
            wb.write(pid,"segments.json",[
                {"id":"s1","start":0,"end":1,"text":"你好"},
                {"id":"s2","start":1.1,"end":2,"text":"你好"},
                {"id":"s3","start":2.1,"end":2.5,"text":"嗯"},
                {"id":"s4","start":3,"end":4,"text":"重要"},
            ])
            suggestions=[
                {"id":"d1","start":1.1,"end":2,"category":"repeat","selected":True},
                {"id":"d2","start":2.1,"end":2.5,"category":"filler","selected":True},
                {"id":"d3","start":3,"end":4,"category":"filler","selected":True},
            ]
            calls=[]
            def analyze(project):
                calls.append("analyze")
                wb.write(project,"suggestions.json",suggestions)
            def export(project):
                calls.append("export")
                selected={r["id"] for r in wb.read(project,"suggestions.json") if r["selected"]}
                self.assertEqual(selected,{"d1","d2","d3"})
            def summary(project): calls.append("summary")
            with patch.object(wb,"task_analyze",side_effect=analyze), patch.object(wb,"task_export",side_effect=export), patch.object(wb,"task_summary",side_effect=summary):
                wb.launch_all(pid)
                self._wait(wb)
            self.assertEqual(calls,["analyze","summary","export"])
            project=wb.read(pid,"project.json")
            self.assertEqual(project["review_mode"],"automatic")
            self.assertEqual(project["auto_selected_count"],3)
            self.assertEqual(project["progress"]["export"]["percent"],100)

    def test_restart_marks_stale_running_project_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            wb=Workbench(root)
            pid=wb.create("lesson.mp4",io.BytesIO(b"video"),5)
            wb.status(pid,"running","运行外部 ASR")
            wb.progress(pid,"transcribe","running",None,"识别中")
            reopened=Workbench(root)
            project=reopened.read(pid,"project.json")
            self.assertEqual(project["status"],"interrupted")
            self.assertEqual(project["progress"]["transcribe"]["state"],"interrupted")

    @staticmethod
    def _wait(wb):
        deadline=time.monotonic()+3
        while wb.busy.locked() and time.monotonic()<deadline:
            time.sleep(0.01)
        if wb.busy.locked(): raise AssertionError("batch worker did not finish")


if __name__=="__main__": unittest.main()
