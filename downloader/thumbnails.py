"""Download one optional image using the worker's existing yt-dlp connection."""
from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import urlsplit

MAX_THUMBNAIL_BYTES = 12 * 1024 * 1024


def image_extension(data):
    if len(data) >= 16 and data.startswith(b"\xff\xd8\xff") and data.rstrip().endswith(b"\xff\xd9"):
        return "jpg"
    if len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[-8:-4] == b"IEND":
        return "png"
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and struct.unpack_from("<I", data, 4)[0] + 8 == len(data):
        return "webp"
    raise ValueError("The thumbnail response was not a supported JPEG, PNG, or WebP image.")


def download_thumbnail(ydl, info, work_dir: Path):
    from yt_dlp.networking import Request
    candidates = list(reversed(info.get("thumbnails") or []))
    if info.get("thumbnail"):
        candidates.insert(0, {"url": info["thumbnail"]})
    urls = []
    for candidate in candidates:
        url = candidate.get("url") if isinstance(candidate, dict) else None
        if not isinstance(url, str) or url in urls:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
                urls.append(url)
        except ValueError:
            continue
    if not urls:
        raise ValueError("No thumbnail is available for this video.")
    last_error = None
    for url in urls[:3]:
        try:
            with ydl.urlopen(Request(url, headers=info.get("http_headers") or {})) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_THUMBNAIL_BYTES:
                    raise ValueError("The thumbnail exceeds the 12 MB limit.")
                data = bytearray()
                while chunk := response.read(min(65536, MAX_THUMBNAIL_BYTES + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > MAX_THUMBNAIL_BYTES:
                        raise ValueError("The thumbnail exceeds the 12 MB limit.")
                if length and len(data) != int(length):
                    raise ValueError("The thumbnail download was incomplete.")
            suffix = image_extension(data)
            target = work_dir / ("thumbnail." + suffix)
            target.write_bytes(data)
            return target
        except Exception as error:
            response = getattr(error, "response", None)
            if response is not None:
                response.close()
            last_error = error
    raise RuntimeError("The thumbnail could not be downloaded. " + str(last_error)) from last_error
