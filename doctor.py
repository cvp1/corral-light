#!/usr/bin/env python3
"""corral-light doctor — which lanes can start here, and what to do about it.

    python3 doctor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The gitignored npm package that provides the Claude and ChatGPT adapters.
NPM_DIR = "spike"
NPM_MARKER = "node_modules"


def npm_problem(root=None):
    """The missing `npm install`, named, or None. Checks the directory; a
    partial install is reported by the lane itself."""
    spike = Path(root or HERE) / NPM_DIR
    if (spike / NPM_MARKER).exists():
        return None
    if not (spike / "package.json").is_file():
        return (f"{spike} has no package.json — this checkout is incomplete; "
                f"the Claude and ChatGPT lanes come from a package installed "
                f"there.")
    return (f"the Claude and ChatGPT adapters are not installed. They are an "
            f"npm package, gitignored, so no clone arrives with them:\n"
            f"      cd {spike} && npm install\n"
            f"    Until then those two lanes refuse with the path they looked "
            f"at, which reads like a broken install rather than a setup step "
            f"nobody mentioned.")


def _size(path):
    """Bytes under `path`, never following symlinks."""
    total = 0
    for d, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total


def _human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def claude_leftovers(entries):
    """~/.claude/projects folders named for worktrees that are purged or missing.

    Claude keys its transcripts by cwd. Where panes share the real ~/.claude
    (macOS: docs/worktree-plan-macos.md M4) every worktree path leaves one,
    and reconcile and purge never touch it.
    """
    import re
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return []
    out = []
    for e in entries:
        if e.get("phase") not in ("purged", "missing") or not e.get("path"):
            continue
        for p in {e["path"], os.path.join(e["path"], e.get("subdir") or "")}:
            slug = re.sub(r"[^A-Za-z0-9]", "-", p.rstrip("/"))
            if (projects / slug).is_dir() and slug not in out:
                out.append(slug)
    return out


def worktree_lines(registry=None):
    """Own branches: git version, where worktrees live, what is in trash.
    Reads only; repairs nothing."""
    import worktrees as wt
    ok, bad = "  ok  ", "  --  "
    out = ["", "  own branches"]
    try:
        v = wt.git_version()
    except Exception as e:                       # noqa: BLE001 — a line, never a crash
        out.append(f"{bad}git: could not run it ({e})")
        v = None
    if v is not None:
        good = tuple(v[:2]) >= wt.MIN_GIT
        out.append((ok if good else bad) + f"git {'.'.join(map(str, v))} ({wt.GIT_BIN})"
                   + ("" if good else f" — own branches needs {'.'.join(map(str, wt.MIN_GIT))} or newer"))
    root = wt.worktree_root()
    fs = wt._fstype(root) or "an unknown filesystem"
    shown = str(root).replace(str(Path.home()), "~", 1)
    if fs == "tmpfs":
        out.append(f"{bad}worktree root {shown} on tmpfs — memory, gone on reboot, so own "
                   f"branches are refused; set CORRAL_LIGHT_WORKTREES to a folder on disk")
    else:
        out.append(f"{ok}worktree root {shown} on {fs}")
    reg = registry or wt.Registry()
    entries = reg.all(include_unreadable=True)
    if not entries:
        out.append(f"{ok}no own-branch worktrees yet")
    else:
        phases = {}
        for e in entries:
            k = "unreadable" if e.get("unreadable") else e.get("phase", "?")
            phases[k] = phases.get(k, 0) + 1
        parts = ", ".join(f"{n} {k}" for k, n in sorted(phases.items()))
        line = f"{ok}{len(entries)} worktree{'s' if len(entries) != 1 else ''} ({parts})"
        trash = wt.trash_dir()
        if phases.get("trashed") and trash.is_dir():
            line += (f"; trash holds {_human(_size(trash))} — "
                     f"`corral-light worktrees purge <id>` frees it")
        out.append(line)
    for e in entries:
        if any(o.get("state") == "unknown" for o in e.get("ops") or []):
            out.append(f"  !   {e['id']}: an action has an unknown outcome — "
                       f"corral-light worktrees resolve {e['id']}")
    left = claude_leftovers(entries)
    if left:
        out.append(f"  !   {len(left)} Claude transcript folder{'s' if len(left) != 1 else ''} in "
                   f"~/.claude/projects belong{'s' if len(left) == 1 else ''} to worktrees that are gone (listed, never "
                   f"deleted): " + ", ".join(left[:5]) + (" …" if len(left) > 5 else ""))
    strays = wt.orphans(registry=reg)
    if strays:
        out.append(f"  !   {len(strays)} orphan{'s' if len(strays) != 1 else ''} under the "
                   f"worktree root — `corral-light worktrees` lists them")
    return out


def report(root=None, agents=None):
    """The lines `doctor` prints. Returns a list of strings so a test can read
    them; main() is the only thing that writes to stdout."""
    root = Path(root or HERE)
    if agents is None:
        import sessions
        agents = sessions.available_agents()
    out = []
    for a in agents:
        mark = "  ok  " if a["available"] else "  --  "
        out.append(mark + a["label"] + (" — " + a["why"] if a.get("why") else ""))

    notes = []
    npm = npm_problem(root)
    if npm:
        notes.append(npm)
    # The env note is identical across lanes; read it off the first.
    env = (agents[0].get("envNote") if agents else None)
    if env:
        notes.append(env)
    for n in notes:
        out.append("")
        out.append("  !   " + n)
    out.extend(worktree_lines())
    return out


def main(argv=None):
    for line in report():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
