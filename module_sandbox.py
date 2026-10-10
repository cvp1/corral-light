"""The module sandbox: an allowlist bubblewrap profile (docs/finops-module-plan.md §3).

Used for a module's collector, CLI and doctor runs, and (with two extra
file binds) for the core's own `grok usage` runs (vendor_reports.py).

Not the blind reviewer's profile (review_sandbox.wrap starts from the host
root read-only and shares the network when no egress proxy is given).
Here the root is an empty tmpfs and only what is listed exists inside:

- /usr read-only; /bin, /sbin, /lib, /lib32, /lib64 as the host has them
  (symlinks into /usr on merged-/usr hosts, read-only binds otherwise);
- a few non-secret /etc files the loader and libc read;
- each declared read path read-only, the feed read-only, any extra
  (source, destination) file binds read-only;
- one writable dir (also HOME and the cwd); the tmpfs root remounted
  read-only so nothing else is writable;
- fresh /dev, /proc (own pid namespace), private /tmp;
- --unshare-all without --share-net: no host loopback (the hub), no
  internet, no abstract unix sockets;
- --die-with-parent, --new-session, every capability dropped, no nested
  user namespaces; --clearenv, then only the variables given.

Nothing else from $HOME exists: not session.key, not ~/.ssh, not any
lane's login. `available()` says whether this host can do it.
Phase 0 prototype and measurements: spike/p0/sandbox/, docs/finops-phase0.md.

On macOS the same interface builds a Seatbelt profile run by
/usr/bin/sandbox-exec (docs/finops-macos-sandbox.md): deny by default;
read-only system paths, the interpreter's own prefix (plus the keg
library dirs its extension modules link to) and each declared read;
metadata only on the parent folders of what is allowed; one
writable dir; no network, or for a fetcher outbound to one loopback
proxy port only; a cleared environment. There are no mount namespaces,
so a file bind whose destination lies outside the data dir is a path
alias (the source path is used and allowed), and one inside it is a
copy. Every path is passed as a profile parameter, never spliced in.
"""
import functools
import glob
import os
import resource
import shutil
import subprocess
import sys
import sysconfig
import time

import review_sandbox

SYSTEM_TOPS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64")
# What the loader and libc read. No shadow, no TLS keys, no resolv.conf.
ETC_FILES = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
             "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf")
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"

# Resource limits applied to the sandboxed process tree (inherited through
# bwrap). Address space is generous: a Python collector reading big JSONL
# needs room, and a runaway is still stopped.
# nproc is headroom ABOVE the user's current process count: RLIMIT_NPROC
# counts every thread of the real uid, not just this tree.
LIMITS = {"as_bytes": 2 << 30, "nproc": 128, "fsize_bytes": 1 << 30, "nofile": 1024}


