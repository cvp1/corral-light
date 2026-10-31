#!/usr/bin/python3
"""keeper — holds one agent process so a hub restart does not end it.

The hub used to fork each ACP adapter itself, over pipes; a hub restart
ended the agent mid-turn. The keeper is a small process in its own session
that owns the adapter's stdio and serves it to one hub at a time over a Unix
socket. The delivery contract is docs/DURABLE-RUNS.md "The keeper's delivery
contract"; this docstring is its summary.

OUTBOX. Every line the adapter writes gets a sequence number and goes into
one ordered, in-memory outbox (the adapter's exit is the last numbered line).
A hub acknowledges CUMULATIVELY -- `{"keeper": "ack", "s": N}` means "I have
applied every line up to N" -- and acked lines leave the outbox. A hub that
attaches is sent every line after the last ack, in order, then live lines.
So each line reaches the hubs exactly once, except the lines a hub had
applied but not yet acked when it CRASHED (the client acks a card, a
response or the exit at once, anything else within its ack window; a hub
that detaches on purpose acks first).

CARDS. A request from the agent (a permission card) is held until a hub's
answer for its id passes through here, not until it is acked: a hub that
attaches is re-sent every card still unanswered, first, and never one that
was answered. An answer is accepted once; a second answer for the same id is
dropped here, so the agent gets one reply per question.

OWNERSHIP. One hub at a time. A newer connection replaces the older, whose
socket is shut down. What a hub sends is ACCEPTED (bookkeeping, then queued
for the adapter's stdin) under the lock, and only while that hub is the
owner; one stdin-writer thread drains the queue to the adapter. No lock is
ever held across a socket or pipe write, so a hub or an adapter that stops
reading blocks only its own writer thread.

BACKPRESSURE. Each hub connection has a reader and a drain thread. The
reader applies control lines (ack, close) at once and puts every other line
on the connection's inbound queue, up to STDIN_MAX_BYTES; the drain applies
those, in order, under the owner check. So an agent that stops reading stdin
delays acks and a close only once BOTH queues are full. A hub that hangs up
has its inbound queue applied before it lets go of the agent; a REPLACED
hub's inbound lines are dropped, as its waiting line always was.

Wire, keeper -> hub (newline-delimited JSON):
    {"keeper": "hello", "version": 3, ...}       first, once
    {"keeper": "l", "s": N, "m": <adapter line>}  every numbered line
    {"keeper": "caught-up", "dropped": D}         once the backlog is sent
Wire, hub -> keeper:
    {"keeper": "ack", "s": N} | {"keeper": "close"} | any JSON-RPC line

Bounds: one adapter line up to MAX_LINE (a line that is not a JSON object
is dropped and counted); the outbox up to OUTBOX_MAX_BYTES (past it the
oldest notifications not yet acked are dropped and counted -- never a card,
a response or the exit); the stdin queue and each hub's inbound queue up to
STDIN_MAX_BYTES each (a hub past both waits); with no hub for ORPHAN_S the adapter is ended; after the exit the
keeper lingers until a hub acks it, or LINGER_S.

Stdlib only. Runs as the hub's user; the socket is 0600, unique to this
keeper (the hub names it), in a 0700 dir.
"""
import argparse
import collections
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# The client reads at most 16 MiB per line; the envelope adds a few dozen
# bytes, so an adapter line is capped below that (a cut line is not JSON and
# is dropped, counted).
MAX_LINE = 16 * 1024 * 1024 - 4096
MAX_STDERR_LINE = 256 * 1024
STDERR_TAIL = 40
OUTBOX_MAX_BYTES = int(os.environ.get("CORRAL_KEEPER_OUTBOX_MAX") or 64 * 1024 * 1024)
STDIN_MAX_BYTES = 16 * 1024 * 1024
WRITE_BATCH = 256                      # lines per socket write
ORPHAN_S = int(os.environ.get("CORRAL_KEEPER_ORPHAN_S") or 6 * 3600)
LINGER_S = 600
TERM_WAIT_S = 5
ACCEPT_BACKLOG = 4
VERSION = 3

