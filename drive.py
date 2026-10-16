"""Drive: work panes on a paired hub from this wall (docs/remote-panes-plan.md).

`drive` is an SSH login by another road. The operator is the same user on
both machines; a hub granted `drive` opens agents on this one, prompts
them, and answers their cards, exactly as the operator would at this
machine's own wall. What this module adds is the transport, held to the
bar the operator set: as robust, efficient and fast as possible.

One persistent duplex channel per pair of hubs:

- The driving hub dials the peer's hub-link listener with mutual TLS and
  the pinned certificate, and sends ONE signed request to
  /fed/v1/drive/channel. It passes the usual checks (pinned certificate,
  grant, clock, nonce, recipient). The peer answers 101 and the same TLS
  connection carries length-prefixed JSON frames both ways from then on.
  Each frame is authenticated by that TLS session, as each SSH packet is by
  its transport: no per-frame signature, nonce or disk write.
- Down: every event of every pane this peer drives, sent the moment it
  lands, batched only with what is already queued behind it, plus pane
  snapshots on open, resume and resync.
- Up: commands, each with a mutation id. The peer runs a command once; a
  retry of the same id (after a reconnect, say) gets the first result.
- Heartbeats every HEARTBEAT_S both ways; DEAD_S of silence ends it. The
  driving side redials at once and then with jittered backoff up to
  BACKOFF_MAX_S, and resumes with a cursor per pane. A drop costs the peer
  nothing: its agents keep working, and the wall catches up.

Frame: 4-byte big-endian length, then UTF-8 JSON, at most FRAME_MAX.
"""
import json
import os
import queue
import random
import secrets
import socket
import ssl
import struct
import sys
import threading
import time
import uuid

FRAME_MAX = 8 << 20
HEARTBEAT_S = 10
DEAD_S = 30
BATCH_MAX = 256                # events per frame; a batch is what is already queued
BACKOFF_FIRST_S = 0.25
BACKOFF_MAX_S = 5.0
MAX_CHANNELS = 8               # channels this hub serves at once (one per peer)
MAX_INFLIGHT = 16              # commands one channel runs at once
MUTATIONS_KEEP = 500           # remembered command results per peer
CALL_TIMEOUT_S = 60
LANES_EVERY_S = 30
QUEUE_MAX = 5000
PROTOCOL = "corral-drive/1"
PATH = "/fed/v1/drive/channel"
# Commands. Every one but `open` names a pane the caller opened.
OPS = ("open", "send", "answer", "answer_question", "cancel", "close", "resume",
       "history")


class ProtocolError(Exception):
    pass


class DriveError(Exception):
    """A command the peer refused or could not run; `why` is for the operator."""

    def __init__(self, why, status=400):
        super().__init__(why)
        self.why, self.status = str(why), status


# ── frames ──────────────────────────────────────────────────────────────────
def pack(obj):
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(data) > FRAME_MAX:
        raise ProtocolError(f"frame of {len(data)} bytes is over the {FRAME_MAX} cap")
    return struct.pack(">I", len(data)) + data


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("the other hub closed the channel")
        buf += chunk
    return bytes(buf)


def recv_frame(sock):
    (n,) = struct.unpack(">I", _recv_exact(sock, 4))
    if n > FRAME_MAX:
        raise ProtocolError(f"frame of {n} bytes is over the {FRAME_MAX} cap")
    try:
        obj = json.loads(_recv_exact(sock, n))
    except ValueError:
        raise ProtocolError("a frame that is not JSON")
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ProtocolError("a frame without a type")
    return obj


def _tune(sock):
    for level, opt in ((socket.IPPROTO_TCP, socket.TCP_NODELAY),
                       (socket.SOL_SOCKET, socket.SO_KEEPALIVE)):
        try:
            sock.setsockopt(level, opt, 1)
        except (OSError, AttributeError):
            pass


class _Wire:
    """One TLS socket, framed: a write lock, a read loop, liveness."""

    def __init__(self, sock):
        self.sock = sock
        self.wlock = threading.Lock()
        self.last_in = time.monotonic()
        self.closed = threading.Event()

    def send(self, obj):
        data = pack(obj)
        with self.wlock:
            if self.closed.is_set():
                raise ConnectionError("channel closed")
            self.sock.sendall(data)

    def recv(self):
        obj = recv_frame(self.sock)
        self.last_in = time.monotonic()
        return obj

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        for f in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                f()
            except OSError:
                pass


