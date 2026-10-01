#!/usr/bin/python3
"""hub — Corral Light's server: static PWA, SSE event stream, POST control plane.

Stdlib only.

WHAT THIS SERVER DOES NOT HAVE, AND WHY THAT IS THE POINT
    The full Corral's hub carries fifteen more routes: the fleet mailbox, the
    attention queue, the run registry, the scheduler, the Library index, mail,
    FinOps, delegate boards, tmux adoption. Every one of them reads state that
    only exists on linux-host. They are not stubbed here — a route that
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
import signal
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Checked HERE, before anything imports, because the alternative is worse than
# a hard exit: on 3.8 the static-path containment check (`Path.is_relative_to`,
# 3.9+) raises AttributeError inside a request handler — a route that 500s
# rather than a startup that refuses, i.e. a security check failing OPEN-ish on
# a version nobody tested. Degrade toward safety, loudly (P4).
if sys.version_info < (3, 9):
    raise SystemExit(
        f"corral-light needs Python 3.9 or newer; this is "
        f"{sys.version.split()[0]}. (Path.is_relative_to, used by the "
        f"static-file containment check, arrived in 3.9.)")

import auth
import claude_auth
import claude_login
import notify
import sessions
from corral_core import edge

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
#
# In the container (CORRAL_CONTAINER=1) the default is 0.0.0.0, because the
# host's published port arrives on the container's bridge interface, not its
# loopback. The exposure decision moves to the compose file, which publishes
# on host 127.0.0.1/::1 only (T-NET-1). Serve trust is unaffected: edge.py
# grants it to loopback peers only, and the bridge gateway is not loopback.
CONTAINER = os.environ.get("CORRAL_CONTAINER") == "1"
BIND = os.environ.get("CORRAL_LIGHT_BIND", "0.0.0.0" if CONTAINER else "127.0.0.1")
PORT = int(os.environ.get("CORRAL_LIGHT_PORT", "8098"))
COOKIE = "corral_light"          # its own cookie name, so a browser paired to
                                 # a full Corral on the same host cannot have
                                 # its session silently overwritten by this one
SSE_PING = 20                    # keep proxies and sleeping laptops honest
STREAM_RECHECK = 30              # re-verify the cookie behind an open SSE stream
# Bind the pairing cookie to ONE tailnet identity when Tailscale Serve fronts
# the hub (corral_core/edge.py). Unset = LAN behaviour, unchanged.
BOUND_LOGIN = (os.environ.get("CORRAL_TAILSCALE_LOGIN") or "").strip() or None
MAX_BODY = 1 << 20


def parse_content_length(raw, cap=MAX_BODY):
    """Refuse negative and oversize Content-Length. `read(-1)` is EOF."""
    try:
        n = int(raw if raw not in (None, "") else 0)
    except (TypeError, ValueError) as e:
        raise ValueError("bad Content-Length") from e
    if n < 0 or n > cap:
        raise ValueError("body too large")
    return n


FRAME_LOCK = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
)
# How much of a note a chat-only lane gets quoted into its composer. Bounded
# (P8) and deliberately modest: this text goes into a context window, it is
# visible in the box before anything is sent, and a note that does not fit is
# a note to open in a lane that can read files.
ATTACH_EXCERPT_CHARS = 6000

MGR = sessions.Manager()


def _login_signed_in():
    """A real sign-in (claude_login judged all three facts): read the new
    credential now rather than in CACHE_S, and bring back what it killed."""
    claude_auth.status(force=True)
    MGR.auth_sweep()


# DESIGN-6 S4: the vendor's own login, started on a click, in a window on
# this machine's screen. Its watch thread is its own; the tick never waits on
# it (a `claude auth status` that hangs must not freeze tick_age_s).
LOGIN = claude_login.Login(sessions.STATE, on_success=_login_signed_in)
LOGIN_FROM = ("rail", "banner", "picker", "composer")

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
_TICK = {"at": 0.0, "errors": 0}


def _observe_loop():
    """Poll pane liveness. Failures skip a PANE, never the tick or the thread.

    The tick advances per pane, inside the loop (Astra and Grok 2026-09-28,
    P0-d): it used to update only after the whole loop, so ONE pane whose
    snapshot() raised froze tick_age_s for the entire hub — and a watchdog
    judging that number would page (or, as first planned, restart and kill
    twelve healthy panes) over one bad row. A failing pane is counted in
    `errors`, which /health reports, instead of hiding the observer's pulse.
    """
    while True:
        time.sleep(TICK_S)
        _observe_once()


def _observe_once():
    try:
        panes = list(MGR.panes.values())
    except Exception:                              # noqa: BLE001
        panes = []
    for p in panes:
        try:
            p.snapshot(since=1 << 60)       # for the edge-broadcast side effect
        except Exception:                          # noqa: BLE001
            _TICK["errors"] += 1
        _TICK["at"] = time.time()
    _TICK["at"] = time.time()               # an empty roster still ticks
    # The Claude login, on the same pulse: warn before it lapses, and bring
    # back what it killed once the operator has signed in again (2026-09-30).
    try:
        MGR.auth_sweep()
    except Exception:                              # noqa: BLE001
        _TICK["errors"] += 1


# ── needs-you, off the glass (P0-e'; Astra and Grok 2026-09-28) ─────────────
# The first plan notified when MGR.subscribers was empty. Both reviews: an SSE
# subscriber proves a stream is OPEN, not that a human is LOOKING — a
# backgrounded tab keeps one, which is exactly when you are not looking. So
# the browser (and the CLI, when it prints) reports the highest seq a human
# surface actually showed, per pane, and a `permission` or `dead` event that
# nobody has seen after a grace period becomes a desktop notification.
# In memory on purpose: after a restart nothing has been seen, which errs
# toward telling you.
NOTIFY_GRACE_S = 20         # a focused browser acks within a second or two;
                            # twenty means "nobody looked", not "slow network"
NOTIFY_KINDS = ("permission", "dead")
SEEN = {}                   # pane id -> highest seq a human surface showed
_NOTIFY_PENDING = {}        # pane id -> newest unseen notifiable event
_NOTIFY_LOCK = threading.Lock()


def pending_payloads(pane):
    """What a human must see to answer each pending card, oldest first.

    Built from the record answer() enforces (`_gate`), never from the event
    ring. An oversize payload is withheld exactly as the browser withholds
    it: only refusal is possible for bytes nobody can be shown.
    """
    out = []
    for rid, req in list(pane.pending.items()):
        gate = req.get("_gate") or {}
        tc = req.get("toolCall") or {}
        over = bool(gate.get("oversize"))
        out.append({"requestId": rid, "title": tc.get("title"),
                    "kind": tc.get("kind"), "digest": gate.get("digest"),
                    "bytes": gate.get("bytes"), "oversize": over,
                    "rawInput": None if over else tc.get("rawInput"),
                    "content": [] if over else (tc.get("content") or []),
                    "locations": [] if over else (tc.get("locations") or []),
                    "options": req.get("options") or []})
    return out


def mark_seen(pane_id, seq):
    MGR.get(pane_id)                     # ValueError for a pane that is not here
    seq = int(seq)
    with _NOTIFY_LOCK:
        if seq > SEEN.get(pane_id, 0):
            SEEN[pane_id] = seq
    return SEEN[pane_id]


def _notify_event(ev):
    """Consider one broadcast event; schedule at most one check per pane."""
    if ev.get("kind") not in NOTIFY_KINDS or not ev.get("pane"):
        return
    pid = ev["pane"]
    with _NOTIFY_LOCK:
        first = pid not in _NOTIFY_PENDING
        _NOTIFY_PENDING[pid] = ev          # coalesce: the newest one is checked
    if first:
        t = threading.Timer(NOTIFY_GRACE_S, _notify_check, args=(pid,))
        t.daemon = True
        t.start()


def _notify_check(pane_id):
    with _NOTIFY_LOCK:
        ev = _NOTIFY_PENDING.pop(pane_id, None)
        seen = SEEN.get(pane_id, 0)
    if ev is None or seen >= ev.get("seq", 0):
        return None                         # a human saw it
    p = MGR.panes.get(pane_id)
    if p is None:
        return None                         # closed meanwhile
    d = ev.get("data") or {}
    if ev["kind"] == "permission":
        if d.get("requestId") not in getattr(p, "pending", {}):
            return None                     # answered meanwhile
        title = f"{p.title} needs you"
        body = f"permission: {d.get('title') or d.get('kind') or 'a tool call'}"
    else:
        if p.state != "dead":
            return None                     # already resumed
        title = f"{p.title} stopped"
        body = str(d.get("reason") or "the agent exited")
    shown, why = notify.desktop(f"Corral Light — {title}", body)
    return shown, why


def _notify_loop():
    """Subscribe like a browser and feed _notify_event. Never dies."""
    q = queue.Queue(maxsize=1000)
    MGR.subscribe(q)
    while True:
        try:
            _notify_event(q.get())
        except Exception:                          # noqa: BLE001
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "corral-light"

    def log_message(self, *a):
        pass                     # silent in steady state (P7)

    # ── plumbing ─────────────────────────────────────────────────────────
    def _carries_body(self):
        te = self.headers.get("Transfer-Encoding")
        cl = (self.headers.get("Content-Length") or "").strip()
        return bool(te) or cl not in ("", "0")

    def _send(self, code, body, ctype="application/json", extra=None):
        if self._carries_body() and not getattr(self, "_body_read", False):
            # Answering a request without reading its body leaves those bytes
            # on a keep-alive socket, where they parse as the NEXT request —
            # with no Serve headers, so past the identity gate (Astra 1,
            # 2026-09-24, reproduced). Grok, same day: the flag used to be set
            # BEFORE the read, so a bad/oversized Content-Length 400 stayed
            # keep-alive, and a GET with a body was never covered. Any answer
            # to a request whose body we did not consume ends the connection.
            self.close_connection = True
        data = body if isinstance(body, bytes) else str(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in FRAME_LOCK:
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj), "application/json", extra)

    def _token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:
            return None
        m = c.get(COOKIE)
        return m.value if m else None

    def _peer_route(self, p, method):
        """DESIGN-5 S8: the seat tools' routes. Runs BEFORE the cookie check
        and never reads the cookie: the pane token alone decides who is
        sending (sessions.ManagerBase.peer_http), so a browser session can
        never be used to send as a pane. Answers only a caller on THIS
        machine -- the MCP child dials loopback by construction."""
        import ipaddress
        peer = self._peer()
        try:
            local = peer is not None and (ipaddress.ip_address(peer).is_loopback
                                          or peer == BIND)
        except ValueError:
            local = False
        if not local:
            return self._json({"error": "peer routes answer only on this machine"}, 403)
        # SAME UNIX USER, checked by the kernel, not assumed (measured
        # 2026-09-29: the Claude adapter puts the pane token on a world-
        # readable command line). On Linux the calling socket's owner is in
        # /proc/net/tcp; unknown is refused, never waved through (P4). Other
        # platforms have no such table here -- the README says so.
        if sys.platform.startswith("linux"):
            uid = edge.local_peer_uid(self.client_address,
                                      self.connection.getsockname())
            if uid != os.getuid():
                return self._json({"error": "peer routes answer only the hub's own "
                                            "UNIX user" + ("" if uid is not None else
                                            " (the caller could not be identified)")},
                                  403)
        if method == "POST":
            try:
                body = self._body()
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        else:       # a GET's arguments are its query (S11's /api/peer/turn)
            body = {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items()}
        status, obj = MGR.peer_http(method, p,
                                    self.headers.get(sessions._core.PEER_TOKEN_HEADER),
                                    body)
        return self._json(obj, status)

    def _peer(self):
        return self.client_address[0] if self.client_address else None

    def _user(self):
        tok = self._token()
        user = auth.verify(tok) if tok else None
        return user if user and edge.audience_ok(user, self.headers, self._peer()) else None

    def _edge_refused(self):
        """Identity binding for a hub fronted by Tailscale Serve (corral_core/
        edge.py). Unbound (no CORRAL_TAILSCALE_LOGIN) => never refuses."""
        ok, why = edge.identity_ok(self.headers, BOUND_LOGIN, self._peer())
        if ok:
            return False
        self.close_connection = True   # never parse an unread body as a request
        self._json({"error": "tailnet identity refused: " + why}, 403)
        return True

    def _body(self):
        n = parse_content_length(self.headers.get("Content-Length", 0))
        try:
            raw = self.rfile.read(n)
            self._body_read = True   # only now are the bytes off the socket
            return json.loads(raw or b"{}")
        except ValueError:
            raise ValueError("malformed JSON body")

    def _local_human(self):
        """Is the person who clicked sitting at THIS machine? Loopback socket,
        and no proxy hop of any kind: Tailscale Serve reaches us from loopback
        too, so its identity header (or any forwarding header) means the
        browser is somewhere else, and a window opened here would sit on an
        unattended desk."""
        if edge._peer_kind(self._peer()) != "loopback":
            return False
        return not any(self.headers.get(h) for h in
                       ("X-Forwarded-For", "Forwarded", "X-Real-IP",
                        edge.TS_LOGIN))

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
        self._body_read = False
        q = parse_qs(urlparse(self.path).query)

        if p == "/health":
            # Unauthenticated on purpose: an outside watchdog reads this.
            # Non-sensitive by design — liveness and counts, no titles, no
            # content (P20: this can answer on an open port).
            age = int(time.time() - _TICK["at"]) if _TICK["at"] else -1
            panes = list(MGR.panes.values())
            live = sum(1 for x in panes if x.state not in ("dead", "detached"))
            blocked = sum(len(x.pending) for x in panes)
            # orphans_reaped: adapters a PREVIOUS hub left running that this
            # one stopped at boot (Grok 2026-09-28). The count the pane-host
            # decision (review §6, step 8) is waiting on.
            reaped = sum(1 for v in getattr(MGR, "orphans", {}).values()
                         if v in ("reaped", "killed"))
            return self._json({"ok": 1, "service": "corral-light",
                               "tick_age_s": age, "panes_live": live,
                               "permissions_waiting": blocked,
                               "orphans_reaped": reaped,
                               "tick_errors": _TICK.get("errors", 0),
                               "not_restored": getattr(MGR, "not_restored", 0)})

        if self._edge_refused():
            return

        # DESIGN-5 S8: token-only routes, before any cookie is looked at.
        if p.startswith("/api/peer/"):
            return self._peer_route(p, "GET")

        if p == "/api/pair/new":
            try:
                code, ttl = auth.new_code()
            except auth.TooMany as e:
                return self._json({"error": str(e)}, 429)
            return self._json({"code": code, "ttl": ttl,
                               "how": f"corral-light pair {code}",
                               "host": auth.host_id()})
        if p == "/api/pair/claim":
            tok, status = auth.claim((q.get("code") or [""])[0])
            if not tok:
                return self._json({"status": status}, 200)
            serve = edge.via_serve(self.headers, self._peer())
            if serve:
                tok = auth.mint(user=edge.SERVE_USER)   # good only via Serve
            return self._json({"status": "ok"}, 200, {
                "Set-Cookie": edge.cookie_header(
                    COOKIE, tok, auth.SESSION_TTL, secure=serve)})

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

        # Rigs (DESIGN-5 S12): one surface for both products, in the core,
        # and only past the pairing check above.
        if p == "/api/session/rigs":
            from corral_core import rigs
            st, out = rigs.route(MGR, "GET", p)
            return self._json(out, st)

        if p == "/api/search":
            # Content search for the palette. Additive: a broken or missing
            # index degrades to an error string IN the payload and an empty
            # hit list, never a non-200 that would make the palette look
            # broken when only one of its four sources is.
            import content
            return self._json(content.search((q.get("q") or [""])[0]))
        if p == "/api/content/status":
            # What the index knows — for the palette's empty state, so "no
            # results" can distinguish "nothing matches" from "you have not
            # pointed this at anything yet".
            import content
            return self._json(content.status())
        if p == "/api/session/search":
            # What was SAID in any pane, live or archived (transcripts.py).
            # A broken index degrades to an error string in the payload.
            import transcripts
            try:
                return self._json(transcripts.search(
                    (q.get("q") or [""])[0],
                    limit=int((q.get("n") or ["30"])[0] or 30),
                    pane=(q.get("pane") or [None])[0]))
            except Exception as e:                  # noqa: BLE001
                return self._json({"hits": [], "error": str(e)[:200]})
        if p == "/api/session/digest":
            import transcripts
            live = {x.id for x in list(MGR.panes.values())
                    if x.state not in ("dead", "detached")}
            try:
                hours = float((q.get("hours") or ["24"])[0] or 24)
            except ValueError:
                hours = 24.0
            return self._json({"text": transcripts.digest(hours, live=live)})
        if p == "/api/session/schedule":
            return self._json({"jobs": MGR.schedule.list()})
        if p == "/api/session/roles":
            # Every role, with the lanes each one can start on and why not
            # (roles.py). A broken role tree degrades the dialog to "no
            # roles", never to a 500.
            try:
                import roles
                out = [dict(r, resolvableOn=roles.resolvable_on(r["id"]))
                       if not r.get("error") else r for r in roles.list_roles()]
                return self._json({"roles": out, "dir": str(roles.roles_dir())})
            except Exception as e:                  # noqa: BLE001
                return self._json({"roles": [], "error": str(e)[-200:]})
        if p == "/api/session/pending":
            # The AUTHORITATIVE pending permission payloads, with the digest
            # an approval must carry (Astra/Grok 2026-09-28, CLI). The event
            # ring is a bounded presentation cache — a permission older than
            # MAX_EVENTS has left it while still blocking the agent — so a
            # terminal answering a card reads it from pane.pending, the same
            # record answer() checks the digest against.
            try:
                pane = MGR.get((q.get("pane") or [""])[0])
                return self._json({"pane": pane.id, "state": pane.state,
                                   "pending": pending_payloads(pane)})
            except ValueError as e:
                return self._json({"error": str(e)[:200]}, 400)
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
            out = MGR.state(since)
            out["claudeLogin"] = LOGIN.snapshot()
            return self._json(out)
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
        try:
            # Subscribe INSIDE the try: a reset while the headers are being
            # written used to skip the finally and leak the queue (Astra 5,
            # Gemini 5, 2026-09-24).
            MGR.subscribe(q)
            self._stream_body(q)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            MGR.unsubscribe(q)
            # HTTP/1.1 keep-alive would otherwise hold the socket open after an
            # expired stream returns (measured 2026-09-24). A finished stream
            # ends its connection.
            self.close_connection = True

    def _stream_body(self, q):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in FRAME_LOCK:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b": connected\n\n")
        self.wfile.flush()
        last = time.time()
        checked = time.time()
        while True:
            # The cookie was verified when this stream OPENED and never
            # again, so a stolen cookie's transcript feed outlived its
            # 12h TTL (Astra finding 3, 2026-09-24). Re-verify on a clock.
            if time.time() - checked > STREAM_RECHECK:
                checked = time.time()
                if not self._user():
                    self.wfile.write(b"event: expired\ndata: {}\n\n")
                    self.wfile.flush()
                    break
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

    # ── POST ─────────────────────────────────────────────────────────────
    def do_POST(self):
        p = urlparse(self.path).path
        self._body_read = False
        if self._edge_refused():
            return
        # DESIGN-5 S8: token-only routes, before any cookie is looked at.
        if p.startswith("/api/peer/"):
            return self._peer_route(p, "POST")
        user = self._user()
        if not user:
            return self._json({"error": "not paired"}, 401)
        if not self._same_origin():
            return self._json({"error": "cross-origin request refused"}, 403)
        try:
            b = self._body()
            if p in ("/api/session/rigs/save", "/api/session/rigs/up",
                     "/api/session/rigs/rm"):
                from corral_core import rigs
                r = rigs.route(MGR, "POST", p, b, by=user)
                if r is not None:
                    return self._json(r[1], r[0])
            if p == "/api/claude/login":
                # The requester is the hub's own words (which surface, which
                # pane), never request text: none of it reaches the argv.
                where = b.get("from") if b.get("from") in LOGIN_FROM else None
                pane = b.get("pane") if b.get("pane") in MGR.panes else None
                who = f"pane {pane}" if pane else "browser"
                r = LOGIN.start(f"{who} ({where})" if where else who,
                                local=self._local_human())
                return self._json(r, 200 if r["state"] != "refused" else 403)
            if p == "/api/session/new":
                agent = b.get("agent", "")
                posture = b.get("posture") or sessions.DEFAULT_POSTURE
                effort = (b.get("effort") or "").strip() or None
                role = (b.get("role") or "").strip() or None
                role_sha, preamble, notes = None, "", []
                if role:
                    # Resolved before anything is created, so a refusal costs
                    # no process and reads as a message on the dialog.
                    import roles
                    try:
                        r = roles.resolve(role, lane=agent or None,
                                          posture=b.get("posture") or None,
                                          effort=effort)
                    except roles.RoleError as e:
                        return self._json({"error": str(e)[:400]}, 400)
                    agent, effort = r.agent, r.effort
                    posture = r.posture or sessions.DEFAULT_POSTURE
                    role_sha, preamble, notes = r.sha256, r.preamble, r.notes
                pane = MGR.create(agent, b.get("cwd") or str(sessions.default_cwd()),
                                  posture, (b.get("model") or "").strip() or None,
                                  effort, role=role, role_sha=role_sha)
                # The preamble comes BACK rather than being sent from here:
                # it lands in the composer, visible, and goes as the first
                # turn only when you press send (P17).
                return self._json({"ok": True, "pane": pane.snapshot(),
                                   "preamble": preamble, "notes": notes})
            if p == "/api/content/attach":
                # What "attach a note to a pane" MEANS lives here, in one
                # place, because it is not the same thing for every lane and
                # the difference is load-bearing:
                #
                #   a lane WITH tools  -> a reference. The agent opens the file
                #       itself, through its own permission gate, so the bytes
                #       reach the model the same way any other file it reads
                #       does — visible in the transcript, refusable in the
                #       rail. Corral does not smuggle file contents into a
                #       prompt behind the gate's back.
                #   a lane WITHOUT    -> a quoted excerpt, bounded and clearly
                #       fenced. A path handed to an agent with no filesystem
                #       is a dead end that looks like a working feature.
                #
                # Returning TEXT (not markup, not a command) keeps this a
                # composer convenience: it lands in the box, the operator reads it,
                # and nothing is sent until he presses send. The attach itself
                # authorizes nothing (P17).
                import content
                item = content.get((b.get("id") or "").strip())
                if item is None:
                    raise ValueError("that page is not in the index any more")
                pane = MGR.get(b.get("pane", "")) if b.get("pane") else None
                if pane and pane.agent.startswith("host:"):
                    raise ValueError("SSH panes cannot receive note attachments")
                has_tools = bool(pane and sessions.AGENTS.get(pane.agent, {})
                                 .get("tools"))
                if has_tools:
                    text = f"{item['path']}\n\n"
                    mode = "reference"
                else:
                    body = (item["body"] or "")[:ATTACH_EXCERPT_CHARS]
                    clipped = len(item["body"] or "") > ATTACH_EXCERPT_CHARS
                    text = (f"From my notes — {item['title']} "
                            f"({item['rel']}):\n\n\"\"\"\n{body}"
                            f"{chr(10) + '[…truncated]' if clipped else ''}"
                            f"\n\"\"\"\n\n")
                    mode = "excerpt"
                return self._json({"ok": True, "mode": mode, "text": text,
                                   "title": item["title"], "path": item["path"],
                                   "dir": str(Path(item["path"]).parent)})
            if p == "/api/session/send":
                if b.get("panes"):
                    # Same prompt, several panes: the first half of a panel.
                    r = MGR.fanout(list(b.get("panes") or []), b.get("text", ""))
                    return self._json({"ok": r["sent"] > 0, **r})
                # `turn` is the ledger id, durable before this ack
                # (P0-ledger): a client can ask later what became of it.
                # `via` is the CALLER's word for where this came from
                # ("consult", "cli"); the browser sends none. A label on the
                # supported path, not a control -- see TURN_VIAS.
                tid = MGR.get(b.get("pane", "")).send(b.get("text", ""),
                                                      via=b.get("via") or None)
                return self._json({"ok": True, "turn": tid})
            if p == "/api/session/quote":
                # Composer text only, like content attach: nothing is sent
                # until the operator presses send in the target pane.
                r = MGR.quote(b.get("from", ""), b.get("pane") or None)
                return self._json({"ok": True, "mode": "quote", **r})
            if p == "/api/session/crossfeed":
                r = MGR.crossfeed(list(b.get("panes") or []), b.get("text", ""))
                return self._json({"ok": r["sent"] > 0, **r})
            if p == "/api/session/permission":
                pane = MGR.get(b.get("pane", ""))
                ok = pane.answer(b.get("requestId", ""), b.get("optionId", ""),
                                 digest=(b.get("digest") or ""))
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
            if p == "/api/session/seat":
                # A HUMAN verb (DESIGN-5 S6), behind the pairing cookie like
                # every route below the auth check: naming a pane is how it
                # becomes addressable by other panes, so no agent-facing path
                # may reach this. "" unbinds.
                pane = MGR.bind_seat(b.get("pane", ""), b.get("seat"))
                return self._json({"ok": True, "seat": pane.seat})
            if p == "/api/session/rename":
                t = MGR.get(b.get("pane", "")).rename(b.get("title", ""))
                return self._json({"ok": True, "title": t})
            if p == "/api/session/minimize":
                m = MGR.get(b.get("pane", "")).set_minimized(
                    b.get("minimized", True))
                return self._json({"ok": True, "minimized": m})
            if p == "/api/session/port/preview":
                # The exact bytes a port would send, and their sha (port.py).
                pack = MGR.port_preview(b.get("pane", ""), b.get("agent", ""))
                return self._json({"ok": True, **pack})
            if p == "/api/session/port":
                r = MGR.port(b.get("pane", ""), b.get("agent", ""),
                             sha=b.get("sha") or "", cwd=b.get("cwd") or None)
                return self._json({"ok": r["delivered"], "delivered": r["delivered"],
                                   "error": r["error"], "pane": r["pane"].snapshot()})
            if p == "/api/session/schedule/add":
                # schedule.py: the SAME create+send a click takes, at a time.
                job = MGR.schedule.add(
                    b.get("agent", ""), b.get("cwd") or str(sessions.default_cwd()),
                    b.get("prompt", ""), b.get("when", ""),
                    repeat=b.get("repeat") or "", posture=b.get("posture") or None,
                    model=(b.get("model") or "").strip() or None,
                    effort=(b.get("effort") or "").strip() or None,
                    title=b.get("title") or "", action=b.get("action") or "start",
                    pane_id=b.get("pane") or None,
                    role=(b.get("role") or "").strip() or None)
                return self._json({"ok": True, "job": job})
            if p == "/api/session/schedule/remove":
                return self._json({"ok": True,
                                   "removed": MGR.schedule.remove(b.get("id", ""))})
            if p == "/api/session/seen":
                # A human surface SHOWED this pane up to `seq` (P0-e'). The
                # browser calls it only while the page is visible and focused.
                return self._json({"ok": True, "seen": mark_seen(
                    b.get("pane", ""), b.get("seq") or 0)})
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


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not print a traceback when a client
    simply goes away.

    WHY (mac-host, 2026-08-31): the log filled with

        Exception occurred during processing of request from …
        ConnectionResetError: [Errno 54] Connection reset by peer

    raised inside `handle_one_request` at `self.rfile.readline(...)` — i.e.
    BEFORE any of this module's code runs, which is why Handler's own
    BrokenPipe/ConnectionReset guards never caught it. socketserver's default
    `handle_error` prints the full traceback for it.

    Nothing is wrong when this happens. The peer closed the socket before
    sending a request line, which is the normal end of a browser's speculative
    preconnect, a reloaded tab's abandoned SSE stream, and every keep-alive
    socket a laptop takes with it when it sleeps. A cockpit that prints a
    stack trace for the routine case teaches its operator that stack traces
    are routine — and the next one, which is real, gets scrolled past.

    Narrow ON PURPOSE: exactly the exception types that mean "the other end
    left", and everything else still gets the loud default. Silencing errors
    generally would be the opposite of distrusting green (P1); this silences
    a non-error.
    """

    daemon_threads = True
    _QUIET = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
              TimeoutError)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], self._QUIET):
            return
        super().handle_error(request, client_address)


