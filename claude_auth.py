#!/usr/bin/python3
"""claude_auth — is the Claude Code login good, and for how long?

WHY (dogma-2, 2026-09-30, 05:41 local)
    Craig opened a Claude pane, sent one real prompt, and the pane died:

        agent stopped — session/prompt: {'code': -32000,
                                         'message': 'Authentication required'}

    The pane's own transcript held the vendor's sentence one line earlier:
    "Failed to authenticate: OAuth session expired and could not be
    refreshed". Not the 2026-08-31 CLAUDE_CONFIG_DIR/Keychain bug (that one
    is fixed and documented in sessions.py) — this time the REFRESH TOKEN had
    lapsed, so no process on the machine could mint an access token until a
    human signed in again. He then typed `/login` into the pane and got
    "/login isn't available in this environment", opened a terminal, ran
    `/login`, and came back. Three things were wrong with that morning:

      1. It was foreseeable. The credential carries `refreshTokenExpiresAt`
         — the same number behind the CLI's "login expires in N days" banner
         — and nothing read it.
      2. The death was reported in JSON-RPC, not in English. "-32000" tells
         an operator nothing; "your Claude login expired, run `claude auth
         login`" tells him exactly what to do.
      3. Coming back was manual. Once he had signed in, the pane still sat
         dead until he pressed ↻.

    This module answers 1 (a credential read that returns timestamps and
    never a token), classifies 2 (is_auth_error / explain), and the hub's
    sweep uses both to do 3 (sessions.Manager.auth_sweep resumes the panes
    that died of it once a login is back).

WHAT IT WILL NOT DO
    - Return, log or bind a token. The Keychain item and the credentials file
      are read into one local parse and only two timestamps come out.
    - Complete the login. Corral starts the vendor's own login in a window
      Craig sees, on his click; it never completes, reads, or relays it
      (claude_login.py; decision note "corral-starts-vendor-login",
      2026-09-30, which replaced "never run the login"). This module says
      WHEN and WHAT; he does the signing in.
    - Claim ok on no data. A read that fails is `ok: None` with a reason,
      never a green light (PRINCIPLES 1: distrust green; "no data must not
      render as positive data").

STORES (read-only)
    macOS: Keychain generic password, service "Claude Code-credentials" —
           the CLI's own store (see sessions.darwin_keychain_blocks_isolation
           for the vendor source that names it). Read with the system
           `security` tool, which the CLI provisioned for.
    else:  ~/.claude/.credentials.json (or $CLAUDE_CONFIG_DIR/.credentials.json).
    Both hold {"claudeAiOauth": {"expiresAt": ms, "refreshTokenExpiresAt": ms,
    ...}}. Key names are the vendor's; a missing key reads as unknown.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SERVICE = "Claude Code-credentials"
WARN_H = 48             # say something this far ahead of refresh expiry
CACHE_S = 60            # a login does not change second to second
SECURITY_TIMEOUT_S = 5  # a Keychain prompt must never hang the hub's tick
LOGIN_CMD = "claude auth login"

# The vendor's words for this failure, at the three layers they surface:
# the ACP error (adapter: RequestError.authRequired → -32000 "Authentication
# required"), the SDK's synthetic assistant text, and the CLI's own sentence.
_AUTH_RE = re.compile(
    r"Authentication required|Please run /login|OAuth session expired|"
    r"could not be refreshed|not logged in|-32000", re.IGNORECASE)

_cache = {"at": 0.0, "value": None}
_lock = threading.Lock()


def is_auth_error(reason):
    """Did this dead-pane reason come from a lapsed/missing Claude login?"""
    return bool(reason) and bool(_AUTH_RE.search(str(reason)))


def remedy():
    return (f"Claude login expired — click Sign in to open the vendor's "
            f"login on the hub machine (or run `{LOGIN_CMD}` there); a pane it "
            f"killed resumes by itself once you are signed in.")


def explain(reason):
    """A dead reason an operator can act on. Auth failures get the remedy
    with the vendor's error kept in brackets; anything else passes through."""
    if not is_auth_error(reason):
        return reason
    return (f"{remedy()} Typing /login in this pane does the same. "
            f"[{str(reason)[:160]}]")


# ── the read ────────────────────────────────────────────────────────────────

def _config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _read_keychain():
    """The credential JSON from the macOS Keychain, or None. The value is
    returned to ONE caller (_timestamps) and parsed there; nothing else sees
    it."""
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password",
                            "-s", SERVICE, "-w"],
                           capture_output=True, text=True,
                           timeout=SECURITY_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout


