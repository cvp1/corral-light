#!/usr/bin/env python3
"""Resolve every pinned artifact in release-manifest.json to a digest.

WHY A SEPARATE STEP
    The Dockerfile installs ONLY what the manifest names and fails on a
    checksum mismatch (T-IMG-2). Something has to produce those checksums,
    and it must not be the build itself: a build that trusts whatever it
    downloaded and writes the hash down afterwards proves nothing. `pin.py`
    is run by a person, its diff is reviewed, and only then does a build
    consume it.

    Upstreams differ in what they publish:
      - base image  : a registry digest (docker buildx imagetools inspect)
      - Node        : SHASUMS256.txt beside the tarball — we copy its line
      - Grok CLI    : no checksum published (install.sh only runs --version),
                      so the digest is pinned from our first download, the
                      same rule install_antigravity_acp.RELEASES follows
      - Antigravity : already pinned in install_antigravity_acp.RELEASES;
                      copied from there so the two can never disagree
      - Seed        : a git commit, not a tarball — the tag's commit is pinned
      - Claude Code + Codex: arrive through `npm ci` from spike/package-lock,
                      whose sha512 integrity fields are the pin

    python3 container/pin.py            # print what would change
    python3 container/pin.py --write    # rewrite the manifest

Network: fetches nodejs.org metadata, the Grok artifact (once, to hash it),
and the registry manifest. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = HERE / "release-manifest.json"
sys.path.insert(0, str(ROOT))

TIMEOUT = 60
MAX_ARTIFACT = 512 << 20          # a CLI over 512 MiB is a wrong URL, not a CLI


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return r.read(4 << 20).decode()


def sha256_url(url: str) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        while chunk := r.read(1 << 20):
            n += len(chunk)
            if n > MAX_ARTIFACT:
                raise SystemExit(f"pin: {url} exceeds {MAX_ARTIFACT} bytes; refusing")
            h.update(chunk)
    return h.hexdigest(), n


def image_digest(ref: str, platform: str) -> str:
    """The per-platform manifest digest — what `FROM name@sha256:` must name
    so a multi-arch tag can never resolve to a different architecture."""
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref, "--raw"],
        capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout
    index = json.loads(out)
    os_, arch = platform.split("/")
    for m in index.get("manifests", []):
        p = m.get("platform", {})
        if p.get("os") == os_ and p.get("architecture") == arch and not p.get("variant"):
            return m["digest"]
    raise SystemExit(f"pin: {ref} has no {platform} manifest")


def node_pin(major: int) -> dict:
    index = json.loads(fetch_text("https://nodejs.org/dist/index.json"))
    version = next(r["version"] for r in index
                   if r["version"].startswith(f"v{major}."))
    name = f"node-{version}-linux-x64.tar.xz"
    sums = fetch_text(f"https://nodejs.org/dist/{version}/SHASUMS256.txt")
    sha = next(line.split()[0] for line in sums.splitlines()
               if line.split()[-1] == name)
    return {"version": version.lstrip("v"),
            "url": f"https://nodejs.org/dist/{version}/{name}",
            "sha256": sha}


def antigravity_pin() -> dict:
    import install_antigravity_acp as agy
    row = agy.RELEASES[("Linux", "x86_64")]
    return {"release": row["release"],
            "url": f"{agy.BASE_URL}{row['dir']}/agy-acp-server-{row['release']}.zip",
            "sha256": row["sha256"]}


def seed_pin(tag: str) -> dict:
    out = subprocess.run(
        ["git", "ls-remote", "https://github.com/cvp1/ai-os-seed",
         f"refs/tags/{tag}^{{}}", f"refs/tags/{tag}"],
        capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout
    refs = dict(reversed(line.split("\t")) for line in out.splitlines())
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit:
        raise SystemExit(f"pin: ai-os-seed has no tag {tag}")
    return {"tag": tag, "repo": "https://github.com/cvp1/ai-os-seed",
            "commit": commit}


def resolve(m: dict) -> dict:
    new = json.loads(json.dumps(m))
    base = new["base"]
    base["digest"] = image_digest(base["ref"], new["platform"])
    new["node"] = node_pin(int(new["node"]["major"])) | {"major": new["node"]["major"]}
    g = new["lanes"]["grok"]["artifact"]
    url = f"https://x.ai/cli/grok-{g['version']}-linux-x86_64"
    sha, size = sha256_url(url)
    g.update(url=url, sha256=sha, bytes=size)
    new["lanes"]["antigravity"]["artifact"] = antigravity_pin()
    new["seed"] = seed_pin(new["seed"]["tag"])
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    m = json.loads(MANIFEST.read_text())
    new = resolve(m)
    text = json.dumps(new, indent=2) + "\n"
    if a.write:
        MANIFEST.write_text(text)
        print(f"pin: wrote {MANIFEST}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
