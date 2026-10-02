#!/usr/bin/python3
"""watch — the outside observer for a Corral Light hub. Pages; never restarts.

    corral-light watch [--url URL] [--stale SECONDS] [--json]

Judges /health and its `tick_age_s` from outside the hub. On failure it
writes STATE/DEAD and shows a desktop notification, only when the reason
changes; it never restarts the hub, since that kills every live pane's agent.
When healthy again, DEAD is removed.

Exit: 0 healthy, 2 paged (DEAD written), 3 bad arguments.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import notify                                           # noqa: E402

STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
DEFAULT_URL = os.environ.get("CORRAL_LIGHT_URL") or \
    f"http://127.0.0.1:{os.environ.get('CORRAL_LIGHT_PORT', '8098')}"
PROBE_TIMEOUT_S = 10
TICK_STALE_S = 120          # the observer ticks every 5 s (hub.TICK_S)
BOOT_GRACE_S = 60           # tick_age_s is -1 until the first tick; after this
                            # long since the hub wrote its pidfile, -1 is a fault


def hub_process():
    """(alive, pid, why) from STATE/hub.pid, which serve() writes at bind."""
    f = STATE / "hub.pid"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        pid = int(rec["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None, "no hub.pid (the hub has never started with this STATE)"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, pid, f"hub process {pid} is gone"
    except PermissionError:
        pass                                    # exists, not ours to signal
    try:
        from corral_core.acp import process_start_token
        now = process_start_token(pid)
        if rec.get("start") and now and now != rec["start"]:
            return False, pid, f"pid {pid} now belongs to another process"
    except Exception:                           # noqa: BLE001
        pass
    return True, pid, f"hub process {pid} is running"


def probe(url, timeout=PROBE_TIMEOUT_S):
    """(health dict | None, error | None)."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health",
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {getattr(e, 'reason', e)}"


def judge(health, err, proc, stale_s=TICK_STALE_S, now=None):
    """(ok, reason). Pure, so the rules are testable without a hub."""
    alive, pid, pwhy = proc
    if health is None:
        if alive:
            return False, f"the hub is running but /health does not answer ({err})"
        return False, f"the hub is down: {pwhy}; /health: {err}"
    if health.get("service") != "corral-light":
        return False, f"something else answers on this port: {health.get('service')!r}"
    age = health.get("tick_age_s")
    if isinstance(age, (int, float)) and age > stale_s:
        return False, (f"the hub answers but its observer has not ticked for "
                       f"{int(age)}s (pane liveness is no longer being checked)")
    if age == -1:
        started = _pidfile_age(now)
        if started is not None and started > BOOT_GRACE_S:
            return False, "the hub answers but its observer never ticked"
    return True, "healthy"


def _pidfile_age(now=None):
    try:
        return (now or time.time()) - (STATE / "hub.pid").stat().st_mtime
    except OSError:
        return None


def page(reason, quiet=False):
    """Write DEAD; notify only when the reason is new. Returns whether a
    notification was shown."""
    dead = STATE / "DEAD"
    try:
        old = dead.read_text(encoding="utf-8").split("\n", 1)[0]
    except OSError:
        old = None
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = dead.with_name("DEAD.tmp")
    tmp.write_text(f"{reason}\nwritten {time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                   f"by corral-light watch (it never restarts the hub)\n",
                   encoding="utf-8")
    os.replace(tmp, dead)
    if old == reason:
        return False                            # same news; said already
    if not quiet:
        print(f"corral-light watch: PAGE — {reason}", file=sys.stderr, flush=True)
    shown, _why = notify.desktop("Corral Light needs attention", reason)
    return shown


def clear():
    dead = STATE / "DEAD"
    if dead.exists():
        try:
            dead.unlink()
            print("corral-light watch: healthy again; cleared DEAD", flush=True)
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corral-light watch",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--stale", type=int, default=TICK_STALE_S)
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 3
    health, err = probe(a.url)
    proc = hub_process()
    ok, reason = judge(health, err, proc, a.stale)
    if a.json:
        print(json.dumps({"ok": ok, "reason": reason, "health": health,
                          "process": {"alive": proc[0], "pid": proc[1],
                                      "why": proc[2]}}), flush=True)
    if ok:
        clear()
        return 0
    page(reason, quiet=a.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())
