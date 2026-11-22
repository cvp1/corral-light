#!/usr/bin/python3
"""webpush — Web Push to the operator's own devices. Stdlib plus the system openssl.

Payload-less by design: the push service (Apple, Google, Mozilla, Microsoft)
carries an empty wake-up and learns only that one happened. The device's
service worker then asks this hub, over the operator's own link, what needs
them (`recent()`), so no notification text ever leaves through the vendor.
That also means no payload encryption, which the stdlib cannot do.

The one cryptographic step, the VAPID ES256 signature, is done by the system
`openssl` binary (no Python dependency, nothing hand-rolled). Without openssl
push is unavailable and says so; it never fails open.

Bounded: at most MAX_SUBS devices, endpoints only on known push services
(the hub never posts to a URL a client chose), one send per device per
notice with a timeout, RECENT_MAX notices kept in memory.
"""
import base64
import collections
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_SUBS = 8                 # devices; the oldest is dropped past this
MAX_ENDPOINT = 1024          # chars
SEND_TIMEOUT_S = 10
OPENSSL_TIMEOUT_S = 10
JWT_TTL_S = 12 * 3600        # VAPID allows up to 24h
PUSH_TTL_S = 3600            # how long the service may hold an undelivered ping
RECENT_MAX = 20
KEY_FILE = "push-vapid.pem"
SUBS_FILE = "push-subs.json"

# Hosts a subscription endpoint may live on: the browsers' push services.
# Exact names, or a suffix when the name starts with a dot.
PUSH_HOSTS = ("web.push.apple.com", ".push.apple.com",
              "fcm.googleapis.com",
              "updates.push.services.mozilla.com",
              ".notify.windows.com")

_LOCK = threading.Lock()
_RECENT = collections.deque(maxlen=RECENT_MAX)


class PushError(Exception):
    """A refusal a caller shows as-is."""


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def openssl_bin():
    return shutil.which("openssl")


def _run(argv, data=None):
    r = subprocess.run(argv, input=data, capture_output=True, timeout=OPENSSL_TIMEOUT_S)
    if r.returncode != 0:
        raise PushError("openssl failed: " + r.stderr.decode("utf-8", "replace")[:200])
    return r.stdout


# ── the key ──────────────────────────────────────────────────────────────────
def ensure_key(state):
    """The VAPID private key's path, created 0600 on first use."""
    ssl = openssl_bin()
    if not ssl:
        raise PushError("push needs the openssl command, and this machine has none")
    state = Path(state)
    key = state / KEY_FILE
    if key.exists():
        return key
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / (KEY_FILE + ".tmp")
    old = os.umask(0o077)
    try:
        _run([ssl, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(tmp)])
    finally:
        os.umask(old)
    os.chmod(tmp, 0o600)
    os.replace(tmp, key)
    return key


def public_key_raw(key):
    """The uncompressed P-256 point (65 bytes, 0x04|X|Y) of the key's public half."""
    der = _run([openssl_bin(), "ec", "-in", str(key), "-pubout", "-outform", "DER"])
    # SubjectPublicKeyInfo for P-256 is 91 bytes and ends with the 65-byte point.
    pt = der[-65:]
    if len(der) != 91 or pt[0] != 4:
        raise PushError("unexpected public key encoding from openssl")
    return pt


def public_key(state):
    """The applicationServerKey a browser subscribes with, base64url."""
    return b64url(public_key_raw(ensure_key(state)))


# ── the signature ────────────────────────────────────────────────────────────
def _der_len(b, i):
    n = b[i]
    if n < 0x80:
        return n, i + 1
    k = n & 0x7F
    if k == 0 or k > 2:
        raise PushError("bad DER length")
    return int.from_bytes(b[i + 1:i + 1 + k], "big"), i + 1 + k


def der_to_raw(der):
    """An ECDSA DER signature (SEQUENCE{INTEGER r, INTEGER s}) as the 64-byte
    r|s that JOSE ES256 wants."""
    if len(der) < 8 or der[0] != 0x30:
        raise PushError("bad DER signature")
    _, i = _der_len(der, 1)
    out = b""
    for _ in range(2):
        if der[i] != 0x02:
            raise PushError("bad DER integer")
        n, i = _der_len(der, i + 1)
        v = der[i:i + n].lstrip(b"\x00")
        if len(v) > 32:
            raise PushError("integer too long for P-256")
        out += v.rjust(32, b"\x00")
        i += n
    return out


def sign_es256(key, data):
    der = _run([openssl_bin(), "dgst", "-sha256", "-sign", str(key)], data)
    return der_to_raw(der)


