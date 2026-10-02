#!/usr/bin/env python3
"""corral-light doctor — which lanes can start here, and what to do about it.

    python3 doctor.py
"""
from __future__ import annotations

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
    return out


def main(argv=None):
    for line in report():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
