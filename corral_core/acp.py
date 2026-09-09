#!/usr/bin/python3
"""acp — a JSON-RPC 2.0 client for Agent Client Protocol agents.

One transport for every agent Corral drives. The agent runs as a subprocess;
we speak JSON-RPC over its stdio. Verified against
@agentclientprotocol/claude-agent-acp 0.64.0 (spike/FINDINGS.md).

TWO THINGS THIS OWNS THAT THE UI MUST NOT
-----------------------------------------
1. **Permission requests are REQUESTS, not notifications.** The agent blocks
   until answered. So a pending permission is real backpressure on a real
   process, not a card in a list — if Corral drops it, that agent is stuck
   forever. Pending prompts are therefore held explicitly, surfaced, and
   answered exactly once.
2. **Permission posture is ours, not the host's.** Measured in the spike: this
   host's ambient `defaultMode: auto` silently suppressed EVERY prompt and the
   agent just acted. A pane inheriting that would show Craig no approvals at
   all — the UI faithfully rendering a config set for a different purpose, and
   deleting the feature the rail exists for. So every agent is launched under a
   CLAUDE_CONFIG_DIR Corral owns.

3. **No clock ends a session.** Craig's panes are his working space; only he
   clears or quits one. A prompt waits as long as the agent takes, and a
   permission card stays answerable until he answers it. Silence is reported,
   never acted on. See the block comment above the constants.

Stdlib only. Bounded: every buffer capped, every wait released by the process
dying — never by a deadline that outranks the operator.

THIS IS THE ONE COPY — full Corral and Corral Light both import it
------------------------------------------------------------------
Until 2026-09-09 this was two files. On 2026-08-31 a three-model adversarial
panel found fifteen defects in the permission rail and they were fixed in
`corral/acp.py` (394a174). The SAME DAY, corral-light gained the
credential-stripping fix above, which stops an ambient ANTHROPIC_API_KEY from
outranking the login the operator just verified (caf616e). **Neither commit
crossed the fork.** For nine days full Corral spawned agents under whatever
vendor key happened to be in its environment, and Corral Light — the PUBLIC,
MIT product shipped to other people — carried, among others:

  * an agent-supplied `params.requestId` overwriting the wire id the waiter is
    keyed on, so a consent card could reach the rail under an id naming some
    OTHER pending request: approval bound to bytes the human never saw
    (principle 17, gpt finding 1);
  * two near-simultaneous clicks both returning True, so a DENY could be
    overwritten by an allow and the person who pressed deny was told it landed
    (gpt finding 2);
  * a falsy-but-real option id ("" or 0) read as "no selection", so the click
    vanished and the agent blocked until the pane was killed (grok finding 8);
  * a non-object JSON line (`null`, `3`, `[]`) killing the reader thread,
    after which a healthy agent was reported as exited (grok finding 6);
  * an unbounded stderr readline, so one unterminated line grew RSS without
    limit (principle 8, grok finding 7);
  * a reader failure leaving the child's process group orphaned and running
    (gpt finding 8).

None of that was a coding mistake. It was topology: a fork with no seam, where
"we fixed it" and "the product our users run is fixed" are different sentences
and nothing in the tree said so. The rail has ONE home now and fixes land once.

Corral Light is the stricter of the two (public, MIT, no fleet, no delegates),
so the core lives in ITS tree and full Corral reaches sideways for it.
**Nothing in corral_core may import from `corral/`** — that would make the
public product depend on the private one and re-open the fork from the other
end. `test_no_corral_import` in Light's suite asserts it.
"""
import concurrent.futures
import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path

