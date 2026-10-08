"""The fetch proxy: keys stay in the core (docs/finops-module-plan.md §6.7.2).

A module's fetcher never holds a billing key. It sends plain HTTP requests,
in proxy form (`GET https://api.anthropic.com/v1/... HTTP/1.1`), to this
proxy, which the hub runs for one fetch run and one grant. For each request
the proxy:

- accepts only GET or POST, an `https` URL, and a host from that grant's
  vendor list (exact match, never a lane's sign-in host), on port 443;
- drops every request header but a short allowlist, so a module cannot set
  its own credentials, cookies or hosts;
- adds the vendor's credential itself (an API key header, or for Google a
  short-lived token the core obtains with the service account key, signed
  here; the module never sees the key or the token);
- makes the HTTPS request with certificate checks, to a public address
  only, with caps on size and time;
- refuses a response that contains the key or the token, and passes back
  only the status, two headers and the body.

There is no CONNECT: no tunnel the core cannot see into. On Linux the
fetcher reaches this proxy through the same loopback shim as before (a unix
socket bound into the sandbox); on macOS through one loopback TCP port the
Seatbelt profile allows. Transport is review_egress.Egress's; only the
protocol differs.
"""
import base64
import hashlib
import http.client
import json
import re
import ssl
import time
import urllib.parse

import review_egress

MAX_HEAD = 16 << 10
MAX_BODY_IN = 1 << 20
MAX_BODY_OUT = 8 << 20
MAX_REQUESTS = 500
UPSTREAM_TIMEOUT_S = 30
PASS_REQUEST = {"accept", "content-type", "user-agent", "anthropic-version"}
PASS_RESPONSE = {"content-type", "retry-after"}

# How each vendor is authenticated. The module's hosts are modules.FETCH_VENDORS;
# Google's token host is the core's own and is never open to the module.
AUTH = {
    "anthropic": ("header", "x-api-key"),
    "openai": ("bearer", None),
    "xai": ("bearer", None),
    "gcp": ("google", None),
}
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform.read-only"

# Tests point a vendor host at a local stub; empty in a running hub.
UPSTREAM_OVERRIDE = {}


class Refused(Exception):
    def __init__(self, status, why):
        super().__init__(why)
        self.status = status


# ── RSASSA-PKCS1-v1_5 SHA-256, for the Google token (RFC 8017 §8.2) ─────

_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _der(buf, i):
    if i + 2 > len(buf):
        raise ValueError("truncated DER")
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        if not 1 <= k <= 4 or i + k > len(buf):
            raise ValueError("unsupported DER length")
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    if i + n > len(buf):
        raise ValueError("truncated DER")
    return tag, buf[i:i + n], i + n


def _seq(buf):
    tag, body, _ = _der(buf, 0)
    if tag != 0x30:
        raise ValueError("expected a DER SEQUENCE")
    out, i = [], 0
    while i < len(body):
        t, v, i = _der(body, i)
        out.append((t, v))
    return out


def rsa_sign(pem, message):
    """PKCS#1 v1.5 SHA-256 signature, from a PEM PKCS#8 or PKCS#1 key."""
    m = re.search(r"-----BEGIN (RSA )?PRIVATE KEY-----(.+?)-----END (RSA )?PRIVATE KEY-----",
                  pem, re.S)
    if not m:
        raise ValueError("no PEM private key found")
    parts = _seq(base64.b64decode("".join(m.group(2).split())))
    if m.group(1) is None:
        if len(parts) < 3 or parts[2][0] != 0x04:
            raise ValueError("not a PKCS#8 private key")
        parts = _seq(parts[2][1])
    ints = [int.from_bytes(v, "big") for t, v in parts if t == 0x02]
    if len(ints) < 9:
        raise ValueError("not an RSA private key")
    _v, n, _e, _d, p, q, dp, dq, qinv = ints[:9]
    k = (n.bit_length() + 7) // 8
    t = _SHA256_PREFIX + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        raise ValueError("the RSA key is too short")
    em = int.from_bytes(b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t, "big")
    s1, s2 = pow(em, dp, p), pow(em, dq, q)
    return (s2 + q * ((qinv * (s1 - s2)) % p)).to_bytes(k, "big")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def google_assertion(sa, now):
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT",
                            "kid": sa.get("private_key_id", "")}).encode())
    claims = _b64(json.dumps({"iss": sa["client_email"], "scope": GOOGLE_SCOPE,
                              "aud": GOOGLE_TOKEN_URL, "iat": int(now),
                              "exp": int(now) + 3600}).encode())
    signing = head + b"." + claims
    return (signing + b"." + _b64(rsa_sign(sa["private_key"], signing))).decode()


# ── upstream ────────────────────────────────────────────────────────────

