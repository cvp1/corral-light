#!/usr/bin/env python3
"""corral-light doctor — which lanes can start here, and what to do about it.

WHY THIS IS A MODULE AND NOT FOUR LINES OF BASH
    `doctor` used to be a python -c inside the `corral-light` shell script. It
    printed a lane list, and for the two npm-installed adapters it printed
    `not installed: <path>/spike/node_modules/.bin/claude-agent-acp` -- a true
    sentence with the wrong reading. The path looks like a broken install; it
    is actually the one SETUP STEP the README never mentioned. A first-time
    reader clones, runs doctor, sees the flagship lane refuse with a missing
    file, and has nothing telling them `npm install` is what produces it.

    A verb that diagnoses is worth testing, and a python -c in a case
    statement cannot be. This file is the same output plus the next action,
    and `test_corral_light.py` runs it against a copy of the tree with
    `spike/node_modules` removed.

    python3 doctor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The npm package that produces the two vendor adapters. `spike/` has its own
# package.json; this is the step between `git clone` and a working Claude or
# ChatGPT lane, and it is gitignored so no clone ever arrives with it.
NPM_DIR = "spike"
NPM_MARKER = "node_modules"


def npm_problem(root=None):
    """The missing `npm install`, named, or None.

    Checked by the DIRECTORY rather than by either adapter's path: both the
    Claude and the ChatGPT lane come out of it, so one line covers both, and
    a half-finished install (the directory there, one adapter missing) is a
    different problem that the lane's own `not installed: <path>` already
    states accurately.
    """
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


# ── container section (WS3 step 4) — reports only, never refuses to start ──
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
    # The env note every lane carries identically (an ambient vendor key that
    # is being ignored). Read off the first lane, as it always was.
    env = (agents[0].get("envNote") if agents else None)
    if env:
        notes.append(env)
    for n in notes:
        out.append("")
        out.append("  !   " + n)
    out += container_report(root=root)
    return out


def main(argv=None):
    for line in report():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
