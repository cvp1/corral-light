#!/usr/bin/python3
"""hub — Corral Light's server: static PWA, SSE event stream, POST control plane.

Stdlib only.

WHAT THIS SERVER DOES NOT HAVE, AND WHY THAT IS THE POINT
    The full Corral's hub carries fifteen more routes: the fleet mailbox, the
    attention queue, the run registry, the scheduler, the Library index, mail,
    FinOps, delegate boards, tmux adoption. Every one of them reads state that
    only exists on ranch-server. They are not stubbed here — a route that
    answers `{"error": "unavailable"}` is still a surface to maintain and still
    a failure for the browser to render. Light is the Live tab: conversations,
    and the permission rail that unblocks them.

TRANSPORT
    ONE multiplexed SSE stream per browser (every pane's events on one
    connection, so the 6-per-origin cap never bites) plus plain POST for input
    and approvals. Python's stdlib has no WebSocket server and hand-rolling
    RFC 6455 to move text over a LAN is risk with no payoff.

AUTHORITY BOUNDARY
    Answering a permission prompt inside a pane is the AGENT's own tool gate on
    its own host — that is what a conversation is. There is no route here that
    dispatches work anywhere else, at any privilege level.
"""
import json
import mimetypes
import os
import queue
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import auth
import sessions

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def _safe_static_path(rel):
    """Resolve `rel` under STATIC; refuse anything that escapes it.

    A STRING prefix check (`str(f).startswith(str(base))`) is not a
    containment check: if a sibling directory happens to share STATIC's path
    as a string prefix (e.g. `corral-light/static-secret/`),
    `/static/../static-secret/file` resolves OUTSIDE `static/` while its
    string path still starts with the string ".../corral-light/static".
    Comparing the actual path hierarchy with `is_relative_to()` holds
    regardless of what a sibling happens to be named. A pure function so the
    containment logic is testable without a live HTTP request.
    """
    base = STATIC.resolve()
    f = (base / rel).resolve()
    return f if f.is_relative_to(base) else None


# 127.0.0.1 by default, unlike ranch's Corral. Light runs on a personal machine
# that moves between networks — a coffee-shop LAN is not the ranch LAN, and the
# pairing gate should not be the only thing between an arbitrary wifi and an
# agent holding tools in a working tree. Binding wider is a deliberate act:
# CORRAL_LIGHT_BIND=0.0.0.0.
BIND = os.environ.get("CORRAL_LIGHT_BIND", "127.0.0.1")
PORT = int(os.environ.get("CORRAL_LIGHT_PORT", "8098"))
COOKIE = "corral_light"          # its own cookie name, so a browser paired to
                                 # a full Corral on the same host cannot have
                                 # its session silently overwritten by this one
SSE_PING = 20                    # keep proxies and sleeping laptops honest
MAX_BODY = 1 << 20

MGR = sessions.Manager()

# The observer tick. In the full Corral this loop also rebuilt the attention
# queue, projected the run registry, polled the fleet mailbox and drove push
# notifications. Here it does the ONE thing that must not be lost with them:
# call snapshot() on every pane, which is what actually asks the OS whether
# each agent process is still alive and broadcasts the state edge when the
# answer disagrees with our own bookkeeping (busy → uncertain, poll()-detected
# dead). Without it, a pane whose adapter wedged with its pipe open renders a
# healthy pulsing `busy` until someone reloads. A monitor cannot certify its
# own liveness — this one's is exposed as /health's tick_age_s, for a watcher
# outside this process (P21).
TICK_S = 5
_TICK = {"at": 0.0}


