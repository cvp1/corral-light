#!/usr/bin/python3
"""acp — a JSON-RPC 2.0 client for Agent Client Protocol agents over stdio.

Permission requests block the agent until answered, so each is held and
answered exactly once. Agents run under a Corral-owned CLAUDE_CONFIG_DIR so
host permission settings cannot suppress prompts. No clock ends a prompt; only
the process dying does. Stdlib only; every buffer is bounded.
Must not import from `corral/` (asserted by test_no_corral_import).
"""
import concurrent.futures
import collections
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# No clock ends a prompt: only process death does. Silence is reported
# (stall_notice), never acted on.
STALL_NOTICE_S = 900            # report silence after this long; never act
POLL_S = 5                      # request() re-check interval
# Only pre-session handshake calls are time-bounded.
HANDSHAKE_TIMEOUT = 180
PERMISSION_TIMEOUT = None       # never auto-answered; released on agent exit
MAX_PENDING_PERMISSIONS = 32    # waiter threads are per-request; bound them
MAX_EVENTS_PER_SESSION = 4000   # bounded ring; the JSONL on disk is the record
# Bound the raw line read: readline() would buffer an unterminated line whole.
MAX_STDOUT_LINE = 16 * 1024 * 1024


MAX_STDERR_LINE = 256 * 1024    # stderr carries log lines, not payloads

# The keeper (keeper.py, beside corral_core/): a process in its own session
# that owns the adapter's stdio and serves it over a Unix socket, so a hub
# restart does not end the agent. The hub talks the same ACP through it.
KEEPER_PY = Path(__file__).resolve().parent.parent / "keeper.py"
KEEPER_CONNECT_S = 20           # for a freshly spawned keeper's socket to answer
KEEPER_REATTACH_S = 5           # for a surviving keeper's socket to answer
KEEPER_PROTOCOL = 3              # the oldest keeper protocol this client drives
# Keeper protocol 3 (numbered outbox, cumulative acks): a card, a response
# or the exit is acked the moment it is applied; anything else within this.
KEEPER_ACK_EVERY_S = 0.2
DETACH_APPLY_WAIT_S = 2         # detach waits this long for a line being applied
ANSWERED_CARDS_MAX = 1024


class AgentError(Exception):
    pass


class _Unanswered:
    """Sentinel: 'no human selection yet', distinct from a falsy option id."""
    def __repr__(self):
        return "<unanswered>"


_UNANSWERED = _Unanswered()


# One long-lived thread does every fork (see AcpClient.__init__).
_SPAWNER = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="acp-spawn")


# A hung Popen would block the single spawner thread for every lane.
SPAWN_TIMEOUT_S = 30


def _reap_late(fut):
    """Kill a Popen that finished after its caller timed out."""
    try:
        p = fut.result()
    except Exception:                               # noqa: BLE001
        return
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        p.wait(timeout=2)
    except Exception:                               # noqa: BLE001
        pass
    for s in (p.stdin, p.stdout, p.stderr):
        _close_quietly(s)


def _spawn(fn, timeout=None):
    """Run `fn` (a Popen) on the spawner thread, bounded by SPAWN_TIMEOUT_S."""
    fut = _SPAWNER.submit(fn)
    try:
        return fut.result(timeout=SPAWN_TIMEOUT_S if timeout is None else timeout)
    except concurrent.futures.TimeoutError:
        fut.add_done_callback(_reap_late)
        raise AgentError(
            f"the agent process did not start within "
            f"{SPAWN_TIMEOUT_S if timeout is None else timeout}s — the spawner "
            f"is stuck (every lane waits behind it); see the hub's log")


