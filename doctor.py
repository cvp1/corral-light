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
    return out


def main(argv=None):
    for line in report():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
