#!/usr/bin/env python3
"""Build-time fetcher: download every manifest artifact and verify it.

Runs INSIDE the image build (Dockerfile `fetch` stage). It installs nothing
the manifest does not name, and a digest mismatch is fatal (T-IMG-2): the
build stops before any unverified byte reaches a layer.

    python3 fetch.py --manifest release-manifest.json --out /dl

Writes:
    /dl/node.tar.xz             Node, verified against the manifest sha256
    /dl/grok                    Grok CLI linux-x86_64, verified, mode 755
    /dl/antigravity/            agy_acp_server.par + localharness_external
    /dl/aios-seed/              Seed checkout at the pinned commit (no .git)

Stdlib only; the base image has Python and nothing else we can rely on.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

TIMEOUT = 120
AGY_FILES = ("agy_acp_server.par", "localharness_external")


class DigestMismatch(SystemExit):
    pass


def download(url: str, dest: Path, sha256: str) -> None:
    if not sha256 or len(sha256) != 64:
        raise SystemExit(f"fetch: no sha256 pinned for {url}; run container/pin.py")
    h = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r, tmp.open("wb") as out:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            out.write(chunk)
    got = h.hexdigest()
    if got != sha256:
        tmp.unlink()
        raise DigestMismatch(f"fetch: CHECKSUM MISMATCH for {url}\n"
                             f"  manifest {sha256}\n  download {got}")
    tmp.replace(dest)
    print(f"fetch: ok {dest.name} {got[:16]}…", flush=True)


def seed(m: dict, out: Path) -> None:
    s = m["seed"]
    dest = out / "aios-seed"
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--depth=1",
                    s["repo"], s["commit"]], check=True, timeout=TIMEOUT)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"], check=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != s["commit"]:
        raise DigestMismatch(f"fetch: seed HEAD {head} != pinned {s['commit']}")
    shutil.rmtree(dest / ".git")
    print(f"fetch: ok aios-seed {s['tag']} {head[:12]}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    m = json.loads(Path(a.manifest).read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if m.get("platform") != "linux/amd64":
        raise SystemExit(f"fetch: manifest platform {m.get('platform')!r}; "
                         f"this build is linux/amd64 only")

    n = m["node"]
    download(n["url"], out / "node.tar.xz", n["sha256"])

    g = m["lanes"]["grok"]["artifact"]
    download(g["url"], out / "grok", g["sha256"])
    os.chmod(out / "grok", 0o755)

    agy = m["lanes"]["antigravity"]["artifact"]
    download(agy["url"], out / "antigravity.zip", agy["sha256"])
    adir = out / "antigravity"
    adir.mkdir(exist_ok=True)
    with zipfile.ZipFile(out / "antigravity.zip") as zf:
        names = set(zf.namelist())
        for name in AGY_FILES:          # only the expected root files, never
            if name not in names:       # arbitrary archive paths
                raise SystemExit(f"fetch: antigravity archive lacks {name}")
            (adir / name).write_bytes(zf.read(name))
            os.chmod(adir / name, 0o755)
    (out / "antigravity.zip").unlink()

    seed(m, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
