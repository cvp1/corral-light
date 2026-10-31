#!/usr/bin/python3
"""keeper — holds one agent process so a hub restart does not end it.

The hub used to fork each ACP adapter itself, over pipes. Restarting the hub
(`corral-light update`, a crash, a service restart) closed the pipes and the
agent died mid-turn, taking the turn, its pending permission cards and its
queued work with it. The keeper sits between the two: a small process in its
own session that owns the adapter's stdio and offers it to the hub over a
Unix socket. The hub connects, talks ACP through the keeper, and if the hub
goes away the keeper spools what the agent says until a hub connects again.

Wire (newline-delimited JSON on the socket):
    keeper -> hub   first a `{"keeper": "hello", ...}` frame, then every
                    response the previous hub never acknowledged, then every
                    spooled adapter line (verbatim), then
                    `{"keeper": "replayed", "n": N}`, then live adapter lines.
                    `{"keeper": "exit", "rc": .., "stderr_tail": [...]}` when
                    the adapter stops. Adapter lines never carry a top-level
                    `keeper` key (they are JSON-RPC), so the two cannot mix.
    hub -> keeper   `{"keeper": "close"}` ends the adapter and the keeper;
                    `{"keeper": "ack", "id": N}` says the hub has the response
                    to its request N; `{"keeper": "ack-exit"}` says it has
                    the adapter's exit (the keeper may quit); anything else
                    is written to the adapter's stdin as is.

A line is CARRIED once a hub has been sent it and the hello carries it again
(an unanswered permission card, an unacked response). A carried line is
never spooled or replayed a second time: the next hub gets it once, from the
hello or the unacked replay, never also from the spool.

The hello frame carries what a NEW hub needs to pick the conversation up:
    outstanding     hub->agent requests whose response no hub has
                    acknowledged: [{id, method}] (a `session/prompt` here
                    means a turn is running, or ended with nobody listening)
    next_id         one past the largest request id any hub has used
    agent_requests  agent->hub requests (permission cards) a hub was sent and
                    never answered; the new hub re-raises them
    spooled / spool_overflow   lines held while no hub was connected
    alive / rc      whether the adapter still runs

Delivery is one writer thread per connection over a byte-bounded queue: a
hub that stops reading is dropped (and its undelivered lines spooled) rather
than freezing the adapter, a replacement, or the orphan watch. A replaced
connection is shut down, so a stale hub's reader gets EOF and can neither
drive the adapter nor answer a card the new hub owns.

Bounds: one adapter line up to MAX_LINE; the spool up to SPOOL_MAX_BYTES and
the outbound queue up to QUEUE_MAX_BYTES (past either, lines are counted and
dropped, and the hub is told); with no hub for ORPHAN_S the adapter is ended
and the keeper exits; after the adapter exits the keeper lingers LINGER_S
for a hub to collect the exit, then quits.

Stdlib only. Runs as the hub's user; the socket is 0600 in a 0700 dir.
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

MAX_LINE = 16 * 1024 * 1024          # one adapter line; the hub's bound too
MAX_STDERR_LINE = 256 * 1024
STDERR_TAIL = 40                     # lines kept for the exit frame
SPOOL_MAX_BYTES = 64 * 1024 * 1024   # held while no hub is connected
QUEUE_MAX_BYTES = int(os.environ.get("CORRAL_KEEPER_QUEUE_MAX") or 64 * 1024 * 1024)
MAX_UNACKED = 64                     # responses kept for a hub that died before reading
ORPHAN_S = int(os.environ.get("CORRAL_KEEPER_ORPHAN_S") or 6 * 3600)
LINGER_S = 600                       # after the adapter exits, for a hub to hear it
TERM_WAIT_S = 5                      # SIGTERM grace before SIGKILL on close
ACCEPT_BACKLOG = 2
VERSION = 2


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
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


class _Conn:
    """One hub connection: its socket, a byte-bounded outbound queue, and
    the writer thread that drains it. Replay (hello, unacked responses, the
    spool) is the writer's first job, so live lines queue behind it in order."""

    def __init__(self, sock, keeper):
        self.sock = sock
        self.keeper = keeper
        self.file = sock.makefile("wb")
        self.q = collections.deque()
        self.bytes = 0
        self.cv = threading.Condition(keeper.lock)      # shares the keeper's lock
        self.closed = False
        self.inflight = None                             # popped, being written now
        self.replay = []                                 # lines written before the spool
        self.thread = threading.Thread(target=self._write_loop, daemon=True, name="hub-out")

    def enqueue_locked(self, line):
        """-> False when this hub is not keeping up (the caller drops it)."""
        if self.bytes + len(line) > QUEUE_MAX_BYTES:
            return False
        self.q.append(line)
        self.bytes += len(line)
        self.cv.notify_all()
        return True

    def _write_loop(self):
        k = self.keeper
        try:
            for line in self.replay:
                self.file.write(line)
            self.file.flush()
            if k.replay_spool and k.spool_path.exists():
                with k.spool_path.open("rb") as sp:
                    for line in sp:
                        with k.lock:
                            if k.conn is not self:
                                return               # replaced mid-replay; the spool stays
                            if k._carried_locked(line):
                                continue             # the hello already gave it to this hub
                            k._sent_locked(line)
                        self.file.write(line)
                self.file.flush()
            with k.lock:
                if k.conn is self:
                    k.spool_cleared_locked()
            self.file.write(k._frame("replayed", n=k.replay_count, dropped=k.replay_dropped))
            self.file.flush()
            while True:
                with k.lock:
                    while not self.q and not self.closed:
                        self.cv.wait()
                    if self.closed and not self.q:
                        return
                    line = self.q.popleft()
                    self.bytes -= len(line)
                    self.inflight = line
                self.file.write(line)
                self.file.flush()
                with k.lock:
                    if self.inflight is line:
                        self.inflight = None
        except (OSError, ValueError) as e:
            with k.lock:
                # Dropping moves the in-flight line and the queue to the spool,
                # in order; if a newer hub replaced us, that already happened.
                if k.conn is self:
                    k._drop_conn_locked(f"write failed: {e}")

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
        self.spool_path = self.dir / "spool.jsonl"
        self.lock = threading.Lock()          # conn, spool, bookkeeping
        self.conn = None                      # the connected hub (_Conn)
        self.spooled = 0                      # lines in the spool file
        self.spool_bytes = 0
        self.spool_dropped = 0
        self.replay_count = 0                 # what the current attach replays
        self.replay_dropped = 0
        self.replay_spool = False
        self.outstanding = {}                 # hub req id -> method, until acked
        self.unacked = collections.OrderedDict()   # hub req id -> response line sent, not acked
        self.max_hub_id = 0
        self.agent_requests = collections.OrderedDict()   # agent req id -> frame (bytes)
        self.stderr_tail = []
        self.exit_rc = None
        self.exit_acked = False               # a hub confirmed it has the adapter's exit
        self.last_hub_seen = time.monotonic()
        self.stop = threading.Event()
        self.closing = False
        self.p = None
        self.stdin_lock = threading.Lock()
        # Held across "is this hub still the owner?" and the write to the
        # adapter (or a close), and taken by _attach before it swaps owners:
        # a replaced hub cannot slip one already-read command in after.
        # Order: drive_lock, then lock.
        self.drive_lock = threading.Lock()
        # A previous keeper's spool in a reused dir is not this agent's.
        try:
            self.spool_path.unlink()
        except FileNotFoundError:
            pass

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
                self._deliver(line)
        except (OSError, ValueError) as e:
            _log(f"adapter stdout reader stopped: {e}")
        rc = None
        try:
            rc = self.p.wait(timeout=TERM_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        # Its last words on stderr (an auth failure names itself there) must
        # be in the exit frame: let the stderr reader reach EOF first.
        self._err_thread.join(timeout=2)
        with self.lock:
            self.exit_rc = rc if rc is not None else -1
            tail = list(self.stderr_tail)
        _log(f"adapter exited rc={self.exit_rc}")
        self._deliver(self._frame("exit", rc=self.exit_rc, stderr_tail=tail,
                                  closed=self.closing), exit_frame=True)
        self._linger_then_stop()

    def _linger_then_stop(self):
        def run():
            end = time.monotonic() + LINGER_S
            while time.monotonic() < end and not self.stop.is_set():
                with self.lock:
                    done = self._linger_done_locked()
                if done:
                    break
                time.sleep(0.1)
            self.stop.set()
        threading.Thread(target=run, daemon=True, name="linger").start()

    def _linger_done_locked(self):
        """May the keeper quit after the adapter's exit? Only once a hub
        ACKED the exit and has gone: an exit frame merely queued for a hub
        that then stalled and was dropped reached nobody (2026-10-31 F9)."""
        return self.exit_acked and self.conn is None

    # ── bookkeeping ──────────────────────────────────────────────────────
    def _sent_locked(self, line):
        """An adapter line was handed to a hub (queued live, or replayed).
        A response to a hub request stays `outstanding`, and its line is
        kept, until that hub ACKS it: a flush is not a receipt, and a hub
        that dies between the two must not lose the turn's end. An agent
        request (a permission card) is remembered as unanswered from here."""
        msg = _parse(line)
        if msg is None:
            return
        if "id" in msg and "method" not in msg:               # response to a hub request
            if msg["id"] in self.outstanding or str(msg["id"]) in self.outstanding:
                self.unacked[msg["id"]] = line
                while len(self.unacked) > MAX_UNACKED:
                    old, _ = self.unacked.popitem(last=False)
                    self.outstanding.pop(old, None)
        elif "id" in msg and "method" in msg:                 # agent asks the hub
            self.agent_requests[str(msg["id"])] = line

    def _carried_locked(self, line):
        """Will the next hello (or the unacked replay) give a hub this line?"""
        msg = _parse(line)
        if msg is None:
            return False
        if "keeper" in msg:
            return True                       # hello carries alive/rc itself
        if "id" in msg and "method" not in msg:
            return msg["id"] in self.unacked or str(msg["id"]) in self.unacked
        if "id" in msg and "method" in msg:
            return str(msg["id"]) in self.agent_requests
        return False

    def _requeue_locked(self, line):
        """A line a dropped hub never got: to the spool unless carried."""
        if line is not None and not self._carried_locked(line):
            self._spool_locked(line)

    def _from_hub_locked(self, msg):
        if "id" in msg and "method" in msg:
            self.outstanding[msg["id"]] = msg["method"]
            if isinstance(msg["id"], int):
                self.max_hub_id = max(self.max_hub_id, msg["id"])
        elif "id" in msg and "method" not in msg:             # answers an agent request
            self.agent_requests.pop(str(msg["id"]), None)

    def _ack_locked(self, rid):
        keys = {rid, str(rid)}
        try:
            keys.add(int(rid))
        except (TypeError, ValueError):
            pass
        for key in keys:
            self.outstanding.pop(key, None)
            self.unacked.pop(key, None)

    # ── delivery and the spool ───────────────────────────────────────────
    @staticmethod
    def _frame(kind, **fields):
        return (json.dumps({"keeper": kind, **fields}) + "\n").encode("utf-8")

    def _deliver(self, line, exit_frame=False):
        with self.lock:
            c = self.conn
            if c is not None:
                if c.enqueue_locked(line):
                    if not exit_frame:
                        self._sent_locked(line)
                    return
                self._drop_conn_locked("not reading: outbound queue full")
            if not exit_frame:                # the next hello says alive/rc
                self._spool_locked(line)

    def _spool_locked(self, line):
        if self.spool_bytes + len(line) > SPOOL_MAX_BYTES:
            self.spool_dropped += 1
            return
        try:
            with self.spool_path.open("ab") as fh:
                fh.write(line)
            self.spooled += 1
            self.spool_bytes += len(line)
        except OSError as e:
            self.spool_dropped += 1
            _log(f"spool write failed: {e}")

    def spool_cleared_locked(self):
        """The current attach has replayed the spool: start it over."""
        try:
            self.spool_path.unlink()
        except OSError:
            pass
        self.spooled = self.spool_bytes = self.spool_dropped = 0

    def _drop_conn_locked(self, why):
        """Let go of the current hub. What it was sent but may not have
        read (the in-flight line, then the queue) goes to the spool in
        order, minus what the next hello carries anyway."""
        c, self.conn = self.conn, None
        if c is not None:
            _log(f"hub disconnected: {why}")
            c.closed = True
            pending = ([c.inflight] if c.inflight is not None else []) + list(c.q)
            c.inflight = None
            c.q.clear()
            c.bytes = 0
            for line in pending:
                self._requeue_locked(line)
            c.cv.notify_all()
            c.shutdown()
        self.last_hub_seen = time.monotonic()

    # ── the hub's side ───────────────────────────────────────────────────
    def serve(self):
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        try:
            mine = os.stat(self.sock_path).st_ino
        except OSError:
            mine = None
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
            self._attach(c)
        try:
            srv.close()
        except OSError:
            pass
        # Remove the socket file only if it is still OURS: a resume may have
        # started a successor on the same path while we were shutting down,
        # and unlinking its door made it unreachable (measured 2026-10-30).
        try:
            if mine is not None and os.stat(self.sock_path).st_ino == mine:
                self.sock_path.unlink()
        except OSError:
            pass

    def _attach(self, sock):
        """A hub connected: hello, the unacked responses, the spool, then
        live. One hub at a time; a newer connection replaces the older,
        whose socket is shut down so its reader sees EOF at once."""
        conn = _Conn(sock, self)
        with self.drive_lock, self.lock:
            if self.closing:
                # An orphan close is under way (or the operator closed it):
                # this hub gets no hello and treats the keeper as gone.
                conn.shutdown()
                return
            self._drop_conn_locked("replaced by a new connection")
            hello = self._frame(
                "hello", version=VERSION, pid=os.getpid(), adapter_pid=self.p.pid,
                pgid=os.getpgid(0), alive=self.exit_rc is None, rc=self.exit_rc,
                outstanding=[{"id": k, "method": v} for k, v in self.outstanding.items()],
                next_id=self.max_hub_id + 1,
                agent_requests=[m for m in (_parse(v) for v in self.agent_requests.values()) if m],
                spooled=self.spooled, spool_overflow=self.spool_dropped,
                unacked=len(self.unacked), stderr_tail=list(self.stderr_tail))
            conn.replay = [hello] + list(self.unacked.values())
            self.replay_count = self.spooled + len(self.unacked)
            self.replay_dropped = self.spool_dropped
            self.replay_spool = self.spooled > 0
            self.conn = conn
            self.last_hub_seen = time.monotonic()
        _log("hub attached")
        conn.thread.start()
        threading.Thread(target=self._read_hub, args=(conn,), daemon=True,
                         name="hub-in").start()

    def _read_hub(self, conn):
        r = conn.sock.makefile("rb")
        try:
            while True:
                line = _read_line(r, MAX_LINE)
                if line is None:
                    break
                if line.strip() == b"":
                    continue
                if not self._drive(conn, line):
                    break                            # replaced: this hub drives nothing
        except (OSError, ValueError):
            pass
        with self.lock:
            if self.conn is conn:
                self._drop_conn_locked("hub closed the connection")

    def _drive(self, conn, line):
        """One line from hub `conn`: bookkeeping, then to the adapter (or a
        keeper command). -> False, doing nothing, when `conn` no longer owns
        the agent. The owner check and the write share drive_lock."""
        msg = _parse(line) if line.lstrip().startswith(b"{") else None
        with self.drive_lock:
            with self.lock:
                if self.conn is not conn:
                    return False
                if msg is not None and "keeper" in msg:
                    cmd = msg.get("keeper")
                    if cmd == "ack":
                        self._ack_locked(msg.get("id"))
                    elif cmd == "ack-exit":
                        self.exit_acked = True
                elif msg is not None:
                    self._from_hub_locked(msg)
            if msg is not None and "keeper" in msg:
                if msg.get("keeper") == "close":
                    self.close_adapter()
                return True
            if self.p.stdin is None:
                return True
            try:
                with self.stdin_lock:
                    self.p.stdin.write(line)
                    self.p.stdin.flush()
            except (OSError, ValueError) as e:
                _log(f"adapter stdin closed: {e}")
            return True

    # ── lifecycle ────────────────────────────────────────────────────────
    def _claim_close_locked(self):
        if self.closing:
            return False
        self.closing = True
        return True

    def close_adapter(self):
        """End the adapter (SIGTERM, then SIGKILL) and the keeper with it."""
        with self.lock:
            if not self._claim_close_locked():
                return
        self._end_adapter()

    def _end_adapter(self):
        _log("close requested")
        if self.p.poll() is None:
            try:
                self.p.terminate()
                self.p.wait(timeout=TERM_WAIT_S)
            except subprocess.TimeoutExpired:
                self.p.kill()
            except OSError:
                pass
        self._kill_group_except_self()
        self.stop.set()

    def _kill_group_except_self(self):
        try:
            pgid = os.getpgid(0)
        except OSError:
            return
        # SIGTERM the group; our own copy is ignored once `closing` is set
        # (the handler in main checks it). No SIGKILL from in here: that
        # would take the keeper down before the socket is cleaned up; the
        # hub's close() SIGKILLs the group after the keeper is gone anyway.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _watch(self):
        """No hub for ORPHAN_S: end the adapter rather than run forever."""
        while not self.stop.is_set():
            time.sleep(5)
            if self._watch_once():
                return

    def _watch_once(self, now=None):
        """-> True when it ended the adapter. The decision and the claim
        are one hold of the lock, and _attach refuses once a close is
        claimed, so a hub that attaches in between is never cut off after."""
        now = time.monotonic() if now is None else now
        with self.lock:
            orphaned = (self.conn is None and now - self.last_hub_seen > ORPHAN_S
                        and self.exit_rc is None)
            if not orphaned or not self._claim_close_locked():
                return False
            since = now - self.last_hub_seen
        _log(f"no hub for {int(since)}s; ending the agent")
        self._end_adapter()
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

    # Our own SIGTERM (a hub's killpg on close) ends the adapter cleanly; the
    # copy we send the group while closing is ignored.
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
