"""Real yt-dlp + FFmpeg integration using locally generated, served media."""
import functools
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from downloader.dependencies import find_ffmpeg
from downloader.worker import execute


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(find_ffmpeg(), "FFmpeg is required for real media integration")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ffmpeg = find_ffmpeg()
        cls.ffprobe = str(Path(cls.ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        cls.flags = flags
        subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", str(cls.root / "A clean title!.mp4")], check=True, capture_output=True, creationflags=flags, timeout=30)
        subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(cls.root / "A clean title!.mp4"), "-c", "copy",
                        "-f", "dash", "-seg_duration", "1", "Separate streams.mpd"], cwd=cls.root, check=True, capture_output=True, creationflags=flags, timeout=30)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def download(self, filename, format, quality):
        output = self.root / (format + filename.split(".")[-1])
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output) as work:
            events = []
            execute({"url": f"http://127.0.0.1:{self.server.server_port}/{quote(filename)}", "format": format, "quality": quality,
                     "work_dir": work, "output_dir": str(output)}, events.append)
        completed = next(e for e in events if e["event"] == "completed")
        result = Path(completed["filename"])
        probe = subprocess.run([self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result)],
                               check=True, capture_output=True, text=True, creationflags=self.flags, timeout=15)
        self.assertTrue(any(e["event"] == "progress" for e in events))
        return result, json.loads(probe.stdout), events

    def test_real_mp4_download(self):
        result, probe, _ = self.download("A clean title!.mp4", "MP4", "Best available")
        self.assertEqual(result.suffix, ".mp4")
        self.assertIn("A clean title!", result.name)
        self.assertEqual({s["codec_type"] for s in probe["streams"]}, {"audio", "video"})

    def test_real_mp3_bitrate_conversion(self):
        result, probe, events = self.download("A clean title!.mp4", "MP3", "320 kbps")
        self.assertEqual(result.suffix, ".mp3")
        self.assertEqual(probe["streams"][0]["codec_name"], "mp3")
        self.assertEqual(int(probe["streams"][0]["bit_rate"]), 320000)
        self.assertTrue(any(e["event"] == "processing" for e in events))

    def test_real_separate_stream_mp4_merge(self):
        result, probe, _ = self.download("Separate streams.mpd", "MP4", "360p")
        self.assertEqual(result.suffix, ".mp4")
        self.assertEqual({s["codec_type"] for s in probe["streams"]}, {"audio", "video"})
        self.assertIn("mp4", probe["format"]["format_name"])


if __name__ == "__main__":
    unittest.main()
