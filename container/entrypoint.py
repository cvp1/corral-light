#!/usr/bin/env python3
"""Container entrypoint (WS2): identity, PATH, parity map, SSH, overlays,
hostname — then drop to the host user and exec the hub.

Runs as root for exactly the identity step, then never again.

RULES (docs/container-implementation-plan.md WS2):
  - Same UID/GID/home path as the host user, so getpwuid(), Path.home() and
    `~` agree with the host (T-ID-1).
  - PATH is the image's, unconditionally. A compose file or env cannot put a
    host bin dir on it (T-ID-3).
  - Never write a host path. Everything this writes lives in the image
    (/etc/passwd, /etc/group) or on container-only storage (/run/corral,
    /var/lib/corral). The ONE chown it does is the home directory itself, and
    only when root owns it — that is a fresh `corral-home` volume, never a
    host bind mount (those arrive owned by the host user).
  - Report, never gate (C2). Every check lands in /run/corral/entrypoint.json
    for `doctor`; nothing here refuses to start the hub except missing
    identity, without which nothing is the user's.

Environment (set by the compose file install.sh renders — runtime config,
never baked into the public image):
  HOST_UID HOST_GID HOST_USER HOST_HOME    required
  HOST_HOSTNAME                            expected `hostname` (report on mismatch)
  CORRAL_PARITY_MAP                        JSON file: [{"path": ..., "expect": ...}]
  CORRAL_OVERLAYS                          ':'-separated paths compose overlays
  CORRAL_WORKSPACE                         default pane directory (hub reads it)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

IMAGE_PATH = "/opt/corral/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin"
RUN = Path(os.environ.get("CORRAL_RUN_DIR", "/run/corral"))
STATE_ROOT = Path("/var/lib/corral")
HUB = ["/usr/local/bin/python3", "/opt/corral-light/hub.py"]
DOCKER_DESKTOP_AGENT = "/run/host-services/ssh-auth.sock"
# macOS-only ssh options a Mac ~/.ssh/config commonly carries; Linux ssh
# refuses the whole file on an unknown option unless told to ignore it.
SSH_IGNORE = "IgnoreUnknown UseKeychain,AddKeysToAgent,UseRoaming"
HOST_BIN_HINTS = ("/opt/homebrew", "/usr/local/Cellar", "/usr/local/opt",
                  "/Applications", "/System", "/Library")


class IdentityError(SystemExit):
    pass


# ── identity ──────────────────────────────────────────────────────────────

def identity(env) -> dict:
    missing = [k for k in ("HOST_UID", "HOST_GID", "HOST_USER", "HOST_HOME")
               if not env.get(k)]
    if missing:
        raise IdentityError("corral entrypoint: missing " + ", ".join(missing)
                            + " — the compose file install.sh renders sets them")
    try:
        uid, gid = int(env["HOST_UID"]), int(env["HOST_GID"])
    except ValueError:
        raise IdentityError("corral entrypoint: HOST_UID/HOST_GID must be integers")
    if uid == 0:
        raise IdentityError("corral entrypoint: refusing HOST_UID=0; panes run as "
                            "the host user, never root")
    home = env["HOST_HOME"]
    if not home.startswith("/") or ":" in home or "\n" in home:
        raise IdentityError(f"corral entrypoint: HOST_HOME {home!r} is not a usable "
                            f"absolute path")
    user = env["HOST_USER"]
    if not user or any(c in user for c in ":\n/ "):
        raise IdentityError(f"corral entrypoint: HOST_USER {user!r} is not a usable name")
    return {"uid": uid, "gid": gid, "user": user, "home": home}


def merge_group(lines: list[str], gid: int, name: str) -> tuple[list[str], str]:
    """Keep an existing group with this gid (macOS staff=20 is Debian's
    `dialout`; the NUMBER is what file ownership uses). Else add one."""
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 3 and parts[2] == str(gid):
            return lines, parts[0]
    taken = {l.split(":")[0] for l in lines if l}
    gname = name if name not in taken else f"{name}-host"
    return lines + [f"{gname}:x:{gid}:"], gname


def merge_passwd(lines: list[str], ident: dict) -> list[str]:
    """Drop any entry holding this uid or name, then add ours. The image's
    own entries (root, daemon...) stay; a clash with one of them on NAME is
    the user's name winning, which is what parity means."""
    keep = []
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 3 and (parts[0] == ident["user"] or parts[2] == str(ident["uid"])):
            continue
        keep.append(line)
    return keep + [f"{ident['user']}:x:{ident['uid']}:{ident['gid']}:"
                   f"{ident['user']}:{ident['home']}:/bin/bash"]


