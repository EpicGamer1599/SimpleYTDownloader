"""Small custom drawing and text-editing primitives, independent of pages."""
from __future__ import annotations

import math
import os
from pathlib import Path

import pygame

from ui.native import clipboard_get, clipboard_set

BG = (15, 17, 20)
SIDEBAR = (19, 21, 25)
PANEL = (25, 28, 33)
FIELD = (19, 22, 26)
BORDER = (47, 51, 58)
TEXT = (238, 240, 241)
MUTED = (145, 153, 166)
FAINT = (100, 109, 122)
ACCENT = (255, 139, 110)
GREEN = (133, 208, 173)
RED = (255, 147, 147)


def blend(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


class Painter:
    def __init__(self, surface, accent=ACCENT):
        self.surface = surface
        self.fonts = {}
        self.accent = accent

    @property
    def tint(self):
        return blend(PANEL, self.accent, 0.12)

    def font(self, size=16, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            filename = "segoeuib.ttf" if bold else "segoeui.ttf"
            path = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / filename
            self.fonts[key] = pygame.font.Font(str(path), size) if path.exists() else pygame.font.SysFont("dejavusans", size, bold=bold)
        return self.fonts[key]

    def text(self, value, position, size=16, color=TEXT, bold=False, width=None):
        font = self.font(size, bold)
        value = str(value)
        if width is not None and font.size(value)[0] > width:
            # Binary search avoids thousands of measurements for long titles
            # or unbroken URLs in release notes.
            lower, upper = 0, len(value)
            while lower < upper:
                middle = (lower + upper + 1) // 2
                if font.size(value[:middle] + "…")[0] <= max(0, width):
                    lower = middle
                else:
                    upper = middle - 1
            value = value[:lower] + "…"
        rendered = font.render(value, True, color)
        self.surface.blit(rendered, position)
        return rendered.get_rect(topleft=position)

    def wrap(self, value, position, width, size=14, color=MUTED, line_height=None, max_lines=10):
        line_height = line_height or size + 7
        x, y = position
        lines = []
        for paragraph in str(value).split("\n"):
            line = ""
            for word in paragraph.split():
                if line and self.font(size).size(line + " " + word)[0] > width:
                    lines.append(line)
                    line = word
                else:
                    line = (line + " " + word).strip()
            lines.append(line)
        for index, line in enumerate(lines[:max_lines]):
            line_y = y + index * line_height
            clip = self.surface.get_clip()
            if line_y + line_height > clip.top and line_y < clip.bottom:
                self.text(line, (x, line_y), size, color, width=width)
        return min(len(lines), max_lines) * line_height

    def panel(self, rect, color=PANEL, border=BORDER, radius=16):
        pygame.draw.rect(self.surface, color, rect, border_radius=radius)
        if border:
            pygame.draw.rect(self.surface, border, rect, width=1, border_radius=radius)

    def icon(self, name, center, color=TEXT, size=20):
        x, y = center
        k = size / 24

        def p(dx, dy):
            return round(x + dx * k), round(y + dy * k)

        def line(points, width=2):
            pygame.draw.lines(self.surface, color, False, [p(*point) for point in points], width)

        if name == "download":
            line([(0, -9), (0, 3)])
            line([(-5, -2), (0, 3), (5, -2)])
            line([(-9, 4), (-9, 9), (9, 9), (9, 4)])
        elif name == "queue":
            for dy in (-6, 0, 6):
                line([(-3, dy), (9, dy)])
                pygame.draw.circle(self.surface, color, p(-9, dy), max(1, round(k * 1.5)))
        elif name == "settings":
            for dx, dy in ((-7, -4), (0, 5), (7, -2)):
                line([(dx, -9), (dx, 9)])
                pygame.draw.circle(self.surface, color, p(dx, dy), max(2, round(3 * k)), 2)
        elif name == "about":
            pygame.draw.circle(self.surface, color, p(0, 0), round(10 * k), 2)
            line([(0, -1), (0, 6)])
            pygame.draw.circle(self.surface, color, p(0, -5), max(1, round(k)))
        elif name == "video":
            pygame.draw.rect(self.surface, color, pygame.Rect(p(-10, -8), (round(20 * k), round(16 * k))), 2, border_radius=3)
            pygame.draw.polygon(self.surface, color, [p(-2, -4), p(-2, 4), p(4, 0)])
        elif name == "audio":
            line([(-4, 6), (-4, -7), (8, -9), (8, 4)])
            pygame.draw.ellipse(self.surface, color, pygame.Rect(p(-10, 3), (round(7 * k), round(5 * k))))
            pygame.draw.ellipse(self.surface, color, pygame.Rect(p(2, 1), (round(7 * k), round(5 * k))))
        elif name == "folder":
            line([(-10, 8), (-10, -7), (-3, -7), (0, -4), (10, -4), (10, 8), (-10, 8)])
        elif name == "link":
            line([(-2, -6), (2, -10), (8, -10), (11, -7), (11, -3), (6, 2)])
            line([(2, 6), (-2, 10), (-8, 10), (-11, 7), (-11, 3), (-6, -2)])
            line([(-4, 4), (4, -4)])
        elif name == "check":
            line([(-7, 0), (-2, 5), (8, -5)])
        elif name == "plus":
            line([(-7, 0), (7, 0)])
            line([(0, -7), (0, 7)])
        elif name == "arrow":
            line([(-8, 0), (8, 0)])
            line([(2, -6), (8, 0), (2, 6)])
        elif name == "close":
            line([(-5, -5), (5, 5)])
            line([(-5, 5), (5, -5)])


class TextInput:
    def __init__(self, text="", placeholder="", limit=8192):
        self.text = text
        self.placeholder = placeholder
        self.limit = limit
        self.cursor = len(text)
        self.anchor = self.cursor
        self.scroll = 0
        self.rect = pygame.Rect(0, 0, 1, 1)

    def set(self, text):
        self.text = str(text)[:self.limit]
        self.cursor = len(self.text)
        self.anchor = self.cursor

    @property
    def selection(self):
        return sorted((self.cursor, self.anchor))

    def insert(self, text):
        start, end = self.selection
        text = "".join(c for c in text if c.isprintable())
        text = text[:max(0, self.limit - len(self.text) + end - start)]
        self.text = self.text[:start] + text + self.text[end:]
        self.cursor = start + len(text)
        self.anchor = self.cursor

    def key(self, event):
        if event.type == pygame.TEXTINPUT:
            self.insert(event.text)
            return
        if event.type != pygame.KEYDOWN:
            return
        ctrl = bool(event.mod & pygame.KMOD_CTRL)
        shift = bool(event.mod & pygame.KMOD_SHIFT)
        start, end = self.selection
        if ctrl and event.key == pygame.K_a:
            self.anchor, self.cursor = 0, len(self.text)
        elif ctrl and event.key in (pygame.K_c, pygame.K_x):
            if start != end:
                clipboard_set(self.text[start:end])
                if event.key == pygame.K_x:
                    self.insert("")
        elif ctrl and event.key == pygame.K_v:
            self.insert(clipboard_get())
        elif event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            if start != end:
                self.insert("")
            elif event.key == pygame.K_BACKSPACE and self.cursor:
                self.anchor = self.cursor - 1
                self.insert("")
            elif event.key == pygame.K_DELETE:
                self.anchor = min(len(self.text), self.cursor + 1)
                self.insert("")
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_HOME, pygame.K_END):
            if event.key == pygame.K_HOME:
                self.cursor = 0
            elif event.key == pygame.K_END:
                self.cursor = len(self.text)
            elif not shift and start != end:
                self.cursor = start if event.key == pygame.K_LEFT else end
            else:
                direction = -1 if event.key == pygame.K_LEFT else 1
                self.cursor = min(len(self.text), max(0, self.cursor + direction))
                if ctrl:
                    while 0 < self.cursor < len(self.text) and self.text[self.cursor - (1 if direction < 0 else 0)] != " ":
                        self.cursor += direction
            if not shift:
                self.anchor = self.cursor

    def click(self, x, painter):
        relative = x - self.rect.x - 16 + self.scroll
        font = painter.font(16)
        self.cursor = min(range(len(self.text) + 1), key=lambda n: abs(font.size(self.text[:n])[0] - relative))
        self.anchor = self.cursor

    def draw(self, painter, rect, focused):
        self.rect = pygame.Rect(rect)
        painter.panel(rect, FIELD, painter.accent if focused else BORDER, 10)
        old_clip = painter.surface.get_clip()
        painter.surface.set_clip(self.rect.inflate(-28, -8).clip(old_clip))
        font = painter.font(16)
        caret = font.size(self.text[:self.cursor])[0]
        self.scroll = max(0, min(self.scroll, caret))
        if caret - self.scroll > self.rect.w - 36:
            self.scroll = caret - self.rect.w + 36
        origin = self.rect.x + 16 - self.scroll
        y = self.rect.centery - font.get_height() // 2
        start, end = self.selection
        if focused and start != end:
            left = font.size(self.text[:start])[0]
            right = font.size(self.text[:end])[0]
            pygame.draw.rect(painter.surface, blend(FIELD, painter.accent, 0.3), (origin + left, y, right - left, font.get_height()))
        painter.text(self.text or self.placeholder, (origin, y), color=TEXT if self.text else FAINT)
        if focused and math.sin(pygame.time.get_ticks() / 180) > -0.3:
            pygame.draw.line(painter.surface, painter.accent, (origin + caret, y + 2), (origin + caret, y + font.get_height() - 2), 2)
        painter.surface.set_clip(old_clip)
