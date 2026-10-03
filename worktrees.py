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
import base64
import contextlib
import hashlib
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import namedtuple
from pathlib import Path

# Overridable for tests (a stub binary) and for the git 2.38 release check.
# Resolved once, at start, on the hub's own PATH: a host can have two gits
# (dogma-2: Homebrew 2.55 ahead of Apple's 2.54), and doctor says which ran.
GIT_BIN = os.environ.get("CORRAL_TEST_GIT") or shutil.which("git") or "git"

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
    return _run([GIT_BIN, *args], cwd, timeout, check, max_out, env_extra, input,
                optional_locks_off)


def _iso_log(cwd):
    """T-ISO-1's spy: record where each call runs. Inert outside the test suite."""
    log = os.environ.get("CORRAL_WT_ISO_LOG")
    if log and os.environ.get("CORRAL_WT_TEST") == "1":
        with open(log, "a", encoding="utf-8") as f:
            f.write(os.path.realpath(str(cwd)) + "\n")


def _feed(stdin, data):
    try:
        stdin.write(data)
    except (BrokenPipeError, ValueError, OSError):
        pass
    finally:
        with contextlib.suppress(BrokenPipeError, ValueError, OSError):
            stdin.close()


def _run(cmd, cwd, timeout=DEFAULT_TIMEOUT_S, check=True, max_out=DEFAULT_MAX_OUT,
         env_extra=None, input=None, optional_locks_off=False):
    """The process runner behind git() (and gh): see git()."""
    _iso_log(cwd)
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=git_env(env_extra, optional_locks_off),
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    out, err = [], []
    readers = [threading.Thread(target=_drain, args=(proc.stdout, max_out, out), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, max_out, err), daemon=True)]
    if input is not None:
        # In a thread: a child that never reads must not stall us past the deadline.
        readers.append(threading.Thread(target=_feed, args=(proc.stdin, input), daemon=True))
    for t in readers:
        t.start()
    # One deadline for everything: a child that exits while a grandchild
    # (a hook's helper) keeps the pipes open must not outlive the timeout.
    deadline = time.monotonic() + timeout
    try:
        rc = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        for t in readers:
            t.join(max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in readers):
            raise subprocess.TimeoutExpired(cmd, timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        for t in readers:
            t.join(KILL_GRACE_S)
        raise GitTimeout(cmd, timeout) from None
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


# ── locations (D2) ────────────────────────────────────────────────────────────

def state_dir():
    """The hub state dir; read per call so tests and the CLI can point it elsewhere."""
    return Path(os.environ.get("CORRAL_LIGHT_STATE")
                or Path.home() / ".local" / "share" / "corral-light")


def worktree_root():
    """Where worktrees live: $CORRAL_LIGHT_WORKTREES, else <state>/worktrees.

    Holds only <repo>-<hash6>/ dirs and .trash/. The registry is NOT in here.
    """
    return Path(os.environ.get("CORRAL_LIGHT_WORKTREES") or state_dir() / "worktrees")


def registry_dir():
    """Beside the root, never inside it (reconcile would list it as an orphan)."""
    return state_dir() / "worktree-registry"


# ── the registry (D13) ────────────────────────────────────────────────────────

REGISTRY_V = 1
PHASES = ("intent", "active", "trashed", "purged", "missing", "tampered")
OP_STATES = ("intent", "done", "unknown")
_ID_RE = re.compile(r"^wt-[0-9a-f]{6,16}$")


class RegistryVersionError(Exception):
    """An entry written by a newer hub: read-only, never rewritten."""


def _fsync_dir(d):
    fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path, obj):
    """Write `obj` to `path` so a crash leaves the old file or the new one, never a part."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        _unlink_own_temp(tmp)              # our own temp file, never anything else
        raise
    _fsync_dir(path.parent)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Registry:
    """One JSON file per worktree under registry_dir(), written before git runs.

    Every write happens under an fcntl lock on the directory, so the hub and
    the `corral-light worktrees` CLI never interleave. An entry whose `v` is
    newer than REGISTRY_V is refused read-only.
    """

    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else registry_dir()

    def _ensure(self):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def lock(self):
        self._ensure()
        with open(self.dir / ".lock", "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _path(self, wt_id):
        if not isinstance(wt_id, str) or not _ID_RE.match(wt_id):
            raise ValueError(f"not a worktree id: {wt_id!r}")
        return self.dir / f"{wt_id}.json"

    def _load(self, path):
        entry = json.loads(path.read_text(encoding="utf-8"))
        v = entry.get("v")
        if v != REGISTRY_V:
            raise RegistryVersionError(f"registry v{v} is newer than this hub (v{REGISTRY_V})"
                                       if isinstance(v, int) and v > REGISTRY_V
                                       else f"registry entry has unknown version {v!r}")
        return entry

    def read(self, wt_id):
        return self._load(self._path(wt_id))

    def create(self, **fields):
        """A new entry in phase `intent`; returns it. Call BEFORE `git worktree add`."""
        with self.lock():
            while True:
                wt_id = "wt-" + secrets.token_hex(3)
                if not self._path(wt_id).exists():
                    break
            entry = {"v": REGISTRY_V, "id": wt_id, "phase": "intent", "created": _now(),
                     "last_commit": None, "published": None, "recovery_refs": [], "ops": []}
            entry.update(fields)
            entry["id"], entry["v"] = wt_id, REGISTRY_V
            self._check(entry)
            atomic_write_json(self._path(wt_id), entry)
            return entry

    def _check(self, entry):
        if entry.get("phase") not in PHASES:
            raise ValueError(f"unknown phase {entry.get('phase')!r}")
        for op in entry.get("ops") or []:
            if op.get("state") not in OP_STATES:
                raise ValueError(f"unknown op state {op.get('state')!r}")

    def _mutate(self, wt_id, fn):
        with self.lock():
            path = self._path(wt_id)
            entry = self._load(path)
            fn(entry)
            self._check(entry)
            atomic_write_json(path, entry)
            return entry

    def update(self, wt_id, **fields):
        def fn(e):
            for k in ("id", "v", "ops"):
                if k in fields:
                    raise ValueError(f"{k} cannot be set through update()")
            e.update(fields)
        return self._mutate(wt_id, fn)

    def begin_op(self, wt_id, op, **fields):
        """Journal an op in state `intent` before its git runs; returns the op_id."""
        op_id = "op-" + secrets.token_hex(4)

        def fn(e):
            rec = {"op_id": op_id, "op": op, "state": "intent", "stage": None, "at": _now()}
            rec.update(fields)
            e.setdefault("ops", []).append(rec)
        self._mutate(wt_id, fn)
        return op_id

    def set_op(self, wt_id, op_id, **fields):
        def fn(e):
            for rec in e.get("ops") or []:
                if rec.get("op_id") == op_id:
                    rec.update(fields)
                    rec["at"] = _now()
                    return
            raise KeyError(f"{wt_id} has no op {op_id}")
        return self._mutate(wt_id, fn)

    def finish_op(self, wt_id, op_id, op_fields, **fields):
        """Close op `op_id` with `op_fields` AND update the entry in one write.

        A crash between two writes could leave an op `done` while the entry
        still described the old state (a discarded worktree still `active`,
        so nothing could restore it). One mutation, one rename: both or neither.
        """
        def fn(e):
            for k in ("id", "v", "ops"):
                if k in fields:
                    raise ValueError(f"{k} cannot be set through finish_op()")
            for rec in e.get("ops") or []:
                if rec.get("op_id") == op_id:
                    rec.update(op_fields)
                    rec["at"] = _now()
                    break
            else:
                raise KeyError(f"{wt_id} has no op {op_id}")
            e.update(fields)
        return self._mutate(wt_id, fn)

    def all(self, include_unreadable=False):
        """Every entry, oldest first. Unreadable ones only with include_unreadable."""
        if not self.dir.is_dir():
            return []
        out = []
        for f in sorted(self.dir.glob("wt-*.json")):
            try:
                out.append(self._load(f))
            except (OSError, ValueError, RegistryVersionError) as e:
                if include_unreadable:
                    out.append({"id": f.stem, "unreadable": str(e)})
        out.sort(key=lambda e: (e.get("created") or "", e.get("id")))
        return out


# ── probe (F1) ────────────────────────────────────────────────────────────────

MIN_GIT = (2, 38)
PROBE_TIMEOUT_S = 20
_VERSION = []


def git_version():
    """The git binary's version as a tuple of ints, e.g. (2, 55, 0). Cached."""
    if not _VERSION:
        r = git(["--version"], cwd=os.getcwd() if os.path.isdir(os.getcwd()) else "/",
                timeout=PROBE_TIMEOUT_S)
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", r.text)
        _VERSION.append(tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0))
    return _VERSION[0]


def _existing_ancestor(p):
    p = Path(p).absolute()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _fstype(path):
    """The filesystem type of the mount holding `path` (or its nearest existing ancestor).

    Linux reads /proc/self/mountinfo; elsewhere (macOS) parses `mount`.
    """
    target = os.path.realpath(_existing_ancestor(path))
    best, kind = "", None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                mnt = left.split()[4].replace("\\040", " ")
                if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, kind = mnt, right.split()[0]
    except OSError:
        try:
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return _fstype_from_mount(out, target)
    return kind


def _fstype_from_mount(text, target):
    """BSD/macOS `mount` lines (`<dev> on <dir> (<type>, ...)`): the longest match's type."""
    best, kind = "", None
    for line in text.splitlines():
        m = re.match(r"^.+? on (.+) \(([^,)]+)", line)
        if not m:
            continue
        mnt, fs = m.group(1), m.group(2).strip()
        if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
            best, kind = mnt, fs
    return kind


def _lfs_installed(cwd):
    try:
        return git(["lfs", "version"], cwd=cwd, check=False, timeout=PROBE_TIMEOUT_S).rc == 0
    except OSError:
        return False


