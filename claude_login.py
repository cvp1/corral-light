#!/usr/bin/python3
"""claude_login — start the vendor's own Claude login, in a window you can see.

WHY (DESIGN-6 S4; decision note "corral-starts-vendor-login", 2026-09-30)
    A lapsed Claude login killed a pane at its first prompt, and the way back
    was a terminal the operator had to open and a command they had to know.
    The rule changed from "Corral never runs the login" to "Corral STARTS the
    vendor's login on your click; it never completes, reads, or relays it".

WHAT IT DOES
    start(requester, local=...) writes a fixed wrapper script into STATE and
    opens it in a terminal window on THIS machine: Terminal via `open` on
    macOS, `x-terminal-emulator -e` on Linux with a display. The wrapper runs
    `<abs claude> auth login` (not exec'd, so its exit code survives) and
    writes that code to STATE/claude-login.exit. You sign in, in your browser
    or by pasting the code into that window. A watch thread (never the hub's
    tick) reads the outcome.

WHAT IT WILL NOT DO
    - Start a window nobody is in front of. A request that is not from
      loopback, or that came through Tailscale Serve or any proxy, is refused
      with the command to run instead. So is a machine with no display.
    - Hold the CLI's output. The terminal is spawned with no pipes; no URL,
      code, or token ever passes through this process.
    - Put request bytes in the argv. The argv is a fixed list, and the
      wrapper's one variable line (who asked) is sanitized and quoted.
    - Call a rotation a sign-in. Success needs ALL of: the CLI exited 0, the
      credential's refresh expiry strictly advanced past its value at start,
      and `claude auth status` says logged in. Anything less reads
      `check-status`, and nothing is resumed.
    - Kill anything. A login still open after LOGIN_WATCH_S reads `gave-up`;
      the window stays, and a new start is refused while it is alive.

STATES (snapshot()["state"])
    idle · running · signed-in · check-status · closed · gave-up
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOGIN_COOLDOWN_S = 30   # after a window closes, before a new one may open
LOGIN_POLL_S = 5        # the watch thread's pulse
LOGIN_WATCH_S = 600     # stop watching (never killing) after this
STARTUP_GRACE_S = 10    # `open` returns before the wrapper process exists
STATUS_TIMEOUT_S = 15   # `claude auth status` may touch the Keychain
LEDGER_MAX_LINES = 400  # fold the ledger back to LEDGER_KEEP past this (P8)
LEDGER_KEEP = 200
COMMAND = "claude auth login"
_REQUESTER_RE = re.compile(r"[^A-Za-z0-9 @._:/()-]")


def _iso(t):
    return (datetime.fromtimestamp(t, timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z")) if t else None


def clean_requester(raw):
    """Who asked, safe for a banner line: a fixed alphabet, 60 chars."""
    s = _REQUESTER_RE.sub("", str(raw or ""))[:60].strip()
    return s or "unknown"


def claude_bin():
    """The absolute path of the `claude` CLI, or None."""
    p = os.environ.get("CORRAL_CLAUDE_BIN") or shutil.which("claude")
    return str(Path(p).resolve()) if p and Path(p).is_file() else None


def terminal(platform=None, env=None):
    """The argv prefix that opens a visible window running one script, or
    None when this machine has no display to open it on."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "darwin":
        return ["/usr/bin/open"]
    if platform.startswith("linux") and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        x = shutil.which("x-terminal-emulator")
        return [x, "-e"] if x else None
    return None


def wrapper_text(claude, exit_file, requester, at):
    """The whole script. Everything in it is fixed but `requester`, which
    clean_requester() has already reduced to a safe alphabet."""
    q = shlex.quote
    banner = (f"Corral started the Claude sign-in, asked by {requester} at {at}. "
              f"Sign in below; this window can close when it is done.")
    return ("#!/bin/sh\n"
            f"echo {q(banner)}\n"
            f"{q(claude)} auth login\n"
            "code=$?\n"
            f"echo \"$code\" > {q(str(exit_file) + '.tmp')} && "
            f"mv {q(str(exit_file) + '.tmp')} {q(str(exit_file))}\n"
            "echo\n"
            "echo \"claude auth login exited $code. You can close this window.\"\n")


