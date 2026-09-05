# Verification results

Latest verification: Windows, 2026-09-06. Earlier release results are retained below.

## Version 1.0.4

Added the supplied sound effects, a persistent master sound preference, and separate channels for toggle clicks, the activity loop, and completion chimes. Hardened worker progress/text handling and cleared stale button targets when dialogs open or close. Rebuilt with `APP_VERSION = "1.0.4"`; no dependencies were added or upgraded.

| Verification | Result |
| --- | --- |
| Syntax compilation and module imports | Passed for the entry point, settings, downloader, UI, tests, packaging modules, checker, and updater |
| `python -m unittest discover -v` | **100 passed**, no skips, in 32.129 seconds |
| `python -m tests.sound_executable_smoke` | **1 passed** in 3.057 seconds |
| `python -m tests.executable_smoke` | **7 passed** in 39.375 seconds |
| `python -m tests.updater_executable_smoke` | **6 passed** in 43.629 seconds |
| One-file/windowed PyInstaller build with sound data | Exit code 0; packaged EXE exists and `--version` reports **1.0.4** |
| Release ZIP integrity | Both ZIPs passed CRC checks and contain exactly the hash-matching `SimpleYTDownloader.exe` at their root |

**114 tests passed** across the source and packaged suites. Build command:

```powershell
python -m PyInstaller --onefile --windowed --name SimpleYTDownloader --add-data "soundeffects:soundeffects" main.py
```

Sound and regression coverage:

- Actual supplied assets decode through Pygame: `on_off.mp3` (0.144 seconds), `FinishDownload.mp3` (2.924 seconds), and `Downloading.wav` (5.352 seconds). Playback starts on independent channels, the activity loop does not restart each frame, and it becomes quieter while a chime plays.
- Muting stops activity/chimes with one final toggle click; later muted actions stay silent. Unmuting resumes active work. Startup with saved sound disabled does not initialize audio. Shutdown is idempotent and stops all channels.
- Simulated missing devices, missing files, and playback failures do not crash the app. A frozen asset lookup resolves the bundled directory. Old preferences default to sound enabled, and a saved mute choice survives reload.
- GUI checks exercise boolean setting feedback, mute persistence, active/paused/cancelling queues, update-download activity, shutdown, and exactly-once completion feedback across repeated frames. Queue tests verify a completion event survives removal of completed history and failed attempts do not emit it.
- NaN, infinity, malformed types, huge integers, negative values, and invalid text in worker events are safely handled before rendering. Opening an error dialog blocks stale underlying button targets immediately; closing it blocks a stale report-copy target.
- `test-artifacts/settings-sounds-1.0.4.png` was visually reviewed. The new sound preference is visible in the normal Settings layout; existing small-window GUI regressions also passed.

The standalone sound test copies only the EXE to an isolated directory with no external `soundeffects/` folder, then decodes and plays all three bundled clips, verifies loop start/stop and toggle/completion channel activity, and confirms version 1.0.4. It uses SDL's **dummy audio device**, so it verifies decoding and playback state rather than audible output from physical speakers/headphones.

The seven packaged media/native-picker tests cover MP4, MP3 conversion, separate-stream merging, thumbnails with both formats, missing/disabled thumbnails, and the real Windows folder dialog. The six real frozen-helper transactions cover replacement/relaunch/cleanup, failed-startup rollback, cancellation before handoff, and direct upgrades from the preserved **1.0.0, 1.0.2, and 1.0.3** EXEs to **1.0.4**. All replacement tests use disposable installations. No production installation or GitHub release was changed.

Artifacts:

- `dist/SimpleYTDownloader.exe`: **27,892,007 bytes**, SHA-256 `384c1bd80d311d2c1a21951a4dbcd8b806832bb8e158dcc4458c35bede3fb5a8`.
- `dist/SimpleYTDownloader-v1.0.4.zip` and `Builds/1.0.4.zip`: **27,616,444 bytes** each, SHA-256 `8c7621dad5ef1937c800c2dd0f6d4f4737d852f3977b13c4f0bbe71e53f34ee5`.
- `test-artifacts/tests-1.0.4.log`, `build-1.0.4.log`, `packaged-sounds-1.0.4.log`, `packaged-media-1.0.4.log`, and `updater-1.0.4.log`: detailed results.
- `test-artifacts/release-1.0.4.json`, `sound-executable-1.0.4.json`, and `updater-executable-1.0.4.json`: artifact and runtime reports.
- `docs/releases/1.0.4.md`: release notes and upload instructions. Earlier release ZIPs are preserved.