def probe(path):
    """Can a pane started in `path` get its own worktree? Writes nothing.

    Returns facts plus `refusals` (reasons, in words, that the checkbox is
    replaced by) and `warnings` (shown beside it).
    """
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    q = dict(cwd=path, check=False, timeout=PROBE_TIMEOUT_S, optional_locks_off=True)
    ver = git_version()
    root = worktree_root()
    out = {"inside": False, "top": None, "repo_top": None, "subdir": "", "common_dir": None,
           "branch": None, "head": None, "detached": False, "unborn": False, "dirty": False,
           "bare": False, "submodules": False, "sparse": False, "lfs_needed": False,
           "lfs_ok": None, "git_version": list(ver), "root": str(root),
           "root_tmpfs": _fstype(root) == "tmpfs", "same_fs_as_root": None,
           "refusals": [], "warnings": []}
    refuse = out["refusals"].append
    if ver[:2] < MIN_GIT:
        refuse(f"git {'.'.join(map(str, ver))} is too old; own branches need git "
               f"{'.'.join(map(str, MIN_GIT))} or newer")
    r = git(["rev-parse", "--is-bare-repository", "--is-inside-git-dir",
             "--git-common-dir", "--absolute-git-dir"], **q)
    if r.rc != 0:
        refuse("this folder is not inside a git repository")
        return out
    bare, in_git_dir, common, _gitdir = r.text.splitlines()[:4]
    common = Path(common) if os.path.isabs(common) else (path / common)
    out["common_dir"] = str(common.resolve())
    if bare == "true" or in_git_dir == "true":
        out["bare"] = True
        refuse("this is a bare repository (or its .git folder); pick a folder in a checkout")
        return out
    r = git(["rev-parse", "--show-toplevel", "--show-prefix"], **q)
    lines = r.text.splitlines() + ["", ""]
    out["inside"] = True
    out["top"] = str(Path(lines[0]).resolve())
    out["subdir"] = lines[1].rstrip("/")
    main = Path(out["common_dir"])
    out["repo_top"] = str(main.parent if main.name == ".git" else Path(out["top"]))
    sym = git(["symbolic-ref", "-q", "HEAD"], **q)
    if sym.rc != 0:
        out["detached"] = True
        refuse("HEAD is detached; check out a branch first, so there is a base to merge back to")
    else:
        out["branch"] = sym.text.strip()
    head = git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], **q)
    if head.rc == 0:
        out["head"] = head.text.strip()
    elif not out["detached"]:
        out["unborn"] = True
        refuse("this repository has no commits yet; make a first commit, then try again")
    st = git(["status", "--porcelain=v1", "-z", "--untracked-files=normal",
              "--ignore-submodules=none"], **q)
    out["dirty"] = bool(st.out.strip(b"\0"))
    if out["dirty"]:
        out["warnings"].append(f"your uncommitted changes in {out['top']} stay there; "
                               "the new branch starts from the last commit")
    top = out["top"]
    ls = git(["ls-files", "-s", "-z"], cwd=top, check=False, timeout=PROBE_TIMEOUT_S,
             optional_locks_off=True, max_out=64 << 20)
    if (Path(top) / ".gitmodules").exists() or any(
            rec.startswith(b"160000 ") for rec in ls.out.split(b"\0")):
        out["submodules"] = True
        refuse("this repository has submodules; own branches do not support them yet")
    sp = git(["config", "--type=bool", "--get", "core.sparseCheckout"], **q)
    if sp.text.strip() == "true":
        out["sparse"] = True
        refuse("this checkout is sparse; own branches do not support sparse checkouts yet")
    lfs = git(["grep", "-q", "-I", "filter=lfs", "--", ".gitattributes",
               ":(glob)**/.gitattributes"], cwd=top, check=False, timeout=PROBE_TIMEOUT_S,
              optional_locks_off=True)
    if lfs.rc == 0:
        out["lfs_needed"] = True
        out["lfs_ok"] = _lfs_installed(top)
        if not out["lfs_ok"]:
            refuse("this repository uses Git LFS and git-lfs is not installed")
    if git(["show-ref", "--verify", "-q", "refs/heads/corral"], **q).rc == 0:
        refuse("a branch named exactly 'corral' exists; it blocks the corral/<name> "
               "branches own branches use")
    if out["root_tmpfs"]:
        refuse(f"the worktree folder {root} is on tmpfs (memory); point "
               "CORRAL_LIGHT_WORKTREES at a disk")
    try:
        out["same_fs_as_root"] = (os.stat(_existing_ancestor(root)).st_dev
                                  == os.stat(top).st_dev)
    except OSError:
        pass
    return out


# ── names (D3) ────────────────────────────────────────────────────────────────

SLUG_MAX = 40
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
RESERVED_SLUGS = {"head"}
BRANCH_PREFIX = "refs/heads/corral/"


def _true_case(path):
    """On macOS, `path` with each existing component in its on-disk letter case.

    APFS is usually case-insensitive and realpath does not fold case, so
    `.../Ab` opened as `.../aB` would hash as another repository
    (docs/worktree-plan-macos.md M3). An exact match wins (a case-sensitive
    volume may hold both); a component that does not exist is left as given.
    Elsewhere the path is returned unchanged.
    """
    if sys.platform != "darwin":
        return path
    parts = Path(path).parts
    out = Path(parts[0])
    for i, name in enumerate(parts[1:], 1):
        try:
            entries = os.listdir(out)
        except OSError:
            return str(out.joinpath(*parts[i:]))
        if name not in entries:
            folded = [e for e in entries if e.casefold() == name.casefold()]
            name = folded[0] if len(folded) == 1 else name
        out = out / name
    return str(out)


def repo_dir(pr):
    """<root>/<repo name>-<hash6>: one dir per repository, from the common dir's realpath
    (in its on-disk letter case on macOS)."""
    common = _true_case(os.path.realpath(pr["common_dir"]))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(pr["repo_top"]).name).strip("-.") or "repo"
    return worktree_root() / f"{name[:40]}-{hashlib.sha256(common.encode()).hexdigest()[:6]}"


def _slugify(text):
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", folded.lower())).strip("-")


def plan_slug(title, pr, fallback):
    """A free `corral/<slug>` name for a new worktree. Never trusts user text.

    Unique against existing corral/* branches, admin dirs under
    <common_dir>/worktrees/, and paths in this repo's dir under the root.
    The full ref must pass `git check-ref-format`. Raises ValueError if a
    branch named exactly `corral` blocks the namespace.
    """
    common = Path(pr["common_dir"])
    top = pr["top"]
    if git(["show-ref", "--verify", "-q", "refs/heads/corral"], cwd=top, check=False).rc == 0:
        raise ValueError("a branch named exactly 'corral' blocks corral/<name> branches")
    base = _slugify(title)[:SLUG_MAX].strip("-")
    if not base or base in RESERVED_SLUGS:
        base = _slugify(fallback)[:SLUG_MAX].strip("-") or "pane"
    taken = set(git(["for-each-ref", "--format=%(refname)", BRANCH_PREFIX], cwd=top).text.split())
    rdir = repo_dir(pr)
    for n in range(1, 1000):
        suffix = "" if n == 1 else f"-{n}"
        slug = base[:SLUG_MAX - len(suffix)].rstrip("-") + suffix
        if (BRANCH_PREFIX + slug in taken or (common / "worktrees" / slug).exists()
                or os.path.lexists(rdir / slug)):
            continue
        if not _SLUG_RE.match(slug):
            continue
        if git(["check-ref-format", BRANCH_PREFIX + slug], cwd=top, check=False).rc != 0:
            continue
        return slug
    raise ValueError("no free branch name after 999 tries")


# ── create and verify (F2, D13, D14) ──────────────────────────────────────────

ADD_TIMEOUT_S = 300


class Refused(Exception):
    """An action refused for a machine-readable `reason` (a route answers 409 with it).

    Reasons: busy, changed, identity, missing, tampered, signing, uncommitted,
    remote_changed, non_ff, unknown.
    """

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class IdentityError(Refused):
    """A worktree is not what the registry says. `reason`: missing | tampered | identity."""


def repo_lock_key(pr):
    """dev:inode of the common dir, so two spellings of one repo share a lock."""
    st = os.stat(pr["common_dir"])
    return f"{st.st_dev}-{st.st_ino}"


