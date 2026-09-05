import ctypes
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from downloader.manager import DownloadManager


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = DownloadManager(worker_command=[sys.executable, "-u", "-m", "tests.fake_worker"])

    def tearDown(self):
        self.manager.shutdown()
        self.assertFalse(self.manager.alive)
        self.temp.cleanup()

    def add(self, id="jNQXAC9IVRw"):
        return self.manager.add("https://youtu.be/" + id, "MP4", "720p", self.temp.name)

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            items = self.manager.snapshot()
            if predicate(items):
                return items
            time.sleep(0.02)
        self.fail("Timed out: " + repr(self.manager.snapshot()))

    def test_manual_start_sequential_pause_resume_and_clear(self):
        self.add()
        self.add()
        time.sleep(0.1)
        self.assertTrue(all(i.state == "Waiting" for i in self.manager.snapshot()))
        self.manager.start()
        self.wait_for(lambda items: items[0].progress > 0)
        self.manager.pause()
        items = self.wait_for(lambda items: items[0].state == "Completed")
        self.assertEqual(items[1].state, "Waiting")
        self.manager.start()
        self.wait_for(lambda items: all(i.state == "Completed" for i in items))
        self.manager.clear_completed()
        self.assertEqual(self.manager.snapshot(), [])
        self.add()
        time.sleep(0.15)
        self.assertEqual(self.manager.snapshot()[0].state, "Waiting")

    def test_failure_continues_and_retry(self):
        first = self.add("FAIL0000001")
        self.add()
        self.manager.start()
        items = self.wait_for(lambda items: items[1].state == "Completed")
        self.assertEqual(items[0].state, "Failed")
        self.assertIn("private", items[0].error)
        self.manager.pause()
        self.manager.retry(first.id)
        self.assertEqual(self.manager.snapshot()[0].state, "Waiting")

    def test_cancel_active_and_waiting_cleanup(self):
        first = self.add()
        second = self.add()
        self.manager.cancel(second.id)
        self.manager.start()
        self.wait_for(lambda items: items[0].progress > 0)
        self.manager.cancel(first.id)
        self.wait_for(lambda items: items[0].state == "Cancelled" and self.manager.active_id is None)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.manager.remove(first.id)
        self.assertEqual(len(self.manager.snapshot()), 1)

    def test_auto_start(self):
        self.manager.auto_start = True
        self.add()
        self.wait_for(lambda items: items[0].state == "Completed")

    def test_cancel_during_thumbnail_keeps_saved_media_and_cleans_staging(self):
        item = self.manager.add("https://youtu.be/THUMB000001", "MP4", "720p", self.temp.name, True)
        self.manager.start()
        self.wait_for(lambda items: items[0].media_saved)
        self.manager.cancel(item.id)
        items = self.wait_for(lambda items: self.manager.active_id is None)
        self.assertEqual(items[0].state, "Completed")
        self.assertEqual(Path(items[0].filename).read_bytes(), b"completed media")
        self.assertEqual(items[0].warning_code, "SYTD-THUMBNAIL")
        self.assertFalse(list(Path(self.temp.name).glob(".ytd-*")))

    def test_worker_crash_or_error_after_save_keeps_media_and_continues_queue(self):
        for video_id in ("CRASH000001", "SAVEERROR01"):
            self.manager.add("https://youtu.be/" + video_id, "MP4", "720p", self.temp.name, True)
        self.add()
        self.manager.start()
        items = self.wait_for(lambda items: items[-1].state == "Completed" and self.manager.active_id is None)
        for item in items[:2]:
            self.assertEqual(item.state, "Completed")
            self.assertFalse(item.error)
            self.assertEqual(item.warning_code, "SYTD-THUMBNAIL")
            self.assertEqual(Path(item.filename).read_bytes(), b"completed media")
        self.assertFalse(list(Path(self.temp.name).glob(".ytd-*")))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object test")
    def test_shutdown_terminates_child_process(self):
        self.add("CHILD000001")
        self.manager.start()
        self.wait_for(lambda items: "Converting" in items[0].stage)
        pid_file = next(Path(self.temp.name).glob(".ytd-*/child.pid"))
        pid = int(pid_file.read_text())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        self.assertTrue(handle)
        try:
            self.manager.shutdown()
            self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
