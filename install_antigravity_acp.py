#!/usr/bin/python3
"""Install the exact Google native Antigravity ACP release used by Corral.

This intentionally pins one archive plus SHA-256 per platform. A changed
upstream release is an operator decision, not a silent in-place update to an
agent that can act in a working tree.
"""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


# ONE PINNED ARCHIVE PER PLATFORM, AND NO ROW MEANS NO INSTALL.
#
# Before a platform guard existed, running --install on a Mac downloaded the
# Linux archive, verified its SHA correctly, installed it — and
# `corral-light doctor` then reported the Antigravity lane as **ok**, because
# availability is "the files exist on disk". The pane would die at exec.
#
# That is the exact failure this whole codebase argues against: a picker
# listing a binary that cannot run is a button that lies, and it is WORSE than
# the honest "not installed" it replaced, because the operator has stopped
# looking. Refuse at install, where the platform is knowable and the message
# can say why (P4: degrade toward safety, loudly).
#
# Google publishes under .../releases/linux/ and .../releases/macos/ (the
# darwin/ and mac/ paths 404). There is no darwin-x86_64 build (404,
# 2026-10-01), so an Intel Mac — or an x86-64 Python under Rosetta — is refused.
#
# The linux-x86_64 digest was pinned from its first download. The two rows
# added 2026-10-01 are pinned the same way, trust-on-first-download: the
# archive was fetched whole (length matched Content-Length, zip test clean)
# and its SHA-256 written here. darwin-arm64 was then run live; linux-arm64
# has not been executed on any host.
#
# `args` is per build, because the builds disagree: Google registered the
# Linux server as `agy_acp_server.par --uid=`, and the macOS build has no
# such flag — it dies "Unknown command line flag 'uid'" before initialize
# (2026-10-01), while a bare start handshakes. linux-arm64 is assumed to
# match linux-x86_64 (same registration); not yet run.
BASE_URL = "https://dl.google.com/agy-extensions/releases/"
RELEASES = {
    ("Linux", "x86_64"): {
        "release": "agy_acp_server_20260818_01_RC01-linux-x86_64",
        "args": ["--uid="],
        "dir": "linux",
        "sha256": "ce3f09628575b25497cf5a3c19d073b49acb80f1dab1ff8592919e9c9b8799e1",
    },
    ("Linux", "arm64"): {
        "release": "agy_acp_server_20260818_01_RC01-linux-arm64",
        "args": ["--uid="],
        "dir": "linux",
        "sha256": "70fcdac70684de60f7a0eb16ea497d6cc4498728420f060e0850cfc9a9329b40",
    },
    ("Darwin", "arm64"): {
        "release": "agy_acp_server_20260818_01_RC01-darwin-arm64",
        "args": [],
        "dir": "macos",
        "sha256": "f122ca7e7030a27f9649da4cf1a7d80e12c48c5f6118ff35affc34d56cbf83dd",
    },
}
FILES = ("agy_acp_server.par", "localharness_external")
RUNTIME = Path.home() / ".local/lib/corral/antigravity-acp"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3      # short reads only; every attempt is still SHA-checked

# The server refuses session/new — "Authentication required … No
# authentication method selected" — until settings.json names one. Installed
# files and a clean initialize handshake do not reveal that, so the lane read
# available and died on first use (Omarchy, 2026-09-27). The installer picks
# oauth-personal: the operator's own Google login, the subscription path.
# NEVER gemini-api-key — a vendor key silently changes who pays and who sees
# the data, and that is the operator's call, not an installer default.
SETTINGS = Path.home() / ".gemini/antigravity-acp/settings.json"
AUTH_TYPE = "oauth-personal"


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


class ShortDownload(RuntimeError):
    """The connection closed before Content-Length bytes arrived."""


def download(url, destination):
    size = 0
    with urllib.request.urlopen(url, timeout=30) as source, Path(destination).open("wb") as out:
        # A connection that closes early ends the read loop exactly like a
        # finished one (seen 2026-10-01: 92 MB of a 315 MB archive, no error).
        # The SHA check would still refuse it, but say what actually happened.
        expected = source.headers.get("Content-Length")
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise RuntimeError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte bound")
            out.write(chunk)
    if expected and expected.isdigit() and size != int(expected):
        raise ShortDownload(f"download ended short: {size} of {expected} bytes")


def host_platform(system=None, machine=None):
    """(system, machine) normalized to the RELEASES keys."""
    system = system or platform.system()
    machine = machine or platform.machine()
    # Same CPU, different reporting conventions: Windows/WSL and some BSDs
    # say AMD64; Linux says aarch64 where macOS says arm64.
    if machine in ("x86_64", "amd64", "AMD64"):
        machine = "x86_64"
    elif machine in ("arm64", "aarch64", "ARM64"):
        machine = "arm64"
    return system, machine


