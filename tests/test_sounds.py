"""Sound routing, missing-device recovery, and decoding of the supplied assets."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from config.settings import SettingsManager
from ui.sounds import SOUND_FILES, SoundEffects, sound_directory


class FakeChannel:
    def __init__(self):
        self.playing = False
        self.plays = []
        self.volume = 0
        self.fail = False

    def play(self, sound, loops=0):
        if self.fail:
            raise pygame.error("device disconnected")
        self.playing = True
        self.plays.append((sound, loops))

    def get_busy(self):
        return self.playing

    def stop(self):
        self.playing = False

    def set_volume(self, value):
        self.volume = value


class FakeMixer:
    def __init__(self):
        self.initialized = False
        self.channels = [FakeChannel() for _ in range(3)]
        self.init_calls = 0

    def get_init(self):
        return self.initialized

    def init(self, **kwargs):
        self.init_calls += 1
        self.initialized = True

    def get_num_channels(self):
        return 3

    def set_num_channels(self, count):
        pass

    def set_reserved(self, count):
        pass

    def Channel(self, index):
        return self.channels[index]

    def Sound(self, filename):
        return Path(filename).name

    def quit(self):
        self.initialized = False


class SoundTests(unittest.TestCase):
    def test_loop_starts_once_and_stops_without_cutting_other_channels(self):
        effects = SoundEffects(mixer=FakeMixer())
        for _ in range(60):
            effects.sync(True)
        channel = effects.channels["downloading"]
        self.assertEqual(channel.plays, [("Downloading.wav", -1)])
        effects.finished()
        effects.toggle()
        effects.sync(True)
        self.assertEqual(channel.volume, 0.07)
        effects.sync(False)
        self.assertFalse(channel.get_busy())
        self.assertTrue(effects.channels["finished"].get_busy())
        self.assertTrue(effects.channels["toggle"].get_busy())
        effects.close()

    def test_muting_stops_activity_with_one_final_click_and_unmute_resumes(self):
        effects = SoundEffects(mixer=FakeMixer())
        effects.sync(True)
        effects.finished()
        toggle = effects.channels["toggle"]
        effects.set_enabled(False)
        self.assertEqual(len(toggle.plays), 1)
        self.assertFalse(effects.channels["downloading"].get_busy())
        self.assertFalse(effects.channels["finished"].get_busy())
        effects.toggle()
        effects.finished()
        effects.sync(True)
        self.assertEqual(len(toggle.plays), 1)
        self.assertFalse(effects.channels["downloading"].get_busy())
        effects.set_enabled(True)
        effects.sync(True)
        self.assertEqual(len(toggle.plays), 2)
        self.assertTrue(effects.channels["downloading"].get_busy())
        effects.close()

    def test_disabled_startup_does_not_open_audio_and_close_is_idempotent(self):
        mixer = FakeMixer()
        effects = SoundEffects(False, mixer=mixer)
        effects.sync(True)
        effects.finished()
        self.assertEqual(mixer.init_calls, 0)
        effects.set_enabled(True)
        self.assertEqual(mixer.init_calls, 1)
        effects.sync(True)
        effects.close()
        effects.close()
        effects.set_enabled(True)
        effects.sync(True)
        self.assertFalse(any(channel.get_busy() for channel in mixer.channels))
        self.assertFalse(mixer.initialized)

    def test_no_device_missing_files_and_playback_failure_are_nonfatal(self):
        mixer = FakeMixer()
        with patch.object(mixer, "init", side_effect=pygame.error("no audio device")):
            effects = SoundEffects(mixer=mixer)
        self.assertIn("unavailable", effects.problem)
        effects.sync(True)
        effects.toggle()
        effects.finished()
        effects.close()
        with tempfile.TemporaryDirectory() as folder:
            effects = SoundEffects(directory=folder, mixer=FakeMixer())
            self.assertEqual(effects.sounds, {})
            self.assertIn("missing", effects.problem)
            effects.sync(True)
            effects.finished()
            effects.close()
        effects = SoundEffects(mixer=FakeMixer())
        effects.channels["downloading"].fail = True
        effects.sync(True)
        self.assertIn("unavailable", effects.problem)
        effects.close()

    def test_real_supplied_mp3_and_wav_assets_decode_and_play(self):
        effects = SoundEffects()
        try:
            self.assertEqual(set(effects.sounds), set(SOUND_FILES), effects.problem)
            self.assertTrue(all(sound.get_length() > 0.1 for sound in effects.sounds.values()))
            effects.sync(True)
            effects.finished()
            effects.toggle()
            self.assertTrue(all(channel.get_busy() for channel in effects.channels.values()))
            effects.stop()
            self.assertFalse(any(channel.get_busy() for channel in effects.channels.values()))
        finally:
            effects.close()

    def test_old_settings_enable_sounds_and_saved_mute_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "settings.json").write_text('{"accent_theme":"Blue"}')
            settings = SettingsManager(path)
            self.assertTrue(settings.settings.sound_effects)
            settings.settings.sound_effects = False
            settings.save()
            self.assertFalse(SettingsManager(path).settings.sound_effects)
            self.assertEqual(SettingsManager(path).settings.accent_theme, "Blue")

    def test_frozen_asset_lookup_uses_bundle_instead_of_install_directory(self):
        with patch("ui.sounds.sys._MEIPASS", "temporary-bundle", create=True):
            self.assertEqual(sound_directory(), Path("temporary-bundle") / "soundeffects")
