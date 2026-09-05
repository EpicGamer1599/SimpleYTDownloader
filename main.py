"""Launch YouTube Downloader with `python main.py`."""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


def main() -> int:
    from runtime import prepare_standard_streams
    prepare_standard_streams()
    if sys.argv[1:2] == ["--download-worker"]:
        from downloader.worker import main as worker_main
        return worker_main()
    if sys.argv[1:2] == ["--folder-picker"]:
        from ui.native import main as picker_main
        return picker_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="YouTube Downloader — Python desktop application")
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", help="Open the real GUI and close it after a short test")
    parser.add_argument("--screenshot", help="Save a screenshot at the end of a smoke test")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        message = "YouTube Downloader requires Python 3.10 or newer. Install Python, then install requirements.txt."
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "YouTube Downloader", 0x10)
        print(message, file=sys.stderr)
        return 1
    try:
        import pygame
    except ImportError:
        message = "pygame is missing. From this folder, run:\n\npython -m pip install -r requirements.txt\n\nThen launch main.py again."
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "YouTube Downloader — Setup required", 0x10)
        print(message, file=sys.stderr)
        return 1
    from ui.app import App
    app = App()
    app.run(args.smoke_test, args.screenshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
