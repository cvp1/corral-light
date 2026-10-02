#!/usr/bin/python3
"""lanes — keep the vendor lanes current.

    corral-light lanes check [--job | --json]
    corral-light lanes update <lane> [--version V | --release NAME] [--json]

An update stages the new adapter, probes it with a real session on a private
hub, and moves exactly one pin only if the probe passes.
"""
import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRASH = Path.home() / ".Trash"

NPM = {
    "codex": {"pkg": "@agentclientprotocol/codex-acp", "bin": "codex-acp",
              "env": "CORRAL_CODEX_ACP"},
    "claude": {"pkg": "@agentclientprotocol/claude-agent-acp", "bin": "claude-agent-acp",
               "env": "CORRAL_CLAUDE_ADAPTER"},
}
ALIASES = {"chatgpt": "codex", "antigravity": "gemini", "agy": "gemini"}
LANES = ("codex", "claude", "gemini", "grok")

RELEASE_RE = re.compile(r"agy_acp_server_\d{8}_\d{2}_RC\d{2}")
PROBE_PROMPT = "Reply with exactly the one word: pong"
NPM_TIMEOUT_S = 600
CHECK_TIMEOUT_S = 60
HUB_UP_S = 30
ASK_TIMEOUT_S = 180
PROBE_TIMEOUT_S = 300           # the whole probe client, handshake included
MAX_REPLY = 200                 # chars of the reply kept in the record


class Red(RuntimeError):
    """The update cannot go green; nothing has been changed."""


def lane_key(name):
    key = ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
    if key not in LANES:
        raise Red(f"no such lane {name!r}; lanes are {', '.join(LANES)}")
    return key


def _npm():
    """npm by absolute path; launchd's minimal PATH does not include it."""
    for c in (shutil.which("npm"), "/opt/homebrew/bin/npm", "/usr/local/bin/npm"):
        if c and os.access(c, os.X_OK):
            return c
    return "npm"


def _run(argv, timeout, cwd=None, env=None):
    """A fixed argv, never a shell string. Returns (rc, stdout, stderr)."""
    if argv and argv[0] == "npm":
        # npm is a node script: its own directory has to be on PATH too.
        npm = _npm()
        argv = [npm, *argv[1:]]
        env = dict(env or os.environ)
        env["PATH"] = os.path.dirname(npm) + os.pathsep + env.get("PATH", "")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, env=env)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", f"{type(e).__name__}: {e}"


def stamp():
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


# ── versions ──────────────────────────────────────────────────────────────
def npm_installed(root, lane):
    pkg = NPM[lane]["pkg"]
    try:
        return json.loads((root / "spike" / "node_modules" / pkg / "package.json")
                          .read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None


def npm_latest(lane, run=_run):
    rc, out, err = run(["npm", "view", NPM[lane]["pkg"], "version"], CHECK_TIMEOUT_S)
    v = out.strip()
    if rc != 0 or not re.fullmatch(r"\d+\.\d+\.\d+(-[\w.]+)?", v):
        raise Red(f"npm view failed: {(err or out).strip()[:200] or f'rc {rc}'}")
    return v


def grok_check(run=_run):
    import grok_launcher
    grok = grok_launcher.resolve_grok()
    if not grok:
        raise Red("grok CLI not found")
    rc, out, err = run([grok, "update", "--check", "--json"], CHECK_TIMEOUT_S)
    try:
        d = json.loads(out)
        return d["currentVersion"], d["latestVersion"]
    except (ValueError, KeyError, TypeError):
        raise Red(f"grok update --check gave no version: {(err or out).strip()[:200]}")


# ── staging ───────────────────────────────────────────────────────────────
def stage_npm(root, lane, version, run=_run):
    """Build the pinned tree in a scratch dir beside the live one (same
    filesystem, so the swap is a rename)."""
    spike = root / "spike"
    scratch = Path(tempfile.mkdtemp(prefix=".lanes-stage-", dir=spike))
    try:
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(spike / name, scratch / name)
        pkg = NPM[lane]["pkg"]
        rc, out, err = run(["npm", "install", "--prefix", str(scratch), f"{pkg}@{version}",
                            "--save-exact", "--no-audit", "--no-fund"], NPM_TIMEOUT_S)
        if rc != 0:
            raise Red(f"npm install failed (rc {rc}): {(err or out).strip()[-300:]}")
        check_one_pin(spike / "package.json", scratch / "package.json", pkg, version)
        adapter = scratch / "node_modules" / ".bin" / NPM[lane]["bin"]
        if not adapter.exists():
            raise Red(f"staged tree has no {adapter.name}")
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return scratch, {NPM[lane]["env"]: str(adapter)}


def check_one_pin(old_path, new_path, pkg, version):
    """Exactly one dependency changed, and it is now exactly `version`."""
    old = json.loads(Path(old_path).read_text())
    new = json.loads(Path(new_path).read_text())
    od, nd = old.get("dependencies", {}), new.get("dependencies", {})
    changed = sorted(k for k in set(od) | set(nd) if od.get(k) != nd.get(k))
    rest_same = ({k: v for k, v in old.items() if k != "dependencies"}
                 == {k: v for k, v in new.items() if k != "dependencies"})
    if changed != [pkg] or nd.get(pkg) != version or not rest_same:
        raise Red(f"staged package.json changes {changed or 'nothing'} "
                  f"(want exactly {pkg} -> {version})")


def stage_release(release, fetch=None):
    """Fetch a named Antigravity release beside the runtime; TOFU digest."""
    import install_antigravity_acp as inst
    row = inst.release_for()
    if row is None:
        raise Red(inst.platform_problem())
    if not RELEASE_RE.fullmatch(release):
        raise Red(f"not a release name: {release!r} (want agy_acp_server_YYYYMMDD_NN_RCNN)")
    suffix = row["release"][len(RELEASE_RE.match(row["release"]).group(0)):]
    new = dict(row, release=release + suffix)
    new["url"] = f"{inst.BASE_URL}{row['dir']}/agy-acp-server-{new['release']}.zip"
    inst.RUNTIME.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".lanes-stage-", dir=inst.RUNTIME.parent))
    try:
        extract, digest = (fetch or inst.fetch)(new, scratch, None)
    except Exception as e:                       # noqa: BLE001
        shutil.rmtree(scratch, ignore_errors=True)
        raise Red(f"fetch {new['release']} failed: {e}")
    new["sha256"] = digest
    return scratch, extract, row, new, {
        "CORRAL_ANTIGRAVITY_ACP_BINARY": str(extract / inst.FILES[0])}