def _upstream(method, host, path, headers, body):
    """-> (status, headers dict lowercased, body bytes). Raises Refused."""
    override = UPSTREAM_OVERRIDE.get(host)
    if override is None:
        if not review_egress.public_addresses(host, 443):
            raise Refused(502, f"{host} is not a public address")
        conn = http.client.HTTPSConnection(host, 443, timeout=UPSTREAM_TIMEOUT_S,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(override[0], override[1], timeout=UPSTREAM_TIMEOUT_S)
    try:
        conn.request(method, path, body=body, headers=dict(headers, Host=host,
                                                           Connection="close"))
        r = conn.getresponse()
        data = r.read(MAX_BODY_OUT + 1)
        if len(data) > MAX_BODY_OUT:
            raise Refused(502, "the vendor's response is larger than 8 MiB")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data
    except (OSError, http.client.HTTPException) as e:
        raise Refused(502, f"{host}: {type(e).__name__}") from None
    finally:
        conn.close()


class FetchProxy(review_egress.Egress):
    """One fetch run's proxy. `key` is the grant's key bytes; `hosts` the
    vendor's exact hosts the module may name; `on_host(host, ok)` records
    every request (the host is the module's text when refused)."""

    def __init__(self, path, vendor, key, hosts, on_host=None, tcp=False):
        if vendor not in AUTH:
            raise ValueError(f"no auth scheme for {vendor!r}")
        self.vendor, self.key = vendor, key.strip()
        self.hosts = tuple(h.lower() for h in hosts)
        self.requests = 0
        self.token = None                     # Google's access token, core-side only
        self.secrets = [self.key]
        super().__init__(path, None, on_host=on_host,
                         allow=lambda h: review_egress.exact_allowed(h, self.hosts), tcp=tcp)

    # ── the module's request ──
    def _one(self, c):
        try:
            c.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = c.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > MAX_HEAD:
                    return self._reply(c, 431, b"request head too large")
            raw_head, rest = head.split(b"\r\n\r\n", 1)
            lines = raw_head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or parts[0] not in ("GET", "POST"):
                self.on_host("-", False)
                return self._reply(c, 405, b"only GET and POST, in proxy form")
            method, url = parts[0], parts[1]
            hdrs = {}
            for line in lines[1:]:
                k, sep, v = line.partition(":")
                if sep:
                    hdrs[k.strip().lower()] = v.strip()
            n = hdrs.get("content-length", "0")
            if not n.isdigit() or int(n) > MAX_BODY_IN:
                return self._reply(c, 413, b"request body too large")
            body = rest
            while len(body) < int(n):
                chunk = c.recv(65536)
                if not chunk:
                    break
                body += chunk
            body = body[:int(n)]
            status, rh, data = self._forward(method, url, hdrs, body)
            self._reply(c, status, data, rh)
        except Refused as e:
            self._reply(c, e.status, str(e).encode()[:300])
        except Exception:  # noqa: BLE001 — one request, never the hub
            self._reply(c, 502, b"proxy error")
        finally:
            try:
                c.close()
            except OSError:
                pass

    def _forward(self, method, url, hdrs, body):
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.port not in (None, 443) or u.username or u.password \
                or not self.allow(host):
            self.on_host(host or "-", False)
            raise Refused(403, "not one of this grant's vendor hosts")
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise Refused(429, f"more than {MAX_REQUESTS} requests in one run")
        self.on_host(host, True)
        out = {k: v for k, v in hdrs.items() if k in PASS_REQUEST}
        out.update(self._credential())
        if method == "POST":
            out["Content-Length"] = str(len(body))
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        status, rh, data = _upstream(method, host, path, out, body if method == "POST" else None)
        if self._contains_secret(data):
            raise Refused(502, "the vendor's response contained the credential; refused")
        return status, {k: v for k, v in rh.items() if k in PASS_RESPONSE}, data

    # ── credentials ──
    def _credential(self):
        kind, name = AUTH[self.vendor]
        if kind == "header":
            return {name: self.key.decode("utf-8", "replace")}
        if kind == "bearer":
            return {"Authorization": "Bearer " + self.key.decode("utf-8", "replace")}
        return {"Authorization": "Bearer " + self._google_token()}

    def _google_token(self):
        if self.token and self.token[1] > time.time() + 60:
            return self.token[0]
        try:
            sa = json.loads(self.key)
        except ValueError:
            raise Refused(502, "the granted key is not a service account JSON file") from None
        if not isinstance(sa, dict) or sa.get("type") != "service_account" or \
                not sa.get("client_email") or not sa.get("private_key"):
            raise Refused(502, "the granted key is not a service account JSON file")
        try:
            assertion = google_assertion(sa, time.time())
        except (ValueError, KeyError):
            raise Refused(502, "the service account key could not sign") from None
        form = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": assertion}).encode()
        host = urllib.parse.urlsplit(GOOGLE_TOKEN_URL).hostname
        status, _rh, data = _upstream("POST", host, "/token",
                                      {"Content-Type": "application/x-www-form-urlencoded",
                                       "Content-Length": str(len(form))}, form)
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            doc = {}
        tok = doc.get("access_token") if isinstance(doc, dict) else None
        if status != 200 or not isinstance(tok, str) or not tok:
            err = doc.get("error") if isinstance(doc, dict) else None
            raise Refused(502, f"Google refused the service account ({status}"
                               + (f", {str(err)[:40]}" if err else "") + ")")
        life = doc.get("expires_in") if isinstance(doc.get("expires_in"), int) else 3600
        self.token = (tok, time.time() + life)
        self.secrets.append(tok.encode())
        return tok

    def _contains_secret(self, data):
        for s in self.secrets:
            if s and (s in data or base64.b64encode(s) in data):
                return True
        return False

    def _reply(self, c, status, body, headers=None):
        reason = http.client.responses.get(status, "")
        head = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(body)}",
                "Connection: close"]
        for k, v in (headers or {}).items():
            head.append(f"{k}: {v}")
        try:
            c.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)
        except OSError:
            pass

