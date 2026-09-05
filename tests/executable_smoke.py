"""Verify the built executable's real media worker and native folder picker.

Run explicitly after building: python -m tests.executable_smoke
"""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_gui, test_media

EXECUTABLE = Path(__file__).resolve().parents[1] / "dist" / "SimpleYTDownloader.exe"


def execute_packaged(job, emit):
    result = subprocess.run([str(EXECUTABLE), "--download-worker"],
                            input=json.dumps(job) + "\n", capture_output=True,
                            text=True, encoding="utf-8", timeout=45)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if result.returncode:
        raise AssertionError(f"Packaged worker failed: {events!r}; stderr={result.stderr!r}")
    for event in events:
        emit(event)


class ExecutableMediaTests(test_media.MediaTests):
    def download(self, filename, format, quality, save_thumbnails=False):
        with patch.object(test_media, "execute", execute_packaged):
            return super().download(filename, format, quality, save_thumbnails)


class ExecutablePickerTests(test_gui.GuiTests):
    def setUp(self):
        super().setUp()
        override = patch("ui.app.helper_command", lambda kind: [str(EXECUTABLE), "--" + kind])
        override.start()
        self.addCleanup(override.stop)


if __name__ == "__main__":
    if not EXECUTABLE.is_file():
        raise SystemExit("Build dist/SimpleYTDownloader.exe first.")
    suite = unittest.TestSuite([
        *unittest.defaultTestLoader.loadTestsFromTestCase(ExecutableMediaTests),
        ExecutablePickerTests("test_real_native_folder_selection"),
    ])
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
