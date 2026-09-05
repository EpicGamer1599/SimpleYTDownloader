"""Exercise the real frozen helper using disposable installation copies only.

No network access or production update. Run after building:
    python -m tests.updater_executable_smoke
"""
import ctypes
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from ctypes import wintypes
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from app_version import APP_VERSION
from downloader.process import ProcessTree
from update_checker import EXE_NAME
from updater import (WindowsProcess, atomic_json, cleanup_stage, create_stage, file_hash,
                     launch_executable, prepare_plan, read_json)

BASE = Path(__file__).resolve().parents[1]
ARTIFACTS = BASE / "test-artifacts"
REPORT = []


def windows_for(executable):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    @callback_type
    def callback(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process = WindowsProcess(pid.value, executable)
        except Exception:
            return True
        try:
            if process.running():
                found.append((hwnd, pid.value))
        finally:
            process.close()
        return True

    user.EnumWindows(callback, 0)
    return found


def close_windows(executable):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for hwnd, _ in windows_for(executable):
        user.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE, only for the verified disposable image.


def wait_until(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("Timed out waiting for disposable executable test state.")


@unittest.skipUnless(os.name == "nt", "Windows packaged updater")
class PackagedUpdaterTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="packaged-update-test-", dir=ARTIFACTS)).resolve()
        self.install = self.directory / "Writable installation with spaces"
        self.install.mkdir()
        self.target = self.install / EXE_NAME
        self.previous_version = APP_VERSION
        prior_builds = {"test_upgrade_from_1_0_0": "1.0.0", "test_upgrade_from_1_0_2": "1.0.2", "test_upgrade_from_1_0_3": "1.0.3"}
        if self._testMethodName in prior_builds:
            self.previous_version = prior_builds[self._testMethodName]
            with zipfile.ZipFile(BASE / "Builds" / (self.previous_version + ".zip")) as archive:
                self.target.write_bytes(archive.read(EXE_NAME))
        else:
            shutil.copyfile(BASE / "dist" / EXE_NAME, self.target)
        self.original_hash = file_hash(self.target)
        self.environment = patch.dict(os.environ, {"YTD_CONFIG_DIR": str(self.directory / "settings"), "SDL_AUDIODRIVER": "dummy"})
        self.environment.start()
        self.old = launch_executable(self.target, ["--smoke-test", "90"])
        self.old_tree = ProcessTree(self.old)
        self.helper = self.helper_tree = None
        self.record = {"test": self._testMethodName}

    def tearDown(self):
        close_windows(self.target)
        if hasattr(self, "root"):
            close_windows(self.root / "update-helper.exe")
        self.old_tree.close()
        self.old.wait(timeout=10)
        if self.helper_tree:
            # A failed handoff may leave the helper showing its recovery dialog.
            # Reacquire ownership only of this test's known child process.
            ProcessTree(self.helper).close()
        if self.helper:
            self.helper.wait(timeout=10)
        # The restarted GUI is independent of both original and helper.
        wait_until(lambda: not windows_for(self.target), timeout=12)
        self.environment.stop()
        # Only delete this test's resolved directory inside test-artifacts.
        assert self.directory.parent == ARTIFACTS.resolve()
        assert self.directory.name.startswith("packaged-update-test-")
        for attempt in range(80):
            try:
                shutil.rmtree(self.directory)
                break
            except OSError:
                if attempt == 79:
                    raise
                time.sleep(0.15)  # allow one-file bootloader file handles to close
        REPORT.append(self.record)

    def stage(self, expected_version):
        original_windows = wait_until(lambda: windows_for(self.target))
        self.original_pid = original_windows[0][1]
        self.root = create_stage(self.directory)
        payload = self.root / "payload"
        payload.mkdir()
        staged = payload / EXE_NAME
        shutil.copyfile(BASE / "dist" / EXE_NAME, staged)
        # Change an unused DOS-stub byte, leaving Windows PE headers, the
        # PyInstaller archive, and APP_VERSION intact; this makes replacement
        # verifiable by hash without building or publishing a fake release.
        with staged.open("r+b") as stream:
            stream.seek(0x40)
            value = stream.read(1)[0]
            stream.seek(0x40)
            stream.write(bytes([value ^ 1]))
        self.plan = prepare_plan(self.target, staged, expected_version, self.original_pid, threading.Event())
        # The old frozen app would create its plan with its own APP_VERSION.
        self.plan = replace(self.plan, previous_version=self.previous_version)
        atomic_json(self.root / "plan.json", asdict(self.plan))
        self.assertNotEqual(self.plan.original_sha256, self.plan.new_sha256)
        self.helper = launch_executable(self.root / "update-helper.exe", [
            "--apply-update", str(self.root / "plan.json"), "--update-token", self.plan.token])
        self.helper_tree = ProcessTree(self.helper)
        wait_until(lambda: (self.root / "ready.json").is_file())
        ready = read_json(self.root / "ready.json")
        self.assertEqual(ready["token"], self.plan.token)
        time.sleep(0.25)
        self.assertIsNone(self.old.poll())
        self.assertEqual(file_hash(self.target), self.original_hash)
        self.assertFalse(self.plan.backup.exists())
        self.record["waited_for_running_app"] = True

    def transaction(self, rollback=False):
        expected = ".".join([str(int(APP_VERSION.split(".")[0]) + 1), "0", "0"]) if rollback else APP_VERSION
        self.stage(expected)
        atomic_json(self.root / "commit.json", {"token": self.plan.token})
        self.helper_tree.release()  # same handoff as UpdateService
        close_windows(self.target)
        self.assertEqual(self.old.wait(timeout=15), 0)
        marker_name = "rollback-ok.json" if rollback else "launch-ok.json"
        captured = {}

        def restarted():
            for filename in (marker_name, "status.json"):
                try:
                    captured[filename] = read_json(self.root / filename)
                except (OSError, ValueError):
                    pass
            windows = windows_for(self.target)
            return windows and windows[0][1] != self.original_pid and not self.root.exists()

        wait_until(restarted, timeout=65)
        self.assertEqual(self.helper.wait(timeout=10), 0)
        self.assertEqual(file_hash(self.target), self.original_hash if rollback else self.plan.new_sha256)
        self.assertFalse(self.plan.backup.exists())
        self.assertFalse(self.plan.incoming.exists())
        self.assertFalse(self.plan.failed.exists())
        self.assertFalse(self.root.exists())
        self.record.update({"result": "rolled_back" if rollback else "success", "temporary_files_removed": True,
                            "relaunched_gui": True, "target_hash_verified": True, "captured": captured})

    def test_successful_replacement_restart_and_cleanup(self):
        self.transaction()

    @unittest.skipUnless((BASE / "Builds" / "1.0.0.zip").is_file(), "Previous release ZIP required")
    def test_upgrade_from_1_0_0(self):
        self.transaction()
        self.record["previous_version"] = "1.0.0"
        self.record["new_version"] = APP_VERSION

    @unittest.skipUnless((BASE / "Builds" / "1.0.2.zip").is_file(), "Previous release ZIP required")
    def test_upgrade_from_1_0_2(self):
        self.transaction()
        self.record["previous_version"] = "1.0.2"
        self.record["new_version"] = APP_VERSION

    @unittest.skipUnless((BASE / "Builds" / "1.0.3.zip").is_file(), "Previous release ZIP required")
    def test_upgrade_from_1_0_3(self):
        self.transaction()
        self.record["previous_version"] = "1.0.3"
        self.record["new_version"] = APP_VERSION

    def test_failed_new_version_startup_restores_and_restarts_old(self):
        self.transaction(rollback=True)

    def test_cancel_helper_before_handoff_preserves_running_exe(self):
        self.stage(APP_VERSION)
        atomic_json(self.root / "cancel.json", {"token": self.plan.token})
        self.assertEqual(self.helper.wait(timeout=10), 2)
        self.assertIsNone(self.old.poll())
        self.assertEqual(file_hash(self.target), self.original_hash)
        self.assertFalse(self.plan.backup.exists())
        cleanup_stage(self.root)
        self.record.update({"result": "cancelled", "original_remained_running": True, "temporary_files_removed": True})


if __name__ == "__main__":
    if not (BASE / "dist" / EXE_NAME).is_file():
        raise SystemExit("Build dist/SimpleYTDownloader.exe first.")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PackagedUpdaterTests))
    (ARTIFACTS / "updater-executable-report.json").write_text(json.dumps({
        "successful": result.wasSuccessful(), "tests": result.testsRun, "transactions": REPORT,
        "production_update": False}, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
