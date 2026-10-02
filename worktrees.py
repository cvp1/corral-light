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
import contextlib
import hashlib
import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections import namedtuple
from pathlib import Path

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
        with contextlib.suppress(OSError):
            os.unlink(tmp)                 # our own temp file, never anything else
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
    """The filesystem type of the mount holding `path` (or its nearest existing ancestor)."""
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
        return None
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


def repo_dir(pr):
    """<root>/<repo name>-<hash6>: one dir per repository, from the common dir's realpath."""
    common = os.path.realpath(pr["common_dir"])
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


class IdentityError(Exception):
    """A worktree is not what the registry says. `reason`: missing | tampered | identity."""

    def __init__(self, reason, detail):
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


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


def agent_cwd(entry):
    """The folder the agent starts in: the worktree plus the subdir the user chose."""
    return Path(entry["path"]) / (entry.get("subdir") or "")


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
        try:
            rdir.mkdir(parents=True, exist_ok=True, mode=0o700)
            _refuse_symlink(rdir, "repository folder")
            if not os.path.realpath(rdir).startswith(os.path.realpath(root) + os.sep):
                raise ValueError(f"{rdir} resolves outside the worktree root")
            git(["worktree", "add", "-q", "-b", "corral/" + slug, "--", str(path), pr["head"]],
                cwd=pr["top"], timeout=ADD_TIMEOUT_S)
            verify(entry)
        except BaseException as e:
            registry.update(entry["id"], phase="missing", error=str(e)[:ERR_SNIPPET])
            raise
        return registry.update(entry["id"], phase="active")


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
