"""Pygame error details with an explicit, asynchronous GitHub report action."""
from collections import deque
import queue
import threading
import webbrowser

import pygame

from error_reporting import ErrorReport
from ui.native import clipboard_set
from ui.widgets import BORDER, FIELD, MUTED, PANEL, RED, TEXT


class ErrorDialog:
    def __init__(self, app):
        self.app = app
        self.reports = deque()
        self.status = ""
        self.opening = False
        self.results = queue.Queue()

    @property
    def visible(self):
        return bool(self.reports)

    def show(self, report):
        self.app.hits = []
        if report not in self.reports:
            self.reports.append(report)
        self.app.focus = "error:close"
        self.app.pressed = None

    def close(self):
        self.app.hits = []
        if self.reports:
            self.reports.popleft()
        self.app.notice = None
        self.status = ""
        self.app.focus = "error:close" if self.reports else "update:later" if self.app.update_dialog.visible else None

    def copy(self):
        try:
            clipboard_set(self.reports[0].body())
            self.status = "Report copied. You can paste it into a GitHub issue."
        except OSError:
            self.status = "Could not use the clipboard. Try Report on GitHub."

    def report(self):
        if self.opening or not self.reports:
            return
        report = self.reports[0]
        self.opening = True
        self.status = "Opening a prefilled GitHub issue in your browser…"

        def open_issue():
            try:
                opened = webbrowser.open(report.issue_url(), new=2)
            except Exception:
                opened = False
            self.results.put((report, opened))

        threading.Thread(target=open_issue, name="open-error-report", daemon=True).start()

    def poll(self):
        try:
            while True:
                report, opened = self.results.get_nowait()
                self.opening = False
                if self.reports and self.reports[0] == report:
                    self.status = ("Review the report in your browser, then submit it." if opened else
                                   "Could not open the browser. Copy the report and visit the project's Issues page.")
        except queue.Empty:
            pass

    def draw(self):
        app, report = self.app, self.reports[0]
        p = app.p
        width, height = app.surface.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 190))
        app.surface.blit(veil, (0, 0))
        box = pygame.Rect((width - min(670, width - 48)) // 2, (height - 444) // 2, min(670, width - 48), 444)
        p.panel(box, PANEL, BORDER, 20)
        x, y, inner = box.x + 28, box.y + 25, box.w - 56
        p.text("LET'S FIX THIS", (x, y), 10, p.accent, True)
        p.text("Something needs your attention", (x, y + 27), 24, TEXT, True, inner)
        p.text(report.context, (x, y + 65), 13, MUTED, width=inner - 210)
        p.text(report.code, (box.right - 230, y + 65), 12, RED, True, 202)
        details = pygame.Rect(x, y + 98, inner, 153)
        p.panel(details, FIELD, BORDER, 12)
        p.wrap(report.message, (x + 16, details.y + 13), inner - 32, 14, TEXT, 21, 6)
        p.wrap(self.status or "Report this on GitHub with the error code and app version already filled in. Review the details before submitting.",
               (x, y + 269), inner, 13, MUTED, 19, 3)
        if len(self.reports) > 1:
            p.text(f"{len(self.reports) - 1} more reports", (x, box.bottom - 25), 10, MUTED)
        app.hits = []
        app.button("error:close", "Close", (x, box.bottom - 77, 100, 42), self.close)
        app.button("error:copy", "Copy report", (x + 112, box.bottom - 77, 126, 42), self.copy)
        app.button("error:report", "Report on GitHub", (box.right - 212, box.bottom - 77, 184, 42), self.report,
                   primary=True, enabled=not self.opening)
