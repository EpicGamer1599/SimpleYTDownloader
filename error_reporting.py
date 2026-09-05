"""Stable support codes and bounded, redacted GitHub issue drafts.

Reports are opened only by an explicit UI action; nothing is submitted here.
"""
from __future__ import annotations

import errno
import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from app_version import APP_VERSION

ISSUES_URL = "https://github.com/EpicGamer1599/SimpleYTDownloader/issues"


def error_code(error, context=""):
    text = (str(error) + " " + context).lower()
    if isinstance(error, PermissionError):
        return "SYTD-ACCESS"
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "SYTD-NETWORK"
    if isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112):
        return "SYTD-DISK-FULL"
    rules = (
        (("disk is full", "disk full", "no space left"), "SYTD-DISK-FULL"),
        (("access denied", "access is denied", "permission", "denied access", "preferences could not be saved"), "SYTD-ACCESS"),
        (("thumbnail",), "SYTD-THUMBNAIL"),
        (("yt-dlp is missing",), "SYTD-DEPENDENCY"),
        (("ffmpeg", "ffprobe"), "SYTD-FFMPEG"),
        (("private video", "video is private"), "SYTD-VIDEO-PRIVATE"),
        (("unavailable", "removed", "restricted"), "SYTD-UNAVAILABLE"),
        (("verification", "sign in", "bot", "403", "forbidden"), "SYTD-VERIFICATION"),
        (("limiting", "429", "too many requests"), "SYTD-RATE-LIMIT"),
        (("internet", "connection", "timed out", "timeout", "getaddrinfo", "name resolution", "network"), "SYTD-NETWORK"),
        (("github", "update", "sha-256", "release zip"), "SYTD-UPDATE"),
        (("valid youtube", "paste a youtube", "single youtube", "playlist", "live now"), "SYTD-LINK"),
        (("output folder", "output path", "output location", "folder picker"), "SYTD-FOLDER"),
        (("quality", "format"), "SYTD-FORMAT"),
        (("clipboard",), "SYTD-CLIPBOARD"),
        (("preferences", "settings"), "SYTD-SETTINGS"),
        (("worker stopped",), "SYTD-WORKER"),
    )
    for phrases, code in rules:
        if any(phrase in text for phrase in phrases):
            return code
    return "SYTD-UNEXPECTED"


def redact(value):
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(value))
    text = "".join(c for c in text if c.isprintable() or c == "\n")
    for private in (str(Path.home()), os.getenv("APPDATA", ""), os.getenv("LOCALAPPDATA", "")):
        if private:
            text = re.sub(re.escape(private), "[local folder]", text, flags=re.I)
    text = re.sub(r"https?://\S+", "[URL omitted]", text, flags=re.I)
    text = re.sub(r"(?i)\b(?:token|password|authorization|cookie|api[_-]?key)\s*[:=]\s*[^\n,;]+", "[credential omitted]", text)
    text = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\n\r\"<>]+", "[local path]", text)
    text = re.sub(r"(?<!\w)/(?:home|Users|tmp|var)/[^\n\r\"<>]+", "[local path]", text)
    text = re.sub(r"\[local folder\][^\n\r\"<>]*", "[local path]", text)
    return text.replace("```", "'''").strip()[:650]


@dataclass(frozen=True)
class ErrorReport:
    code: str
    message: str
    context: str

    @classmethod
    def create(cls, message, context="Application", code=""):
        if not re.fullmatch(r"SYTD-[A-Z0-9-]{2,40}", code or ""):
            code = error_code(message, context)
        return cls(code, redact(message), redact(context)[:80])

    def body(self, message=None):
        mode = "Windows EXE" if getattr(sys, "frozen", False) else "Source checkout"
        return (f"### Error report\n"
                f"- App: SimpleYTDownloader {APP_VERSION}\n"
                f"- Error code: {self.code}\n"
                f"- Action: {self.context}\n"
                f"- OS: {platform.system()} {platform.release()}\n"
                f"- Run mode: {mode}\n\n"
                f"### Error message\n```text\n{self.message if message is None else message}\n```\n\n"
                "### Steps to reproduce\n1. \n2. \n\n"
                "### Expected behaviour\nDescribe what should have happened.\n\n"
                "### Extra details\nAdd relevant settings or a screenshot, after checking for personal information.\n")

    def issue_url(self):
        message = self.message
        while True:
            url = ISSUES_URL + "/new?" + urlencode({"title": f"[{self.code}] {self.context}", "body": self.body(message)})
            if len(url) <= 1900:
                return url
            if not message:
                return ISSUES_URL + "/new?" + urlencode({"title": self.code, "body": "App version: " + APP_VERSION + "\nError code: " + self.code})
            message = message[:max(0, len(message) - 40)]


def show_fatal_error(error):
    """Last-resort support prompt when the Pygame loop cannot be started/used."""
    report = ErrorReport.create(error, "Application startup or unexpected failure")
    if os.name == "nt":
        import ctypes
        import webbrowser
        answer = ctypes.windll.user32.MessageBoxW(None,
            f"SimpleYTDownloader could not continue.\n\n{report.code}\n{report.message}\n\nOpen a prefilled GitHub issue to report this?",
            "SimpleYTDownloader", 0x14)  # MB_YESNO | MB_ICONERROR
        if answer == 6:
            try:
                webbrowser.open(report.issue_url(), new=2)
            except Exception:
                pass
    else:
        print(report.body(), file=sys.stderr)
