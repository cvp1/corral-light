#!/usr/bin/python3
"""folder_diff — what changed since HEAD in a plain pane's folder, read only.

A pane without its own branch works in your repo itself, so the agent may
have written that repo's `.git/config` and `.gitattributes`. Git porcelain run
there by the hub (`diff`, `status`, `add`) would honour them: a clean filter,
a textconv, an external diff or `core.fsmonitor` is a program that would then
run as you, outside the agent's permissions. So this never runs porcelain in
the folder. It uses plumbing that runs no configured program:

- `rev-parse`, `ls-tree`, `cat-file` (no `--filters`/`--textconv`): read objects;
- `ls-files --cached --others --exclude-standard`, with `core.fsmonitor` and
  `core.untrackedCache` forced off on the command line (which wins over the
  repo's own config): names tracked and untracked, not ignored, files;

and hashes and diffs the working files itself. "Changed" means the bytes on
disk differ from HEAD's blob: a repo that rewrites line endings or runs clean
filters shows those files as changed, by design.

Bounded: files scanned, bytes hashed, files shown, bytes of patch returned.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import stat
from pathlib import Path

import worktrees

# Forced on every call, after the subcommand's own -c can be read: a
# command-line -c overrides the repository's config for that key.
SAFE = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.hooksPath=/dev/null"]
SCAN_MAX_FILES = 20_000          # tracked + untracked paths looked at
HASH_MAX_BYTES = 256 << 20       # bytes read from disk to find what changed
FILE_MAX_BYTES = 512 << 10       # a file over this is listed, not diffed
SHOW_MAX_FILES = 200
PATCH_MAX_BYTES = 400_000        # of unified diff text, all files together
TIMEOUT_S = 15


class Refused(ValueError):
    pass


def _git(args, cwd, **kw):
    return worktrees.git([*SAFE, *args], cwd=cwd, timeout=TIMEOUT_S,
                         optional_locks_off=True, **kw)


def _blob_id(data, algo):
    h = hashlib.new(algo)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _is_binary(data):
    return b"\0" in data[:8192]


def _read_work(path):
    """(bytes, kind) of the working file: its content, a symlink's target, or None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, None
    if stat.S_ISLNK(st.st_mode):
        return os.fsencode(os.readlink(path)), "link"
    if not stat.S_ISREG(st.st_mode):
        return None, None
    with open(path, "rb") as fh:
        return fh.read(), "file"


def changes(cwd):
    """{repo, top, head, branch, files: [{path, status, binary, too_big, patch}],
    counts, truncated}. `status`: M modified, A untracked (new), D deleted."""
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise Refused(f"not a folder: {cwd}")
    top_r = _git(["rev-parse", "--show-toplevel"], cwd, check=False)
    if top_r.rc != 0:
        return {"repo": False, "files": [], "truncated": False}
    top = Path(top_r.out.decode().strip())
    algo = (_git(["rev-parse", "--show-object-format"], top, check=False).out.decode().strip()
            or "sha1")
    if algo not in ("sha1", "sha256"):
        raise Refused(f"unknown object format {algo!r}")
    head_r = _git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], top, check=False)
    head = head_r.out.decode().strip() if head_r.rc == 0 else None
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], top, check=False).out.decode().strip()
    rel = os.path.relpath(cwd.resolve(), top.resolve())
    prefix = "" if rel == "." else rel.rstrip("/") + "/"

    # HEAD's blobs, under this folder only.
    at_head = {}
    if head:
        out = _git(["ls-tree", "-r", "-z", "--full-tree", head], top, max_out=64 << 20).out
        for rec in out.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            p = path.decode("utf-8", "replace")
            if kind == "blob" and p.startswith(prefix):
                at_head[p] = (mode, oid)

    # Paths on disk that git would track (tracked + untracked, not ignored).
    out = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--full-name",
                "--", "." if not prefix else prefix], top, max_out=64 << 20).out
    on_disk = [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]
    names = sorted(set(at_head) | set(on_disk))
    truncated = False
    if len(names) > SCAN_MAX_FILES:
        names, truncated = names[:SCAN_MAX_FILES], True

    changed, hashed = [], 0
    for p in names:
        data, kind = _read_work(top / p)
        if p in at_head:
            if data is None:
                changed.append((p, "D", None))
                continue
            hashed += len(data)
            if hashed > HASH_MAX_BYTES:
                truncated = True
                break
            if _blob_id(data, algo) != at_head[p][1]:
                changed.append((p, "M", data))
        elif data is not None:
            changed.append((p, "A", data))

    shown = changed[:SHOW_MAX_FILES]
    truncated = truncated or len(changed) > len(shown)
    # HEAD's side of what is shown, read without filters in one batch.
    old = {}
    want = [at_head[p][1] for p, st, _ in shown if st in ("M", "D")]
    if want:
        raw = _git(["cat-file", "--batch"], top, input=("\n".join(want) + "\n").encode(),
                   max_out=256 << 20).out
        i = 0
        while i < len(raw):
            nl = raw.index(b"\n", i)
            parts = raw[i:nl].decode().split()
            if len(parts) != 3:                  # "<oid> missing": nothing to show
                i = nl + 1
                continue
            oid, _kind, size = parts
            size = int(size)
            old[oid] = raw[nl + 1:nl + 1 + size]
            i = nl + 1 + size + 1
    files, budget = [], PATCH_MAX_BYTES
    for p, st, data in shown:
        before = old.get(at_head[p][1], b"") if p in at_head else b""
        after = data or b""
        f = {"path": p[len(prefix):] if prefix else p, "status": st,
             "binary": _is_binary(before) or _is_binary(after),
             "too_big": max(len(before), len(after)) > FILE_MAX_BYTES, "patch": None}
        if not f["binary"] and not f["too_big"] and budget > 0:
            a = before.decode("utf-8", "replace").splitlines(keepends=True)
            b = after.decode("utf-8", "replace").splitlines(keepends=True)
            patch = "".join(difflib.unified_diff(a, b, "a/" + f["path"], "b/" + f["path"], n=3))
            if len(patch) > budget:
                patch, truncated = patch[:budget] + "\n… (cut: the rest is past the size cap)\n", True
            budget -= len(patch)
            f["patch"] = patch
        files.append(f)
    return {"repo": True, "top": str(top), "head": head, "branch": branch,
            "files": files, "counts": {"changed": len(changed), "shown": len(files)},
            "truncated": truncated}
