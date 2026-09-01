#!/usr/bin/python3
"""ssh_acp.py -- a remote host as a Corral Light SHELL pane (ACP over stdio).

Ported from ranch-server's `corral/ssh_acp.py` (cvp1/corral @ 2026-08-31) on
2026-09-01. The heavy build generates one of these per lightsail box from the
estate inventory; Light has no estate, so its hosts come from a hand-written
`ssh-hosts.json` instead (sessions._live_ssh_hosts). The adapter itself is
unchanged in every part that carries a guarantee -- it is the same wire
contract, the same bounds, and the same consent model, because those were
earned by running and not by design.

One persistent `ssh ... bash` per pane. Every line Craig types runs on the host
as his own ssh identity; stdout+stderr stream back. No LLM anywhere in the
chain.

Consent model: there is no permission rail here ON PURPOSE -- the human types
the exact command that runs (PRINCIPLES 17: the artifact approved IS the bytes
executed). This lane must therefore never be handed to an agent as a tool; it
exists only behind Corral's paired-browser auth, driven by the human keyboard,
exactly like a terminal window.

The chain, and where each guarantee lives:

    corral pane -> this adapter (dogma-2) -> ssh -T user@host bash
                -> the host's own account permissions

  * AUTH: Craig's ssh key + the host's own account -- nothing new. Corral
    Light's own auth is already "possession of his UNIX login" (auth.py), so
    this lane grants a paired browser exactly the reach the pairing already
    proved, and no more.
  * BOUNDS (P8): output capped per command (MAX_OUTPUT_BYTES, then the
    shell is killed and restarted clean), wall clock capped per command
    (CMD_TIMEOUT, same recovery), every read line capped (MAX_LINE).
  * NON-INTERACTIVE ONLY: a command that reads stdin (cat, vim) or runs
    forever (tail -f) eats the completion sentinel and hits the timeout;
    the shell restarts and says so. That is the designed degrade, not a
    bug -- this is a command runner, not a pty. It is also what keeps a
    password prompt from ever reaching the transcript: nothing here can
    answer one, so nothing here can record one.

Wire contract: initialize, session/new, session/load, session/list,
session/prompt, session/cancel. An unreachable host refuses the pane UP FRONT
with the real reason (the gemini-lane lesson).

Test hook: SSH_ACP_CONNECT overrides the whole connect command (shlex split),
so a local `bash --noprofile --norc` exercises this entire file with no host
and no ssh.
"""
import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# Light keeps its state under its own root, never the full Corral's and never
# the CC workspace's (see sessions.STATE, and the structural test that forbids
# a `Github/CC/` path anywhere in this tree). Honours CORRAL_LIGHT_STATE so a
# test run does not scribble in the real one.
_LIGHT_STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                                   str(Path.home() / ".local/share/corral-light")))
STATE_ROOT = Path(os.environ.get("SSH_ACP_STATE", str(_LIGHT_STATE / "ssh-acp")))
MAX_LINE = 1 * 1024 * 1024          # one read line; longer is a broken stream
MAX_OUTPUT_BYTES = 200 * 1024       # per command; then kill + restart clean
CONNECT_TIMEOUT = 12                # ssh + first pong
CMD_TIMEOUT = int(os.environ.get("SSH_ACP_CMD_TIMEOUT", "120"))
FLUSH_INTERVAL = 0.3                # seconds between streamed chunks
SENTINEL = "__CC_SSH_ACP_DONE__"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ShellError(Exception):
    pass