def release_for(system=None, machine=None):
    """The pinned row for this host, with its URL filled in, or None."""
    row = RELEASES.get(host_platform(system, machine))
    if row is None:
        return None
    return dict(row, url=f"{BASE_URL}{row['dir']}/agy-acp-server-{row['release']}.zip")


def platform_problem():
    """Why this host cannot run any pinned release, or None. See RELEASES."""
    if release_for() is not None:
        return None
    system, machine = platform.system(), platform.machine()
    pinned = ", ".join(f"{s} {m}" for s, m in sorted(RELEASES))
    return (f"there is no pinned Antigravity ACP release for {system} "
            f"{machine}; the pinned platforms are {pinned}. Installing "
            f"another platform's build here would put a binary on disk that "
            f"cannot execute, and the lane would then report as available. "
            f"Refusing.\n"
            f"  If a build for this platform now exists, pinning it is an "
            f"operator decision: add a row to RELEASES in this file with the "
            f"real archive and its verified digest.")


def auth_type(settings=None):
    """The auth.type the server will use, or None when none is selected."""
    try:
        data = json.loads(Path(settings or SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    auth = data.get("auth") if isinstance(data, dict) else None
    value = auth.get("type") if isinstance(auth, dict) else None
    return value if isinstance(value, str) and value else None


def auth_problem(settings=None):
    """Why a session would be refused for want of an auth method, or None."""
    settings = settings or SETTINGS
    if auth_type(settings):
        return None
    return (f"no sign-in method selected in {settings} — run "
            f"`python3 install_antigravity_acp.py --install` to select "
            f"{AUTH_TYPE} (your Google login)")


def select_auth(settings=None):
    """Select AUTH_TYPE where nothing is selected. A choice already made —
    including an API key — is the operator's and is left alone, as is a
    file that is not a JSON object (it is not ours to rewrite)."""
    settings = Path(settings or SETTINGS)
    current = auth_type(settings)
    if current:
        return f"sign-in method already selected: {current}"
    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            raise RuntimeError(f"{settings} is not a JSON object; set auth.type "
                               f"to {AUTH_TYPE} in it by hand")
    auth = data.get("auth")
    data["auth"] = dict(auth, type=AUTH_TYPE) if isinstance(auth, dict) else {"type": AUTH_TYPE}
    settings.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = settings.with_name(settings.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings)
    return f"selected sign-in method {AUTH_TYPE}: {settings}"


def install(destination=RUNTIME, settings=None):
    """Download, verify and install if absent, then make sure a sign-in
    method is selected. Existing runtime is untouched."""
    destination = Path(destination)
    if installed_ok(destination):
        return f"already installed: {destination}\n{select_auth(settings)}"
    row = release_for()
    if row is None:
        raise RuntimeError(platform_problem())
    if destination.exists():
        raise RuntimeError(f"refusing to replace incomplete runtime: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # BESIDE the destination, never /tmp. The last step is os.replace, which
    # is atomic only within one filesystem; this pinned "/tmp" and on a Linux
    # box whose /tmp is tmpfs (Omarchy, 2026-09-27) it failed EXDEV — "Invalid
    # cross-device link" — after the download and the SHA check had both
    # passed. Same directory also keeps a 1.5 GB archive out of RAM.
    with tempfile.TemporaryDirectory(prefix=".corral-antigravity-acp-",
                                     dir=destination.parent) as td:
        extract, _ = fetch(row, td, row["sha256"])
        os.replace(extract, destination)
    return f"installed {row['release']}: {destination}\n{select_auth(settings)}"


def fetch(row, workdir, expect_sha):
    """Download row's archive into workdir and extract the expected files.
    Returns (extract_dir, sha256). With expect_sha None the digest is only
    measured — trust-on-first-download, for `lanes update` staging a release
    nobody has pinned yet; the caller writes it into RELEASES on green."""
    archive = Path(workdir) / "release.zip"
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            download(row["url"], archive)
            break
        except ShortDownload:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
    got = sha256(archive)
    if expect_sha is not None and got != expect_sha:
        raise RuntimeError(f"archive SHA-256 mismatch: got {got}, expected {expect_sha}")
    extract = Path(workdir) / "extract"
    with zipfile.ZipFile(archive) as zf:
        if expect_sha is None and zf.testzip() is not None:
            raise RuntimeError("archive failed its zip integrity test")
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
    archive.unlink()
    return extract, got


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
        if ok and auth_problem():
            print(f"installed, but NOT SIGNED IN — {auth_problem()}", flush=True)
            return 1
        print("installed" if ok else f"missing ({problem})" if problem
              else "missing", flush=True)
        return 0 if ok else 1
    parser.error("choose --install or --check")


if __name__ == "__main__":
    raise SystemExit(main())