class _KeeperProc:
    """What AcpClient needs of a Popen, answered over a keeper's socket: the
    adapter's stdio as text files, a pid (the keeper's, the group leader, so
    killpg reaches the adapter too), and poll()/wait() fed by the keeper's
    exit frame. terminate() asks the keeper to end the adapter."""

    def __init__(self, sock, reader, hello, keeper_pid):
        self.sock = sock
        self.stdout = reader
        self.stdin = sock.makefile("w", encoding="utf-8", newline="\n")
        self.stderr = None                      # the keeper keeps it; see hello
        self.pid = keeper_pid
        self.adapter_pid = hello.get("adapter_pid")
        self.rc = None
        self._ended = threading.Event()
        if not hello.get("alive", True):
            self.exited(hello.get("rc"))

    def poll(self):
        return self.rc

    def wait(self, timeout=None):
        if not self._ended.wait(timeout):
            raise subprocess.TimeoutExpired("keeper", timeout)
        return self.rc

    def exited(self, rc):
        self.rc = -1 if rc is None else rc
        self._ended.set()

    def terminate(self):
        try:
            self.stdin.write(json.dumps({"keeper": "close"}) + "\n")
            self.stdin.flush()
        except (OSError, ValueError):
            pass

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass

    def close_socket(self):
        """End the connection. shutdown() first: a readline() in progress on
        another thread returns EOF at once, where closing its file would wait
        for the read to finish (measured: a detach blocked until the agent's
        next line, which then landed in the hub that was letting go). The
        reader thread closes `stdout` itself when its loop ends."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        _close_quietly(self.stdin)
        _close_quietly(self.sock)


class AcpClient:
    """One agent subprocess. Thread-safe for one writer per method."""

    def _init_state(self):
        """All mutable client state; separate so tests can build via __new__."""
        self._id = 0
        self._pending = {}
        self._wlock = threading.Lock()
        self._idlock = threading.Lock()
        self._perm_answers = {}
        self._permlock = threading.Lock()   # claims a card exactly once
        self._last_activity = time.monotonic()
        self.alive = False
        self.exit_reason = None
        self.stderr_tail = []
        self._closed = False       # set by close()
        self._detached = False     # set by detach(): the hub lets go, the agent stays
        self._stop_reading = False
        self.keeper = None         # {sock, dir} when a keeper holds the adapter
        self.kp = None             # the keeper Popen (None for a reattached one)
        self.adopted = []          # reattach(): hub requests still outstanding
        self.hello = None
        self._kv = None            # the keeper's protocol version (3), None with no keeper
        self._applied = 0          # v3: the last numbered line this hub applied
        self._acked_sent = 0
        self._ack_stop = threading.Event()
        # Held by the reader while it applies one line; detach takes it so
        # its final ack covers a line being applied as it lets go.
        self._apply_lock = threading.Lock()
        # Cards this client answered (bounded): a keeper re-sending one in
        # the window between its re-check and its write is dropped here.
        self._answered_cards = collections.OrderedDict()
        # v3: lines the pane has taken in but not yet written out (buffered
        # text, an open thought): tag -> the seq it started at. No ack passes
        # them, so a restart replays what was never emitted.
        self._holds = {}
        self._cur_seq = 0

    def __init__(self, argv, cwd, env=None, on_event=None, on_permission=None,
                 strip_env=(), keeper=None):
        self.argv = list(argv)
        self.cwd = str(cwd)
        self.on_event = on_event or (lambda kind, payload: None)
        self.on_permission = on_permission or (lambda req: None)
        self._init_state()

        # Drop env vars matching `strip_env` prefixes so an ambient vendor key
        # cannot outrank the verified login.
        full_env = {k: v for k, v in os.environ.items()
                    if not (strip_env and k.startswith(tuple(strip_env)))}
        full_env.update(env or {})
        if keeper:
            self.keeper = dict(keeper)
            self._spawn_keeper(full_env)
            return
        # Fork from a long-lived thread: PR_SET_PDEATHSIG fires when the
        # forking THREAD exits, and callers run on short-lived HTTP threads.
        try:
            self.p = _spawn(lambda: subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                env=full_env, cwd=self.cwd, start_new_session=True))
        except OSError as e:
            raise AgentError(f"could not start {self.argv[0]}: {e}")
        # Record pgid now; getpgid() fails once the leader exits.
        self.pgid = self.p.pid
        # Start time lets a restarted hub tell its orphan from a reused pid.
        self.start_token = process_start_token(self.p.pid)
        self.alive = True
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ── the keeper: an adapter that outlives this hub ────────────────────
    def _spawn_keeper(self, full_env):
        """Start keeper.py around the adapter and attach to its socket."""
        sock_path, work = str(self.keeper["sock"]), Path(self.keeper["dir"])
        try:
            work.mkdir(parents=True, exist_ok=True, mode=0o700)
            Path(sock_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                Path(sock_path).unlink()
            except FileNotFoundError:
                pass
        except OSError as e:
            raise AgentError(f"could not prepare the keeper's dir: {e}")
        argv = [sys.executable, "-I", str(KEEPER_PY), "--sock", sock_path,
                "--dir", str(work), "--cwd", self.cwd, "--", *self.argv]
        try:
            log = (work / "keeper.log").open("wb")
        except OSError as e:
            raise AgentError(f"could not open the keeper's log: {e}")
        try:
            self.kp = _spawn(lambda: subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                env=full_env, cwd=self.cwd, start_new_session=True))
        except OSError as e:
            raise AgentError(f"could not start the keeper: {e}")
        finally:
            _close_quietly(log)
        self.pgid = self.kp.pid
        self.start_token = process_start_token(self.kp.pid)
        try:
            self._connect_keeper(sock_path, self.kp.pid, KEEPER_CONNECT_S)
        except AgentError:
            try:
                os.killpg(self.kp.pid, signal.SIGKILL)
            except OSError:
                pass
            raise
        self.alive = True          # the stream: it ends after the exit line
        threading.Thread(target=self._read_stdout, daemon=True).start()
        self._start_acks()

    def _start_acks(self):
        threading.Thread(target=self._ack_loop, daemon=True, name="keeper-ack").start()

    def _ack_loop(self):
        while not self._ack_stop.wait(KEEPER_ACK_EVERY_S):
            self._send_ack()

    def hold(self, tag):
        """The pane buffered line `_cur_seq` under `tag`: acks stop before it."""
        if self._kv is not None:
            self._holds.setdefault(tag, self._cur_seq)

    def release(self, tag):
        """The pane wrote out what it held under `tag`."""
        self._holds.pop(tag, None)

    def _send_ack(self):
        n = self._applied
        holds = list(self._holds.values())
        if holds:
            n = min(n, min(holds) - 1)
        if n > self._acked_sent:
            try:
                self._write({"keeper": "ack", "s": n})
                self._acked_sent = n
            except AgentError:
                pass

    def _applied_line(self, seq, urgent):
        """v3: this hub has applied numbered line `seq` (emitted, raised,
        settled). Urgent lines are acked now, so a crash cannot repeat them."""
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            return
        if seq > self._applied:
            self._applied = seq
        if urgent:
            self._send_ack()

    def _connect_keeper(self, sock_path, keeper_pid, timeout):
        """Connect to a keeper's socket and read its hello. Sets self.p."""
        deadline = time.monotonic() + timeout
        sock = None
        while True:
            if self.kp is not None and self.kp.poll() is not None:
                raise AgentError(f"the keeper exited rc={self.kp.returncode} before "
                                 f"its socket answered; see its keeper.log")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect(sock_path)
                sock = s
                break
            except (FileNotFoundError, ConnectionRefusedError):
                _close_quietly(s)
                if time.monotonic() >= deadline:
                    raise AgentError(f"the keeper's socket did not answer within "
                                     f"{timeout}s ({sock_path})")
                time.sleep(0.05)
            except OSError as e:
                _close_quietly(s)
                raise AgentError(f"could not reach the keeper: {e}")
        reader = sock.makefile("r", encoding="utf-8", errors="replace")
        try:
            line = reader.readline()
            hello = json.loads(line) if line.strip() else None
        except (OSError, ValueError):
            hello = None
        if not isinstance(hello, dict) or hello.get("keeper") != "hello":
            _close_quietly(reader)
            _close_quietly(sock)
            raise AgentError("the keeper did not say hello")
        try:
            kv = int(hello.get("version") or 2)
        except (TypeError, ValueError):
            kv = 2
        if kv < KEEPER_PROTOCOL:
            # Protocol 2 was retired once no keeper ran it. A keeper this old
            # cannot be driven safely (its acks mean something else): refuse,
            # loudly, and leave the keeper to its orphan watch.
            _close_quietly(reader)
            _close_quietly(sock)
            raise AgentError(f"the keeper speaks protocol {kv}; this hub needs "
                             f"{KEEPER_PROTOCOL} or later (restart that agent)")
        sock.settimeout(None)
        self.hello = hello
        self._kv = kv
        self.p = _KeeperProc(sock, reader, hello, keeper_pid)
        self.stderr_tail = [str(x)[:400] for x in (hello.get("stderr_tail") or [])][-40:]
        with self._idlock:
            self._id = max(self._id, int(hello.get("next_id") or 1) - 1)
        return hello

    @classmethod
    def reattach(cls, sock_path, keeper_pid, cwd, on_event=None, on_permission=None):
        """Adopt an adapter a keeper kept alive across a hub restart.

        `adopted` lists the hub requests the previous hub never saw answered
        (a `session/prompt` means a turn is still running: wait on it with
        await_adopted). Permission requests the old hub left unanswered are
        raised again through `on_permission`, then whatever the agent said
        while no hub listened arrives through `on_event` in order.
        """
        c = cls.__new__(cls)
        c.argv, c.cwd = [], str(cwd)
        c.on_event = on_event or (lambda kind, payload: None)
        c.on_permission = on_permission or (lambda req: None)
        c._init_state()
        c.keeper = {"sock": str(sock_path)}
        hello = c._connect_keeper(str(sock_path), int(keeper_pid), KEEPER_REATTACH_S)
        c.pgid = int(hello.get("pgid") or keeper_pid)
        c.start_token = process_start_token(c.pgid)
        for o in hello.get("outstanding") or []:
            if isinstance(o, dict) and o.get("id") is not None:
                c.adopted.append({"id": o["id"], "method": str(o.get("method") or "")})
                c._pending[o["id"]] = {"ev": threading.Event(), "result": None,
                                       "error": None}
        # alive is the STREAM (it ends after the exit line, which follows
        # every answer the agent gave), so a waiter on an adopted turn of an
        # agent that already exited still gets the replayed answer. Cards the
        # previous hub never answered come first, as numbered lines.
        c.alive = True
        threading.Thread(target=c._read_stdout, daemon=True).start()
        c._start_acks()
        return c

    def await_adopted(self, rid, method="session/prompt"):
        """Wait for the answer to a request a previous hub sent."""
        slot = self._pending.get(rid)
        if slot is None:
            raise AgentError(f"{method}: no such outstanding request")
        return self._wait_slot(rid, slot, method, None)

    def detach(self, flush=None):
        """Let go of a kept adapter without ending it: the socket closes, the
        keeper spools, and no waiter here is told anything (the hub is
        exiting). A client with no keeper is simply closed."""
        if not isinstance(getattr(self, "p", None), _KeeperProc):
            return self.close()
        # Wait (bounded) for a line being applied to finish, so the final
        # ack covers it; after this the reader applies nothing more.
        got = self._apply_lock.acquire(timeout=DETACH_APPLY_WAIT_S)
        try:
            self._detached = True
            self._stop_reading = True
            self._ack_stop.set()
            if flush is not None:
                try:
                    flush()         # the pane writes out what it buffered...
                except Exception:   # noqa: BLE001
                    pass
            self._send_ack()        # ...so the ack covers it, and no more
        finally:
            if got:
                self._apply_lock.release()
        self.p.close_socket()

    # ── wire ─────────────────────────────────────────────────────────────
    def _write(self, obj):
        if not self.alive and not (isinstance(obj, dict) and "keeper" in obj
                                   and isinstance(getattr(self, "p", None), _KeeperProc)):
            raise AgentError("agent is not running")
        try:
            with self._wlock:
                self.p.stdin.write(json.dumps(obj) + "\n")
                self.p.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            self.alive = False
            raise AgentError(f"agent stdin closed: {e}")

    def request(self, method, params=None, timeout=None):
        """Send a JSON-RPC request and wait for its response.

        `timeout=None` waits until the process dies; pass a number only for
        handshake calls. Time blocked on a permission card is not counted.
        """
        with self._idlock:
            self._id += 1
            rid = self._id
            slot = {"ev": threading.Event(), "result": None, "error": None}
            self._pending[rid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": params or {}})
        except AgentError:
            with self._idlock:
                self._pending.pop(rid, None)
            raise
        return self._wait_slot(rid, slot, method, timeout)

    def _wait_slot(self, rid, slot, method, timeout):
        # Poll in slices so silence can be reported without being acted on.
        noticed = False
        sent_at = time.monotonic()
        blocked_on_human = 0.0          # time spent waiting on a permission card
        while not slot["ev"].wait(POLL_S):
            if not self.alive:
                # Must raise: `alive` flips before slot errors are written, so
                # returning would report an empty successful turn.
                self._pending.pop(rid, None)
                raise AgentError(
                    f"{method}: {slot['error'] or self.exit_reason or 'agent stopped'}")
            quiet = time.monotonic() - self._last_activity
            if self._perm_answers:
                # Blocked on a permission card: not silence, and excluded
                # from the handshake bound.
                self._last_activity = time.monotonic()
                blocked_on_human += POLL_S
                noticed = False
                continue
            if timeout is not None and \
                    time.monotonic() - sent_at - blocked_on_human >= timeout:
                # Handshake only: elapsed time since send, not since last
                # output, so chatter cannot extend it.
                self._pending.pop(rid, None)
                raise AgentError(
                    f"{method} timed out after {timeout}s "
                    f"(not counting time waiting on you)")
            if quiet < STALL_NOTICE_S:
                noticed = False         # output resumed; re-arm the notice
            if quiet >= STALL_NOTICE_S and not noticed:
                # Report once and keep waiting.
                noticed = True
                self.on_event("stall_notice", {
                    "method": method, "quietFor": int(quiet),
                    "text": f"no output for {int(quiet // 60)} min — still "
                            f"attached and still waiting; pause or stop the "
                            f"pane if it looks wedged"})
        self._pending.pop(rid, None)
        if slot["error"]:
            raise AgentError(f"{method}: {slot['error']}")
        return slot["result"]

    def _read_stderr(self):
        if getattr(self.p, "stderr", None) is None:
            return                      # a keeper holds it; its tail rides the frames
        try:
            while True:
                line = self._read_bounded_line(self.p.stderr, MAX_STDERR_LINE)
                if line is None:
                    break
                if line.strip():
                    self.stderr_tail.append(line.rstrip()[:400])
                    del self.stderr_tail[:-40]        # bounded
        except (ValueError, OSError):
            pass                        # stop reading stderr; keep the pane
        finally:
            _close_quietly(getattr(self.p, "stderr", None))    # this thread owns the pipe

    @staticmethod
    def _read_bounded_line(stream, limit=None):
        """Read one line, raising ValueError if it exceeds `limit` chars."""
        limit = MAX_STDOUT_LINE if limit is None else limit
        chunks, total = [], 0
        while True:
            chunk = stream.readline(limit - total + 1)
            if chunk == "":
                return "".join(chunks) if chunks else None      # EOF
            chunks.append(chunk)
            total += len(chunk)
            if chunk.endswith("\n"):
                return "".join(chunks)
            if total > limit:
                raise ValueError(
                    f"agent output line exceeded {limit} bytes "
                    f"without a newline — refusing to buffer it")

    def _read_stdout(self):
        try:
            while not self._stop_reading:
                line = self._read_bounded_line(self.p.stdout)
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue            # valid JSON but not a message
                with self._apply_lock:
                    if self._stop_reading:
                        break           # detached: the next hub gets this line
                    self._dispatch(msg)
        except (ValueError, OSError) as e:
            self.exit_reason = str(e)
        except Exception as e:  # noqa: BLE001
            self.exit_reason = f"reader failed: {type(e).__name__}: {e}"
        finally:
            if self._detached:
                # The hub let go on purpose; the agent runs on under its
                # keeper, and nothing here is news to anyone. This thread
                # owns stdout; it closes it.
                _close_quietly(getattr(self.p, "stdout", None))
            else:
                self._reader_ended()

    def _reader_ended(self):
        """The adapter's stream ended: settle the process, release every
        waiter, and report the exit."""
        self._send_ack()                 # the exit line, if it was the last one
        self._ack_stop.set()
        self.alive = False
        rc = self.p.poll()
        if self.kp is not None:
            self.kp.poll()                       # reap a keeper that is gone (no zombie)
        if rc is None and self.exit_reason and not self._closed:
            # The reader failed but the process lives; kill its group.
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.p.kill()
                except Exception:       # noqa: BLE001
                    pass
            try:
                rc = self.p.wait(timeout=2)
            except Exception:           # noqa: BLE001
                pass
        if self._closed:
            # rc=-15 is our own SIGTERM, not a failure.
            self.exit_reason = "closed by you"
        else:
            self.exit_reason = self.exit_reason or (
                f"agent exited rc={rc}" + (f": {self.stderr_tail[-1]}"
                                           if self.stderr_tail else ""))
        # Release every waiter on a dead process. A slot already answered
        # (a replayed completion, then the exit frame) keeps its answer.
        for slot in list(self._pending.values()):
            if slot["ev"].is_set():
                continue
            slot["error"] = self.exit_reason
            slot["ev"].set()
        for key, slot in list(self._perm_answers.items()):
            slot["ev"].set()
            self.on_event("permission_expired",
                          {"requestId": key, "reason": "agent exited"})
        # This thread owns stdout; close it to avoid an fd leak.
        _close_quietly(getattr(self.p, "stdout", None))
        close_sock = getattr(self.p, "close_socket", None)
        if close_sock is not None:
            close_sock()                     # the keeper then quits
        self.on_event("agent_exit", {"reason": self.exit_reason,
                                     "closed": self._closed, "rc": rc})

    def _dispatch(self, msg):
        # Any parsed message counts as activity for the stall notice.
        self._last_activity = time.monotonic()
        if "keeper" in msg and isinstance(self.p, _KeeperProc):
            if msg.get("keeper") == "l":                       # v3: one numbered line
                inner = msg.get("m")
                urgent = False
                try:
                    self._cur_seq = int(msg.get("s"))
                except (TypeError, ValueError):
                    pass
                if isinstance(inner, dict):
                    urgent = "id" in inner or "keeper" in inner
                    self._dispatch_line(inner)
                self._applied_line(msg.get("s"), urgent)
                return
            return self._on_keeper_frame(msg)
        return self._dispatch_line(msg)

    def _dispatch_line(self, msg):
        if "keeper" in msg and isinstance(self.p, _KeeperProc):
            return self._on_keeper_frame(msg)                  # the exit, numbered (v3)
        if "id" in msg and "method" not in msg:               # response to us
            slot = self._pending.get(msg["id"])
            if slot:
                slot["result"], slot["error"] = msg.get("result"), msg.get("error")
                slot["ev"].set()
            return
        if "method" in msg and "id" in msg:                    # agent asks us
            return self._on_request(msg)
        if msg.get("method") == "session/update":              # notification
            u = (msg.get("params") or {}).get("update") or {}
            self.on_event(u.get("sessionUpdate") or "unknown", u)

    def _on_keeper_frame(self, msg):
        kind = msg.get("keeper")
        if kind == "caught-up":
            self.on_event("keeper_replayed", {"n": msg.get("n") or 0,
                                              "dropped": msg.get("dropped") or 0})
            if self.p.rc is not None:
                # The adapter had already exited when we attached and its
                # exit frame went to the previous hub: this is the end.
                self._closed = self._closed or bool((self.hello or {}).get("closed"))
                self.exit_reason = self.exit_reason or f"agent exited rc={self.p.rc}"
                self._stop_reading = True
        elif kind == "exit":
            tail = [str(x)[:400] for x in (msg.get("stderr_tail") or [])][-40:]
            if tail:
                self.stderr_tail = tail
            if msg.get("closed"):
                self._closed = True
            self.p.exited(msg.get("rc"))       # acked as a numbered line
            self._stop_reading = True

    def _on_request(self, msg):
        method, rid, params = msg["method"], msg["id"], msg.get("params") or {}
        if method == "session/request_permission":
            # Hand the card up synchronously (keeps transcript order); wait
            # on a separate thread so the reader keeps reading.
            key = f"{rid}"
            # Refuse duplicates too: overwriting a live slot leaks its waiter.
            too_many = len(self._perm_answers) >= MAX_PENDING_PERMISSIONS
            duplicate = key in self._perm_answers
            if (key in self._answered_cards
                    and isinstance(getattr(self, "p", None), _KeeperProc)):
                return                  # answered here already: never raised twice
            if duplicate and isinstance(getattr(self, "p", None), _KeeperProc):
                # A keeper replaying a card this hub already holds (the hello
                # raised it): the agent asked ONCE, so it gets ONE answer --
                # the human's. Answering "cancelled" here would refuse a card
                # still on screen and give the agent two replies.
                return
            if too_many or duplicate:
                reason = (f"permission request id {key} is already pending"
                          if duplicate else
                          f"more than {MAX_PENDING_PERMISSIONS} "
                          f"permission requests pending")
                self._write({"jsonrpc": "2.0", "id": rid, "result": {
                    "outcome": {"outcome": "cancelled"}}})
                self.on_event("permission_expired",
                              {"requestId": key, "reason": reason})
                return
            ev = threading.Event()
            self._perm_answers[key] = {"ev": ev, "option": _UNANSWERED}
            # Our requestId must win over any agent-supplied one.
            self.on_permission({**params, "requestId": key})
            threading.Thread(target=self._await_permission,
                             args=(key, rid, params, ev), daemon=True,
                             name=f"acp-perm-{key}").start()
            return
        # No fs/terminal capabilities advertised; refuse anything else.
        self._write({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"unsupported: {method}"}})

    def _await_permission(self, key, rid, params, ev):
        """Wait for one permission answer off the reader thread, then reply.

        Released only by an answer or agent exit; never synthesizes an answer.
        """
        ev.wait(PERMISSION_TIMEOUT)
        with self._permlock:
            # Pop only our own slot (matched by Event): the id may already
            # belong to a newer request.
            cur = self._perm_answers.get(key)
            slot = cur if cur is not None and cur.get("ev") is ev else None
            if slot is not None:
                self._perm_answers.pop(key, None)
        option = (slot or {}).get("option", _UNANSWERED)
        # Identity check: option ids may be falsy ("" or 0).
        if not self.alive or option is _UNANSWERED:
            return
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "result": {
                "outcome": {"outcome": "selected", "optionId": option}}})
            with self._permlock:
                self._answered_cards[key] = True
                while len(self._answered_cards) > ANSWERED_CARDS_MAX:
                    self._answered_cards.popitem(last=False)
        except AgentError:
            pass                        # died mid-reply; exit path reports it

    def answer_permission(self, request_id, option_id):
        """Claim a pending card; the first answer wins, later ones get False."""
        with self._permlock:
            slot = self._perm_answers.get(str(request_id))
            if not slot or slot["option"] is not _UNANSWERED:
                return False          # already answered, or expired
            slot["option"] = option_id
            slot["ev"].set()
        return True

    # ── protocol ─────────────────────────────────────────────────────────
    def initialize(self):
        return self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False}}, timeout=60)

    def new_session(self, cwd, mcp_servers=None):
        return self.new_session_full(cwd, mcp_servers).get("sessionId")

    def new_session_full(self, cwd, mcp_servers=None):
        """The whole response: sessionId, modes, and configOptions (model,
        effort, mode, fast) with their allowed values and labels."""
        return self.request("session/new", {"cwd": str(cwd),
                                            "mcpServers": mcp_servers or []},
                            timeout=120) or {}

    def set_config(self, session_id, config_id, value):
        """Set a session config option; ids and values come from configOptions."""
        return self.request("session/set_config_option",
                            {"sessionId": session_id, "configId": config_id,
                             "value": value}, timeout=60)

    def load_session(self, session_id, cwd, mcp_servers=None):
        """Re-attach a fresh agent process to an existing conversation.

        The agent replays history as session/update; the caller must suppress it.
        """
        return self.request("session/load",
                            {"sessionId": session_id, "cwd": str(cwd),
                             "mcpServers": mcp_servers or []},
                            timeout=HANDSHAKE_TIMEOUT) or {}

    def prompt(self, session_id, text):
        return self.request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}]})

    def cancel(self, session_id):
        try:
            self._write({"jsonrpc": "2.0", "method": "session/cancel",
                         "params": {"sessionId": session_id}})
            return True
        except AgentError:
            return False

    def list_sessions(self, cwd=None):
        try:
            r = self.request("session/list", {"cwd": cwd} if cwd else {}, timeout=60)
            return r.get("sessions", []) if isinstance(r, dict) else (r or [])
        except AgentError:
            return []

    def close(self):
        self._closed = True
        self.alive = False
        # Signal and wait on the whole process group so children are reaped.
        pgid = getattr(self, "pgid", None) or self.p.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.p.terminate()
            except Exception:
                pass
        try:
            self.p.wait(timeout=3)
        except Exception:
            pass
        if self.kp is not None:
            # Reap the keeper we forked: until its parent waits on it, it is a
            # zombie, and a zombie still answers for its process group below.
            try:
                self.kp.wait(timeout=3)
            except Exception:               # noqa: BLE001
                pass
        # Bounded: close() runs inside an HTTP handler.
        deadline = time.time() + 2
        while time.time() < deadline:
            if not _group_alive(pgid):
                break
            time.sleep(0.1)
        if _group_alive(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.p.kill()
                except Exception:
                    pass
            # Give SIGKILL a moment to land.
            gone = time.time() + 2
            while time.time() < gone and _group_alive(pgid):
                time.sleep(0.05)
        try:
            self.p.wait(timeout=2)              # reap, so it is not a zombie
        except Exception:
            pass
        _close_quietly(getattr(self.p, "stdin", None))           # the writer end is ours
        if isinstance(self.p, _KeeperProc):
            self.p.close_socket()
            self._unlink_own_socket()

    def _unlink_own_socket(self):
        """Remove this keeper's socket file. Each keeper has its own name
        (sessions.keeper_sock_path), so no successor ever shares the path;
        the inode guard this replaced could not tell a successor apart on
        Linux, where tmpfs reuses inode numbers (CI, 2026-10-31)."""
        try:
            Path(self.keeper["sock"]).unlink()
        except (OSError, KeyError, TypeError):
            pass


# ── orphans from a previous hub ─────────────────────────────────────────────
# Adapters run in their own session and can outlive a hub exit. restore()
# passes recorded pid/pgid/start time here before any session/load; identity
# is verified before any signal since pids get reused.
ORPHAN_TERM_WAIT_S = 5      # shared SIGTERM grace for all orphans
ORPHAN_KILL_WAIT_S = 2      # wait after SIGKILL
PS_TIMEOUT_S = 5            # one `ps` call


def process_start_token(pid):
    """An exec-stable fingerprint of when `pid` started, or None.

    Linux reads /proc (clock ticks since boot); elsewhere `ps -o lstart=`.
    """
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        # comm (field 2) may contain spaces and parens; split after the LAST ')'
        return "proc:" + raw.rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        pass
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))],
                           capture_output=True, text=True, timeout=PS_TIMEOUT_S)
        s = " ".join(r.stdout.split())
        return ("ps:" + s) if r.returncode == 0 and s else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def process_args(pid):
    """The command line of `pid` as one string, or None if it is gone."""
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        if raw:
            return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(["ps", "-o", "args=", "-p", str(int(pid))],
                           capture_output=True, text=True, timeout=PS_TIMEOUT_S)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def is_our_adapter(pid, pgid, start=None, argv=()):
    """(verdict, why): True only if the pid still leads `pgid` and matches the
    recorded start time (or, lacking one, the lane's argv[0..1]). Doubt is False.
    """
    try:
        pid, pgid = int(pid), int(pgid)
    except (TypeError, ValueError):
        return False, "no pid recorded"
    if pid <= 1 or pgid <= 1:
        return False, "refusing a system pid"
    try:
        if os.getpgid(pid) != pgid:
            return False, f"pid {pid} no longer leads group {pgid}"
    except ProcessLookupError:
        return False, "not running"
    except OSError as e:
        return False, f"cannot inspect pid {pid}: {e}"
    if start:
        now = process_start_token(pid)
        if now != start:
            return False, (f"pid {pid} was reused by another process "
                           f"(start {now!r} != recorded {start!r})")
        return True, "start time matches"
    args = process_args(pid) or ""
    want = [a for a in list(argv)[:2] if a]
    if want and all(_argv_piece_present(a, args) for a in want):
        return True, "command line matches the lane"
    return False, f"pid {pid} runs {args[:80]!r}, not this lane's adapter"