@contextlib.contextmanager
def repo_lock(pr):
    """Held for every mutating function on one repository, across threads and processes."""
    d = registry_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(d / f".repo-{repo_lock_key(pr)}.lock", "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _refuse_symlink(p, what):
    if os.path.islink(p):
        raise ValueError(f"the {what} {p} is a symlink; refusing (point "
                         "CORRAL_LIGHT_WORKTREES at a real folder)")


def refuse_open_ops(entry, registry):
    """Refuse a mutating action while any op on this worktree is not settled.

    `unknown`: a past action's outcome could not be checked; only the CLI
    resolves it. `intent`: an action is running in another process, or one
    was cut short and the restart has not checked it yet. Reads the entry
    as it is now, not the caller's copy.
    """
    try:
        cur = registry.read(entry["id"])
    except (OSError, ValueError, RegistryVersionError) as e:
        raise Refused("missing", f"this branch's record cannot be read: {e}") from None
    for o in cur.get("ops") or []:
        if o.get("state") == "unknown":
            raise Refused("unknown", "an action on this branch has an unknown outcome; "
                                     "resolve it with `corral-light worktrees`")
        if o.get("state") == "intent":
            raise Refused("unknown", f"an earlier {o.get('op') or 'action'} on this branch has "
                                     "not finished; restart the hub to check it, or resolve "
                                     "it with `corral-light worktrees`")
    return cur


def agent_cwd(entry):
    """The folder the agent starts in: the worktree plus the subdir the user chose."""
    return Path(entry["path"]) / (entry.get("subdir") or "")


def check_agent_cwd(entry):
    """Refuse if the agent's folder resolves outside its worktree.

    A subdir that is a committed symlink (replaced by a real folder only in
    the main checkout) passes probe, and the new checkout restores the link.
    Checked before every start, resume and dispatch, on the live disk."""
    root = os.path.realpath(entry["path"])
    real = os.path.realpath(agent_cwd(entry))
    if real != root and not real.startswith(root + os.sep):
        raise Refused("identity", f"this pane's folder {agent_cwd(entry)} resolves to {real}, "
                                  "outside its own branch; the agent was not started")
    return real


def create(pr, title, owner_pane, registry=None):
    """Register intent, then `git worktree add -b corral/<slug>` under the root.

    Never: reuses a path, creates outside the root, or runs where probe
    refused. A failure after the intent leaves the entry in phase `missing`
    with the error, never silently gone.
    """
    if pr.get("refusals"):
        raise ValueError(pr["refusals"][0])
    if not pr.get("inside") or not pr.get("head") or not pr.get("branch"):
        raise ValueError("probe did not find a branch with commits here")
    registry = registry or Registry()
    root = worktree_root()
    _refuse_symlink(root, "worktree root")
    with repo_lock(pr):
        rdir = repo_dir(pr)
        _refuse_symlink(rdir, "repository folder")
        slug = plan_slug(title, pr, fallback=owner_pane)
        path = rdir / slug
        if os.path.lexists(path):
            raise ValueError(f"{path} already exists; refusing to reuse it")
        entry = registry.create(
            owner_pane=owner_pane, path=str(path), subdir=pr.get("subdir") or "",
            branch=BRANCH_PREFIX + slug, admin_name=slug, repo_top=pr["repo_top"],
            common_dir=pr["common_dir"], common_dir_id=repo_lock_key(pr),
            base_ref=pr["branch"], base_sha=pr["head"])
        _crash_point("create:intent")
        try:
            rdir.mkdir(parents=True, exist_ok=True, mode=0o700)
            _refuse_symlink(rdir, "repository folder")
            if not os.path.realpath(rdir).startswith(os.path.realpath(root) + os.sep):
                raise ValueError(f"{rdir} resolves outside the worktree root")
            git(["worktree", "add", "-q", "-b", "corral/" + slug, "--", str(path), pr["head"]],
                cwd=pr["top"], timeout=ADD_TIMEOUT_S)
            _crash_point("create:added")
            verify(entry)
        except BaseException as e:
            if isinstance(e, GitError) and not isinstance(e, GitTimeout) and _added_anyway(entry):
                # git exits non-zero when a post-checkout hook fails, AFTER the
                # worktree and branch exist. Marking that `missing` orphaned a
                # live worktree nothing could list or discard.
                return registry.update(entry["id"], phase="active", warning=(
                    "git reported an error after creating the worktree (a failing "
                    f"post-checkout hook?): {str(e)[:ERR_SNIPPET]}"))
            registry.update(entry["id"], phase="missing", error=str(e)[:ERR_SNIPPET])
            raise
        return registry.update(entry["id"], phase="active")


def _added_anyway(entry):
    """Did `git worktree add` register the path and check out our branch despite failing?"""
    try:
        if os.path.realpath(entry["path"]) not in _registered_paths(entry):
            return False
        verify(entry)
        return True
    except Exception:                               # noqa: BLE001 — doubt means no
        return False


def _registered_paths(entry):
    r = git(["worktree", "list", "--porcelain", "-z"], cwd=entry["common_dir"])
    return {os.path.realpath(f[len(b"worktree "):].decode("utf-8", "surrogateescape"))
            for f in r.out.split(b"\0") if f.startswith(b"worktree ")}


def verify(entry):
    """Is the worktree still the one we registered? Returns {"oid"}; raises IdentityError.

    Checks: the path is not a symlink and resolves under the root; its .git
    file points at our admin dir; git lists it for this common dir; its HEAD
    is symbolic and names our branch. Repairs nothing.
    """
    p = entry["path"]
    if os.path.islink(p):
        raise IdentityError("tampered", f"{p} is now a symlink")
    if not os.path.isdir(p):
        raise IdentityError("missing", f"{p} is gone")
    root = os.path.realpath(worktree_root())
    if not os.path.realpath(p).startswith(root + os.sep):
        raise IdentityError("tampered", f"{p} resolves outside {root}")
    want_admin = os.path.realpath(Path(entry["common_dir"]) / "worktrees" / entry["admin_name"])
    try:
        dotgit = Path(p, ".git").read_text(encoding="utf-8").strip()
    except OSError:
        raise IdentityError("tampered", f"{p}/.git is not a worktree link") from None
    if not dotgit.startswith("gitdir: ") or os.path.realpath(
            os.path.join(p, dotgit[len("gitdir: "):])) != want_admin:
        raise IdentityError("tampered", f"{p}/.git points somewhere else")
    if os.path.realpath(p) not in _registered_paths(entry):
        raise IdentityError("missing", f"git no longer lists {p} as a worktree")
    sym = git(["symbolic-ref", "-q", "HEAD"], cwd=p, check=False)
    if sym.rc != 0 or sym.text.strip() != entry["branch"]:
        raise IdentityError("identity", f"{p} is on {sym.text.strip() or 'a detached HEAD'}, "
                                        f"not {entry['branch']}")
    oid = git(["rev-parse", "--verify", "-q", entry["branch"] + "^{commit}"], cwd=p).text.strip()
    return {"oid": oid}


# ── summary (F3) ──────────────────────────────────────────────────────────────

SUMMARY_TIMEOUT_S = 20


def summary(entry):
    """Files changed and lines added/removed vs the base. Writes nothing.

    Tracked: `git diff --numstat <base_sha>` (working tree vs the base commit,
    so agent commits still count). Untracked: `ls-files --others
    --exclude-standard`. Runs with GIT_OPTIONAL_LOCKS=0; never touches any
    index and never writes objects. `digest` changes whenever any changed
    file's content-relevant stat changes, so the rail can tell "new since
    you looked" even when the totals stay the same.
    """
    p = entry["path"]
    q = dict(cwd=p, timeout=SUMMARY_TIMEOUT_S, optional_locks_off=True)
    num = git(["diff", "--numstat", "-z", "--no-renames", "--no-textconv", "--no-ext-diff",
               entry["base_sha"], "--"], **q)
    files, added, deleted, binary, paths = 0, 0, 0, [], []
    for rec in num.out.split(b"\0"):
        if not rec:
            continue
        a, d, path = rec.split(b"\t", 2)
        path = path.decode("utf-8", "surrogateescape")
        files += 1
        paths.append(path)
        if a == b"-":
            binary.append(path)
        else:
            added += int(a)
            deleted += int(d)
    other = git(["ls-files", "--others", "--exclude-standard", "-z"], **q)
    untracked = []
    for rec in other.out.split(b"\0"):
        if not rec:
            continue
        path = rec.decode("utf-8", "surrogateescape")
        try:
            size = os.lstat(os.path.join(p, path)).st_size
        except OSError:
            size = None
        untracked.append({"path": path, "size": size})
        paths.append(path)
    h = hashlib.sha256(num.out + b"\1" + other.out)
    for path in sorted(paths):
        try:
            st = os.lstat(os.path.join(p, path))
            h.update(f"{path}\0{st.st_size}\0{st.st_mtime_ns}\0{st.st_ino}\n".encode(
                "utf-8", "surrogateescape"))
        except OSError:
            h.update(f"{path}\0gone\n".encode("utf-8", "surrogateescape"))
    return {"files": files + len(untracked), "added": added, "deleted": deleted,
            "binary": binary, "untracked": untracked, "digest": h.hexdigest()[:16],
            "truncated": num.truncated or other.truncated}


# ── snapshot and diff (F4, N4) ────────────────────────────────────────────────

SNAPSHOT_TIMEOUT_S = 60
SNAP_UNTRACKED_MAX = 512 << 10     # untracked files larger than this are named, not added
BLOB_MAX = 512 << 10               # files larger than this are listed without hunks
DIFF_FILE_MAX = 256 << 10          # patch bytes per file
DIFF_TOTAL_MAX = 1536 << 10        # JSON-encoded patch bytes per response
DIFF_JSON_MAX = 2 << 20            # what diff() may encode to, metadata included
REVIEW_JSON_MAX = 2 << 20          # the whole review response (hub.py caps bodies by this)
DIFF_MAX_PATCHED_FILES = 400
IGNORED_SAMPLE = 20
REVIEW_REF = "refs/corral/review/"


def _unlink_own_temp(path):
    """Remove a temp file THIS module created. The only unlink in worktrees.py."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


@contextlib.contextmanager
def _temp_index(tmp_dir, content, mtime_ns=None):
    """A private copy of an index, removed afterwards; the real one is never touched.

    Pass the real index's `mtime_ns`: git's racy-clean check compares each
    entry's mtime with the index file's own mtime, and a copy stamped "now"
    would make a same-size edit made in the same instant look unchanged.
    """
    Path(tmp_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(prefix=".corral-index-", dir=str(tmp_dir))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
        yield path
    finally:
        _unlink_own_temp(path)


def _index_path(p):
    return git(["rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=p).text.strip()


def _split_z(out):
    return [os.fsdecode(f) for f in out.split(b"\0") if f]


def _names(p, args):
    return _split_z(git(args, cwd=p, optional_locks_off=True, max_out=64 << 20).out)


def snapshot(entry, tmp_dir=None):
    """Freeze what the user is about to review as an immutable tree OID.

    Copies the worktree's real index (so force-added and intent-to-add
    entries survive), runs `add -A` and `write-tree` on the copy, and pins
    the tree with refs/corral/review/<id> so gc cannot collect it. Untracked
    files over 512 KiB are named in `too_big`, not added. Ignored files are
    counted and sampled, never added. Records the real index's identity and
    any path whose staged content differs from its working file.
    Never: touches the real index, or rebuilds on a timer.
    """
    verify(entry)
    with repo_lock(entry):
        return _snapshot_locked(entry, tmp_dir)


def _snapshot_locked(entry, tmp_dir=None, pin=True):
    """snapshot() for a caller that already holds repo_lock(entry).

    `pin=False` only looks: the review pin keeps naming what the user sees."""
    p = entry["path"]
    idx = _index_path(p)
    try:
        real = Path(idx).read_bytes()
        idx_mtime = os.stat(idx).st_mtime_ns
    except FileNotFoundError:
        real, idx_mtime = b"", None
    head = git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=p).text.strip()
    head_tree = git(["rev-parse", "--verify", head + "^{tree}"], cwd=p).text.strip()
    too_big = []
    for path in _names(p, ["ls-files", "--others", "--exclude-standard", "-z"]):
        try:
            st = os.lstat(os.path.join(p, path))
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_size > SNAP_UNTRACKED_MAX:
            too_big.append({"path": path, "size": st.st_size})
    specs = [b"."] + [b":(exclude,literal)" + os.fsencode(b["path"]) for b in too_big]
    with _temp_index(tmp_dir or registry_dir() / "tmp", real, idx_mtime) as tmp:
        env = {"GIT_INDEX_FILE": tmp}
        git(["add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul"], cwd=p,
            env_extra=env, input=b"\0".join(specs) + b"\0", timeout=SNAPSHOT_TIMEOUT_S)
        tree = git(["write-tree"], cwd=p, env_extra=env, timeout=SNAPSHOT_TIMEOUT_S).text.strip()
    staged = set(_names(p, ["diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD", "--"]))
    unstaged = set(_names(p, ["diff", "--name-only", "-z", "--no-renames", "--"]))
    ignored = _names(p, ["ls-files", "--others", "--ignored", "--exclude-standard",
                         "--directory", "-z"])
    if pin:
        pin = git(["commit-tree", tree, "-p", head], cwd=p,
                  input=f"corral review snapshot {entry['id']}\n".encode()).text.strip()
        git(["update-ref", REVIEW_REF + entry["id"], pin], cwd=p)
    return {"tree": tree, "head": head, "head_tree": head_tree, "base_sha": entry["base_sha"],
            "index_id": hashlib.sha256(real).hexdigest(),
            "staged_differs": sorted(staged & unstaged), "too_big": too_big,
            "ignored": {"count": len(ignored), "sample": ignored[:IGNORED_SAMPLE]}}


def _path_fields(raw):
    """JSON-safe path fields: `path`, plus `path_b64` when the bytes are not UTF-8."""
    try:
        return {"path": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": raw.decode("utf-8", "replace"),
                "path_b64": base64.b64encode(raw).decode()}


def _diff_tree_args(*extra):
    # `-c` must come before the subcommand: after it, `diff-tree -c` means combined diff.
    return ["-c", "diff.renameLimit=1000", "diff-tree", "-r", "-z", "-M",
            "--no-textconv", "--no-ext-diff", *extra]


def diff(entry, tree):
    """The reviewed tree against the base, as a file list with capped unified patches.

    Every changed file is listed; hunks are omitted for binaries, files over
    512 KiB, and anything past the per-file or per-response caps (then
    `truncated` is set). Symlinks show as symlink changes and are never
    followed. Textconv, external diff, colour and prefix config are ignored.
    """
    p = entry["path"]
    base = entry["base_sha"]
    raw = git(_diff_tree_args("--raw", base, tree), cwd=p, max_out=64 << 20)
    fields = raw.out.split(b"\0")
    recs, i = [], 0
    while i < len(fields) and fields[i]:
        meta = fields[i].decode("ascii").lstrip(":").split()
        old_mode, new_mode, old_sha, new_sha, status = meta[:5]
        if status[0] in "RC":
            old_raw, new_raw = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old_raw = new_raw = fields[i + 1]
            i += 2
        recs.append({"status": status[0], "old_mode": old_mode, "new_mode": new_mode,
                     "old_sha": old_sha, "new_sha": new_sha, "old_raw": old_raw, "new_raw": new_raw})
    num = git(_diff_tree_args("--numstat", base, tree), cwd=p, max_out=64 << 20).out.split(b"\0")
    stats, j = {}, 0
    while j < len(num) and num[j]:
        a, d, rest = num[j].split(b"\t", 2)
        if rest == b"":                      # rename: old and new follow as fields
            key, j = num[j + 2], j + 3
        else:
            key, j = rest, j + 1
        stats[key] = (a, d)
    shas = sorted({s for r in recs for s in (r["old_sha"], r["new_sha"]) if set(s) != {"0"}})
    sizes = {}
    if shas:
        bc = git(["cat-file", "--batch-check=%(objectname) %(objectsize)"], cwd=p,
                 input=("\n".join(shas) + "\n").encode())
        for line in bc.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                sizes[parts[0]] = int(parts[1])
    files, budget, patched, truncated = [], DIFF_TOTAL_MAX, 0, False
    for r in recs:
        a, d = stats.get(r["new_raw"], (b"0", b"0"))
        binary = a == b"-"
        size = max(sizes.get(r["old_sha"], 0), sizes.get(r["new_sha"], 0))
        f = dict(_path_fields(r["new_raw"]), status=r["status"],
                 old_path=_path_fields(r["old_raw"])["path"] if r["status"] in "RC" else None,
                 add=None if binary else int(a), **{"del": None if binary else int(d)},
                 binary=binary, too_big=size > BLOB_MAX,
                 symlink="120000" in (r["old_mode"], r["new_mode"]),
                 mode_change=(r["old_mode"] != r["new_mode"] and r["status"] == "M"),
                 patch=None)
        if not binary and not f["too_big"]:
            if budget <= 0 or patched >= DIFF_MAX_PATCHED_FILES:
                truncated = True
            else:
                spec = {os.fsdecode(b":(literal)" + r["old_raw"]), os.fsdecode(b":(literal)" + r["new_raw"])}
                args = [a for a in _diff_tree_args("-p", "--no-color", "--src-prefix=a/",
                                                    "--dst-prefix=b/", base, tree, "--",
                                                    *sorted(spec)) if a != "-z"]
                pr = git(args, cwd=p, max_out=min(DIFF_FILE_MAX, max(budget, 1)))
                patched += 1
                text = None if pr.truncated else pr.out.decode("utf-8", "replace")
                cost = len(json.dumps(text)) if text is not None else 0
                if text is None or cost > budget:
                    truncated = True        # escaping can triple Unicode; charge what is sent
                else:
                    f["patch"] = text
                    budget -= cost
        files.append(f)
    return fit_review({"base": base, "tree": tree, "files": files, "truncated": truncated},
                      limit=DIFF_JSON_MAX, at=None)


def fit_review(obj, limit=REVIEW_JSON_MAX, at="diff"):
    """Shrink a review until json.dumps() of it fits `limit`, and say so.

    `obj[at]` (or `obj` itself when `at` is None) is a diff: patches go
    first, largest first, then file rows from the end (`files_omitted`
    counts them). Never touches the tree or anything an action depends on."""
    d = obj if at is None else obj.get(at)
    if not isinstance(d, dict) or len(json.dumps(obj)) <= limit:
        return obj
    files = d.get("files") or []
    over = len(json.dumps(obj)) - limit
    for f in sorted((f for f in files if f.get("patch")),
                    key=lambda f: len(json.dumps(f["patch"])), reverse=True):
        over -= len(json.dumps(f["patch"])) - len("null")
        f["patch"] = None
        d["truncated"] = True
        if over <= 0:
            return obj
    while files and len(json.dumps(obj)) > limit:
        cut = max(1, len(files) // 8)
        del files[-cut:]
        d["files_omitted"] = (d.get("files_omitted") or 0) + cut
        d["truncated"] = True
    big = obj.get("too_big") if at is not None else None
    while big and len(json.dumps(obj)) > limit:
        cut = max(1, len(big) // 8)
        del big[-cut:]
        obj["too_big_omitted"] = (obj.get("too_big_omitted") or 0) + cut
    ign = (obj.get("ignored") or {}).get("sample") if at is not None else None
    while ign and len(json.dumps(obj)) > limit:
        del ign[-max(1, len(ign) // 8):]
    return obj


# ── crash points (tests only) ─────────────────────────────────────────────────

def _crash_point(name):
    """SIGKILL ourselves at `name` when the crash suite asks (T-CRS-*). Inert otherwise."""
    if os.environ.get("CORRAL_WT_TEST") == "1" and os.environ.get("CORRAL_WT_CRASH_AT") == name:
        os.kill(os.getpid(), signal.SIGKILL)


# ── commit (D5) ───────────────────────────────────────────────────────────────

COMMIT_TIMEOUT_S = 120
RECOVERY_REF = "refs/corral/recovery/"


def _ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{secrets.token_hex(2)}"


def _tree_of_index(p, content, tmp_dir):
    """write-tree of an index's bytes, via a private copy (write-tree may rewrite the index)."""
    with _temp_index(tmp_dir, content) as tmp:
        return git(["write-tree"], cwd=p, env_extra={"GIT_INDEX_FILE": tmp}).text.strip()


