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

Review round 2026-09-24 (Astra + Gemini, rnd/reviews/2026-09-22-self-hosted-
grokbot-muse/03-code-review): "no headers = local" failed OPEN for a tailnet
peer dialling the hub's 0.0.0.0 port directly, skipping Serve. So `identity_ok`
now takes the socket peer: with a bound login, a no-header request is "local"
only from loopback or a NON-tailnet address (the LAN keeps its 2026-08-01
pairing-gate ruling); a direct tailnet connection is refused. And a cookie
minted through Serve carries the audience `SERVE_USER`, which `audience_ok`
accepts only on a request that came through Serve — a stolen Serve cookie
cannot be replayed on the LAN port.

What this does NOT fix (on the record, Astra finding 1): the approval
authority still shares the hub's UNIX identity — a process running as the
hub user can mint and claim a cookie without a human. That is a controller/
worker split, not a header check.
"""

import ipaddress

TS_LOGIN = "Tailscale-User-Login"
FORWARDED_FOR = "X-Forwarded-For"
SERVE_USER = "operator-ts"     # token audience for a cookie minted through Serve
# Tailscale's address space: CGNAT v4 range and the ULA v6 prefix it assigns.
_TAILNET = (ipaddress.ip_network("100.64.0.0/10"),
            ipaddress.ip_network("fd7a:115c:a1e0::/48"))


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


def via_serve(headers, peer=None):
    """True iff Tailscale Serve proxied this request.

    Serve stamps the identity header and strips client copies, but only on
    ITS OWN hop, which always reaches us from loopback. The same header on a
    socket from anywhere else was written by the client, so with a known
    peer it counts only from loopback. (`peer=None` = caller has no socket.)"""
    if _get(headers, TS_LOGIN) is None:
        return False
    return peer is None or _peer_kind(peer) == "loopback"


def cookie_header(name, token, ttl, secure):
    """The Set-Cookie value both hubs emit on a successful pair claim."""
    flags = "HttpOnly; SameSite=Strict; " + ("Secure; " if secure else "")
    return f"{name}={token}; {flags}Path=/; Max-Age={int(ttl)}"


def _peer_kind(peer):
    """'loopback' | 'tailnet' | 'other' for a socket peer address string."""
    try:
        ip = ipaddress.ip_address((peer or "").split("%", 1)[0])
    except ValueError:
        return "other"
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if any(ip in n for n in _TAILNET):
        return "tailnet"
    return "other"


def identity_ok(headers, required_login, peer=None):
    """(ok, why). Refuses only what the hub is CONFIGURED to bind to.

    `peer` is the socket's remote address (`self.client_address[0]`)."""
    if not required_login:
        return True, "unbound"
    login = _get(headers, TS_LOGIN)
    if login is None:
        if _get(headers, FORWARDED_FOR) is not None:
            return False, "proxied request carries no tailnet identity"
        if _peer_kind(peer) == "tailnet":
            return False, "direct tailnet connection skips Serve"
        return True, "local"
    if peer is not None and _peer_kind(peer) != "loopback":
        return False, "identity header from a peer that is not the Serve proxy"
    if login.lower() == required_login.strip().lower():
        return True, "bound"
    return False, f"tailnet identity {login!r} is not the bound login"


def audience_ok(user, headers, peer=None):
    """A Serve-minted cookie is only good on a request that came through Serve."""
    if user == SERVE_USER:
        return via_serve(headers, peer)
    return bool(user)


# ── who owns the socket calling the peer routes (DESIGN-5 S8, measured) ──────
# The seat tools' token is handed to each adapter in its MCP descriptor, and
# the Claude adapter puts that descriptor on the `claude` process's COMMAND
# LINE -- measured live 2026-09-29. /proc/<pid>/cmdline is world-readable on a
# host whose /proc is not mounted hidepid (this one is not), so without a
# further check ANY local user could read a pane's token with `ps` and send as
# that pane. The documented boundary is "the same UNIX user"; this makes it
# true on Linux: the kernel records which user opened each TCP socket, and a
# loopback caller's own socket is in /proc/net/tcp with that uid.

PROC_NET = "/proc/net"


def _hex_addr(ip, port):
    a = ipaddress.ip_address(ip)
    if a.version == 4:
        host = "".join(f"{b:02X}" for b in reversed(a.packed))
    else:
        p = a.packed
        host = "".join("".join(f"{b:02X}" for b in reversed(p[i:i + 4]))
                       for i in range(0, 16, 4))
    return f"{host}:{int(port):04X}"


def local_peer_uid(client, server, proc_net=PROC_NET):
    """The uid that owns the CLIENT end of a local TCP connection, or None.

    `client` and `server` are (ip, port) as the server sees them: the peer
    address, and the accepted socket's own address (getsockname -- NOT the
    listening address, which may be 0.0.0.0). The client's socket is the
    /proc/net/tcp row whose local address is `client` and remote is `server`;
    its uid column is who opened it. None when it cannot be found -- the
    caller must treat that as "unknown", never as "fine" (P4).
    """
    try:
        want_local, want_rem = _hex_addr(*client[:2]), _hex_addr(*server[:2])
    except (ValueError, TypeError):
        return None
    for name in ("tcp", "tcp6"):
        try:
            with open(f"{proc_net}/{name}", encoding="ascii") as fh:
                next(fh, None)                               # header
                for line in fh:
                    f = line.split()
                    if len(f) > 7 and f[1] == want_local and f[2] == want_rem:
                        try:
                            return int(f[7])
                        except ValueError:
                            return None
        except OSError:
            continue
    return None
