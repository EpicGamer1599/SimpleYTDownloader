"""Deterministic subprocess used only to test queue lifecycle and responsiveness."""
import json
import subprocess
import sys
import time
from pathlib import Path

job = json.loads(sys.stdin.readline())


def emit(**event):
    print(json.dumps(event), flush=True)


emit(event="metadata", title="Test video — a readable title", actual_quality="720p")
video_id = job["url"].rsplit("=", 1)[-1]
if video_id in ("THUMB000001", "CRASH000001", "SAVEERROR01"):
    saved = Path(job["output_dir"]) / (video_id + ".mp4")
    saved.write_bytes(b"completed media")
    emit(event="media_saved", filename=str(saved))
    emit(event="processing", stage="Saving the video thumbnail")
    if video_id == "CRASH000001":
        sys.exit(5)
    if video_id == "SAVEERROR01":
        emit(event="error", error="Thumbnail processing failed")
        sys.exit(1)
    time.sleep(120)
if video_id == "FAIL0000001":
    emit(event="error", error="This video is private. Choose a publicly available video.")
    sys.exit(1)
if video_id == "CHILD000001":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(job["work_dir"], "child.pid").write_text(str(child.pid))
    emit(event="processing", stage="Converting / merging with FFmpeg")
    time.sleep(120)
for index in range(12):
    emit(event="progress", progress=index / 12, speed=1024 * 1024, eta=12 - index, downloaded_bytes=index * 1024)
    time.sleep(0.07)
emit(event="completed", progress=1, filename=str(Path(job["output_dir"]) / "test.mp4"))
