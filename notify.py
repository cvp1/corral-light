#!/usr/bin/python3
"""notify — a desktop notification, or nothing. Stdlib only, no network.

Used by the hub (a pane needs you and nobody has seen it) and by the outside
watcher (`corral-light watch`, the hub itself is down). Resilience review v2,
P0-d' and P0-e' (Astra and Grok 2026-09-28).

WHAT IT WILL NOT DO
    - Call a network service. The review's first draft offered an optional
      ntfy URL; a local laptop app paging through a third-party relay is a
      data path nobody asked for, and "no network calls" is the ruling.
    - Speak during quiet hours, 21:00–05:00 local (`security()` below shows a
      silent banner then, and never speaks). The operator's sleep is
      an invariant, not a preference; a page that matters at 02:00 is still
      on disk (the watcher's DEAD file, the pane's rail) at 05:00.
    - Raise. A notifier that can crash its caller is worse than none.

SECURITY NOTICES (DESIGN-6 S9)
    `security()` is the one exception to quiet hours, and it is silent. A
    break-glass pairing, a key enrolled or removed, or a pairing-policy
    change is shown as a banner at ANY hour, because the operator should see
    it the next time they look at the screen, and it changes who can get in.
    It never makes a sound (no `sound name`, and notify-send is asked to
    suppress one) and, like everything here, it calls no network service, so
    nothing pages a phone from 21:00 to 05:00 or at any other time. The
    audit record is the caller's ledger line, written before the banner.
"""
import shutil
import subprocess
import sys
from datetime import datetime

QUIET_START_H = 21          # quiet from 21:00 ...
QUIET_END_H = 5             # ... until 05:00, local time
NOTIFY_TIMEOUT_S = 5        # one notifier call; a hung osascript must not hang us
MAX_TITLE = 80              # notification centres truncate anyway; keep it readable
MAX_BODY = 240


def quiet_now(now=None):
    h = (now or datetime.now()).hour
    return h >= QUIET_START_H or h < QUIET_END_H


def _argv(title, body, silent=False):
    if sys.platform == "darwin" and shutil.which("osascript"):
        def q(s):
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        # No `sound name` clause: `display notification` is silent without one.
        return ["osascript", "-e",
                f"display notification {q(body)} with title {q(title)}"]
    if shutil.which("notify-send"):
        return (["notify-send", "--app-name=corral-light"]
                + (["--hint=boolean:suppress-sound:true"] if silent else [])
                + [title, body])
    return None


def desktop(title, body, now=None, force=False):
    """Show one notification. Returns (shown, why) — never raises.

    `force` skips quiet hours; only tests use it.
    """
    if not force and quiet_now(now):
        return False, "quiet hours (21:00–05:00)"
    return _show(title, body)


def security(title, body, now=None):
    """A security notice (S9): a SILENT banner at any hour, quiet hours or
    not. Returns (shown, why) and never raises. `now` is accepted so a caller
    passes its clock; the hour changes nothing here, which is the point."""
    return _show(title, body, silent=True)


def _show(title, body, silent=False):
    title = " ".join(str(title).split())[:MAX_TITLE]
    body = " ".join(str(body).split())[:MAX_BODY]
    argv = _argv(title, body, silent)
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
