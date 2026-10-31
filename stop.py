#!/usr/bin/env python3
"""stop — stop the hub's service AND every agent its keepers still hold.

A hub restart must not end the agents (that is what the keepers are for),
and the hub cannot tell a stop from a restart: both are SIGTERM. So a plain
`systemctl --user stop corral-light` (KillMode=process) or a launchd unload
leaves every keeper running until its orphan watch ends it, hours later.
This is the stop that means it:

    corral-light stop               stop the service, then close every keeper
    corral-light stop --keepers-only   close every keeper; leave the hub alone
                                    (only when no hub is running)

Order matters. The service goes first, so the hub lets go of its agents the
way it does on a restart; then each keeper is sent the same
`{"keeper": "close"}` the hub sends when a pane is closed, and it ends its
agent. A keeper that does not answer its socket is signalled, but only once
`ps` confirms the pid is still a keeper.py (a pid can be reused). Refused
from inside a pane: it would end the agent running it.

Start the hub again with your service manager (printed at the end).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update                                           # noqa: E402

CLOSE_WAIT_S = 10            # every keeper gone within this (TERM_WAIT_S is 5)
SOCK_TIMEOUT_S = 2
HUB_EXIT_WAIT_S = 30


class Refused(Exception):
    pass


def keepers(state=None):
    """Every keeper this install's panes record whose pid is still a keeper:
    [{pane, sock, pid, pgid}]."""
    state = Path(state or update.STATE)
    out = []
    for meta in sorted((state / "panes").glob("*/meta.json")):
        try:
            k = json.loads(meta.read_text(encoding="utf-8")).get("keeper") or {}
            pid = int(k.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        sock = str(k.get("sock") or "")
        if not pid or not sock or not is_keeper(pid):
            continue
        pgid = pid
        try:
            info = json.loads((Path(sock).parent / "keeper.json").read_text(encoding="utf-8"))
            if int(info.get("keeper_pid") or 0) == pid:
                pgid = int(info.get("pgid") or pid)
        except (OSError, ValueError, TypeError):
            pass
        out.append({"pane": meta.parent.name, "sock": sock, "pid": pid, "pgid": pgid})
    return out


def is_keeper(pid):
    """Is `pid` alive and running keeper.py (not a reused pid)?"""
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "keeper.py" in r.stdout


def close_keeper(sock_path):
    """Connect, read the hello, send close. -> the open socket when the close
    was sent, else None. Connecting replaces any hub still attached, so the
    close is ours. The caller keeps READING it until the keeper exits: a hub
    that hangs up at once can lose its close to the keeper's write failure."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(SOCK_TIMEOUT_S)
    try:
        s.connect(sock_path)
        hello = s.makefile("rb").readline(64 * 1024)
        if not hello.startswith(b"{") or b'"hello"' not in hello:
            s.close()
            return None
        s.sendall(b'{"keeper": "close"}\n')
        return s
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        return None


def _read_to_eof(s, deadline):
    """Drain what the keeper writes until it exits (EOF) or the deadline."""
    try:
        while time.monotonic() < deadline:
            try:
                if not s.recv(65536):
                    return
            except socket.timeout:
                continue
    except OSError:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass


def close_all(found, say, wait_s=CLOSE_WAIT_S):
    """Close every keeper in `found` (at once, each read until it exits);
    signal the ones that did not answer. -> the ones still running after
    `wait_s`."""
    end = time.monotonic() + wait_s
    readers = []
    for k in found:
        s = close_keeper(k["sock"])
        if s is not None:
            say(f"  pane {k['pane']}: close sent to keeper {k['pid']}")
            t = threading.Thread(target=_read_to_eof, args=(s, end), daemon=True)
            t.start()
            readers.append(t)
        elif is_keeper(k["pid"]):
            say(f"  pane {k['pane']}: keeper {k['pid']} did not answer; SIGTERM")
            try:
                os.killpg(k["pgid"], signal.SIGTERM)
            except OSError:
                pass
    left = list(found)
    while left and time.monotonic() < end:
        left = [k for k in left if is_keeper(k["pid"])]
        if left:
            time.sleep(0.2)
    for t in readers:
        t.join(0.5)
    return left


def stop_service():
    """Stop the hub's service. -> (stopped, how to start it again)."""
    mgr, active = update.service_manager()
    if mgr == "systemd":
        if active:
            subprocess.run(["systemctl", "--user", "stop", update.UNIT],
                           check=True, timeout=60)
        return active, f"systemctl --user start {update.UNIT}"
    if mgr == "launchd":
        label = update.launchd_label() or update.LAUNCHD_LABEL
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        # KeepAlive: a kill would be restarted at once; unload instead.
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                       check=True, timeout=60)
        return True, f"launchctl bootstrap gui/{os.getuid()} {plist}"
    return False, "corral-light serve (or install the service)"


def hub_running():
    pid = update.hub_pid()
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def run(a, say):
    if update.inside_hub():
        raise Refused("refusing to stop from inside a pane: it would end the agent "
                      "running this command. Run it from a terminal.")
    start_again = None
    if not a.keepers_only:
        stopped, start_again = stop_service()
        say("service stopped" if stopped else "no hub service is running here")
    end = time.monotonic() + HUB_EXIT_WAIT_S
    while hub_running() and time.monotonic() < end:
        time.sleep(0.2)
    pid = hub_running()
    if pid:
        raise Refused(f"the hub (pid {pid}) is still running, not as a service: stop "
                      f"it first (Ctrl-C), then run `corral-light stop --keepers-only`. "
                      f"Closing keepers under a live hub would end its panes.")
    found = keepers()
    say(f"{len(found)} keeper(s) running")
    left = close_all(found, say)
    if left:
        raise Refused("still running after "
                      f"{CLOSE_WAIT_S}s: " + ", ".join(f"keeper {k['pid']}" for k in left))
    say("every keeper is gone")
    if start_again:
        say(f"start the hub again with: {start_again}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keepers-only", action="store_true",
                    help="close every keeper; leave the service alone (no hub may run)")
    a = ap.parse_args(argv)
    try:
        return run(a, lambda msg: print(msg, flush=True))
    except Refused as e:
        print(f"corral-light stop: {e}", file=sys.stderr, flush=True)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"corral-light stop: {' '.join(e.cmd)} failed (rc={e.returncode})",
              file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