def write_identity(ident: dict, etc: Path = Path("/etc")) -> str:
    g = (etc / "group").read_text().splitlines()
    g, gname = merge_group(g, ident["gid"], ident["user"])
    (etc / "group").write_text("\n".join(g) + "\n")
    p = merge_passwd((etc / "passwd").read_text().splitlines(), ident)
    (etc / "passwd").write_text("\n".join(p) + "\n")
    return gname


def prepare_dirs(ident: dict) -> list[str]:
    notes = []
    home = Path(ident["home"])
    if not home.exists():
        home.mkdir(parents=True, mode=0o755)
        os.chown(home, ident["uid"], ident["gid"])
        notes.append(f"home {home} did not exist; created in the container "
                     f"(no corral-home volume mounted?)")
    elif home.stat().st_uid == 0:
        os.chown(home, ident["uid"], ident["gid"])     # fresh volume only
    for d in (STATE_ROOT, STATE_ROOT / "state", STATE_ROOT / "ssh", RUN):
        d.mkdir(parents=True, exist_ok=True)
        if d.stat().st_uid != ident["uid"]:
            os.chown(d, ident["uid"], ident["gid"])
    return notes


# ── PATH ──────────────────────────────────────────────────────────────────

def path_report(path: str, home: str) -> dict:
    bad = [p for p in path.split(":")
           if p and (p.startswith(HOST_BIN_HINTS) or p.startswith(home + "/"))]
    return {"path": path, "host_dirs": bad, "ok": not bad}


# ── parity map ────────────────────────────────────────────────────────────

def parity_report(map_file: str | None) -> list[dict]:
    """Each mapped interpreter path must exist IN THE CONTAINER (compose
    mounts a container-only file/volume there) and execute as Linux. Never
    written to: a missing one is reported for doctor."""
    if not map_file:
        return []
    try:
        entries = json.loads(Path(map_file).read_text())
    except (OSError, ValueError) as e:
        return [{"map": map_file, "ok": False, "why": f"unreadable: {e}"}]
    out = []
    for e in entries if isinstance(entries, list) else []:
        p = str(e.get("path", ""))
        r = {"path": p, "expect": e.get("expect"), "ok": False}
        if not os.access(p, os.X_OK):
            r["why"] = "not present or not executable in the container"
        else:
            try:
                cp = subprocess.run([p, "--version"], capture_output=True,
                                    text=True, timeout=10)
                r["version"] = (cp.stdout or cp.stderr).strip()[:200]
                r["ok"] = cp.returncode == 0
                if not r["ok"]:
                    r["why"] = f"--version exited {cp.returncode}"
            except OSError as ex:     # Mach-O on Linux = Exec format error
                r["why"] = f"does not execute here: {ex.strerror or ex}"
            except subprocess.TimeoutExpired:
                r["why"] = "--version timed out"
        out.append(r)
    return out


# ── SSH ───────────────────────────────────────────────────────────────────

def ssh_config(home: str, agent: str | None, host_config_readable: bool) -> str:
    lines = [SSH_IGNORE,
             f"UserKnownHostsFile {STATE_ROOT}/ssh/known_hosts {home}/.ssh/known_hosts"]
    if agent:
        lines.append(f"IdentityAgent {agent}")
    if host_config_readable:
        lines.append(f"Include {home}/.ssh/config")
    return "\n".join(lines) + "\n"


def ssh_setup(ident: dict, env) -> dict:
    home = ident["home"]
    agent = None
    for cand in (env.get("SSH_AUTH_SOCK"), DOCKER_DESKTOP_AGENT):
        if cand and Path(cand).is_socket():
            agent = cand
            break
    host_cfg = Path(home, ".ssh", "config")
    readable = host_cfg.is_file() and os.access(host_cfg, os.R_OK)
    cfg = RUN / "ssh_config"
    cfg.write_text(ssh_config(home, agent, readable))
    os.chown(cfg, ident["uid"], ident["gid"])
    return {"config": str(cfg), "agent": agent, "host_config": readable,
            "known_hosts_ro": Path(home, ".ssh", "known_hosts").is_file(),
            "credential_helper": "deferred to WS5 (needs corral-host-shell)"}


