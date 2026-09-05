"""Small, validated JSON preferences; no registry access."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from config.themes import THEMES

VIDEO_QUALITIES = ("Best available", "2160p", "1440p", "1080p", "720p", "480p", "360p")
AUDIO_QUALITIES = ("Best available", "320 kbps", "256 kbps", "192 kbps", "128 kbps")


@dataclass
class Settings:
    default_format: str = "MP4"
    video_quality: str = "Best available"
    audio_quality: str = "192 kbps"
    output_dir: str = str(Path.home() / "Downloads")
    auto_start: bool = False
    remember: bool = True
    ffmpeg_location: str = ""
    auto_check_updates: bool = True
    save_thumbnails: bool = False
    accent_theme: str = "Orange"


class SettingsManager:
    def __init__(self, directory: Path | None = None):
        base = os.getenv("YTD_CONFIG_DIR") or os.getenv("APPDATA")
        self.directory = directory or (Path(base) / "YouTube Downloader" if base else Path.home() / ".config" / "youtube-downloader")
        self.path = self.directory / "settings.json"
        self.warning = ""
        self.settings = self.load()

    def load(self) -> Settings:
        defaults = Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Settings must be an object")
            for field in fields(defaults):
                value = data.get(field.name)
                if type(value) is type(getattr(defaults, field.name)):
                    setattr(defaults, field.name, value)
            if not defaults.remember:
                return Settings()
            if defaults.default_format not in ("MP4", "MP3"):
                defaults.default_format = "MP4"
            if defaults.video_quality not in VIDEO_QUALITIES:
                defaults.video_quality = "Best available"
            if defaults.audio_quality not in AUDIO_QUALITIES:
                defaults.audio_quality = "192 kbps"
            if defaults.accent_theme not in THEMES:
                defaults.accent_theme = "Orange"
            if not defaults.output_dir.strip():
                defaults.output_dir = Settings().output_dir
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.warning = "Preferences could not be read. Default settings are in use."
        return defaults

    def save(self) -> None:
        if not self.settings.remember:
            self.path.unlink(missing_ok=True)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self.settings), indent=2), encoding="utf-8")
        temporary.replace(self.path)
