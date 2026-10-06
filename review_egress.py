"""The blind reviewer's only way out to the network (docs/ux-10x-plan.md §2.5).

The reviewer sandbox unshares the network namespace, so nothing on the
host's loopback (other agents' harnesses, sync and print daemons, this hub)
and no abstract unix socket is reachable from inside. The vendor CLI still
needs its API, so:

- the hub runs an `Egress` proxy per reviewer on a unix socket inside the
  reviewer's pane dir (the sandbox binds that dir, so the socket is
  reachable by path, the one thing a new network namespace does not cut);
- inside, `shim` (this file run as a script) listens on the sandbox's own
  loopback and forwards each connection to that socket, then runs the lane
  with HTTPS_PROXY pointing at it;
- the proxy allows `CONNECT host:443` only, only to the lane's own vendor
  domains, only when every address the name resolves to is public. Every
  host asked for is recorded, allowed or not.
"""
import ipaddress
import os
import select
import socket
import sys
import threading

SHIM_PORT = 18443                   # on the sandbox's own loopback
PORTS = (443,)
MAX_HEADER = 8192
MAX_CONNECTIONS = 32               # per reviewer: a flood costs it, not the hub

# Each lane's vendor domains (suffix match). A lane reaching anything else
# is refused, and the refusal is shown on the challenge.
LANE_DOMAINS = {
    "claude": ("anthropic.com", "claude.com"),
    "codex": ("openai.com", "chatgpt.com"),
    "grok": ("x.ai", "grok.com"),
    "gemini": ("googleapis.com", "google.com"),
}


# Sign-in hosts a reviewer must never reach. These vendors rotate refresh
# tokens: a renewal inside the sandbox spends the shared refresh token and
# cannot save its replacement, which signs the operator out of that lane
# everywhere. The hub checks a token has enough life before a reviewer starts
# (review_sandbox.login_seconds_left) instead. Google does not rotate, so
# Gemini may renew.
LANE_DENY = {
    "claude": ("platform.claude.com", "console.anthropic.com", "claude.ai"),
    "codex": ("auth.openai.com",),
    "grok": ("auth.x.ai", "accounts.x.ai"),
}


def host_allowed(host, lane, extra=()):
    host = host.lower().rstrip(".")
    for d in LANE_DENY.get(lane, ()):
        if host == d or host.endswith("." + d):
            return False
    for d in tuple(LANE_DOMAINS.get(lane, ())) + tuple(extra):
        if host == d or host.endswith("." + d):
            return True
    return False


def public_addresses(host, port):
    """Every address `host` resolves to, or None if any is not public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    out = []
    for fam, _t, _p, _c, sa in infos:
        ip = ipaddress.ip_address(sa[0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return None
        out.append((fam, sa))
    return out or None


def _pipe(a, b):
    socks = [a, b]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 300)
            if x or not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


class Egress:
    """A CONNECT proxy on a unix socket for one reviewer. `on_host(host,
    allowed)` is called for every request."""

    def __init__(self, path, lane, on_host=None):
        self.path, self.lane = str(path), lane
        self.on_host = on_host or (lambda host, allowed: None)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        os.chmod(self.path, 0o600)
        self.sock.listen(64)
        self.closed = False
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        threading.Thread(target=self._serve, daemon=True, name="review-egress").start()

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _serve(self):
        while not self.closed:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                c.close()                     # over its share: refused, nothing spawned
                continue
            threading.Thread(target=self._held, args=(c,), daemon=True).start()

    def _held(self, c):
        try:
            self._one(c)
        finally:
            self._slots.release()

    def _one(self, c):
        try:
            c.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = c.recv(1024)
                if not chunk:
                    c.close()
                    return
                head += chunk
                if len(head) > MAX_HEADER:
                    raise ValueError("header too long")
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split()
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                return self._refuse(c, "only CONNECT", "-")
            host, _, port = parts[1].rpartition(":")
            host = host.strip("[]")
            if not port.isdigit() or int(port) not in PORTS:
                return self._refuse(c, "port", parts[1])
            if not host_allowed(host, self.lane):
                return self._refuse(c, "not allowed for this lane", host)
            addrs = public_addresses(host, int(port))
            if not addrs:
                return self._refuse(c, "not a public address", host)
            up = None
            for fam, sa in addrs:
                try:
                    up = socket.create_connection(sa[:2], timeout=15)
                    break
                except OSError:
                    continue
            if up is None:
                return self._refuse(c, "unreachable", host, code=b"502 Bad Gateway")
            self.on_host(host, True)
            c.settimeout(None)
            up.settimeout(None)
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            rest = head.split(b"\r\n\r\n", 1)[1]
            if rest:
                up.sendall(rest)
            _pipe(c, up)
        except Exception:                             # noqa: BLE001 — one connection, never the hub
            try:
                c.close()
            except OSError:
                pass

    def _refuse(self, c, why, host, code=b"403 Forbidden"):
        self.on_host(host, False)
        try:
            c.sendall(b"HTTP/1.1 " + code + b"\r\nContent-Length: 0\r\n\r\n")
        finally:
            c.close()


def shim(argv):
    """In the sandbox: forward 127.0.0.1:SHIM_PORT to the egress socket, then
    run the lane. `argv`: SOCKET -- COMMAND..."""
    sock_path, sep, cmd = argv[0], argv[1], argv[2:]
    if sep != "--" or not cmd:
        print("usage: review_egress.py SOCKET -- COMMAND...", file=sys.stderr, flush=True)
        return 2
    lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lst.bind(("127.0.0.1", SHIM_PORT))
    lst.listen(64)
    pid = os.fork()
    if pid == 0:                                       # the forwarder
        os.setsid()
        while True:
            c, _ = lst.accept()
            u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                u.connect(sock_path)
            except OSError:
                c.close()
                u.close()
                continue
            threading.Thread(target=_pipe, args=(c, u), daemon=True).start()
    lst.close()
    proxy = f"http://127.0.0.1:{SHIM_PORT}"
    env = dict(os.environ)
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        env[k] = proxy
    # The sandbox's own loopback reaches nothing on the host (its own network
    # namespace), and lanes talk to their own helpers there.
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    os.execvpe(cmd[0], cmd, env)


if __name__ == "__main__":
    sys.exit(shim(sys.argv[1:]))