# ── overlays, hostname, emulation ─────────────────────────────────────────

def mountpoints(mountinfo: str = "/proc/self/mountinfo") -> set[str]:
    try:
        text = Path(mountinfo).read_text()
    except OSError:
        return set()
    return {line.split()[4].replace("\\040", " ") for line in text.splitlines()
            if len(line.split()) > 4}


def overlay_report(spec: str | None, mounts: set[str]) -> list[dict]:
    return [{"path": p, "mounted": p in mounts}
            for p in (spec or "").split(":") if p]


def emulation() -> dict:
    """Is this amd64 process emulated? Rosetta's Linux runtime reports
    'VirtualApple' as the CPU vendor; QEMU user mode shows the host's
    model via a qemu string. Native x86 shows neither."""
    try:
        cpu = Path("/proc/cpuinfo").read_text(errors="replace")
    except OSError:
        cpu = ""
    vendor = next((l.split(":", 1)[1].strip() for l in cpu.splitlines()
                   if l.startswith("vendor_id")), "")
    if "VirtualApple" in vendor or Path("/run/rosetta").exists():
        mode = "rosetta"
    elif "qemu" in cpu.lower():
        mode = "qemu"
    else:
        mode = "native"
    return {"machine": os.uname().machine, "vendor_id": vendor, "mode": mode}


def hostname_report(expected: str | None) -> dict:
    actual = socket.gethostname()
    return {"expected": expected, "actual": actual,
            "ok": (not expected) or actual == expected}


# ── main ──────────────────────────────────────────────────────────────────

def build_report(env) -> tuple[dict, dict]:
    ident = identity(env)
    gname = write_identity(ident)
    notes = prepare_dirs(ident)
    report = {
        "identity": ident | {"group": gname},
        "path": path_report(IMAGE_PATH, ident["home"]),
        "parity_map": parity_report(env.get("CORRAL_PARITY_MAP")),
        "ssh": ssh_setup(ident, env),
        "overlays": overlay_report(env.get("CORRAL_OVERLAYS"), mountpoints()),
        "hostname": hostname_report(env.get("HOST_HOSTNAME")),
        "emulation": emulation(),
        "workspace": env.get("CORRAL_WORKSPACE"),
        "notes": notes,
    }
    return ident, report


def child_env(env, ident: dict, report: dict) -> dict:
    e = {k: v for k, v in env.items() if k not in ("PATH", "HOME", "USER", "LOGNAME")}
    e.update(PATH=IMAGE_PATH, HOME=ident["home"], USER=ident["user"],
             LOGNAME=ident["user"], SHELL="/bin/bash",
             GIT_SSH_COMMAND=f"ssh -F {report['ssh']['config']}")
    return e


def main(argv: list[str]) -> int:
    env = dict(os.environ)
    if os.getuid() != 0:
        raise IdentityError("corral entrypoint: must start as root to set the "
                            "identity; do not set `user:` in compose")
    ident, report = build_report(env)
    out = RUN / "entrypoint.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    os.chown(out, ident["uid"], ident["gid"])
    for line in summary(report):
        print("corral entrypoint: " + line, file=sys.stderr, flush=True)

    cmd = HUB if (not argv or argv == ["hub"]) else argv
    os.chdir(ident["home"])
    drop = ["setpriv", f"--reuid={ident['uid']}", f"--regid={ident['gid']}",
            "--init-groups", "--"]
    os.execvpe(drop[0], drop + cmd, child_env(env, ident, report))
    return 127


def summary(report: dict) -> list[str]:
    i, e = report["identity"], report["emulation"]
    lines = [f"user {i['user']} uid={i['uid']} gid={i['gid']}({i['group']}) home={i['home']}",
             f"cpu {e['machine']} emulation={e['mode']}"]
    if not report["path"]["ok"]:
        lines.append(f"! host dirs on PATH: {report['path']['host_dirs']}")
    if not report["hostname"]["ok"]:
        h = report["hostname"]
        lines.append(f"! hostname {h['actual']!r} != expected {h['expected']!r}")
    for p in report["parity_map"]:
        if not p.get("ok"):
            lines.append(f"! parity map {p.get('path') or p.get('map')}: {p.get('why')}")
    for o in report["overlays"]:
        if not o["mounted"]:
            lines.append(f"! overlay not mounted: {o['path']}")
    lines += ["! " + n for n in report["notes"]]
    return lines


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
