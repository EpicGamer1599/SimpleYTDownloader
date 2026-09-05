"""Thread-safe sequential queue. All network and conversion work is isolated."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, replace
from pathlib import Path

from config.settings import AUDIO_QUALITIES, VIDEO_QUALITIES
from downloader.dependencies import ROOT
from downloader.models import DownloadItem, TERMINAL_STATES
from downloader.process import ProcessTree
from downloader.utils import friendly_error, normalize_youtube_url, output_directory
from runtime import helper_command


class DownloadManager:
    def __init__(self, auto_start: bool = False, ffmpeg_location: str = "", worker_command: list[str] | None = None):
        self.auto_start = auto_start
        self.ffmpeg_location = ffmpeg_location
        self.items: list[DownloadItem] = []
        self.running = False
        self._paused = False
        self.active_id: str | None = None
        self._condition = threading.Condition(threading.RLock())
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._command = worker_command or helper_command("download-worker")
        self._thread = threading.Thread(target=self._dispatch, name="download-queue", daemon=True)
        self._thread.start()

    def snapshot(self) -> list[DownloadItem]:
        with self._condition:
            return [replace(item) for item in self.items]

    def add(self, url: str, format: str, quality: str, output_dir: str) -> DownloadItem:
        url = normalize_youtube_url(url)
        if format not in ("MP4", "MP3") or quality not in (VIDEO_QUALITIES if format == "MP4" else AUDIO_QUALITIES):
            raise ValueError("Choose a valid format and quality.")
        folder = output_directory(output_dir)
        item = DownloadItem(url, format, quality, str(folder), title=f"YouTube video · {url.rsplit('=', 1)[-1]}")
        with self._condition:
            if self._stop.is_set():
                raise ValueError("The application is closing.")
            self.items.append(item)
            if self.auto_start and not self._paused:
                self.running = True
            self._condition.notify_all()
        return replace(item)

    def start(self) -> None:
        with self._condition:
            self.running = True
            self._paused = False
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            self.running = False
            self._paused = True

    def cancel(self, item_id: str) -> None:
        with self._condition:
            for item in self.items:
                if item.id == item_id and item.state not in TERMINAL_STATES:
                    if item.id == self.active_id:
                        item.stage = "Cancelling..."
                        self._cancel.set()
                    else:
                        item.state, item.stage = "Cancelled", "Cancelled before download"

    def remove(self, item_id: str) -> None:
        with self._condition:
            self.items[:] = [i for i in self.items if i.id != item_id or i.id == self.active_id]

    def clear_completed(self) -> None:
        with self._condition:
            self.items[:] = [i for i in self.items if i.state != "Completed"]

    def retry(self, item_id: str) -> None:
        with self._condition:
            for index, item in enumerate(self.items):
                if item.id == item_id and item.state in ("Failed", "Cancelled") and item.id != self.active_id:
                    self.items[index] = DownloadItem(item.url, item.format, item.quality, item.output_dir, id=item.id, title=item.title)
            if self.auto_start and not self._paused:
                self.running = True
            self._condition.notify_all()

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        self._cancel.set()
        with self._condition:
            self.running = False
            self._condition.notify_all()
        if wait:
            self._thread.join(timeout=12)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _dispatch(self) -> None:
        while not self._stop.is_set():
            with self._condition:
                self._condition.wait_for(lambda: self._stop.is_set() or (self.running and any(i.state == "Waiting" for i in self.items)))
                if self._stop.is_set():
                    return
                item = next(i for i in self.items if i.state == "Waiting")
                self._cancel.clear()
                self.active_id = item.id
                item.state, item.stage = "Downloading", "Starting download"
            try:
                self._run_item(item)
            except Exception as error:
                with self._condition:
                    item.state, item.error = "Failed", friendly_error(error)
                    item.stage = "Download failed"
            finally:
                with self._condition:
                    item.speed, item.eta = 0, None
                    self.active_id = None
                    if not any(i.state == "Waiting" for i in self.items):
                        self.running = False

    def _run_item(self, item: DownloadItem) -> None:
        work_dir = None
        process = None
        tree = None
        reader = None
        try:
            output = output_directory(item.output_dir, create=True)
            work_dir = Path(tempfile.mkdtemp(prefix=".ytd-", dir=output))
            job = asdict(item) | {"work_dir": str(work_dir), "ffmpeg_location": self.ffmpeg_location}
            process = subprocess.Popen(self._command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                       env=os.environ | {"PYTHONIOENCODING": "utf-8"},
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                       start_new_session=os.name != "nt")
            tree = ProcessTree(process)
            messages: queue.Queue = queue.Queue()

            def read_events():
                try:
                    for line in process.stdout:
                        try:
                            event = json.loads(line)
                            if isinstance(event, dict):
                                messages.put(event)
                        except ValueError:
                            continue
                finally:
                    messages.put(None)

            reader = threading.Thread(target=read_events, name="download-events", daemon=True)
            reader.start()
            process.stdin.write(json.dumps(job) + "\n")
            process.stdin.close()
            while True:
                if self._cancel.is_set() or self._stop.is_set():
                    tree.close()
                    process.wait(timeout=5)
                    reader.join(timeout=2)
                    with self._condition:
                        if item.state != "Completed":
                            # A file committed just before cancellation still counts as complete.
                            while not messages.empty():
                                event = messages.get_nowait()
                                if event and event.get("event") == "completed":
                                    self._apply_event(item, event)
                            if item.state != "Completed":
                                item.state, item.stage = "Cancelled", "Download cancelled"
                    break
                try:
                    event = messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event is None:
                    break
                with self._condition:
                    self._apply_event(item, event)
            process.wait(timeout=5)
            with self._condition:
                if item.state not in TERMINAL_STATES:
                    item.state, item.stage = "Failed", "Download failed"
                    item.error = "The download worker stopped unexpectedly. Check dependencies in Settings and retry."
        finally:
            if tree:
                tree.close()
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                if reader:
                    reader.join(timeout=2)
                for pipe in (process.stdin, process.stdout):
                    if pipe and not pipe.closed:
                        pipe.close()
            if work_dir:
                # Only this item's uniquely created staging folder is removed.
                try:
                    resolved = work_dir.resolve()
                    if resolved.parent == output.resolve() and resolved.name.startswith(".ytd-"):
                        shutil.rmtree(resolved)
                except OSError:
                    with self._condition:
                        item.warning = f"Temporary files could not be removed: {work_dir}"

    def _apply_event(self, item: DownloadItem, event: dict) -> None:
        kind = event.get("event")
        for key in ("title", "actual_quality", "progress", "downloaded_bytes", "total_bytes", "speed", "eta", "stage", "filename", "warning", "error"):
            if key in event:
                setattr(item, key, event[key])
        if kind == "completed":
            item.state, item.stage, item.progress = "Completed", "Saved to your folder", 1.0
        elif kind == "error":
            item.state, item.stage = "Failed", "Download failed"
        if item.state in TERMINAL_STATES and not any(i.state == "Waiting" for i in self.items):
            self.running = False