NOTE, RESP, CARD, EXIT = "note", "resp", "card", "exit"


def _log(msg):
    print(f"keeper[{os.getpid()}]: {msg}", file=sys.stderr, flush=True)


def _read_line(stream, limit):
    """One line (bytes, newline kept) or None at EOF; a line past `limit`
    is cut there and the rest of it discarded, never buffered whole."""
    buf = bytearray()
    cut = False
    while True:
        chunk = stream.readline(limit + 1 - len(buf) if not cut else 65536)
        if not chunk:
            return bytes(buf) if buf else None
        if cut:
            if chunk.endswith(b"\n"):
                return bytes(buf) + b"\n"
            continue
        buf += chunk
        if chunk.endswith(b"\n"):
            return bytes(buf)
        if len(buf) > limit:
            cut = True
            del buf[limit:]


def _parse(line):
    try:
        msg = json.loads(line)
    except (ValueError, RecursionError):        # RecursionError: nested too deep
        return None
    return msg if isinstance(msg, dict) else None


def _key(rid):
    """One key for a request id whatever its JSON type (1 and "1" alike)."""
    return str(rid)


def _valid_id(rid):
    """A JSON-RPC id this keeper can key on: str, int or null (not bool)."""
    return rid is None or isinstance(rid, str) or (
        isinstance(rid, int) and not isinstance(rid, bool))


def envelope(seq, line):
    """The numbered frame for one outbox line, built without re-encoding it."""
    return b'{"keeper":"l","s":%d,"m":' % seq + line.rstrip(b"\r\n") + b"}\n"


class Entry:
    __slots__ = ("line", "kind", "rid")

    def __init__(self, line, kind, rid=None):
        self.line, self.kind, self.rid = line, kind, rid


