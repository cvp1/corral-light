"""Prototype: the FinOps module collector's sandbox profile (plan §3, Phase 0).

A NEW allowlist profile, not the blind reviewer's (review_sandbox.wrap mounts
the host root read-only and shares the network when no egress proxy is
given). Here the sandbox root is an empty tmpfs and only what is listed
below exists inside:

- /usr read-only; /bin, /sbin, /lib, /lib64 recreated as the host has them
  (symlinks into /usr on merged-/usr hosts, read-only binds otherwise);
- a handful of non-secret /etc files the loader and libc read;
- each declared read path read-only, the feed read-only;
- the module's data dir writable (also HOME and the cwd); the tmpfs root
  itself remounted read-only, so nothing else (e.g. /etc/x) is writable;
- fresh /dev, /proc (own pid namespace), private /tmp;
- --unshare-all without --share-net: own user, pid, ipc, uts, cgroup and
  network namespaces, so neither the host's loopback (the hub) nor the
  internet nor abstract unix sockets are reachable;
- --die-with-parent, --new-session, all capabilities dropped, no nested
  user namespaces;
- --clearenv, then only PATH, HOME (= data dir), LANG.

Nothing else from $HOME exists: not session.key, not ~/.ssh, not any
lane's login. Reuses review_sandbox's BWRAP setting and executable lookup.
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import review_sandbox  # noqa: E402

BWRAP = review_sandbox.BWRAP

# Top-level system entries: on merged-/usr hosts these are symlinks and are
# recreated as such; a real directory is bound read-only.
SYSTEM_TOPS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64")

# /etc entries the dynamic loader and libc read. No shadow, no ssl keys,
# no hosts, no resolv.conf (there is no network to resolve on).
ETC_FILES = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
             "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf")

SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"
SANDBOX_LANG = "C.UTF-8"


class SandboxError(ValueError):
    """A path the profile refuses to mount."""


def _abs_existing(p, what, *, want_dir=None):
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


def build_argv(argv, read_only_paths, feed_dir, data_dir, *, extra_env=None):
    """-> bwrap argv running `argv` inside the collector profile.

    `read_only_paths`: absolute paths (files or dirs) bound read-only at
    their own (resolved) path. `feed_dir`: the hub's module feed, read-only.
    `data_dir`: the one writable dir; also HOME and cwd. `extra_env`: extra
    variables to set inside (the profile's own PATH/HOME/LANG win)."""
    if not argv:
        raise SandboxError("argv is empty")
    data = _abs_existing(data_dir, "data dir", want_dir=True)
    feed = _abs_existing(feed_dir, "feed dir", want_dir=True)
    reads = [_abs_existing(p, "read path") for p in read_only_paths]
    for r in reads + [feed]:
        if data == r or data.startswith(r.rstrip("/") + "/") or r.startswith(data + "/"):
            raise SandboxError(f"data dir and read path overlap: {data!r} / {r!r}")

    exe = shutil.which(BWRAP) or BWRAP
    a = [exe, "--unshare-all", "--unshare-user", "--disable-userns",
         "--die-with-parent", "--new-session", "--cap-drop", "ALL",
         "--tmpfs", "/",                       # explicit: an empty root
         "--ro-bind", "/usr", "/usr"]
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
    a += ["--ro-bind", feed, feed]
    a += ["--bind", data, data]
    # The tmpfs root holds only mount points; make it read-only so the
    # collector's one writable place really is its data dir (and /tmp).
    a += ["--remount-ro", "/"]
    env = dict(extra_env or {})
    env.update({"PATH": SANDBOX_PATH, "HOME": data, "LANG": SANDBOX_LANG})
    a += ["--clearenv"]
    for k in sorted(env):
        a += ["--setenv", k, str(env[k])]
    a += ["--chdir", data, "--"]
    return a + [str(x) for x in argv]


def available():
    """(ok, why): is bwrap installed. A real probe is the test suite's job."""
    if not shutil.which(BWRAP):
        return (False, "bubblewrap (bwrap) is not installed on this host")
    return (True, "")
