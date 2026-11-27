#!/usr/bin/python3
"""Host jobs: this machine's own scheduled jobs, beside the ones Later runs.

Where AI-OS Seed is installed, its scheduler (`scheduler/manifest.yml`, synced
to crontab or launchd) runs jobs that never open a pane. The Scheduled jobs
page lists them and can turn one off, on, or remove it.

Nothing of Seed is imported here. hostjobs_seed.py runs as a fresh, time-boxed
subprocess and does each thing Seed's own way: off and on are Seed's switch
(`observability/switches.py`: the job is skipped and its freshness check stays
quiet until the switch's date), and a removal goes through the manifest and
Seed's sync, never around them.

The Seed folder: $CORRAL_SEED_ROOT, else "seed_root" in
~/.config/corral-light/hostjobs.json, else ~/aios. No Seed, no host jobs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "hostjobs_seed.py"
LIST_TIMEOUT_S = 15
RM_TIMEOUT_S = 150
MAX_JOBS = 200


def _config_dir():
    return Path(os.environ.get("CORRAL_LIGHT_CONFIG_DIR",
                               Path.home() / ".config/corral-light"))


def seed_root():
    """The Seed install whose scheduler this hub shows, or None."""
    cands = [os.environ.get("CORRAL_SEED_ROOT") or ""]
    try:
        d = json.loads((_config_dir() / "hostjobs.json").read_text(encoding="utf-8"))
        cands.append(str(d.get("seed_root") or "") if isinstance(d, dict) else "")
    except (OSError, ValueError):
        pass
    cands.append(str(Path.home() / "aios"))
    for c in cands:
        if c:
            p = Path(c).expanduser()
            return p if (p / "scheduler" / "manifest.yml").is_file() else None
    return None


def _run(verb, *args, timeout=LIST_TIMEOUT_S):
    root = seed_root()
    if root is None:
        raise ValueError("no Seed scheduler on this machine")
    try:
        r = subprocess.run([sys.executable, str(HELPER), str(root), verb, *args],
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ValueError(f"Seed's scheduler did not answer in {timeout}s") from None
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError("Seed's scheduler gave no answer: "
                         + (r.stderr or r.stdout).strip()[-200:]) from None
    if not d.get("ok"):
        raise ValueError(d.get("error") or "Seed's scheduler refused")
    return d


def list_jobs():
    """Always a dict the page can render: no Seed and a broken Seed both say so."""
    root = seed_root()
    if root is None:
        return {"available": False, "jobs": [], "error": ""}
    try:
        d = _run("list")
    except ValueError as e:
        return {"available": True, "root": str(root), "jobs": [], "error": str(e)}
    return {"available": True, "root": str(root), "jobs": d["jobs"][:MAX_JOBS],
            "error": (f"switches unreadable: {d['switchError']}"
                      if d.get("switchError") else "")}


def set_paused(name, paused):
    return _run("off" if paused else "on", str(name or ""))


def remove(name, state_dir):
    """Remove a job from Seed's manifest and its scheduler. The entry cut out
    is kept under <state>/hostjobs-removed/, since Seed keeps no undo."""
    return _run("rm", str(name or ""), str(Path(state_dir) / "hostjobs-removed"),
                timeout=RM_TIMEOUT_S)