def user_tasks():
    """Tasks (threads) this uid runs now, or None when they cannot be
    counted. RLIMIT_NPROC counts them all, so the cap must sit above this
    count or bwrap cannot even start. Call it in the parent, never after
    fork. macOS counts processes, not threads, and has no /proc: ps."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["/bin/ps", "-x", "-U", str(os.getuid()), "-o", "pid="],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return len(r.stdout.split()) if r.returncode == 0 else None
    uid, n = os.getuid(), 0
    try:
        pids = os.listdir("/proc")
    except OSError:
        return None
    for d in pids:
        if d.isdigit():
            try:
                if os.stat("/proc/" + d).st_uid == uid:
                    n += len(os.listdir(f"/proc/{d}/task"))   # threads count too
            except OSError:
                pass
    return n


class SandboxError(ValueError):
    """A path the profile refuses to mount."""


def _bwrap():
    return os.environ.get("CORRAL_BWRAP", review_sandbox.BWRAP)


def _real(p, what, *, want_dir=None):
    p = str(p)
    if not os.path.isabs(p):
        raise SandboxError(f"{what} must be an absolute path: {p!r}")
    real = os.path.realpath(p)          # bind the target, never a swappable link
    if not os.path.exists(real):
        raise SandboxError(f"{what} does not exist: {p!r}")
    if want_dir and not os.path.isdir(real):
        raise SandboxError(f"{what} must be a directory: {p!r}")
    if real == "/":
        raise SandboxError(f"{what} may not be the filesystem root")
    return real


def _overlaps(a, b):
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def build_argv(argv, *, read_only=(), feed_dir=None, data_dir, file_binds=(), env=None,
               writable_extra=(), proxy_port=None):
    """-> the bwrap argv that runs `argv` inside the profile.

    read_only: absolute paths bound read-only at their own resolved path.
    feed_dir: bound read-only at its own path (None: no feed).
    data_dir: the one writable dir; HOME and the cwd.
    file_binds: (source, destination) pairs bound read-only; the
      destination may lie inside data_dir (a scratch home), where the
      parent dirs are created by bwrap.
    writable_extra: further dirs bound writable at their own path (a
      module's config dir, for its interactive setup only).
    env: the complete environment inside, beyond PATH and HOME (which the
      profile sets). Nothing from the caller's environment crosses.
    proxy_port: macOS only, a loopback port the process may connect to
      (the fetcher's egress proxy); Linux reaches its proxy by a bound
      unix socket instead."""
    if not argv:
        raise SandboxError("argv is empty")
    if sys.platform == "darwin":
        return _darwin_argv(argv, read_only=read_only, feed_dir=feed_dir, data_dir=data_dir,
                            file_binds=file_binds, env=env, writable_extra=writable_extra,
                            proxy_port=proxy_port)
    data = _real(data_dir, "data dir", want_dir=True)
    reads = [_real(p, "read path") for p in read_only]
    feed = _real(feed_dir, "feed dir", want_dir=True) if feed_dir else None
    for r in reads + ([feed] if feed else []):
        if _overlaps(data, r):
            raise SandboxError(f"data dir and read path overlap: {data!r} / {r!r}")
    rw = [_real(p, "writable dir", want_dir=True) for p in writable_extra]
    for w in rw:
        for r in reads + ([feed] if feed else []):
            if _overlaps(w, r):
                raise SandboxError(f"writable dir and read path overlap: {w!r} / {r!r}")
    binds = []
    for src, dst in file_binds:
        s = _real(src, "bound file")
        if not os.path.isabs(str(dst)) or ".." in str(dst).split("/"):
            raise SandboxError(f"bind destination must be absolute and plain: {dst!r}")
        binds.append((s, str(dst)))

    a = [shutil.which(_bwrap()) or _bwrap(),
         "--unshare-all", "--unshare-user", "--disable-userns",
         "--die-with-parent", "--new-session", "--cap-drop", "ALL",
         "--tmpfs", "/", "--ro-bind", "/usr", "/usr"]
    for top in SYSTEM_TOPS:
        if os.path.islink(top):
            a += ["--symlink", os.readlink(top), top]
        elif os.path.isdir(top):
            a += ["--ro-bind", top, top]
    for f in ETC_FILES:
        if os.path.exists(f):
            a += ["--ro-bind", f, f]
    a += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for r in reads:
        a += ["--ro-bind", r, r]
    if feed:
        a += ["--ro-bind", feed, feed]
    a += ["--bind", data, data]
    for w in rw:
        a += ["--bind", w, w]
    for s, d in binds:
        a += ["--ro-bind", s, d]
    a += ["--remount-ro", "/"]
    full = dict(env or {})
    full.update({"PATH": SANDBOX_PATH, "HOME": data})
    a += ["--clearenv"]
    for k in sorted(full):
        a += ["--setenv", k, str(full[k])]
    a += ["--chdir", data, "--"]
    return a + [str(x) for x in argv]


# ── macOS: Seatbelt ─────────────────────────────────────────────────────

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# Read-only system paths every process needs (measured on macOS 27: dyld
# also reads the root directory itself, which lists only top-level names).
DARWIN_SYSTEM = ("/usr", "/System")
DARWIN_DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
# No process-fork: a child that calls setsid() leaves the process group, and
# macOS has no pid namespace to end it, so a sandboxed module starts no
# child processes at all (docs/finops-macos-sandbox.md §3). exec alone is
# enough for sandbox-exec -> env -> python.
DARWIN_BASE = """(version 1)
(deny default)
(allow process-exec)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow ipc-posix-sem)
(deny file-write-setugid)
(allow file-read-data file-read-metadata (literal "/"))
(allow file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))
(deny system-fcntl (fcntl-command 80 110))
"""


def _python_roots():
    """The running interpreter's own files, wherever they live."""
    exe = os.path.realpath(sys.executable)
    roots = {os.path.dirname(exe), os.path.realpath(sys.base_prefix),
             os.path.realpath(sys.prefix)}
    return sorted(r for r in roots if r and r != "/")