class Conn:
    """One hub connection and the thread that writes to it. The writer reads
    the outbox under the lock and writes outside it."""

    def __init__(self, sock, keeper, gen, hello, held, start):
        self.sock = sock
        self.keeper = keeper
        self.gen = gen
        self.file = sock.makefile("wb")
        self.hello = hello                  # bytes, snapshotted at attach
        self.held = held                    # [(seq, line)] unanswered cards <= acked
        self.next = start                   # next outbox seq to send
        self.closed = False
        self.inq = collections.deque()      # non-control lines read, not yet applied
        self.inq_bytes = 0
        self.read_done = None               # why the reader ended, once it has
        self.thread = threading.Thread(target=self._run, daemon=True, name="hub-out")

    def _run(self):
        k = self.keeper
        caught_up_sent = False
        try:
            self.file.write(self.hello)
            with k.lock:
                # Re-checked here, not at attach: a card answered since the
                # snapshot is not re-sent. (The window from here to the write
                # remains; the client drops a card it already answered.)
                held = [(s, l) for s, l in self.held if s in k.held]
            for seq, line in held:
                self.file.write(envelope(seq, line))
            self.file.flush()
            while True:
                out = []
                with k.lock:
                    while not self.closed and self.next >= k.next_seq and caught_up_sent:
                        k.cv.wait()
                    if self.closed:
                        return
                    self.next = max(self.next, k.base)
                    while self.next < k.next_seq and len(out) < WRITE_BATCH:
                        e = k.entries[self.next - k.base]
                        if e is not None and not k.answered_locked(e):
                            out.append(envelope(self.next, e.line))
                        self.next += 1
                    caught = self.next >= k.next_seq and not caught_up_sent
                    dropped = k.dropped
                for b in out:
                    self.file.write(b)
                if caught:
                    self.file.write(k.frame("caught-up", dropped=dropped))
                    caught_up_sent = True
                self.file.flush()
        except (OSError, ValueError) as e:
            with k.lock:
                if k.conn is self:
                    k.drop_conn_locked(f"write failed: {e}")

    def shutdown(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Keeper:
    def __init__(self, argv, cwd, sock_path, work_dir):
        self.argv = argv
        self.cwd = cwd
        self.sock_path = Path(sock_path)
        self.dir = Path(work_dir)
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)      # outbox grew / conn changed
        self.stdin_cv = threading.Condition(self.lock)
        # The outbox: entries[i] is seq base+i; None is a dropped line.
        self.entries = collections.deque()
        self.base = 1
        self.next_seq = 1
        self.bytes = 0
        self.dropped = 0                    # lines dropped (overflow, not JSON), total
        self.acked = 0                      # cumulative ack from the hubs
        self.held = {}                      # seq -> line: unanswered cards at or below acked
        self.cards = {}                     # agent request id (str) -> seq, until answered
        self.outstanding = {}               # str(id) -> (id, method), until its response is acked
        self.resp_seq = {}                  # str(id) -> seq of its response
        self.max_hub_id = 0
        self.exit_seq = None
        self.exit_rc = None
        self.stderr_tail = []
        self.conn = None
        self.gen = 0
        self.stdin_q = collections.deque()
        self.stdin_bytes = 0
        self.last_hub_seen = time.monotonic()
        self.stop = threading.Event()
        self.closing = False
        self.p = None

    # ── the outbox ───────────────────────────────────────────────────────
    @staticmethod
    def frame(kind, **fields):
        return (json.dumps({"keeper": kind, **fields}) + "\n").encode("utf-8")

    def append(self, line, kind=None, rid=None):
        """Number one adapter line (or the exit) and queue it for the hub."""
        if kind is None:
            msg = _parse(line)
            if msg is None:
                with self.lock:
                    self.dropped += 1       # not a JSON object: never valid ACP
                return None
            if "id" in msg and "method" not in msg:
                kind, rid = RESP, msg["id"]
            elif "id" in msg and "method" in msg:
                kind, rid = CARD, str(msg["id"])
            else:
                kind = NOTE
        with self.lock:
            seq = self.next_seq
            self.next_seq += 1
            self.entries.append(Entry(line, kind, rid))
            self.bytes += len(line)
            if kind == RESP:
                self.resp_seq[_key(rid)] = seq
            elif kind == CARD:
                self.cards[rid] = seq
            elif kind == EXIT:
                self.exit_seq = seq
            self._bound_locked()
            self.cv.notify_all()
            return seq

    def _bound_locked(self):
        """Past OUTBOX_MAX_BYTES, drop the oldest unacked notifications."""
        if self.bytes <= OUTBOX_MAX_BYTES:
            return
        for i, e in enumerate(self.entries):
            if self.bytes <= OUTBOX_MAX_BYTES:
                break
            if e is not None and e.kind == NOTE:
                self.bytes -= len(e.line)
                self.entries[i] = None
                self.dropped += 1

    def answered_locked(self, e):
        """A card whose answer already passed through: never sent again."""
        return e.kind == CARD and e.rid not in self.cards

    def ack_locked(self, n):
        n = min(int(n), self.next_seq - 1)
        if n <= self.acked:
            return
        self.acked = n
        while self.entries and self.base <= self.acked:
            e = self.entries.popleft()
            if e is not None:
                self.bytes -= len(e.line)
                if e.kind == CARD and e.rid in self.cards:
                    self.held[self.base] = e.line          # unanswered: held for a re-raise
            self.base += 1
        for key, s in list(self.resp_seq.items()):
            if s <= self.acked:
                self.resp_seq.pop(key, None)
                self.outstanding.pop(key, None)

    # ── adapter ──────────────────────────────────────────────────────────
    def start_adapter(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CORRAL_KEEPER_")}
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=self.cwd, env=env, bufsize=0)
        (self.dir / "keeper.json").write_text(json.dumps({
            "version": VERSION, "keeper_pid": os.getpid(), "adapter_pid": self.p.pid,
            "pgid": os.getpgid(0), "sock": str(self.sock_path),
            "started": time.time()}), encoding="utf-8")
        self._err_thread = threading.Thread(target=self._read_stderr, daemon=True,
                                            name="adapter-err")
        self._err_thread.start()
        threading.Thread(target=self._write_stdin, daemon=True, name="adapter-in").start()
        threading.Thread(target=self._read_adapter, daemon=True, name="adapter-out").start()

    def _read_stderr(self):
        try:
            while True:
                line = _read_line(self.p.stderr, MAX_STDERR_LINE)
                if line is None:
                    break
                text = line.decode("utf-8", "replace").rstrip("\n")
                with self.lock:
                    self.stderr_tail.append(text[:2000])
                    del self.stderr_tail[:-STDERR_TAIL]
        except (OSError, ValueError):
            pass

    def _read_adapter(self):
        try:
            while True:
                line = _read_line(self.p.stdout, MAX_LINE)
                if line is None:
                    break
                if line.strip():
                    self.append(line)
        except Exception as e:                       # noqa: BLE001
            # Whatever stops the reader, the exit below is still appended.
            _log(f"adapter stdout reader stopped: {type(e).__name__}: {e}")
        rc = None
        try:
            rc = self.p.wait(timeout=TERM_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        self._err_thread.join(timeout=2)        # its last words go in the exit
        with self.lock:
            self.exit_rc = rc if rc is not None else -1
            tail = list(self.stderr_tail)
            self.stdin_cv.notify_all()
        _log(f"adapter exited rc={self.exit_rc}")
        self.append(self.frame("exit", rc=self.exit_rc, stderr_tail=tail,
                               closed=self.closing), kind=EXIT)
        threading.Thread(target=self._linger, daemon=True, name="linger").start()

    def _write_stdin(self):
        """The one writer to the adapter's stdin. Blocks only itself."""
        while True:
            with self.lock:
                while not self.stdin_q and self.exit_rc is None and not self.stop.is_set():
                    self.stdin_cv.wait(1.0)
                if not self.stdin_q:
                    if self.exit_rc is not None or self.stop.is_set():
                        return
                    continue
                line = self.stdin_q.popleft()
                self.stdin_bytes -= len(line)
                self.stdin_cv.notify_all()
            try:
                self.p.stdin.write(line)
                self.p.stdin.flush()
            except (OSError, ValueError) as e:
                _log(f"adapter stdin closed: {e}")
                with self.lock:
                    self.stdin_q.clear()
                    self.stdin_bytes = 0
                    self.stdin_cv.notify_all()
                return

    def linger_done_locked(self):
        """After the exit: quit once a hub acked it and has gone."""
        return (self.exit_seq is not None and self.acked >= self.exit_seq
                and self.conn is None)

    def _linger(self):
        end = time.monotonic() + LINGER_S
        while time.monotonic() < end and not self.stop.is_set():
            with self.lock:
                if self.linger_done_locked():
                    break
            time.sleep(0.1)
        self.stop.set()

    # ── hubs ─────────────────────────────────────────────────────────────
    def serve(self):
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        srv.listen(ACCEPT_BACKLOG)
        srv.settimeout(0.2)
        threading.Thread(target=self._watch, daemon=True, name="watch").start()
        while not self.stop.is_set():
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.attach(c)
        try:
            srv.close()
        except OSError:
            pass
        try:
            self.sock_path.unlink()          # unique to this keeper: no one else's
        except OSError:
            pass

    def attach(self, sock):
        """A hub connected. Never blocks on anything but the lock."""
        with self.lock:
            if self.closing:
                try:
                    sock.close()
                except OSError:
                    pass
                return None
            self.drop_conn_locked("replaced by a new connection")
            self.gen += 1
            hello = self.frame(
                "hello", version=VERSION, pid=os.getpid(),
                adapter_pid=self.p.pid if self.p else None, pgid=os.getpgid(0),
                alive=self.exit_rc is None, rc=self.exit_rc,
                outstanding=[{"id": rid, "method": m}
                             for rid, m in self.outstanding.values()],
                next_id=self.max_hub_id + 1, acked=self.acked, next_seq=self.next_seq,
                held=len(self.held), dropped=self.dropped,
                # What this attach replays before live lines: the unacked,
                # undropped, unanswered lines plus the held cards.
                spooled=len(self.held) + sum(
                    1 for e in self.entries
                    if e is not None and not self.answered_locked(e)),
                stderr_tail=list(self.stderr_tail))
            held = sorted(self.held.items())
            c = Conn(sock, self, self.gen, hello, held, self.acked + 1)
            self.conn = c
            self.last_hub_seen = time.monotonic()
        _log("hub attached")
        c.thread.start()
        threading.Thread(target=self._read_hub, args=(c,), daemon=True, name="hub-in").start()
        threading.Thread(target=self._drain_hub, args=(c,), daemon=True,
                         name="hub-drain").start()
        return c

    def drop_conn_locked(self, why):
        c, self.conn = self.conn, None
        if c is not None:
            _log(f"hub disconnected: {why}")
            c.closed = True
            c.shutdown()
            self.cv.notify_all()
            self.stdin_cv.notify_all()
        self.last_hub_seen = time.monotonic()

    def _read_hub(self, c):
        """Control lines at once; the rest to the drain, in order. A clean
        end hands the drop to the drain, so lines read before the hang-up
        still reach the agent."""
        r = c.sock.makefile("rb")
        why = "hub closed the connection"
        failed = False
        try:
            while True:
                line = _read_line(r, MAX_LINE)
                if line is None:
                    break
                if not line.strip():
                    continue
                msg = _parse(line)
                if msg is not None and "keeper" in msg:
                    ok = self.accept_from_hub(c, line)
                else:
                    ok = self.queue_from_hub(c, line)
                if not ok:
                    break
        except (OSError, ValueError):
            pass
        except Exception as e:                       # noqa: BLE001
            # A line nothing above expected must not leave a dead reader
            # owning the agent (the orphan watch would never fire).
            why = f"hub reader failed: {type(e).__name__}: {e}"
            failed = True
        finally:
            with self.lock:
                c.read_done = why
                if failed and self.conn is c:
                    self.drop_conn_locked(why)
                self.stdin_cv.notify_all()
            try:
                r.close()
            except OSError:
                pass

    def queue_from_hub(self, c, line):
        """Put a non-control line on `c`'s inbound queue (the reader only).
        -> False when `c` has been replaced."""
        with self.lock:
            while True:
                if self.conn is not c:
                    return False
                if c.inq_bytes + len(line) <= STDIN_MAX_BYTES or not c.inq:
                    break
                self.stdin_cv.wait(1.0)
            c.inq.append(line)
            c.inq_bytes += len(line)
            self.stdin_cv.notify_all()
        return True

    def _drain_hub(self, c):
        """The one thread that applies `c`'s inbound lines, in order, each
        under the owner check in accept_from_hub. Once the reader has ended
        cleanly and the queue is empty, `c` lets go of the agent."""
        try:
            while True:
                with self.lock:
                    while not c.inq and c.read_done is None and self.conn is c:
                        self.stdin_cv.wait(1.0)
                    if self.conn is not c:
                        return
                    if not c.inq:
                        self.drop_conn_locked(c.read_done)
                        return
                    line = c.inq.popleft()
                    c.inq_bytes -= len(line)
                    self.stdin_cv.notify_all()
                if not self.accept_from_hub(c, line):
                    return
        except Exception as e:                       # noqa: BLE001
            with self.lock:
                if self.conn is c:
                    self.drop_conn_locked(f"hub drain failed: {type(e).__name__}: {e}")

    def accept_from_hub(self, c, line):
        """Take one line from hub `c`: bookkeeping and the stdin queue, both
        under the lock and only while `c` owns the agent. -> False when `c`
        has been replaced (it drives nothing from then on)."""
        msg = _parse(line)
        close = False                       # this hub's close, claimed under the lock
        with self.lock:
            while True:
                if self.conn is not c:
                    return False
                if msg is not None and "keeper" in msg:
                    break                                    # no stdin space needed
                if self.closing:
                    return False                             # the agent is being ended
                if self.exit_rc is not None:
                    break                                    # bookkeeping only, below
                if self.stdin_bytes + len(line) <= STDIN_MAX_BYTES or not self.stdin_q:
                    break
                self.stdin_cv.wait(1.0)                      # the agent is not reading
            if msg is not None and "keeper" in msg:
                cmd = msg.get("keeper")
                if cmd == "ack":
                    try:
                        self.ack_locked(msg.get("s"))
                    except (TypeError, ValueError, OverflowError):   # 1e309 is inf
                        pass
                    self.cv.notify_all()
                # Claimed in the same hold as the owner check: a hub
                # replaced after this point cannot have its close run.
                close = cmd == "close" and self.claim_close_locked()
            else:
                if msg is not None and "id" in msg and not _valid_id(msg["id"]):
                    self.dropped += 1                # JSON-RPC ids are str, int or null
                    return True
                if msg is not None and "id" in msg and "method" in msg:
                    # Keyed by str(id): a reply whose id is "1" for request 1
                    # still clears it. The raw id goes back in the hello.
                    self.outstanding[_key(msg["id"])] = (msg["id"], msg["method"])
                    if isinstance(msg["id"], int):
                        self.max_hub_id = max(self.max_hub_id, msg["id"])
                elif msg is not None and "id" in msg:
                    rid = str(msg["id"])
                    seq = self.cards.pop(rid, None)
                    if seq is None:
                        # This card was answered already (or never asked):
                        # the agent gets one reply per question.
                        return True
                    self.held.pop(seq, None)
                if self.exit_rc is None:
                    self.stdin_q.append(line)
                    self.stdin_bytes += len(line)
                    self.stdin_cv.notify_all()
        if close:
            self.end_adapter()
        return True

    # ── lifecycle ────────────────────────────────────────────────────────
    def claim_close_locked(self):
        if self.closing:
            return False
        self.closing = True
        return True

    def close_adapter(self):
        """End the adapter (SIGTERM, then SIGKILL) and the keeper with it."""
        with self.lock:
            if not self.claim_close_locked():
                return
        self.end_adapter()

    def end_adapter(self):
        _log("close requested")
        if self.p is not None and self.p.poll() is None:
            try:
                self.p.terminate()
                self.p.wait(timeout=TERM_WAIT_S)
            except subprocess.TimeoutExpired:
                self.p.kill()
            except OSError:
                pass
        try:
            os.killpg(os.getpgid(0), signal.SIGTERM)   # ignored by us once closing
        except (ProcessLookupError, PermissionError, OSError):
            pass
        self.stop.set()

    def _watch(self):
        while not self.stop.is_set():
            time.sleep(5)
            if self.watch_once():
                return

    def watch_once(self, now=None):
        """No hub for ORPHAN_S: end the adapter. Decision and claim are one
        hold of the lock, and attach refuses once a close is claimed."""
        now = time.monotonic() if now is None else now
        with self.lock:
            orphaned = (self.conn is None and now - self.last_hub_seen > ORPHAN_S
                        and self.exit_rc is None)
            if not orphaned or not self.claim_close_locked():
                return False
            since = now - self.last_hub_seen
        _log(f"no hub for {int(since)}s; ending the agent")
        self.end_adapter()
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sock", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not cmd:
        ap.error("no adapter command given after --")
    k = Keeper(cmd, a.cwd, a.sock, a.dir)

    def on_term(*_):
        if not k.closing:
            threading.Thread(target=k.close_adapter, daemon=True).start()
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    k.start_adapter()
    k.serve()
    if k.p.poll() is None:
        k.close_adapter()
    return 0


if __name__ == "__main__":
    sys.exit(main())
