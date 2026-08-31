#!/usr/bin/python3
"""Install the exact Google native Antigravity ACP release used by Corral.

This intentionally pins one archive plus SHA-256. A changed upstream release
is an operator decision, not a silent in-place update to an agent that can act
in a working tree.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


RELEASE = "agy_acp_server_20260818_01_RC01-linux-x86_64"
URL = ("https://dl.google.com/agy-extensions/releases/linux/"
       f"agy-acp-server-{RELEASE}.zip")
ARCHIVE_SHA256 = "ce3f09628575b25497cf5a3c19d073b49acb80f1dab1ff8592919e9c9b8799e1"
FILES = ("agy_acp_server.par", "localharness_external")
RUNTIME = Path.home() / ".local/lib/corral/antigravity-acp"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_ok(destination=RUNTIME):
    destination = Path(destination)
    return (all((destination / name).is_file() for name in FILES)
            and os.access(destination / FILES[0], os.X_OK))


def download(url, destination):
    size = 0
    with urllib.request.urlopen(url, timeout=30) as source, Path(destination).open("wb") as out:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                return
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise RuntimeError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte bound")
            out.write(chunk)


def install(destination=RUNTIME):
    """Download, verify and install if absent. Existing runtime is untouched."""
    destination = Path(destination)
    if installed_ok(destination):
        return f"already installed: {destination}"
    if destination.exists():
        raise RuntimeError(f"refusing to replace incomplete runtime: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="corral-antigravity-acp-", dir="/tmp") as td:
        archive = Path(td) / "release.zip"
        download(URL, archive)
        got = sha256(archive)
        if got != ARCHIVE_SHA256:
            raise RuntimeError(f"archive SHA-256 mismatch: got {got}, expected {ARCHIVE_SHA256}")
        extract = Path(td) / "extract"
        with zipfile.ZipFile(archive) as zf:
            missing = set(FILES) - set(zf.namelist())
            if missing:
                raise RuntimeError(f"archive missing expected files: {sorted(missing)}")
            # Extract only the expected root files, never arbitrary zip paths.
            extract.mkdir(mode=0o700)
            for name in FILES:
                target = extract / name
                with zf.open(name) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
                target.chmod(0o555)
        extract.chmod(0o700)
        os.replace(extract, destination)
    return f"installed {RELEASE}: {destination}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="download and install the pinned release")
    parser.add_argument("--check", action="store_true", help="check whether the runtime is present")
    args = parser.parse_args(argv)
    if args.install:
        print(install())
        return 0
    if args.check:
        ok = installed_ok()
        print("installed" if ok else "missing")
        return 0 if ok else 1
    parser.error("choose --install or --check")


if __name__ == "__main__":
    raise SystemExit(main())
