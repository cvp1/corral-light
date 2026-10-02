"""worktrees — per-pane git worktrees: the only module in Corral Light that runs git.

A pane can start on its own worktree and branch, cut from the repository the
user picked; the user reviews what the agent changed as a diff and commits,
publishes or discards it. Plan: docs/worktree-review-plan.md (v3.2).

A worktree is a separate checkout, NOT a sandbox: an agent with shell access
can still write anywhere the user can.

The rule above all others: no function here makes work unrecoverable. Every
destructive step first writes a recovery ref or moves files into a trash
folder under the worktree root; real deletion is a separate, typed purge.

WHAT THIS MODULE NEVER DOES
- runs git through a shell, or lets git prompt, page or open an editor;
- lets GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE / injected config from the
  hub's environment steer a call to another repository;
- holds output in memory past a byte cap;
- leaves a timed-out git (or anything it spawned) running;
- removes an index.lock it cannot prove a child of ours left behind.

Stdlib only.
"""
import os
import signal
import subprocess
import threading
from collections import namedtuple

# Overridable for tests (a stub binary) and for the git 2.38 release check.
GIT_BIN = os.environ.get("CORRAL_TEST_GIT") or "git"

DEFAULT_TIMEOUT_S = 20
DEFAULT_MAX_OUT = 8 << 20          # 8 MiB per stream
ERR_SNIPPET = 400                  # bytes of stderr kept on a GitError
KILL_GRACE_S = 2                   # TERM, then this long, then KILL

# Variables that route git to another repo, index, object store or config,
# or swap in external programs. Removed from every call's environment.
ROUTING_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_EXEC_PATH", "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS",
    "GIT_OPTIONAL_LOCKS",
)
ROUTING_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

# Set on every call: no prompt, pager, editor or askpass helper can run.
NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GIT_EDITOR": "true",
    "GIT_ASKPASS": "",             # empty masks core.askPass and SSH_ASKPASS
    "SSH_ASKPASS": "",
    "LC_ALL": "C",
    "GH_PROMPT_DISABLED": "1",
}


class GitError(Exception):
    """git exited non-zero (or produced output we cannot read as a result)."""

    def __init__(self, cmd, rc, err):
        self.cmd = list(cmd)
        self.rc = rc
        self.err = (err or "")[:ERR_SNIPPET]
        super().__init__(f"git {' '.join(self.cmd[1:3])} failed (rc {rc}): {self.err.strip()}")


class GitTimeout(GitError):
    """git ran past its timeout; it and its whole process group were killed."""

    def __init__(self, cmd, timeout):
        super().__init__(cmd, None, f"timed out after {timeout}s")
        self.timeout = timeout


class GitResult(namedtuple("GitResult", "rc out err truncated")):
    """rc, stdout bytes, stderr bytes, and whether either stream hit its cap."""

    @property
    def text(self):
        return self.out.decode("utf-8", "replace")

    @property
    def err_text(self):
        return self.err.decode("utf-8", "replace")


def git_env(env_extra=None, optional_locks_off=False):
    """The environment one git call runs under.

    `optional_locks_off` sets GIT_OPTIONAL_LOCKS=0, for read-only probe and
    summary calls only — never for a cleanliness check that guards an action.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ROUTING_ENV and not k.startswith(ROUTING_PREFIXES)}
    env.update(NO_PROMPT_ENV)
    if optional_locks_off:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    if env_extra:
        env.update(env_extra)
    return env


def _drain(stream, cap, sink):
    """Read `stream` to EOF, keeping at most `cap` bytes; sink = [bytes, truncated]."""
    buf = bytearray()
    truncated = False
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        room = cap - len(buf)
        if room > 0:
            buf += chunk[:room]
        if len(chunk) > max(room, 0):
            truncated = True            # keep reading so the child never blocks
    stream.close()
    sink[:] = [bytes(buf), truncated]


def _kill_group(proc):
    """TERM the child's process group, wait, then KILL it."""
    for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, KILL_GRACE_S)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=wait)
            if sig == signal.SIGKILL or _group_gone(proc.pid):
                return
        except subprocess.TimeoutExpired:
            continue
    # One more KILL for stragglers that outlived the leader.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _group_gone(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def git(args, cwd, timeout=DEFAULT_TIMEOUT_S, check=True, max_out=DEFAULT_MAX_OUT,
        env_extra=None, input=None, optional_locks_off=False):
    """Run `git <args>` in `cwd`; return a GitResult. Never through a shell.

    `input` (bytes) is written to git's stdin and the pipe closed; otherwise
    stdin is /dev/null. stdout and stderr are each capped at `max_out` bytes
    while streaming. On timeout the whole process group is killed and
    GitTimeout raised. With check=True a non-zero rc raises GitError.
    """
    cmd = [GIT_BIN, *args]
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=git_env(env_extra, optional_locks_off),
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    out, err = [], []
    readers = [threading.Thread(target=_drain, args=(proc.stdout, max_out, out), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, max_out, err), daemon=True)]
    for t in readers:
        t.start()
    if input is not None:
        try:
            proc.stdin.write(input)
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        for t in readers:
            t.join(KILL_GRACE_S)
        raise GitTimeout(cmd, timeout) from None
    for t in readers:
        t.join()
    res = GitResult(rc, out[0], err[0], out[1] or err[1])
    if check and rc != 0:
        raise GitError(cmd, rc, res.err_text)
    return res


def parse_merge_tree(rc, out):
    """Read `git merge-tree --write-tree -z --name-only` output.

    rc 0 → clean, `tree` is the merged tree. rc 1 → conflicts: `tree` is None
    (the conflicted tree git printed is never a result), `conflicts` lists the
    paths, `messages` the informational records. Any other rc is an error.
    Layout frozen from testkit/fixtures/merge-tree (identical on 2.38 and 2.55).
    """
    if rc not in (0, 1):
        raise GitError(["git", "merge-tree"], rc, out.decode("utf-8", "replace"))
    fields = out.split(b"\0")
    tree = fields[0].decode("ascii", "replace")
    i = 1
    conflicts = []
    if rc == 1:
        while i < len(fields) and fields[i] != b"":
            conflicts.append(fields[i].decode("utf-8", "surrogateescape"))
            i += 1
        i += 1                          # the empty field that closes the section
    messages = []
    while i < len(fields) and fields[i] != b"":
        try:
            n = int(fields[i])
        except ValueError:
            raise GitError(["git", "merge-tree"], rc, "unreadable merge-tree record") from None
        paths = [f.decode("utf-8", "surrogateescape") for f in fields[i + 1:i + 1 + n]]
        kind = fields[i + 1 + n].decode("utf-8", "replace") if i + 1 + n < len(fields) else ""
        msg = fields[i + 2 + n].decode("utf-8", "replace") if i + 2 + n < len(fields) else ""
        messages.append({"paths": paths, "type": kind, "message": msg})
        i += 3 + n
    return {"clean": rc == 0, "tree": tree if rc == 0 else None,
            "conflicts": conflicts, "messages": messages}


def lock_is_ours_and_stale(lock_path, record):
    """True only if `record` proves a child we spawned left `lock_path` and is gone.

    `record` is {"pid", "start", "lock"} written when the hub spawned the
    child. A live pid — even one whose start token differs, i.e. a reused
    pid that may itself hold the lock — is never ours to clear. Doubt is False.
    """
    if not record or os.path.realpath(record.get("lock") or "") != os.path.realpath(lock_path):
        return False
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if pid <= 1 or not record.get("start"):
        return False
    return not _pid_running(pid)


def _pid_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                   # an unreaped zombie no longer writes
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return True
