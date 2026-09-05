"""Updater security, failure recovery, background work, and Pygame integration."""
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from app_version import APP_VERSION
from update_checker import API_ROOT, EXE_NAME, LATEST_RELEASE_URL, RELEASE_ROOT, GitHubClient, RestrictedRedirects, SemVersion, UpdateCancelled, UpdateError, parse_release
from update_service import UpdateService
from updater import atomic_json, cleanup_stage, create_stage, extract_update, file_hash, load_plan, prepare_plan, read_json, replace_and_launch


def pe_bytes(label=b"new"):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, 0x8664)
    struct.pack_into("<H", data, 150, 2)
    data[512:512 + len(label)] = label
    return bytes(data)


def zip_bytes(content=None, name=EXE_NAME):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(name, content or pe_bytes())
    return output.getvalue()


def release_data(version="1.1.0", archive=None):
    archive = archive or zip_bytes()
    name = f"SimpleYTDownloader-v{version}.zip"
    return {"id": 12, "url": API_ROOT + "/releases/12", "draft": False, "prerelease": False,
            "published_at": "2026-09-05T10:00:00Z", "tag_name": "v" + version, "name": "A better downloader",
            "body": "Improved downloads.\nFixed queue handling.", "assets": [
                {"id": 34, "name": name, "state": "uploaded", "url": API_ROOT + "/releases/assets/34",
                 "browser_download_url": f"{RELEASE_ROOT}/download/v{version}/{name}", "size": len(archive),
                 "digest": "sha256:" + hashlib.sha256(archive).hexdigest()}]}


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class Opener:
    def __init__(self, data, headers=None, failure=None):
        self.data, self.headers, self.failure = data, headers, failure
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return Response(self.data, self.headers)


class VersionAndReleaseTests(unittest.TestCase):
    def test_semver_numeric_order_prereleases_and_build_metadata(self):
        values = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.9", "1.0.10", "1.2.0", "1.10.0", "2.0.0"]
        versions = [SemVersion.parse(value) for value in values]
        self.assertEqual(sorted(reversed(versions)), versions)
        self.assertEqual(SemVersion.parse("1.2.3+first"), SemVersion.parse("1.2.3+second"))

    def test_reject_non_semver(self):
        for value in ("latest", "main", "abc123", "1.2", "01.2.3", "1.2.3.4", "1.2.3-01", "1.2.3/evil", "v1.2.3", "１.2.3", None):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                SemVersion.parse(value)

    def test_older_equal_draft_prerelease_and_unpublished(self):
        for version in ("0.9.0", APP_VERSION):
            data = release_data(version)
            data["assets"] = []
            self.assertIsNone(parse_release(data))
        for field in ("draft", "prerelease"):
            data = release_data()
            data[field] = True
            self.assertIsNone(parse_release(data))
        self.assertIsNone(parse_release(release_data("1.1.0-beta")))
        data = release_data()
        data["published_at"] = None
        with self.assertRaises(UpdateError):
            parse_release(data)

    def test_valid_release_and_exact_asset(self):
        release = parse_release(release_data())
        self.assertEqual(release.version, "1.1.0")
        self.assertEqual(release.asset_name, "SimpleYTDownloader-v1.1.0.zip")
        self.assertIn("Fixed queue", release.notes)

    def test_wrong_repository_and_arbitrary_asset_url_rejected(self):
        for field, value in (("url", "https://api.github.com/repos/attacker/fake/releases/assets/34"),
                             ("browser_download_url", "https://github.com/attacker/fake/releases/download/v1.1.0/SimpleYTDownloader-v1.1.0.zip"),
                             ("browser_download_url", "https://github.com.evil.test/update.zip"),
                             ("browser_download_url", "http://github.com/EpicGamer1599/SimpleYTDownloader/releases/download/v1.1.0/SimpleYTDownloader-v1.1.0.zip")):
            data = release_data()
            data["assets"][0][field] = value
            with self.subTest(value=value), self.assertRaises(UpdateError):
                parse_release(data)
        data = release_data()
        data["url"] = "https://api.github.com/repos/attacker/fake/releases/12"
        with self.assertRaises(UpdateError):
            parse_release(data)

    def test_missing_ambiguous_malformed_and_unverified_assets(self):
        for mutation in (lambda d: d.update(assets=[]), lambda d: d["assets"].append(d["assets"][0].copy()),
                         lambda d: d["assets"][0].update(size=-1), lambda d: d["assets"][0].update(digest=None),
                         lambda d: d["assets"][0].update(state="new"), lambda d: d["assets"][0].update(name="1.1.0.zip")):
            data = release_data()
            mutation(data)
            with self.assertRaises(UpdateError):
                parse_release(data)
        for value in (None, [], {}, {"id": "12"}):
            with self.assertRaises(UpdateError):
                parse_release(value)


