#!/usr/bin/python3
"""update — bring a Corral Light install up to date with its repository.

    corral-light update                 fetch, fast-forward, restart the hub when it is safe
    corral-light update --check         say what an update would do; change nothing
    corral-light update --now           restart even if panes are mid-turn or waiting
    corral-light update --wait SECONDS  wait up to SECONDS for the panes to settle
    corral-light update --no-restart    pull only (the running hub keeps the old code)
    corral-light update --install-skill   link the corral-update skill into ~/.claude/skills
    corral-light update --install-timer   a daily unattended update (systemd or launchd)
    corral-light update --remove-timer

The checkout updated is the one the running hub serves (it says so in
/api/state), else this script's own. Nothing is touched when the checkout has
local changes, commits of its own, or is not on the branch; every reason is
said. The adapters (spike/) are reinstalled with `npm ci` only when their
lockfile changed, and a failed install puts the old commit back.

The hub is restarted only when no pane is mid-turn or waiting on a
permission, since a restart ends those turns (an open ask_human question
survives). Run from inside a pane, that pane's own turn is not counted; the
restart is queued after this prints, and the pane resumes on the next message.

Exit: 0 up to date or updated, 1 refused or failed, 3 deferred (panes busy).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
DEFAULT_URL = os.environ.get("CORRAL_LIGHT_URL") or \
    f"http://127.0.0.1:{os.environ.get('CORRAL_LIGHT_PORT', '8098')}"
BRANCH = "master"
UNIT = "corral-light.service"
TIMER_NAME = "corral-light-update"
from install_service import LABEL as LAUNCHD_LABEL     # noqa: E402
TIMER_LABEL = LAUNCHD_LABEL + ".update"
STATUS_FILE = STATE / "update-status.json"
SKILL = "corral-update"
LOCKFILE = "spike/package-lock.json"
# The checkout's own adapter patcher (adapter_patches.py). npm ci installs
# unpatched adapters, so after a reinstall the patches must be applied again.
PATCHER = "adapter_patches.py"
PATCH_TIMEOUT_S = 120
SERVICE_TEMPLATES = ("corral-light.service", f"{LAUNCHD_LABEL}.plist",
                     "corral-light-watch.service", "corral-light-watch.timer")
GIT_TIMEOUT_S = 120
NPM_TIMEOUT_S = 900
BOOT_WAIT_S = 90
POLL_S = 15
WORKING = ("busy", "starting", "uncertain")

EXIT_OK, EXIT_REFUSED, EXIT_DEFERRED = 0, 1, 3


class Refused(Exception):
    """The update cannot proceed; nothing was changed."""


# ── git ──────────────────────────────────────────────────────────────────
def git(root, *args, check=True, timeout=GIT_TIMEOUT_S):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                       text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: "
                      f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def plan(root, branch=BRANCH, fetch=True):
    """Where the checkout stands against origin/<branch>. Raises Refused with
    the reason when it cannot be fast-forwarded. Touches nothing but refs."""
    root = Path(root)
    if not (root / ".git").exists():
        raise Refused(f"{root} is not a git checkout — reinstall with install.sh")
    if fetch:
        git(root, "fetch", "-q", "origin")
    cur = git(root, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if not cur:
        raise Refused(f"{root} is on a detached commit (pinned with "
                      f"CORRAL_LIGHT_REF?); check out {branch} to follow it")
    if cur != branch:
        raise Refused(f"{root} is on branch {cur!r}, not {branch!r}")
    dirty = git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise Refused(f"{root} has local changes — left exactly as it is:\n"
                      + "\n".join("    " + line for line in dirty.splitlines()[:10]))
    want = f"origin/{branch}"
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", want)
    ahead = int(git(root, "rev-list", "--count", f"{want}..HEAD"))
    if ahead:
        raise Refused(f"{root} has {ahead} commit(s) {want} does not contain — "
                      f"push or drop them first")
    behind = int(git(root, "rev-list", "--count", f"HEAD..{want}"))
    changed = git(root, "diff", "--name-only", head, target).splitlines() if behind else []
    subjects = git(root, "log", "--format=%h %s", f"HEAD..{want}").splitlines() if behind else []
    return {"root": str(root), "branch": branch, "head": head, "target": target,
            "behind": behind, "changed": changed, "subjects": subjects,
            "lockfile": LOCKFILE in changed,
            "templates": [f for f in changed if f in SERVICE_TEMPLATES]}


def head_moved_at(root):
    """Seconds since the epoch when HEAD last moved (the reflog), or None."""
    out = git(root, "log", "-g", "-1", "--format=%ct", check=False)
    return int(out) if out.isdigit() else None


# ── the running hub ──────────────────────────────────────────────────────
def hub_state(url):
    """The hub's /api/state, or None when nothing answers. Pairs locally."""
    import consult
    try:
        hub = consult.connect(url)
        return hub.get("/api/state", timeout=30)
    except consult.ConsultError:
        return None


