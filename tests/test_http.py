import io, json, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from video_workbench.app import Handler
from video_workbench.workbench import Workbench

class HttpTests(unittest.TestCase):
    def test_folder_batch_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/"source"
            source.mkdir()
            (source/"one.mp4").write_bytes(b"video")
            Handler.wb=Workbench(root/"data")
            Handler.wb.save_settings({"output_mode":"source"})
            server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base="http://127.0.0.1:"+str(server.server_port)
            try:
                body=json.dumps({"path":str(source)}).encode()
                req=urllib.request.Request(base+"/api/batches/import",data=body,
                    headers={"Content-Type":"application/json"},method="POST")
                result=json.load(urllib.request.urlopen(req))
                self.assertEqual(result["count"],1)
                batches=json.load(urllib.request.urlopen(base+"/api/batches"))
                self.assertEqual(batches[0]["id"],result["batch_id"])
                projects=json.load(urllib.request.urlopen(base+"/api/projects"))
                self.assertIn("source_workbench",projects[0]["output_path"])
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)

    def test_upload_subtitle_and_range(self):
        with tempfile.TemporaryDirectory() as directory:
            Handler.wb=Workbench(Path(directory))
            server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base="http://127.0.0.1:"+str(server.server_port)
            try:
                upload=urllib.request.Request(base+"/api/projects",data=b"0123456789",
                    headers={"X-Filename":"sample.mp4"},method="POST")
                pid=json.load(urllib.request.urlopen(upload))["id"]
                subtitle=urllib.request.Request(base+"/api/projects/"+pid+"/subtitles",
                    data=b"1\n00:00:00,000 --> 00:00:01,000\nhello\n",method="POST")
                self.assertEqual(json.load(urllib.request.urlopen(subtitle))["count"],1)
                req=urllib.request.Request(base+"/api/projects/"+pid+"/source",headers={"Range":"bytes=2-5"})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status,206)
                    self.assertEqual(response.read(),b"2345")
                    self.assertEqual(response.headers["Content-Range"],"bytes 2-5/10")
                hostile=urllib.request.Request(base+"/api/settings",data=b"{}",headers={"Origin":"https://elsewhere.example","Content-Type":"application/json"},method="POST")
                with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(hostile)
                self.assertEqual(cm.exception.code,400)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)
if __name__=="__main__": unittest.main()