def _index_id(idx):
    """The identity snapshot() records: sha256 of the index bytes (none: of b"")."""
    try:
        return hashlib.sha256(Path(idx).read_bytes()).hexdigest()
    except FileNotFoundError:
        return hashlib.sha256(b"").hexdigest()


def _replace_index(idx, content, expect_id=None):
    """Swap `content` in as the real index the way git does: O_EXCL index.lock, then rename.

    With `expect_id`, the index must still be that one once the lock is held
    (git writes the index only under index.lock, so it cannot change after):
    otherwise someone staged work, and it is left exactly as it is.
    """
    lock = idx + ".lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise Refused("busy", "another git process holds the worktree's index.lock") from None
    try:
        try:
            if expect_id is not None and _index_id(idx) != expect_id:
                raise Refused("changed", "something was staged in the worktree since review; "
                                         "the index was left as it is")
            os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(lock, idx)
    except BaseException:
        _unlink_own_temp(lock)          # our own lock, never another process's
        raise


def _reconcile_index(p, new, tmp_dir, expect_id=None):
    """Point the real index at commit `new` without touching the work tree (step 6).

    `expect_id` (the identity journalled with the commit) must match both
    before the rebuild and under index.lock; otherwise Refused("changed").
    """
    idx = _index_path(p)
    if expect_id is not None and _index_id(idx) != expect_id:
        raise Refused("changed", "something was staged in the worktree since review; "
                                 "the index was left as it is")
    with _temp_index(tmp_dir, Path(idx).read_bytes(), os.stat(idx).st_mtime_ns) as tmp:
        env = {"GIT_INDEX_FILE": tmp}
        git(["read-tree", new], cwd=p, env_extra=env, timeout=COMMIT_TIMEOUT_S)
        git(["update-index", "-q", "--refresh"], cwd=p, env_extra=env, check=False,
            timeout=COMMIT_TIMEOUT_S)
        content = Path(tmp).read_bytes()
    _replace_index(idx, content, expect_id)


