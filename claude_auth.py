#!/usr/bin/python3
"""claude_auth — is the Claude Code login good, and for how long?

Reads only the expiry timestamps from the CLI's credential store (macOS
Keychain "Claude Code-credentials", else ~/.claude/.credentials.json); never
returns or logs a token. Also classifies auth-failure pane deaths. A failed
read is `ok: None`, never ok.
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
WARN_H = 48             # warn this far ahead of refresh expiry
CACHE_S = 60
SECURITY_TIMEOUT_S = 5  # a Keychain prompt must never hang the hub's tick
LOGIN_CMD = "claude auth login"

# The vendor's wording for this failure across the ACP, SDK and CLI layers.
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
    """The credential JSON from the macOS Keychain, or None; only _timestamps
    sees it."""
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
        # Keychain only: a leftover credentials file may be stale.
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
