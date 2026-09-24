"""edge — what the hub knows about the proxy in front of it.

Corral's cookie is the boundary (pairing is the gate, not the bind). That was
ruled for a LAN hub over plain HTTP (decisions/corral-lan-exposure-2026-08-01)
with an explicit "revisit if Corral ever leaves the LAN". Fronting the hub with
Tailscale Serve IS that revisit (2026-09-24, Grok 4.7 + Astra consult,
rnd/reviews/2026-09-22-self-hosted-grokbot-muse). Three things change and all
three live here, as PURE functions over a headers mapping, so both skins call
one implementation and a guard cannot exist in one fork and not the other.

  1. `via_serve(headers)` — Serve stamps `Tailscale-User-Login` on every
     request it proxies from the tailnet and STRIPS any incoming header of
     that name (tailscale.com/kb/1312/serve), so its presence is proof the
     request came through Serve over TLS. Funnel traffic never carries it.
  2. `cookie_header(...)` — `Secure` on the session cookie when the request
     came via Serve; unchanged on the LAN, where the origin is http:// and a
     Secure cookie would simply never be sent back.
  3. `identity_ok(headers, required_login)` — BIND the pairing cookie to a
     tailnet identity. With `required_login` set, a request that arrived
     through the proxy must carry that exact login; proxied traffic with NO
     identity (Funnel, a tagged device) is refused outright. A request with
     no proxy marks at all is local/LAN and is left to the pairing gate, as
     before. This is a narrowing, never a grant: a spoofed header can only
     make a request FAIL this check, because a cookie is still required to
     do anything.

What this does NOT fix (on the record, Astra finding 1): the approval
authority still shares the hub's UNIX identity — a process running as the
hub user can mint and claim a cookie without a human. That is a controller/
worker split, not a header check.
"""

TS_LOGIN = "Tailscale-User-Login"
FORWARDED_FOR = "X-Forwarded-For"


def _get(headers, name):
    """Case-insensitive read over an http.server headers object or a dict."""
    try:
        v = headers.get(name)
    except AttributeError:
        v = None
    if v is None and hasattr(headers, "items"):
        low = name.lower()
        for k, val in headers.items():
            if str(k).lower() == low:
                v = val
                break
    return (v or "").strip() or None


def via_serve(headers):
    """True iff Tailscale Serve proxied this request (identity header present)."""
    return _get(headers, TS_LOGIN) is not None


def cookie_header(name, token, ttl, secure):
    """The Set-Cookie value both hubs emit on a successful pair claim."""
    flags = "HttpOnly; SameSite=Strict; " + ("Secure; " if secure else "")
    return f"{name}={token}; {flags}Path=/; Max-Age={int(ttl)}"


def identity_ok(headers, required_login):
    """(ok, why). Refuses only what the hub is CONFIGURED to bind to."""
    if not required_login:
        return True, "unbound"
    login = _get(headers, TS_LOGIN)
    if login is None:
        if _get(headers, FORWARDED_FOR) is not None:
            return False, "proxied request carries no tailnet identity"
        return True, "local"
    if login.lower() == required_login.strip().lower():
        return True, "bound"
    return False, f"tailnet identity {login!r} is not the bound login"