_SHUTTING_DOWN = {"sig": None}


def _on_shutdown_signal(signum, frame):              # noqa: ARG001
    """SIGTERM/SIGINT: say what is being cut off, then exit. Nothing else.

    Resilience review v2, P0-b' (Astra and Grok 2026-09-28). Runs on the MAIN
    thread (Python delivers signals there), where serve_forever is parked in
    select() holding no pane lock, so emit() cannot deadlock. It writes one
    `note` per pane with a turn in flight or messages queued, naming them —
    the transcript on disk is then truthful about the interruption — and
    does NOT pause(): pause clears the queue and the in-flight prompt was
    already popped, so "persist then pause" lost exactly the message that
    mattered. The adapters are left to the service manager (KillMode=mixed
    signals them only after this returns) and, where they outlive the hub,
    to the next boot's orphan reap. A second signal exits immediately.
    """
    name = signal.Signals(signum).name
    if _SHUTTING_DOWN["sig"] is not None:
        os._exit(128 + signum)
    _SHUTTING_DOWN["sig"] = name
    try:
        n = MGR.shutdown_notes(name)
    except Exception as e:                             # noqa: BLE001
        n = f"? ({e})"
    print(f"corral-light: {name} — wrote {n} interrupted-turn note(s); exiting",
          file=sys.stderr, flush=True)
    raise SystemExit(0)


