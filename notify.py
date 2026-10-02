#!/usr/bin/python3
"""notify — a local desktop notification, or nothing. Stdlib only, no network.

Silent during quiet hours (21:00–05:00 local); never raises.
"""
import shutil
import subprocess
import sys
from datetime import datetime

QUIET_START_H = 21          # quiet from 21:00 ...
QUIET_END_H = 5             # ... until 05:00, local time
NOTIFY_TIMEOUT_S = 5        # a hung notifier must not hang the caller
MAX_TITLE = 80
MAX_BODY = 240


def quiet_now(now=None):
    h = (now or datetime.now()).hour
    return h >= QUIET_START_H or h < QUIET_END_H


def _argv(title, body):
    if sys.platform == "darwin" and shutil.which("osascript"):
        def q(s):
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return ["osascript", "-e",
                f"display notification {q(body)} with title {q(title)}"]
    if shutil.which("notify-send"):
        return ["notify-send", "--app-name=corral-light", title, body]
    return None


def desktop(title, body, now=None, force=False):
    """Show one notification. Returns (shown, why) — never raises.

    `force` skips quiet hours; only tests use it.
    """
    if not force and quiet_now(now):
        return False, "quiet hours (21:00–05:00)"
    title = " ".join(str(title).split())[:MAX_TITLE]
    body = " ".join(str(body).split())[:MAX_BODY]
    argv = _argv(title, body)
    if argv is None:
        return False, "no notifier on this host (notify-send / osascript)"
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=NOTIFY_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"notifier failed: {e}"
    if r.returncode != 0:
        return False, f"notifier exited {r.returncode}: {r.stderr.strip()[:120]}"
    return True, "shown"