def hub_pid():
    try:
        return int(json.loads((STATE / "hub.pid").read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def process_started_at(pid):
    """Epoch seconds the process started, from `ps` (Linux and macOS)."""
    try:
        r = subprocess.run(["ps", "-o", "etimes=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=5)
        return time.time() - int(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def hub_is_stale(st, root):
    """True when the hub serves an older commit than the checkout holds, False
    when it serves this one, None when that cannot be told."""
    info = (st or {}).get("hub") or {}
    head = git(root, "rev-parse", "HEAD", check=False)
    if info.get("commit"):
        return info["commit"] != head
    # A hub from before /api/state named its commit: compare its start time
    # with the moment HEAD last moved.
    pid, moved = hub_pid(), head_moved_at(root)
    started = process_started_at(pid) if pid else None
    if started is None or moved is None:
        return None
    return started < moved


def own_pane():
    """The pane this process runs in, or None."""
    pid = os.environ.get("CORRAL_PANE_ID")
    if pid:
        return pid
    # A hub from before CORRAL_PANE_ID: Claude panes run under
    # STATE/panes/<id>/config.
    m = re.search(r"/panes/([0-9a-f]{12})/config/?$",
                  os.environ.get("CLAUDE_CONFIG_DIR", ""))
    return m.group(1) if m else None


def inside_hub():
    """Is this process a descendant of the hub (so a restart ends it)?"""
    if own_pane():
        return True
    try:
        return UNIT in Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False


def blockers(st, own=None):
    """Panes a restart would interrupt: mid-turn, or waiting on a permission
    (pending requests die with the agent). `own` is never counted."""
    out = []
    for p in (st or {}).get("panes") or []:
        if p.get("id") == own:
            continue
        why = None
        if p.get("pending"):
            why = f"{len(p['pending'])} permission(s) waiting on you"
        elif p.get("state") in WORKING:
            why = f"{p.get('state')}"
        if why:
            out.append({"id": p.get("id"), "title": p.get("title") or p.get("label"),
                        "why": why})
    return out


# ── the change ───────────────────────────────────────────────────────────
def node_bin():
    """The npm to use: the installer's private Node first, then PATH."""
    for d in (os.environ.get("CORRAL_NODE_BIN"), STATE / "node" / "bin",
              Path.home() / ".hermes" / "node" / "bin"):
        if d and (Path(d) / "npm").exists():
            return Path(d)
    found = shutil.which("npm")
    return Path(found).parent if found else None


def npm_ci(root):
    nb = node_bin()
    if nb is None:
        raise Refused("the adapters' lockfile changed and no npm was found "
                      "(set CORRAL_NODE_BIN)")
    env = dict(os.environ, PATH=f"{nb}:{os.environ.get('PATH', '')}")
    r = subprocess.run([str(nb / "npm"), "ci", "--no-audit", "--no-fund"],
                       cwd=str(Path(root) / "spike"), env=env, capture_output=True,
                       text=True, timeout=NPM_TIMEOUT_S)
    if r.returncode != 0:
        raise Refused(f"npm ci failed: {(r.stderr or r.stdout).strip()[-400:]}")


def patch_adapters(root):
    """Run the checkout's OWN patcher on its spike/: the code just pulled,
    which knows the adapter versions just installed, not this running
    script. -> its report, or None for a checkout without one. Raises
    Refused when a patch does not take."""
    script = Path(root) / PATCHER
    if not script.is_file():
        return None
    r = subprocess.run([sys.executable, "-I", "-B", str(script), "apply",
                        str(Path(root) / "spike")],
                       capture_output=True, text=True, timeout=PATCH_TIMEOUT_S)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        raise Refused(f"the adapter patches did not apply: {out[-400:]}")
    return out


def apply(p, say):
    """Fast-forward, and reinstall the adapters when their lockfile changed.
    A failed install puts the old commit (and its adapters) back."""
    root, old = p["root"], p["head"]
    if p["lockfile"] and node_bin() is None:
        raise Refused("the adapters' lockfile changed and no npm was found "
                      "(set CORRAL_NODE_BIN) — nothing was changed")
    git(root, "merge", "--ff-only", "-q", p["target"])
    say(f"updated {short(old)} → {short(p['target'])} ({p['behind']} commit(s))")
    if p["lockfile"] or PATCHER in p["changed"]:
        report = None
        try:
            if p["lockfile"]:
                say("the adapters' lockfile changed: npm ci in spike/ …")
                npm_ci(root)
            report = patch_adapters(root)
        except (Refused, subprocess.SubprocessError, OSError) as e:
            git(root, "reset", "-q", "--keep", old)
            # Pristine adapters first (a patch may have half-taken), then
            # the old checkout's own patches. Say so if that fails too.
            try:
                npm_ci(root)
                patch_adapters(root)
                recovered = ""
            except (Refused, subprocess.SubprocessError, OSError) as r:
                recovered = (f"; the adapters could NOT be restored ({str(r)[:200]}): run "
                             f"`npm ci` in spike/ and `python3 adapter_patches.py apply`")
            raise Refused(f"{e} — rolled back to {short(old)}{recovered}")
        if p["lockfile"]:
            say("adapters reinstalled")
        if report:
            say("adapter patches: " + "; ".join(report.splitlines()[:3]))


def service_manager():
    """('systemd'|'launchd', active) for the hub's service, or (None, False)."""
    if platform.system() == "Darwin":
        if not shutil.which("launchctl"):
            return None, False
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                           capture_output=True, text=True)
        return ("launchd", True) if r.returncode == 0 else (None, False)
    if not shutil.which("systemctl"):
        return None, False
    r = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                       capture_output=True, text=True)
    return "systemd", r.stdout.strip() == "active"


def restart(inside):
    """Restart the hub's service. From inside the hub it is queued, so this
    process can finish speaking first. -> (done, message)."""
    mgr, active = service_manager()
    if not active:
        return False, ("the hub is not running as a service here — restart it "
                       "by hand to load the new code")
    if mgr == "systemd":
        argv = ["systemctl", "--user", "restart", UNIT]
        if inside:
            argv.insert(2, "--no-block")
    else:
        argv = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    if inside:
        # Detached and delayed: this process dies with the hub.
        subprocess.Popen(["/bin/sh", "-c", "sleep 2; exec \"$@\"", "restart", *argv],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "hub restart queued (this pane's turn ends with it)"
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False, f"restart failed: {(r.stderr or r.stdout).strip()[:300]}"
    return True, "hub restarted"


def wait_healthy(url, timeout=BOOT_WAIT_S):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=5) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


# ── timer and skill ──────────────────────────────────────────────────────
def unit_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def timer_units(root):
    service = f"""# Written by `corral-light update --install-timer`.
[Unit]
Description=Corral Light — update to the latest {BRANCH} when the hub is idle

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {root}/update.py --unattended
# Exit 3 means "deferred: panes were busy", the updater doing its job.
SuccessExitStatus=3
"""
    timer = """# Written by `corral-light update --install-timer`.
[Unit]
Description=Corral Light — daily update check

[Timer]
OnCalendar=*-*-* 05:15
RandomizedDelaySec=20m
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service, timer


def launchd_plist(root):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{TIMER_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>{root}/update.py</string><string>--unattended</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>{STATE}/update.log</string>
  <key>StandardErrorPath</key><string>{STATE}/update.log</string>
</dict>
</plist>
"""


def install_timer(root, say):
    if platform.system() == "Darwin":
        path = Path.home() / "Library/LaunchAgents" / f"{TIMER_LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launchd_plist(root), encoding="utf-8")
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{TIMER_LABEL}"],
                       capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Refused(f"launchctl bootstrap failed: {r.stderr.strip()[:300]}")
        say(f"wrote {path}; daily at 05:15")
        return
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    service, timer = timer_units(root)
    (d / f"{TIMER_NAME}.service").write_text(service, encoding="utf-8")
    (d / f"{TIMER_NAME}.timer").write_text(timer, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{TIMER_NAME}.timer"],
                   check=True, capture_output=True)
    say(f"enabled {TIMER_NAME}.timer: daily around 05:15, only when the hub is idle")


def remove_timer(say):
    if platform.system() == "Darwin":
        path = Path.home() / "Library/LaunchAgents" / f"{TIMER_LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{TIMER_LABEL}"],
                       capture_output=True)
        path.unlink(missing_ok=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{TIMER_NAME}.timer"],
                       capture_output=True)
        for ext in ("service", "timer"):
            (unit_dir() / f"{TIMER_NAME}.{ext}").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    say("update timer removed")


def install_skill(root, say, home=None):
    """Link the skill from the checkout, so every update refreshes it. Panes
    see ~/.claude/skills through their private config dirs."""
    src = Path(root) / "skills" / SKILL
    dst = Path(home or Path.home()) / ".claude" / "skills" / SKILL
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        if dst.resolve() == src.resolve():
            return say(f"skill already linked: {dst}")
        dst.unlink()
    elif dst.exists():
        raise Refused(f"{dst} exists and is not this checkout's link — left as it is")
    dst.symlink_to(src)
    say(f"linked {dst} → {src}")


# ── the verb ─────────────────────────────────────────────────────────────
def short(sha):
    return (sha or "")[:7]


def record(result):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_name(STATUS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(dict(result, at=int(time.time())), indent=1),
                       encoding="utf-8")
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def run(a, say):
    """-> (exit code, result dict)."""
    st = hub_state(a.url)
    served = ((st or {}).get("hub") or {}).get("root")
    root = Path(a.dir or served or ROOT)
    res = {"root": str(root), "hub": "running" if st else "not answering"}
    if served and Path(served).resolve() != root.resolve():
        say(f"note: the running hub serves {served}, not {root}")
    p = plan(root, a.branch, fetch=True)
    res.update({k: p[k] for k in ("head", "target", "behind", "subjects")})
    stale = hub_is_stale(st, root) if st else False
    res["hub_stale"] = stale
    if p["behind"]:
        say(f"{root}: {p['behind']} commit(s) behind origin/{a.branch}")
        for s in p["subjects"][:15]:
            say(f"    {s}")
        if p["lockfile"]:
            say("    (the adapters' lockfile changed: npm ci will run)")
    else:
        say(f"{root}: up to date with origin/{a.branch} ({short(p['head'])})")
    if stale:
        say("the running hub is serving older code than this checkout")
    elif stale is None and st:
        say("could not tell which commit the running hub serves")
    need_restart = bool(st) and (p["behind"] > 0 or bool(stale)) and not a.no_restart
    if a.check:
        res["action"] = ("update" if p["behind"] else
                         "restart" if need_restart else "none")
        last = None
        try:
            last = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if last:
            say(f"last unattended run: {last.get('outcome')} "
                f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(last.get('at', 0)))})")
        return EXIT_OK, res
    if not p["behind"] and not need_restart:
        res["outcome"] = "current"
        return EXIT_OK, res

    own, inside = own_pane(), inside_hub()
    if need_restart and not a.now:
        deadline = time.time() + max(0, a.wait)
        while True:
            busy = blockers(st, own)
            if not busy or time.time() >= deadline:
                break
            time.sleep(POLL_S)
            st = hub_state(a.url) or {}
        if busy:
            say("not now — a restart would interrupt:")
            for b in busy:
                say(f"    {b['id']}  {b['title']}  ({b['why']})")
            say("nothing was changed; run again when they settle, or with --now")
            res.update(outcome="deferred", blockers=busy)
            return EXIT_DEFERRED, res

    if p["behind"]:
        apply(p, say)
    for t in p["templates"]:
        say(f"note: {t} changed — `corral-light install-service --print` shows "
            f"the new one; installed service files are not rewritten")
    if not need_restart:
        res["outcome"] = "updated" if p["behind"] else "current"
        if p["behind"] and not st:
            say("no hub answered; the next start loads the new code")
        elif a.no_restart and p["behind"]:
            say("--no-restart: the running hub keeps the old code until it restarts")
        return EXIT_OK, res
    if inside:
        say("restarting the hub from inside it: this pane's turn ends now; "
            "it resumes on your next message")
    done, msg = restart(inside)
    say(msg)
    res["outcome"] = ("updated" if p["behind"] else "restarted") if done else "restart-failed"
    if not done:
        return EXIT_REFUSED, res
    if not inside:
        if wait_healthy(a.url):
            st2 = hub_state(a.url)
            served2 = ((st2 or {}).get("hub") or {}).get("commit")
            want = git(root, "rev-parse", "HEAD")
            if served2 and served2 != want:
                say(f"the hub came back on {short(served2)}, not {short(want)}")
                res["outcome"] = "restart-failed"
                return EXIT_REFUSED, res
            say(f"hub answering on {short(want)}")
        else:
            say(f"the hub did not answer within {BOOT_WAIT_S}s — "
                f"journalctl --user -u {UNIT} -n 50")
            res["outcome"] = "restart-failed"
            return EXIT_REFUSED, res
    return EXIT_OK, res