def write_pidfile():
    """STATE/hub.pid: who is serving, for `corral-light watch` (P0-d').
    pid + start-time fingerprint, so a reused pid is not mistaken for us."""
    try:
        from corral_core.acp import process_start_token
        sessions.STATE.mkdir(parents=True, exist_ok=True)
        f = sessions.STATE / "hub.pid"
        tmp = f.with_name("hub.pid.tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(),
                                   "start": process_start_token(os.getpid())}),
                       encoding="utf-8")
        os.replace(tmp, f)
    except OSError as e:
        print(f"corral-light: could not write hub.pid: {e}", file=sys.stderr,
              flush=True)


def install_shutdown_handler():
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _on_shutdown_signal)


def serve(bind=BIND, port=PORT):
    # The hub runs as a systemd unit, and every pane it spawns inherits its
    # environment -- so until 2026-09-09 every interactive agent under it
    # carried systemd's INVOCATION_ID and passed the "scheduled estate" gate
    # in _lib/mail.py and ontology/_sender (measured live: grok, codex and
    # claude children of the hub all had it). An agent in a pane is the
    # interactive case those gates exist to refuse. Drop the unit identity
    # here, once, before the first spawn; the gates additionally require
    # CC_SCHEDULED_JOB, which only observability/log_run.py sets.
    for _k in ("INVOCATION_ID", "JOURNAL_STREAM", "CC_SCHEDULED_JOB"):
        os.environ.pop(_k, None)
    threading.Thread(target=_observe_loop, daemon=True).start()
    threading.Thread(target=_notify_loop, daemon=True).start()
    MGR.schedule.start()                    # schedule.py: scheduled prompts
    # Where a pane's seat-tools child dials this hub (DESIGN-5 S8). Loopback
    # when bound to every interface; the bound address otherwise, since a hub
    # bound to one LAN address does not answer on 127.0.0.1.
    sessions._core.PEER_HUB_URL = (
        f"http://{'127.0.0.1' if bind in ('0.0.0.0', '', '::') else bind}:{port}")
    httpd = Server((bind, port), Handler)
    httpd.daemon_threads = True
    install_shutdown_handler()
    write_pidfile()
    # flush=True, and it is not cosmetic. Python line-buffers stdout only when
    # it is a TTY; under systemd, launchd, or `> log 2>&1` it is block-buffered,
    # so this line — the ONE signal that the server bound its port — sat in a
    # 8 KB buffer and never appeared. Measured 2026-08-31 from a fresh clone:
    # the service was up and healthy with a zero-byte log, which reads exactly
    # like a service that failed to start.
    print(f"corral-light: http://{bind}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