class TransportTests(unittest.TestCase):
    def test_uses_only_latest_release_api(self):
        opener = Opener(json.dumps(release_data()).encode())
        client = GitHubClient(lambda *args: opener)
        self.assertIsNotNone(client.latest(threading.Event()))
        self.assertEqual(opener.requests[0].full_url, LATEST_RELEASE_URL)

    def test_offline_unavailable_no_releases_and_malformed_json(self):
        for failure in (URLError("offline"), HTTPError(LATEST_RELEASE_URL, 500, "unavailable", {}, None),
                        HTTPError(LATEST_RELEASE_URL, 403, "rate limit", {}, None)):
            client = GitHubClient(lambda *args: Opener(b"", failure=failure))
            with self.assertRaises(UpdateError):
                client.latest(threading.Event())
        client = GitHubClient(lambda *args: Opener(b"", failure=HTTPError(LATEST_RELEASE_URL, 404, "none", {}, None)))
        self.assertIsNone(client.latest(threading.Event()))
        for raw in (b"<html>error</html>", b"x" * (1024 * 1024 + 1)):
            with self.assertRaises(UpdateError):
                GitHubClient(lambda *args: Opener(raw)).latest(threading.Event())

    def test_redirect_policy(self):
        original = parse_release(release_data()).download_url
        for value in ("http://release-assets.githubusercontent.com/x", "https://evil.test/x", "https://github.com/attacker/x", "https://user@release-assets.githubusercontent.com/x", "https://release-assets.githubusercontent.com:444/x", "file:///tmp/x"):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                RestrictedRedirects(original).redirect_request(Request(original), None, 302, "found", {}, value)
        result = RestrictedRedirects(original).redirect_request(Request(original), None, 302, "found", {}, "https://release-assets.githubusercontent.com/github-production-release-asset/x?signature=abc")
        self.assertEqual(result.host, "release-assets.githubusercontent.com")
        with self.assertRaises(UpdateError):
            RestrictedRedirects().redirect_request(Request(LATEST_RELEASE_URL), None, 301, "moved", {}, LATEST_RELEASE_URL + "new")

    def test_verified_download_progress_and_integrity_failures(self):
        archive = zip_bytes()
        release = parse_release(release_data(archive=archive))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "release.zip"
            progress = []
            GitHubClient(lambda *args: Opener(archive)).download(release, target, threading.Event(), lambda n, total: progress.append((n, total)))
            self.assertEqual(target.read_bytes(), archive)
            self.assertEqual(progress[-1], (len(archive), len(archive)))
            target.unlink()
            for data, headers in ((archive[:-1], {}), (b"x" * len(archive), {}), (archive + b"extra", {}), (archive, {"Content-Length": "4"})):
                with self.assertRaises(UpdateError):
                    GitHubClient(lambda *args: Opener(data, headers)).download(release, target, threading.Event(), lambda *args: None)
                self.assertFalse(target.exists())
                self.assertFalse(target.with_suffix(".part").exists())

    def test_cancel_download_removes_partial_file(self):
        archive = zip_bytes(os.urandom(200000))
        release = parse_release(release_data(archive=archive))
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "release.zip"
            with self.assertRaises(UpdateCancelled):
                GitHubClient(lambda *args: Opener(archive)).download(release, target, cancel, lambda *args: cancel.set())
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".part").exists())


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = create_stage(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_extraction(self):
        archive = self.root / "release.zip"
        archive.write_bytes(zip_bytes())
        result = extract_update(archive, self.root, threading.Event())
        self.assertEqual(result.read_bytes(), pe_bytes())

    def test_traversal_extra_files_duplicates_links_and_corrupt_zip(self):
        bad_archives = [zip_bytes(name="../outside.exe"), zip_bytes(name="C:/outside.exe"), zip_bytes(name="nested/" + EXE_NAME),
                        zip_bytes(name="/outside.exe"), zip_bytes(name="SimpleYTDownloader.exe:stream"), b"not a zip", zip_bytes(b"not an EXE")]
        for make_link in (True, False):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as package:
                entry = zipfile.ZipInfo(EXE_NAME)
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16 if make_link else (stat.S_IFREG | 0o644) << 16
                package.writestr(entry, pe_bytes())
                if not make_link:
                    package.writestr("side-loaded.dll", b"bad")
            bad_archives.append(buffer.getvalue())
        for data in bad_archives:
            stage = create_stage(Path(self.temp.name))
            archive = stage / "release.zip"
            archive.write_bytes(data)
            with self.assertRaises(UpdateError):
                extract_update(archive, stage, threading.Event())
        self.assertFalse((Path(self.temp.name) / "outside.exe").exists())

    def test_cancellation_and_failed_extraction(self):
        archive = self.root / "release.zip"
        archive.write_bytes(zip_bytes())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(UpdateCancelled):
            extract_update(archive, self.root, cancel)
        stage = create_stage(Path(self.temp.name))
        with patch("updater.zipfile.ZipFile", side_effect=PermissionError("denied")), self.assertRaises(PermissionError):
            extract_update(archive, stage, threading.Event())

    def test_cleanup_requires_ownership_and_preserves_unrelated_directory(self):
        unrelated = Path(self.temp.name) / "important"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")
        with self.assertRaises(UpdateError):
            cleanup_stage(unrelated)
        cleanup_stage(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual((unrelated / "keep.txt").read_text(), "keep")


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.target = self.base / EXE_NAME
        self.target.write_bytes(pe_bytes(b"old"))
        self.root = create_stage(self.base)
        (self.root / "release.zip").write_bytes(zip_bytes())
        staged = extract_update(self.root / "release.zip", self.root, threading.Event())
        self.plan = prepare_plan(self.target, staged, "1.1.0", os.getpid(), threading.Event())

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_and_successful_replacement(self):
        self.assertEqual(load_plan(self.root / "plan.json", self.plan.token), self.plan)
        # Merely preparing an update never modifies the old executable.
        self.assertEqual(self.target.read_bytes(), pe_bytes(b"old"))
        calls = []
        def launch(plan, rollback):
            calls.append(rollback)
            self.assertEqual(self.target.read_bytes(), pe_bytes())
            self.assertEqual(plan.backup.read_bytes(), pe_bytes(b"old"))
        self.assertEqual(replace_and_launch(self.plan, launch), "success")
        self.assertEqual(calls, [False])
        self.assertFalse(self.plan.backup.exists())
        self.assertFalse(self.plan.incoming.exists())

    def test_replacement_failure_restores_old_and_relaunches(self):
        def move(source, destination):
            if source == self.plan.incoming:
                raise PermissionError("locked")
            source.replace(destination)
        calls = []
        with patch("updater.replace_with_retry", side_effect=move):
            self.assertEqual(replace_and_launch(self.plan, lambda p, rollback: calls.append(rollback)), "rolled_back")
        self.assertEqual(calls, [True])
        self.assertEqual(self.target.read_bytes(), pe_bytes(b"old"))

    def test_failed_launch_restores_old(self):
        calls = []
        def launch(plan, rollback):
            calls.append(rollback)
            if not rollback:
                raise UpdateError("new executable did not acknowledge startup")
        self.assertEqual(replace_and_launch(self.plan, launch), "rolled_back")
        self.assertEqual(calls, [False, True])
        self.assertEqual(self.target.read_bytes(), pe_bytes(b"old"))

    def test_backup_kept_if_windows_blocks_restore(self):
        def move(source, destination):
            if source in (self.plan.incoming, self.plan.backup):
                raise PermissionError("locked")
            source.replace(destination)
        with patch("updater.replace_with_retry", side_effect=move), self.assertRaises(UpdateError):
            replace_and_launch(self.plan, lambda *args: None)
        self.assertEqual(self.plan.backup.read_bytes(), pe_bytes(b"old"))

    def test_tampered_payload_is_never_launched(self):
        Path(self.plan.staged_exe).write_bytes(pe_bytes(b"tampered"))
        calls = []
        self.assertEqual(replace_and_launch(self.plan, lambda plan, rollback: calls.append(rollback)), "rolled_back")
        self.assertEqual(calls, [True])
        self.assertEqual(self.target.read_bytes(), pe_bytes(b"old"))

    def test_wrong_token_target_and_downgrade_rejected(self):
        with self.assertRaises(UpdateError):
            load_plan(self.root / "plan.json", "a" * 32)
        data = read_json(self.root / "plan.json")
        for key, value in (("target", str(self.base / "other.exe")), ("repository", "evil/repo"), ("version", "0.9.0"), ("staged_exe", str(self.target))):
            atomic_json(self.root / "plan.json", data | {key: value})
            with self.assertRaises(UpdateError):
                load_plan(self.root / "plan.json", self.plan.token)


class BackgroundTests(unittest.TestCase):
    def wait(self, service):
        deadline = time.monotonic() + 3
        while service.alive and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service.alive)

    def test_check_is_background_and_cancellable(self):
        entered, released = threading.Event(), threading.Event()
        class SlowClient:
            def latest(self, cancel, version):
                entered.set()
                released.wait(2)
                return parse_release(release_data())
        service = UpdateService(SlowClient())
        start = time.monotonic()
        self.assertTrue(service.check())
        self.assertLess(time.monotonic() - start, 0.1)
        self.assertTrue(entered.wait(1))
        self.assertFalse(service.check(manual=True))
        service.cancel_check()
        released.set()
        self.wait(service)
        self.assertEqual(service.snapshot().state, "idle")
        self.assertEqual(service.events(), [])

    def test_startup_errors_are_silent_manual_errors_are_reported(self):
        class OfflineClient:
            def latest(self, *args):
                raise UpdateError("No internet")
        service = UpdateService(OfflineClient())
        service.check()
        self.wait(service)
        self.assertEqual(service.snapshot().state, "error")
        self.assertEqual(service.events(), [])
        service.check(manual=True)
        self.wait(service)
        self.assertEqual(service.events(), [("error", True)])

    def test_no_newer_release_does_nothing_automatic_and_reports_manual(self):
        class Client:
            def latest(self, *args):
                return None
        service = UpdateService(Client())
        service.check()
        self.wait(service)
        self.assertEqual(service.events(), [])
        service.check(manual=True)
        self.wait(service)
        self.assertEqual(service.events(), [("latest", True)])


if __name__ == "__main__":
    unittest.main()
