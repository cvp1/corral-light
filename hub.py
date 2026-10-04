#!/usr/bin/python3
"""hub — Corral Light's server: static PWA, SSE event stream, POST control plane.

Stdlib only. One multiplexed SSE stream per browser carries every pane's
events; input and permission answers are plain POSTs. No route dispatches
work anywhere other than the panes on this host.
"""
import json
import mimetypes
import os
import queue
import re
import signal
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Checked before imports: the static containment check needs Path.is_relative_to.
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
import worktrees
from corral_core import edge

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def _boot_commit():
    """The commit this process loaded, read once at import, so `corral-light
    update` can tell a hub still serving older code. None outside git."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


BOOT_COMMIT = _boot_commit()


def _safe_static_path(rel):
    """Resolve `rel` under STATIC; None if it escapes (path-wise, not by
    string prefix, so a sibling like `static-secret/` cannot match)."""
    base = STATIC.resolve()
    f = (base / rel).resolve()
    return f if f.is_relative_to(base) else None


# Loopback by default; set CORRAL_LIGHT_BIND=0.0.0.0 to expose on the network.
# In the container the default is 0.0.0.0 (the published port arrives on the
# bridge, not loopback); the compose file publishes on host loopback only.
CONTAINER = os.environ.get("CORRAL_CONTAINER") == "1"
BIND = os.environ.get("CORRAL_LIGHT_BIND", "0.0.0.0" if CONTAINER else "127.0.0.1")
PORT = int(os.environ.get("CORRAL_LIGHT_PORT", "8098"))
COOKIE = "corral_light"          # distinct from full Corral's cookie on the same host
SSE_PING = 20                    # keepalive for proxies and sleeping clients
STREAM_RECHECK = 30              # re-verify the cookie behind an open SSE stream
# Bind the pairing cookie to one tailnet identity behind Tailscale Serve
# (corral_core/edge.py); unset = LAN behaviour.
BOUND_LOGIN = (os.environ.get("CORRAL_TAILSCALE_LOGIN") or "").strip() or None
# DESIGN-6 S7: the hostname Tailscale Serve presents for this hub, if any. A
# key enrolled through Serve is scoped to it (WebAuthn binds a credential to
# its rpId). It comes from configuration, never from a request's Host.
SERVE_HOST = (os.environ.get("CORRAL_LIGHT_SERVE_HOST") or "").strip().lower() or None
if SERVE_HOST and not re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", SERVE_HOST):
    SERVE_HOST = None
KEY_ROUTES = ("/api/pair/key/begin", "/api/pair/key/finish")
ENROLL_ROUTES = ("/api/pair/key/enroll/begin", "/api/pair/key/enroll/finish",
                 "/api/pair/key/enroll/approve")
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
# How much of a note a chat-only lane gets quoted into its composer.
ATTACH_EXCERPT_CHARS = 6000

MGR = sessions.Manager()

# What code this process is running, for /health (docs/PERF-REVIEW-2026-10-04.md
# item 1): on 2026-10-04 the hub ran code 6.5 h older than its tree and nothing
# said so. The fingerprint hashes the served source at start; `code_stale`
# compares the files' current stat with the one taken at start.
STARTED_AT = time.time()


def _code_files():
    files = [ROOT / "hub.py", ROOT / "static" / "app.js"]
    files += sorted(ROOT.glob("*.py")) + sorted((ROOT / "corral_core").glob("*.py"))
    return sorted({f for f in files if not f.name.startswith("test_")})


def _code_stat():
    out = {}
    for f in _code_files():
        try:
            st = f.stat()
            out[f.name if f.parent == ROOT else f"{f.parent.name}/{f.name}"] = \
                (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
    return out


def _code_fingerprint():
    import hashlib
    h = hashlib.sha256()
    for f in _code_files():
        try:
            h.update(f.name.encode() + b"\0" + f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


CODE_STAT = _code_stat()
CODE_FINGERPRINT = _code_fingerprint()


def _login_signed_in():
    """On a confirmed sign-in, re-read the credential now and revive affected panes."""
    claude_auth.status(force=True)
    sessions.AVAIL.invalidate()        # the picker's Claude row, without waiting a period
    MGR.auth_sweep()


# The vendor's own login, started on a click; its watch thread never blocks the tick.
LOGIN = claude_login.Login(sessions.STATE, on_success=_login_signed_in)
LOGIN_FROM = ("rail", "banner", "picker", "composer")

# The observer tick: snapshot() every pane so dead or wedged adapters surface as
# state edges. Its own liveness is /health's tick_age_s, for an outside watcher.
TICK_S = 5
_TICK = {"at": 0.0, "errors": 0}


def _observe_loop():
    """Poll pane liveness. A failing pane is counted in `errors` and skipped;
    the tick advances per pane so one bad pane cannot freeze tick_age_s."""
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
    # The Claude login: warn before it lapses, revive panes after sign-in.
    try:
        MGR.auth_sweep()
    except Exception:                              # noqa: BLE001
        _TICK["errors"] += 1


# ── needs-you, off the glass ─────────────────────────────────────────────────
# Human surfaces report the highest seq they showed per pane; a `permission` or
# `dead` event still unseen after NOTIFY_GRACE_S becomes a desktop notification.
# An open SSE stream is not proof anyone is looking. Kept in memory only.
NOTIFY_GRACE_S = 20
NOTIFY_KINDS = ("permission", "dead")
SEEN = {}                   # pane id -> highest seq a human surface showed
_NOTIFY_PENDING = {}        # pane id -> newest unseen notifiable event
_NOTIFY_LOCK = threading.Lock()


def pending_payloads(pane):
    """What a human must see to answer each pending card, oldest first.

    Built from the `_gate` record answer() enforces; oversize payloads are withheld.
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
        pass                     # silent in steady state

    # ── plumbing ─────────────────────────────────────────────────────────
    def _carries_body(self):
        te = self.headers.get("Transfer-Encoding")
        cl = (self.headers.get("Content-Length") or "").strip()
        return bool(te) or cl not in ("", "0")

    def _send(self, code, body, ctype="application/json", extra=None):
        if self._carries_body() and not getattr(self, "_body_read", False):
            # An unread body would parse as the next request on a keep-alive
            # socket, bypassing the identity gate: close the connection.
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
        """The seat tools' routes: authenticated by pane token only (never the
        cookie), and only for a caller on this machine."""
        import ipaddress
        peer = self._peer()
        try:
            local = peer is not None and (ipaddress.ip_address(peer).is_loopback
                                          or peer == BIND)
        except ValueError:
            local = False
        if not local:
            return self._json({"error": "peer routes answer only on this machine"}, 403)
        # Same UNIX user, via /proc/net/tcp on Linux (the pane token can be on
        # a world-readable command line); unknown is refused.
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
        else:       # a GET's arguments are its query
            body = {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items()}
        status, obj = MGR.peer_http(method, p,
                                    self.headers.get(sessions._core.PEER_TOKEN_HEADER),
                                    body)
        return self._json(obj, status)

    def _peer(self):
        return self.client_address[0] if self.client_address else None

    # ── own-branch worktrees (worktrees.py) ─────────────────────────────────

    def _worktree_probe(self, cwd):
        if not cwd or not os.path.isdir(os.path.expanduser(cwd)):
            return self._json({"error": "not a directory"}, 400)
        pr = worktrees.probe(os.path.expanduser(cwd))
        pr.pop("common_dir", None)
        lanes = {a: sessions.worktree_refusal(a) for a in sessions.AGENTS}
        return self._json({"probe": pr, "laneRefusals": lanes})

    def _worktree_post(self, action, b):
        """Snapshot / commit / publish / discard. A refusal is 409 with a reason."""
        pane = b.get("pane", "")
        try:
            if action == "snapshot":
                r = MGR.worktree_snapshot(pane)
                r["remotes"] = MGR.worktree_remotes(pane)
            elif action == "commit":
                r = MGR.worktree_commit(pane, _oid(b.get("tree")), _oid(b.get("head")),
                                        str(b.get("index_id") or ""), str(b.get("message") or ""))
            elif action == "publish":
                pr = b.get("pr") if isinstance(b.get("pr"), dict) else None
                r = MGR.worktree_publish(pane, _oid(b.get("oid")), _oid(b.get("tree")),
                                         str(b.get("remote") or ""), str(b.get("push_url") or ""), pr)
            elif action == "discard":
                r = MGR.worktree_discard(pane, _oid(b.get("tree")))
            else:
                return self._json({"error": "not found"}, 404)
        except worktrees.Refused as e:
            return self._json({"error": str(e.detail)[:400], "reason": e.reason}, 409)
        except worktrees.GitError as e:
            return self._json({"error": str(e)[:400], "reason": "git"}, 409)
        return self._json(dict(r, ok=True))

    def _user(self):
        tok = self._token()
        user = auth.verify(tok) if tok else None
        return user if user and edge.audience_ok(user, self.headers, self._peer()) else None

    def _edge_refused(self):
        """Identity binding behind Tailscale Serve; never refuses when unbound."""
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
            self._body_read = True
            return json.loads(raw or b"{}")
        except ValueError:
            raise ValueError("malformed JSON body")

    def _local_human(self):
        """Is the clicker at this machine? Loopback socket and no proxy or
        Tailscale Serve header (Serve also arrives via loopback)."""
        if edge._peer_kind(self._peer()) != "loopback":
            return False
        return not any(self.headers.get(h) for h in
                       ("X-Forwarded-For", "Forwarded", "X-Real-IP",
                        edge.TS_LOGIN))

    def _same_origin(self):
        """CSRF defence: a browser's Origin on POST must match Host."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True                       # non-browser client (curl, tests)
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    def _key_origin(self):
        """(origin, rpId) a key ceremony is held to (DESIGN-6 S7). From
        configuration only: this hub's own localhost origin, or the Serve
        hostname when the request came through Serve. Never the Host or any
        forwarded header, which the client writes."""
        if edge.via_serve(self.headers, self._peer()):
            return (f"https://{SERVE_HOST}", SERVE_HOST) if SERVE_HOST else (None, None)
        return f"http://localhost:{PORT}", "localhost"

    def _key_route(self, p, b):
        """The key-pairing routes. Pair routes are unauthenticated (they are
        how a browser becomes authenticated); enroll routes arrive here only
        past the cookie and same-origin checks."""
        origin, rp_id = self._key_origin()
        if not origin:
            return self._json({"error": "key pairing through Serve needs "
                                        "CORRAL_LIGHT_SERVE_HOST configured"}, 403)
        try:
            if p == "/api/pair/key/begin":
                return self._json(auth.key_begin(b.get("code"), origin, rp_id))
            if p == "/api/pair/key/finish":
                auth.key_finish(b, origin, rp_id)
                serve = edge.via_serve(self.headers, self._peer())
                tok = auth.mint(user=edge.SERVE_USER if serve else edge.LAN_USER)
                return self._json({"status": "ok"}, 200, {
                    "Set-Cookie": edge.cookie_header(
                        COOKIE, tok, auth.SESSION_TTL, secure=serve)})
            cookie = self._token()
            if p == "/api/pair/key/enroll/begin":
                return self._json(auth.enroll_begin(cookie, b.get("code"), origin, rp_id))
            if p == "/api/pair/key/enroll/finish":
                return self._json(auth.enroll_finish(cookie, b, origin, rp_id))
            if p == "/api/pair/key/enroll/approve":
                return self._json(auth.enroll_approve(cookie, b, origin, rp_id))
        except auth.TooMany as e:
            return self._json({"error": str(e)}, 429)
        except auth.KeyRefused as e:
            return self._json({"error": e.reason}, e.status)
        return self._json({"error": "not found"}, 404)

    # ── GET ──────────────────────────────────────────────────────────────
    def do_GET(self):
        p = urlparse(self.path).path
        self._body_read = False
        q = parse_qs(urlparse(self.path).query)

        if p == "/health":
            # Unauthenticated for outside watchdogs: liveness and counts only.
            age = int(time.time() - _TICK["at"]) if _TICK["at"] else -1
            panes = list(MGR.panes.values())
            live = sum(1 for x in panes if x.state not in ("dead", "detached"))
            blocked = sum(len(x.pending) for x in panes)
            # orphans_reaped: adapters a previous hub left running, stopped at boot.
            reaped = sum(1 for v in getattr(MGR, "orphans", {}).values()
                         if v in ("reaped", "killed"))
            extra = {}
            emit_perf = sessions._core.emit_perf_snapshot()
            if emit_perf is not None:               # CORRAL_PERF=1 only
                extra["emit_perf"] = emit_perf
            return self._json({**extra, "ok": 1, "service": "corral-light",
                               "tick_age_s": age, "panes_live": live,
                               "permissions_waiting": blocked,
                               "orphans_reaped": reaped,
                               "tick_errors": _TICK.get("errors", 0),
                               "not_restored": getattr(MGR, "not_restored", 0),
                               "started_at": int(STARTED_AT),
                               "code": CODE_FINGERPRINT,
                               "code_stale": _code_stat() != CODE_STAT})

        if self._edge_refused():
            return

        # Token-only routes, before any cookie is looked at.
        if p.startswith("/api/peer/"):
            return self._peer_route(p, "GET")

        if p == "/api/pair/new":
            try:
                code, ttl = auth.new_code()
            except auth.TooMany as e:
                return self._json({"error": str(e)}, 429)
            origin, rp_id = self._key_origin()
            # keyOrigin (S8): the one origin a key ceremony here is held to,
            # from configuration. The page compares it with its own address,
            # so 127.0.0.1 is offered a link to localhost rather than a button
            # whose ceremony the hub would refuse.
            return self._json({"code": code, "ttl": ttl,
                               "how": f"corral-light pair {code}",
                               "host": auth.host_id(),
                               "keyAvailable": bool(origin) and
                               auth.key_available(origin, rp_id),
                               "keyOrigin": origin})
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
            # Served from the root so its scope covers the whole app.
            return self._static("sw.js")
        if p == "/manifest.json":
            return self._static("manifest.json")
        if p.startswith("/static/"):
            return self._static(p[len("/static/"):])

        user = self._user()
        if not user:
            return self._json({"error": "not paired"}, 401)

        if p == "/api/session/worktree/probe":
            return self._worktree_probe((q.get("cwd") or [""])[0])
        if p == "/api/session/worktrees":
            return self._json({"worktrees": MGR.worktree_list()})

        if p == "/api/pair/key/list":
            # What Security keys shows. No spki, no counters. `origin` is
            # where an enrollment from this request would be held (S8).
            keys, err = auth.load_keys()
            pol, perr = auth.policy()
            state, why = auth.verifier_state()
            origin, _ = self._key_origin()
            return self._json({
                "origin": origin,
                "policy": pol, "policyError": perr, "keysError": err,
                "verifier": state if state != "ok" else "ok",
                "verifierWhy": None if state == "ok" else why,
                "keys": [{"id": k["id"], "label": k.get("label"),
                          "origin": k["origin"], "enrolledAt": k.get("enrolledAt"),
                          "lastUsed": k.get("lastUsed")} for k in keys]})

        # Rigs: shared core route, past the pairing check.
        if p == "/api/session/rigs":
            from corral_core import rigs
            st, out = rigs.route(MGR, "GET", p)
            return self._json(out, st)

        if p == "/api/search":
            # Content search for the palette; a broken index degrades in-payload.
            import content
            return self._json(content.search((q.get("q") or [""])[0]))
        if p == "/api/content/status":
            # Index status, for the palette's empty state.
            import content
            return self._json(content.status())
        if p == "/api/session/search":
            # Transcript search across live and archived panes.
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
            # Every role with the lanes it can start on; errors degrade in-payload.
            try:
                import roles
                out = [dict(r, resolvableOn=roles.resolvable_on(r["id"]))
                       if not r.get("error") else r for r in roles.list_roles()]
                return self._json({"roles": out, "dir": str(roles.roles_dir())})
            except Exception as e:                  # noqa: BLE001
                return self._json({"roles": [], "error": str(e)[-200:]})
        if p == "/api/session/pending":
            # Authoritative pending permission payloads (from pane.pending, not
            # the bounded event ring), with the digest an approval must carry.
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
        if p == "/api/lanes":
            # The lane list alone, for clients that only need to know what can
            # start (consult open_pane). Served from the availability worker.
            agents, _auth, checked_at = sessions.AVAIL.read()
            return self._json({"agents": agents, "checkedAt": int(checked_at)})
        if p == "/api/state":
            # ?since={"paneId":seq,...} -> only events after what the client
            # already has.
            # No `since`, or `full=1`, is the whole document; a cursor without
            # `full` is a poller's light delta (Manager.state). A cursor that
            # does not parse is treated as no cursor: the safe, full answer.
            since = None
            raw = (q.get("since") or [None])[0]
            if raw:
                try:
                    since = {str(k): int(v) for k, v in json.loads(raw).items()}
                except (ValueError, AttributeError, TypeError):
                    since = None
            full = (q.get("full") or ["0"])[0] in ("1", "true")
            out = MGR.state(since, full=full)
            out["claudeLogin"] = LOGIN.snapshot()
            # Which checkout and commit this process serves (`corral-light update`).
            out["hub"] = {"root": str(ROOT), "commit": BOOT_COMMIT}
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
            # Subscribe inside the try so a reset cannot leak the queue.
            MGR.subscribe(q)
            self._stream_body(q)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            MGR.unsubscribe(q)
            # A finished stream ends its keep-alive connection.
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
            # Re-verify the cookie periodically so a stream cannot outlive its TTL.
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
        # Token-only routes, before any cookie is looked at.
        if p.startswith("/api/peer/"):
            return self._peer_route(p, "POST")
        if p in KEY_ROUTES:
            # Unauthenticated: this is how a browser becomes paired.
            try:
                kb = self._body()
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._key_route(p, kb if isinstance(kb, dict) else {})
        user = self._user()
        if not user:
            return self._json({"error": "not paired"}, 401)
        if not self._same_origin():
            return self._json({"error": "cross-origin request refused"}, 403)
        try:
            b = self._body()
            if p in ENROLL_ROUTES:
                return self._key_route(p, b if isinstance(b, dict) else {})
            if p in ("/api/session/rigs/save", "/api/session/rigs/up",
                     "/api/session/rigs/rm"):
                from corral_core import rigs
                r = rigs.route(MGR, "POST", p, b, by=user)
                if r is not None:
                    return self._json(r[1], r[0])
            if p == "/api/claude/login":
                # Only allow-listed values describe the requester; no request text.
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
                    # Resolve before creating, so a refusal costs no process.
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
                try:
                    pane = MGR.create(agent, b.get("cwd") or str(sessions.default_cwd()),
                                      posture, (b.get("model") or "").strip() or None,
                                      effort, role=role, role_sha=role_sha,
                                      worktree=b.get("worktree") is True,
                                      title=(b.get("title") or "").strip()[:80] or None,
                                      # Bulk spawners: minimized until it needs you.
                                      background=b.get("background") is True)
                except Exception:
                    # The cached lane list may have said "available" for a lane
                    # that just failed: recheck now rather than in a period.
                    sessions.AVAIL.invalidate()
                    raise
                # The preamble is returned to the composer, not sent from here.
                return self._json({"ok": True, "pane": pane.snapshot(),
                                   "preamble": preamble, "notes": notes})
            if p == "/api/content/attach":
                # Composer text only, nothing is sent. A lane with tools gets
                # the path (it reads the file through its own permission gate);
                # a lane without gets a bounded, fenced excerpt.
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
                    # Same prompt, several panes.
                    r = MGR.fanout(list(b.get("panes") or []), b.get("text", ""))
                    return self._json({"ok": r["sent"] > 0, **r})
                # `turn` is the durable ledger id; `via` is the caller's
                # origin label ("consult", "cli"), not a control.
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
                # Stop the process, keep the conversation.
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
                # Human-only (cookie-gated): a seat makes a pane addressable
                # by other panes. "" unbinds.
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
                # The exact bytes a port would send, and their sha.
                pack = MGR.port_preview(b.get("pane", ""), b.get("agent", ""))
                return self._json({"ok": True, **pack})
            if p == "/api/session/port":
                r = MGR.port(b.get("pane", ""), b.get("agent", ""),
                             sha=b.get("sha") or "", cwd=b.get("cwd") or None)
                return self._json({"ok": r["delivered"], "delivered": r["delivered"],
                                   "error": r["error"], "pane": r["pane"].snapshot()})
            if p == "/api/session/schedule/add":
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
                # A human surface showed this pane up to `seq` (sent only while
                # the page is visible and focused). `seen: {pane: seq, ...}`
                # marks several in one request (the browser's batch, one POST
                # per tick instead of one per pane); a pane no longer here is
                # skipped, not an error for the rest. Bounded by MAX_PANES.
                many = b.get("seen")
                if isinstance(many, dict):
                    out = {}
                    for pid, s in list(many.items())[:sessions.MAX_PANES]:
                        try:
                            out[pid] = mark_seen(str(pid), s or 0)
                        except (ValueError, TypeError):
                            continue
                    return self._json({"ok": True, "seen": out})
                return self._json({"ok": True, "seen": mark_seen(
                    b.get("pane", ""), b.get("seq") or 0)})
            if p in WORKTREE_POSTS:
                return self._worktree_post(p.rsplit("/", 1)[1], b)
            if p == "/api/session/cancel":
                return self._json({"ok": MGR.get(b.get("pane", "")).cancel()})
            if p == "/api/session/close":
                MGR.close(b.get("pane", ""))
                return self._json({"ok": True})
            if p == "/api/session/forget":
                try:
                    forgot = MGR.forget(b.get("pane", ""), keep_branch=b.get("keep_branch") is True)
                except worktrees.Refused as e:
                    return self._json({"error": str(e.detail)[:400], "reason": e.reason}, 409)
                return self._json({"ok": True, "forgot": forgot})
            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:                      # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"[:300]}, 500)


# Spelled out, so the front end's route check can see each one.
WORKTREE_POSTS = ("/api/session/worktree/snapshot", "/api/session/worktree/commit",
                  "/api/session/worktree/publish", "/api/session/worktree/discard")

_OID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _oid(v):
    """A full object id from the browser, or ValueError (400)."""
    if not isinstance(v, str) or not _OID_RE.fullmatch(v):
        raise ValueError("expected a full git object id")
    return v


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that stays quiet when a client simply disconnects
    (raised before Handler code runs); all other errors keep the default."""

    daemon_threads = True
    _QUIET = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
              TimeoutError)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], self._QUIET):
            return
        super().handle_error(request, client_address)