Limits: audible volume, seamlessness of the supplied loop, and physical device reconnection need manual listening/hardware checks. Missing-device failure paths are simulated. The app can retry unavailable audio initialization by switching sound off and on. Audio initialization and the initial decoding of the small bundled clips are synchronous; subsequent playback uses SDL's background mixer. Media tests use loopback fixtures, not current live YouTube extraction. Existing FFmpeg/runtime, updater permissions, antivirus-lock, power-loss, and startup-acknowledgement limitations remain as described below.

## Version 1.0.3

Reviewed the update transaction, HTTP client, download worker/queue, preference persistence, text rendering, and release dialog. Fixed concrete failure paths and rebuilt the application with `APP_VERSION = "1.0.3"`. No dependencies were added or upgraded.

| Verification | Result |
| --- | --- |
| Syntax compilation and module imports | Passed for the entry point, updater, worker/queue, settings, UI, and packaging modules |
| Final `python -m unittest discover -v` | **88 passed**, no skips, in 29.654 seconds |
| `python -m tests.executable_smoke` | **7 passed** in 41.129 seconds |
| `python -m tests.updater_executable_smoke` | **5 passed** in 35.529 seconds |
| `python -m PyInstaller --onefile --windowed --name SimpleYTDownloader main.py` | Exit code 0; the packaged EXE exists and its `--version` reports **1.0.3** |
| Live metadata-only GitHub check | Selected published **1.0.2.zip** and read **1,514 description characters**, using comparison version 1.0.0 to exercise the new checker |
| ZIP integrity | Both ZIPs passed CRC checks and contain exactly the hash-matching `SimpleYTDownloader.exe` at their root |

**100 tests passed** across the final source and packaged suites. An earlier 86-test source run also passed before the final streaming/deadline regressions were added; that earlier run is not included in the total.

Regressions added for this release:

- A failed terminal status write after successful replacement cannot trigger rollback. A failed status write after rollback does not report the restored running application as a failed launch. These disk/permission failures are simulated, and backup preservation is asserted.
- Cancelling during thumbnail work keeps the already saved media, marks it completed with a warning, and removes staging. Worker crashes and reported errors after the media-save event also preserve the file and allow the queue to continue. Tests use actual isolated subprocesses. Thumbnail tests verify that the media file exists before the image request begins.
- Concurrent preference writers use separate temporary files, with complete final JSON. A failed fsync preserves previous settings and removes its temporary file. Oversized, deeply nested, and invalid-encoding preferences recover with a warning.
- Long release notes reach their final line without scrolling beyond short notes. A 12,000-character string is truncated using fewer than 20 font measurements. `test-artifacts/update-notes-1.0.3.png` was visually reviewed.
- Failed HTTP responses close their bodies; incomplete transfers produce readable network errors. Cancellation is checked between available reads, release checks have an overall deadline, and a pre-existing partial download is never deleted by a failed new download attempt.

The seven packaged media/native-picker tests verify MP4, MP3, separate-stream merging, optional thumbnails with both formats, a missing thumbnail preserving the media, disabled thumbnails, and the Windows folder dialog. The five real helper transactions verify success/relaunch/cleanup, failed-startup rollback, cancellation before handoff, **1.0.0 → 1.0.3**, and **1.0.2 → 1.0.3**. Old-client transactions use the actual EXEs from the preserved release ZIPs. All installation changes use disposable directories; no production installation or GitHub release was changed.

Artifacts:

