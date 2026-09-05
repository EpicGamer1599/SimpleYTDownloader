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
    if sys.argv[1:2] == ["--apply-update"]:
        from updater import helper_main
        helper_parser = argparse.ArgumentParser(description="Internal application updater")
        helper_parser.add_argument("--apply-update", required=True)
        helper_parser.add_argument("--update-token", required=True)
        helper_args = helper_parser.parse_args()
        return helper_main(helper_args.apply_update, helper_args.update_token)
    parser = argparse.ArgumentParser(description="YouTube Downloader — Python desktop application")
    from app_version import APP_VERSION
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", help="Open the real GUI and close it after a short test")
    parser.add_argument("--screenshot", help="Save a screenshot at the end of a smoke test")
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("--update-startup", help=argparse.SUPPRESS)
    startup.add_argument("--update-rollback", help=argparse.SUPPRESS)
    parser.add_argument("--update-token", help=argparse.SUPPRESS)
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
    update_plan = args.update_startup or args.update_rollback
    app = App(check_updates=args.smoke_test is None and not update_plan)
    if update_plan:
        from updater import acknowledge_startup
        try:
            app.notify(acknowledge_startup(update_plan, args.update_token, bool(args.update_rollback)))
        except Exception:
            app.close()
            return 1
    app.run(args.smoke_test, args.screenshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