# ── the peer side: serve one channel ────────────────────────────────────────
class Served:
    """A channel this hub serves to one peer. Runs on the listener's request
    thread (the reader) plus a pump thread (events down) and a heartbeat."""

    def __init__(self, svc, adapter, wire, pid, peer, cursors):
        self.svc, self.adapter, self.wire = svc, adapter, wire
        self.pid, self.peer = pid, peer
        self.owner = {"hub": pid, "gen": peer.get("gen"), "name": peer.get("name")}
        self.cursors = {str(k): int(v) for k, v in (cursors or {}).items()
                        if isinstance(v, int) or str(v).isdigit()}
        self.q = queue.Queue(maxsize=QUEUE_MAX)
        self.owned = set()
        self.olock = threading.Lock()
        self.sent = {}                      # pane -> last seq sent down
        self.inflight = threading.Semaphore(MAX_INFLIGHT)
        self.lanes_sig = None

    # The grant and the pairing generation are re-read for every command and
    # every batch: a revoke or a re-pair ends driving at once.
    def still_allowed(self):
        cur = self.svc.peers().get(self.pid)
        return bool(cur and "drive" in cur.get("grants", [])
                    and cur.get("gen") == self.owner["gen"])

    def run(self):
        self.adapter.subscribe(self.q)
        try:
            with self.olock:
                self.owned = set(self.adapter.owned(self.owner))
            self.wire.send({"t": "ready", "proto": PROTOCOL,
                            "hub": self.svc.identity()[1],
                            "lanes": self._lanes(force=True),
                            "home": self.adapter.home(),
                            "panes": [self._snap(p) for p in sorted(self.owned)],
                            "heartbeat": HEARTBEAT_S})
            for target in (self._pump, self._beat):
                threading.Thread(target=target, daemon=True,
                                 name=f"drive-{target.__name__}-{self.pid[:6]}").start()
            self._read()
        finally:
            self.adapter.unsubscribe(self.q)
            self.wire.close()

    def _snap(self, pane, since=None):
        since = self.cursors.get(pane, 0) if since is None else since
        s = self.adapter.snapshot(pane, since)
        if s is None:
            return {"id": pane, "gone": True}
        evs = s.get("events") or []
        first = evs[0]["seq"] if evs else None
        # The ring holds only the tail: a cursor older than it is a gap the
        # mirror fills from history, never silently.
        s["gap"] = bool(since and first is not None and first > since + 1)
        if evs:
            self.sent[pane] = evs[-1]["seq"]
        elif s.get("seq"):
            self.sent[pane] = max(self.sent.get(pane, 0), int(s["seq"]))
        return s

    def _lanes(self, force=False):
        lanes = self.adapter.lanes()
        sig = json.dumps(lanes, sort_keys=True)
        if not force and sig == self.lanes_sig:
            return None
        self.lanes_sig = sig
        return lanes

    def _pump(self):
        next_lanes = time.monotonic() + LANES_EVERY_S
        while not self.wire.closed.is_set():
            try:
                ev = self.q.get(timeout=1.0)
            except queue.Empty:
                ev = None
            batch, resync = [], False
            if ev is not None:
                while True:
                    if ev.get("kind") == "resync" and ev.get("pane") is None:
                        resync = True
                    else:
                        pane = ev.get("pane")
                        with self.olock:
                            mine = pane in self.owned
                        if mine and ev.get("seq", 0) > self.sent.get(pane, 0):
                            batch.append(ev)
                            self.sent[pane] = ev["seq"]
                    if len(batch) >= BATCH_MAX:
                        break
                    # Batch only what is already waiting; never wait for more.
                    # A short timed wait is stretched to ~100 ms by macOS
                    # timer coalescing (measured), and the panes already
                    # coalesce streamed text before it reaches the bus.
                    try:
                        ev = self.q.get_nowait()
                    except queue.Empty:
                        break
            try:
                if not self.still_allowed():
                    self.wire.send({"t": "bye", "why": "this hub no longer lets yours drive it"})
                    self.wire.close()
                    return
                if resync:
                    # This channel fell behind the event bus: send every owned
                    # pane again from what was last sent. Never a silent gap.
                    with self.olock:
                        owned = sorted(self.owned)
                    self.wire.send({"t": "panes", "panes": [
                        self._snap(p, self.sent.get(p, 0)) for p in owned]})
                elif batch:
                    self.wire.send({"t": "ev", "evs": batch})
                if time.monotonic() >= next_lanes:
                    next_lanes = time.monotonic() + LANES_EVERY_S
                    lanes = self._lanes()
                    if lanes is not None:
                        self.wire.send({"t": "lanes", "lanes": lanes})
            except (OSError, ConnectionError, ProtocolError):
                self.wire.close()
                return

    def _beat(self):
        while not self.wire.closed.wait(HEARTBEAT_S):
            if time.monotonic() - self.wire.last_in > DEAD_S:
                self.wire.close()
                return
            try:
                self.wire.send({"t": "ping"})
            except (OSError, ConnectionError):
                self.wire.close()
                return

    def _read(self):
        self.wire.sock.settimeout(DEAD_S)
        while not self.wire.closed.is_set():
            try:
                f = self.wire.recv()
            except (OSError, ConnectionError, ProtocolError, socket.timeout):
                return
            t = f.get("t")
            if t == "ping":
                self._safe_send({"t": "pong"})
            elif t == "cmd":
                if not self.inflight.acquire(blocking=False):
                    self._safe_send({"t": "res", "id": f.get("id"), "ok": False,
                                     "error": "too many commands at once", "status": 429})
                    continue
                threading.Thread(target=self._cmd, args=(f,), daemon=True,
                                 name=f"drive-cmd-{self.pid[:6]}").start()
            elif t == "bye":
                return

    def _safe_send(self, obj):
        try:
            self.wire.send(obj)
        except (OSError, ConnectionError, ProtocolError):
            self.wire.close()

    def _cmd(self, f):
        try:
            mid, op = str(f.get("id") or ""), f.get("op")
            args = f.get("args") if isinstance(f.get("args"), dict) else {}
            if not (8 <= len(mid) <= 64) or not mid.replace("-", "").isalnum():
                return self._safe_send({"t": "res", "id": mid, "ok": False,
                                        "error": "bad command id", "status": 400})
            res = self.svc.drive_once(self.pid, mid, lambda: self._run(op, args))
            self._safe_send({"t": "res", "id": mid, **res})
            if res.get("ok") and op == "open":
                pane = (res.get("result") or {}).get("pane")
                if pane:
                    with self.olock:
                        self.owned.add(pane)
                    self._safe_send({"t": "panes", "panes": [self._snap(pane, 0)]})
            elif res.get("ok") and op == "close":
                pane = args.get("pane")
                with self.olock:
                    self.owned.discard(pane)
        finally:
            self.inflight.release()

    def _run(self, op, args):
        if not self.still_allowed():
            raise DriveError("this hub no longer lets yours drive it", 403)
        if op not in OPS:
            raise DriveError(f"unknown command {op!r}")
        if op != "open":
            pane = str(args.get("pane") or "")
            with self.olock:
                mine = pane in self.owned
            if not mine:
                # Opened before a restart of this hub: ownership is on disk.
                mine = pane in set(self.adapter.owned(self.owner))
                if mine:
                    with self.olock:
                        self.owned.add(pane)
            if not mine:
                raise DriveError("that pane was not opened by your hub", 403)
        result = self.adapter.op(op, args, self.owner)
        self.svc.ledger("drive", peer=self.pid, name=self.owner["name"], op=op,
                        pane=(result or {}).get("pane") or args.get("pane"),
                        digest=args.get("digest") if op == "answer" else None)
        return result