def commit_tree(entry, tree, index_id, message, expect_head, registry=None, tmp_dir=None):
    """Commit exactly the reviewed `tree` on the pane's branch (the §2.2 protocol).

    1. refuse if commit.gpgSign is set (commit-tree would not sign);
    2. under the repo lock: refuse on index.lock; re-snapshot and require the
       same tree and the same real-index identity; branch must be expect_head;
    3. keep staged content that differs from the file as a recovery ref;
    4. commit-tree → new, journalled `prepared`;
    5. compare-and-swap update-ref, journalled `ref_moved`;
    6. rebuild the index from `new` on a copy, refresh stat, swap in under
       index.lock, journalled `done`;
    7. postcondition: branch == new and the index's tree == the reviewed tree.
    Never: runs hooks, amends, touches the work tree, runs read-tree -u, or
    prompts for a key. Hub commits are unsigned; the Commit button says so.
    """
    registry = registry or Registry()
    tmp_dir = tmp_dir or registry_dir() / "tmp"
    if not (message or "").strip():
        raise ValueError("a commit message is required")
    p = entry["path"]
    sign = git(["config", "--type=bool", "--get", "commit.gpgSign"], cwd=p, check=False)
    if sign.text.strip() == "true":
        raise Refused("signing", "this repository signs commits (commit.gpgSign); hub commits "
                                 "cannot be signed, so commit in the worktree by hand")
    v = verify(entry)
    with repo_lock(entry):
        refuse_open_ops(entry, registry)
        idx = _index_path(p)
        if os.path.exists(idx + ".lock"):
            raise Refused("busy", "another git process holds the worktree's index.lock")
        now = _snapshot_locked(entry, tmp_dir)
        if now["tree"] != tree or now["index_id"] != index_id:
            raise Refused("changed", "files changed since you opened review; refresh it")
        v = verify(entry)
        if v["oid"] != expect_head or now["head"] != expect_head:
            raise Refused("identity", f"the branch moved since review (now {v['oid'][:12]})")
        head_tree = git(["rev-parse", expect_head + "^{tree}"], cwd=p).text.strip()
        if head_tree == tree:
            return {"commit": None, "noop": True, "recovery_refs": []}
        recovery = []
        if now["staged_differs"]:
            staged_tree = _tree_of_index(p, Path(idx).read_bytes(), tmp_dir)
            keep = git(["commit-tree", staged_tree, "-p", expect_head], cwd=p,
                       input=b"corral: staged content kept before hub commit\n").text.strip()
            ref = f"{RECOVERY_REF}{entry['id']}/{_ts()}-index"
            git(["update-ref", ref, keep, ""], cwd=p)
            recovery.append(ref)
            registry.update(entry["id"], recovery_refs=list(
                registry.read(entry["id"]).get("recovery_refs") or []) + [ref])
        msg = message.strip() + "\n"
        new = git(["commit-tree", tree, "-p", expect_head], cwd=p, input=msg.encode(),
                  timeout=COMMIT_TIMEOUT_S).text.strip()
        op = registry.begin_op(entry["id"], "commit", stage="prepared", new=new,
                               expect_old=expect_head, tree=tree, index_id=index_id)
        _crash_point("commit:prepared")
        r = git(["update-ref", entry["branch"], new, expect_head], cwd=p, check=False)
        if r.rc != 0:
            registry.set_op(entry["id"], op, state="done", stage="refused")
            raise Refused("identity", "the branch moved while committing; nothing was committed")
        registry.set_op(entry["id"], op, stage="ref_moved")
        _crash_point("commit:ref_moved")
        try:
            _reconcile_index(p, new, tmp_dir, expect_id=index_id)
        except Exception as err:            # noqa: BLE001 — the ref moved: never `intent`
            registry.set_op(entry["id"], op, state="unknown", error=str(err)[:ERR_SNIPPET])
            raise Refused("unknown", "the commit is on the branch but the index could not be "
                                     f"updated ({err}); staged work was left as it is. Resolve "
                                     "it with `corral-light worktrees`") from None
        _crash_point("commit:index")
        try:
            ok = (git(["rev-parse", entry["branch"]], cwd=p).text.strip() == new
                  and _tree_of_index(p, Path(idx).read_bytes(), tmp_dir) == tree)
        except Exception as err:            # noqa: BLE001 — the ref moved: never `intent`
            registry.set_op(entry["id"], op, state="unknown", error=str(err)[:ERR_SNIPPET])
            raise Refused("unknown", f"the commit is on the branch but checking it failed "
                                     f"({err}); resolve it with `corral-light worktrees`") from None
        if not ok:
            registry.set_op(entry["id"], op, state="unknown", stage="done")
            raise Refused("unknown", "the commit landed but the index does not match; "
                                     "resolve it with `corral-light worktrees`")
        registry.finish_op(entry["id"], op, {"state": "done", "stage": "done"}, last_commit=new)
        return {"commit": new, "noop": False, "recovery_refs": recovery}


# ── publish (D7) ──────────────────────────────────────────────────────────────

PUSH_TIMEOUT_S = 120
GH_BIN = os.environ.get("CORRAL_TEST_GH") or "gh"
_GITHUB_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def push_urls(entry, remote):
    """Where `git push <remote>` would really go: pushurl and pushInsteadOf applied."""
    r = git(["remote", "get-url", "--push", "--all", "--", remote], cwd=entry["path"])
    return [u for u in r.text.splitlines() if u]


