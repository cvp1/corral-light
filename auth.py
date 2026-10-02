#!/usr/bin/python3
"""auth — personal identity for Corral, proved by UNIX account possession.

The browser shows a one-time code; the user approves it with `corral pair
<code>` from their own shell, so account possession is the proof. Session
cookies are HMAC-signed with a 0600 key in the state dir; codes are single-use
and fail closed when expired or unknown.
"""
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import uuid
from pathlib import Path

# Separate from full Corral's state dir, so neither hub accepts the other's cookies.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
KEYFILE = STATE / "session.key"
LOCKFILE = STATE / "pair.lock"
PAIRFILE = STATE / "pairing.json"

CODE_TTL = 300
SESSION_TTL = 12 * 3600
MAX_PENDING = 8              # kept above MAX_MINTS so one rate-limited burst never trips it
CLAIM_WINDOW = 60            # seconds
MAX_CLAIMS = 40              # claim attempts per window; the browser polls ~40/min
MINT_WINDOW = 60             # seconds
MAX_MINTS = 6                # codes minted per window; a browser needs ONE


class TooMany(Exception):
    """Refused for rate, not for identity — the caller should just wait."""


def _secret():
    STATE.mkdir(parents=True, exist_ok=True)
    if not KEYFILE.is_file():
        KEYFILE.write_bytes(secrets.token_bytes(32))
        KEYFILE.chmod(0o600)
    return KEYFILE.read_bytes()


@contextlib.contextmanager
def _locked():
    """Serialize load-modify-save across processes (the pair CLI and the server)."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOCKFILE, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load():
    try:
        return json.loads(PAIRFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pending": {}}


def _save(d):
    """Atomically write the pairing file."""
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = PAIRFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, PAIRFILE)


def _rate_ok(d, now):
    """Throttle claim attempts on the unauthenticated /api/pair/claim."""
    win = [t for t in d.get("attempts", []) if t > now - CLAIM_WINDOW]
    d["attempts"] = win[-MAX_CLAIMS:]
    return len(win) < MAX_CLAIMS


def _prune(d, now=None):
    now = now or time.time()
    d["pending"] = {c: v for c, v in d.get("pending", {}).items()
                    if v.get("expires", 0) > now}
    return d


def host_id():
    """An opaque salted-hash tag for this machine, served with pairing codes so
    a client can tell a local hub from one behind a tunnel."""
    raw = ""
    for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            raw = Path(f).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw:
            break
    if not raw:                                          # macOS: no machine-id
        raw = f"{socket.gethostname()}:{uuid.getnode()}"
    return hashlib.sha256(b"corral-pair-host\0" + raw.encode()).hexdigest()[:16]


def new_code(now=None):
    """Mint a pairing code for a browser that has none."""
    now = now or time.time()
    with _locked():
        d = _prune(_load(), now)
        # /api/pair/new is unauthenticated, so cap the mint rate outright.
        mints = [t for t in d.get("mints", []) if t > now - MINT_WINDOW]
        if len(mints) >= MAX_MINTS:
            d["mints"] = mints[-MAX_MINTS:]
            _save(d)
            raise TooMany(
                f"too many pairing codes requested — wait {MINT_WINDOW}s. "
                f"An existing code is still good for its full {CODE_TTL}s.")
        if len(d["pending"]) >= MAX_PENDING:
            # Refuse, never evict: eviction would let an attacker push out a
            # live code. Checked before the mint is recorded.
            _save(d)
            raise TooMany(
                f"too many pairing codes are already pending — an "
                f"already-displayed code is unaffected and still good for "
                f"its full {CODE_TTL}s; wait for one to clear or retry "
                f"shortly.")
        mints.append(now)
        d["mints"] = mints[-MAX_MINTS:]
        # Six symbols from a 32-char alphabet without 0/O, 1/I: 30 bits.
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        raw = "".join(secrets.choice(alphabet) for _ in range(6))
        code = f"{raw[:3]}-{raw[3:]}"
        d["pending"][code] = {"expires": now + CODE_TTL, "approved": False}
        _save(d)
    return code, CODE_TTL


def approve(code, now=None):
    """Called by the `corral pair` CLI, i.e. by the user's own UNIX account."""
    now = now or time.time()
    code = (code or "").strip().upper()
    with _locked():
        d = _prune(_load(), now)
        entry = d["pending"].get(code)
        if not entry:
            return False, "unknown or expired code"
        entry["approved"] = True
        entry["approved_at"] = now
        _save(d)
    return True, f"paired — the browser showing {code} is now authorized"


def claim(code, now=None):
    """Browser polls with its code; once approved, it gets a session token."""
    now = now or time.time()
    code = (code or "").strip().upper()
    with _locked():
        d = _prune(_load(), now)
        entry = d["pending"].get(code)
        if not entry:
            # Only a miss counts against the limit; the browser polls its live code.
            if not _rate_ok(d, now):
                _save(d)
                return None, "slow down"
            d.setdefault("attempts", []).append(now)
            _save(d)
            return None, "expired"
        if not entry.get("approved"):
            return None, "pending"
        # Single use: the removal is committed inside the lock.
        d["pending"].pop(code, None)
        _save(d)
    return mint(now=now), "ok"


def mint(now=None, ttl=SESSION_TTL, user="craig"):
    # `user` is the token's audience: the default, or edge.SERVE_USER for a
    # cookie minted through Tailscale Serve.
    if "." in user:
        raise ValueError("a token user may not contain '.'")
    now = int(now or time.time())
    exp = now + ttl
    body = f"{user}.{exp}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def verify(token, now=None):
    """Constant-time verify. Any malformation is a plain failure, never a pass."""
    now = now or time.time()
    try:
        user, exp, sig = (token or "").split(".", 2)
    except ValueError:
        return None
    try:
        if int(exp) < now:
            return None
    except ValueError:
        return None
    body = f"{user}.{exp}"
    want = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    want_b64 = base64.urlsafe_b64encode(want).decode().rstrip("=")
    return user if hmac.compare_digest(sig, want_b64) else None