- `dist/SimpleYTDownloader.exe`: **27,246,816 bytes**, SHA-256 `a238568d483dbe3bfa0e9e05c5ee7cf4351090a69537c60dfd54f01cd4175976`.
- `dist/SimpleYTDownloader-v1.0.3.zip` and `Builds/1.0.3.zip`: **26,971,644 bytes** each, SHA-256 `ce92d783793cabe151995824aa06a33fdbf2becdd6c39b39dafe4eed4d642e75`.
- `test-artifacts/tests-1.0.3.log`, `build-1.0.3.log`, `packaged-media-1.0.3.log`, and `updater-1.0.3.log`: detailed results.
- `test-artifacts/release-1.0.3.json` and `live-release-1.0.3.json`: artifact hashes and validated release metadata.
- `docs/releases/1.0.3.md`: release notes and publisher instructions.

Limits: older installed EXEs continue to use their bundled updater helper until replaced, so the terminal-status fix protects updates initiated from 1.0.3 onward. Blocked status writes or cleanup may leave recovery files behind. Abrupt power loss, antivirus-specific locks, other Windows architectures/installations, and future YouTube behavior are not exhaustively tested. A media file committed just before a forced process kill is retained even if the worker has not yet delivered its save notification; in that narrow window the queue may still label the attempt cancelled. An active GitHub socket read can take up to its 10-second timeout before cancellation/deadline handling resumes. The previous media dependency and startup-acknowledgement limits still apply.

## Version 1.0.2

Fixed release asset selection to accept the official release's single attached ZIP, including short filenames. With multiple ZIPs, the standard versioned filename is preferred; ambiguous downloads are rejected. Release name, version, and description are retained when asset validation fails, and closing the error report shows those details. The application version is now 1.0.2 because 1.0.1 was already published.

| Verification | Result |
| --- | --- |
| Syntax compilation and imports | Passed for entry point, version, checker, service, updater, UI, tests, and packaging modules |
| `python -m unittest discover -v` | **74 passed**, no skips, in 39.087 seconds |
| One-file/windowed PyInstaller build | Exit code 0; packaged `--version` verified as **1.0.2** |
| `python -m tests.updater_executable_smoke` | **4 passed** in 43.048 seconds |
| Live release API check | New source checker, using comparison version 1.0.0, accepted the published **1.0.1.zip** and read **1,613 characters** of release description |
| Release ZIP integrity | Both ZIPs passed CRC checks and contain only the hash-matching EXE at their root |

The nine added regressions cover short/custom/encoded ZIP filenames, tags with and without `v`, preferred and ambiguous assets, release details after asset failure, rejection of description links/generated source archives/path filenames, verified download and extraction of a short-named ZIP, retained repository/hash protections, quiet automatic errors, and GUI release-note visibility after closing the error popup. The source suite also exercises the existing media, queue, thumbnail, theme, and reporting features. The previous packaged media suite was not rerun for this updater-only change.

The four packaged helper tests verified cancellation before handoff, replacement/relaunch/cleanup, failed-startup rollback, and an actual **1.0.0 → 1.0.2** transaction using the old executable from `Builds/1.0.0.zip`. Installation tests use disposable local copies. The live check was metadata-only: no GitHub release was changed, downloaded for execution, or installed into a production application. The 1.0.0 EXE's network checker is unchanged; it still requires a standard versioned release asset to reach this fix.

Artifacts:

- `dist/SimpleYTDownloader.exe`: **27,242,459 bytes**, SHA-256 `5c95aca63a8825a9d58490b5ef3de7bf9733efb0e6c7adbec6e1a203ca817a56`.
- `dist/SimpleYTDownloader-v1.0.2.zip` and `Builds/1.0.2.zip`: **26,967,312 bytes** each, SHA-256 `04e5e7c0f4a603cbe879c91e435d5cb41efc2b62b73dc6f624e9e363eec33c8b`.
- `test-artifacts/tests-1.0.2.log`, `build-1.0.2.log`, and `updater-1.0.2.log`: check/build logs.
- `test-artifacts/release-1.0.2.json` and `live-release-1.0.2.json`: artifact hashes and validated API release details.
- `test-artifacts/update-notes-1.0.2.png`: visually reviewed release details and asset error in the actual Pygame UI.
- `docs/releases/1.0.2.md`: release notes and instructions for reaching installed old clients.