def _argv_piece_present(piece, args):
    """Is one recorded argv element visible in a live command line?

    Python interpreters match by basename family (macOS framework Python
    re-execs as `.../Python`); everything else matches literally.
    """
    if piece in args:
        return True
    base = os.path.basename(piece).lower()
    if not base.startswith("python"):
        return False
    head = args.split(" ", 1)[0]
    return os.path.basename(head).lower().startswith("python")


def process_parent(pid):
    """The parent pid of `pid`, or None. Linux reads /proc; elsewhere `ps`."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        return int(raw.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(int(pid))],
                           capture_output=True, text=True, timeout=PS_TIMEOUT_S)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def owning_hub(pid):
    """The pid of a RUNNING hub that still parents adapter `pid`, or None.

    An orphan is an adapter whose hub died: the kernel has re-parented it to
    init/launchd (pid 1) or a subreaper such as `systemd --user`. An adapter
    whose parent is a live process running a `hub.py` is not an orphan, no
    matter what a state directory says -- it belongs to a hub that is serving
    right now. Without this check, any process that built a Manager against a
    live hub's state (a test that forgot its scratch state, a second hub
    started by mistake) killed every live pane's agent: measured 2026-10-01,
    twice, on the pane doing the work. A parent that is this very process is
    not "another hub" and answers None.
    """
    ppid = process_parent(pid)
    if not ppid or ppid <= 1 or ppid == os.getpid():
        return None
    return ppid if "hub.py" in (process_args(ppid) or "") else None


def reap_orphans(candidates, term_wait=None, kill_wait=None):
    """SIGTERM every verified orphan group, wait once, SIGKILL survivors.

    `candidates`: [(key, pid, pgid, start, argv)]. Returns {key: outcome}:
    "reaped", "killed", or why it was left alone.
    """
    term_wait = ORPHAN_TERM_WAIT_S if term_wait is None else term_wait
    kill_wait = ORPHAN_KILL_WAIT_S if kill_wait is None else kill_wait
    out, groups = {}, {}
    for key, pid, pgid, start, argv in candidates:
        ok, why = is_our_adapter(pid, pgid, start, argv)
        if not ok:
            out[key] = f"left alone: {why}"
            continue
        hub = owning_hub(pid)
        if hub:
            out[key] = f"left alone: still owned by a running hub (pid {hub})"
            continue
        try:
            os.killpg(int(pgid), signal.SIGTERM)
            groups[key] = int(pgid)
        except (ProcessLookupError, PermissionError, OSError) as e:
            out[key] = f"left alone: {e}"
    deadline = time.time() + term_wait
    while groups and time.time() < deadline:
        _reap_own_children(groups.values())
        if not any(_group_alive(g) for g in groups.values()):
            break
        time.sleep(0.05)
    for key, g in groups.items():
        if not _group_alive(g):
            out[key] = "reaped"
            continue
        try:
            os.killpg(g, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        end = time.time() + kill_wait
        while time.time() < end and _group_alive(g):
            _reap_own_children([g])
            time.sleep(0.05)
        out[key] = "killed" if not _group_alive(g) else "still running after SIGKILL"
    return out


def _reap_own_children(pids):
    """waitpid() each pid that is this process's own child, so a group leader
    we forked ourselves (a keeper, in a test or a hub that restarted in
    place) does not linger as a zombie that still answers for its group."""
    for pid in pids:
        try:
            os.waitpid(int(pid), os.WNOHANG)
        except (ChildProcessError, OSError, ValueError, TypeError):
            pass


def _close_quietly(stream):
    try:
        if stream is not None:
            stream.close()
    except Exception:                           # noqa: BLE001
        pass


def _group_alive(pgid):
    """Signal 0 asks 'does this group still exist' without touching it."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True                             # there, but not ours to poke

