"""Validation and Windows-safe, readable filenames."""
from __future__ import annotations

import errno
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def sanitize_filename(title: str | None, max_length: int = 180) -> str:
    """Limit UTF-16 code units, keeping punctuation and normal spaces intact."""
    if max_length < 1:
        raise ValueError("The output path is too long. Choose a shorter folder path.")
    name = unicodedata.normalize("NFC", str(title or ""))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip().rstrip(". ")
    name = "".join(c for c in name if not 0xD800 <= ord(c) <= 0xDFFF)
    if not name:
        name = "Untitled video"
    if RESERVED.match(name.split(".", 1)[0].rstrip(" ")):
        name = "_" + name
    while utf16_length(name) > max_length:
        name = name[:-1]
    name = name.rstrip(". ") or "_"
    if RESERVED.match(name.split(".", 1)[0].rstrip(" ")):
        name = "_" + name
        while utf16_length(name) > max_length:
            name = name[:-1]
    return name


def normalize_youtube_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Paste a YouTube video link first.")
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError
        parts = parsed.path.strip("/").split("/")
        if host in ("youtu.be", "www.youtu.be") and len(parts) == 1:
            video_id = parts[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"):
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in ("shorts", "live", "embed", "v"):
                video_id = parts[1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError:
        raise ValueError("Enter a valid YouTube video link (watch, youtu.be, Shorts, or live). Playlists are not supported.") from None
    return f"https://www.youtube.com/watch?v={video_id}"


def output_directory(value: str, create: bool = False) -> Path:
    if not value.strip():
        raise ValueError("Choose an output folder first.")
    path = Path(os.path.expandvars(value.strip())).expanduser()
    if not path.is_absolute():
        raise ValueError("Use a full output folder path, or choose Browse.")
    if os.name == "nt" and any(re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) or RESERVED.match(part.split(".", 1)[0]) for part in path.parts[1:]):
        raise ValueError("The output folder contains a name Windows does not support.")
    if utf16_length(str(path)) > 190:
        raise ValueError("The output path is too long. Choose a shorter folder path.")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_dir():
        raise ValueError("The output location is a file. Choose a folder.")
    return path


def publish_file(source: Path, output: Path, title: str, extension: str) -> Path:
    """Atomically publish on the same volume without replacing existing files."""
    for index in range(10000):
        suffix = f" ({index + 1})" if index else ""
        budget = min(180, 240 - utf16_length(str(output)) - len(extension) - len(suffix) - 2)
        name = sanitize_filename(title, budget) + suffix + "." + extension
        target = output / name
        try:
            if os.name == "nt":
                source.rename(target)  # Windows rename refuses to replace an existing file.
            else:
                os.link(source, target)
                source.unlink()
            return target
        except FileExistsError:
            continue
    raise OSError("Too many files share this title. Choose another output folder.")


def friendly_error(error: Exception | str) -> str:
    if isinstance(error, OSError):
        if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
            return "The disk is full. Free up space or select a different output folder."
        if isinstance(error, PermissionError):
            return "Access denied. Choose a folder you can write to and check that the file is not in use."
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    lower = message.lower()
    for phrases, explanation in (
        (("no space left", "disk full", "not enough space"), "The disk is full. Free up space or select a different output folder."),
        (("permission denied", "access is denied"), "Access denied. Choose a writable output folder and close any file using this name."),
        (("private video",), "This video is private. Choose a publicly available video."),
        (("video unavailable", "has been removed", "not available", "copyright"), "This video is unavailable, removed, or restricted in your region."),
        (("sign in", "confirm your age", "bot", "po token"), "YouTube requires verification for this video or connection. Update yt-dlp, try another public video, or try again later."),
        (("429", "too many requests"), "YouTube is limiting requests. Pause the queue and try again later."),
        (("403", "forbidden", "signature", "nsig"), "YouTube rejected the request. Update yt-dlp and check the JavaScript runtime in Settings, then retry."),
        (("timed out", "timeout", "connection", "name resolution", "getaddrinfo", "unable to download webpage"), "Could not reach YouTube. Check your connection and try again."),
        (("requested format", "no video formats"), "This quality is unavailable. Try Best available; some formats may require FFmpeg or a JavaScript runtime."),
        (("ffmpeg", "ffprobe"), "FFmpeg could not process this download. Check FFmpeg in Settings and confirm there is enough free disk space."),
    ):
        if any(phrase in lower for phrase in phrases):
            return explanation
    message = re.sub(r"^ERROR:\s*", "", message).strip()
    return message[:380] or "The download failed. Check your connection and try again."