# ── the probe ─────────────────────────────────────────────────────────────
_HUB_WRAPPER = (
    "import runpy, sys; sys.path.insert(0, sys.argv[1]); import notify\n"
    "notify.desktop = lambda *a, **k: (False, 'muted: lanes probe hub')\n"
    "sys.argv = [sys.argv[1] + '/hub.py']\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def probe(root, lane, overrides):
    """Handshake + one real prompt + model list on a private hub, stopped by
    its own process group so the live hub is never matched. Returns a record."""
    state = Path(tempfile.mkdtemp(prefix="corral-lanes-probe-"))
    cwd = state / "cwd"
    cwd.mkdir()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, **overrides, CORRAL_LIGHT_STATE=str(state),
               CORRAL_LIGHT_BIND="127.0.0.1", CORRAL_LIGHT_PORT=str(port),
               CORRAL_LIGHT_URL=url,
               CORRAL_LIGHT_CONSULT_CFG=str(state / "consult-session.json"))
    t0 = time.time()
    rec = {"lane": lane, "overrides": overrides, "ok": False}
    log = (state / "hub.log").open("wb")
    hub = subprocess.Popen([sys.executable, "-c", _HUB_WRAPPER, str(root)], env=env,
                           cwd=str(root), stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        if not _wait_up(url, hub):
            rec["why"] = "private hub did not come up: " + _tail(state / "hub.log")
            return rec
        rc, out, err = _run([sys.executable, str(root / "lanes.py"), "_probe-client",
                             lane, url, str(cwd)], PROBE_TIMEOUT_S, cwd=str(root), env=env)
        try:
            rec.update(json.loads(out.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            rec["why"] = f"probe client gave no record (rc {rc}): {(err or out).strip()[-300:]}"
    finally:
        _stop(hub)
        log.close()
        rec["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(state, ignore_errors=True)
    return rec


def _wait_up(url, hub):
    deadline = time.time() + HUB_UP_S
    while time.time() < deadline:
        if hub.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url + "/health", timeout=2):
                return True
        except Exception:                                # noqa: BLE001
            time.sleep(0.5)
    return False


def _stop(hub):
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        if hub.poll() is not None:
            break
        try:
            os.killpg(hub.pid, sig)
        except ProcessLookupError:
            break
        try:
            hub.wait(wait)
        except subprocess.TimeoutExpired:
            continue


def _tail(path, n=300):
    try:
        return Path(path).read_text(errors="replace")[-n:].strip()
    except OSError:
        return "(no log)"


def probe_client(lane, url, cwd):
    """Run inside the probe's subprocess environment so all state is the
    private hub's; returns one record."""
    import lane_probe
    import consult
    rec = {"handshake": False, "reply": "", "models": [], "model": None, "ok": False}
    hs = lane_probe.probe(lane, force=True)
    rec["handshake"] = bool(hs.get("ok"))
    if not hs.get("ok"):
        rec["why"] = f"handshake: {hs.get('error') or 'failed'}"
        return rec
    hub = consult.connect(url)
    pane = consult.open_pane(hub, lane, cwd, title="lanes update probe")
    pid = pane["id"]
    try:
        r = consult.send_and_wait(hub, pid, PROBE_PROMPT, ASK_TIMEOUT_S)
        rec["reply"] = (r.get("text") or "")[:MAX_REPLY]
        p = consult._pane_in(consult._state(hub, {pid: 1 << 40}), pid) or {}
        model = (p.get("config") or {}).get("model") or {}
        rec["model"] = model.get("value") or p.get("model")
        rec["models"] = [o.get("value") for o in model.get("options") or [] if o.get("value")]
        if not (r.get("complete") and rec["reply"].strip()):
            rec["why"] = f"prompt did not round-trip: {r.get('why') or 'no complete reply'}"
        elif not rec["models"]:
            rec["why"] = "no model list read back from the pane"
        else:
            rec["ok"] = True
    finally:
        try:
            hub.post("/api/session/close", {"pane": pid})
        except Exception:                                # noqa: BLE001
            pass
    return rec


# ── the swap ──────────────────────────────────────────────────────────────
def swap_npm(root, scratch, label, trash=TRASH):
    """Swap the staged tree into spike/node_modules with its package.json and
    lock; the replaced tree goes to the Trash."""
    spike = root / "spike"
    live = spike / "node_modules"
    trash.mkdir(parents=True, exist_ok=True)
    prev = trash / f"node_modules-{label}-{stamp()}"
    os.rename(live, prev)
    try:
        os.rename(scratch / "node_modules", live)
    except OSError:
        os.rename(prev, live)
        raise
    for name in ("package.json", "package-lock.json"):
        os.replace(scratch / name, spike / name)
    shutil.rmtree(scratch, ignore_errors=True)
    return prev


def swap_release(installer_path, old, new, extract, trash=TRASH):
    """Rewrite this host's row in the installer, then the runtime."""
    import install_antigravity_acp as inst
    text = Path(installer_path).read_text()
    for key in ("release", "sha256"):
        if text.count(f'"{old[key]}"') != 1:
            raise Red(f"installer row {key} {old[key]!r} is not unique; not editing")
    text = text.replace(f'"release": "{old["release"]}"', f'"release": "{new["release"]}"')
    text = text.replace(f'"sha256": "{old["sha256"]}",',
                        f'"sha256": "{new["sha256"]}",  # TOFU {stamp()[:10]}, lanes update')
    trash.mkdir(parents=True, exist_ok=True)
    prev = None
    if inst.RUNTIME.exists():
        prev = trash / f"antigravity-acp-{old['release']}-{stamp()}"
        os.rename(inst.RUNTIME, prev)
    try:
        os.replace(extract, inst.RUNTIME)
    except OSError:
        if prev is not None:
            os.rename(prev, inst.RUNTIME)
        raise
    tmp = Path(installer_path).with_name(Path(installer_path).name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, installer_path)
    return prev


# ── update ────────────────────────────────────────────────────────────────
def update(lane, root=ROOT, version=None, release=None, run=_run, probe_fn=probe,
           notify_fn=None, trash=TRASH, fetch=None):
    """Returns a record: {"lane", "outcome": current|updated|red|unknown|
    checked, "installed", "latest", "probe", "why"}. Never raises Red."""
    rec = {"lane": lane, "outcome": "red", "installed": None, "latest": None}
    scratch = None
    try:
        lane = rec["lane"] = lane_key(lane)
        if lane == "grok":
            rec["installed"], rec["latest"] = grok_check(run)
            rec["probe"] = probe_fn(root, lane, {})
            if not rec["probe"].get("ok"):
                raise Red(f"probe: {rec['probe'].get('why')}")
            rec["outcome"] = "checked"
            rec["why"] = ("current" if rec["installed"] == rec["latest"] else
                          f"behind: the Grok CLI updates itself; run `grok update` "
                          f"to move {rec['installed']} -> {rec['latest']}")
            return rec

        if lane == "gemini":
            import install_antigravity_acp as inst
            row = inst.release_for()
            rec["installed"] = row["release"] if row else None
            if not release:
                rec["outcome"] = "unknown"
                rec["why"] = ("Google publishes no index of Antigravity releases; "
                              "name one with --release")
                return rec
            rec["latest"] = release
            if row and row["release"].startswith(release + "-"):
                rec["outcome"], rec["why"] = "current", "already pinned"
                return rec
            scratch, extract, old, new, overrides = stage_release(release, fetch)
            rec["latest"] = new["release"]
            rec["probe"] = probe_fn(root, lane, overrides)
            if not rec["probe"].get("ok"):
                raise Red(f"probe: {rec['probe'].get('why')}")
            rec["replaced"] = str(swap_release(root / "install_antigravity_acp.py",
                                               old, new, extract, trash) or "")
            rec["sha256"] = new["sha256"]
            rec["outcome"] = "updated"
            return rec

        rec["installed"] = npm_installed(root, lane)
        rec["latest"] = version or npm_latest(lane, run)
        if rec["installed"] == rec["latest"]:
            rec["outcome"], rec["why"] = "current", "installed is latest"
            return rec
        scratch, overrides = stage_npm(root, lane, rec["latest"], run)
        rec["probe"] = probe_fn(root, lane, overrides)
        if not rec["probe"].get("ok"):
            raise Red(f"probe: {rec['probe'].get('why')}")
        rec["replaced"] = str(swap_npm(root, scratch, f"{lane}-{rec['installed']}", trash))
        scratch = None
        rec["outcome"] = "updated"
        return rec
    except Red as e:
        rec["outcome"], rec["why"] = "red", str(e)
        (notify_fn or _notify)(f"lanes update {rec['lane']}: not updated", str(e))
        return rec
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _notify(title, body):
    try:
        import notify
        notify.desktop(f"Corral Light — {title}", body)
    except Exception:                                    # noqa: BLE001
        pass


def render(rec):
    p = rec.get("probe") or {}
    lines = [f"{rec['lane']}: {rec['outcome']}  installed {rec.get('installed')}  "
             f"latest {rec.get('latest')}"]
    if rec.get("why"):
        lines.append(f"  why: {rec['why']}")
    if p:
        lines.append(f"  probe: handshake {'ok' if p.get('handshake') else 'FAILED'}, "
                     f"reply {p.get('reply', '')!r}, model {p.get('model')}, "
                     f"{len(p.get('models') or [])} models, {p.get('seconds')} s")
        if p.get("models"):
            lines.append("  models: " + ", ".join(p["models"]))
    if rec.get("replaced"):
        lines.append(f"  replaced tree moved to {rec['replaced']}")
    return "\n".join(lines)


# ── check: installed against latest, read only ───────────────────────────
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
CHECK_STATE = "lanes-check.json"
MAX_HEADS = 45                  # Antigravity dates scanned per check
HEAD_TIMEOUT_S = 10


def _head(url):
    """HTTP status of a HEAD, or None when the request itself failed."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=HEAD_TIMEOUT_S) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:                                    # noqa: BLE001
        return None


def check_gemini(head=_head, today=None):
    """No release index exists, so HEAD `<date>_01_RC01` for each day after
    the pin, newest first. Found is `behind`; none found is `unknown`, never
    `current` (other _NN/_RCNN builds are not probed)."""
    import install_antigravity_acp as inst
    row = inst.release_for()
    if row is None:
        return {"status": "unknown", "installed": None, "latest": None,
                "why": "no pinned row for this platform"}
    rec = {"installed": row["release"], "latest": None}
    pinned = head(row["url"])
    if pinned != 200:
        return dict(rec, status="unknown",
                    why=f"pinned archive HEAD gave {pinned or 'no answer'}")
    m = re.match(r"agy_acp_server_(\d{8})_", row["release"])
    pin_day = datetime.strptime(m.group(1), "%Y%m%d").date()
    suffix = row["release"][len(RELEASE_RE.match(row["release"]).group(0)):]
    day = today or datetime.now().date()
    asked = 0
    while day > pin_day and asked < MAX_HEADS:
        name = f"agy_acp_server_{day:%Y%m%d}_01_RC01"
        code = head(f"{inst.BASE_URL}{row['dir']}/agy-acp-server-{name}{suffix}.zip")
        asked += 1
        if code == 200:
            return dict(rec, status="behind", latest=name + suffix,
                        why=f"lanes update gemini --release {name}")
        if code != 404:
            return dict(rec, status="unknown", why=f"HEAD {name} gave {code or 'no answer'}")
        day = datetime.fromordinal(day.toordinal() - 1).date()
    return dict(rec, status="unknown",
                why=f"no index; no newer _01_RC01 build in {asked} days asked")


def check(root=ROOT, run=_run, head=_head, today=None):
    """One row per lane; a check that fails is `unknown`, never `current`."""
    rows = []
    for lane in LANES:
        try:
            if lane in NPM:
                installed, latest = npm_installed(root, lane), npm_latest(lane, run)
            elif lane == "grok":
                installed, latest = grok_check(run)
            else:
                rows.append(dict(check_gemini(head, today), lane=lane))
                continue
            if not installed:
                rows.append({"lane": lane, "status": "unknown", "installed": None,
                             "latest": latest, "why": "not installed"})
                continue
            behind = installed != latest
            rows.append({"lane": lane, "status": "behind" if behind else "current",
                         "installed": installed, "latest": latest,
                         "why": ("run `grok update`" if lane == "grok" else
                                 f"lanes update {lane}") if behind else ""})
        except Red as e:
            rows.append({"lane": lane, "status": "unknown", "installed": None,
                         "latest": None, "why": str(e)})
        except Exception as e:                           # noqa: BLE001
            rows.append({"lane": lane, "status": "unknown", "installed": None,
                         "latest": None, "why": f"{type(e).__name__}: {e}"[:200]})
    return rows


def edges(rows, prev):
    """Notices for lanes that became behind (or whose latest moved while
    behind) or returned to current; `unknown` never notifies."""
    out = []
    for r in rows:
        was = prev.get(r["lane"]) or {}
        if r["status"] == "behind" and (was.get("status") != "behind"
                                        or was.get("latest") != r["latest"]):
            out.append(f"{r['lane']} is behind: {r['installed']} -> {r['latest']} "
                       f"({r['why']})")
        elif r["status"] == "current" and was.get("status") == "behind":
            out.append(f"{r['lane']} is current again at {r['installed']}")
    return out


def run_job(rows, state_dir=None, notify_fn=None, now=None):
    """Notify once per edge, persist state, and return job stdout (empty when
    all lanes are current and nothing changed)."""
    path = Path(state_dir or STATE) / CHECK_STATE
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    notices = edges(rows, prev)
    for n in notices:
        (notify_fn or _notify)("lanes", n)
    at = (now or datetime.now()).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({r["lane"]: {"status": r["status"], "latest": r["latest"],
                                           "at": at} for r in rows}, indent=2))
    os.replace(tmp, path)
    off = [r for r in rows if r["status"] != "current"]
    if not off and not notices:
        return ""
    summary = ", ".join(f"{r['lane']} {r['status']}" for r in off) or "all current"
    lines = [f"FINDINGS: {summary}"] if off else [summary]
    lines += [render_check_row(r) for r in rows]
    return "\n".join(lines)


def render_check_row(r):
    tail = f"  ({r['why']})" if r.get("why") else ""
    return (f"  {r['lane']:<7} {r['status']:<8} installed {r.get('installed')}  "
            f"latest {r.get('latest')}{tail}")


def _lane_arg(name):
    try:
        return lane_key(name)
    except Red as e:                 # a usage error, not a red to notify
        raise argparse.ArgumentTypeError(str(e))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_probe-client"]:
        print(json.dumps(probe_client(*argv[1:4])), flush=True)
        return 0
    ap = argparse.ArgumentParser(prog="corral-light lanes",
                                 description="keep the vendor lanes current")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check", help="installed against latest for every lane; read only")
    s.add_argument("--job", action="store_true",
                   help="scheduled mode: notify once per edge, print only FINDINGS")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("update", help="stage, probe on a private hub, then move one pin")
    s.add_argument("lane", type=_lane_arg, help="codex | claude | gemini | grok")
    s.add_argument("--version", help="npm lanes: this version instead of latest")
    s.add_argument("--release", help="gemini: the release to stage, e.g. "
                                     "agy_acp_server_20260818_01_RC01")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        rows = check()
        if a.job:
            out = run_job(rows)
            if out:
                print(out, flush=True)
        elif a.json:
            print(json.dumps(rows, indent=2), flush=True)
        else:
            print("\n".join(render_check_row(r) for r in rows), flush=True)
        return 0
    rec = update(a.lane, version=a.version, release=a.release)
    print(json.dumps(rec, indent=2) if a.json else render(rec), flush=True)
    return 1 if rec["outcome"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
