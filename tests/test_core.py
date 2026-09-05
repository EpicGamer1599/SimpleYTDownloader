import errno
import json
import os
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from config.settings import SettingsManager
from downloader.utils import friendly_error, normalize_youtube_url, output_directory, publish_file, sanitize_filename, utf16_length
from downloader.worker import build_options, execute


class FilenameTests(unittest.TestCase):
    def test_readable_punctuation_and_invalid_characters(self):
        self.assertEqual(sanitize_filename("This is a cool title! [HD] (2026), - yes"), "This is a cool title! [HD] (2026), - yes")
        self.assertEqual(sanitize_filename('a<>:"/\\|?*b'), "a_________b")
        self.assertEqual(sanitize_filename("a\x00b\x1fc"), "a_b_c")

    def test_reserved_trailing_empty_and_unicode(self):
        for value in ("CON", "prn.mp4", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9", "COM¹", "CONIN$", "CONOUT$", "con .txt"):
            with self.subTest(value=value):
                self.assertTrue(sanitize_filename(value).startswith("_"))
        self.assertEqual(sanitize_filename("Title ...  "), "Title")
        self.assertEqual(sanitize_filename("..."), "Untitled video")
        self.assertEqual(sanitize_filename(None), "Untitled video")
        self.assertEqual(sanitize_filename("COM10"), "COM10")
        self.assertLessEqual(utf16_length(sanitize_filename("😀" * 300)), 180)
        self.assertEqual(sanitize_filename("café 日本語"), "café 日本語")

    def test_publish_preserves_existing_and_limits_path(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "media.mp4"
            source.write_bytes(b"first")
            first = publish_file(source, folder, "My video!", "mp4")
            source.write_bytes(b"second")
            second = publish_file(source, folder, "My video!", "mp4")
            self.assertEqual(first.name, "My video!.mp4")
            self.assertEqual(second.name, "My video! (2).mp4")
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")
            source.write_bytes(b"long")
            long = publish_file(source, folder, "😀" * 500, "mp3")
            self.assertLessEqual(utf16_length(str(long)), 240)


class ValidationTests(unittest.TestCase):
    def test_supported_url_variants(self):
        expected = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        for url in (expected + "&list=abc&t=2", "youtu.be/jNQXAC9IVRw", "https://m.youtube.com/watch?v=jNQXAC9IVRw",
                    "https://youtube.com/shorts/jNQXAC9IVRw", "https://www.youtube.com/live/jNQXAC9IVRw",
                    "https://youtube-nocookie.com/embed/jNQXAC9IVRw"):
            self.assertEqual(normalize_youtube_url(url), expected)

    def test_unsupported_and_invalid_urls(self):
        for url in ("", "not a link", "https://youtube.com.evil.test/watch?v=jNQXAC9IVRw", "file:///abc",
                    "https://youtube.com/playlist?list=hello", "https://youtu.be/short", "https://youtube.com@evil.test/watch?v=jNQXAC9IVRw", "https://youtube.com:999/watch?v=jNQXAC9IVRw"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_youtube_url(url)

    def test_output_validation_and_human_errors(self):
        with self.assertRaises(ValueError):
            output_directory("")
        with self.assertRaises(ValueError):
            output_directory("relative/path")
        self.assertIn("disk is full", friendly_error(OSError(errno.ENOSPC, "disk")))
        self.assertIn("Access denied", friendly_error(PermissionError("denied")))
        self.assertIn("private", friendly_error("ERROR: Private video"))
        self.assertNotIn("Traceback", friendly_error("ERROR: unable to download webpage: timed out"))


class SettingsTests(unittest.TestCase):
    def test_simultaneous_saves_use_independent_atomic_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first, second = SettingsManager(folder), SettingsManager(folder)
            first.settings.default_format, first.settings.accent_theme = "MP3", "Blue"
            second.settings.default_format, second.settings.accent_theme = "MP4", "Gold"
            barrier = threading.Barrier(2)
            original = Path.replace
            def replace(source, target):
                barrier.wait(timeout=3)
                return original(source, target)
            with patch.object(Path, "replace", replace), ThreadPoolExecutor(max_workers=2) as pool:
                saves = [pool.submit(manager.save) for manager in (first, second)]
                for result in saves:
                    result.result(timeout=5)
            loaded = SettingsManager(folder)
            self.assertFalse(loaded.warning)
            self.assertIn((loaded.settings.default_format, loaded.settings.accent_theme), (("MP3", "Blue"), ("MP4", "Gold")))
            self.assertEqual([p.name for p in folder.iterdir()], ["settings.json"])

    def test_failed_settings_save_preserves_old_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SettingsManager(Path(directory))
            manager.save()
            original = manager.path.read_bytes()
            manager.settings.accent_theme = "Blue"
            with patch("config.settings.os.fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
                manager.save()
            self.assertEqual(manager.path.read_bytes(), original)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["settings.json"])

    def test_oversized_deeply_nested_and_invalid_encoding_settings_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for data in (b" " * 65537, b"[" * 2000 + b"0" + b"]" * 2000, b"\xff\xfe"):
                path.write_bytes(data)
                manager = SettingsManager(Path(directory))
                self.assertTrue(manager.warning)
                self.assertEqual(manager.settings.default_format, "MP4")

    def test_roundtrip_and_forget(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SettingsManager(Path(directory))
            manager.settings.default_format = "MP3"
            manager.settings.output_dir = directory
            manager.save()
            reloaded = SettingsManager(Path(directory))
            self.assertEqual(reloaded.settings.default_format, "MP3")
            self.assertEqual(reloaded.settings.output_dir, directory)
            manager.settings.remember = False
            manager.save()
            self.assertFalse(manager.path.exists())
            self.assertEqual(SettingsManager(Path(directory)).settings.default_format, "MP4")

    def test_corrupt_and_wrong_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{oops")
            self.assertTrue(SettingsManager(Path(directory)).warning)
            path.write_text(json.dumps({"default_format": "bad", "auto_start": "true", "video_quality": "999p", "output_dir": ""}))
            settings = SettingsManager(Path(directory)).settings
            self.assertEqual(settings.default_format, "MP4")
            self.assertFalse(settings.auto_start)
            self.assertEqual(settings.video_quality, "Best available")
            self.assertTrue(settings.output_dir)


class FormatSelectionTests(unittest.TestCase):
    def select(self, quality, formats, ffmpeg=True):
        from yt_dlp import YoutubeDL
        options = build_options({"quality": quality, "format": "MP4", "work_dir": tempfile.gettempdir()}, "ffmpeg" if ffmpeg else None)
        options.pop("ffmpeg_location", None)
        options.pop("postprocessors", None)
        options["quiet"] = True
        options["no_warnings"] = True
        with YoutubeDL(options) as ydl:
            return ydl.process_ie_result({"id": "test", "title": "test", "formats": formats, "extractor": "test"}, download=False)

    @staticmethod
    def video(id, height, ext="mp4", audio="none"):
        return {"format_id": id, "url": "https://example.com/" + id, "ext": ext, "height": height, "width": height * 16 // 9, "vcodec": "avc1", "acodec": audio}

    def test_resolution_cap_merge_and_fallback(self):
        formats = [self.video("360", 360), self.video("720", 720), self.video("1080", 1080), self.video("2160", 2160),
                   {"format_id": "audio", "url": "https://example.com/audio", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128}]
        self.assertEqual(self.select("1080p", formats)["height"], 1080)
        self.assertEqual(self.select("1440p", formats)["height"], 1080)
        self.assertEqual(self.select("Best available", formats)["height"], 2160)
        self.assertEqual(self.select("360p", formats[1:])["height"], 720)

    def test_progressive_without_ffmpeg(self):
        formats = [self.video("360", 360, audio="mp4a"), self.video("720", 720, audio="mp4a"), self.video("1080", 1080)]
        self.assertEqual(self.select("1080p", formats, False)["height"], 720)


class DependencyFailureTests(unittest.TestCase):
    def test_missing_yt_dlp_explains_setup(self):
        with patch.dict("sys.modules", {"yt_dlp": None}):
            with self.assertRaisesRegex(RuntimeError, "yt-dlp is missing"):
                execute({}, lambda event: None)

    def test_missing_ffmpeg_explains_mp3_requirement(self):
        with patch("downloader.worker.find_ffmpeg", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "MP3 conversion requires FFmpeg"):
                execute({"format": "MP3"}, lambda event: None)


if __name__ == "__main__":
    unittest.main()
