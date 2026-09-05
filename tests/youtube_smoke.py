"""Opt-in network/GUI test: downloads a short public YouTube video twice."""
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from config.settings import SettingsManager
from ui.app import App


def main():
    root = Path(__file__).resolve().parents[1] / "test-artifacts" / "youtube"
    root.mkdir(parents=True, exist_ok=True)
    config = SettingsManager(root / "config")
    config.settings.output_dir = str(root)
    config.settings.auto_check_updates = False
    app = App(config)
    app.manager.add("https://www.youtube.com/watch?v=jNQXAC9IVRw", "MP4", "360p", str(root))
    app.manager.add("https://www.youtube.com/watch?v=jNQXAC9IVRw", "MP3", "192 kbps", str(root))
    app.navigate("Queue")
    app.start_queue()
    start = time.monotonic()
    frames, max_frame = 0, 0
    try:
        while time.monotonic() - start < 120:
            frame_start = time.monotonic()
            app.step()
            max_frame = max(max_frame, time.monotonic() - frame_start)
            frames += 1
            items = app.manager.snapshot()
            if all(i.state in ("Completed", "Failed", "Cancelled") for i in items):
                break
            app.clock.tick(60)
        pygame.image.save(app.surface, root / "queue.png")
        report = {"frames": frames, "maximum_frame_seconds": max_frame, "elapsed_seconds": time.monotonic() - start,
                  "items": [asdict(i) for i in app.manager.snapshot()]}
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        app.close()


if __name__ == "__main__":
    main()