# ── the driving side: one link per peer ─────────────────────────────────────
class Link:
    """The driving hub's end of one channel. Keeps it up; delivers frames to
    `on_frame(link, frame)`; runs commands with `call()`."""

    def __init__(self, svc, pid, on_frame, cursors=lambda: {}):
        self.svc, self.pid = svc, pid
        self.on_frame, self.cursors = on_frame, cursors
        self.wire = None
        self.up = threading.Event()
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.waiting = {}                   # mid -> [Event, frame, cmd]
        self.error = None
        self.connected_at = None
        self.ready = None                   # the last ready frame
        self.kick = threading.Event()       # redial now
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"drive-link-{pid[:6]}")
        self._thread.start()

    def status(self):
        return {"up": self.up.is_set(), "error": self.error,
                "since": self.connected_at,
                "hub": (self.ready or {}).get("hub")}

    def stop(self):
        self.stopped.set()
        self.kick.set()
        w = self.wire
        if w:
            try:
                w.send({"t": "bye"})
            except (OSError, ConnectionError, ProtocolError):
                pass
            w.close()

    def redial(self):
        """Drop the current connection and dial again now (wake, network change)."""
        w = self.wire
        if w:
            w.close()
        self.kick.set()

    def call(self, op, args, timeout=CALL_TIMEOUT_S, mid=None):
        """Run `op` on the peer and return its result. The command keeps its
        id across reconnects, so it runs there at most once."""
        mid = mid or uuid.uuid4().hex
        ev = threading.Event()
        cmd = {"t": "cmd", "id": mid, "op": op, "args": args}
        slot = [ev, None, cmd]
        with self.lock:
            self.waiting[mid] = slot
        try:
            self._try_send(cmd)
            if not ev.wait(timeout):
                raise DriveError(self.error or "the other hub did not answer in time", 504)
            res = slot[1]
            if not res.get("ok"):
                raise DriveError(res.get("error") or "refused", int(res.get("status") or 400))
            return res.get("result")
        finally:
            with self.lock:
                self.waiting.pop(mid, None)

    def _try_send(self, cmd):
        w = self.wire
        if w and self.up.is_set():
            try:
                w.send(cmd)
            except (OSError, ConnectionError, ProtocolError):
                w.close()            # resent by _loop once the channel is back

    # The link's life: dial, serve, back off, dial again.
    def _loop(self):
        attempt = 0
        while not self.stopped.is_set():
            ok = False
            try:
                ok = self._session()
            except Exception as e:                       # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"[:300]
            finally:
                self.up.clear()
                self.wire = None
                try:
                    self.on_frame(self, {"t": "down", "why": self.error})
                except Exception:                        # noqa: BLE001
                    pass
            if self.stopped.is_set():
                return
            attempt = 0 if ok else attempt + 1
            if attempt == 0:
                delay = 0.0
            else:
                delay = min(BACKOFF_MAX_S, BACKOFF_FIRST_S * (2 ** (attempt - 1)))
                delay *= 0.5 + random.random() / 2
            if self.error and "no longer" in self.error:
                delay = max(delay, 30.0)               # refused, not dropped
            self.kick.wait(delay)
            self.kick.clear()

    def _session(self):
        """One connection. True when it got as far as `ready` (the next dial is
        immediate); False when it never did (back off)."""
        import hublink
        svc = self.svc
        peers = svc.peers()
        peer = peers.get(self.pid)
        if not peer:
            self.stopped.set()
            self.error = "unpaired"
            return False
        me, _n, _f = svc.identity()
        body = json.dumps({"cursors": self.cursors() or {}}).encode()
        raw = socket.create_connection((peer["addr"], int(peer["port"])),
                                       timeout=hublink.CALL_TIMEOUT)
        _tune(raw)
        try:
            sock = svc._client_ctx(present=True).wrap_socket(raw)
        except (ssl.SSLError, OSError) as e:
            raw.close()
            raise ConnectionError(f"TLS to the other hub failed ({e})")
        wire = _Wire(sock)
        try:
            der = sock.getpeercert(binary_form=True)
            if not der or not hublink.hmac.compare_digest(
                    hublink.fingerprint_der(der), peer["fp"] or ""):
                raise ConnectionError("the other hub presented a different certificate "
                                      "than the one paired — refused before sending anything")
            ts, nonce = str(int(time.time())), secrets.token_hex(16)
            sig = hublink._mac(hublink._b64d(peer["key"]), "POST", PATH, ts, nonce,
                               body, me, self.pid)
            head = (f"POST {PATH} HTTP/1.1\r\nHost: corral-hub\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                    f"Upgrade: {PROTOCOL}\r\nConnection: Upgrade\r\n"
                    f"X-Hub-From: {me}\r\nX-Hub-To: {self.pid}\r\nX-Hub-Ts: {ts}\r\n"
                    f"X-Hub-Nonce: {nonce}\r\nX-Hub-Sig: {sig}\r\n\r\n").encode()
            sock.sendall(head + body)
            status, headers, rest = _read_head(sock)
            if status != 101:
                n = int(headers.get("content-length") or 0)
                data = rest + (_recv_exact(sock, max(0, n - len(rest))) if n > len(rest) else b"")
                try:
                    why = json.loads(data or b"{}").get("error")
                except ValueError:
                    why = None
                raise ConnectionError(why or f"the other hub answered {status}")
            if rest:
                raise ProtocolError("bytes after the upgrade before any frame")
            sock.settimeout(HEARTBEAT_S)
            ready = wire.recv()
            if ready.get("t") != "ready":
                raise ProtocolError("the channel did not start with ready")
            self.ready, self.wire, self.error = ready, wire, None
            self.connected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.up.set()
            self.on_frame(self, ready)
            # Anything still waiting (sent before the drop, or while it was
            # down) goes again under the same id: the peer runs it once.
            with self.lock:
                again = [s[2] for s in self.waiting.values() if s[1] is None]
            for cmd in again:
                wire.send(cmd)
            self._serve(wire)
            return True
        finally:
            wire.close()

    def _serve(self, wire):
        last_beat = time.monotonic()
        wall = time.time()
        while not wire.closed.is_set() and not self.stopped.is_set():
            try:
                f = wire.recv()
            except socket.timeout:
                f = None
            except (OSError, ConnectionError, ProtocolError) as e:
                self.error = f"channel dropped ({e})"[:300]
                return
            now, nwall = time.monotonic(), time.time()
            # A wall-clock jump far past the monotonic one is a sleep and
            # wake: the socket may look fine and be dead. Dial again.
            if (nwall - wall) - (now - last_beat) > HEARTBEAT_S:
                self.error = "this machine slept; dialling again"
                return
            if now - wire.last_in > DEAD_S:
                self.error = "the other hub went silent"
                return
            if now - last_beat >= HEARTBEAT_S:
                last_beat, wall = now, nwall
                try:
                    wire.send({"t": "ping"})
                except (OSError, ConnectionError, ProtocolError):
                    return
            if f is None:
                continue
            t = f.get("t")
            if t == "ping":
                try:
                    wire.send({"t": "pong"})
                except (OSError, ConnectionError, ProtocolError):
                    return
            elif t == "res":
                with self.lock:
                    slot = self.waiting.get(str(f.get("id")))
                if slot:
                    slot[1] = f
                    slot[0].set()
            elif t == "bye":
                self.error = f.get("why") or "the other hub closed the channel"
                return
            elif t != "pong":
                try:
                    self.on_frame(self, f)
                except Exception as e:                   # noqa: BLE001
                    print(f"corral-light: drive: frame handler: {type(e).__name__}: {e}",
                          file=sys.stderr, flush=True)


