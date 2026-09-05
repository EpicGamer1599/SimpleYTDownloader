"""Exercise real Pygame events and a native Windows folder selection."""
import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import PropertyMock, patch

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from config.settings import SettingsManager
from downloader.manager import DownloadManager
from ui.app import App
from ui.native import clipboard_get, clipboard_set
from update_checker import UpdateCancelled
from update_service import UpdateService
from tests.test_updates import parse_release, release_data

ARTIFACTS = Path(__file__).resolve().parents[1] / "test-artifacts"


class GuiTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        settings = SettingsManager(Path(self.temp.name) / "config")
        settings.settings.output_dir = self.temp.name
        settings.settings.auto_check_updates = False
        manager = DownloadManager(worker_command=[sys.executable, "-u", "-m", "tests.fake_worker"])
        self.app = App(settings, manager)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def click(self, key):
        self.app.draw()
        hit = next(hit for hit in self.app.hits if hit.key == key)
        self.app.step([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=hit.rect.center),
                       pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=hit.rect.center)])

    def key(self, key, mod=0):
        self.app.step([pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod)])

    def test_manual_update_dialog_background_check_and_preferences(self):
        entered, release_check = threading.Event(), threading.Event()
        class Client:
            def latest(self, cancel, version):
                entered.set()
                release_check.wait(3)
                return parse_release(release_data())
        self.app.updates.shutdown()
        self.app.updates = UpdateService(Client())
        self.addCleanup(release_check.set)
        self.click("nav:Settings")
        self.click("check_updates")
        self.assertTrue(entered.wait(1))
        self.click("nav:Download")
        started = time.monotonic()
        for _ in range(15):
            self.app.step([])
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(self.app.update_dialog.visible)
        release_check.set()
        self.app.updates._thread.join(2)
        self.app.step([])
        self.assertTrue(self.app.update_dialog.visible)
        self.assertEqual(self.app.updates.snapshot().release.version, "1.1.0")
        self.assertTrue(all(h.key.startswith("update:") for h in self.app.hits))
        self.assertFalse(any(h.key == "update:now" for h in self.app.hits))  # source checkout
        with patch.object(UpdateService, "install_supported", new_callable=PropertyMock, return_value=True):
            self.app.draw()
            pygame.image.save(self.app.surface, ARTIFACTS / "update-available.png")
            self.assertTrue(any(h.key == "update:now" for h in self.app.hits))
            self.app.manager.add("https://youtu.be/jNQXAC9IVRw", "MP4", "720p", self.temp.name)
            self.app.draw()
            self.assertFalse(any(h.key == "update:now" for h in self.app.hits))
        self.key(pygame.K_ESCAPE)
        self.assertFalse(self.app.update_dialog.visible)
        self.app.updates.client.latest = lambda *args: None
        self.click("nav:Settings")
        self.click("toggle:auto_check_updates")
        self.assertTrue(SettingsManager(Path(self.temp.name) / "config").settings.auto_check_updates)
        self.app.updates._thread.join(2)
        self.click("toggle:auto_check_updates")
        self.assertFalse(SettingsManager(Path(self.temp.name) / "config").settings.auto_check_updates)
        pygame.image.save(self.app.surface, ARTIFACTS / "update-settings.png")

    def test_update_download_progress_and_cancel_leave_gui_usable(self):
        entered = threading.Event()
        class Client:
            def download(self, release, target, cancel, progress):
                progress(release.size // 2, release.size)
                entered.set()
                cancel.wait(3)
                raise UpdateCancelled("cancelled")
        self.app.updates.shutdown()
        self.app.updates = UpdateService(Client())
        self.app.updates._set(state="available", release=parse_release(release_data()))
        self.app.update_dialog.show()
        with patch.object(UpdateService, "install_supported", new_callable=PropertyMock, return_value=True):
            self.click("update:now")
            self.assertTrue(entered.wait(1))
            stage = self.app.updates._stage
            started = time.monotonic()
            for _ in range(15):
                self.app.step([])
            self.assertLess(time.monotonic() - started, 1)
            self.assertGreater(self.app.updates.snapshot().received, 0)
            pygame.image.save(self.app.surface, ARTIFACTS / "update-progress.png")
            self.click("update:later")
            self.app.updates._thread.join(2)
        self.app.step([])
        self.assertEqual(self.app.updates.snapshot().state, "cancelled")
        self.assertFalse(stage.exists())
        self.click("update:later")
        self.click("nav:Queue")
        self.assertTrue(self.app.running)
        self.assertEqual(self.app.page, "Queue")

    def test_url_format_quality_multiple_items_and_keyboard(self):
        self.click("url")
        self.app.step([pygame.event.Event(pygame.TEXTINPUT, text="https://youtu.be/jNQXAC9IVRw")])
        self.assertEqual(self.app.inputs["url"].text, "https://youtu.be/jNQXAC9IVRw")
        self.click("format:MP3")
        self.click("quality:320 kbps")
        self.assertEqual(self.app.audio_quality, "320 kbps")
        self.click("add")
        self.app.notice = None
        self.assertEqual(self.app.manager.snapshot()[0].format, "MP3")
        self.assertEqual(self.app.manager.snapshot()[0].quality, "320 kbps")
        self.click("format:MP4")
        self.click("quality:1080p")
        self.click("url")
        self.app.step([pygame.event.Event(pygame.TEXTINPUT, text="youtu.be/jNQXAC9IVRw")])
        self.key(pygame.K_RETURN)
        self.app.notice = None
        self.assertEqual(len(self.app.manager.snapshot()), 2)
        self.assertTrue(all(i.state == "Waiting" for i in self.app.manager.snapshot()))
        self.click("url")
        self.app.step([pygame.event.Event(pygame.TEXTINPUT, text="bad link")])
        self.key(pygame.K_RETURN)
        self.assertTrue(self.app.notice[1])
        self.assertEqual(len(self.app.manager.snapshot()), 2)
        self.key(pygame.K_ESCAPE)
        self.click("url")
        self.key(pygame.K_a, pygame.KMOD_CTRL)
        self.key(pygame.K_BACKSPACE)
        self.assertEqual(self.app.inputs["url"].text, "")
        self.key(pygame.K_TAB)
        self.assertEqual(self.app.focus, "paste")
        self.app.draw()
        pygame.image.save(self.app.surface, ARTIFACTS / "download.png")

    @unittest.skipUnless(os.name == "nt", "Windows clipboard")
    def test_clipboard_ctrl_v_and_paste_button(self):
        previous = clipboard_get()
        try:
            clipboard_set("https://youtu.be/jNQXAC9IVRw")
            self.click("url")
            self.key(pygame.K_v, pygame.KMOD_CTRL)
            self.assertEqual(self.app.inputs["url"].text, "https://youtu.be/jNQXAC9IVRw")
            self.app.inputs["url"].set("")
            self.click("paste")
            self.assertEqual(self.app.inputs["url"].text, "https://youtu.be/jNQXAC9IVRw")
        finally:
            clipboard_set(previous)

    def test_resizing_navigation_scrolling_and_responsiveness(self):
        for index in range(10):
            self.app.manager.add("https://youtu.be/" + ("FAIL0000001" if index == 1 else "jNQXAC9IVRw"), "MP4", "720p", self.temp.name)
        self.click("nav:Queue")
        self.click("start")
        frames, maximum_frame = 0, 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            start = time.monotonic()
            self.app.step([])
            maximum_frame = max(maximum_frame, time.monotonic() - start)
            frames += 1
            time.sleep(0.005)
        self.assertGreater(frames, 30)
        self.assertLess(maximum_frame, 0.5)
        self.assertTrue(any(i.state == "Completed" for i in self.app.manager.snapshot()))
        self.assertTrue(any(i.state == "Failed" for i in self.app.manager.snapshot()))
        pygame.image.save(self.app.surface, ARTIFACTS / "queue.png")
        self.app.step([pygame.event.Event(pygame.MOUSEWHEEL, y=-5, x=0)])
        self.assertGreater(self.app.scroll["Queue"], 0)
        self.app.step([pygame.event.Event(pygame.VIDEORESIZE, w=760, h=600)])
        self.assertEqual(self.app.surface.get_size(), (760, 600))
        self.click("nav:Settings")
        self.assertGreater(self.app.max_scroll, 0)
        pygame.image.save(self.app.surface, ARTIFACTS / "settings-small.png")
        self.click("nav:Download")
        self.app.inputs["url"].set("https://youtu.be/jNQXAC9IVRw")
        self.app.step([pygame.event.Event(pygame.MOUSEWHEEL, y=-8, x=0)])
        self.assertTrue(any(hit.key == "add" for hit in self.app.hits))
        pygame.image.save(self.app.surface, ARTIFACTS / "download-small.png")
        self.click("nav:About")
        self.app.draw()
        pygame.image.save(self.app.surface, ARTIFACTS / "about-small.png")

    @unittest.skipUnless(os.name == "nt", "Native Windows folder picker")
    def test_real_native_folder_selection(self):
        selected = Path(self.temp.name) / "Chosen folder"
        selected.mkdir()
        self.app.inputs["output"].set(str(selected))
        self.click("browse:output")
        process = self.app.picker
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetDlgCtrlID.argtypes = [wintypes.HWND]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        clicked = False
        frames = 0
        seen = set()

        @callback_type
        def child(hwnd, _):
            nonlocal clicked
            text = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, text, 256)
            seen.add((user.GetDlgCtrlID(hwnd), text.value))
            if "Select Folder" in text.value.replace("&", "") and user.GetDlgCtrlID(hwnd) == 1:
                user.PostMessageW(hwnd, 0x00F5, 0, 0)  # BM_CLICK on this dialog's Select Folder button.
                clicked = True
            return True

        @callback_type
        def window(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, 256)
            # Windows venv executables may redirect to a child Python process.
            if pid.value == process.pid or title.value == "YouTube Downloader — Choose a folder":
                user.EnumChildWindows(hwnd, child, 0)
            return True

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and self.app.picker:
            self.app.step([])
            if not clicked:
                user.EnumWindows(window, 0)
            frames += 1
            time.sleep(0.02)
        self.assertTrue(clicked, "Native folder picker Select Folder button was not found: " + repr((seen, self.app.notice)))
        self.assertIsNone(self.app.picker)
        self.assertGreater(frames, 2)
        reloaded = SettingsManager(Path(self.temp.name) / "config")
        self.assertEqual(Path(reloaded.settings.output_dir), selected)


if __name__ == "__main__":
    unittest.main()