_SHUTTING_DOWN = {"sig": None}


def _on_shutdown_signal(signum, frame):              # noqa: ARG001
    """SIGTERM/SIGINT: note interrupted turns in each pane's transcript, then
    exit. Does not pause() (that would drop queued messages); adapters are
    left to the service manager. A second signal exits immediately."""
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
    """Write STATE/hub.pid (pid + start-time fingerprint) for `corral-light watch`."""
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
    # Drop the service-unit identity so interactive panes never look like
    # scheduled jobs to environment-based gates.
    for _k in ("INVOCATION_ID", "JOURNAL_STREAM", "CC_SCHEDULED_JOB"):
        os.environ.pop(_k, None)
    threading.Thread(target=_observe_loop, daemon=True).start()
    threading.Thread(target=_notify_loop, daemon=True).start()
    sessions.AVAIL.start()             # lane availability, off the request path
    MGR.schedule.start()
    # Where a pane's seat-tools child dials this hub: loopback when bound to
    # all interfaces, else the bound address.
    sessions._core.PEER_HUB_URL = (
        f"http://{'127.0.0.1' if bind in ('0.0.0.0', '', '::') else bind}:{port}")
    httpd = Server((bind, port), Handler)
    httpd.daemon_threads = True
    install_shutdown_handler()
    write_pidfile()
    # flush: stdout is block-buffered under a service manager.
    print(f"corral-light: http://{bind}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
