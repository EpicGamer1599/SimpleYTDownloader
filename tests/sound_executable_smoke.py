"""Verify bundled sound decoding/playback using only a copied one-file EXE."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app_version import APP_VERSION

BASE = Path(__file__).resolve().parents[1]


class PackagedSoundTests(unittest.TestCase):
    def test_executable_loads_and_plays_embedded_sounds_without_external_assets(self):
        artifacts = BASE / "test-artifacts"
        artifacts.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sound-exe-", dir=artifacts) as folder:
            target = Path(folder) / "SimpleYTDownloader.exe"
            shutil.copyfile(BASE / "dist" / target.name, target)
            self.assertFalse((target.parent / "soundeffects").exists())
            result = subprocess.run([str(target), "--sound-smoke-test"], cwd=folder,
                env=os.environ | {"SDL_AUDIODRIVER": "dummy", "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["version"], APP_VERSION)
            self.assertTrue(report["frozen"])
            self.assertEqual(set(report["loaded"]), {"toggle", "downloading", "finished"})
            self.assertFalse(report["problem"])
            for field in ("loop_started", "loop_stopped", "finish_started", "toggle_started"):
                self.assertTrue(report[field], field)
            (artifacts / f"sound-executable-{APP_VERSION}.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
