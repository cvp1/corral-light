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
import socket
import time
import sys
import uuid
from pathlib import Path

from corral_core import webauthn

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
    looks like the server forgot the pairing, not like a crash. Through
    _write_private (S7): 0600 from the first byte rather than chmod after the
    write, and a failed replace removes its temp file instead of leaving one."""
    _write_private(PAIRFILE, json.dumps(d, indent=1))


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
    d["challenges"] = {c: v for c, v in d.get("challenges", {}).items()
                       if v.get("expires", 0) > now}
    if d.get("enroll_code", {}).get("expires", 0) <= now:
        d.pop("enroll_code", None)
    return d


def host_id():
    """An opaque tag for THIS machine, served with every pairing code so a
    client can tell "the hub's store is on my host but is not the one I read"
    (fail fast) from "the hub is elsewhere, maybe behind an ssh -L tunnel to
    127.0.0.1" (wait for its human). A URL cannot tell those apart; this can.
    Salted hash: /api/pair/new is unauthenticated, so the tag names nothing."""
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


def approve(code, now=None, break_glass=False, why="break-glass"):
    """Called by the `corral pair` CLI, i.e. by Craig's own UNIX account.

    Under policy `key-only` a code is refused with the reason, unless this is
    `pair --break-glass`, which works and leaves one ledger line (S7)."""
    now = now or time.time()
    code = (code or "").strip().upper()
    pol, perr = policy()
    if pol == "key-only" and not break_glass:
        return False, ("refused: pairing policy is key-only"
                       + (f" ({perr})" if perr else "")
                       + " — touch your key on the pairing page, or run "
                         "`corral-light pair --break-glass <code>` (audited)")
    with _locked():
        d = _prune(_load(), now)
        entry = d["pending"].get(code)
        if not entry:
            return False, "unknown or expired code"
        if break_glass:
            # Recorded before the approval commits: a break-glass pairing
            # that could not be audited does not happen.
            _ledger("break-glass", now, why=why, policy=pol)
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


def mint(now=None, ttl=SESSION_TTL, user="craig"):
    # `user` is the token's audience: "craig" everywhere, or edge.SERVE_USER
    # for a cookie minted through Tailscale Serve (corral_core/edge.py).
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


# ── pairing by touching a key (DESIGN-6 S7) ─────────────────────────────────
#
# A browser can pair by a WebAuthn assertion from an enrolled security key
# instead of a shell. corral_core/webauthn.py judges the bytes; this is the
# store, the challenges, the enrollment ceremony and the policy around it.
# Everything here runs under the pairing lock above, in the same file
# discipline (atomic, 0600), because the CLI and the hub are two processes.
#
# What this is NOT (the C threat model, webauthn.py's docstring): a process
# running as this UNIX user can read session.key and forge a cookie, and can
# edit keys.json. `key-only` is a workflow guard against well-meaning agents
# using the documented pair command; it is not a boundary.

MAX_KEYS = 4
CHALLENGE_TTL = 120          # seconds a key ceremony may take
MAX_CHALLENGES = 8           # in flight at once; at the cap REFUSE, never evict
ENROLL_CODE_TTL = 300        # the shell's one-time code for a first key
POLICIES = ("code", "key-or-code", "key-only")
DEFAULT_POLICY = "code"      # before any key is enrolled
MAX_LABEL = 40

NO_KEY_FOR_ORIGIN = "no key enrolled for this origin"
NEEDS_LIVE_CODE = "needs the live pairing code this page is showing"
NO_CHALLENGE = "unknown, used or expired challenge"
WRONG_PURPOSE = "challenge was issued for another ceremony"
WRONG_ORIGIN = "challenge was issued to another origin"
WRONG_COOKIE = "challenge was issued to another session"
POLICY_CODE = "key pairing is off (policy: code)"
NEEDS_ENROLL_CODE = ("the first key for this origin needs the one-time code "
                     "from `corral-light key enroll`")
ENROLL_CODE_SPENT = "the enrollment code was used or expired; run `corral-light key enroll` again"
TOO_MANY_KEYS = f"already {MAX_KEYS} keys enrolled; remove one from a shell first"
ALREADY_ENROLLED = "this key is already enrolled"
KEY_ADDED_MEANWHILE = "a key was enrolled for this origin meanwhile; start again"


class KeyRefused(Exception):
    """A key ceremony refused, with the reason and the HTTP status to say it."""

    def __init__(self, reason, status=403):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _keys_path():
    return STATE / "keys.json"


def _policy_path():
    return STATE / "key-policy.json"


def _ledger_path():
    return STATE / "key-ledger.jsonl"


def _loud(msg):
    print(f"corral-light auth: {msg}", file=sys.stderr, flush=True)


def _write_private(path, text):
    """Atomic and 0600 from the first byte. An interrupted write leaves the
    old file in place (the temp file is removed), never a truncated one."""
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _ledger(event, now=None, **fields):
    """One JSON line per security event, append-only, 0600. Raises on failure:
    the callers that promise an audit (break-glass) record before they act."""
    STATE.mkdir(parents=True, exist_ok=True)
    rec = {"at": int(now or time.time()), "event": event, **fields}
    fd = os.open(_ledger_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def ledger_lines():
    try:
        text = _ledger_path().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def policy():
    """(policy, error). Missing file -> `code`, the pre-enrollment default.
    Unreadable or unknown -> `key-only` and a loud error: degrade toward the
    strict end, where break-glass still pairs from a shell."""
    try:
        raw = _policy_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_POLICY, None
    except OSError as e:
        err = f"key-policy.json is unreadable ({e.__class__.__name__}); treated as key-only"
        _loud(err)
        return "key-only", err
    try:
        value = json.loads(raw).get("policy")
    except (ValueError, AttributeError):
        value = None
    if value not in POLICIES:
        err = "key-policy.json is corrupt; treated as key-only"
        _loud(err)
        return "key-only", err
    return value, None


def _set_policy(value, now, by):
    old, _ = policy()
    _write_private(_policy_path(), json.dumps({"policy": value}) + "\n")
    _ledger("policy", now, before=old, after=value, by=by)


def _valid_key(k):
    return (isinstance(k, dict)
            and all(isinstance(k.get(f), str) and k.get(f)
                    for f in ("id", "spki", "rpId", "origin"))
            and isinstance(k.get("signCount"), int) and k["signCount"] >= 0)


def load_keys():
    """(keys, error). Missing file -> no keys. Corrupt -> NO keys and a loud
    error, and the file is left exactly as it is: it may hold real keys, so
    nothing here overwrites it (enrollment refuses while it is unreadable)."""
    try:
        raw = _keys_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], None
    except OSError as e:
        err = f"keys.json is unreadable ({e.__class__.__name__}); no keys loaded"
        _loud(err)
        return [], err
    try:
        keys = json.loads(raw)["keys"]
        if not isinstance(keys, list) or len(keys) > MAX_KEYS \
                or not all(_valid_key(k) for k in keys) \
                or len({k["id"] for k in keys}) != len(keys):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        err = ("keys.json is corrupt; no keys loaded. It is kept as it is -- "
               "move it aside to start over")
        _loud(err)
        return [], err
    return keys, None


def _save_keys(keys):
    _write_private(_keys_path(), json.dumps({"keys": keys}, indent=1) + "\n")


def verifier_state():
    """('ok', binary) or ('unavailable', why). Without openssl no assertion
    can be checked, so key pairing says so rather than passing."""
    b = webauthn.openssl_bin()
    if b:
        return "ok", b
    return webauthn.UNAVAILABLE, "no openssl binary on this machine"


def _digest(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def _window(d, name, now, window, cap):
    """A rate list trimmed to its window; True while under the cap."""
    win = [t for t in d.get(name, []) if t > now - window]
    d[name] = win[-cap:]
    return len(win) < cap


def _new_challenge(d, now, purpose, origin, rp_id, **bind):
    pending = d.setdefault("challenges", {})
    if len(pending) >= MAX_CHALLENGES:
        # Refuse, never evict -- the same reasoning as new_code(): evicting
        # would let a caller under the rate limit keep killing Craig's own
        # ceremony. One already started still finishes.
        raise TooMany(f"too many key ceremonies in flight -- one already "
                      f"started still finishes; wait up to {CHALLENGE_TTL}s")
    raw = secrets.token_bytes(32)
    pending[webauthn.b64url(raw)] = {"purpose": purpose, "expires": now + CHALLENGE_TTL,
                                     "origin": origin, "rpId": rp_id, **bind}
    return raw


def _challenge(d, ch, purpose, origin, rp_id, stage=None):
    entry = d.get("challenges", {}).get(ch) if isinstance(ch, str) else None
    if not entry:
        raise KeyRefused(NO_CHALLENGE)
    if entry.get("purpose") != purpose or entry.get("stage") != stage:
        raise KeyRefused(WRONG_PURPOSE)
    if entry.get("origin") != origin or entry.get("rpId") != rp_id:
        raise KeyRefused(WRONG_ORIGIN)
    return entry


def _b(body, name):
    try:
        return webauthn.b64url_decode((body or {}).get(name))
    except webauthn.Refused:
        raise KeyRefused(f"{name} is not base64url", 400)


def _for_origin(keys, origin, rp_id):
    return [k for k in keys if k["origin"] == origin and k["rpId"] == rp_id]


def key_available(origin, rp_id):
    """Can a browser on this origin pair by key right now? For the page."""
    pol, _ = policy()
    keys, err = load_keys()
    return (pol != "code" and not err and verifier_state()[0] == "ok"
            and bool(_for_origin(keys, origin, rp_id)))


def _usable(now_policy=None):
    pol = now_policy or policy()[0]
    if pol == "code":
        raise KeyRefused(POLICY_CODE)
    state, why = verifier_state()
    if state != "ok":
        raise KeyRefused(f"{webauthn.UNAVAILABLE}: {why}", 503)


def key_begin(code, origin, rp_id, now=None):
    """POST /api/pair/key/begin. Unauthenticated, so it needs the live pairing
    code the page is already showing -- no credential id reaches a caller
    without one -- and is mint-rate-limited like /api/pair/new."""
    now = now or time.time()
    _usable()
    code = (code or "").strip().upper() if isinstance(code, str) else ""
    with _locked():
        d = _prune(_load(), now)
        if code not in d["pending"]:
            # A wrong code is a miss on the same limiter as /api/pair/claim.
            if not _rate_ok(d, now):
                _save(d)
                raise TooMany("slow down")
            d.setdefault("attempts", []).append(now)
            _save(d)
            raise KeyRefused(NEEDS_LIVE_CODE)
        keys, err = load_keys()
        if err:
            raise KeyRefused(err, 503)
        allow = [k["id"] for k in _for_origin(keys, origin, rp_id)]
        if not allow:
            raise KeyRefused(NO_KEY_FOR_ORIGIN, 404)
        if not _window(d, "key_begins", now, MINT_WINDOW, MAX_MINTS):
            _save(d)
            raise TooMany(f"too many key ceremonies started -- wait {MINT_WINDOW}s")
        raw = _new_challenge(d, now, "pair", origin, rp_id, code=code)
        d["key_begins"].append(now)
        _save(d)
    return {"challenge": webauthn.b64url(raw), "rpId": rp_id,
            "allowCredentials": allow, "userVerification": "required",
            "timeout": CHALLENGE_TTL * 1000}


def key_finish(body, origin, rp_id, now=None):
    """POST /api/pair/key/finish -> the credential id that paired. The caller
    mints the cookie, and only after this returns.

    Verify, consume the challenge (and the pairing code it was begun with),
    and advance the key's signCount in ONE lock acquisition: two finishes of
    the same assertion cannot both get past the consume. The challenge is
    committed spent before the counter, so a failure between the two writes
    leaves no replayable challenge and mints nothing."""
    now = now or time.time()
    _usable()
    ch = (body or {}).get("challenge")
    with _locked():
        d = _prune(_load(), now)
        if not _window(d, "key_attempts", now, CLAIM_WINDOW, MAX_CLAIMS):
            _save(d)
            raise TooMany("slow down")
        try:
            entry = _challenge(d, ch, "pair", origin, rp_id)
            keys, err = load_keys()
            if err:
                raise KeyRefused(err, 503)
            creds = {k["id"]: k for k in _for_origin(keys, origin, rp_id)}
            ad = _b(body, "authenticatorData")
            ok, why = webauthn.verify_assertion(
                client_data_json=_b(body, "clientDataJSON"), auth_data=ad,
                signature=_b(body, "signature"), credential_id=_b(body, "id"),
                challenge=webauthn.b64url_decode(ch), origins=(origin,),
                rp_id=rp_id, credentials=creds)
            if not ok:
                raise KeyRefused(why, 503 if why == webauthn.UNAVAILABLE else 403)
        except KeyRefused as e:
            if e.status != 503:
                d["key_attempts"].append(now)       # only a miss counts
                _save(d)
            raise
        cred_id = webauthn.b64url(_b(body, "id"))
        d["challenges"].pop(ch)
        d["pending"].pop(entry.get("code"), None)   # the code this page showed is spent too
        _save(d)
        rec = creds[cred_id]
        rec["signCount"] = webauthn.parse_auth_data(ad)["signCount"]
        rec["lastUsed"] = int(now)
        _save_keys(keys)
        _ledger("key-pair", now, key=cred_id, origin=origin)
    return cred_id


def mint_enroll_code(now=None):
    """`corral-light key enroll`: a one-time code for a first key, shell only.
    Stored as a digest; a new one replaces the old."""
    now = now or time.time()
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    with _locked():
        d = _prune(_load(), now)
        d["enroll_code"] = {"digest": _digest(code), "expires": now + ENROLL_CODE_TTL}
        _save(d)
        _ledger("enroll-code", now)
    return code, ENROLL_CODE_TTL


def _enroll_code_live(d, digest):
    live = d.get("enroll_code") or {}
    return bool(digest) and hmac.compare_digest(live.get("digest", ""), digest)


def enroll_begin(cookie, code, origin, rp_id, now=None):
    """POST /api/pair/key/enroll/begin (cookie + same-origin, checked by the
    hub). The first key for an origin needs the shell's enrollment code; a
    later key needs a second touch from an enrolled one (enroll_approve)."""
    now = now or time.time()
    state, why = verifier_state()
    if state != "ok":
        raise KeyRefused(f"{webauthn.UNAVAILABLE}: {why}", 503)
    keys, err = load_keys()
    if err:
        raise KeyRefused(err, 503)
    if len(keys) >= MAX_KEYS:
        raise KeyRefused(TOO_MANY_KEYS, 409)
    first = not _for_origin(keys, origin, rp_id)
    with _locked():
        d = _prune(_load(), now)
        bind = {"stage": "create", "cookie": _digest(cookie), "first": first}
        if first:
            digest = _digest((code or "").strip().upper()) if isinstance(code, str) and code else ""
            if not _enroll_code_live(d, digest):
                raise KeyRefused(NEEDS_ENROLL_CODE)
            bind["enrollCode"] = digest
        raw = _new_challenge(d, now, "enroll", origin, rp_id, **bind)
        _save(d)
    return {"challenge": webauthn.b64url(raw),
            "rp": {"id": rp_id, "name": "Corral Light"},
            "user": {"id": webauthn.b64url(hashlib.sha256(b"corral-light").digest()[:16]),
                     "name": "corral-light", "displayName": "Corral Light"},
            "pubKeyCredParams": [{"type": "public-key", "alg": webauthn.ALG_ES256}],
            # Every key on this rpId, so an authenticator never enrolls twice.
            "excludeCredentials": [k["id"] for k in keys if k["rpId"] == rp_id],
            "authenticatorSelection": {"authenticatorAttachment": "cross-platform",
                                       "residentKey": "discouraged",
                                       "userVerification": "required"},
            "attestation": "none", "timeout": CHALLENGE_TTL * 1000, "first": first}


def _label(text):
    text = "".join(c for c in (text if isinstance(text, str) else "") if c.isprintable())
    return text.strip()[:MAX_LABEL] or "security key"


def enroll_finish(cookie, body, origin, rp_id, now=None):
    """POST /api/pair/key/enroll/finish. Verifies the registration. A first
    key is enrolled here, spending the enrollment code and -- if the policy
    is still `code` -- setting `key-or-code`, all in this lock acquisition.
    A later key becomes a TRANSACTION instead: the proposed credential, its
    key digest and the requesting cookie are bound to a new approval
    challenge that an enrolled key must sign (enroll_approve).
    -> {"enrolled": id, "policy": p} or {"approve": {...}}."""
    now = now or time.time()
    ch = (body or {}).get("challenge")
    with _locked():
        d = _prune(_load(), now)
        entry = _challenge(d, ch, "enroll", origin, rp_id, stage="create")
        if not hmac.compare_digest(entry.get("cookie", ""), _digest(cookie)):
            raise KeyRefused(WRONG_COOKIE)
        ok, why, cred = webauthn.verify_registration(
            client_data_json=_b(body, "clientDataJSON"),
            attestation_object=_b(body, "attestationObject"),
            challenge=webauthn.b64url_decode(ch), origins=(origin,), rp_id=rp_id)
        if not ok:
            raise KeyRefused(why)
        keys, err = load_keys()
        if err:
            raise KeyRefused(err, 503)
        if any(k["id"] == cred["id"] for k in keys):
            raise KeyRefused(ALREADY_ENROLLED, 409)
        if len(keys) >= MAX_KEYS:
            raise KeyRefused(TOO_MANY_KEYS, 409)
        rec = {"id": cred["id"], "spki": webauthn.b64url(cred["spki"]),
               "signCount": cred["signCount"], "rpId": rp_id, "origin": origin,
               "label": _label(body.get("label")), "enrolledAt": int(now),
               "lastUsed": None, "aaguid": cred["aaguid"]}
        if entry.get("first"):
            if _for_origin(keys, origin, rp_id):
                raise KeyRefused(KEY_ADDED_MEANWHILE, 409)
            if not _enroll_code_live(d, entry.get("enrollCode")):
                raise KeyRefused(ENROLL_CODE_SPENT)
            d["challenges"].pop(ch)
            d.pop("enroll_code", None)
            _save(d)
            _save_keys(keys + [rec])
            _ledger("enroll", now, key=rec["id"], origin=origin, via="enroll-code")
            if policy()[0] == "code":
                _set_policy("key-or-code", now, by="first enrollment")
            return {"enrolled": rec["id"], "policy": policy()[0]}
        allow = [k["id"] for k in _for_origin(keys, origin, rp_id)]
        d["challenges"].pop(ch)
        raw = _new_challenge(d, now, "enroll", origin, rp_id, stage="approve",
                             cookie=entry["cookie"], proposed=rec,
                             keyDigest=hashlib.sha256(cred["spki"]).hexdigest())
        _save(d)
    return {"approve": {"challenge": webauthn.b64url(raw), "rpId": rp_id,
                        "allowCredentials": allow, "userVerification": "required",
                        "timeout": CHALLENGE_TTL * 1000}}


def enroll_approve(cookie, body, origin, rp_id, now=None):
    """POST /api/pair/key/enroll/approve: the second touch, from an ENROLLED
    key, over the transaction's own challenge. Only the key the transaction
    bound is enrolled -- the request carries no key of its own -- and the
    transaction, the approver's signCount and the new key commit in one lock
    acquisition."""
    now = now or time.time()
    ch = (body or {}).get("challenge")
    with _locked():
        d = _prune(_load(), now)
        entry = _challenge(d, ch, "enroll", origin, rp_id, stage="approve")
        if not hmac.compare_digest(entry.get("cookie", ""), _digest(cookie)):
            raise KeyRefused(WRONG_COOKIE)
        rec = dict(entry["proposed"])
        if hashlib.sha256(webauthn.b64url_decode(rec["spki"])).hexdigest() != entry.get("keyDigest"):
            raise KeyRefused("the transaction's key does not match its digest")
        keys, err = load_keys()
        if err:
            raise KeyRefused(err, 503)
        if any(k["id"] == rec["id"] for k in keys):
            raise KeyRefused(ALREADY_ENROLLED, 409)
        if len(keys) >= MAX_KEYS:
            raise KeyRefused(TOO_MANY_KEYS, 409)
        creds = {k["id"]: k for k in _for_origin(keys, origin, rp_id)}
        ad = _b(body, "authenticatorData")
        ok, why = webauthn.verify_assertion(
            client_data_json=_b(body, "clientDataJSON"), auth_data=ad,
            signature=_b(body, "signature"), credential_id=_b(body, "id"),
            challenge=webauthn.b64url_decode(ch), origins=(origin,),
            rp_id=rp_id, credentials=creds)
        if not ok:
            raise KeyRefused(why, 503 if why == webauthn.UNAVAILABLE else 403)
        approver = creds[webauthn.b64url(_b(body, "id"))]
        d["challenges"].pop(ch)
        _save(d)
        approver["signCount"] = webauthn.parse_auth_data(ad)["signCount"]
        approver["lastUsed"] = int(now)
        rec["approvedBy"] = approver["id"]
        _save_keys(keys + [rec])
        _ledger("enroll", now, key=rec["id"], origin=origin, approvedBy=approver["id"])
    return {"enrolled": rec["id"], "policy": policy()[0]}


# ── the shell-only verbs (`corral-light key ...`) ───────────────────────────

def set_policy(value, now=None):
    """`corral-light key policy`. key-only needs a readable store with a key
    in it: with none, only break-glass could ever pair again."""
    now = now or time.time()
    if value not in POLICIES:
        return False, f"policy must be one of {', '.join(POLICIES)}"
    with _locked():
        keys, err = load_keys()
        if value == "key-only" and (err or not keys):
            return False, ("refused: key-only needs an enrolled key"
                           + (f" ({err})" if err else ""))
        _set_policy(value, now, by="shell")
    return True, f"pairing policy is now {value}"


def remove_key(key_id, now=None):
    """`corral-light key rm`. Removing the last key returns the policy to
    `code`: key-or-code and key-only mean nothing without a key."""
    now = now or time.time()
    with _locked():
        keys, err = load_keys()
        if err:
            return False, f"refused: {err}"
        left = [k for k in keys if k["id"] != key_id]
        if len(left) == len(keys):
            return False, "no key with that id (corral-light key list)"
        _save_keys(left)
        _ledger("rm", now, key=key_id)
        if not left and policy()[0] != "code":
            _set_policy("code", now, by="last key removed")
    return True, f"removed {key_id}" + ("" if left else "; no keys left, policy is code")


def recover(code, key_ids, now=None):
    """`corral-light key recover <pair-code> <id>...|--all`: lost a key. A
    break-glass pairing for the browser showing <code>, then the named keys
    removed, then a fresh enrollment code. Stops at the first failure."""
    now = now or time.time()
    ok, msg = approve(code, now=now, break_glass=True, why="recover")
    if not ok:
        return False, [msg]
    out = [msg]
    for kid in key_ids:
        ok, m = remove_key(kid, now=now)
        out.append(m)
        if not ok:
            return False, out
    enroll, ttl = mint_enroll_code(now=now)
    _ledger("recover", now, removed=list(key_ids))
    out.append(f"enrollment code {enroll} -- good for {ttl}s, once: "
               f"Settings -> Security keys -> Enroll")
    return True, out
