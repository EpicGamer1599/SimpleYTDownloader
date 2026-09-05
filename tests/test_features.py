"""Version 1.0.1 thumbnails, preference migration, and issue-report safety."""
import io
import json
import os
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app_version import APP_VERSION
from config.settings import SettingsManager
from config.themes import THEMES
from downloader.manager import DownloadManager
from downloader.thumbnails import MAX_THUMBNAIL_BYTES, download_thumbnail, image_extension
from downloader.worker import execute
from error_reporting import ErrorReport, ISSUES_URL, error_code


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    pixels = b''.join(b'\0' + b'\x64\xa0\xf0' * 32 for _ in range(18))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 32, 18, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data) if length is None else length)}


class FeatureTests(unittest.TestCase):
    def test_old_preferences_migrate_and_invalid_theme_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'settings.json').write_text(json.dumps({'default_format': 'MP3', 'auto_check_updates': False}))
            manager = SettingsManager(path)
            self.assertEqual(manager.settings.accent_theme, 'Orange')
            self.assertFalse(manager.settings.save_thumbnails)
            self.assertFalse(manager.settings.auto_check_updates)
            manager.settings.accent_theme = 'Blue'
            manager.settings.save_thumbnails = True
            manager.save()
            reloaded = SettingsManager(path).settings
            self.assertEqual(reloaded.accent_theme, 'Blue')
            self.assertTrue(reloaded.save_thumbnails)
            (path / 'settings.json').write_text(json.dumps({'accent_theme': 'bogus', 'save_thumbnails': 'true'}))
            self.assertEqual(SettingsManager(path).settings.accent_theme, 'Orange')
            self.assertFalse(SettingsManager(path).settings.save_thumbnails)
            self.assertEqual(len(THEMES), 6)

    def test_retry_retains_thumbnail_choice_and_errors_are_reported_once(self):
        with tempfile.TemporaryDirectory() as folder:
            import sys
            manager = DownloadManager(worker_command=[sys.executable, '-u', '-m', 'tests.fake_worker'])
            try:
                item = manager.add('https://youtu.be/FAIL0000001', 'MP4', '720p', folder, True)
                manager.cancel(item.id)
                manager.retry(item.id)
                self.assertTrue(manager.snapshot()[0].save_thumbnails)
                manager.start()
                deadline = time.monotonic() + 5
                reports = []
                while time.monotonic() < deadline and not reports:
                    reports = manager.events()
                    time.sleep(0.02)
                self.assertEqual(len(reports), 1)
                self.assertEqual(reports[0].code, 'SYTD-VIDEO-PRIVATE')
                self.assertEqual(manager.events(), [])
            finally:
                manager.shutdown()

    def test_stable_error_codes(self):
        cases = {'Private video': 'SYTD-VIDEO-PRIVATE', 'FFmpeg failed': 'SYTD-FFMPEG',
                 'Could not reach YouTube. Check your connection.': 'SYTD-NETWORK',
                 'The disk is full': 'SYTD-DISK-FULL', 'Access denied': 'SYTD-ACCESS',
                 'GitHub release ZIP is corrupt': 'SYTD-UPDATE', 'Thumbnail failed': 'SYTD-THUMBNAIL'}
        for message, code in cases.items():
            self.assertEqual(error_code(message), code)

    def test_issue_draft_has_version_code_template_and_no_private_paths_or_links(self):
        report = ErrorReport.create('Access denied: C:\\Users\\SecretUser\\Private files\\video.mp4\n'
                                    'https://example.test/private?token=secret\nAuthorization: supersecret',
                                    'Download MP4')
        url = report.issue_url()
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme + '://' + parsed.netloc + parsed.path, ISSUES_URL + '/new')
        query = parse_qs(parsed.query)
        body = query['body'][0]
        self.assertIn(APP_VERSION, body)
        self.assertIn('SYTD-ACCESS', body)
        self.assertIn('### Steps to reproduce', body)
        for private in ('SecretUser', 'video.mp4', 'supersecret', 'example.test'):
            self.assertNotIn(private, body)
        self.assertLessEqual(len(url), 1900)

    def test_issue_unicode_is_bounded_and_cannot_inject_an_endpoint(self):
        report = ErrorReport.create('\u65e5' * 4000 + '\n```bad```', 'Application', 'not a code')
        self.assertLessEqual(len(report.issue_url()), 1900)
        self.assertEqual(urlsplit(report.issue_url()).netloc, 'github.com')
        self.assertEqual(report.code, 'SYTD-UNEXPECTED')
        self.assertNotIn('```bad', report.message)

    def test_thumbnail_fallback_corrupt_size_and_scheme_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self):
                    self.calls = []
                def urlopen(self, request):
                    self.calls.append(request.url)
                    return Response(b'not an image' if request.url.endswith('/bad') else png_bytes())
            client = Client()
            result = download_thumbnail(client, {'thumbnail': 'https://example.test/bad',
                'thumbnails': [{'url': 'https://example.test/good'}]}, Path(folder))
            self.assertEqual(result.read_bytes(), png_bytes())
            self.assertEqual(len(client.calls), 2)
            with self.assertRaises(ValueError):
                download_thumbnail(client, {'thumbnail': 'file:///private.png'}, Path(folder))
            for data, size in ((png_bytes(), MAX_THUMBNAIL_BYTES + 1), (png_bytes(), len(png_bytes()) + 4),
                               (b'<html>server failure</html>', 27)):
                with patch.object(client, 'urlopen', return_value=Response(data, size)), self.assertRaises(RuntimeError):
                    download_thumbnail(client, {'thumbnail': 'https://example.test/good'}, Path(folder))
            with self.assertRaises(ValueError):
                image_extension(png_bytes()[:-3])

    def run_worker(self, enabled=True, fail=False, existing=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / 'work'
            work.mkdir()
            if existing:
                (root / 'Example title.png').write_bytes(b'existing')
            calls = []
            class FakeYDL:
                def __init__(self, options):
                    self.options = options
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def extract_info(self, *args, **kwargs):
                    return {'title': 'Example title', 'thumbnail': 'https://example.test/image.png'}
                def process_info(self, info):
                    (work / 'media.mp4').write_bytes(b'media fixture')
                def urlopen(self, request):
                    calls.append(request.url)
                    if fail:
                        raise OSError('offline')
                    return Response(png_bytes())
            events = []
            with patch('yt_dlp.YoutubeDL', FakeYDL), patch('downloader.worker.find_ffmpeg', return_value=None):
                execute({'format': 'MP4', 'quality': '720p', 'url': 'https://youtu.be/jNQXAC9IVRw',
                         'work_dir': str(work), 'output_dir': str(root), 'save_thumbnails': enabled}, events.append)
            completed = next(e for e in events if e['event'] == 'completed')
            self.assertEqual(Path(completed['filename']).read_bytes(), b'media fixture')
            if enabled and not fail:
                self.assertEqual(Path(completed['thumbnail_filename']).read_bytes(), png_bytes())
                if existing:
                    self.assertEqual((root / 'Example title.png').read_bytes(), b'existing')
                    self.assertEqual(Path(completed['thumbnail_filename']).name, 'Example title (2).png')
            else:
                self.assertEqual(completed['thumbnail_filename'], '')
            return calls, events

    def test_disabled_thumbnails_make_no_image_request(self):
        calls, _ = self.run_worker(enabled=False)
        self.assertEqual(calls, [])

    def test_thumbnail_saved_and_existing_image_preserved(self):
        calls, _ = self.run_worker(existing=True)
        self.assertEqual(len(calls), 1)

    def test_thumbnail_failure_keeps_media_and_reports_warning(self):
        _, events = self.run_worker(fail=True)
        warnings = [e for e in events if e['event'] == 'warning']
        self.assertEqual(warnings[0]['warning_code'], 'SYTD-THUMBNAIL')
        self.assertFalse(any(e['event'] == 'error' for e in events))


if __name__ == '__main__':
    unittest.main()