_OTOOL = "/usr/bin/otool"


@functools.lru_cache(maxsize=None)
def _linked_lib_dirs():
    """Directories of the shared libraries the interpreter's extension
    modules link to outside the system and its own prefix, read-only. A
    Homebrew python's _sqlite3, _ssl, _lzma, _zstd and _decimal link
    sibling kegs (/opt/homebrew/opt/sqlite/lib/...); without these the
    import fails inside the profile with "blocked by sandbox". Empty
    when otool (developer tools) is absent — then only the prefix."""
    if sys.platform != "darwin" or not os.path.exists(_OTOOL):
        return ()
    dynload = sysconfig.get_config_var("DESTSHARED") or ""
    exts = sorted(glob.glob(os.path.join(dynload, "*.so"))) if dynload else []
    if not exts:
        return ()
    try:
        out = subprocess.run([_OTOOL, "-L", *exts], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    inside = tuple(DARWIN_SYSTEM) + tuple(_python_roots())
    dirs = set()
    for line in out.splitlines():
        if not line.startswith("\t"):
            continue
        lib = line.strip().split(" (", 1)[0]
        if not lib.startswith("/"):
            continue  # @rpath, @loader_path: resolved inside the prefix
        real = os.path.realpath(lib)
        if any(real == r or real.startswith(r + "/") for r in inside):
            continue
        # Both spellings: dyld opens the install name (a Homebrew opt/
        # symlink), the kernel evaluates the resolved vnode.
        dirs.add(os.path.dirname(lib))
        dirs.add(os.path.dirname(real))
    return tuple(sorted(dirs))


def _ancestors(p):
    out = []
    p = os.path.dirname(p.rstrip("/"))
    while p and p != "/":
        out.append(p)
        p = os.path.dirname(p)
    return out


def _translate(value, aliases):
    """A path that starts with a bind destination, as its source path."""
    if not isinstance(value, str):
        return value
    for dst, src in aliases:
        if value == dst or value.startswith(dst + "/"):
            return src + value[len(dst):]
    return value


def _darwin_argv(argv, *, read_only, feed_dir, data_dir, file_binds, env, writable_extra,
                 proxy_port):
    data = _real(data_dir, "data dir", want_dir=True)
    reads = [_real(p, "read path") for p in read_only]
    feed = _real(feed_dir, "feed dir", want_dir=True) if feed_dir else None
    for r in reads + ([feed] if feed else []):
        if _overlaps(data, r):
            raise SandboxError(f"data dir and read path overlap: {data!r} / {r!r}")
    rw = [_real(p, "writable dir", want_dir=True) for p in writable_extra]
    for w in rw:
        for r in reads + ([feed] if feed else []):
            if _overlaps(w, r):
                raise SandboxError(f"writable dir and read path overlap: {w!r} / {r!r}")
    aliases = []
    for src, dst in file_binds:
        s = _real(src, "bound file")
        d = str(dst)
        if not os.path.isabs(d) or ".." in d.split("/"):
            raise SandboxError(f"bind destination must be absolute and plain: {dst!r}")
        if d == data or d.startswith(data + "/"):
            # Inside the writable dir: a copy stands in for the bind.
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copyfile(s, d)
        else:
            aliases.append((d.rstrip("/"), s))
            reads.append(s)
    aliases.sort(key=lambda a: -len(a[0]))
    if proxy_port is not None and not (isinstance(proxy_port, int) and 0 < proxy_port < 65536):
        raise SandboxError(f"proxy port must be a port number: {proxy_port!r}")

    params, rules = [], [DARWIN_BASE]

    def param(value):
        params.append(value)
        return f'(param "P{len(params) - 1}")'

    ro = (list(DARWIN_SYSTEM) + _python_roots() + list(_linked_lib_dirs())
          + reads + ([feed] if feed else []))
    rules.append("(allow file-read* file-map-executable "
                 + " ".join(f"(subpath {param(p)})" for p in ro) + ")")
    rules.append("(allow file-read* " + " ".join(f"(literal {param(d)})"
                                                 for d in DARWIN_DEVICES) + ")")
    rules.append("(allow file-read* file-write* "
                 + " ".join(f"(subpath {param(p)})" for p in [data] + rw) + ")")
    meta = sorted({a for p in ro + [data] + rw for a in _ancestors(p)})
    if meta:
        rules.append("(allow file-read-metadata "
                     + " ".join(f"(literal {param(a)})" for a in meta) + ")")
    if proxy_port is not None:
        rules.append(f'(allow network-outbound (remote tcp "localhost:{int(proxy_port)}"))')
    full = {k: _translate(str(v), aliases) for k, v in (env or {}).items()}
    full.update({"PATH": SANDBOX_PATH, "HOME": data, "TMPDIR": data})
    a = [SANDBOX_EXEC, "-p", "\n".join(rules)]
    for i, v in enumerate(params):
        a += ["-D", f"P{i}={v}"]
    a += ["/usr/bin/env", "-i"] + [f"{k}={full[k]}" for k in sorted(full)]
    return a + [_translate(str(x), aliases) for x in argv]


def limits_fn(tasks=None):
    """-> a preexec_fn that sets the resource limits, soft and hard, for
    the child tree. Everything that reads files or allocates is done here,
    in the parent: Python documents preexec_fn as unsafe in a threaded
    process (the hub), so the child only calls setrlimit. POSIX only.
    tasks: the uid's current task count (default: counted now)."""
    tasks = user_tasks() if tasks is None else tasks
    nproc = getattr(resource, "RLIMIT_NPROC", None) if tasks is not None else None
    plan = []
    for which, val in ((resource.RLIMIT_AS, LIMITS["as_bytes"]),
                       (nproc, (tasks or 0) + LIMITS["nproc"]),
                       (resource.RLIMIT_FSIZE, LIMITS["fsize_bytes"]),
                       (resource.RLIMIT_NOFILE, LIMITS["nofile"])):
        if which is None:
            continue
        try:
            _soft, hard = resource.getrlimit(which)
        except (ValueError, OSError):
            continue
        cap = val if hard == resource.RLIM_INFINITY else min(val, hard)
        plan.append((which, (cap, cap)))     # hard too: the child cannot raise it
    plan = tuple(plan)

    def apply():
        for which, pair in plan:
            try:
                resource.setrlimit(which, pair)
            except (ValueError, OSError):
                pass
    return apply


_AVAILABLE = None
_FAILED_AT = 0.0
FAIL_TTL_S = 60


def available(refresh=False):
    """(ok, why): can this host build the module sandbox? A real spawn that
    must find no network and an empty home. A pass is kept for the hub's
    life; a failure is retried after FAIL_TTL_S."""
    global _AVAILABLE, _FAILED_AT
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        return (False, "no module sandbox on this platform (Linux bubblewrap or macOS "
                       "Seatbelt only)")
    if _AVAILABLE is not None and not refresh:
        if _AVAILABLE[0] or time.monotonic() - _FAILED_AT < FAIL_TTL_S:
            return _AVAILABLE
    _AVAILABLE = _probe()
    if not _AVAILABLE[0]:
        _FAILED_AT = time.monotonic()
    return _AVAILABLE


PROBE = ("import os,socket,sys\n"
         "s=socket.socket(); s.settimeout(1)\n"
         "try:\n s.connect(('1.1.1.1',443)); sys.exit(3)\n"
         "except OSError: pass\n"
         "sys.exit(4 if os.path.exists(sys.argv[1]) else 0)\n")


def _probe():
    if sys.platform == "darwin":
        if not os.access(SANDBOX_EXEC, os.X_OK):
            return (False, f"{SANDBOX_EXEC} is not on this Mac")
    else:
        exe = shutil.which(_bwrap())
        if not exe:
            return (False, "bubblewrap (bwrap) is not installed on this host")
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="corral-msbx-",
                                         dir=os.path.expanduser("~")) as t:
            argv = build_argv([os.path.realpath(sys.executable), "-I", "-c", PROBE,
                               os.path.realpath(os.path.expanduser("~/.ssh"))],
                              data_dir=t)
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired, SandboxError) as e:
        return (False, f"bubblewrap did not run: {e}")
    if r.returncode == 3:
        return (False, "the module sandbox reached the network")
    if r.returncode == 4:
        return (False, "the module sandbox could see the home directory")
    if r.returncode != 0:
        why = (r.stderr or "").strip().splitlines()[-1:] or [f"exit {r.returncode}"]
        tool = "sandbox-exec" if sys.platform == "darwin" else "bubblewrap"
        return (False, f"{tool} cannot build the module sandbox here: {why[0][:200]}")
    return (True, "")