def logged_in(claude, timeout=STATUS_TIMEOUT_S):
    """`claude auth status --json` says loggedIn: True, False, or None."""
    try:
        r = subprocess.run([claude, "auth", "status", "--json"],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout)
        return bool(json.loads(r.stdout).get("loggedIn"))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def alive(script):
    """Is any process still running the wrapper? (pgrep on its unique path)"""
    try:
        return subprocess.run(["pgrep", "-f", str(script)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _refresh_expiry():
    import claude_auth
    e = claude_auth.expiry() or {}
    return e.get("refreshExpiresAt")


class Login:
    """One sign-in at a time. Every collaborator is injectable for tests."""

    def __init__(self, state_dir, on_success=None, *, claude=None, find_terminal=None,
                 refresh_expiry=None, status=None, is_alive=None, clock=None,
                 sleep=None, spawn=None):
        self.dir = Path(state_dir)
        self.script = self.dir / "claude-login.command"
        self.exit_file = self.dir / "claude-login.exit"
        self.ledger = self.dir / "claude-login.jsonl"
        self.on_success = on_success or (lambda: None)
        self._claude = claude
        self.find_terminal = find_terminal or terminal
        self.refresh_expiry = refresh_expiry or _refresh_expiry
        self.status = status or logged_in
        self.alive = is_alive or alive
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.spawn = spawn or self._spawn
        self._lock = threading.Lock()
        self.s = {"state": "idle", "why": "", "at": None, "requester": None}
        self.started = self.ended = None
        self.ref0 = None

    # ── what the browser sees ─────────────────────────────────────────────
    def snapshot(self):
        with self._lock:
            return dict(self.s)

    def _set(self, state, why, requester=None):
        self.s = {"state": state, "why": why, "at": _iso(self.clock()),
                  "requester": requester or self.s.get("requester")}
        self._log(state, why, self.s["requester"])

    def _log(self, event, why, requester):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": _iso(self.clock()), "event": event,
                                    "requester": requester, "why": why}) + "\n")
            lines = self.ledger.read_text(encoding="utf-8").splitlines()
            if len(lines) > LEDGER_MAX_LINES:
                tmp = self.ledger.with_suffix(".tmp")
                tmp.write_text("\n".join(lines[-LEDGER_KEEP:]) + "\n", encoding="utf-8")
                os.replace(tmp, self.ledger)
        except OSError:
            pass                                    # a ledger write never blocks a login

    # ── the click ─────────────────────────────────────────────────────────
    def _refuse(self, why, requester):
        self._log("refused", why, requester)
        return {"ok": False, "state": "refused", "why": why, "command": COMMAND}

    def start(self, requester, *, local):
        """Open the vendor's login, or say why not. Never raises."""
        requester = clean_requester(requester)
        if not local:
            return self._refuse(
                "sign-in opens a window on the hub's own screen, so it starts "
                f"only from that machine — there, run `{COMMAND}`", requester)
        claude = self._claude or claude_bin()
        term = self.find_terminal()
        if not term:
            return self._refuse(f"this machine has no display to open a sign-in "
                                f"window on — in a terminal run `{COMMAND}`", requester)
        if not claude:
            return self._refuse(f"the claude CLI was not found — install it, then "
                                f"run `{COMMAND}`", requester)
        with self._lock:
            now = self.clock()
            st = self.s["state"]
            if st == "running" or (st == "gave-up" and self.alive(self.script)):
                return {"ok": False, "state": "running", "command": COMMAND,
                        "why": "a sign-in window is already open"}
            if self.ended is not None and now - self.ended < LOGIN_COOLDOWN_S:
                wait = int(LOGIN_COOLDOWN_S - (now - self.ended)) + 1
                return {"ok": False, "state": "cooldown", "command": COMMAND,
                        "why": f"the last sign-in window just closed — try again in {wait}s"}
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                for f in (self.exit_file, Path(str(self.exit_file) + ".tmp")):
                    if f.exists():
                        f.unlink()
                self.ref0 = self.refresh_expiry()
                tmp = self.script.with_suffix(".tmp")
                tmp.write_text(wrapper_text(claude, self.exit_file, requester, _iso(now)),
                               encoding="utf-8")
                os.chmod(tmp, 0o700)
                os.replace(tmp, self.script)
                self.spawn(list(term) + [str(self.script)])
            except (OSError, subprocess.SubprocessError) as e:
                self.ended = now
                self._set("check-status", f"could not open the sign-in window: {e}"[:200],
                          requester)
                return {"ok": False, "state": "check-status", "why": self.s["why"],
                        "command": COMMAND}
            self.started, self.ended = now, None
            self._set("running", "sign in in the window that opened", requester)
        threading.Thread(target=self.watch, daemon=True).start()
        return {"ok": True, "state": "running", "why": self.s["why"], "command": COMMAND}

    @staticmethod
    def _spawn(argv):
        """No pipes: the hub never holds the terminal's or the CLI's output."""
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True,
                                close_fds=True)

    # ── the watch thread ──────────────────────────────────────────────────
    def watch(self):
        """Poll until the login ends, the window closes, or LOGIN_WATCH_S."""
        while True:
            self.sleep(LOGIN_POLL_S)
            if self.poll() != "running":
                return

    def poll(self):
        """One look. Returns the state after it. Never raises."""
        try:
            return self._poll()
        except Exception as e:                      # noqa: BLE001
            with self._lock:
                self.ended = self.clock()
                self._set("check-status", f"the sign-in watch failed: {e}"[:200])
                return "check-status"

    def _poll(self):
        now = self.clock()
        with self._lock:
            if self.s["state"] != "running":
                return self.s["state"]
        if self.exit_file.exists():
            return self._judge(self.exit_file.read_text(encoding="utf-8").strip())
        with self._lock:
            if now - self.started > LOGIN_WATCH_S:
                self._set("gave-up", "stopped watching the sign-in window; it was "
                                     "left open, nothing was killed")
                return "gave-up"
            if now - self.started > STARTUP_GRACE_S and not self.alive(self.script):
                self.ended = now
                self._set("closed", "the sign-in window closed before the login finished")
                return "closed"
        return "running"

    def _judge(self, raw):
        """The CLI finished. Signed in only on all three facts."""
        try:
            code = int(raw)
        except ValueError:
            code = None
        ref1 = self.refresh_expiry()
        advanced = ref1 is not None and (self.ref0 is None or ref1 > self.ref0)
        claude = self._claude or claude_bin()
        logged = self.status(claude) if (code == 0 and advanced and claude) else None
        ok = code == 0 and advanced and logged is True
        with self._lock:
            self.ended = self.clock()
            if ok:
                self._set("signed-in", "Claude login is back")
            elif code != 0:
                self._set("check-status", f"`{COMMAND}` exited {raw or '?'} — "
                                          f"not signed in")
            elif not advanced:
                self._set("check-status", "the CLI finished but the credential did "
                                          "not change — check `claude auth status`")
            else:
                self._set("check-status", "the credential changed but `claude auth "
                                          "status` does not say logged in")
        if ok:
            try:
                self.on_success()
            except Exception:                       # noqa: BLE001
                pass
        return self.s["state"]