def vapid_auth(endpoint, key, subject, now=None):
    """The Authorization header for one push service: `vapid t=<jwt>, k=<key>`."""
    u = urlparse(endpoint)
    head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": f"{u.scheme}://{u.netloc}",
              "exp": int(now if now is not None else time.time()) + JWT_TTL_S,
              "sub": subject}
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode("ascii")
    jwt = f"{head}.{body}.{b64url(sign_es256(key, signing))}"
    return f"vapid t={jwt}, k={b64url(public_key_raw(key))}"


# ── subscriptions ────────────────────────────────────────────────────────────
def endpoint_ok(url):
    """(ok, why). Only https on a known push service; no userinfo, no odd port."""
    if not isinstance(url, str) or not url or len(url) > MAX_ENDPOINT:
        return False, "no endpoint, or too long"
    u = urlparse(url)
    if u.scheme != "https":
        return False, "a push endpoint must be https"
    if u.username or u.password or u.port not in (None, 443):
        return False, "a push endpoint carries no login or port"
    host = (u.hostname or "").lower()
    for h in PUSH_HOSTS:
        if host == h or (h.startswith(".") and host.endswith(h)):
            return True, "ok"
    return False, f"{host or 'that'} is not a browser push service"


def _subs_path(state):
    return Path(state) / SUBS_FILE


def load_subs(state):
    try:
        v = json.loads(_subs_path(state).read_text())
        return [s for s in v if isinstance(s, dict) and endpoint_ok(s.get("endpoint"))[0]] \
            if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def _save_subs(state, subs):
    p = _subs_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs[-MAX_SUBS:], indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def subscribe(state, endpoint, label="", now=None):
    ok, why = endpoint_ok(endpoint)
    if not ok:
        raise PushError(why)
    with _LOCK:
        subs = [s for s in load_subs(state) if s["endpoint"] != endpoint]
        subs.append({"endpoint": endpoint, "label": str(label or "")[:80],
                     "at": int(now if now is not None else time.time())})
        _save_subs(state, subs)
        return len(subs[-MAX_SUBS:])


def unsubscribe(state, endpoint):
    with _LOCK:
        subs = load_subs(state)
        keep = [s for s in subs if s["endpoint"] != endpoint]
        if len(keep) != len(subs):
            _save_subs(state, keep)
        return len(subs) - len(keep)


# ── notices and sending ──────────────────────────────────────────────────────
def remember(pane, kind, title, body, now=None):
    """Keep a notice for the service worker to fetch after a ping."""
    n = {"pane": str(pane or ""), "kind": str(kind or ""), "title": str(title)[:120],
         "body": str(body)[:300], "at": int(now if now is not None else time.time())}
    with _LOCK:
        _RECENT.append(n)
    return n


def recent(limit=5):
    with _LOCK:
        return list(_RECENT)[-limit:][::-1]          # newest first


def send_all(state, subject, *, urlopen=None, now=None):
    """Ping every subscribed device once. Returns {"sent", "dropped", "errors"}.

    A 404/410 means the browser forgot the subscription: it is removed.
    Never raises for a device; a missing key or openssl raises PushError."""
    subs = load_subs(state)
    out = {"sent": 0, "dropped": 0, "errors": []}
    if not subs:
        return out
    key = ensure_key(state)
    opener = urlopen or urllib.request.urlopen
    gone = []
    for s in subs:
        ep = s["endpoint"]
        try:
            req = urllib.request.Request(ep, data=b"", method="POST", headers={
                "TTL": str(PUSH_TTL_S), "Urgency": "high",
                "Authorization": vapid_auth(ep, key, subject, now),
                "Content-Length": "0"})
            with opener(req, timeout=SEND_TIMEOUT_S) as r:
                code = getattr(r, "status", 201)
            if 200 <= code < 300:
                out["sent"] += 1
            else:
                out["errors"].append(f"{urlparse(ep).hostname}: HTTP {code}")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                gone.append(ep)
            else:
                out["errors"].append(f"{urlparse(ep).hostname}: HTTP {e.code}")
        except (OSError, PushError, ValueError) as e:
            out["errors"].append(f"{urlparse(ep).hostname}: {str(e)[:120]}")
    for ep in gone:
        out["dropped"] += unsubscribe(state, ep)
    return out


def send_async(state, subject):
    """send_all on a daemon thread; the notifier never waits on the network."""
    if not load_subs(state):
        return None
    def run():
        try:
            send_all(state, subject)
        except Exception:                                 # noqa: BLE001
            pass
    t = threading.Thread(target=run, name="push", daemon=True)
    t.start()
    return t
