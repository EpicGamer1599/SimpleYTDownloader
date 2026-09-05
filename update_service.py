"""Background update coordination; the Pygame thread only reads snapshots/events."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from app_version import APP_VERSION
from downloader.process import ProcessTree
from update_checker import EXE_NAME, GitHubClient, Release, ReleaseAssetError, ReleaseDetails, UpdateCancelled, UpdateError, check_cancelled
from updater import atomic_json, cleanup_stage, create_stage, extract_update, launch_executable, prepare_plan, read_json


@dataclass(frozen=True)
class UpdateSnapshot:
    state: str = "idle"
    message: str = "Updates have not been checked yet."
    release: ReleaseDetails | None = None
    received: int = 0
    total: int = 0


class UpdateService:
    def __init__(self, client=None):
        self.client = client or GitHubClient()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._snapshot = UpdateSnapshot()
        self._events = queue.Queue()
        self._stopping = False
        self._transferred = False
        self._stage = None

    @property
    def install_supported(self):
        return os.name == "nt" and bool(getattr(sys, "frozen", False)) and Path(sys.executable).name.lower() == EXE_NAME.lower()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _set(self, **values):
        with self._lock:
            self._snapshot = replace(self._snapshot, **values)

    def events(self):
        result = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    def _start(self, action, initial):
        with self._lock:
            if self.alive or self._stopping or self._transferred:
                return False
            self._cancel.clear()
            self._snapshot = replace(self._snapshot, **initial)
            self._thread = threading.Thread(target=action, name="application-update", daemon=True)
            self._thread.start()
            return True

    def check(self, manual=False):
        def action():
            try:
                release = self.client.latest(self._cancel, APP_VERSION)
                check_cancelled(self._cancel)
                if release:
                    self._set(state="available", release=release, message=f"Version {release.version} is available.")
                    self._events.put(("available", manual))
                else:
                    self._set(state="latest", release=None, message="No newer published release is available.")
                    if manual:
                        self._events.put(("latest", True))
            except UpdateCancelled:
                self._set(state="idle", message="Update check cancelled.")
            except ReleaseAssetError as error:
                self._set(state="error", release=error.release, message=self._error(error))
                if manual:
                    self._events.put(("error", True))
            except Exception as error:
                self._set(state="error", release=None, message=self._error(error))
                if manual:
                    self._events.put(("error", True))
        return self._start(action, {"state": "checking", "message": "Checking published GitHub releases…", "release": None})

    def download_and_install(self):
        snapshot = self.snapshot()
        release = snapshot.release
        if snapshot.state != "available" or not isinstance(release, Release):
            return False
        if not self.install_supported:
            self._set(message="Run SimpleYTDownloader.exe to install application updates automatically.")
            return False

        def action():
            tree = None
            helper = None
            try:
                root = self._stage = create_stage()
                self.client.download(release, root / "release.zip", self._cancel,
                                     lambda received, total: self._set(received=received, total=total))
                self._set(state="extracting", message="ZIP verified. Extracting the update…")
                staged = extract_update(root / "release.zip", root, self._cancel)
                self._set(state="preparing", message="Checking the install folder and preparing a safe restart…")
                plan = prepare_plan(Path(sys.executable), staged, release.version, os.getpid(), self._cancel)
                check_cancelled(self._cancel)
                self._set(state="installing", message="Starting the updater. The application will restart shortly…")
                helper = launch_executable(root / "update-helper.exe", ["--apply-update", str(root / "plan.json"), "--update-token", plan.token])
                tree = ProcessTree(helper)
                deadline = time.monotonic() + 30
                while not (root / "ready.json").is_file():
                    check_cancelled(self._cancel)
                    if helper.poll() is not None or time.monotonic() > deadline:
                        raise UpdateError("The updater could not start. Your installed application has not changed.")
                    time.sleep(0.05)
                ready = read_json(root / "ready.json")
                if ready.get("token") != plan.token:
                    raise UpdateError("The updater returned an invalid startup acknowledgement.")
                with self._lock:
                    check_cancelled(self._cancel)
                    atomic_json(root / "commit.json", {"token": plan.token})
                    tree.release()
                    self._transferred = True
                    self._snapshot = replace(self._snapshot, state="restarting", message="Restarting to install the verified update…")
                self._events.put(("restart", True))
            except UpdateCancelled:
                self._set(state="cancelled", message="Update cancelled. Your installed application has not changed.")
                self._events.put(("cancelled", True))
            except Exception as error:
                self._set(state="error", message=self._error(error))
                self._events.put(("error", True))
            finally:
                if tree:
                    tree.close()
                if helper and not self._transferred:
                    try:
                        helper.wait(timeout=8)
                    except Exception:
                        pass
                if self._stage and not self._transferred:
                    try:
                        cleanup_stage(self._stage)
                        self._stage = None
                    except (OSError, UpdateError):
                        self._set(message=self.snapshot().message + " Some temporary update files could not be removed.")
        return self._start(action, {"state": "downloading", "message": "Downloading the verified GitHub release…", "received": 0, "total": release.size})

    @staticmethod
    def _error(error):
        if isinstance(error, UpdateError):
            return str(error)[:500]
        if isinstance(error, PermissionError):
            return "Windows denied access. Run the app from a writable folder and check that security software is not locking its files."
        if isinstance(error, OSError):
            return "The update could not be saved or extracted. Check disk space and folder permissions, then try again."
        return "The update could not be completed. Your installed application is still usable. Please try again later."

    def cancel(self):
        with self._lock:
            if self._transferred:
                return False
            self._cancel.set()
            if self.alive and self._snapshot.state != "checking":
                self._snapshot = replace(self._snapshot, state="cancelling", message="Cancelling the update…")
            return True

    def cancel_check(self):
        if self.snapshot().state == "checking":
            self.cancel()

    def shutdown(self, wait=True):
        self._stopping = True
        self.cancel()
        if wait and self.alive:
            self._thread.join(timeout=12)
