#!/usr/bin/python3
"""auth — personal identity for Corral, proved by UNIX account possession.

WHY NOT THE EXISTING SSO
------------------------
ranch-hub authenticates against a SHARED credential set (`ranch`/`dash`) — it
proves "someone with the household password", not "Craig". That is a knowing
compromise for viewing a dashboard, and it is documented as one
(`06 Logs/Decisions/2026-08-01 fleet approval authority reaches the ranch dash`).

It is NOT acceptable for Corral, because a Corral session drives real agents
with real tools in real directories. Anyone holding the shared password would
be able to start an agent and answer its permission prompts. So conversation
features require a personal gate, and this is it.

THE MECHANISM
    Browser shows a one-time code. Craig runs, in a shell he already trusts:
        corral pair <code>
    That command can only run as his UNIX user (over ssh or locally), so
    possession of the account IS the proof. No password, no new identity
    provider, no dependency — the estate already treats ssh access as identity.

The session cookie is HMAC-signed with a key in the state dir (0600). Expired
or unknown codes fail closed; a code is single-use and dies on first claim.
"""
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

# Its own state dir, NOT the full Corral's. The session key lives here, so
# sharing one would mean either hub could mint a cookie the other accepts.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
KEYFILE = STATE / "session.key"
LOCKFILE = STATE / "pair.lock"
PAIRFILE = STATE / "pairing.json"

CODE_TTL = 300               # 5 min to walk to a shell
SESSION_TTL = 12 * 3600      # re-pair twice a day
MAX_PENDING = 8              # bounded: a code mill is a brute-force surface. Kept
                             # above MAX_MINTS so one legitimate rate-limited
                             # burst never trips this cap on its own — see
                             # new_code()'s pending-cap branch.
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
    """Serialize load-modify-save ACROSS PROCESSES.

    `approve()` runs in the `corral pair` CLI while the server is serving
    /api/pair/claim, so these are genuinely two processes racing on one file.
    Unlocked, a claim and an approval could each read, each write, and the
    later write would silently undo the earlier one — including undoing the
    single-use removal, which is how one approved code mints two sessions.
    """
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
    """Atomic. A plain write_text can be interrupted mid-file, and a truncated
    pairing file reads as "no pending codes" — which locks the browser out and
    looks like the server forgot the pairing, not like a crash."""
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = PAIRFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, PAIRFILE)


def _rate_ok(d, now):
    """Throttle claim attempts. /api/pair/claim is UNAUTHENTICATED by
    necessity — it is how you become authenticated — so it is the one endpoint
    an attacker on the LAN can hammer. 32^6 is a large space, but "large" is
    not a rate limit, and the failures were free."""
    win = [t for t in d.get("attempts", []) if t > now - CLAIM_WINDOW]
    d["attempts"] = win[-MAX_CLAIMS:]
    return len(win) < MAX_CLAIMS


def _prune(d, now=None):
    now = now or time.time()
    d["pending"] = {c: v for c, v in d.get("pending", {}).items()
                    if v.get("expires", 0) > now}
    return d


def new_code(now=None):
    """Mint a pairing code for a browser that has none."""
    now = now or time.time()
    with _locked():
        d = _prune(_load(), now)
        # /api/pair/new is unauthenticated too — it has to be, that is how
        # pairing bootstraps identity in the first place — so the server can
        # never tell Craig's own mint from an attacker's. A miss-counting
        # limiter is wrong here (every call is a "hit"), so this one is a
        # plain ceiling on how fast codes may be minted at all.
        mints = [t for t in d.get("mints", []) if t > now - MINT_WINDOW]
        if len(mints) >= MAX_MINTS:
            d["mints"] = mints[-MAX_MINTS:]
            _save(d)
            raise TooMany(
                f"too many pairing codes requested — wait {MINT_WINDOW}s. "
                f"An existing code is still good for its full {CODE_TTL}s.")
        if len(d["pending"]) >= MAX_PENDING:
            # REFUSE, never evict. This used to push the OLDEST pending code
            # out to make room — which meant an attacker who stayed under the
            # mint-rate ceiling above could still repeatedly evict Craig's
            # own live, about-to-be-approved code and deny him pairing
            # indefinitely: the rate limit bounded the SPEED of the attack,
            # never stopped it. gpt-5.6-sol, third-pass review, finding 6.
            # Refusing costs only a NEW mint while the pool is full; any code
            # already displayed is untouched and stays good for its full
            # CODE_TTL. This check runs BEFORE the mint is recorded, so a
            # pending-cap refusal does not also burn mint-rate budget — the
            # two limits stay independent.
            _save(d)
            raise TooMany(
                f"too many pairing codes are already pending — an "
                f"already-displayed code is unaffected and still good for "
                f"its full {CODE_TTL}s; wait for one to clear or retry "
                f"shortly.")
        mints.append(now)
        d["mints"] = mints[-MAX_MINTS:]
        # Six symbols from a 32-char alphabet with the ambiguous glyphs (0/O,
        # 1/I) removed: 30 bits. A 2026-08-01 review read this as losing a
        # character to the slicing; measured across 400 codes, every position
        # carries the full alphabet. Built plainly now so nobody has to
        # re-derive that.
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        raw = "".join(secrets.choice(alphabet) for _ in range(6))
        code = f"{raw[:3]}-{raw[3:]}"
        d["pending"][code] = {"expires": now + CODE_TTL, "approved": False}
        _save(d)
    return code, CODE_TTL


def approve(code, now=None):
    """Called by the `corral pair` CLI, i.e. by Craig's own UNIX account."""
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
            # Only a MISS counts against the limit. The browser polls its own
            # live code roughly every 1.5s while it waits, so counting every
            # call would throttle the one flow this is meant to protect — the
            # rate limiter would lock Craig out and leave a guesser unbothered.
            if not _rate_ok(d, now):
                _save(d)
                return None, "slow down"
            d.setdefault("attempts", []).append(now)
            _save(d)
            return None, "expired"
        if not entry.get("approved"):
            return None, "pending"
        # Single use, and the removal is committed INSIDE the lock — that is
        # what makes it single use rather than single-use-if-nobody-else-is-
        # looking.
        d["pending"].pop(code, None)
        _save(d)
    return mint(now=now), "ok"


def mint(now=None, ttl=SESSION_TTL):
    now = int(now or time.time())
    exp = now + ttl
    body = f"craig.{exp}"
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