# A TURN IS NEVER ENDED BY A CLOCK. Craig, 2026-08-31: "I don't want to have a
# session end without me saying so. It needs to be something I either clear or
# I quit. Otherwise we persist a session. It's my working space."
#
# Two clocks tried to be smarter than that and both were wrong in the same
# direction — they killed a HEALTHY agent and took live work with it:
#   * 2026-08-23: a flat request-duration bound killed a 26-minute turn that
#     was streaming updates the whole time (it emitted at least every ~157s);
#     the underlying process ran on, orphaned. Fixed by making it a SILENCE
#     budget rather than a duration.
#   * 2026-08-31: the silence budget then killed a turn blocked on an
#     unanswered permission card — an agent doing exactly the right thing,
#     waiting for a human. "agent stopped — session/prompt timed out after
#     900s of silence", pane dead, work lost.
#
# There is no third fix, because the premise was wrong. A clock cannot tell a
# wedged agent from a slow one, from one waiting on Craig; only Craig can. The
# ONE thing that legitimately ends a turn is the process actually dying, and
# that is observed directly — _read_stdout's exit path releases every waiter
# the moment stdout EOFs. So a prompt waits for as long as it takes. Silence
# is now REPORTED (stall_notice → the pane goes `uncertain` in the rail) and
# never acted on. Ending it is a button: pause, or stop.
#
# Bounded (P8) is preserved without a kill: the wait terminates on process
# death, the notice fires once, and pending permission waiters stay capped by
# MAX_PENDING_PERMISSIONS.
STALL_NOTICE_S = 900            # silence for this long: SAY SO. Never act.
POLL_S = 5                      # how often request() re-checks; a knob so
                                 # tests need not wait real minutes
# Handshake calls keep a real bound, and only they. initialize / session/new /
# session/load / set_config all run BEFORE there is a working session — a hang
# there means nothing was ever established, so there is no working space to
# protect and failing is the only way to tell the operator it did not attach.
HANDSHAKE_TIMEOUT = 180
PERMISSION_TIMEOUT = None       # never auto-answered. Letting it lapse into a
                                 # reject after an hour was still a machine
                                 # ending Craig's turn for him. Fail-closed is
                                 # unharmed: an unanswered card is never an
                                 # allow — it stays a card until he clicks it
                                 # or stops the pane, which is strictly safer
                                 # than a synthetic no. Released on agent exit.
MAX_PENDING_PERMISSIONS = 32    # waiter threads are per-request; bound them (P8)
MAX_EVENTS_PER_SESSION = 4000   # bounded ring; the JSONL on disk is the record
# `for line in self.p.stdout` has NO size limit -- readline() will buffer an
# entire pathological line into memory before ever returning it. A
# malfunctioning or hostile adapter writing one giant unterminated line could
# exhaust the daemon's memory before Corral's own oversize handling
# (sessions.MAX_PERM_BYTES, 256KB) ever sees a parsed message -- that check
# runs on the DECODED payload, after this layer has already paid the cost of
# holding it all. Bound the READ itself. Any real ACP message (a diff, a tool
# result, a permission request) fits comfortably under this; a line this long
# is the protocol already broken. gpt-5.6-sol, third-pass review, finding 5.
MAX_STDOUT_LINE = 16 * 1024 * 1024


MAX_STDERR_LINE = 256 * 1024    # same argument as MAX_STDOUT_LINE, applied to
                                 # the OTHER pipe: stderr was still an
                                 # unbounded readline, so an adapter spewing a
                                 # single unterminated line grew Corral's RSS
                                 # without limit and the 400-char tail
                                 # truncation never ran (panel 2026-08-31, grok
                                 # finding 7 / gpt finding 5). Smaller than
                                 # stdout's: nothing legitimate on this pipe is
                                 # a payload, it is log lines.


class AgentError(Exception):
    pass


class _Unanswered:
    """Sentinel: 'no human selection yet', distinct from a falsy option id."""
    def __repr__(self):
        return "<unanswered>"


_UNANSWERED = _Unanswered()


# One thread, alive for the life of the process, that does every fork. See
# AcpClient.__init__ for why the forking thread's lifetime is load-bearing.
_SPAWNER = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="acp-spawn")


def _spawn(fn):
    """Run `fn` (a Popen) on the long-lived spawner thread and return its
    result, re-raising its exception unchanged."""
    return _SPAWNER.submit(fn).result()


