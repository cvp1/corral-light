#!/usr/bin/python3
"""file_mention — the files under a pane's folder that match what you type
after `@` in its message box. Read only, bounded.

In a git repository the list is git's own (tracked plus untracked, not
ignored), from `ls-files` with `core.fsmonitor` and `core.untrackedCache`
forced off on the command line: the agent may have written the repo's config,
and fsmonitor names a program git would otherwise run (see folder_diff.py).
Outside a repository it is a bounded walk that skips hidden folders and the
usual build and dependency folders.
"""
from __future__ import annotations

import os
from pathlib import Path

import worktrees

SAFE = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.hooksPath=/dev/null"]
SCAN_MAX = 100_000       # paths looked at
WALK_MAX = 20_000        # entries a non-repo walk visits
LIMIT = 30               # suggestions returned
QUERY_MAX = 200
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}


def _repo_files(cwd):
    r = worktrees.git([*SAFE, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                      cwd=cwd, timeout=10, check=False, optional_locks_off=True,
                      max_out=32 << 20)
    if r.rc != 0:
        return None
    out = [p.decode("utf-8", "replace") for p in r.out.split(b"\0") if p]
    return out[:SCAN_MAX]


def _walk_files(cwd):
    out, seen = [], 0
    for root, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for f in sorted(files):
            seen += 1
            if seen > WALK_MAX:
                return out
            out.append(os.path.relpath(os.path.join(root, f), cwd))
    return out


def rank(paths, q, limit=LIMIT):
    """Case-insensitive: a file name that starts with the query first, then one
    that contains it, then a path that contains it; shorter paths first."""
    q = (q or "").lower()
    scored = []
    for p in paths:
        low = p.lower()
        base = low.rsplit("/", 1)[-1]
        if not q:
            s = 3
        elif base.startswith(q):
            s = 0
        elif q in base:
            s = 1
        elif q in low:
            s = 2
        else:
            continue
        scored.append((s, len(p), p))
    scored.sort()
    return [p for _, _, p in scored[:limit]]


def suggest(cwd, q):
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise ValueError(f"not a folder: {cwd}")
    q = str(q or "")[:QUERY_MAX]
    files = _repo_files(cwd)
    if files is None:
        files = _walk_files(cwd)
    return {"files": rank(files, q)}