def _observe_loop():
    """Poll pane liveness. Failures skip a tick, never kill the thread — a
    dead observer is exactly the silent failure this loop exists to end."""
    while True:
        time.sleep(TICK_S)
        try:
            for p in list(MGR.panes.values()):
                p.snapshot(since=1 << 60)   # for the edge-broadcast side effect
            _TICK["at"] = time.time()
        except Exception:                          # noqa: BLE001
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "corral-light"

    def log_message(self, *a):
        pass                     # silent in steady state (P7)

    # ── plumbing ─────────────────────────────────────────────────────────
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else str(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj), "application/json", extra)

    def _user(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:
            return None
        m = c.get(COOKIE)
        return auth.verify(m.value) if m else None

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ValueError("malformed JSON body")

    def _same_origin(self):
        """A cookie-authed control plane needs CSRF defence. The browser always
        sends Origin on POST; a cross-site form cannot forge it."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True                       # non-browser client (curl, tests)
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    # ── GET ──────────────────────────────────────────────────────────────
    def do_GET(self):
        p = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)

        if p == "/health":
            # Unauthenticated on purpose: an outside watchdog reads this.
            # Non-sensitive by design — liveness and counts, no titles, no
            # content (P20: this can answer on an open port).
            age = int(time.time() - _TICK["at"]) if _TICK["at"] else -1
            panes = list(MGR.panes.values())
            live = sum(1 for x in panes if x.state not in ("dead", "detached"))
            blocked = sum(len(x.pending) for x in panes)
            return self._json({"ok": 1, "service": "corral-light",
                               "tick_age_s": age, "panes_live": live,
                               "permissions_waiting": blocked})

        if p == "/api/pair/new":
            try:
                code, ttl = auth.new_code()
            except auth.TooMany as e:
                return self._json({"error": str(e)}, 429)
            return self._json({"code": code, "ttl": ttl,
                               "how": f"corral-light pair {code}"})
        if p == "/api/pair/claim":
            tok, status = auth.claim((q.get("code") or [""])[0])
            if not tok:
                return self._json({"status": status}, 200)
            return self._json({"status": "ok"}, 200, {
                "Set-Cookie": f"{COOKIE}={tok}; HttpOnly; SameSite=Strict; "
                              f"Path=/; Max-Age={auth.SESSION_TTL}"})

        if p in ("/", "/index.html"):
            return self._static("index.html")
        if p == "/sw.js":
            # Served from the root so its scope covers the whole app. A worker
            # under /static/ could only control /static/*, which is not where
            # the app is.
            return self._static("sw.js")
        if p == "/manifest.json":
            return self._static("manifest.json")
        if p.startswith("/static/"):
            return self._static(p[len("/static/"):])

        user = self._user()
        if not user:
            return self._json({"error": "not paired"}, 401)

        if p == "/api/session/history":
            # Transcript paging: events OLDER than `before` from the on-disk
            # log — the ring in /api/state holds only the tail.
            try:
                pane = MGR.get((q.get("pane") or [""])[0])
                before = int((q.get("before") or ["0"])[0] or 0)
                n = int((q.get("n") or ["200"])[0] or 200)
                return self._json({"events": pane.history(before, n)})
            except (ValueError, KeyError) as e:
                return self._json({"error": str(e)[:200]}, 400)
        if p == "/api/state":
            # ?since={"paneId":seq,...} -> only events after what the client
            # already has.
            since = {}
            raw = (q.get("since") or [None])[0]
            if raw:
                try:
                    since = {str(k): int(v) for k, v in json.loads(raw).items()}
                except (ValueError, AttributeError, TypeError):
                    since = {}
            return self._json(MGR.state(since))
        if p == "/api/stream":
            return self._stream()
        return self._json({"error": "not found"}, 404)

    def _static(self, rel):
        f = _safe_static_path(rel)
        if f is None or not f.is_file():
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self._send(200, f.read_bytes(), ctype)

    def _stream(self):
        """One SSE connection carries every pane's events."""
        q = queue.Queue(maxsize=1000)
        MGR.subscribe(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last = time.time()
            while True:
                try:
                    ev = q.get(timeout=2)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    last = time.time()   # a real event is as good as a ping
                except queue.Empty:
                    if time.time() - last > SSE_PING:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            MGR.unsubscribe(q)

    # ── POST ─────────────────────────────────────────────────────────────
    def do_POST(self):
        p = urlparse(self.path).path
        user = self._user()
        if not user:
            return self._json({"error": "not paired"}, 401)
        if not self._same_origin():
            return self._json({"error": "cross-origin request refused"}, 403)
        try:
            b = self._body()
            if p == "/api/session/new":
                pane = MGR.create(b.get("agent", ""),
                                  b.get("cwd") or str(Path.home()),
                                  b.get("posture") or sessions.DEFAULT_POSTURE,
                                  (b.get("model") or "").strip() or None,
                                  (b.get("effort") or "").strip() or None)
                return self._json({"ok": True, "pane": pane.snapshot()})
            if p == "/api/session/send":
                MGR.get(b.get("pane", "")).send(b.get("text", ""))
                return self._json({"ok": True})
            if p == "/api/session/permission":
                pane = MGR.get(b.get("pane", ""))
                ok = pane.answer(b.get("requestId", ""), b.get("optionId", ""))
                return self._json({"ok": True, "delivered": ok})
            if p == "/api/session/resume":
                pane = MGR.resume(b.get("pane", ""))
                return self._json({"ok": pane.state != "dead",
                                   "pane": pane.snapshot()})
            if p == "/api/session/config":
                r = MGR.get(b.get("pane", "")).set_config(
                    (b.get("configId") or "").strip(),
                    (b.get("value") or "").strip())
                return self._json({"ok": True, "config": r})
            if p == "/api/session/pause":
                # Stop the process, keep the conversation. Close was the only
                # exit, so interrupted work had nowhere to sit.
                pane = MGR.pause(b.get("pane", ""))
                return self._json({"ok": True, "pane": pane.snapshot()})
            if p == "/api/session/reopen":
                pane = MGR.reopen(b.get("pane", ""))
                return self._json({"ok": True, "pane": pane.snapshot()})
            if p == "/api/session/order":
                return self._json({"ok": True,
                                   "set": MGR.reorder(b.get("ids") or [])})
            if p == "/api/session/pin":
                return self._json({"ok": True, "pinned":
                                   MGR.set_pinned(b.get("pane", ""),
                                                  b.get("pinned", True))})
            if p == "/api/session/rename":
                t = MGR.get(b.get("pane", "")).rename(b.get("title", ""))
                return self._json({"ok": True, "title": t})
            if p == "/api/session/minimize":
                m = MGR.get(b.get("pane", "")).set_minimized(
                    b.get("minimized", True))
                return self._json({"ok": True, "minimized": m})
            if p == "/api/session/cancel":
                return self._json({"ok": MGR.get(b.get("pane", "")).cancel()})
            if p == "/api/session/close":
                MGR.close(b.get("pane", ""))
                return self._json({"ok": True})
            if p == "/api/session/forget":
                return self._json({"ok": True,
                                   "forgot": MGR.forget(b.get("pane", ""))})
            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:                      # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"[:300]}, 500)


def serve(bind=BIND, port=PORT):
    threading.Thread(target=_observe_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True
    print(f"corral-light: http://{bind}:{port}/")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
