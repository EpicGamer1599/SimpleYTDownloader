# Verification results

Tested on Windows on 2026-09-05.

## Windows executable build

Built `dist/SimpleYTDownloader.exe` using PyInstaller 6.22.2 with the requested command in the project's virtual environment:

```powershell
python -m PyInstaller --onefile --windowed --name SimpleYTDownloader main.py
```

The final updater-enabled one-file executable is **27,226,920 bytes**, with application version **1.0.0**. PyInstaller exited with code 0. Its real GUI opened, saved `test-artifacts/updated-executable.png`, and closed successfully with exit code 0. `python -m scripts.package_release` verified the packaged `--version` and created **dist/SimpleYTDownloader-v1.0.0.zip** (26,951,894 bytes), containing only the matching EXE at its root; its ZIP CRC check passed.

After updater integration, all **50 discovered tests passed in 22.909 seconds**. The final rollback adjustment also passed all 24 updater unit tests again. In addition, `python -m tests.executable_smoke` passed **four executable-specific checks in 22.988 seconds**: actual MP4 downloading, 320 kbps MP3 conversion, separate-stream MP4 merging, and native Windows folder selection. The media checks invoke the built EXE's worker and verify output with FFprobe. The real updater helper passed three additional transactions described below.

Python, Pygame, yt-dlp, its JavaScript solver assets, and Python dependencies are included. FFmpeg/FFprobe and a JavaScript runtime remain external tools. Build warnings concerned optional platform/dependency modules and the unused Tk fallback; the native Windows picker was verified successfully.

## Automatic updater verification

Syntax checks and imports passed for `main`, `app_version`, `update_checker`, `update_service`, `updater`, the Pygame dialog/app, and the release packaging script:

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py runtime.py app_version.py update_checker.py update_service.py updater.py config downloader ui tests scripts
.\.venv\Scripts\python.exe -c "import main, app_version, update_checker, update_service, updater, ui.update_dialog, ui.app, scripts.package_release"
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m tests.updater_executable_smoke
```

The 24 updater unit tests cover semantic ordering (including prerelease/build rules), published-release validation, older/equal versions, no releases, missing/ambiguous assets, malformed data, wrong-repository URLs, restricted redirects, offline/unavailable/rate-limited GitHub responses, byte counts and SHA-256, cancellation, traversal/extra-file/link rejection, corrupt archives and invalid executable headers, failed extraction, owned-directory cleanup, staged-file tampering, failed replacement, failed launch, backup preservation when rollback is blocked, and asynchronous check behavior. Network and filesystem failure cases are controlled fixtures.

Two additional real Pygame tests cover manual checking while frames and navigation continue, update details, Later/Escape, blocking installation when the queue has unfinished work, source-install restrictions, persisted automatic-check preferences, progress rendering, cancellation cleanup, and continued GUI use. Screenshots `update-available.png`, `update-progress.png`, and `update-settings.png` under `test-artifacts/` were visually reviewed. Existing GUI tests also retain narrow-window coverage.

The frozen updater smoke suite passed **3 tests in 29.976 seconds**, using disposable copies in a writable Windows directory whose name includes spaces:

| Real packaged helper scenario | Verified outcome |
| --- | --- |
| Cancel before handoff | Original EXE remains running and unchanged; helper exits; temporary files removed |
| Candidate starts with the wrong expected version | Candidate fails the startup acknowledgement; original hash restored; previous GUI relaunched; helper and transaction files removed |
| Successful replacement | Helper waits for the old GUI process to exit; new hash installed; new GUI confirms startup and survives helper exit; backup and temporary files removed |

Success uses the current version with a harmless change to an unused DOS-stub byte in a **disposable candidate**, giving it a distinct hash without publishing a fake release. The checker still requires a strictly newer version from GitHub. Failure uses a deliberately mismatched expected version to exercise real rollback. These tests run the actual one-file EXE/helper and the PyInstaller environment reset, without an external Python interpreter for those child applications. Test orchestration itself uses the development environment. Details are recorded in `test-artifacts/updater-executable-report.json`.

Testing exposed and resolved two integration defects: the automatic-check toggle captured a later Settings value, and Windows refused to overwrite a recently executed candidate during rollback. The toggle now captures its own value; rollback moves the failed candidate aside before restoring the backup.

A read-only request to `https://api.github.com/repos/EpicGamer1599/SimpleYTDownloader/releases/latest` returned **HTTP 404** during verification. No accessible latest release was available for a real hosted update, so the network checks use controlled responses and the installation checks use local EXE copies. The API request required network access outside the tool sandbox; no release was published, downloaded for execution, or installed into a production application.

Not tested: another physical Windows installation/architecture, antivirus-specific locking behavior, protected installation directories, disk exhaustion, power interruption, and a real update between two published GitHub versions. Permission/disk failures are simulated. Automatic installation supports normal writable Windows folders; prolonged locks or abrupt shutdown may require restoring a preserved backup or removing leftover temporary files manually. The startup acknowledgement verifies the first GUI frame and expected version, rather than prolonged application health. See README for these limits, the required SHA-256 release metadata, and publisher instructions.

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

**50 tests passed, no skips, in 22.909 seconds.** This includes the original 24 tests plus 26 updater and update-GUI tests.

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

## Earlier live YouTube download verification

The following existing media verification predates updater integration and was not repeated for this change. Local media and packaged worker tests were rerun as recorded above.

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