def rewrite_rule(entry, url):
    """The url.<base>.insteadOf / pushInsteadOf rule git would apply to `url`, or None.

    push() passes the confirmed URL to git, and git rewrites a URL argument
    too, so a chained rule would send the push (and its check) elsewhere."""
    r = git(["config", "--null", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$"],
            cwd=entry["path"], check=False)
    if r.rc not in (0, 1) or r.truncated:   # 1 is "no such keys"; anything else is unknown
        return f"git config could not be read ({r.err_text.strip()[:120]})"
    # --null: "<key>\n<value>\0"; a key's subsection (the base URL) may hold spaces.
    for rec in r.out.split(b"\0"):
        if not rec:
            continue
        key, _, prefix = rec.decode("utf-8", "replace").partition("\n")
        if url.startswith(prefix):          # an empty prefix matches every URL
            return f"{key} {prefix!r}"
    # A word that names a configured remote is that remote to `git push`, with
    # its own pushurl, whatever its fetch URL says.
    rr = git(["remote"], cwd=entry["path"], check=False)
    if rr.rc != 0 or rr.truncated:
        return "the configured remotes could not be listed"
    names = [n for n in rr.text.split("\n") if n]   # any whitespace but newline is a name
    if url in names:
        return f"it is the name of the remote {url!r}"
    if url and "/" not in url and os.sep not in url and any(
            os.path.lexists(Path(entry["common_dir"]) / d / url) for d in ("remotes", "branches")):
        return f"git reads {url!r} as a remote defined under .git/remotes or .git/branches"
    # What git itself makes of the argument: insteadOf applied, and a word that
    # names a configured remote becomes that remote's URL.
    got = git(["ls-remote", "--get-url", "--", url], cwd=entry["path"], check=False)
    if got.rc != 0 or got.text.strip() != url:
        return f"git reads it as {got.text.strip() or 'something else'}"
    return None


# Keys that can change where or how a push travels without changing its URL.
# Whole sections, not a list of known keys: http.* alone has curloptResolve,
# proxies, TLS and redirect settings, and a denylist of names kept missing one.
# url.* is here too: a repository-scoped rewrite rule is refused outright, not
# only when rewrite_rule can see that it matches.
_TRANSPORT_KEYS = re.compile(r"^(core\.sshcommand|core\.gitproxy|http\..+|ssh\..+|url\..+)$",
                             re.I)


def transport_override(entry):
    """A transport setting from this repository's own config (local or worktree
    scope, includes followed), as "key=value (scope)", or None.

    An agent can set these from inside its worktree with one `git config`,
    and git would send the push (and the check after it) wherever they say.
    The user's global and system config, and the hub's environment, are the
    user's own and are honoured."""
    r = git(["config", "--show-scope", "--null", "--get-regexp", "."], cwd=entry["path"],
            check=False, max_out=8 << 20)
    if r.rc not in (0, 1) or r.truncated:
        return "git config could not be read"
    # --show-scope --null: "<scope>\0<key>\n<value>\0" per entry.
    fields = r.out.split(b"\0")
    for i in range(0, len(fields) - 1, 2):
        scope = fields[i].decode("utf-8", "replace")
        key, _, value = fields[i + 1].decode("utf-8", "replace").partition("\n")
        if scope in ("local", "worktree", "command") and _TRANSPORT_KEYS.match(key):
            return f"{key}={value!r} ({scope})"
    return None


def github_repo(url):
    """"owner/name" for a GitHub remote URL, else None."""
    m = _GITHUB_RE.match(url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def push(entry, remote, push_url, oid, reviewed_tree, registry=None):
    """Push the reviewed commit to refs/heads/corral/<slug> at the confirmed URL. Never forced.

    Refuses: a dirty worktree, a branch tip other than `oid`, a HEAD tree
    other than the last reviewed one, a remote whose effective push URL is
    not the one confirmed (or that has more than one). Pushes to the URL,
    not the remote name, with a fully qualified refspec so a tag of the same
    name cannot match. Failure causes are read from stderr (a non-fast-forward
    rejection is rc 1). Pre-push hooks still run.
    """
    registry = registry or Registry()
    p = entry["path"]
    refuse_open_ops(entry, registry)
    v = verify(entry)
    if v["oid"] != oid:
        raise Refused("identity", f"the branch is at {v['oid'][:12]}, not the reviewed {oid[:12]}")
    if git(["rev-parse", oid + "^{tree}"], cwd=p).text.strip() != reviewed_tree:
        raise Refused("changed", "the commit is not the tree you reviewed; open review again")
    st = git(["status", "--porcelain=v1", "-z", "--untracked-files=normal"], cwd=p)
    if st.out.strip(b"\0"):
        raise Refused("uncommitted", "the worktree has changes that are not committed; "
                                     "commit or discard them first")
    urls = push_urls(entry, remote)
    if len(urls) != 1:
        raise Refused("remote_changed", f"{remote} has {len(urls)} push URLs; "
                                        "publishing needs exactly one")
    if urls[0] != push_url:
        raise Refused("remote_changed", f"{remote} now pushes to {urls[0]}, not the "
                                        f"{push_url} you confirmed")
    rule = rewrite_rule(entry, push_url)
    if rule:
        raise Refused("rewrite", f"git would rewrite {push_url} again ({rule}), so the push "
                                 "would not go where you confirmed; nothing was pushed")
    over = transport_override(entry)
    if over:
        raise Refused("transport", f"this repository's own git config changes how pushes "
                                   f"travel ({over}), so the push and its check could go "
                                   "somewhere you did not confirm; remove it (or set it in "
                                   "your global config instead), then publish again")
    ref = entry["branch"]
    op = registry.begin_op(entry["id"], "push", url=push_url, ref=ref, oid=oid)
    _crash_point("push:before")
    r = git(["push", "--porcelain", "--", push_url, f"{oid}:{ref}"], cwd=p, check=False,
            timeout=PUSH_TIMEOUT_S)
    _crash_point("push:after")
    if r.rc != 0:
        registry.set_op(entry["id"], op, state="done", stage="refused")
        both = r.text + r.err_text
        if "non-fast-forward" in both or "fetch first" in both or "[rejected]" in both:
            raise Refused("non_ff", "the remote branch has commits this one does not; "
                                    "it was not overwritten")
        raise GitError(["git", "push"], r.rc, r.err_text)
    there = git(["ls-remote", "--", push_url, ref], cwd=p, timeout=PUSH_TIMEOUT_S).text.split()
    ok = bool(there) and there[0] == oid
    registry.set_op(entry["id"], op, state="done" if ok else "unknown", stage="done")
    if not ok:
        raise Refused("unknown", "the push ran but the remote does not show the commit")
    prev = registry.read(entry["id"]).get("published") or {}
    registry.update(entry["id"], published=dict(prev, url=push_url, ref=ref, oid=oid, at=_now()))
    return {"pushed": oid, "url": push_url, "ref": ref}


def _gh(args, cwd, input=None):
    try:
        return _run([GH_BIN, *args], cwd, timeout=PUSH_TIMEOUT_S, check=False, input=input)
    except FileNotFoundError:
        return None


def _pr_head_owner(head, repo):
    """The GitHub owner a PR's head lives under: the fork's for `owner:branch`,
    else the base repo's. Lower case; "" when unknown."""
    return (head.split(":", 1)[0] if ":" in head else (repo or "").split("/")[0]).lower()


def open_pr(entry, title, body, repo, registry=None, remote="origin"):
    """Open (or find) the pull request for this branch on the confirmed `repo`.

    Without gh, returns a compare URL for GitHub remotes. Idempotent: an
    open PR for the head is returned, never duplicated. Text goes to gh as
    argv and stdin, never through a shell; prompts are off and --repo is
    always explicit, so gh never chooses between a fork and its parent.
    """
    registry = registry or Registry()
    refuse_open_ops(entry, registry)      # before anything, found PR or not
    p = entry["path"]
    branch = entry["branch"][len("refs/heads/"):]
    base = entry["base_ref"][len("refs/heads/"):]
    urls = push_urls(entry, remote)
    origin_repo = github_repo(urls[0]) if len(urls) == 1 else None
    head = branch
    if origin_repo and origin_repo != repo:
        head = f"{origin_repo.split('/')[0]}:{branch}"     # a fork's branch into its parent
    auth = _gh(["auth", "status"], p)
    if auth is None:
        if not origin_repo:
            raise Refused("no_gh", "gh is not installed and this remote is not on GitHub")
        return {"pr_url": None, "compare_url":
                f"https://github.com/{repo}/compare/{base}...{head}?expand=1"}
    if auth.rc != 0:
        raise Refused("gh_signed_out", "gh is signed out; run `gh auth login` in a terminal, "
                                       "then publish again")
    found = _gh(["pr", "list", "--repo", repo, "--head", branch, "--state", "open",
                 "--json", "url,headRepositoryOwner"], p)
    owner = _pr_head_owner(head, repo)
    url = None
    if found is not None and found.rc == 0:
        try:
            # --head matches the branch name in any fork: keep the one from our head.
            for pr in json.loads(found.text or "[]"):
                login = ((pr.get("headRepositoryOwner") or {}).get("login") or "").lower()
                if login and login == owner:    # no owner (a deleted fork) is not ours
                    url = pr["url"]
                    break
        except (ValueError, KeyError, TypeError, AttributeError):
            url = None
    if not url:
        refuse_open_ops(entry, registry)
        op = registry.begin_op(entry["id"], "pr", repo=repo, head=head)
        _crash_point("pr:before")
        made = _gh(["pr", "create", "--repo", repo, "--head", head, "--base", base,
                    "--title", title, "--body-file", "-"], p, input=(body or "").encode())
        if made is None or made.rc != 0:
            registry.set_op(entry["id"], op, state="done", stage="failed")
            raise GitError(["gh", "pr", "create"], made.rc if made else None,
                           made.err_text if made else "gh vanished")
        url = (made.text.strip().splitlines() or [""])[-1]
        _crash_point("pr:after")
        registry.set_op(entry["id"], op, state="done", stage="done", url=url)
    prev = registry.read(entry["id"]).get("published") or {}
    registry.update(entry["id"], published=dict(prev, pr_url=url))
    return {"pr_url": url, "compare_url": None}


# ── discard, restore, purge (D9) ──────────────────────────────────────────────

LOCK_WAIT_S = 5
IGNORED_INVENTORY_MAX = 500


SCAN_TIMEOUT_S = 20


class ScanFailed(Exception):
    """The process scan could not answer. Discard refuses: it must fail closed."""


def _have_proc():
    return os.path.isdir("/proc/self/fd")


def _lsof_bin():
    """lsof, where macOS keeps it first; None when there is none."""
    for cand in ("/usr/sbin/lsof", shutil.which("lsof")):
        if cand and os.access(cand, os.X_OK):
            return cand
    return None


def processes_in(path):
    """[(pid, command)] of this user's processes whose cwd or an open file is inside `path`.

    Linux reads /proc (it catches setsid'd grandchildren and the user's own
    shells, not just what the hub started); elsewhere lsof +D (macOS has no
    /proc: docs/worktree-plan-macos.md M1). Never includes us. Raises
    ScanFailed when it cannot answer, so a caller never reads "none" into
    "could not look".
    """
    root = os.path.realpath(path)
    return _procs_proc(root) if _have_proc() else _procs_lsof(root)


def _procs_proc(root):
    me, uid, found = os.getpid(), os.getuid(), []
    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError as e:
        raise ScanFailed(f"cannot list /proc: {e}") from None
    for pid in pids:
        if pid == me:
            continue
        base = f"/proc/{pid}"
        try:
            if os.stat(base).st_uid != uid:
                continue
        except OSError:
            continue                        # gone
        # An unreadable cwd still has its open files read. A process whose cwd
        # and fds are both unreadable (non-dumpable: keyring agents and the
        # like) cannot be inspected by anyone without privilege; it is left
        # out, as before, rather than blocking every Discard on the machine.
        hits = []
        try:
            hits.append(os.readlink(f"{base}/cwd"))
        except FileNotFoundError:
            continue                        # gone
        except OSError:
            pass
        try:
            for fd in os.listdir(f"{base}/fd"):
                with contextlib.suppress(OSError):
                    hits.append(os.readlink(f"{base}/fd/{fd}"))
        except OSError:
            pass
        if any(h == root or h.startswith(root + os.sep) for h in hits):
            try:
                with open(f"{base}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            except OSError:
                cmd = "?"
            found.append((pid, cmd[:120]))
    return found


def _procs_lsof(root):
    """lsof -F output: `p<pid>` starts a process, `c<command>` names it.

    lsof's exit code is no signal (1 both when it finds processes and when a
    path is bad), so: records mean found; no records and anything on stderr
    means the scan failed; no records and silence means none.
    """
    lsof = _lsof_bin()
    if not lsof:
        raise ScanFailed("lsof is not installed, so open files cannot be checked")
    try:
        # cwd: beside the worktree, never inside it (lsof would count itself).
        where = ["+D", root] if os.path.isdir(root) else []
        r = _run([lsof, "-nP", "-w", "-a", "-u", str(os.getuid()), *where, "-F", "pc",
                  *([] if where else ["--", root])],
                 os.path.dirname(root), timeout=SCAN_TIMEOUT_S, check=False, max_out=8 << 20)
    except GitTimeout:
        raise ScanFailed(f"lsof took over {SCAN_TIMEOUT_S}s") from None
    if r.truncated:
        raise ScanFailed("lsof produced more output than can be read")
    me, found, pid = os.getpid(), {}, None
    for line in r.out.decode("utf-8", "replace").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
            if pid != me:
                found.setdefault(pid, "?")
        elif line.startswith("c") and pid in found:
            found[pid] = line[1:120]
    # lsof's own warnings (e.g. a devfs it cannot stat on macOS) are not a
    # failed scan; any other stderr line is.
    errs = [l for l in r.err_text.splitlines() if l.strip() and not l.startswith("lsof: WARNING")]
    if not found and (r.rc not in (0, 1) or errs):
        raise ScanFailed(f"lsof failed: {r.err_text.strip()[:200] or f'exit {r.rc}'}")
    return sorted(found.items())


def _set_aside_stale_lock(idx, record):
    """Move index.lock aside only if nothing can still be writing it.

    `record` is {"lock", "stopped_at", "pid"?} from the pane whose agent is
    stopped or dead. The lock must name the same file and predate the stop
    (a git started since is never touched); the stopped writer, when its pid
    is known, must be gone; and no process may hold the lock open. git keeps
    its lock file open for as long as it holds the lock, so a live git from
    anywhere (outside the worktree included) keeps it. Renamed, not deleted.
    """
    lock = idx + ".lock"
    if not record or os.path.realpath(record.get("lock") or "") != os.path.realpath(lock):
        return False
    try:
        if record.get("pid") is not None:
            pid = int(record["pid"])
            if pid <= 1 or _pid_running(pid):
                return False
        if os.stat(lock).st_mtime > float(record.get("stopped_at") or 0):
            return False
        if processes_in(lock):
            return False
        os.replace(lock, f"{lock}.corral-stale-{_ts()}")
    except (OSError, TypeError, ValueError, ScanFailed):
        return False
    return True


def _wait_lock_gone(idx, stale=None):
    if stale and os.path.exists(idx + ".lock"):
        _set_aside_stale_lock(idx, stale)
    deadline = time.monotonic() + LOCK_WAIT_S
    while os.path.exists(idx + ".lock"):
        if time.monotonic() > deadline:
            raise Refused("busy", "a git process still holds the worktree's index.lock")
        time.sleep(0.05)


def trash_dir():
    return worktree_root() / ".trash"


def _admin_dir(entry):
    """The worktree's git admin dir (.git/worktrees/<name>): its index and index.lock."""
    return str(Path(entry["common_dir"]) / "worktrees" / entry["admin_name"])


def _processes_using(entry):
    """processes_in() over the worktree and its admin dir, de-duplicated."""
    seen = {}
    for root in (entry["path"], _admin_dir(entry)):
        if os.path.isdir(root):
            for pid, cmd in processes_in(root):
                seen.setdefault(pid, cmd)
    return sorted(seen.items())


PREFLIGHT_SETTLE_S = 0.5       # two looks this far apart tell "outdated" from "still writing"


def discard_preflight(entry, registry=None, agent_pgids=(), tree=None, tmp_dir=None):
    """What can refuse a discard before the agent is stopped (converged finding 3).

    Unsettled ops, a worktree that is not ours any more, an unusable trash
    folder, and processes inside the worktree that are NOT in the agent's
    process groups (`agent_pgids`): stopping the agent cannot clear those,
    so killing it first would only lose its turn. With `tree`, a review
    that no longer matches the files refuses here too. Raises Refused or
    ValueError; returns the processes the agent's stop will end.
    """
    registry = registry or Registry()
    refuse_open_ops(entry, registry)
    verify(entry)
    tdir = trash_dir()
    _refuse_symlink(tdir, "trash folder")
    try:
        busy = _processes_using(entry)
    except ScanFailed as e:
        raise Refused("busy", f"could not check for processes inside the worktree ({e}); "
                              "nothing was moved") from None
    ours = set(agent_pgids or ())

    def pgid(pid):
        try:
            return os.getpgid(pid)
        except OSError:
            return None
    foreign = [(pid, cmd) for pid, cmd in busy if pgid(pid) not in ours]
    if foreign:
        raise Refused("busy", "still running inside the worktree: " +
                      ", ".join(f"pid {pid} ({cmd})" for pid, cmd in foreign) +
                      "; the agent was left running")
    if tree is not None:
        # An outdated review refuses here, before the stop. But if the files
        # are still changing, the agent (or something it started) is writing:
        # stopping it is what lets the next review hold (T-RMV-11).
        def now():
            with repo_lock(entry):
                return _snapshot_locked(entry, tmp_dir, pin=False)["tree"]
        first = now()
        if first != tree:
            time.sleep(PREFLIGHT_SETTLE_S)
            if now() == first:
                raise Refused("changed", "files changed since you opened review; refresh "
                                         "it (the agent was left running)")
    return [pid for pid, _ in busy]


def discard(entry, tree, registry=None, tmp_dir=None, stale_lock=None):
    """Recovery ref, then move the whole worktree into <root>/.trash/. Deletes nothing.

    The caller stops the pane's writers first (D11); this refuses, naming
    them, if any process still has its cwd or an open file inside. Then:
    verify; wait for index.lock; under the repo lock re-snapshot and require
    the reviewed `tree`; point refs/corral/recovery/<id>/<ts> at a commit of
    it; inventory ignored files; journal the move; plain `git worktree move`
    (Phase 0: a dirty worktree moves without --force; never any --force).
    The branch is kept.
    """
    registry = registry or Registry()
    p = entry["path"]
    refuse_open_ops(entry, registry)
    try:
        busy = _processes_using(entry)
    except ScanFailed as e:
        raise Refused("busy", f"could not check for processes inside the worktree ({e}); "
                              "nothing was moved") from None
    if busy:
        raise Refused("busy", "still running inside the worktree: " +
                      ", ".join(f"pid {pid} ({cmd})" for pid, cmd in busy))
    verify(entry)
    _wait_lock_gone(_index_path(p), stale_lock)
    with repo_lock(entry):
        refuse_open_ops(entry, registry)
        now = _snapshot_locked(entry, tmp_dir or registry_dir() / "tmp")
        if now["tree"] != tree:
            raise Refused("changed", "files changed since you opened review; refresh it")
        ts = _ts()
        keep = git(["commit-tree", tree, "-p", now["head"]], cwd=p,
                   input=f"corral: discarded {entry['id']}\n".encode()).text.strip()
        ref = f"{RECOVERY_REF}{entry['id']}/{ts}"
        git(["update-ref", ref, keep, ""], cwd=p)
        ignored = _names(p, ["ls-files", "--others", "--ignored", "--exclude-standard",
                             "--directory", "-z"])
        tdir = trash_dir()
        tdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _refuse_symlink(tdir, "trash folder")
        dest = tdir / f"{entry['id']}-{ts}"
        oid = verify(entry)["oid"]
        registry.update(entry["id"], recovery_refs=list(
            registry.read(entry["id"]).get("recovery_refs") or []) + [ref],
            ignored_at_discard=ignored[:IGNORED_INVENTORY_MAX], branch_oid_at_discard=oid)
        op = registry.begin_op(entry["id"], "discard", src=p, dst=str(dest))
        _crash_point("discard:journalled")
        git(["worktree", "move", "--", p, str(dest)], cwd=entry["common_dir"], timeout=ADD_TIMEOUT_S)
        _crash_point("discard:moved")
        registry.finish_op(entry["id"], op, {"state": "done", "stage": "done"},
                           phase="trashed", trash_path=str(dest))
    return {"recovery_ref": ref, "trash_path": str(dest)}


def restore(entry, registry=None):
    """Move a trashed worktree back to its path. Never overwrites an existing path."""
    registry = registry or Registry()
    if entry.get("phase") != "trashed" or not entry.get("trash_path"):
        raise ValueError("this worktree is not in trash")
    if os.path.lexists(entry["path"]):
        raise ValueError(f"{entry['path']} exists; refusing to overwrite it")
    parent = Path(entry["path"]).parent
    root = os.path.realpath(worktree_root())
    if not os.path.lexists(parent) and os.path.realpath(parent.parent) == root:
        parent.mkdir(mode=0o700)            # the repo's folder under the root, emptied by Discard
    with repo_lock(entry):
        refuse_open_ops(entry, registry)
        op = registry.begin_op(entry["id"], "restore", src=entry["trash_path"], dst=entry["path"])
        git(["worktree", "move", "--", entry["trash_path"], entry["path"]],
            cwd=entry["common_dir"], timeout=ADD_TIMEOUT_S)
        e = registry.finish_op(entry["id"], op, {"state": "done", "stage": "done"},
                               phase="active", trash_path=None)
    verify(e)
    return e


def purge(entry, confirm, registry=None):
    """Really delete a TRASHED worktree: typed confirmation of the branch name required.

    Removes only a path under <root>/.trash/; deletes the branch only if it
    is a corral/* branch still at the OID recorded at discard; never deletes
    recovery refs.
    """
    registry = registry or Registry()
    branch = entry.get("branch") or ""
    if not branch.startswith(BRANCH_PREFIX):
        raise ValueError(f"{branch!r} is not a corral/* branch; refusing")
    short = branch[len("refs/heads/"):]
    if confirm != short:
        raise ValueError(f"type the branch name ({short}) to confirm deletion")
    if entry.get("phase") != "trashed":
        raise ValueError("only a discarded (trashed) worktree can be purged")
    t = entry.get("trash_path") or ""
    tdir = os.path.realpath(trash_dir())
    if os.path.islink(t) or not os.path.realpath(t).startswith(tdir + os.sep):
        raise ValueError(f"{t} is not inside {tdir}; refusing")
    with repo_lock(entry):
        refuse_open_ops(entry, registry)
        op = registry.begin_op(entry["id"], "purge", path=t)
        git(["worktree", "remove", "--force", "--", t], cwd=entry["common_dir"], timeout=ADD_TIMEOUT_S)
        deleted = _delete_discarded_branch(entry)
        registry.finish_op(entry["id"], op, {"state": "done", "stage": "done",
                                             "branch_deleted": deleted},
                           phase="purged", trash_path=None)
    return {"branch_deleted": deleted}


def _delete_discarded_branch(entry):
    """Delete the corral/* branch only if it is still at the OID recorded at discard."""
    branch, want = entry.get("branch") or "", entry.get("branch_oid_at_discard")
    if not branch.startswith(BRANCH_PREFIX) or not want:
        return False
    return git(["update-ref", "-d", branch, want], cwd=entry["common_dir"], check=False).rc == 0


def is_integrated(entry):
    """True only if the branch tip is already contained in the base branch's tip.

    Having been pushed, or having an upstream, does not count as merged.
    """
    c = entry["common_dir"]
    tip = git(["rev-parse", "--verify", "-q", entry["branch"]], cwd=c, check=False).text.strip()
    if not tip:
        return False
    return git(["merge-base", "--is-ancestor", tip, entry["base_ref"]], cwd=c, check=False).rc == 0


# ── reconcile and restart resolution (F8, D13) ────────────────────────────────

def _registered(common_dir):
    """Realpaths git lists as worktrees of `common_dir`, or None if the repo is gone."""
    if not os.path.isdir(common_dir):
        return None
    r = git(["worktree", "list", "--porcelain", "-z"], cwd=common_dir, check=False)
    if r.rc != 0:
        return None
    return {os.path.realpath(f[len(b"worktree "):].decode("utf-8", "surrogateescape"))
            for f in r.out.split(b"\0") if f.startswith(b"worktree ")}


def _ref(entry, ref):
    return git(["rev-parse", "--verify", "-q", ref], cwd=entry["common_dir"],
               check=False).text.strip() or None


def resolve_op(entry, op, registry):
    """Settle an op left in `intent` by a crash, by CHECKING its postcondition.

    Never assumes rollback. A commit resumes from its journalled stage with
    the stored OIDs (no second commit). Anything that does not fit becomes
    `unknown`, which blocks further actions until resolved from the CLI.
    Returns a note for the user.
    """
    wid, kind, tmp = entry["id"], op.get("op"), registry_dir() / "tmp"
    done = lambda **f: registry.set_op(wid, op["op_id"], state="done", **f)  # noqa: E731
    unknown = lambda why: (registry.set_op(wid, op["op_id"], state="unknown"), why)[1]  # noqa: E731
    if kind == "commit":
        tip, new, old = _ref(entry, entry["branch"]), op.get("new"), op.get("expect_old")
        p = entry["path"]
        if op.get("stage") == "prepared" and tip == old and new:
            if git(["update-ref", entry["branch"], new, old], cwd=p, check=False).rc != 0:
                return unknown("commit interrupted; the branch moved meanwhile, outcome unknown")
            registry.set_op(wid, op["op_id"], stage="ref_moved")
            tip = new
        if tip == new and new:
            idx = _index_path(p)
            if _tree_of_index(p, Path(idx).read_bytes(), tmp) != op.get("tree"):
                if not op.get("index_id"):
                    # Journalled before the index identity existed: nothing can
                    # tell staged work apart from the review, so touch nothing.
                    return unknown("commit interrupted; the branch has it, but this journal "
                                   "predates the index check, so the index was left as it "
                                   "is; outcome unknown")
                _wait_lock_gone(idx)
                try:
                    # The identity journalled with the commit: anything staged
                    # while the hub was down is left exactly as it is.
                    _reconcile_index(p, new, tmp, expect_id=op.get("index_id"))
                except Refused as err:
                    return unknown(f"commit interrupted; the branch has it but the index "
                                   f"was not updated ({err.detail}), outcome unknown")
            if _tree_of_index(p, Path(idx).read_bytes(), tmp) == op.get("tree"):
                registry.finish_op(wid, op["op_id"], {"state": "done", "stage": "done"},
                                   last_commit=new)
                return "commit interrupted by a restart; finished it (outcome checked after restart)"
        return unknown("commit interrupted; branch and journal disagree, outcome unknown")
    if kind == "push":
        r = git(["ls-remote", "--", op["url"], op["ref"]], cwd=entry["common_dir"],
                check=False, timeout=PUSH_TIMEOUT_S)
        if r.rc != 0:
            # Could not ask the remote: that is not "it did not happen".
            return unknown("push interrupted; the remote could not be checked, outcome unknown")
        there = r.text.split()
        if there and there[0] == op.get("oid"):
            done(stage="done")
            prev = registry.read(wid).get("published") or {}
            registry.update(wid, published=dict(prev, url=op["url"], ref=op["ref"], oid=op["oid"], at=_now()))
            return "push interrupted by a restart; the remote has it (outcome checked after restart)"
        done(stage="not_done")
        return "push interrupted by a restart; the remote does not have it, so it did not happen"
    if kind == "pr":
        found = _gh(["pr", "list", "--repo", op.get("repo", ""), "--head",
                     entry["branch"][len("refs/heads/"):], "--state", "open",
                     "--json", "url,headRepositoryOwner"], entry["common_dir"])
        try:
            listed = json.loads(found.text) if found and found.rc == 0 else None
        except ValueError:
            listed = None
        if listed is not None:
            # --head matches the branch name in any fork: only the PR from the
            # head this op journalled is ours (the same rule as open_pr).
            owner = _pr_head_owner(op.get("head") or "", op.get("repo") or "")
            listed = [x for x in listed if isinstance(x, dict) and owner and
                      ((x.get("headRepositoryOwner") or {}).get("login") or "").lower() == owner]
        if listed:
            done(stage="done", url=listed[0].get("url"))
            prev = registry.read(wid).get("published") or {}
            registry.update(wid, published=dict(prev, pr_url=listed[0].get("url")))
            return "pull request creation interrupted; found it (outcome checked after restart)"
        if listed == []:
            done(stage="not_done")
            return "pull request creation interrupted; none exists, so it did not happen"
        return unknown("pull request creation interrupted; could not ask gh, outcome unknown")
    if kind in ("discard", "restore"):
        reg = _registered(entry["common_dir"]) or set()
        src, dst = os.path.realpath(op["src"]), os.path.realpath(op["dst"])
        if dst in reg and os.path.isdir(dst):
            registry.finish_op(wid, op["op_id"], {"state": "done", "stage": "done"},
                               phase="trashed" if kind == "discard" else "active",
                               trash_path=op["dst"] if kind == "discard" else None)
            return f"{kind} interrupted by a restart; it completed (outcome checked after restart)"
        if src in reg and os.path.isdir(src):
            done(stage="not_done")
            return f"{kind} interrupted by a restart; nothing moved"
        return unknown(f"{kind} interrupted; the worktree is at neither place, outcome unknown")
    if kind == "purge":
        if not os.path.lexists(op.get("path", "")):
            deleted = _delete_discarded_branch(entry)
            registry.finish_op(wid, op["op_id"], {"state": "done", "stage": "done",
                                                  "branch_deleted": deleted},
                               phase="purged", trash_path=None)
            return "purge interrupted by a restart; the files are gone (outcome checked)"
        done(stage="not_done")
        return "purge interrupted by a restart; nothing was deleted"
    return unknown(f"unknown op {kind!r}")


def reconcile(registry=None):
    """Compare the registry with git and the disk after a restart. Repairs nothing.

    Finishes or flags `intent` entries, resolves journalled ops, marks
    missing/tampered worktrees (each reported once, when its phase changes),
    reports deleted branches, and lists unknown dirs under the root as
    orphans. Never prunes, deletes or edits git state beyond finishing a
    journalled op. Returns notes: [{"id", "kind", "note", "path"?}].
    """
    registry = registry or Registry()
    notes = []
    note = lambda e, kind, text, **x: notes.append(dict(id=e["id"] if e else None, kind=kind,  # noqa: E731
                                                       note=text, **x))
    entries = registry.all()
    by_repo = {}
    for e in entries:
        by_repo.setdefault(e.get("common_dir"), []).append(e)
    known = set()
    for common, group in by_repo.items():
        try:
            reg = _registered(common) if common else None
        except Exception as err:            # noqa: BLE001 — one repo never stops the rest
            for e in group:
                for op in [o for o in e.get("ops") or [] if o.get("state") == "intent"]:
                    registry.set_op(e["id"], op["op_id"], state="unknown",
                                    error=str(err)[:ERR_SNIPPET])
                note(e, "error", f"could not list the worktrees of {e.get('repo_top') or common} "
                                 f"({err}); {e.get('branch') or e['id']} was not checked")
            continue
        for e in group:
            try:
                _reconcile_entry(e, reg, registry, note, known)
            except Exception as err:        # noqa: BLE001 — one entry never stops the rest
                note(e, "error", f"could not check {e.get('branch') or e['id']}: {err}")
    for child in orphans(known):
        note(None, "orphan", f"{child} is under the worktree root but in no registry entry",
             path=str(child))
    return notes


def _reconcile_entry(e, reg, registry, note, known):
    """reconcile() for one entry; `known` collects its paths for orphans()."""
    for k in ("path", "trash_path"):
        if e.get(k):
            known.add(os.path.realpath(e[k]))
    phase = e.get("phase")
    if phase in ("purged", "missing", "tampered"):
        return
    if reg is None:
        registry.update(e["id"], phase="missing", error="the repository is gone")
        note(e, "missing", f"{e['repo_top']} is gone; {e['branch']} cannot be checked")
        return
    for op in [o for o in e.get("ops") or [] if o.get("state") == "intent"]:
        try:
            note(e, "op", resolve_op(e, op, registry))
        except Exception as err:            # noqa: BLE001 — one op never stops the rest
            registry.set_op(e["id"], op["op_id"], state="unknown", error=str(err)[:ERR_SNIPPET])
            note(e, "op", f"{op.get('op')} interrupted; checking it failed ({err}), "
                          "outcome unknown")
    e = registry.read(e["id"])
    if e["phase"] == "intent":
        if os.path.realpath(e["path"]) in reg:
            try:
                verify(e)
                registry.update(e["id"], phase="active")
                return
            except IdentityError:
                pass
        registry.update(e["id"], phase="missing", error="creation did not finish")
        note(e, "missing", f"creating {e['branch']} did not finish before a restart")
        return
    if e["phase"] == "active":
        if not _ref(e, e["branch"]):
            note(e, "branch", f"the branch {e['branch'][len('refs/heads/'):]} was deleted outside Corral")
            return
        try:
            verify(e)
        except IdentityError as err:
            phase = "tampered" if err.reason == "tampered" else "missing"
            if err.reason == "identity":
                note(e, "identity", str(err))
                return
            registry.update(e["id"], phase=phase, error=str(err))
            note(e, phase, str(err))
    elif e["phase"] == "trashed":
        t = e.get("trash_path")
        if not t or os.path.realpath(t) not in reg or not os.path.isdir(t):
            registry.update(e["id"], phase="missing", error="the trashed copy is gone")
            note(e, "missing", f"the trashed copy of {e['branch']} is gone")


def orphans(known=None, registry=None):
    """Paths under the worktree root (and its trash) that no registry entry names.

    Read-only: lists, never removes. `known` is a set of realpaths; without
    it, every entry's path and trash path are read from the registry.
    """
    if known is None:
        known = {os.path.realpath(e[k]) for e in (registry or Registry()).all()
                 for k in ("path", "trash_path") if e.get(k)}
    out = []
    root = worktree_root()
    if root.is_dir():
        for rd in sorted(root.iterdir()):
            if not rd.is_dir() or rd.is_symlink():
                continue
            for child in sorted(rd.iterdir()):
                if os.path.realpath(child) not in known:
                    out.append(child)
    return out