class Shell:
    """The persistent remote bash and its sentinel-framed command protocol."""

    def __init__(self, connect_argv):
        self.connect_argv = connect_argv
        self.proc = None
        self.lines = None            # queue.Queue of stdout lines
        self.lock = threading.Lock()  # one in-flight command at a time
        self.cancelled = False

    def _start(self):
        self.proc = subprocess.Popen(
            self.connect_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._reader, args=(self.proc, self.lines),
                         daemon=True).start()
        # stderr into the same stream so errors land in the pane; guard the
        # write -- an instantly-dead ssh raises BrokenPipeError here.
        try:
            self.proc.stdin.write("exec 2>&1\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ShellError(f"connection died at start: {e}")

    @staticmethod
    def _reader(proc, out_queue):
        try:
            while True:
                try:
                    line = proc.stdout.readline(MAX_LINE)
                except (OSError, ValueError):
                    break            # intentional pipe closure during kill()
                if not line:
                    break
                out_queue.put(line)
        finally:
            out_queue.put(None)      # EOF marker

    def kill(self):
        # Close the pipes and REAP, not just signal. The ported version set
        # `self.proc = None` on a live Popen, which drops the last reference to
        # three open file objects and leaves a zombie behind; CPython closes
        # them eventually with a ResourceWarning, but "eventually" in a daemon
        # that restarts this shell on every overflow, timeout and typed `exit`
        # is a descriptor leak the operator never sees. Surfaced by the suite's
        # own warnings, 2026-09-01 — worth fixing here and upstream on ranch.
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 — teardown must not raise into a pane
            pass

    def run(self, cmd, timeout, on_output):
        """Run one command; stream output via on_output(text). Returns the
        exit status string, raising ShellError on death/timeout/overflow
        (the shell is killed first, so the NEXT command reconnects clean)."""
        with self.lock:
            self.cancelled = False
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            nonce = uuid.uuid4().hex[:12]
            done_mark = f"{SENTINEL} {nonce} "
            try:
                self.proc.stdin.write(
                    cmd + "\n" + f"printf '%s\\n' \"{SENTINEL} {nonce} $?\"\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.kill()
                raise ShellError(f"connection lost: {e}")

            deadline = time.monotonic() + timeout
            emitted = 0
            buf, last_flush = [], time.monotonic()

            def flush():
                nonlocal buf, last_flush
                if buf:
                    on_output("".join(buf))
                    buf, last_flush = [], time.monotonic()

            while True:
                if self.cancelled:
                    self.kill()
                    flush()
                    raise ShellError("cancelled — shell restarts on the next command")
                try:
                    line = self.lines.get(timeout=0.25)
                except queue.Empty:
                    if time.monotonic() - last_flush >= FLUSH_INTERVAL:
                        flush()
                    if time.monotonic() > deadline:
                        self.kill()
                        flush()
                        raise ShellError(
                            f"no completion within {timeout}s — command killed, "
                            "shell restarts on the next command")
                    continue
                if line is None:
                    self.kill()
                    flush()
                    raise ShellError("shell exited (connection closed)")
                idx = line.find(done_mark)
                if idx >= 0:
                    if idx:      # partial line before an un-newlined sentinel
                        buf.append(line[:idx])
                    flush()
                    return line[idx + len(done_mark):].strip()
                emitted += len(line)
                if emitted > MAX_OUTPUT_BYTES:
                    self.kill()
                    flush()
                    raise ShellError(
                        f"output exceeded {MAX_OUTPUT_BYTES // 1024} KB — "
                        "command killed, shell restarts on the next command")
                buf.append(line)
                if time.monotonic() - last_flush >= FLUSH_INTERVAL:
                    flush()


class Server:
    def __init__(self, name, shell, where):
        self.name = name
        self.shell = shell
        self.where = where           # "user@ip" or the override command
        self.stdin = sys.stdin
        self.stdout = sys.stdout
        self._wlock = threading.Lock()
        self.sessions = {}
        self.state_dir = STATE_ROOT / name
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── wire ──────────────────────────────────────────────────────────────
    def _send(self, obj):
        with self._wlock:
            self.stdout.write(json.dumps(obj) + "\n")
            self.stdout.flush()

    def _respond(self, rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self._send(msg)

    def _chunk(self, sid, text):
        self._send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text}}}})

    # ── session state ─────────────────────────────────────────────────────
    def _spath(self, sid):
        return self.state_dir / f"{sid}.json"

    def _persist(self, sess):
        self._spath(sess["sessionId"]).write_text(json.dumps(
            {"sessionId": sess["sessionId"], "title": sess["title"],
             "updatedAt": _now()}))

    def _new_session(self, sid, title=""):
        sess = {"sessionId": sid, "title": title}
        self.sessions[sid] = sess
        return sess

    def _ping(self):
        """Reach the host NOW or refuse with the real reason."""
        # Keep whatever ssh said (stderr is merged into the stream): on a
        # failed ping THAT is the real reason, and reporting only the bound
        # that tripped ("no completion within 12s") sends the reader hunting
        # a network problem when ssh already named an auth one.
        seen = []
        try:
            status = self.shell.run(
                "true", CONNECT_TIMEOUT, lambda text: seen.append(text))
        except ShellError as e:
            why = " ".join("".join(seen).split())[-300:]
            raise ValueError(f"{self.name} unreachable over ssh: {e}"
                             + (f" — ssh said: {why}" if why else ""))
        if status != "0":
            raise ValueError(f"{self.name}: shell answered abnormally ({status})")

    # ── handlers ──────────────────────────────────────────────────────────
    def _h_initialize(self, params):
        return {"protocolVersion": 1, "authMethods": [],
                "agentCapabilities": {"loadSession": True},
                "serverInfo": {"name": f"ssh-acp:{self.name}",
                               "version": "0.1"}}

    def _h_new(self, params):
        self._ping()
        sid = "s-%s" % uuid.uuid4().hex[:12]
        sess = self._new_session(sid)
        self._persist(sess)
        self._chunk(sid, f"[shell on {self.name} ({self.where}) — every line "
                         f"you type runs on the host; non-interactive commands "
                         f"only, {CMD_TIMEOUT}s / {MAX_OUTPUT_BYTES // 1024} KB "
                         f"per command]")
        return {"sessionId": sid}

    def _h_load(self, params):
        sid = params.get("sessionId")
        if sid not in self.sessions:
            path = self._spath(sid)
            if not path.is_file():
                raise ValueError(f"unknown session {sid!r}")
            data = json.loads(path.read_text())
            self._new_session(sid, title=data.get("title", ""))
        self._ping()
        return {}

    def _h_list(self, params):
        out = []
        for f in sorted(self.state_dir.glob("s-*.json")):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            out.append({"sessionId": d.get("sessionId"),
                        "title": d.get("title", ""),
                        "updatedAt": d.get("updatedAt")})
        return {"sessions": out[:100]}

    def _h_prompt(self, rid, params):
        sess = self.sessions.get(params.get("sessionId"))
        if sess is None:
            return self._respond(rid, error={"code": -32602,
                                             "message": "unknown session"})
        sid = sess["sessionId"]
        cmd = "\n".join(b.get("text", "") for b in params.get("prompt") or []
                        if b.get("type") == "text").strip()
        if not sess["title"]:
            sess["title"] = cmd[:60]
            self._persist(sess)
        if not cmd:
            self._chunk(sid, "[empty command]")
            return self._respond(rid, {"stopReason": "end_turn"})
        try:
            status = self.shell.run(cmd, CMD_TIMEOUT,
                                    lambda text: self._chunk(sid, text))
        except ShellError as e:
            self._chunk(sid, f"[{e}]")
            return self._respond(rid, {"stopReason": "refusal"})
        if status != "0":
            self._chunk(sid, f"[exit {status}]")
        self._respond(rid, {"stopReason": "end_turn"})

    # ── dispatch ──────────────────────────────────────────────────────────
    def _dispatch(self, msg):
        rid, method = msg.get("id"), msg.get("method")
        params = msg.get("params") or {}
        if method == "session/cancel":
            self.shell.cancelled = True
            return
        if rid is None:
            return
        if method == "session/prompt":
            threading.Thread(target=self._h_prompt, args=(rid, params),
                             daemon=True).start()
            return
        handlers = {"initialize": self._h_initialize,
                    "session/new": self._h_new,
                    "session/load": self._h_load,
                    "session/list": self._h_list}
        h = handlers.get(method)
        if h is None:
            return self._respond(rid, error={"code": -32601,
                                             "message": f"unsupported: {method}"})
        try:
            self._respond(rid, h(params))
        except Exception as e:  # noqa: BLE001 -- a bad request must not kill the server
            self._respond(rid, error={"code": -32000, "message": str(e)})

    def serve(self):
        for line in self.stdin:
            line = line.strip()
            if not line or len(line) > MAX_LINE:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._dispatch(msg)
        self.shell.kill()


