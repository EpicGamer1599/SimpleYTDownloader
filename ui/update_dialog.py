"""A Pygame update modal, styled with the application's own drawing controls."""
import pygame

from app_version import APP_VERSION
from ui.widgets import BG, BORDER, FIELD, MUTED, PANEL, RED, TEXT


class UpdateDialog:
    def __init__(self, app):
        self.app = app
        self.visible = False
        self.notes_scroll = 0
        self.notes_max_scroll = 0

    def show(self):
        self.app.hits = []
        self.visible = True
        self.notes_scroll = 0
        self.app.focus = "update:later"
        self.app.pressed = None

    def later(self):
        state = self.app.updates.snapshot().state
        if state in ("downloading", "extracting", "preparing", "cancelling"):
            self.app.updates.cancel()
        elif state not in ("installing", "restarting"):
            self.visible = False
            self.app.focus = None
            self.app.hits = []

    def start(self):
        if any(item.state in ("Waiting", "Downloading") for item in self.app.manager.snapshot()):
            return
        self.app.updates.download_and_install()

    def draw(self):
        app = self.app
        snapshot = app.updates.snapshot()
        p = app.p
        width, height = app.surface.get_size()
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 180))
        app.surface.blit(shade, (0, 0))
        box = pygame.Rect((width - min(650, width - 56)) // 2, (height - 440) // 2, min(650, width - 56), 440)
        p.panel(box, PANEL, BORDER, 18)
        x, y, inner = box.x + 28, box.y + 25, box.w - 56
        release = snapshot.release
        p.text("APPLICATION UPDATE", (x, y), 10, p.accent, True)
        p.text("A new version is ready" if release and snapshot.state == "available" else "Application update", (x, y + 28), 25, TEXT, True)
        p.text(f"Current: {APP_VERSION}" + (f"    →    New: {release.version}" if release else ""), (x, y + 72), 14, MUTED, width=inner)
        if release:
            p.text(release.name, (x, y + 105), 16, TEXT, True, inner)
        notes_box = pygame.Rect(x, y + 142, inner, 105)
        p.panel(notes_box, FIELD, BORDER, 9)
        previous = app.surface.get_clip()
        app.surface.set_clip(notes_box.inflate(-20, -12))
        notes = (release.notes or "The publisher did not provide release notes.") if release else "Release details are unavailable."
        notes_height = p.wrap(notes, (x + 12, notes_box.y + 10 - self.notes_scroll), inner - 24, 13, MUTED, 20, 12000)
        self.notes_max_scroll = max(0, notes_height - (notes_box.h - 20))
        self.notes_scroll = min(self.notes_scroll, self.notes_max_scroll)
        app.surface.set_clip(previous)
        busy = snapshot.state in ("downloading", "extracting", "preparing", "installing", "restarting", "cancelling")
        blocked = any(i.state in ("Waiting", "Downloading") for i in app.manager.snapshot())
        if busy:
            progress = snapshot.received / snapshot.total if snapshot.total else 0
            bar = pygame.Rect(x, y + 264, inner, 5)
            pygame.draw.rect(app.surface, FIELD, bar, border_radius=2)
            if progress:
                pygame.draw.rect(app.surface, p.accent, (bar.x, bar.y, int(bar.w * min(1, progress)), bar.h), border_radius=2)
            message = snapshot.message
            if snapshot.state == "downloading":
                message += f"  {snapshot.received / 1048576:.1f} / {snapshot.total / 1048576:.1f} MB"
            p.wrap(message, (x, y + 282), inner, 13, MUTED, 19, 3)
        else:
            message = snapshot.message if snapshot.state != "available" else "Updating will restart the application after the download is verified."
            if snapshot.state == "available" and not app.updates.install_supported:
                message = "Run the packaged SimpleYTDownloader.exe to install updates automatically. Source checkouts can check releases only."
            elif snapshot.state == "available" and blocked:
                message = "Finish or cancel the queued downloads before updating. Choose Later to return to your queue."
            p.wrap(message, (x, y + 264), inner, 13, RED if snapshot.state == "error" else MUTED, 19, 4)
        app.hits = []  # The modal owns pointer and keyboard focus.
        locked = snapshot.state in ("installing", "restarting", "cancelling")
        app.button("update:later", "Cancel" if busy else "Later" if snapshot.state == "available" else "Close",
                   (box.right - 320, box.bottom - 68, 126, 42), self.later, enabled=not locked)
        app.button("update:now", "Update Now", (box.right - 182, box.bottom - 68, 154, 42), self.start,
                   primary=True, enabled=snapshot.state == "available" and app.updates.install_supported and not blocked)