class AcpClient:
    """One agent subprocess. Thread-safe for one writer per method."""

    def _init_state(self):
        """All mutable client state, in ONE place.

        Not inlined in __init__ because the tests build a client around a fake
        process with `__new__` and cannot run the real constructor (it would
        spawn something). That stub used to hand-copy this block, so adding a
        field here — `_permlock`, 2026-08-31 — broke the suite with an
        AttributeError instead of the suite proving the fix. Same lesson as
        Pane.from_meta in sessions.py: two hand-synchronised initialisers are
        one initialiser and a bug waiting for the next field.
        """
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
        self._closed = False       # set by close(): a deliberate stop

    def __init__(self, argv, cwd, env=None, on_event=None, on_permission=None,
                 strip_env=()):
        self.argv = list(argv)
        self.cwd = str(cwd)
        self.on_event = on_event or (lambda kind, payload: None)
        self.on_permission = on_permission or (lambda req: None)
        self._init_state()

        # The child inherits this process's whole environment, which is what
        # you want for PATH and HOME and what you very much do not want for a
        # vendor credential: an ANTHROPIC_API_KEY exported in the shell that
        # started the hub silently OUTRANKS the login the operator just
        # verified, and the agent then runs as a different identity than the
        # one the picker described. `strip_env` names the prefixes the caller
        # will not let through. (From corral-light, 2026-08-31 caf616e — it
        # never reached full Corral until the two forks were merged here on
        # 2026-09-09.)
        full_env = {k: v for k, v in os.environ.items()
                    if not (strip_env and k.startswith(tuple(strip_env)))}
        full_env.update(env or {})
        # Fork from a thread that OUTLIVES the caller. On Linux an agent may
        # ask the kernel for a parent-death signal (Grok Build does:
        # prctl(PR_SET_PDEATHSIG, SIGTERM), measured 2026-09-01), and that
        # signal fires when the parent THREAD that forked it exits -- not the
        # process. The hub creates panes on per-request HTTP threads, so every
        # Grok pane came up `ready` and was SIGTERMed the moment its request
        # returned: dead within the same second, "rc=143", no stderr, and the
        # hub's own trace showed itself as the sender with no kill() of its
        # own. One long-lived spawner thread is the parent of every agent.
        try:
            self.p = _spawn(lambda: subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                env=full_env, cwd=self.cwd, start_new_session=True))
        except OSError as e:
            raise AgentError(f"could not start {self.argv[0]}: {e}")
        # Remember the group id NOW. `start_new_session=True` makes the adapter
        # its own group leader, so the pgid IS the pid — and looking it up
        # later with os.getpgid() fails once the leader has exited, which is
        # precisely the moment its surviving children still need the signal.
        self.pgid = self.p.pid
        self.alive = True
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ── wire ─────────────────────────────────────────────────────────────
    def _write(self, obj):
        if not self.alive:
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

        `timeout=None` (the default, and what every prompt uses) means WAIT —
        only the agent process dying ends it. Pass a number only for the
        pre-session handshake calls, where there is no working space to lose.
        """
        # Allocate the id under a lock. The class docstring claimed thread
        # safety while `self._id += 1` was a read-modify-write shared by every
        # caller — a prompt and a set_config racing here would hand two
        # requests the same JSON-RPC id, and the first response to arrive would
        # satisfy both.
        with self._idlock:
            self._id += 1
            rid = self._id
            slot = {"ev": threading.Event(), "result": None, "error": None}
            self._pending[rid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": params or {}})
        except AgentError:
            # The slot was inserted before the write, so a failed write used to
            # leave it behind forever — and every retry against a dying client
            # added another (P8). Nothing will ever answer a request that was
            # never sent.
            with self._idlock:
                self._pending.pop(rid, None)
            raise
        # Poll in short slices rather than one long wait() so a busy agent --
        # still emitting session/update notifications, just not yet DONE with
        # the turn -- keeps resetting the clock. Only silence kills the wait;
        # a long turn that is visibly working never does. `timeout` is still a
        # hard bound: silence for that long, from a process that has gone
        # quiet, still raises.
        # Poll in slices rather than one long wait() so silence can be NOTICED
        # and reported without being acted on.
        noticed = False
        sent_at = time.monotonic()
        blocked_on_human = 0.0          # time this call spent waiting on Craig
        while not slot["ev"].wait(POLL_S):
            if not self.alive:
                # The process is gone. _read_stdout's exit path sets every
                # pending slot, so this is belt-and-braces for the case where
                # liveness flipped without this slot being released.
                #
                # It must RAISE, not fall through. `close()` and `_write()`
                # both flip `alive` BEFORE any error is written onto pending
                # slots, so breaking here and returning `slot["result"]` (None,
                # untouched) reported a live prompt as a successful empty turn
                # — a second way for something other than Craig to end his
                # turn, and the exact "pane says the turn ended" class of loss
                # the 2026-08-23 incident is. Panel 2026-08-31, grok finding 1,
                # concurred by gpt r2; reproduced before this fix.
                self._pending.pop(rid, None)
                raise AgentError(
                    f"{method}: {slot['error'] or self.exit_reason or 'agent stopped'}")
            quiet = time.monotonic() - self._last_activity
            if self._perm_answers:
                # Not silence at all: the agent asked a question and is
                # blocked on the answer. Don't count it as quiet — and don't
                # count it against the handshake bound either. Craig being
                # away is never a fault. The bound is SUSPENDED, not disabled:
                # the time is banked and the clock resumes the moment he
                # answers.
                self._last_activity = time.monotonic()
                blocked_on_human += POLL_S
                noticed = False
                continue
            if timeout is not None and \
                    time.monotonic() - sent_at - blocked_on_human >= timeout:
                # Handshake calls only — see HANDSHAKE_TIMEOUT above. Measured
                # as ELAPSED time since we sent, less the time spent waiting on
                # a human, rather than as time since the agent last said
                # anything: a bound that resets on every unrelated notification
                # is not a bound, and a `session/new` that never answers while
                # the adapter chatters could wait forever behind a nominal 120s
                # (panel 2026-08-31, grok finding 4 / gpt finding 11).
                # Nothing here bounds a PROMPT — prompts pass timeout=None,
                # and that stays true (constraint: no clock ends Craig's turn).
                self._pending.pop(rid, None)
                raise AgentError(
                    f"{method} timed out after {timeout}s "
                    f"(not counting time waiting on you)")
            if quiet < STALL_NOTICE_S:
                # Output resumed, so RE-ARM. The flag used to clear only while
                # a permission was pending, so a turn that stalled, recovered,
                # and stalled again went silent for the rest of its life — and
                # "say so" is the whole substitute for the kill-clock we
                # removed (panel 2026-08-31, grok finding 3; reproduced).
                noticed = False
            if quiet >= STALL_NOTICE_S and not noticed:
                # SAY it, once, and keep waiting. The pane turns `uncertain`
                # in the rail; Craig decides whether that means stop.
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
        try:
            while True:
                line = self._read_bounded_line(self.p.stderr, MAX_STDERR_LINE)
                if line is None:
                    break
                if line.strip():
                    self.stderr_tail.append(line.rstrip()[:400])
                    del self.stderr_tail[:-40]        # bounded
        except (ValueError, OSError):
            # An over-long stderr line is not worth ending the session over —
            # stop reading this pipe, keep the pane. (stdout is the opposite:
            # there, losing framing means losing the protocol.)
            pass

    @staticmethod
    def _read_bounded_line(stream, limit=None):
        """readline(size) stops at `size` characters OR a newline, whichever
        comes first, and never raises on hitting the limit -- it just returns
        a partial line. So the bound has to be enforced by US, across
        repeated bounded reads, not assumed from the call itself."""
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
            while True:
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
                    # `null`, `true`, `3` and `[]` are all valid JSON and all
                    # blew up `"id" in msg` with a TypeError that this handler
                    # did not catch — one stray line from any adapter killed
                    # the reader, and the finally below then reported a
                    # perfectly healthy agent as exited (panel 2026-08-31, grok
                    # finding 6; reproduced). A non-object is not a protocol
                    # message; skip it exactly like unparseable bytes.
                    continue
                self._dispatch(msg)
        except (ValueError, OSError) as e:
            self.exit_reason = str(e)
        except Exception as e:  # noqa: BLE001
            # Whatever it was, the reader is done and the pane must be told
            # honestly rather than the thread dying with a traceback on stderr
            # and the pane hanging on a client nothing is reading for.
            self.exit_reason = f"reader failed: {type(e).__name__}: {e}"
        finally:
            self.alive = False
            rc = self.p.poll()
            if rc is None and self.exit_reason and not self._closed:
                # The READER failed, not the process. Nothing else is reading
                # its stdout now, so leaving it running is the orphaned-child
                # leak in a new costume (gpt finding 8). Kill the group we
                # started and report what actually happened.
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
                # You asked it to stop. rc=-15 is OUR OWN SIGTERM, and
                # reporting it as "agent exited rc=-15" told Craig his
                # deliberate close was an incident.
                self.exit_reason = "closed by you"
            else:
                self.exit_reason = self.exit_reason or (
                    f"agent exited rc={rc}" + (f": {self.stderr_tail[-1]}"
                                               if self.stderr_tail else ""))
            # Never leave a caller hanging on a dead process.
            for slot in list(self._pending.values()):
                slot["error"] = self.exit_reason
                slot["ev"].set()
            # Same for permission waits: the process that asked is gone, so
            # release them rather than making the owner wait out the full
            # PERMISSION_TIMEOUT on a corpse.
            for key, slot in list(self._perm_answers.items()):
                slot["ev"].set()
                self.on_event("permission_expired",
                              {"requestId": key, "reason": "agent exited"})
            self.on_event("agent_exit", {"reason": self.exit_reason,
                                         "closed": self._closed, "rc": rc})

    def _dispatch(self, msg):
        # Any parsed line -- a response, a streamed update, an agent-initiated
        # request -- proves the process is alive and working. request()'s
        # silence budget resets on this, not on wall-clock since the request
        # was sent.
        self._last_activity = time.monotonic()
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

    def _on_request(self, msg):
        method, rid, params = msg["method"], msg["id"], msg.get("params") or {}
        if method == "session/request_permission":
            # Blocks a real process. Hand it up; answer when the human decides.
            #
            # The WAIT must not run here. _on_request executes on the one
            # _read_stdout thread, and parking it in ev.wait() meant that while
            # a permission sat unanswered, nothing else the agent said was
            # read: updates queued invisibly, a second permission request
            # could not even reach the rail, a cancel's output backed up the
            # pipe until the agent wedged — and the reader could not even see
            # the process die (Gemini adversarial review 2026-08-31, finding
            # 1; reproduced in test_acp_reader.py before this fix). The card
            # is still handed up synchronously so transcript order holds; only
            # the wait-and-reply moves to its own thread.
            key = f"{rid}"
            # The bound is on THREADS, so it has to be checked against the
            # thing that creates them — and a duplicate key overwrote its
            # predecessor's entry without growing the dict, so `len()` stayed
            # put while waiters accumulated: 60 requests all sent as id 1
            # produced 61 live threads against a bound of 32, and the
            # overwritten Events became unreachable, so neither Craig's click
            # nor the exit path could ever release them. Refuse a duplicate
            # that is still live instead (panel 2026-08-31, gemini finding 3 /
            # gpt finding 4; reproduced).
            too_many = len(self._perm_answers) >= MAX_PENDING_PERMISSIONS
            duplicate = key in self._perm_answers
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
            # `**params` LAST is deliberate. Built the other way round, an
            # agent-supplied `params.requestId` overwrote the wire id the
            # waiter is keyed on: the card reached the rail under an id that
            # answer_permission() could not find, so Craig's click returned
            # False and the agent stayed blocked forever — and an id naming
            # some OTHER pending card is consent bound to bytes the human
            # never saw (P17). Corral's id wins (panel 2026-08-31, gpt finding
            # 1; reproduced).
            self.on_permission({**params, "requestId": key})
            threading.Thread(target=self._await_permission,
                             args=(key, rid, params, ev), daemon=True,
                             name=f"acp-perm-{key}").start()
            return
        # We advertise no fs/terminal capabilities, so anything else is refused
        # explicitly rather than left to hang.
        self._write({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"unsupported: {method}"}})

    def _await_permission(self, key, rid, params, ev):
        """Wait out one permission request OFF the reader thread; reply.

        The wait has no clock (PERMISSION_TIMEOUT is None): a card stays
        answerable until Craig answers it or ends the pane. It is released by
        exactly two things — his answer, and the agent process dying, which
        _read_stdout's exit path signals. Fail-closed is intact because we
        never synthesize an answer at all, in either direction.
        """
        ev.wait(PERMISSION_TIMEOUT)
        with self._permlock:
            # Pop only OUR OWN slot, identified by the Event we are waiting
            # on. A plain `pop(key)` removes whatever is under that id, and
            # after a reuse that can be the NEXT request's slot: answering the
            # new card would then find nothing to wake and hang exactly the
            # way the reuse itself used to. Refusing duplicates (in
            # `_on_request`) narrows the window but does not close it — this
            # waiter is released by `answer_permission` setting the Event
            # under the lock, and a fresh request can legitimately claim the
            # freed id before this thread gets scheduled.
            #
            # From corral-light, which had this and full Corral did not; the
            # 2026-09-09 merge that made this the one copy is what surfaced
            # it, via Light's own `test_the_old_waiter_does_not_pop_the_new_slot`.
            cur = self._perm_answers.get(key)
            slot = cur if cur is not None and cur.get("ev") is ev else None
            if slot is not None:
                self._perm_answers.pop(key, None)
        option = (slot or {}).get("option", _UNANSWERED)
        # `is _UNANSWERED`, not `not option`: a vendor is entitled to an option
        # id that is falsy ("" or 0), and treating that as "no selection" made
        # Craig's click vanish — the card left the rail, nothing was ever
        # written back, and the agent blocked until the pane was killed
        # (panel 2026-08-31, grok finding 8).
        if not self.alive or option is _UNANSWERED:
            # Either the process is gone (the reader's exit path already told
            # the owner "agent exited"), or we were woken without a selection.
            # Nothing to reply to, and nothing to invent.
            return
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "result": {
                "outcome": {"outcome": "selected", "optionId": option}}})
        except AgentError:
            # Died between the wake and the write; the reader's exit path
            # owns the messaging from here.
            pass

    def answer_permission(self, request_id, option_id):
        """Claim a pending card. FIRST answer wins, exactly once.

        This used to be a bare assignment, so two clicks that arrived close
        together both returned True and the last one won — a deny could be
        overwritten by an allow, and the human who pressed deny was told it
        landed (panel 2026-08-31, gpt finding 2; reproduced). Claiming under
        the lock makes the second caller get False, which the rail already
        renders as "already answered".
        """
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
        """Set a session config option (model / effort / mode / fast).

        The option ids, their allowed values AND the human labels all come from
        session/new's `configOptions` -- Corral never hardcodes a model list.
        A hardcoded list goes stale the day Anthropic ships a model, and then
        the picker offers something the agent will reject."""
        return self.request("session/set_config_option",
                            {"sessionId": session_id, "configId": config_id,
                             "value": value}, timeout=60)

    def load_session(self, session_id, cwd, mcp_servers=None):
        """Re-attach a FRESH agent process to an existing conversation.

        Verified 2026-08-01: session/load on a brand-new adapter process picks
        up a session created by an earlier one, history intact. The agent
        REPLAYS that history as session/update notifications, so the caller
        must suppress them -- we already hold the transcript on disk, and
        re-emitting would double every message.
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
        # Kill the GROUP, not just the adapter. We start these with
        # start_new_session=True, which makes the adapter a process-group
        # leader — so signalling only the parent left the node adapter's own
        # children (the actual model process, its shells) orphaned and
        # running. Over a day of opening and closing panes that is a leak the
        # operator never sees.
        #
        # And wait for the GROUP, not just the adapter. Waiting on the parent
        # alone returned the moment the adapter honoured SIGTERM — which it
        # does promptly — so a child that ignored the signal outlived the
        # close and the SIGKILL fallback never ran. The leak this was written
        # to fix could still happen, just more quietly.
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
        # Bounded on purpose: close() runs inside an HTTP handler, so the
        # teardown budget is the operator's patience, not the child's.
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
            # SIGKILL is delivered, not applied, the instant killpg returns.
            # Checking straight after says "still there" for a group that is
            # already dying, which would make close() report a leak it just
            # cleaned up.
            gone = time.time() + 2
            while time.time() < gone and _group_alive(pgid):
                time.sleep(0.05)
        try:
            self.p.wait(timeout=2)              # reap, so it is not a zombie
        except Exception:
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