No dependencies were added. Repository restrictions, SHA-256/size verification, ZIP/executable validation, background operations, and the helper/startup protocol remain in place. Previously published 1.0.0 and 1.0.1 ZIPs are retained. The existing Windows permissions, antivirus, power-interruption, and startup-acknowledgement limitations below still apply.

## Version 1.0.1

Implemented optional thumbnail saving, six persistent accent themes, error dialogs with stable support codes, prefilled GitHub issue drafts, and updated SimpleYTDownloader GUI branding. Old settings migrate with thumbnail saving off and Orange selected. The existing 1.0.0 ZIP is retained.

| Verification | Result |
| --- | --- |
| Syntax compilation and module imports | Passed for app, reporting, thumbnail, theme, UI, worker, and updater modules |
| `python -m unittest discover -v` | **65 passed**, no skips, in 33.886 seconds |
| `python -m tests.executable_smoke` | **7 passed** in 53.629 seconds |
| `python -m tests.updater_executable_smoke` | **4 passed** in 41.747 seconds |
| Requested one-file/windowed PyInstaller build | Exit code 0; bundled `--version` reports **1.0.1** |
| Packaged GUI with saved Blue theme and thumbnail setting | Opened successfully, received an invalid-link input, rendered the error dialog with **SYTD-LINK**, and exited with code 0 |
| Release ZIPs | CRC checks passed; each contains only the freshly built, hash-matching `SimpleYTDownloader.exe` at its root |

The new tests exercise thumbnail saving enabled/disabled, fallback candidates, unsupported/truncated/oversized images, existing-image preservation, and a failed thumbnail keeping its downloaded media. Actual yt-dlp and FFmpeg downloads from loopback fixtures verify thumbnail saving for **both MP4 and MP3**, including an HTTP 404 image and disabled saving. These cases were also run through the packaged EXE worker.

GUI tests exercise all six colours, persistence, a 760 × 600 window, the thumbnail toggle and captured queue preference, blocking error-dialog focus, copyable reports, error deduplication, explicit browser actions, a slow/failing browser opener, and continued frame processing. Report checks verify the fixed repository destination, version/code/reproduction template, URL length limits, and removal of common private paths, links, and credential fields. Browser opening is mocked in these tests; no GitHub issue is submitted.

The four real helper transactions verify cancellation before handoff, successful replacement/relaunch/cleanup, failed-startup rollback, and a **1.0.0 → 1.0.1 upgrade using the actual prior build**. All installation changes use disposable copies under `test-artifacts/`; no production installation or GitHub release is modified.

Screenshots visually reviewed: `settings-1.0.1-blue.png`, `settings-1.0.1-small.png`, `error-1.0.1.png`, and `executable-error-1.0.1.png`, all under `test-artifacts/`. The packaged GUI screenshot shows the Blue theme and report dialog in the real windowed EXE.

Artifacts:

- `dist/SimpleYTDownloader.exe`: **27,243,693 bytes**.
- `Builds/1.0.1.zip`: **26,968,198 bytes**, matching the previous build-folder layout.
- `dist/SimpleYTDownloader-v1.0.1.zip`: the identical ZIP with the filename required by the GitHub updater.
- `test-artifacts/release-1.0.1.json`: artifact sizes and SHA-256 hashes.
- `test-artifacts/tests-1.0.1.log`, `packaged-features-1.0.1.log`, and `updater-1.0.1.log`: detailed test output.
- `docs/releases/1.0.1.md`: prepared release notes.

Scope and limits: thumbnails are separate JPEG, PNG, or WebP files, not embedded artwork; unavailable thumbnails produce a warning and leave media saved. Formats are checked by their signatures/structural markers rather than a full image decoder. Transfers are bounded to 12 MiB. The browser report is a draft for the user to review and submit, and GitHub sign-in may be required. Common sensitive strings are redacted, but reports should still be reviewed. No live YouTube thumbnail download or GitHub issue submission was performed. The prior updater limits around permissions, antivirus locks, interrupted power, and startup acknowledgement still apply. No dependencies were added.

## Previous 1.0.0 Windows executable build

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
