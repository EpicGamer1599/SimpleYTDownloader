# Verification results

Tested on Windows on 2026-09-05.

## Windows executable build

Built `dist/SimpleYTDownloader.exe` using PyInstaller 6.22.2 with the requested command in the project's virtual environment:

```powershell
python -m PyInstaller --onefile --windowed --name SimpleYTDownloader main.py
```

The one-file executable is 27,185,465 bytes. Its real GUI opened, saved `test-artifacts/executable.png`, and closed successfully with exit code 0. The download worker also returned a readable missing-FFmpeg error through its redirected pipes in windowed mode.

After the packaging changes, all **24 existing tests passed in 13.150 seconds**. In addition, `python -m tests.executable_smoke` passed **four executable-specific checks in 16.012 seconds**: actual MP4 downloading, 320 kbps MP3 conversion, separate-stream MP4 merging, and native Windows folder selection. The media checks invoke the built EXE's worker and verify output with FFprobe.

Python, Pygame, yt-dlp, its JavaScript solver assets, and Python dependencies are included. FFmpeg/FFprobe and a JavaScript runtime remain external tools. Build warnings concerned optional platform/dependency modules and the unused Tk fallback; the native Windows picker was verified successfully.

## Environment

| Component | Version |
| --- | --- |
| Python | 3.12.8, 64-bit |
| pygame | 2.6.1 |
| yt-dlp | 2026.8.19 |
| FFmpeg / FFprobe | 8.1.1 |
| Node.js | 22.15.1 |

Python dependencies were installed in the project's `.venv`. Existing FFmpeg and Node.js installations were detected; no global Python packages were changed.

## Automated suite

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**24 tests passed, no skips, in 14.932 seconds.**

Coverage includes:

- Readable Windows filenames, all invalid filename characters, device names, trailing spaces/dots, empty titles, Unicode length limits, and duplicate-file preservation.
- YouTube URL variants and rejection of invalid, unsupported, and deceptive hostnames.
- JSON settings round trips, malformed settings recovery, type validation, and removal of saved settings when remembering is disabled.
- Actual yt-dlp format selection against controlled format metadata, including resolution caps, fallback, separate streams, and progressive-only operation without FFmpeg.
- Clear messages when yt-dlp or FFmpeg is missing; disk-full and permission-error message mapping.
- Real Pygame keyboard and mouse events: URL input, Ctrl+V, Paste, Ctrl+A, deletion, Tab, Enter, MP4/MP3 switching, quality selection, multiple queued items, navigation, scrolling, and resizing to 760 × 600.
- Actual native Windows folder selection and persistence of the selected folder.
- Manual start, sequential processing, pause/resume, auto-start, queue draining, removal, clearing, retry, active/waiting cancellation, failure recovery, and temporary-file cleanup.
- Windows Job Object shutdown terminating a child process representing a long-running converter.
- GUI responsiveness while a controlled worker emits progress and failure events.
- Real yt-dlp downloads of generated media served over loopback, real FFmpeg MP3 conversion at 320 kbps, and real MP4 merging of separate DASH video and audio streams. FFprobe verified the resulting formats and streams.

Queue failure/lifecycle cases use a deterministic subprocess, while media pipeline checks use the actual installed yt-dlp and FFmpeg. Missing dependencies and disk/permission failures are simulated; system packages and disks are not modified to create those conditions.

## Live YouTube download

Command:

```powershell
.\.venv\Scripts\python.exe -m tests.youtube_smoke
```

The actual GUI and queue downloaded the short public video **Me at the zoo** (`jNQXAC9IVRw`) twice:

| Request | Result verified with FFprobe |
| --- | --- |
| MP4, 360p preference | Completed: 320 × 240 H.264 video and AAC audio in MP4 |
| MP3, 192 kbps | Completed: MP3 audio at 192,000 bits/s |

The video correctly fell back to its available 240p resolution. Original-title filenames were `Me at the zoo.mp4` and `Me at the zoo.mp3`. Both queue items completed without warnings or errors.

The run processed **828 GUI frames in 13.735 seconds**. The longest measured application frame was **0.016 seconds**. This verifies responsiveness during this download; it is not a performance guarantee for every machine or workload.

Media, a queue screenshot, and the detailed report are in `test-artifacts/youtube/`. The live download and subsequent FFprobe verification ran outside the tool sandbox because its network/file permissions blocked those operations. The application itself uses ordinary Windows user permissions when launched normally.

## Launch and visual checks

Python compilation completed successfully:

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py config downloader ui tests
```

The real entry point was launched and closed successfully:

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test 2 --screenshot docs\screenshot.png
```

Screenshots of Download, Queue, Settings, and About were reviewed, including narrow-window layouts. The final default-page screenshot is [docs/screenshot.png](docs/screenshot.png).

Issues found during verification were fixed: the local Tk installation could not create a picker, so Windows now uses its native COM folder dialog directly; small-window settings panels now contain wrapped controls; the main action has adequate bottom padding; and a completion/cleanup race no longer lets a newly added item start after a manually operated queue has drained.
