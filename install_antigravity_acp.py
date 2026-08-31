#!/usr/bin/python3
"""Install the exact Google native Antigravity ACP release used by Corral.

This intentionally pins one archive plus SHA-256. A changed upstream release
is an operator decision, not a silent in-place update to an agent that can act
in a working tree.
"""
import argparse
import hashlib
import os
import platform
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


# THE PINNED RELEASE IS LINUX x86-64. THERE IS NO OTHER ONE HERE.
#
# Google publishes this server under .../releases/linux/; the analogous
# darwin/arm64, darwin/x86_64 and mac/ paths all 404 (probed 2026-08-31).
# Before this guard existed, running --install on a Mac downloaded the Linux
# archive, verified its SHA correctly, installed it — and `corral-light doctor`
# then reported the Antigravity lane as **ok**, because availability is
# "the files exist on disk". The pane would die at exec.
#
# That is the exact failure this whole codebase argues against: a picker
# listing a binary that cannot run is a button that lies, and it is WORSE than
# the honest "not installed" it replaced, because the operator has stopped
# looking. Refuse at install, where the platform is knowable and the message
# can say why (P4: degrade toward safety, loudly).
PLATFORM = ("Linux", "x86_64")
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


def platform_problem():
    """Why this host cannot run the pinned release, or None. See PLATFORM."""
    system, machine = platform.system(), platform.machine()
    # x86_64/AMD64 are the same thing under different reporting conventions.
    normalized = "x86_64" if machine in ("x86_64", "amd64", "AMD64") else machine
    if (system, normalized) == PLATFORM:
        return None
    return (f"the pinned Antigravity ACP release is {PLATFORM[0]} "
            f"{PLATFORM[1]}, and this host is {system} {machine}. Google "
            f"publishes this server under .../releases/linux/ only — the "
            f"darwin and mac paths 404 (probed 2026-08-31). Installing it "
            f"here would put a binary on disk that cannot execute, and the "
            f"lane would then report as available. Refusing.\n"
            f"  If a build for this platform now exists, pinning it is an "
            f"operator decision: set RELEASE, URL and ARCHIVE_SHA256 in this "
            f"file to the real archive and its verified digest.")


def install(destination=RUNTIME):
    """Download, verify and install if absent. Existing runtime is untouched."""
    destination = Path(destination)
    if installed_ok(destination):
        return f"already installed: {destination}"
    problem = platform_problem()
    if problem:
        raise RuntimeError(problem)
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
        print(install(), flush=True)
        return 0
    if args.check:
        ok = installed_ok()
        problem = platform_problem()
        if ok and problem:
            # Installed AND wrong-platform: the files are there, so
            # `installed_ok` is true and the lane reads available — say the
            # thing that actually matters instead of the reassuring half.
            print(f"installed, but UNRUNNABLE here — {problem}", flush=True)
            return 1
        print("installed" if ok else f"missing ({problem})" if problem
              else "missing", flush=True)
        return 0 if ok else 1
    parser.error("choose --install or --check")


if __name__ == "__main__":
    raise SystemExit(main())
