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
- its own network namespace: nothing on the host's loopback and no
  abstract unix socket is reachable; the vendor's API is reached through
  review_egress.py, which allows HTTPS to that vendor's domains only.

The reviewer's own lane login is readable, never renewed: a vendor that
rotates refresh tokens would sign the operator out everywhere if a sandbox
renewed one and lost the result. review_egress.py blocks those sign-in
hosts, and `login_seconds_left` lets the hub refuse to start a reviewer on
a token about to lapse. Linux only; `available()` says whether this host
can do it.
"""
import os
import shutil
import sys
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
               ".config/rclone", ".config/syncthing", ".local/state/syncthing",
               ".config/op", ".config/Bitwarden", ".config/Bitwarden CLI",
               ".local/share/corral",            # the full Corral's state
               "aios/keyvault")
SECRET_FILES = (".git-credentials", ".netrc", ".pgpass", ".npmrc", ".pypirc",
                ".Xauthority", ".ICEauthority", ".config/git/credentials")

# The only environment a reviewer starts with, beyond what the hub sets for
# its lane: the hub's own environment (tokens a shell exported, say) never
# crosses into the sandbox.
KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM",
            "TZ", "COLORTERM")
KEEP_ENV_PREFIXES = ("LC_",)


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


def _jwt_exp(token):
    import base64
    import json
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp"))
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def login_seconds_left(lane, home=None, now=None):
    """Seconds until the lane's access token lapses, from its login file;
    None when this lane renews safely (Gemini) or the file says nothing.
    Never returns or logs a token."""
    import json
    import time
    from datetime import datetime
    now = time.time() if now is None else now
    files = lane_logins(home).get(lane) or []
    f = next((p for p in files if os.path.isfile(p)), None)
    if lane == "gemini" or f is None:
        return None
    try:
        doc = json.loads(Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    exp = None
    if lane == "claude":
        o = doc.get("claudeAiOauth") or {}
        if not o.get("accessToken"):
            return 0.0
        exp = (o.get("expiresAt") or 0) / 1000
    elif lane == "codex":
        exp = _jwt_exp(((doc.get("tokens") or {}).get("access_token")) or "")
    elif lane == "grok":
        for v in doc.values():
            if isinstance(v, dict) and v.get("key") and v.get("expires_at"):
                try:
                    t = datetime.fromisoformat(str(v["expires_at"]).replace("Z", "+00:00")[:32])
                    exp = max(exp or 0, t.timestamp())
                except ValueError:
                    continue
    if exp is None:
        return None
    return exp - now


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
                       rw_dirs=[pane], tree_dir=tree)
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


def wrap(argv, env, *, lane, cwd, state, rw_dirs, tree_dir, home=None, egress=None,
         base_env=None):
    """-> (argv, env) running `argv` inside the reviewer sandbox.

    `state`: this hub's state dir (hidden). `rw_dirs`: the only dirs under
    it the reviewer may write (its lane config, its egress socket's dir);
    never the pane dir itself, whose metadata says it is a sandboxed
    reviewer. `env`: the lane's own spawn environment; with `base_env`
    (default os.environ) filtered to KEEP_ENV, it is the whole environment
    inside (bubblewrap clears the rest).
    `tree_dir`: the frozen tree (read-only; also the cwd). Paths keep their
    host names inside, so the ACP cwd needs no translation. `egress`: the
    unix socket of this reviewer's review_egress.Egress, inside one of `rw_dirs`;
    with it the sandbox gets its own network namespace and reaches the
    network only through that proxy."""
    home = str(Path(home or Path.home()))
    exe = shutil.which(BWRAP) or BWRAP
    a = [exe, "--unshare-all"] + ([] if egress else ["--share-net"]) + [
         "--die-with-parent", "--new-session",
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
    for name, files in lane_logins(home).items():
        if name == lane:
            continue                # read through the overlay; never renewed here
        for f in files:
            if os.path.isfile(f):
                a += ["--ro-bind", "/dev/null", str(f)]
    state = str(state)
    if os.path.isdir(state):
        a += ["--tmpfs", state]
    for d in rw_dirs:
        a += ["--bind", str(d), str(d)]
    a += ["--ro-bind", str(tree_dir), str(tree_dir)]
    base = os.environ if base_env is None else base_env
    inside = {k: v for k, v in base.items()
              if k in KEEP_ENV or k.startswith(KEEP_ENV_PREFIXES)}
    inside.update({k: v for k, v in (env or {}).items() if k not in DROP_ENV})
    inside["XDG_RUNTIME_DIR"] = "/tmp"
    inside["CORRAL_REVIEW_SANDBOX"] = "1"
    a += ["--clearenv"]
    for k in sorted(inside):
        a += ["--setenv", k, str(inside[k])]
    a += ["--chdir", str(cwd), "--"]
    if egress:
        shim = Path(__file__).resolve().with_name("review_egress.py")
        a += [sys.executable, str(shim), str(egress), "--"]
    return a + list(argv), inside
