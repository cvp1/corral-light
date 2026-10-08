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


# Container section: reports only, never refuses to start.
RUN_REPORT = Path("/run/corral/entrypoint.json")
ELF_MACHINE = {0x3E: "x86-64", 0xB7: "aarch64", 0x28: "arm", 0x03: "x86"}


def elf_arch(path):
    """The ELF machine of a file, 'script' for a #! file, else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return None
    if head[:2] == b"#!":
        return "script"
    if head[:4] != b"\x7fELF" or len(head) < 20:
        return "not-elf"
    return ELF_MACHINE.get(int.from_bytes(head[18:20], "little"), "unknown")


def lane_binaries(root=None):
    """(label, path) for each lane's real executable in the image."""
    import glob
    import os
    nm = Path(root or HERE) / NPM_DIR / NPM_MARKER
    # The vendor dir is the Rust target triple (x86_64-unknown-linux-musl in
    # 0.159.x); glob it so a triple change does not read as a missing binary.
    codex = sorted(glob.glob(str(nm / "@openai/codex-linux-x64/vendor/*/bin/codex")))
    return [
        ("claude", nm / "@anthropic-ai/claude-agent-sdk-linux-x64/claude"),
        ("codex", Path(codex[0]) if codex else nm / "@openai/codex-linux-x64"),
        ("grok", Path(os.environ.get("CORRAL_GROK_BIN", "/opt/corral/bin/grok"))),
        ("antigravity", Path(os.environ.get(
            "CORRAL_ANTIGRAVITY_ACP_BINARY",
            "/opt/corral/antigravity-acp/agy_acp_server.par"))),
        ("node", Path("/opt/node/bin/node")),
    ]


def container_report(run_report=RUN_REPORT, root=None):
    """Lines for `doctor` inside the container; [] outside it."""
    import json
    import os
    if os.environ.get("CORRAL_CONTAINER") != "1":
        return []
    out = ["", "  container"]
    try:
        r = json.loads(Path(run_report).read_text())
    except (OSError, ValueError) as e:
        return out + [f"  !   entrypoint report unreadable ({e}); was the "
                      f"container started through its entrypoint?"]
    i, e = r.get("identity", {}), r.get("emulation", {})
    out.append(f"  ok  identity {i.get('user')} uid={i.get('uid')} "
               f"gid={i.get('gid')} home={i.get('home')}")
    mode = e.get("mode", "?")
    out.append(f"  {'ok' if mode == 'native' else '~~'}  cpu {e.get('machine')} "
               f"emulation={mode}")
    for label, path in lane_binaries(root):
        arch = elf_arch(path)
        mark = "  ok  " if arch in ("x86-64", "script") else "  --  "
        out.append(f"{mark}{label}: {arch or 'missing'} {path}"
                   + (" (under Rosetta)" if arch == "x86-64" and mode == "rosetta" else ""))
    p = r.get("path", {})
    out.append(("  ok  " if p.get("ok") else "  !   ") + "PATH has no host bin dirs"
               + ("" if p.get("ok") else f": {p.get('host_dirs')}"))
    h = r.get("hostname", {})
    if not h.get("ok", True):
        out.append(f"  !   hostname {h.get('actual')!r} != expected {h.get('expected')!r}")
    for m in r.get("parity_map", []):
        out.append(("  ok  " if m.get("ok") else "  !   ") + "parity map "
                   + str(m.get("path") or m.get("map"))
                   + (f": {m.get('version')}" if m.get("ok") else f": {m.get('why')}"))
    for o in r.get("overlays", []):
        out.append(("  ok  " if o.get("mounted") else "  !   ") + "overlay " + o["path"])
    s = r.get("ssh", {})
    out.append(f"  {'ok' if s.get('agent') else '--'}  ssh agent "
               f"{s.get('agent') or 'none (Docker Desktop SSH agent forwarding off?)'}")
    ws = r.get("workspace")
    if ws:
        out.append(f"  {'ok' if Path(ws).is_dir() else '!!'}  workspace {ws}")
    for n in r.get("notes", []):
        out.append("  !   " + n)
    return out


def module_lines(pins=None):
    """Per installed module: pinned commit, verified, sandboxed or
    acknowledged, last run, last error (docs/finops-module-plan.md §4.6)."""
    import modules
    import module_sandbox
    pins = modules.load_pins() if pins is None else pins
    if not pins:
        return []
    sandboxed, why = module_sandbox.available()
    out = ["", "Modules" + ("" if sandboxed else f" (no sandbox here: {why})")]
    for name in sorted(pins):
        pin = pins[name]
        try:
            if pin.get("enabled"):
                modules.verify(name)
            verified = "verified" if pin.get("enabled") else "disabled"
        except modules.ModuleError as e:
            verified = str(e)
        s = modules.summary(name, modules.load_pins().get(name) or pin)
        face = "sandboxed" if sandboxed else (
            "UNSANDBOXED (acknowledged)" if pin.get("unsandboxed_ack") else "not acknowledged")
        mark = "  ok  " if s["state"] == "ok" else "  --  "
        out.append(f"{mark}{name} {str(pin.get('commit', ''))[:12]} — {verified}, {face}, "
                   f"last run {s.get('last_run_at') or 'never'}"
                   + (f"; {s['error']}" if s.get("error") else ""))
        for key, f in sorted((s.get("fetch") or {}).items()):
            fm = "  ok  " if f["state"] == "ok" else "  --  "
            out.append(f"{fm}  {name} fetcher, key {key} ({f['vendor']}): {f['state']}, "
                       f"last run {f.get('last_run_at') or 'never'}"
                       + (f"; {f['error']}" if f.get("error") else ""))
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
    out.extend(module_lines())
    out += container_report(root=root)
    return out


def main(argv=None):
    for line in report():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