def _read_head(sock, cap=16384):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("the other hub closed before answering")
        buf += chunk
        if len(buf) > cap:
            raise ProtocolError("an answer header over the cap")
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise ProtocolError("not an HTTP answer")
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        headers[k.strip().lower()] = v.strip()
    return status, headers, rest


# ── command results, kept so a retry gets the first answer ─────────────────
class Mutations:
    """{peer: {mid: result}} on disk, plus the commands running now."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.running = {}                   # (peer, mid) -> Event
        self.doc = None

    def _load(self):
        if self.doc is None:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.doc = json.load(fh)
            except (OSError, ValueError):
                self.doc = {}
        return self.doc

    def _save(self):
        tmp = f"{self.path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.doc, fh)
        os.replace(tmp, self.path)

    def once(self, peer, mid, fn):
        key = (peer, mid)
        while True:
            with self.lock:
                done = self._load().get(peer, {}).get(mid)
                if done is not None:
                    return done
                ev = self.running.get(key)
                if ev is None:
                    ev = self.running[key] = threading.Event()
                    break
            ev.wait(CALL_TIMEOUT_S)
        try:
            try:
                res = {"ok": True, "result": fn()}
            except DriveError as e:
                res = {"ok": False, "error": e.why, "status": e.status}
            except (ValueError, KeyError, RuntimeError) as e:
                res = {"ok": False, "error": str(e)[:300], "status": 400}
            except Exception as e:                       # noqa: BLE001
                print(f"corral-light: drive: {type(e).__name__}: {e}", file=sys.stderr,
                      flush=True)
                res = {"ok": False, "error": "internal error on the other hub",
                       "status": 500}
            with self.lock:
                d = self._load().setdefault(peer, {})
                d[mid] = {**res, "at": int(time.time())}
                if len(d) > MUTATIONS_KEEP:
                    for k in sorted(d, key=lambda k: d[k].get("at", 0))[:len(d) - MUTATIONS_KEEP]:
                        del d[k]
                try:
                    self._save()
                except OSError:
                    pass
            return res
        finally:
            with self.lock:
                self.running.pop(key, None)
            ev.set()

    def forget_peer(self, peer):
        with self.lock:
            if self._load().pop(peer, None) is not None:
                try:
                    self._save()
                except OSError:
                    pass
