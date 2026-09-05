"""Create the updater ZIP from the matching, already built Windows executable.

Run from the repository root: python -m scripts.package_release
"""
import subprocess
import zipfile
from pathlib import Path

from app_version import APP_VERSION
from update_checker import EXE_NAME, SemVersion


def main():
    version = SemVersion.parse(APP_VERSION)
    if version.prerelease:
        raise SystemExit("The automatic updater accepts stable releases only.")
    directory = Path(__file__).resolve().parents[1] / "dist"
    executable = directory / EXE_NAME
    if not executable.is_file():
        raise SystemExit("Build dist/SimpleYTDownloader.exe first.")
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=35)
    if result.returncode or result.stdout.strip() != APP_VERSION:
        raise SystemExit("The built executable does not match APP_VERSION. Rebuild before packaging.")
    archive = directory / f"SimpleYTDownloader-v{APP_VERSION}.zip"
    # Exclusive creation protects an existing release artifact from accidental replacement.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        package.write(executable, EXE_NAME)
    with zipfile.ZipFile(archive) as package:
        if package.namelist() != [EXE_NAME] or package.testzip() is not None:
            raise SystemExit("The release ZIP failed verification.")
    print(archive)


if __name__ == "__main__":
    main()
