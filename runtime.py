"""Entry-point and stream support for Python and PyInstaller windowed builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def helper_command(kind: str) -> list[str]:
    if kind not in ("download-worker", "folder-picker"):
        raise ValueError("Unknown application helper")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--" + kind]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    module = "downloader.worker" if kind == "download-worker" else "ui.native"
    return [str(executable), "-u", "-m", module]


def prepare_standard_streams() -> None:
    """Reopen redirected Win32 pipes that --windowed leaves as sys.stdout=None.

    Helpers still communicate over their parent's private pipes. A GUI launched
    without redirection uses the null device and never creates a console window.
    """
    for name, standard_id, mode in (("stdin", -10, "r"), ("stdout", -11, "w"), ("stderr", -12, "w")):
        if getattr(sys, name) is not None:
            continue
        stream = None
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                              ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.GetStdHandle(standard_id & 0xffffffff)
            if handle and kernel.GetFileType(handle):
                duplicate = wintypes.HANDLE()
                current = kernel.GetCurrentProcess()
                if kernel.DuplicateHandle(current, handle, current, ctypes.byref(duplicate), 0, False, 2):
                    try:
                        flags = os.O_BINARY | (os.O_RDONLY if mode == "r" else os.O_WRONLY)
                        descriptor = msvcrt.open_osfhandle(duplicate.value, flags)
                    except OSError:
                        kernel.CloseHandle(duplicate)
                    else:
                        stream = os.fdopen(descriptor, mode, encoding="utf-8", errors="replace", buffering=1)
        setattr(sys, name, stream or open(os.devnull, mode, encoding="utf-8"))