def connect_argv(args):
    override = os.environ.get("SSH_ACP_CONNECT", "").strip()
    if override:
        return shlex.split(override), override
    # Light's hosts are hand-written, and the common case on a personal fleet is
    # a `~/.ssh/config` alias carrying the user, port and key already. So --key
    # is OPTIONAL here (ranch's estate always had one to pass): with no key we
    # hand ssh the bare target and let its own config answer, and we do NOT set
    # IdentitiesOnly, which would suppress exactly the config-supplied identity
    # we are deferring to.
    if not args.ip:
        raise SystemExit("ssh_acp: --ip (host or user@host) is required "
                         "(or SSH_ACP_CONNECT for a local test)")
    target = f"{args.user}@{args.ip}" if args.user else args.ip
    argv = ["ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT - 2}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4"]
    if args.key:
        # IdentitiesOnly: the desktop keyring agent's keys are offered
        # BEFORE -i, and sshd's MaxAuthTries (6) disconnects before the
        # right key is ever tried. Same lesson as wan_ip_guard /
        # fleet_verify / delegate_acp (2026-08-24); this lane predates it.
        argv += ["-i", args.key, "-o", "IdentitiesOnly=yes"]
    argv += [target, "bash", "--noprofile", "--norc"]
    return argv, target


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="host name (lane label + state dir)")
    ap.add_argument("--ip", default="", help="hostname, IP, or a ~/.ssh/config alias")
    ap.add_argument("--key", default="")
    ap.add_argument("--user", default="")
    args = ap.parse_args()
    argv, where = connect_argv(args)
    Server(args.name, Shell(argv), where).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
