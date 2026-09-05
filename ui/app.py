"""Responsive desktop pages and the Pygame application loop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from config.settings import AUDIO_QUALITIES, VIDEO_QUALITIES, VERSION, SettingsManager
from downloader.dependencies import ROOT, dependency_status
from downloader.manager import DownloadManager
from downloader.process import ProcessTree
from downloader.utils import output_directory
from ui.native import clipboard_get, clipboard_set, open_folder
from ui.widgets import ACCENT, BG, BORDER, FAINT, FIELD, GREEN, MUTED, PANEL, RED, SIDEBAR, TEXT, Painter, TextInput, blend


@dataclass
class Hit:
    key: str
    rect: pygame.Rect
    action: object = None
    input: TextInput | None = None


def bytes_label(value):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} PB"


def eta_label(value):
    if value is None:
        return "--:--"
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class App:
    def __init__(self, settings_manager=None, manager=None, size=(1180, 820)):
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption("YouTube Downloader")
        self.surface = pygame.display.set_mode(size, pygame.RESIZABLE)
        self.p = Painter(self.surface)
        icon_surface = pygame.Surface((48, 48), pygame.SRCALPHA)
        icon_painter = Painter(icon_surface)
        icon_painter.panel(pygame.Rect(0, 0, 48, 48), PANEL, None, 12)
        icon_painter.icon("download", (24, 24), ACCENT, 28)
        pygame.display.set_icon(icon_surface)
        pygame.key.set_repeat(400, 35)
        pygame.key.start_text_input()
        self.settings_manager = settings_manager or SettingsManager()
        self.settings = self.settings_manager.settings
        self.manager = manager or DownloadManager(self.settings.auto_start, self.settings.ffmpeg_location)
        self.dependencies = dependency_status(self.settings.ffmpeg_location)
        self.page = "Download"
        self.format = self.settings.default_format
        self.video_quality = self.settings.video_quality
        self.audio_quality = self.settings.audio_quality
        self.inputs = {
            "url": TextInput(placeholder="Paste YouTube link here"),
            "output": TextInput(self.settings.output_dir, "Choose an output folder"),
            "settings_output": TextInput(self.settings.output_dir),
            "ffmpeg": TextInput(self.settings.ffmpeg_location, "Auto-detect FFmpeg"),
        }
        self.focus = "url"
        self.hits: list[Hit] = []
        self.pressed = None
        self.hover = {}
        self.mouse = (0, 0)
        self.scroll = {name: 0 for name in ("Download", "Queue", "Settings", "About")}
        self.max_scroll = 0
        self.clock = pygame.time.Clock()
        self.dt = 1 / 60
        self.notice = None
        self.picker = None
        self.picker_tree = None
        self.picker_target = "output"
        self.closing = False
        self.running = True
        self.started_once = False
        self.fade = 0
        self.last_frame = time.monotonic()
        if self.settings_manager.warning:
            self.notify(self.settings_manager.warning, error=True)
        self.draw()

    def notify(self, message, error=False):
        self.notice = (message, error, time.monotonic() + (3600 if error else 4))

    def save_preferences(self):
        try:
            self.settings_manager.save()
            return True
        except OSError:
            self.notify("Preferences could not be saved. Check access to the configuration folder.", True)
            return False

    def navigate(self, page):
        self.page = page
        self.focus = "url" if page == "Download" else None
        self.pressed = None
        self.fade = 0.13

    def set_format(self, value):
        self.format = value

    def set_quality(self, value):
        if self.format == "MP4":
            self.video_quality = value
        else:
            self.audio_quality = value

    def paste(self):
        try:
            value = clipboard_get().strip()
            if value:
                self.inputs["url"].set(value)
                self.focus = "url"
            else:
                self.notify("The clipboard has no text to paste.")
        except OSError as error:
            self.notify(str(error), True)

    def add_to_queue(self):
        try:
            quality = self.video_quality if self.format == "MP4" else self.audio_quality
            self.manager.add(self.inputs["url"].text, self.format, quality, self.inputs["output"].text)
            self.settings.output_dir = self.inputs["output"].text.strip()
            self.inputs["settings_output"].set(self.settings.output_dir)
            saved = self.save_preferences()
            self.inputs["url"].set("")
            self.focus = "url"
            if saved:
                self.notify("Added to your queue. " + ("Download starting." if self.manager.running else "Start it from the Queue page."))
            else:
                self.notify("Added to queue, but preferences could not be saved. Check access to the configuration folder.", True)
        except (OSError, ValueError) as error:
            self.notify(str(error), True)

    def browse(self, target):
        if self.picker:
            return
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        try:
            owner = pygame.display.get_wm_info().get("window", 0)
            self.picker = subprocess.Popen([str(executable), "-m", "ui.native", self.inputs[target].text, str(owner)], cwd=ROOT,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.picker_tree = ProcessTree(self.picker)
            self.picker_target = target
        except OSError:
            self.notify("The folder picker could not open. Type the full folder path instead.", True)

    def poll_picker(self):
        if self.picker and self.picker.poll() is not None:
            output, _ = self.picker.communicate()
            if self.picker_tree:
                self.picker_tree.close()
                self.picker_tree = None
            self.picker = None
            try:
                result = json.loads(output)
                if result.get("error"):
                    self.notify(result["error"], True)
                elif result.get("path"):
                    self.inputs[self.picker_target].set(str(Path(result["path"])))
                    if self.picker_target == "output":
                        self.settings.output_dir = self.inputs["output"].text
                        self.inputs["settings_output"].set(self.settings.output_dir)
                        self.save_preferences()
            except ValueError:
                self.notify("The folder picker closed unexpectedly. Type a folder path or try again.", True)

    def setting(self, key, value):
        setattr(self.settings, key, value)
        if key == "auto_start":
            self.manager.auto_start = value
        self.save_preferences()

    def save_settings(self):
        try:
            output = output_directory(self.inputs["settings_output"].text)
            self.settings.output_dir = str(output)
            self.settings.ffmpeg_location = self.inputs["ffmpeg"].text.strip()
            self.inputs["output"].set(str(output))
            self.manager.ffmpeg_location = self.settings.ffmpeg_location
            self.dependencies = dependency_status(self.settings.ffmpeg_location)
            if self.save_preferences():
                self.notify("Preferences applied." if not self.settings.remember else "Preferences saved.")
        except (OSError, ValueError) as error:
            self.notify(str(error), True)

    def start_queue(self):
        self.started_once = True
        self.manager.start()

    def open_output(self, path):
        try:
            open_folder(path)
        except OSError:
            self.notify("This folder could not be opened. Check that it still exists.", True)

    def copy_error(self, error):
        try:
            clipboard_set(error)
            self.notify("Error copied to clipboard.")
        except OSError as exc:
            self.notify(str(exc), True)

    def register(self, key, rect, action=None, input=None):
        clipped = pygame.Rect(rect).clip(self.surface.get_clip())
        if clipped.w > 0 and clipped.h > 0 and not self.closing:
            self.hits.append(Hit(key, clipped, action, input))

    def button(self, key, label, rect, action, *, primary=False, selected=False, icon=None, enabled=True, quiet=False):
        rect = pygame.Rect(rect)
        over = enabled and rect.collidepoint(self.mouse) and self.surface.get_clip().collidepoint(self.mouse)
        amount = self.hover.get(key, 0)
        amount += ((1 if over else 0) - amount) * min(1, self.dt * 16)
        self.hover[key] = amount
        base = TEXT if primary else (48, 42, 40) if selected else FIELD if not quiet else SIDEBAR
        color = blend(base, (255, 255, 255) if primary else (57, 61, 69), amount * (0.15 if primary else 0.8))
        if self.pressed == key:
            color = blend(color, (0, 0, 0), 0.15)
        text_color = BG if primary else ACCENT if selected else TEXT
        if not enabled:
            color, text_color = FIELD, FAINT
        border = ACCENT if selected or self.focus == key else BORDER if not primary else None
        self.p.panel(rect, color, border, 9)
        font = self.p.font(14, True)
        total = font.size(label)[0] + (26 if icon else 0)
        left = rect.centerx - total // 2
        if icon:
            self.p.icon(icon, (left + 9, rect.centery), text_color, 17)
            left += 26
        self.p.text(label, (left, rect.centery - font.get_height() // 2), 14, text_color, True, rect.w - 18)
        if enabled:
            self.register(key, rect, action)

    def input(self, key, rect):
        control = self.inputs[key]
        control.draw(self.p, pygame.Rect(rect), self.focus == key)
        self.register(key, rect, input=control)

    def choices(self, prefix, options, selected, x, y, width, action):
        initial = x
        for value in options:
            label = "Best available" if value == "Best available" else value
            w = max(67, self.p.font(14, True).size(label)[0] + 28)
            if x + w > initial + width and x != initial:
                x, y = initial, y + 44
            self.button(f"{prefix}:{value}", label, (x, y, w, 36), lambda v=value: action(v), selected=value == selected)
            x += w + 8
        return y + 36

    def choices_height(self, options, width):
        used, rows = 0, 1
        for value in options:
            chip = max(67, self.p.font(14, True).size(value)[0] + 28)
            if used and used + chip > width:
                rows += 1
                used = 0
            used += chip + 8
        return 36 + (rows - 1) * 44

    def folder_field(self, key, x, y, width):
        self.input(key, (x, y, width - 110, 46))
        self.button("browse:" + key, "Browse…", (x + width - 100, y, 100, 46), lambda: self.browse(key), enabled=self.picker is None)

    def draw_sidebar(self, width, height, items):
        pygame.draw.rect(self.surface, SIDEBAR, (0, 0, width, height))
        pygame.draw.line(self.surface, BORDER, (width - 1, 0), (width - 1, height))
        self.p.panel(pygame.Rect(24 if width > 100 else 22, 29, 40, 40), (48, 35, 31), None, 12)
        self.p.icon("download", (44 if width > 100 else 42, 49), ACCENT, 23)
        if width > 100:
            self.p.text("YTD", (76, 28), 22, TEXT, True)
            self.p.text("DESKTOP", (77, 56), 10, MUTED, True)
            self.p.text("WORKSPACE", (24, 116), 10, FAINT, True)
        for index, name in enumerate(("Download", "Queue", "Settings", "About")):
            y = 148 + index * (58 if width > 100 else 76)
            rect = pygame.Rect(12, y, width - 24, 48 if width > 100 else 65)
            selected = self.page == name
            over = rect.collidepoint(self.mouse)
            target = 1 if over else 0
            key = "nav:" + name
            self.hover[key] = self.hover.get(key, 0) + (target - self.hover.get(key, 0)) * min(1, self.dt * 16)
            if selected or self.hover[key] > 0.02:
                self.p.panel(rect, blend((30, 33, 39) if selected else SIDEBAR, (40, 43, 49), self.hover[key]), None, 10)
            color = TEXT if selected else MUTED
            if selected:
                pygame.draw.rect(self.surface, ACCENT, (13, y + 16, 3, 17), border_radius=2)
            if width > 100:
                self.p.icon(name.lower(), (36, y + 24), ACCENT if selected else color, 19)
                self.p.text(name, (56, y + 13), 14, color, selected)
                if name == "Queue" and items:
                    self.p.text(str(len(items)), (width - 34, y + 14), 12, MUTED)
            else:
                self.p.icon(name.lower(), (width // 2, y + 22), ACCENT if selected else color, 21)
                text_width = self.p.font(11).size(name)[0]
                self.p.text(name, (width // 2 - text_width // 2, y + 40), 11, color)
            if self.focus == key:
                pygame.draw.rect(self.surface, ACCENT, rect, 1, border_radius=10)
            self.register(key, rect, lambda value=name: self.navigate(value))
        self.p.text("v" + VERSION, (24, height - 40), 11, FAINT)
        if width > 100:
            self.p.text("Made to keep.", (24, height - 65), 12, MUTED)

    def draw(self):
        self.surface.fill(BG)
        self.hits = []
        w, h = self.surface.get_size()
        sidebar = 180 if w >= 1050 else 84
        items = self.manager.snapshot()
        self.draw_sidebar(sidebar, h, items)
        area_width = w - sidebar
        content_width = min(880, area_width - 64)
        x = sidebar + (area_width - content_width) // 2
        self.p.text("WORKSPACE  /  " + self.page.upper(), (x, 32), 10, MUTED, True)
        title = {"Download": "YouTube Downloader", "Queue": "Your download queue", "Settings": "Make it yours", "About": "A little about this app"}[self.page]
        self.p.text(title, (x, 71), 32, TEXT, True, content_width)
        subtitle = {"Download": "Your next watch, saved for later.", "Queue": "One at a time. Everything in its place.",
                    "Settings": "Set your defaults. Keep your flow.", "About": "Simple tools. Thoughtfully put together."}[self.page]
        self.p.text(subtitle, (x, 120), 15, MUTED)
        if content_width > 650:
            status = "Downloading" if self.manager.active_id else "Ready when you are"
            self.p.panel(pygame.Rect(x + content_width - 168, 24, 168, 29), PANEL, BORDER, 14)
            pygame.draw.circle(self.surface, GREEN, (x + content_width - 151, 38), 3)
            self.p.text(status, (x + content_width - 140, 29), 11, MUTED)
        self.view = pygame.Rect(x - 2, 168, content_width + 4, max(100, h - 216))
        self.surface.set_clip(self.view)
        y = self.view.y + 10 - self.scroll[self.page]
        if self.page == "Download":
            bottom = self.draw_download(x, y, content_width, items)
        elif self.page == "Queue":
            bottom = self.draw_queue(x, y, content_width, items)
        elif self.page == "Settings":
            bottom = self.draw_settings(x, y, content_width)
        else:
            bottom = self.draw_about(x, y, content_width)
        self.surface.set_clip(None)
        content_height = bottom + self.scroll[self.page] - self.view.y + 20
        self.max_scroll = max(0, content_height - self.view.h)
        self.scroll[self.page] = min(self.scroll[self.page], self.max_scroll)
        if self.max_scroll:
            track = pygame.Rect(x + content_width + 12, self.view.y + 4, 4, self.view.h - 8)
            pygame.draw.rect(self.surface, FIELD, track, border_radius=2)
            thumb_height = max(32, int(track.h * self.view.h / content_height))
            thumb_y = track.y + (track.h - thumb_height) * self.scroll[self.page] / self.max_scroll
            pygame.draw.rect(self.surface, BORDER, (track.x, thumb_y, 4, thumb_height), border_radius=2)
        pygame.draw.line(self.surface, BORDER, (sidebar, h - 41), (w, h - 41))
        self.p.text("PYTHON + PYGAME", (x, h - 27), 10, FAINT, True)
        footer = "One download at a time" if self.page == "Queue" else "Ctrl+V to paste  ·  Enter to add" if self.page == "Download" else "YouTube Downloader  /  " + VERSION
        fw = self.p.font(11).size(footer)[0]
        self.p.text(footer, (x + content_width - fw, h - 28), 11, MUTED)
        if self.fade > 0:
            self.fade = max(0, self.fade - self.dt)
            veil = pygame.Surface(self.view.size, pygame.SRCALPHA)
            veil.fill((*BG, int(100 * self.fade / 0.13)))
            self.surface.blit(veil, self.view)
        if self.notice:
            message, error, until = self.notice
            if time.monotonic() > until:
                self.notice = None
            else:
                toast = pygame.Rect(x, h - 133, content_width, 80)
                self.p.panel(toast, (48, 30, 32) if error else (29, 44, 38), (97, 57, 59) if error else (58, 83, 71), 12)
                self.p.wrap(message, (toast.x + 18, toast.y + 16), toast.w - 70, 14, RED if error else GREEN, max_lines=3)
                close = pygame.Rect(toast.right - 40, toast.y + 12, 28, 28)
                self.p.icon("close", close.center, MUTED, 18)
                self.register("dismiss", close, lambda: setattr(self, "notice", None))
        if self.closing:
            veil = pygame.Surface((w, h), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 190))
            self.surface.blit(veil, (0, 0))
            box = pygame.Rect(w // 2 - 230, h // 2 - 70, 460, 140)
            self.p.panel(box)
            self.p.text("Closing safely…", (box.x + 28, box.y + 26), 23, TEXT, True)
            self.p.text("Stopping workers and cleaning temporary files.", (box.x + 28, box.y + 77), 14, MUTED)
        pygame.display.flip()

    def draw_download(self, x, y, width, items):
        # Measure the quality rows before drawing the container.
        options = VIDEO_QUALITIES if self.format == "MP4" else AUDIO_QUALITIES
        inner = width - 56
        row_width, rows = 0, 1
        for value in options:
            chip = max(67, self.p.font(14, True).size(value)[0] + 28)
            if row_width and row_width + chip > inner:
                rows += 1
                row_width = 0
            row_width += chip + 8
        card_height = 473 + (rows - 1) * 44
        self.p.panel(pygame.Rect(x, y, width, card_height))
        left = x + 28
        self.p.text("NEW DOWNLOAD", (left, y + 23), 10, MUTED, True)
        self.p.text("01", (x + width - 47, y + 19), 16, FAINT)
        self.input("url", (left, y + 53, inner - 100, 54))
        self.button("paste", "Paste", (x + width - 118, y + 53, 90, 54), self.paste)
        self.p.text("FORMAT", (left, y + 132), 10, MUTED, True)
        self.button("format:MP4", "MP4  /  Video", (left, y + 154, (inner - 12) // 2, 48), lambda: self.set_format("MP4"), selected=self.format == "MP4", icon="video")
        self.button("format:MP3", "MP3  /  Audio", (left + (inner - 12) // 2 + 12, y + 154, (inner - 12) // 2, 48), lambda: self.set_format("MP3"), selected=self.format == "MP3", icon="audio")
        self.p.text("VIDEO QUALITY" if self.format == "MP4" else "AUDIO QUALITY", (left, y + 225), 10, MUTED, True)
        selected = self.video_quality if self.format == "MP4" else self.audio_quality
        end = self.choices("quality", options, selected, left, y + 248, inner, self.set_quality)
        hint = "Uses an available resolution up to your choice; falls back if needed." if self.format == "MP4" else "MP3 encoding bitrate. Audio quality is limited by the source."
        self.p.text(hint, (left, end + 10), 12, FAINT, width=inner)
        self.p.text("SAVE TO", (left, end + 45), 10, MUTED, True)
        self.folder_field("output", left, end + 67, inner)
        self.p.icon("check", (left + 8, end + 140), GREEN, 15)
        self.p.text("Original titles. Windows-safe filenames.", (left + 24, end + 130), 12, MUTED, width=inner - 245)
        self.button("add", "Add to Queue", (x + width - 219, end + 122, 191, 43), self.add_to_queue, primary=True, icon="plus")
        bottom = y + card_height
        waiting = sum(i.state == "Waiting" for i in items)
        completed = sum(i.state == "Completed" for i in items)
        self.p.panel(pygame.Rect(x, bottom + 16, width, 75), BG, BORDER, 12)
        self.p.icon("queue", (x + 30, bottom + 54), MUTED, 20)
        self.p.text("Your queue", (x + 54, bottom + 29), 14, TEXT, True)
        summary = f"{waiting} waiting  ·  {completed} completed" if items else "Add a link to get started"
        self.p.text(summary, (x + 54, bottom + 52), 12, MUTED)
        self.button("view_queue", "View queue", (x + width - 144, bottom + 34, 126, 38), lambda: self.navigate("Queue"), icon="arrow")
        bottom += 91
        warnings = []
        if not self.dependencies["yt_dlp"]:
            warnings.append("yt-dlp is missing. Install requirements.txt, then restart the app.")
        if not self.dependencies["ffmpeg"]:
            warnings.append("FFmpeg is required for MP3 conversion and some MP4 downloads. Set it up in Settings.")
        if not self.dependencies["javascript"]:
            warnings.append("A JavaScript runtime may be needed for YouTube. See the dependency setup in Settings.")
        for warning in warnings:
            bottom += 14
            bottom += self.p.wrap(warning, (x + 4, bottom), width - 8, 12, ACCENT)
        return bottom

    def draw_queue(self, x, y, width, items):
        waiting = any(i.state == "Waiting" for i in items)
        self.button("start", "Resume queue" if self.started_once else "Start queue", (x, y, 150, 42), self.start_queue, primary=True, enabled=waiting and not self.manager.running)
        self.button("pause", "Pause queue", (x + 160, y, 136, 42), self.manager.pause, enabled=self.manager.running)
        self.button("clear", "Clear completed", (x + width - 154, y, 154, 42), self.manager.clear_completed, enabled=any(i.state == "Completed" for i in items))
        self.p.text("Pause lets the current item finish and holds the rest.", (x, y + 55), 12, MUTED, width=width)
        y += 91
        if not items:
            self.p.panel(pygame.Rect(x, y, width, 280))
            self.p.panel(pygame.Rect(x + width // 2 - 30, y + 43, 60, 60), FIELD, BORDER, 18)
            self.p.icon("queue", (x + width // 2, y + 73), ACCENT, 28)
            label = "Good things start with a link."
            label_width = self.p.font(22, True).size(label)[0]
            self.p.text(label, (x + (width - label_width) // 2, y + 126), 22, TEXT, True)
            sub = "Your downloads will appear here, ready when you are."
            self.p.text(sub, (x + (width - self.p.font(14).size(sub)[0]) // 2, y + 164), 14, MUTED)
            self.button("new", "Add a download", (x + width // 2 - 89, y + 211, 178, 40), lambda: self.navigate("Download"), icon="plus")
            return y + 280
        for item in items:
            height = 200 if item.error else 158
            if y + height < self.view.top or y > self.view.bottom:
                y += height + 14
                continue
            self.p.panel(pygame.Rect(x, y, width, height))
            self.p.panel(pygame.Rect(x + 20, y + 20, 36, 36), FIELD, BORDER, 9)
            self.p.icon("video" if item.format == "MP4" else "audio", (x + 38, y + 38), ACCENT, 18)
            self.p.text(item.title, (x + 70, y + 16), 16, TEXT, True, width - 221)
            self.p.text(item.url, (x + 70, y + 42), 11, FAINT, width=width - 221)
            color = GREEN if item.state == "Completed" else RED if item.state == "Failed" else ACCENT if item.state == "Downloading" else MUTED
            self.p.text(item.state, (x + width - 117, y + 19), 12, color, True)
            quality = item.quality
            if item.actual_quality and item.actual_quality != item.quality:
                quality += f"  →  {item.actual_quality}"
            self.p.text(f"{item.format}  ·  {quality}", (x + 20, y + 73), 12, MUTED, width=width - 235)
            self.p.text(f"{round(item.progress * 100)}%", (x + width - 58, y + 73), 12, color, True)
            progress = pygame.Rect(x + 20, y + 101, width - 40, 4)
            pygame.draw.rect(self.surface, FIELD, progress, border_radius=2)
            if item.progress:
                pygame.draw.rect(self.surface, color, (progress.x, progress.y, int(progress.w * max(0, min(1, item.progress))), 4), border_radius=2)
            stage = item.stage
            if item.state == "Downloading" and item.speed:
                stage = f"{bytes_label(item.speed)}/s   ·   ETA {eta_label(item.eta)}   ·   {bytes_label(item.downloaded_bytes)}"
            self.p.text(stage, (x + 20, y + 118), 12, MUTED, width=width - 265)
            if item.state in ("Waiting", "Downloading"):
                self.button("cancel:" + item.id, "Cancel", (x + width - 178, y + 116, 76, 29), lambda i=item.id: self.manager.cancel(i))
            elif item.state in ("Failed", "Cancelled"):
                self.button("retry:" + item.id, "Retry", (x + width - 178, y + 116, 76, 29), lambda i=item.id: self.manager.retry(i))
            else:
                self.button("folder:" + item.id, "Folder", (x + width - 178, y + 116, 76, 29), lambda path=item.output_dir: self.open_output(path))
            self.button("remove:" + item.id, "Remove", (x + width - 94, y + 116, 74, 29), lambda i=item.id: self.manager.remove(i), enabled=item.id != self.manager.active_id)
            if item.error:
                self.p.wrap(item.error, (x + 20, y + 155), width - 114, 12, RED, 18, 2)
                self.button("error:" + item.id, "Copy", (x + width - 80, y + 160, 60, 28), lambda error=item.error: self.copy_error(error))
            y += height + 14
        return y

    def draw_settings(self, x, y, width):
        inner = width - 48
        defaults_height = 89 + self.choices_height(VIDEO_QUALITIES, inner - 126) + 18 + self.choices_height(AUDIO_QUALITIES, inner - 126) + 26
        self.p.panel(pygame.Rect(x, y, width, defaults_height))
        self.p.text("DOWNLOAD DEFAULTS", (x + 24, y + 21), 10, MUTED, True)
        self.p.text("Format", (x + 24, y + 52), 14, TEXT, True)
        self.choices("default_format", ("MP4", "MP3"), self.settings.default_format, x + 150, y + 43, inner - 126, lambda value: self.setting("default_format", value))
        self.p.text("Video quality", (x + 24, y + 96), 14, TEXT, True)
        end = self.choices("default_video", VIDEO_QUALITIES, self.settings.video_quality, x + 150, y + 89, inner - 126, lambda value: self.setting("video_quality", value))
        self.p.text("Audio quality", (x + 24, end + 25), 14, TEXT, True)
        self.choices("default_audio", AUDIO_QUALITIES, self.settings.audio_quality, x + 150, end + 18, inner - 126, lambda value: self.setting("audio_quality", value))
        y += defaults_height + 16
        self.p.panel(pygame.Rect(x, y, width, 248))
        self.p.text("FILES & TOOLS", (x + 24, y + 21), 10, MUTED, True)
        self.p.text("Default output folder", (x + 24, y + 51), 14, TEXT, True)
        self.folder_field("settings_output", x + 24, y + 79, inner)
        self.p.text("FFmpeg location", (x + 24, y + 143), 14, TEXT, True)
        self.folder_field("ffmpeg", x + 24, y + 172, inner)
        self.p.text("Leave blank to detect automatically, or choose FFmpeg's bin folder.", (x + 24, y + 225), 11, FAINT, width=inner)
        y += 264
        self.p.panel(pygame.Rect(x, y, width, 170))
        for offset, key, label, description in ((20, "auto_start", "Start queue automatically", "Begin downloading when an item is added."), (94, "remember", "Remember settings", "Save preferences for your next launch.")):
            self.p.text(label, (x + 24, y + offset), 14, TEXT, True)
            self.p.text(description, (x + 24, y + offset + 27), 12, MUTED, width=width - 155)
            value = getattr(self.settings, key)
            self.button("toggle:" + key, "ON" if value else "OFF", (x + width - 93, y + offset + 5, 69, 36), lambda k=key, v=value: self.setting(k, not v), selected=value)
        y += 188
        self.button("save_settings", "Save preferences", (x + width - 182, y, 182, 43), self.save_settings, primary=True, icon="check")
        self.p.text("Format and toggle changes save automatically.", (x + 2, y + 12), 12, FAINT, width=width - 210)
        y += 67
        self.p.panel(pygame.Rect(x, y, width, 245))
        self.p.text("DEPENDENCIES", (x + 24, y + 21), 10, MUTED, True)
        status = self.dependencies
        checks = [("Python", sys.version.split()[0], True), ("pygame", pygame.version.ver, True),
                  ("yt-dlp", "Installed" if status["yt_dlp"] else "Missing — install requirements.txt", status["yt_dlp"]),
                  ("FFmpeg + ffprobe", "Detected" if status["ffmpeg"] and status["ffprobe"] else "Missing or incomplete", bool(status["ffmpeg"] and status["ffprobe"])),
                  ("JavaScript runtime", next(iter(status["javascript"]), "Missing — install Deno or Node.js"), bool(status["javascript"]))]
        for index, (name, detail, ok) in enumerate(checks):
            row = y + 54 + index * 30
            pygame.draw.circle(self.surface, GREEN if ok else ACCENT, (x + 29, row + 10), 3)
            self.p.text(name, (x + 44, row), 13, TEXT)
            self.p.text(detail, (x + 205, row), 13, GREEN if ok else ACCENT, width=width - 235)
        self.p.text(status["ffmpeg"] or "FFmpeg is needed for MP3 conversion and separate video/audio streams.", (x + 24, y + 214), 11, FAINT, width=inner)
        y += 264
        self.p.wrap("Dependency setup and update commands are in README.md. Settings are stored in a small local JSON file. Turning Remember settings off removes the saved preferences.", (x + 2, y), width - 4, 12, MUTED)
        return y + 65

    def draw_about(self, x, y, width):
        self.p.panel(pygame.Rect(x, y, width, 396))
        self.p.panel(pygame.Rect(x + 32, y + 32, 65, 65), (48, 35, 31), None, 19)
        self.p.icon("download", (x + 64, y + 64), ACCENT, 34)
        self.p.text("YouTube Downloader", (x + 32, y + 126), 28, TEXT, True)
        self.p.text("Version " + VERSION, (x + 33, y + 174), 13, ACCENT)
        self.p.wrap("A clean Python + Pygame downloader using yt-dlp. Save videos or audio with a considered interface and a queue that stays out of your way.", (x + 32, y + 219), width - 64, 16, MUTED)
        pygame.draw.line(self.surface, BORDER, (x + 32, y + 308), (x + width - 32, y + 308))
        self.p.text("PYTHON", (x + 32, y + 337), 11, MUTED, True)
        self.p.text("PYGAME", (x + 133, y + 337), 11, MUTED, True)
        self.p.text("YT-DLP", (x + 236, y + 337), 11, MUTED, True)
        self.p.text("FFMPEG", (x + 337, y + 337), 11, MUTED, True)
        y += 418
        self.p.wrap("Video titles become readable, Windows-safe filenames. Existing files are kept; duplicate titles get a numbered suffix. Downloads run sequentially. Queue history lasts for this session; your preferences can be remembered.", (x + 2, y), width - 4, 14, MUTED)
        return y + 115

    def begin_close(self):
        if self.closing:
            return
        self.closing = True
        self.manager.shutdown(wait=False)
        if self.picker:
            if self.picker_tree:
                self.picker_tree.close()
                self.picker_tree = None
            else:
                self.picker.terminate()

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.begin_close()
            return
        if event.type == pygame.VIDEORESIZE:
            size = (max(760, event.w), max(600, event.h))
            self.surface = pygame.display.set_mode(size, pygame.RESIZABLE)
            self.p.surface = self.surface
            return
        if self.closing:
            return
        if event.type == pygame.MOUSEMOTION:
            self.mouse = event.pos
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll[self.page] = min(self.max_scroll, max(0, self.scroll[self.page] - event.y * 48))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.mouse = event.pos
            hit = next((h for h in reversed(self.hits) if h.rect.collidepoint(event.pos)), None)
            self.focus = hit.key if hit else None
            self.pressed = hit.key if hit else None
            if hit and hit.input:
                hit.input.click(event.pos[0], self.p)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            hit = next((h for h in reversed(self.hits) if h.rect.collidepoint(event.pos)), None)
            if hit and self.pressed == hit.key and hit.action:
                hit.action()
            self.pressed = None
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_TAB:
                keys = [h.key for h in self.hits]
                if keys:
                    index = keys.index(self.focus) if self.focus in keys else -1
                    self.focus = keys[(index + (-1 if event.mod & pygame.KMOD_SHIFT else 1)) % len(keys)]
            elif event.key == pygame.K_ESCAPE:
                self.notice = None
                self.focus = None
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.focus == "url":
                self.add_to_queue()
            elif self.focus in self.inputs:
                try:
                    self.inputs[self.focus].key(event)
                except OSError as error:
                    self.notify(str(error), True)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                hit = next((h for h in self.hits if h.key == self.focus), None)
                if hit and hit.action:
                    hit.action()
            elif event.key in (pygame.K_PAGEDOWN, pygame.K_PAGEUP):
                direction = 1 if event.key == pygame.K_PAGEDOWN else -1
                self.scroll[self.page] = max(0, min(self.max_scroll, self.scroll[self.page] + direction * self.view.h * 0.8))
        elif event.type == pygame.TEXTINPUT and self.focus in self.inputs:
            self.inputs[self.focus].key(event)

    def step(self, events=None):
        self.dt = min(0.1, time.monotonic() - self.last_frame)
        self.last_frame = time.monotonic()
        if events is not None:
            pygame.event.pump()  # Keep native Windows owner/dialog messages flowing in event-driven tests too.
        for event in pygame.event.get() if events is None else events:
            self.handle_event(event)
        self.poll_picker()
        if self.closing and not self.manager.alive and self.picker is None:
            self.running = False
        self.draw()
        if pygame.display.get_driver() != "dummy":
            try:
                hit = next((h for h in reversed(self.hits) if h.rect.collidepoint(self.mouse)), None)
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_IBEAM if hit and hit.input else pygame.SYSTEM_CURSOR_HAND if hit else pygame.SYSTEM_CURSOR_ARROW)
            except pygame.error:
                pass
        return self.running

    def run(self, smoke_seconds=None, screenshot=None):
        start = time.monotonic()
        try:
            while self.running:
                self.step()
                if smoke_seconds is not None and time.monotonic() - start >= smoke_seconds and not self.closing:
                    if screenshot:
                        Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                        pygame.image.save(self.surface, screenshot)
                    self.begin_close()
                self.clock.tick(60)
        finally:
            self.close()

    def close(self):
        self.manager.shutdown()
        if self.picker:
            if self.picker_tree:
                self.picker_tree.close()
                self.picker_tree = None
            elif self.picker.poll() is None:
                self.picker.terminate()
            self.picker.communicate(timeout=3)
            self.picker = None
        pygame.quit()
