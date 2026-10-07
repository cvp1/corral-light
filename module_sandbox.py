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
lane's login. Linux only; `available()` says whether this host can do it.
Phase 0 prototype and measurements: spike/p0/sandbox/, docs/finops-phase0.md.
"""
import os
import shutil
import subprocess
import sys
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


def _user_procs():
    uid, n = os.getuid(), 0
    try:
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    if os.stat("/proc/" + d).st_uid == uid:
                        n += len(os.listdir(f"/proc/{d}/task"))   # threads count too
                except OSError:
                    pass
    except OSError:
        return None
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
               writable_extra=()):
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
      profile sets). Nothing from the caller's environment crosses."""
    if not argv:
        raise SandboxError("argv is empty")
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


def set_limits():
    """preexec_fn: resource limits for the child tree. POSIX only."""
    import resource
    procs = _user_procs()
    pairs = ((resource.RLIMIT_AS, LIMITS["as_bytes"]),
             (getattr(resource, "RLIMIT_NPROC", None) if procs is not None else None,
              (procs or 0) + LIMITS["nproc"]),
             (resource.RLIMIT_FSIZE, LIMITS["fsize_bytes"]),
             (resource.RLIMIT_NOFILE, LIMITS["nofile"]))
    for which, val in pairs:
        if which is None:
            continue
        try:
            _soft, hard = resource.getrlimit(which)
            cap = val if hard == resource.RLIM_INFINITY else min(val, hard)
            resource.setrlimit(which, (cap, cap))   # hard too: the child cannot raise it
        except (ValueError, OSError):
            pass


_AVAILABLE = None
_FAILED_AT = 0.0
FAIL_TTL_S = 60


def available(refresh=False):
    """(ok, why): can this host build the module sandbox? A real spawn that
    must find no network and an empty home. A pass is kept for the hub's
    life; a failure is retried after FAIL_TTL_S."""
    global _AVAILABLE, _FAILED_AT
    if not sys.platform.startswith("linux"):
        return (False, "no module sandbox on this platform (Linux and bubblewrap only)")
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
    exe = shutil.which(_bwrap())
    if not exe:
        return (False, "bubblewrap (bwrap) is not installed on this host")
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="corral-msbx-",
                                         dir=os.path.expanduser("~")) as t:
            argv = build_argv([sys.executable, "-I", "-c", PROBE,
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
        return (False, f"bubblewrap cannot build the module sandbox here: {why[0][:200]}")
    return (True, "")
