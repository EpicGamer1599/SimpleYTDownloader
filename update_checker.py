"""Published GitHub releases, strict SemVer comparison, and bounded HTTPS downloads."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app_version import APP_VERSION

REPOSITORY = "EpicGamer1599/SimpleYTDownloader"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
RELEASE_ROOT = f"https://github.com/{REPOSITORY}/releases"
LATEST_RELEASE_URL = API_ROOT + "/releases/latest"
EXE_NAME = "SimpleYTDownloader.exe"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
TIMEOUT = 10


class UpdateError(Exception):
    """An update failed; the currently installed application can continue."""


class UpdateCancelled(UpdateError):
    pass


def check_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise UpdateCancelled("Update cancelled. Your installed application has not changed.")


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: str = ""

    @classmethod
    def parse(cls, value: str) -> "SemVersion":
        if not isinstance(value, str) or len(value) > 128:
            raise UpdateError("The release version is not valid semantic versioning.")
        match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", value, re.ASCII)
        if not match:
            raise UpdateError("The release version must use X.Y.Z, optionally followed by SemVer prerelease/build identifiers.")
        pre = tuple(match[4].split(".")) if match[4] else ()
        if any(p.isdigit() and len(p) > 1 and p.startswith("0") for p in pre):
            raise UpdateError("The release prerelease number contains a leading zero.")
        return cls(int(match[1]), int(match[2]), int(match[3]), pre, match[5] or "")

    @property
    def core(self):
        return self.major, self.minor, self.patch

    def __eq__(self, other):
        if not isinstance(other, SemVersion):
            return NotImplemented
        return self.core == other.core and self.prerelease == other.prerelease

    def __lt__(self, other):
        if not isinstance(other, SemVersion):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    name: str
    notes: str
    asset_name: str
    download_url: str
    size: int
    sha256: str
    release_id: int
    asset_id: int


def display_text(value, limit):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if (c.isprintable() or c == "\n") and not 0xD800 <= ord(c) <= 0xDFFF)[:limit]


def parse_release(data: dict, current_version: str = APP_VERSION) -> Release | None:
    """Never interpret commits, repository ZIPs, tags alone, or prereleases as updates."""
    if not isinstance(data, dict) or type(data.get("id")) is not int or data["id"] <= 0:
        raise UpdateError("GitHub returned malformed release data.")
    if data.get("draft") is not False or data.get("prerelease") is not False:
        return None
    try:
        if not isinstance(data.get("published_at"), str):
            raise ValueError
        datetime.fromisoformat(data["published_at"].replace("Z", "+00:00"))
    except ValueError:
        raise UpdateError("GitHub did not return a published release.") from None
    if data.get("url") != f"{API_ROOT}/releases/{data['id']}":
        raise UpdateError("The release does not belong to the expected GitHub repository.")
    tag = data.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("The release has no valid version tag.")
    version = tag[1:] if tag.startswith("v") else tag
    parsed = SemVersion.parse(version)
    if parsed.prerelease or parsed <= SemVersion.parse(current_version):
        return None
    asset_name = f"SimpleYTDownloader-v{version}.zip"
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The newer release has malformed download assets.")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == asset_name]
    if len(matches) != 1:
        raise UpdateError(f"The newer release is missing its unique {asset_name} asset. Try again after the release is corrected.")
    asset = matches[0]
    expected = f"{RELEASE_ROOT}/download/{quote(tag, safe='')}/{quote(asset_name, safe='')}"
    if (type(asset.get("id")) is not int or asset["id"] <= 0 or asset.get("state") != "uploaded"
            or asset.get("url") != f"{API_ROOT}/releases/assets/{asset['id']}"
            or asset.get("browser_download_url") != expected):
        raise UpdateError("The update asset is not an uploaded release file from the expected repository.")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise UpdateError("The update ZIP has an invalid size or exceeds the 256 MB download limit.")
    digest = asset.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError("GitHub has not supplied a SHA-256 digest for this ZIP. The publisher must re-upload the release asset.")
    return Release(version, tag, display_text(data.get("name"), 200) or tag,
                   display_text(data.get("body"), 12000), asset_name, expected, size,
                   digest[7:].lower(), data["id"], asset["id"])


class RestrictedRedirects(HTTPRedirectHandler):
    def __init__(self, asset_url: str | None = None):
        super().__init__()
        self.asset_url = asset_url

    def redirect_request(self, request, fp, code, message, headers, newurl):
        try:
            parsed = urlsplit(newurl)
            allowed = (self.asset_url is not None and parsed.scheme == "https"
                       and not parsed.username and not parsed.password and parsed.port in (None, 443)
                       and not parsed.fragment
                       and (newurl == self.asset_url or parsed.hostname in
                            {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}))
        except ValueError:
            allowed = False
        if not allowed:
            raise UpdateError("GitHub redirected the update to an unexpected address. The update was stopped.")
        return super().redirect_request(request, fp, code, message, headers, newurl)


class GitHubClient:
    def __init__(self, opener_factory=build_opener):
        self.opener_factory = opener_factory

    @staticmethod
    def request(url, accept):
        return Request(url, headers={"User-Agent": f"SimpleYTDownloader/{APP_VERSION}",
                                    "Accept": accept, "Accept-Encoding": "identity",
                                    "X-GitHub-Api-Version": "2022-11-28"})

    def latest(self, cancel: threading.Event, current_version=APP_VERSION) -> Release | None:
        check_cancelled(cancel)
        try:
            with self.opener_factory(RestrictedRedirects()).open(
                    self.request(LATEST_RELEASE_URL, "application/vnd.github+json"), timeout=TIMEOUT) as response:
                raw = response.read(1024 * 1024 + 1)
            check_cancelled(cancel)
            if len(raw) > 1024 * 1024:
                raise UpdateError("GitHub returned an unexpectedly large release response.")
            return parse_release(json.loads(raw), current_version)
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (403, 429):
                raise UpdateError("GitHub is limiting update checks. Try again later.") from None
            raise UpdateError("GitHub is temporarily unavailable. You can keep using the app and try again later.") from None
        except (URLError, TimeoutError, OSError):
            raise UpdateError("Could not check GitHub. Check your internet connection and try again.") from None
        except (ValueError, UnicodeError):
            raise UpdateError("GitHub returned malformed release data. Try again later.") from None

    def download(self, release: Release, destination: Path, cancel: threading.Event, progress) -> None:
        # Derive the only acceptable starting URL instead of accepting user-configured endpoints.
        expected = f"{RELEASE_ROOT}/download/{quote(release.tag, safe='')}/{quote(release.asset_name, safe='')}"
        if release.download_url != expected or release.asset_name != f"SimpleYTDownloader-v{release.version}.zip":
            raise UpdateError("The release download address is invalid.")
        SemVersion.parse(release.version)
        if release.tag not in (release.version, "v" + release.version):
            raise UpdateError("The release tag and asset version do not match.")
        check_cancelled(cancel)
        partial = destination.with_suffix(".part")
        received, digest = 0, hashlib.sha256()
        deadline = time.monotonic() + 600
        try:
            with self.opener_factory(RestrictedRedirects(expected)).open(
                    self.request(expected, "application/octet-stream"), timeout=TIMEOUT) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdecimal() or int(length) != release.size):
                    raise UpdateError("The update download size does not match its GitHub release metadata.")
                with partial.open("xb") as output:
                    while True:
                        check_cancelled(cancel)
                        if time.monotonic() > deadline:
                            raise UpdateError("The update download timed out. Please try again.")
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > release.size or received > MAX_DOWNLOAD:
                            raise UpdateError("The update download exceeded its expected size.")
                        output.write(chunk)
                        digest.update(chunk)
                        progress(received, release.size)
            check_cancelled(cancel)
            if received != release.size or digest.hexdigest() != release.sha256:
                raise UpdateError("The update ZIP is incomplete or failed SHA-256 verification. Please try again.")
            partial.replace(destination)
        except (HTTPError, URLError, TimeoutError):
            raise UpdateError("The update download failed. Check your connection and try again.") from None
        finally:
            partial.unlink(missing_ok=True)
