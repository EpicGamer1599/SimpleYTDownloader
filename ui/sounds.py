"""Small cached effects with dedicated channels and optional audio support."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pygame

SOUND_FILES = {
    "toggle": ("on_off.mp3", "on_off.wav"),
    "finished": ("FinishDownload.mp3", "FinishDownload.wav"),
    "downloading": ("Downloading.wav", "Downloading.mp3"),
}


def sound_directory() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "soundeffects"


class SoundEffects:
    """Playback returns immediately; SDL mixes audio in its background thread."""

    def __init__(self, enabled=True, directory=None, mixer=None):
        self.enabled = bool(enabled)
        self.directory = Path(directory) if directory is not None else sound_directory()
        self.mixer = mixer if mixer is not None else pygame.mixer
        self.sounds = {}
        self.channels = {}
        self.problem = ""
        self.closed = False
        self._owns_mixer = False
        if self.enabled:
            self._load()

    def _load(self):
        self.problem = ""
        try:
            if not self.mixer.get_init():
                self.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                self._owns_mixer = True
            self.mixer.set_num_channels(max(3, self.mixer.get_num_channels()))
            self.mixer.set_reserved(3)
            self.channels = {name: self.mixer.Channel(index) for index, name in enumerate(SOUND_FILES)}
            for name, filenames in SOUND_FILES.items():
                try:
                    path = next((self.directory / filename for filename in filenames if (self.directory / filename).is_file()), None)
                    if path is None or path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError("Missing or oversized sound file")
                    self.sounds[name] = self.mixer.Sound(str(path))
                except (pygame.error, OSError, ValueError):
                    self.problem = "Some sound files are missing or unsupported."
        except (pygame.error, OSError, NotImplementedError):
            self._unavailable()

    def _unavailable(self):
        self.problem = "Audio is unavailable on this device."
        self.stop()
        self.channels.clear()
        self.sounds.clear()

    def _play(self, name):
        if self.closed or name not in self.sounds or name not in self.channels:
            return
        try:
            channel = self.channels[name]
            channel.set_volume(0.55)
            channel.play(self.sounds[name])
        except (pygame.error, OSError):
            self._unavailable()

    def toggle(self):
        if self.enabled:
            self._play("toggle")

    def finished(self):
        if self.enabled:
            self._play("finished")

    def set_enabled(self, enabled, feedback=True):
        enabled = bool(enabled)
        if self.closed or self.enabled == enabled:
            return
        previous = self.enabled
        self.enabled = enabled
        if enabled and not self.channels:
            self._load()
        if not enabled:
            self.stop()
        # Muting gives one final click; subsequent actions are silent.
        if feedback and (previous or enabled):
            self._play("toggle")

    def sync(self, downloading):
        if self.closed or "downloading" not in self.channels:
            return
        try:
            channel = self.channels["downloading"]
            if not self.enabled or not downloading:
                channel.stop()
            elif "downloading" in self.sounds:
                if not channel.get_busy():
                    channel.play(self.sounds["downloading"], loops=-1)
                # Leave the completion cue audible when the next item starts.
                channel.set_volume(0.07 if self.channels["finished"].get_busy() else 0.20)
        except (pygame.error, OSError):
            self._unavailable()

    def stop(self):
        for channel in self.channels.values():
            try:
                channel.stop()
            except (pygame.error, OSError):
                pass

    def close(self):
        if self.closed:
            return
        self.stop()
        self.closed = True
        self.channels.clear()
        self.sounds.clear()
        if self._owns_mixer:
            try:
                self.mixer.quit()
            except (pygame.error, OSError):
                pass


def smoke_test() -> int:
    """Opt-in packaged diagnostic; test runners choose SDL's dummy audio device."""
    from app_version import APP_VERSION
    effects = SoundEffects()
    try:
        effects.sync(True)
        loop_started = bool(effects.channels and effects.channels["downloading"].get_busy())
        effects.finished()
        finish_started = bool(effects.channels and effects.channels["finished"].get_busy())
        effects.set_enabled(False)
        loop_stopped = bool(effects.channels and not effects.channels["downloading"].get_busy())
        toggle_started = bool(effects.channels and effects.channels["toggle"].get_busy())
        result = {"version": APP_VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                  "loaded": {name: round(sound.get_length(), 3) for name, sound in effects.sounds.items()},
                  "loop_started": loop_started, "loop_stopped": loop_stopped,
                  "finish_started": finish_started, "toggle_started": toggle_started, "problem": effects.problem}
        print(json.dumps(result))
        return 0 if len(effects.sounds) == 3 and all((loop_started, finish_started, loop_stopped, toggle_started)) else 1
    finally:
        effects.close()
