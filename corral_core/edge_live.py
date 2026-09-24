"""Behavioural proof of the edge guards against a REAL hub Handler on a socket.

The first cut of these guards was tested by string-matching hub.py, and the
2026-09-24 review (Astra 6, Gemini 6) showed the guards could be disabled with
every such test still green. These checks drive the actual Handler class of
whichever skin passes itself in, over real TCP, and assert status codes, side
effects and cleanup. Both suites call `run(hub, auth)`; it returns a list of
failure strings (empty = pass).

Loopback is the only peer a test can have, so the peer-address rules (tailnet
direct refused; identity header from a non-proxy peer refused) are proved as
pure functions in test_edge.py, and here only through what loopback can show.
"""
import http.client
import socket
import threading
import time
from http.server import ThreadingHTTPServer

from corral_core import edge

ME = "craig@example.com"


def _raw(port, data, read_s=3.0):
    """Send raw bytes; return everything the server sends until it closes or
    `read_s` passes. Returns (bytes, closed_by_server)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=read_s)
    s.sendall(data)
    out, closed = b"", False
    end = time.time() + read_s
    try:
        while time.time() < end:
            chunk = s.recv(65536)
            if not chunk:
                closed = True
                break
            out += chunk
    except socket.timeout:
        pass
    finally:
        s.close()
    return out, closed


def _get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body, r


def run(hub, auth):
    fails = []

    def check(ok, msg):
        if not ok:
            fails.append(msg)

    saved = (hub.BOUND_LOGIN, hub.STREAM_RECHECK)
    hub.BOUND_LOGIN, hub.STREAM_RECHECK = ME, 1
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # 1. a Serve identity that is not the bound login is refused
        st, _, _ = _get(port, "/api/pair/new", {edge.TS_LOGIN: "eve@example.com"})
        check(st == 403, f"wrong tailnet identity was not refused (got {st})")
        # 2. proxied without identity (Funnel / tagged device) is refused
        st, _, _ = _get(port, "/api/pair/new", {edge.FORWARDED_FOR: "203.0.113.9"})
        check(st == 403, f"proxied-without-identity was not refused (got {st})")
        # 3. local, unproxied request is unchanged
        st, _, _ = _get(port, "/api/pair/new")
        check(st == 200, f"a plain local request was refused (got {st})")

        # 4. request smuggling: a refused POST's body must not be parsed as
        #    a second request on the same connection.
        inner = b"GET /api/pair/new HTTP/1.1\r\nHost: x\r\n\r\n"
        outer = (b"POST /api/session/close HTTP/1.1\r\nHost: x\r\n"
                 b"Tailscale-User-Login: eve@example.com\r\n"
                 b"Content-Type: application/json\r\n"
                 b"Content-Length: " + str(len(inner)).encode() + b"\r\n\r\n" + inner)
        out, closed = _raw(port, outer)
        check(out.count(b"HTTP/1.1 ") == 1,
              f"a refused POST's body was answered as a second request "
              f"({out.count(b'HTTP/1.1 ')} responses)")
        check(closed, "the connection stayed open after a refused POST")
        # 4b. the same for an unpaired POST (the pre-existing 401 path)
        outer2 = (b"POST /api/session/close HTTP/1.1\r\nHost: x\r\n"
                  b"Content-Length: " + str(len(inner)).encode() + b"\r\n\r\n" + inner)
        out, closed = _raw(port, outer2)
        check(out.count(b"HTTP/1.1 ") == 1 and closed,
              "an unpaired POST's body was answered as a second request")

        # 5. a Serve-audience cookie works only through Serve
        serve_tok = auth.mint(user=edge.SERVE_USER)
        ck = {"Cookie": f"{hub.COOKIE}={serve_tok}"}
        st, _, _ = _get(port, "/api/state", ck)
        check(st == 401, f"a Serve cookie was accepted without Serve (got {st})")
        st, _, _ = _get(port, "/api/state", {**ck, edge.TS_LOGIN: ME})
        check(st == 200, f"a Serve cookie was refused through Serve (got {st})")
        lan_tok = auth.mint()
        st, _, _ = _get(port, "/api/state", {"Cookie": f"{hub.COOKIE}={lan_tok}"})
        check(st == 200, f"a LAN cookie stopped working locally (got {st})")

        # 5b. Grok (2026-09-24): a PAIRED POST whose Content-Length is bad or
        #     over the cap is refused with its body still on the socket; a GET
        #     that carries a body likewise. Neither may answer the body.
        ck_lan = f"Cookie: {hub.COOKIE}={lan_tok}\r\n".encode()
        for label, cl in (("oversized", b"99999999"), ("malformed", b"abc")):
            req = (b"POST /api/session/close HTTP/1.1\r\nHost: x\r\n" + ck_lan +
                   b"Content-Length: " + cl + b"\r\n\r\n" + inner)
            out, closed = _raw(port, req)
            check(out.count(b"HTTP/1.1 ") == 1 and closed,
                  f"a {label}-Content-Length POST's body was answered as a second "
                  f"request ({out.count(b'HTTP/1.1 ')} responses, closed={closed})")
        req = (b"GET /api/state HTTP/1.1\r\nHost: x\r\n" + ck_lan +
               b"Content-Length: " + str(len(inner)).encode() + b"\r\n\r\n" + inner)
        out, closed = _raw(port, req)
        check(out.count(b"HTTP/1.1 ") == 1 and closed,
              "a GET's body was answered as a second request")

        # 6. Secure only when claimed through Serve (via pairing)
        for via, want in ((True, True), (False, False)):
            h = {edge.TS_LOGIN: ME} if via else {}
            _, body, _ = _get(port, "/api/pair/new", h)
            import json
            code = json.loads(body).get("code")
            if not code:
                check(False, f"could not mint a pairing code: {body[:120]!r}")
                continue
            auth.approve(code)
            st, _, r = _get(port, f"/api/pair/claim?code={code}", h)
            sc = r.getheader("Set-Cookie") or ""
            check(("Secure" in sc) == want,
                  f"Secure flag wrong for via_serve={via}: {sc!r}")
            if via:
                tok = sc.split(";", 1)[0].split("=", 1)[1]
                check(auth.verify(tok) == edge.SERVE_USER,
                      "a Serve claim did not mint the Serve audience")

        # 7. an open stream re-verifies, says `expired`, closes, and cleans up
        base = len(hub.MGR.subscribers)
        short = auth.mint(ttl=2)
        req = (f"GET /api/stream HTTP/1.1\r\nHost: x\r\n"
               f"Cookie: {hub.COOKIE}={short}\r\n\r\n").encode()
        out, closed = _raw(port, req, read_s=12)
        check(b"event: expired" in out, "an expired stream did not say so")
        check(closed, "an expired stream left its connection open")
        time.sleep(0.3)
        check(len(hub.MGR.subscribers) == base,
              f"stream subscriber leaked ({len(hub.MGR.subscribers)} vs {base})")

        # 8. a reset while the stream's headers are written must not leak the
        #    subscriber (the queue used to be added outside the try/finally).
        class Resetting(hub.Handler):
            def end_headers(self):
                if self.path.startswith("/api/stream"):
                    raise BrokenPipeError("client reset mid-headers")
                return super().end_headers()
        srv2 = ThreadingHTTPServer(("127.0.0.1", 0), Resetting)
        srv2.daemon_threads = True
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            base = len(hub.MGR.subscribers)
            for _ in range(3):
                _raw(srv2.server_address[1],
                     (f"GET /api/stream HTTP/1.1\r\nHost: x\r\n"
                      f"Cookie: {hub.COOKIE}={lan_tok}\r\n\r\n").encode(), read_s=2)
            time.sleep(0.3)
            check(len(hub.MGR.subscribers) == base,
                  f"a reset during stream headers leaked subscribers "
                  f"({len(hub.MGR.subscribers)} vs {base})")
        finally:
            srv2.shutdown()
            srv2.server_close()
    finally:
        srv.shutdown()
        srv.server_close()
        hub.BOUND_LOGIN, hub.STREAM_RECHECK = saved
    return fails
