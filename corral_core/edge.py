"""edge — pure header/peer checks for a hub fronted by Tailscale Serve.

`via_serve` detects Serve-proxied requests, `cookie_header` adds `Secure` for
them, `identity_ok` binds the pairing cookie to a tailnet login (a narrowing,
never a grant), and `audience_ok` keeps Serve-minted cookies off the LAN port.
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

    Serve's hop always arrives from loopback, so with a known peer the header
    counts only from loopback. (`peer=None` = caller has no socket.)"""
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
    """(ok, why). `peer` is the socket's remote address."""
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


# ── who owns the socket calling the peer routes ─────────────────────────────
# The seat token can appear on a process command line, readable by other local
# users; checking the caller socket's uid in /proc/net/tcp enforces same-user.

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
    """The uid that owns the client end of a local TCP connection, or None.

    `client`/`server` are (ip, port): the peer and the accepted socket's
    getsockname(). None means unknown and must not be treated as allowed.
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
