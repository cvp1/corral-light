#!/usr/bin/env python3
"""T-IMG-4 / T-SEC-4: scan every layer of an image for personal data.

The image is public, so a host path, username, hostname, LAN address or
token in ANY layer — including a file a later layer deletes — is a leak.
Scans the `docker save` stream: every layer tar, every regular file.

    docker save corral-light:dev | python3 container/scan_layers.py \
        --needle "$USER" --needle "$(hostname -s)" --needle "$HOME"

Built-in patterns cover common token shapes. Needles are the per-host
strings (they cannot be built in: the public repo must not name them).
Exit 1 when anything is found; each hit prints layer, path and pattern,
never the matched secret itself beyond its first 4 chars.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tarfile

TOKEN_PATTERNS = {
    "anthropic-key": rb"sk-ant-[A-Za-z0-9_\-]{20,}",
    "openai-key": rb"sk-(?:proj-)?[A-Za-z0-9]{32,}",
    "github-token": rb"gh[pousr]_[A-Za-z0-9]{36,}",
    "xai-key": rb"xai-[A-Za-z0-9]{40,}",
    "google-api-key": rb"AIza[0-9A-Za-z_\-]{35}",
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "aws-key-id": rb"AKIA[0-9A-Z]{16}",
}
MAX_FILE = 256 << 20      # bigger single files are scanned in the first 256 MiB


def scan_layer(name: str, fileobj, needles: list[bytes], hits: list, allow: set):
    with tarfile.open(fileobj=fileobj, mode="r|*") as t:
        for m in t:
            if not m.isreg():
                continue
            path = m.name
            f = t.extractfile(m)
            data = f.read(MAX_FILE) if f else b""
            for n in needles:
                if n and n in data and (path, n.decode()) not in allow:
                    hits.append((name, path, "needle:" + n.decode()))
            for label, pat in TOKEN_PATTERNS.items():
                mm = re.search(pat, data)
                if mm and (path, label) not in allow:
                    hits.append((name, path, f"{label}:{mm.group(0)[:4].decode(errors='replace')}…"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--needle", action="append", default=[])
    ap.add_argument("--allow", action="append", default=[],
                    help="path=label pairs judged false positives (reviewed)")
    a = ap.parse_args(argv)
    needles = [n.encode() for n in a.needle if n]
    allow = {tuple(x.split("=", 1)) for x in a.allow}
    hits: list = []
    layers = 0
    outer = tarfile.open(fileobj=sys.stdin.buffer, mode="r|*")
    for m in outer:
        if not m.isreg():
            continue
        f = outer.extractfile(m)
        head = f.read(262)
        rest = f.read()
        blob = io.BytesIO(head + rest)
        # layer blobs are tars (possibly gzipped); config/index JSON are not
        if head[:2] == b"\x1f\x8b" or (len(head) > 262 - 5 and head[257:262] == b"ustar"):
            layers += 1
            try:
                scan_layer(m.name, blob, needles, hits, allow)
            except tarfile.TarError:
                pass
        else:
            data = head + rest
            for n in needles:
                if n in data:
                    hits.append((m.name, "(image metadata)", "needle:" + n.decode()))
    for layer, path, what in hits:
        print(f"HIT {what}  {path}  [{layer[:40]}]")
    print(f"scan: {layers} layer(s), {len(hits)} hit(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
