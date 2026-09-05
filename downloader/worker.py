"""Isolated yt-dlp worker. One JSON job on stdin; JSON events on stdout."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from downloader.dependencies import find_ffmpeg, find_js_runtime
from downloader.utils import friendly_error, publish_file
from downloader.thumbnails import download_thumbnail
from error_reporting import error_code


def build_options(job: dict, ffmpeg: str | None) -> dict:
    quality = job["quality"]
    cap = "" if quality == "Best available" or job["format"] == "MP3" else f"[height<={int(quality.removesuffix('p'))}]"
    options = {
        "noplaylist": True, "quiet": True, "no_warnings": False,
        "noprogress": True, "windowsfilenames": True, "restrictfilenames": False,
        "outtmpl": str(Path(job["work_dir"]) / "media.%(ext)s"),
        "socket_timeout": 15, "retries": 3, "fragment_retries": 3,
        "extractor_retries": 2, "continuedl": True, "overwrites": False,
        "concurrent_fragment_downloads": 1, "cachedir": False,
        "js_runtimes": find_js_runtime(),
    }
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    if job["format"] == "MP3":
        options.update(format="bestaudio/best", postprocessors=[{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3",
            "preferredquality": "0" if quality == "Best available" else quality.split()[0],
        }])
    elif ffmpeg:
        options.update({
            "format": f"bv{cap}[ext=mp4]+ba[ext=m4a]/b{cap}[ext=mp4]/bv{cap}+ba/b{cap}/wv+ba/w",
            "format_sort": ["res", "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        })
    else:
        options["format"] = f"b{cap}[ext=mp4]/w[ext=mp4]"
    return options


class Reporter:
    def __init__(self, emit):
        self.emit = emit
        self.last_update = 0.0
        self.streams = {}
        self.stream_count = 1

    def progress(self, data: dict) -> None:
        state = data.get("status")
        if state not in ("downloading", "finished"):
            return
        name = data.get("filename", "media")
        size = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        received = data.get("downloaded_bytes") or 0
        fraction = 1.0 if state == "finished" else min(received / size, 1.0) if size else 0.0
        self.streams[name] = (received, size, fraction)
        now = time.monotonic()
        if state != "finished" and now - self.last_update < 0.1:
            return
        self.last_update = now
        self.emit({"event": "progress", "downloaded_bytes": sum(v[0] for v in self.streams.values()),
                   "total_bytes": sum(v[1] for v in self.streams.values()),
                   "progress": min(0.98, sum(v[2] for v in self.streams.values()) / self.stream_count * 0.98),
                   "speed": data.get("speed") or 0, "eta": data.get("eta"),
                   "stage": "Preparing file" if state == "finished" else f"Downloading stream {len(self.streams)} of {self.stream_count}"})

    def postprocess(self, data: dict) -> None:
        self.emit({"event": "processing", "stage": "Converting / merging with FFmpeg", "speed": 0, "eta": None})


class QuietLogger:
    def __init__(self, emit):
        self.emit = emit

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        self.emit({"event": "warning", "warning": str(message)[:380]})

    def error(self, message):
        pass  # yt-dlp raises DownloadError; the top-level handler reports it once.


def execute(job: dict, emit) -> None:
    thumbnail = None
    thumbnail_warning = ""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError("yt-dlp is missing. Install the packages in requirements.txt, then restart the app.") from None
    ffmpeg = find_ffmpeg(job.get("ffmpeg_location", ""))
    if job["format"] == "MP3" and not ffmpeg:
        raise RuntimeError("MP3 conversion requires FFmpeg. Install FFmpeg or select its bin folder in Settings.")
    reporter = Reporter(emit)
    options = build_options(job, ffmpeg)
    options.update(progress_hooks=[reporter.progress], postprocessor_hooks=[reporter.postprocess], logger=QuietLogger(emit))
    emit({"event": "processing", "stage": "Fetching video details"})
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(job["url"], download=False)
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise ValueError("Choose a single YouTube video. Playlists are not supported.")
        if info.get("is_live"):
            raise ValueError("This stream is live now. Add it after the broadcast ends.")
        title = info.get("title") or "Untitled video"
        height = info.get("height")
        actual = f"{height}p" if height and job["format"] == "MP4" else job["quality"]
        reporter.stream_count = max(1, len(info.get("requested_formats") or []))
        emit({"event": "metadata", "title": title, "actual_quality": actual})
        ydl.process_info(info)
        if job.get("save_thumbnails", False):
            emit({"event": "processing", "stage": "Saving the video thumbnail", "speed": 0, "eta": None})
            try:
                thumbnail = download_thumbnail(ydl, info, Path(job["work_dir"]))
            except Exception:
                thumbnail_warning = "The media was downloaded, but its thumbnail could not be saved. It may be unavailable, unsupported, or temporarily unreachable."
    extension = job["format"].lower()
    source = Path(job["work_dir"]) / f"media.{extension}"
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f"No {extension.upper()} file was produced. Check the selected format and FFmpeg installation.")
    emit({"event": "processing", "stage": "Saving your file", "progress": 0.99})
    result = publish_file(source, Path(job["output_dir"]), title, extension)
    thumbnail_filename = ""
    if thumbnail:
        try:
            thumbnail_filename = str(publish_file(thumbnail, result.parent, result.stem, thumbnail.suffix[1:]))
        except (OSError, ValueError):
            thumbnail_warning = "The media was saved, but its thumbnail could not be copied to the output folder. Check folder permissions and disk space."
    if thumbnail_warning:
        emit({"event": "warning", "warning": thumbnail_warning, "warning_code": "SYTD-THUMBNAIL"})
    emit({"event": "completed", "filename": str(result), "thumbnail_filename": thumbnail_filename, "title": title, "progress": 1.0})


def main() -> int:
    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)

    try:
        job = json.loads(sys.stdin.readline())
        execute(job, emit)
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, (ImportError, RuntimeError)) else friendly_error(error)
        emit({"event": "error", "error": message[:380], "error_code": error_code(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
