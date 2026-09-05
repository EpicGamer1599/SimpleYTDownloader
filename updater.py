"""Verified staging and a self-contained Windows updater carried by the app EXE.

The downloaded executable is never run until the original application exits.
An independently extracted copy of the *current* EXE performs the transaction.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from app_version import APP_VERSION
from downloader.process import ProcessTree
from update_checker import CHUNK_SIZE, EXE_NAME, MAX_EXPANDED, REPOSITORY, SemVersion, UpdateCancelled, UpdateError, check_cancelled

STAGE_PREFIX = "SimpleYTDownloader-update-"


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path) -> dict:
    if path.stat().st_size > 32 * 1024:
        raise UpdateError("The local update record is invalid.")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise UpdateError("The local update record is invalid.")
    return result


def file_hash(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            if cancel is not None:
                check_cancelled(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def validate_executable(path: Path) -> int:
    """Check a PE executable header and return its machine architecture."""
    size = path.stat().st_size
    if not 512 <= size <= MAX_EXPANDED or is_link(path):
        raise UpdateError("The update does not contain a valid Windows executable.")
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:2] != b"MZ":
            raise UpdateError("The downloaded file is not a Windows executable.")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= size - 24:
            raise UpdateError("The executable header is corrupt.")
        stream.seek(offset)
        pe = stream.read(24)
        if pe[:4] != b"PE\0\0":
            raise UpdateError("The executable header is corrupt.")
        machine = struct.unpack_from("<H", pe, 4)[0]
        flags = struct.unpack_from("<H", pe, 22)[0]
        if machine not in (0x14c, 0x8664, 0xaa64) or not flags & 0x2 or flags & 0x2000:
            raise UpdateError("The update is not a supported Windows application executable.")
    return machine


def create_stage(directory: Path | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=directory)).resolve()
    atomic_json(root / "owner.json", {"repository": REPOSITORY, "token": uuid.uuid4().hex, "schema": 1})
    return root


def validate_stage(root: Path) -> dict:
    root = Path(root)
    if not root.is_absolute() or root != root.resolve() or not root.name.startswith(STAGE_PREFIX) or is_link(root):
        raise UpdateError("The temporary update directory is invalid.")
    owner = read_json(root / "owner.json")
    token = owner.get("token", "")
    if (owner.get("repository") != REPOSITORY or owner.get("schema") != 1
            or not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token)):
        raise UpdateError("The temporary update directory is not owned by this application.")
    return owner


def cleanup_stage(root: Path) -> None:
    """Delete only the verified transaction directory, never an install folder."""
    validate_stage(root)
    # Refuse reparse points even if someone changed the staging directory locally.
    for base, directories, files in os.walk(root, followlinks=False):
        if any(is_link(Path(base) / name) for name in directories + files):
            raise UpdateError("Temporary cleanup refused a directory containing links.")
    # Keep the ownership marker until the end so a temporarily locked helper
    # can be retried without losing the evidence needed to validate cleanup.
    for child in root.iterdir():
        if child.name == "owner.json":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    (root / "owner.json").unlink()
    root.rmdir()


def extract_update(archive: Path, root: Path, cancel: threading.Event) -> Path:
    validate_stage(root)
    payload = root / "payload"
    payload.mkdir()
    output = payload / EXE_NAME
    try:
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            # A root-only executable prevents traversal, duplicate names, links,
            # side-loaded DLLs, scripts, and accidental source-code ZIP installs.
            if len(entries) != 1 or entries[0].filename != EXE_NAME:
                raise UpdateError(f"The release ZIP must contain only {EXE_NAME} at its root.")
            entry = entries[0]
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if entry.is_dir() or entry.flag_bits & 1 or kind not in (0, stat.S_IFREG):
                raise UpdateError("The release ZIP contains an encrypted file or a link.")
            if not 512 <= entry.file_size <= MAX_EXPANDED or entry.file_size > max(1, entry.compress_size) * 250:
                raise UpdateError("The extracted update exceeds the allowed size or compression ratio.")
            received = 0
            with package.open(entry) as source, output.open("xb") as destination:
                while chunk := source.read(CHUNK_SIZE):
                    check_cancelled(cancel)
                    received += len(chunk)
                    if received > entry.file_size or received > MAX_EXPANDED:
                        raise UpdateError("The ZIP expanded beyond its declared size.")
                    destination.write(chunk)
            if received != entry.file_size:
                raise UpdateError("The executable in the ZIP is incomplete.")
        check_cancelled(cancel)
        validate_executable(output)
        return output
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        raise UpdateError("The update ZIP is corrupt or could not be extracted. Please try again.") from None


@dataclass(frozen=True)
class UpdatePlan:
    repository: str
    token: str
    version: str
    previous_version: str
    target: str
    staged_exe: str
    original_sha256: str
    new_sha256: str
    parent_pid: int

    @property
    def root(self) -> Path:
        return Path(self.staged_exe).parent.parent

    @property
    def backup(self) -> Path:
        return Path(self.target).with_name(EXE_NAME + ".backup-" + self.token)

    @property
    def incoming(self) -> Path:
        return Path(self.target).with_name(EXE_NAME + ".new-" + self.token)

    @property
    def failed(self) -> Path:
        return Path(self.target).with_name(EXE_NAME + ".failed-" + self.token)


def load_plan(path: Path, token: str) -> UpdatePlan:
    owner = validate_stage(path.parent)
    if path.name != "plan.json" or token != owner["token"]:
        raise UpdateError("The update transaction token is invalid.")
    try:
        plan = UpdatePlan(**read_json(path))
        target, staged = Path(plan.target), Path(plan.staged_exe)
        if (plan.repository != REPOSITORY or plan.token != token or plan.root != path.parent
                or not target.is_absolute() or target != target.resolve()
                or target.name.lower() != EXE_NAME.lower() or target.is_relative_to(plan.root)
                or staged != plan.root / "payload" / EXE_NAME
                or type(plan.parent_pid) is not int or plan.parent_pid <= 0):
            raise ValueError
        for digest in (plan.original_sha256, plan.new_sha256):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError
        if SemVersion.parse(plan.version) < SemVersion.parse(plan.previous_version):
            raise ValueError
        return plan
    except (ValueError, TypeError):
        raise UpdateError("The update plan is malformed or points to an unexpected executable.") from None


def prepare_plan(target: Path, staged_exe: Path, version: str, parent_pid: int, cancel: threading.Event) -> UpdatePlan:
    target = target.resolve()
    root = staged_exe.parent.parent
    owner = validate_stage(root)
    if target.name.lower() != EXE_NAME.lower():
        raise UpdateError(f"Automatic installation requires the executable to be named {EXE_NAME}.")
    if validate_executable(target) != validate_executable(staged_exe):
        raise UpdateError("This update targets a different Windows processor architecture.")
    # Probe the actual install directory before asking the running app to exit.
    with tempfile.TemporaryFile(prefix=".ytd-update-probe-", dir=target.parent) as probe:
        probe.write(b"writable")
    check_cancelled(cancel)
    original = file_hash(target, cancel)
    plan = UpdatePlan(REPOSITORY, owner["token"], version, APP_VERSION, str(target), str(staged_exe),
                      original, file_hash(staged_exe, cancel), parent_pid)
    shutil.copyfile(target, root / "update-helper.exe")
    check_cancelled(cancel)
    if file_hash(root / "update-helper.exe", cancel) != original:
        raise UpdateError("The updater helper could not be copied correctly.")
    atomic_json(root / "plan.json", asdict(plan))
    load_plan(root / "plan.json", plan.token)
    return plan


def independent_environment() -> dict:
    # A copied helper/new app must outlive the old one-file _MEI extraction.
    return os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1", "PYTHONIOENCODING": "utf-8"}


def launch_executable(executable: Path, arguments: list[str]) -> subprocess.Popen:
    return subprocess.Popen([str(executable), *arguments], cwd=executable.parent,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=independent_environment(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


class WindowsProcess:
    """Hold the exact process handle, avoiding PID-reuse races during an update."""
    def __init__(self, pid: int, expected_image: Path):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise UpdateError("The application process could not be verified. Please retry the update.")
        buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(length)) or Path(buffer.value).resolve() != expected_image.resolve():
            self.close()
            raise UpdateError("The updater was given an unexpected application process.")

    def running(self) -> bool:
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise UpdateError("The application process could not be monitored.")
        return result == 258

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WindowsInstallLock:
    """Only one helper may update a particular installation in this session."""
    def __init__(self, target: Path):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        key = hashlib.sha256(str(target.resolve()).casefold().encode("utf-8")).hexdigest()
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\SimpleYTDownloader-update-" + key)
        if not self.handle:
            raise UpdateError("Windows could not reserve this installation for an update.")
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 0x80):  # acquired, or abandoned by a previous helper
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise UpdateError("Another instance is already updating this installation. Please try again later.")

    def close(self):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def replace_with_retry(source: Path, destination: Path, timeout=8) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(destination)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.15)


def status(plan: UpdatePlan, state: str, message="") -> None:
    atomic_json(plan.root / "status.json", {"token": plan.token, "state": state, "message": message[:500], "helper_pid": os.getpid()})


def launch_and_confirm(plan: UpdatePlan, rollback=False, timeout=35) -> None:
    marker = plan.root / ("rollback-ok.json" if rollback else "launch-ok.json")
    marker.unlink(missing_ok=True)
    flag = "--update-rollback" if rollback else "--update-startup"
    process = launch_executable(Path(plan.target), [flag, str(plan.root / "plan.json"), "--update-token", plan.token])
    tree = ProcessTree(process)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.is_file():
                acknowledgement = read_json(marker)
                expected = plan.previous_version if rollback else plan.version
                if acknowledgement.get("token") == plan.token and acknowledgement.get("version") == expected:
                    with_process = WindowsProcess(acknowledgement["pid"], Path(plan.target))
                    try:
                        if with_process.running():
                            tree.release()
                            return
                    finally:
                        with_process.close()
            if process.poll() is not None:
                break
            time.sleep(0.1)
        raise UpdateError("The replacement application did not confirm a successful startup.")
    finally:
        tree.close()
        if not tree.detached:
            process.wait(timeout=8)
            # Windows can keep an exited image locked while its process handle
            # survives in this exception traceback. Close it before rollback.
            if os.name == "nt":
                process._handle.Close()


def replace_and_launch(plan: UpdatePlan, launch=launch_and_confirm) -> str:
    """Only call after the original process has exited and handoff was committed."""
    target = Path(plan.target)
    backed_up = False
    try:
        if is_link(target) or file_hash(target) != plan.original_sha256:
            raise UpdateError("The installed executable changed while the update was being prepared.")
        if file_hash(Path(plan.staged_exe)) != plan.new_sha256:
            raise UpdateError("The staged executable failed its final integrity check.")
        if plan.backup.exists() or plan.incoming.exists() or plan.failed.exists():
            raise UpdateError("This update transaction already has installation files.")
        # Copy onto the destination volume first; do not alter the running file.
        with Path(plan.staged_exe).open("rb") as source, plan.incoming.open("xb") as destination:
            shutil.copyfileobj(source, destination, CHUNK_SIZE)
            destination.flush()
            os.fsync(destination.fileno())
        if file_hash(plan.incoming) != plan.new_sha256:
            raise UpdateError("The executable could not be copied to the installation folder.")
        status(plan, "replacing")
        replace_with_retry(target, plan.backup)
        backed_up = True
        replace_with_retry(plan.incoming, target)
        launch(plan, False)
        # Startup acknowledgement commits the installation. Bookkeeping must
        # never trigger rollback after the replacement is already running.
        try:
            status(plan, "success", "The new version started successfully.")
            plan.backup.unlink()
        except (OSError, UpdateError):
            pass  # Preserve recovery files if status cannot be recorded.
        return "success"
    except Exception as error:
        if backed_up:
            try:
                # Windows may permit renaming a recently exited image while
                # refusing to overwrite/delete it. Move the failed candidate
                # aside first so restoring the backup never overwrites it.
                if target.exists():
                    replace_with_retry(target, plan.failed)
                replace_with_retry(plan.backup, target)
            except OSError:
                status(plan, "restore_failed", f"Restore {plan.backup} to {target}. The original backup has been kept.")
                raise UpdateError(f"Windows blocked recovery. Your original executable is preserved at {plan.backup}.") from error
        if target.is_file() and file_hash(target) == plan.original_sha256:
            try:
                launch(plan, True)
            except Exception as launch_error:
                status(plan, "failed", "The previous version is preserved but could not be restarted. " + str(launch_error)[:300])
                raise UpdateError("The update failed. The previous executable is preserved; open it manually.") from launch_error
            try:
                status(plan, "rolled_back", "The update failed; the previous version was restored. " + str(error)[:300])
            except (OSError, UpdateError):
                pass
            return "rolled_back"
        status(plan, "failed", str(error))
        raise
    finally:
        try:
            plan.incoming.unlink(missing_ok=True)
        except OSError:
            pass


def helper_main(plan_path: str, token: str) -> int:
    plan = None
    parent = None
    installation = None
    committed = False
    try:
        if os.name != "nt" or not getattr(sys, "frozen", False):
            raise UpdateError("The updater helper must run from the packaged Windows application.")
        plan = load_plan(Path(plan_path), token)
        if Path(sys.executable).resolve() != plan.root / "update-helper.exe" or file_hash(Path(sys.executable)) != plan.original_sha256:
            raise UpdateError("The update helper is not the verified copy of the installed application.")
        installation = WindowsInstallLock(Path(plan.target))
        parent = WindowsProcess(plan.parent_pid, Path(plan.target))
        atomic_json(plan.root / "ready.json", {"token": token, "pid": os.getpid()})
        deadline = time.monotonic() + 120
        while parent.running():
            if (plan.root / "cancel.json").exists():
                return 2
            if time.monotonic() > deadline:
                raise UpdateError("The application did not exit in time. The installed executable was not changed.")
            time.sleep(0.1)
        commit = read_json(plan.root / "commit.json")
        if commit.get("token") != token:
            raise UpdateError("The application did not authorize the update handoff.")
        committed = True
        replace_and_launch(plan)
        return 0
    except Exception as error:
        if plan:
            try:
                previous = read_json(plan.root / "status.json") if (plan.root / "status.json").is_file() else {}
                if previous.get("state") != "restore_failed":
                    status(plan, "failed", str(error))
            except (OSError, ValueError, UpdateError):
                pass
        if committed and os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, str(error)[:800], "SimpleYTDownloader update", 0x10)
        return 1
    finally:
        if parent:
            parent.close()
        if installation:
            installation.close()


def acknowledge_startup(plan_path: str, token: str, rollback=False) -> str:
    """Called only after the restarted app has rendered its first Pygame frame."""
    plan = load_plan(Path(plan_path), token)
    expected = plan.previous_version if rollback else plan.version
    if not getattr(sys, "frozen", False) or Path(sys.executable).resolve() != Path(plan.target) or APP_VERSION != expected:
        raise UpdateError("The restarted application did not match the expected update version.")
    marker = "rollback-ok.json" if rollback else "launch-ok.json"
    atomic_json(plan.root / marker, {"token": token, "version": APP_VERSION, "pid": os.getpid()})

    def cleanup_after_helper():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                record = read_json(plan.root / "status.json")
                if record.get("token") == token and record.get("state") in ("success", "rolled_back"):
                    try:
                        helper = WindowsProcess(record["helper_pid"], plan.root / "update-helper.exe")
                    except UpdateError:
                        helper = None
                    if helper:
                        try:
                            while helper.running() and time.monotonic() < deadline:
                                time.sleep(0.15)
                        finally:
                            helper.close()
                    for _ in range(80):
                        try:
                            # Only transaction-specific files are eligible.
                            plan.backup.unlink(missing_ok=True)
                            plan.incoming.unlink(missing_ok=True)
                            plan.failed.unlink(missing_ok=True)
                            cleanup_stage(plan.root)
                            return
                        except (OSError, UpdateError):
                            time.sleep(0.25)
                    return
            except (OSError, ValueError, UpdateError):
                pass
            time.sleep(0.2)

    threading.Thread(target=cleanup_after_helper, name="update-cleanup", daemon=True).start()
    return "The update failed. Your previous version was restored." if rollback else f"Updated successfully to version {APP_VERSION}."
