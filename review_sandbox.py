"""The blind reviewer's sandbox (10x UX Part C, docs/ux-10x-plan.md §2.5).

A challenge feeds another vendor's agent a diff the operator has not vetted.
Whatever that diff says, the reviewer must not be able to change anything,
reach the hub's keys, or use the operator's other logins. Its own lane's
modes are one layer; this is the layer the vendor does not control: the
reviewer's process runs under bubblewrap with

- the whole filesystem read-only, and each of home's directories behind a
  throwaway overlay, so writes the vendor CLI makes to its own state work
  and vanish at exit;
- this hub's state hidden (session.key, other panes, worktrees), except
  the reviewer pane's own dir and the frozen tree it reviews (read-only);
- SSH, GPG, cloud and browser secrets, and every other lane's login, hidden;
- /run replaced (no user bus, no agent sockets, no container sockets), only
  the DNS stub kept; a private /tmp, its own pid namespace (no reading
  another process's environment), a new session, killed with its parent;
- the network kept: the vendor's API needs it.

Only the reviewer's own lane login stays writable, so a token refresh is
not lost. Linux only; `available()` says whether this host can do it.
"""
import os
import shutil
import subprocess
from pathlib import Path

BWRAP = os.environ.get("CORRAL_BWRAP", "bwrap")

# Environment that names a way out of the sandbox (sockets of agents and
# desktops). Dropped before the spawn.
DROP_ENV = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO", "DBUS_SESSION_BUS_ADDRESS",
            "DBUS_SYSTEM_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY",
            "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "I3SOCK", "DOCKER_HOST",
            "CONTAINER_HOST", "KRB5CCNAME")

# Home-relative directories that hold secrets no reviewer needs.
SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".config/gh",
               ".docker", ".kube", ".password-store", ".local/share/keyrings",
               ".mozilla", ".config/google-chrome", ".config/chromium",
               ".config/BraveSoftware", ".config/vivaldi", ".thunderbird",
               ".local/share/corral",            # the full Corral's state
               "aios/keyvault")
SECRET_FILES = (".git-credentials", ".netrc", ".pgpass", ".npmrc", ".pypirc")


def lane_logins(home=None):
    """{lane: [login files]} — each lane's own credential, as its launcher
    keeps it. The reviewer's lane keeps its own writable; the rest vanish."""
    h = Path(home or Path.home())
    codex_home = Path(os.environ.get("CORRAL_CODEX_HOME", h / ".config/corral-light/codex-home"))
    grok_home = Path(os.environ.get("CORRAL_GROK_HOME", h / ".grok"))
    return {
        "claude": [h / ".claude/.credentials.json"],
        "codex": [codex_home / "auth.json", h / ".codex/auth.json"],
        "grok": [grok_home / "auth.json"],
        "gemini": [h / ".gemini/antigravity-acp/acp_token.json",
                   h / ".gemini/oauth_creds.json"],
    }


_AVAILABLE = None


def available(refresh=False):
    """(ok, why): can this host run a reviewer sandbox? Checked once with a
    real `bwrap ... true` using the same namespaces and overlay as a spawn."""
    global _AVAILABLE
    if _AVAILABLE is not None and not refresh:
        return _AVAILABLE
    exe = shutil.which(BWRAP)
    if not exe:
        _AVAILABLE = (False, "bubblewrap (bwrap) is not installed on this host")
        return _AVAILABLE
    import tempfile
    home = str(Path.home())
    with tempfile.TemporaryDirectory(prefix="corral-sbx-") as t:
        pane, tree = os.path.join(t, "pane"), os.path.join(t, "tree")
        os.mkdir(pane)
        os.mkdir(tree)
        argv, _ = wrap(["/bin/sh", "-c", "! touch /etc/.corral-probe 2>/dev/null && "
                        f"touch {home}/.cache/.corral-sandbox-probe 2>/dev/null; "
                        f"! touch {tree}/x 2>/dev/null && touch {pane}/x"],
                       {}, lane="none", cwd=tree, state=os.path.join(t, "state"),
                       pane_dir=pane, tree_dir=tree)
        res = _probe_run(argv)
        wrote = os.path.exists(os.path.join(pane, "x"))
    if isinstance(res, str):
        _AVAILABLE = (False, res)
        return _AVAILABLE
    if res.returncode != 0 or not wrote:
        why = (res.stderr or "").strip().splitlines()[-1:] or [f"exit {res.returncode}"]
        _AVAILABLE = (False, f"bubblewrap cannot build the sandbox here: {why[0][:200]}")
        return _AVAILABLE
    if Path(home, ".cache/.corral-sandbox-probe").exists():   # the overlay leaked
        _AVAILABLE = (False, "the sandbox's home overlay wrote through to the real home")
        return _AVAILABLE
    _AVAILABLE = (True, "")
    return _AVAILABLE


def _probe_run(argv):
    try:
        return subprocess.run(argv, capture_output=True, timeout=20, text=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"bubblewrap did not run: {e}"


def wrap(argv, env, *, lane, cwd, state, pane_dir, tree_dir, home=None):
    """-> (argv, env) running `argv` inside the reviewer sandbox.

    `state`: this hub's state dir (hidden). `pane_dir`: the reviewer pane's
    own dir under it (kept writable: the lane's per-pane config lives there).
    `tree_dir`: the frozen tree (read-only; also the cwd). Paths keep their
    host names inside, so the ACP cwd needs no translation."""
    home = str(Path(home or Path.home()))
    exe = shutil.which(BWRAP) or BWRAP
    a = [exe, "--unshare-all", "--share-net", "--die-with-parent", "--new-session",
         "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
         "--tmpfs", "/run"]
    resolve = "/run/systemd/resolve"
    if os.path.isdir(resolve):                       # /etc/resolv.conf points here
        a += ["--ro-bind", resolve, resolve]
    # Home's own top-level directories each get a throwaway overlay (an
    # overlay on home itself is refused on some filesystems); files directly
    # in home stay read-only.
    try:
        tops = sorted(e.path for e in os.scandir(home) if e.is_dir(follow_symlinks=False))
    except OSError:
        tops = []
    for d in tops:
        a += ["--overlay-src", d, "--tmp-overlay", d]
    for rel in SECRET_DIRS:
        p = os.path.join(home, rel)
        if os.path.isdir(p):
            a += ["--tmpfs", p]
    for rel in SECRET_FILES:
        p = os.path.join(home, rel)
        if os.path.isfile(p):
            a += ["--ro-bind", "/dev/null", p]
    own = []
    for name, files in lane_logins(home).items():
        for f in files:
            if not os.path.isfile(f):
                continue
            if name == lane:
                own.append(str(f))
            else:
                a += ["--ro-bind", "/dev/null", str(f)]
    state = str(state)
    if os.path.isdir(state):
        a += ["--tmpfs", state]
    a += ["--bind", str(pane_dir), str(pane_dir),
          "--ro-bind", str(tree_dir), str(tree_dir)]
    for f in own:                                   # a refreshed token must persist
        a += ["--bind", f, f]
    a += ["--chdir", str(cwd), "--"]
    env = {k: v for k, v in (env or {}).items() if k not in DROP_ENV}
    env["XDG_RUNTIME_DIR"] = "/tmp"
    env["CORRAL_REVIEW_SANDBOX"] = "1"
    return a + list(argv), env
