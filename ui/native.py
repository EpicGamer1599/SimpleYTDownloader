"""Native clipboard and a folder picker hosted outside the Pygame loop."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import uuid
from ctypes import wintypes


def clipboard_get() -> str:
    if os.name != "nt":
        import pygame
        if not pygame.scrap.get_init():
            pygame.scrap.init()
        return (pygame.scrap.get(pygame.SCRAP_TEXT) or b"").decode("utf-8", errors="replace").rstrip("\0")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    if not user.OpenClipboard(None):
        raise OSError("The clipboard is busy. Try Paste again.")
    try:
        handle = user.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()


def clipboard_set(text: str) -> None:
    if os.name != "nt":
        import pygame
        if not pygame.scrap.get_init():
            pygame.scrap.init()
        pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8") + b"\0")
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalFree.argtypes = [wintypes.HANDLE]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    data = (text + "\0").encode("utf-16-le")
    handle = kernel.GlobalAlloc(0x0002, len(data))
    if not handle:
        raise OSError("Could not copy text.")
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        kernel.GlobalFree(handle)
        raise OSError("Could not copy text.")
    ctypes.memmove(pointer, data, len(data))
    kernel.GlobalUnlock(handle)
    if not user.OpenClipboard(None):
        kernel.GlobalFree(handle)
        raise OSError("The clipboard is busy. Try again.")
    try:
        user.EmptyClipboard()
        if not user.SetClipboardData(13, handle):
            kernel.GlobalFree(handle)
            raise OSError("Could not copy text.")
    finally:
        user.CloseClipboard()


def open_folder(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def windows_folder_picker(initial: str, owner: int = 0) -> str:
    """Use IFileOpenDialog directly: no Tk or additional Windows package needed."""
    class GUID(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_ulong), ("data2", ctypes.c_ushort),
                    ("data3", ctypes.c_ushort), ("data4", ctypes.c_ubyte * 8)]

        @classmethod
        def parse(cls, value):
            return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    def method(pointer, index, *argtypes):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])

    def check(result):
        if result < 0:
            raise OSError(f"Windows folder dialog failed (0x{result & 0xffffffff:08x}).")

    ole = ctypes.OleDLL("ole32")
    shell = ctypes.OleDLL("shell32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    check(ole.CoInitializeEx(None, 2))
    dialog = ctypes.c_void_p()
    folder = ctypes.c_void_p()
    result = ctypes.c_void_p()
    display_name = ctypes.c_void_p()
    try:
        clsid = GUID.parse("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
        iid = GUID.parse("d57c7288-d4ad-4768-be02-9d969532d960")
        check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog)))
        options = wintypes.DWORD()
        check(method(dialog, 10, ctypes.POINTER(wintypes.DWORD))(dialog, ctypes.byref(options)))
        check(method(dialog, 9, wintypes.DWORD)(dialog, options.value | 0x20 | 0x40 | 0x800 | 0x8))
        check(method(dialog, 17, wintypes.LPCWSTR)(dialog, "YouTube Downloader — Choose a folder"))
        check(method(dialog, 18, wintypes.LPCWSTR)(dialog, "Select Folder"))
        if os.path.isdir(initial):
            shell_iid = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
            check(shell.SHCreateItemFromParsingName(initial, None, ctypes.byref(shell_iid), ctypes.byref(folder)))
            check(method(dialog, 12, ctypes.c_void_p)(dialog, folder))
        status = method(dialog, 3, wintypes.HWND)(dialog, owner)
        if status & 0xffffffff == 0x800704c7:
            return ""  # User cancelled.
        check(status)
        check(method(dialog, 20, ctypes.POINTER(ctypes.c_void_p))(dialog, ctypes.byref(result)))
        check(method(result, 5, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(result, 0x80058000, ctypes.byref(display_name)))
        return ctypes.wstring_at(display_name)
    finally:
        if display_name:
            ole.CoTaskMemFree(display_name)
        for pointer in (result, folder, dialog):
            if pointer:
                method(pointer, 2)(pointer)
        ole.CoUninitialize()


def folder_picker(initial: str, owner: int = 0) -> str:
    if os.name == "nt":
        return windows_folder_picker(initial, owner)
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(parent=root, initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
                                       title="YouTube Downloader — Choose a folder", mustexist=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        print(json.dumps({"path": folder_picker(sys.argv[1] if len(sys.argv) > 1 else "", int(sys.argv[2]) if len(sys.argv) > 2 else 0)}))
    except Exception:
        print(json.dumps({"error": "The folder picker could not open. You can type a full folder path instead."}))