def build_parser():
    ap = argparse.ArgumentParser(prog="corral-light update",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dir", help="the checkout to update (default: the hub's own)")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--now", action="store_true",
                    help="restart even if panes are mid-turn or waiting")
    ap.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="wait up to SECONDS for busy panes to settle")
    ap.add_argument("--no-restart", action="store_true",
                    help="pull only; the running hub keeps the old code")
    ap.add_argument("--unattended", action="store_true",
                    help="for the timer: record the outcome, notify on trouble")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--install-skill", action="store_true")
    ap.add_argument("--install-timer", action="store_true")
    ap.add_argument("--remove-timer", action="store_true")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    lines = []

    def say(msg):
        lines.append(msg)
        if not a.json:
            print(msg, flush=True)

    try:
        if a.install_skill or a.install_timer or a.remove_timer:
            root = Path(a.dir or ROOT)
            if a.install_skill:
                install_skill(root, say)
            if a.install_timer:
                install_timer(root, say)
            if a.remove_timer:
                remove_timer(say)
            return EXIT_OK
        code, res = run(a, say)
    except Refused as e:
        say(f"refused: {e}")
        code, res = EXIT_REFUSED, {"outcome": "refused", "why": str(e)}
    except (subprocess.SubprocessError, OSError) as e:
        say(f"failed: {type(e).__name__}: {e}")
        code, res = EXIT_REFUSED, {"outcome": "failed", "why": str(e)}
    if a.unattended:
        record(res)
        if res.get("outcome") in ("refused", "failed", "restart-failed"):
            try:
                import notify
                notify.desktop("Corral Light update", lines[-1] if lines else res["outcome"])
            except Exception:                               # noqa: BLE001
                pass
    if a.json:
        print(json.dumps(dict(res, lines=lines), indent=2), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
