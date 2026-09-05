"""Discover tools on PATH, in portable folders, and common Windows installs."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_ffmpeg(configured: str = "") -> str | None:
    filename = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        candidate = path / filename if path.is_dir() else path
        return str(candidate) if candidate.is_file() else None
    candidates = [ROOT / "tools" / "ffmpeg" / "bin" / filename, ROOT / "tools" / filename]
    if os.getenv("FFMPEG_LOCATION"):
        path = Path(os.environ["FFMPEG_LOCATION"])
        candidates.insert(0, path / filename if path.is_dir() else path)
    found = shutil.which("ffmpeg")
    if found:
        candidates.insert(0, Path(found))
    if os.name == "nt":
        local = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        candidates.extend((local / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"))
        candidates.append(Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Kdenlive" / "bin" / filename)
    return next((str(p) for p in candidates if p.is_file()), None)


def find_js_runtime() -> dict:
    for name in ("deno", "node"):
        candidate = shutil.which(name)
        if not candidate:
            if name == "deno":
                path = Path.home() / ".deno" / "bin" / ("deno.exe" if os.name == "nt" else "deno")
            else:
                path = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "nodejs" / "node.exe"
            candidate = str(path) if path.is_file() else None
        if candidate:
            return {name: {"path": candidate}}
    return {}


def dependency_status(configured: str = "") -> dict:
    ffmpeg = find_ffmpeg(configured)
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe") if ffmpeg else None
    return {
        "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
        "ffmpeg": ffmpeg,
        "ffprobe": bool(ffprobe and ffprobe.is_file()),
        "javascript": find_js_runtime(),
    }