def _read_file():
    try:
        return (_config_dir() / ".credentials.json").read_text(encoding="utf-8")
    except OSError:
        return None


def _timestamps(raw):
    """Two epoch-seconds out of the vendor's document; the rest is dropped."""
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        return None
    o = doc.get("claudeAiOauth") if isinstance(doc, dict) else None
    if not isinstance(o, dict):
        return None

    def sec(v):
        return (v / 1000.0) if isinstance(v, (int, float)) and v > 0 else None
    return {"accessExpiresAt": sec(o.get("expiresAt")),
            "refreshExpiresAt": sec(o.get("refreshTokenExpiresAt"))}


def expiry(platform=None):
    """{"accessExpiresAt": s|None, "refreshExpiresAt": s|None, "source": str}
    or None when no credential could be read. Never raises."""
    platform = platform or sys.platform
    if platform == "darwin":
        # The Keychain is THE store here. ~/.claude/.credentials.json can
        # exist beside it as a months-old leftover (it does on dogma-2, dated
        # May) — falling back to it would report a token nobody uses.
        raw, source = _read_keychain(), "keychain"
    else:
        raw, source = _read_file(), "file"
    ts = _timestamps(raw) if raw else None
    if not ts:
        return None
    ts["source"] = source
    return ts


def _iso(s):
    return (datetime.fromtimestamp(s, timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z")) if s else None


def _hours(s):
    if s is None:
        return None
    if s >= 24:
        d = s / 24
        return f"{d:.0f} day{'s' if round(d) != 1 else ''}"
    return f"{s:.0f}h"


def status(now=None, force=False, platform=None, _expiry=None):
    """One honest line about the Claude login.

        {"ok": True | False | None,     # None = could not tell
         "warn": bool,                  # ok, but inside WARN_H of expiry
         "why": str,                    # "" when nothing needs saying
         "refreshExpiresAt": iso|None, "accessExpiresAt": iso|None,
         "hoursLeft": float|None, "source": str|None, "checkedAt": iso}

    Cached CACHE_S so the picker and the tick do not spawn `security` per
    render. `_expiry` lets tests inject a read.
    """
    with _lock:
        hit = _cache["value"]
        if hit and not force and _expiry is None and \
                time.time() - _cache["at"] < CACHE_S:
            return hit
    now = time.time() if now is None else now
    e = _expiry if _expiry is not None else expiry(platform)
    out = {"ok": None, "warn": False, "why": "", "refreshExpiresAt": None,
           "accessExpiresAt": None, "hoursLeft": None, "source": None,
           "checkedAt": _iso(now)}
    if not e:
        out["why"] = (f"could not read the Claude login — run `{LOGIN_CMD}` "
                      f"if a Claude pane dies with 'Authentication required'")
    else:
        ref = e.get("refreshExpiresAt")
        out.update({"source": e.get("source"),
                    "accessExpiresAt": _iso(e.get("accessExpiresAt")),
                    "refreshExpiresAt": _iso(ref)})
        if ref is None:
            out["why"] = ("the Claude credential carries no refresh expiry — "
                          "cannot say when the login lapses")
        else:
            left = (ref - now) / 3600.0
            out["hoursLeft"] = round(left, 1)
            if left <= 0:
                out["ok"] = False
                out["why"] = remedy()
            else:
                out["ok"] = True
                if left < WARN_H:
                    out["warn"] = True
                    out["why"] = (f"Claude login expires in {_hours(left)} — "
                                  f"run `{LOGIN_CMD}` before it does, or the "
                                  f"next Claude pane dies mid-conversation")
    if _expiry is None:
        with _lock:
            _cache["at"], _cache["value"] = time.time(), out
    return out


def main(argv=None):
    """`python3 claude_auth.py` — one line for a human or a cron job.
    Exit 0 ok, 1 expired, 2 unknown, 3 expiring (so a scheduler can alert
    on the edge without parsing)."""
    s = status(force=True)
    print(json.dumps(s, indent=1) if "--json" in (argv or sys.argv[1:]) else
          (s["why"] or f"Claude login ok — refresh expires {s['refreshExpiresAt']} "
                       f"({_hours(s['hoursLeft'])} left)"), flush=True)
    return 0 if s["ok"] and not s["warn"] else 3 if s["ok"] else 1 if s["ok"] is False else 2


if __name__ == "__main__":
    sys.exit(main())
