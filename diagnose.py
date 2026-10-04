#!/usr/bin/python3
"""diagnose — run one lane exactly as a pane does, and report every difference.

Unlike `doctor`, this sends a real prompt (a token or two) and reports what
differs between a pane and a terminal: config dir, environment, working
directory. Prints variable names and file sizes only, never secret values.
"""
import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import acp
import sessions


SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")

# --- WHICH TREE IS ACTUALLY RUNNING ------------------------------------------
# A LaunchAgent pointed at a worktree lacks the gitignored spike/node_modules/,
# so the Claude and ChatGPT lanes vanish. Report what launchd will run and
# what it is running now.
DEFAULT_LAUNCHD_LABEL = "com.cvp1.corral-light"   # what install_service writes


def installed_label(agents=None, env=None):
    """The label of the corral-light LaunchAgent actually installed here.

    CORRAL_LAUNCHD_LABEL wins. Else the default label if its plist exists,
    else any `*.corral-light.plist` in ~/Library/LaunchAgents (a host
    installed before the 2026-10-01 rename keeps its old label until it is
    reinstalled -- diagnosing it must not depend on the name it was given).
    """
    env = os.environ if env is None else env
    if env.get("CORRAL_LAUNCHD_LABEL"):
        return env["CORRAL_LAUNCHD_LABEL"]
    agents = Path(agents or Path.home() / "Library" / "LaunchAgents")
    if (agents / f"{DEFAULT_LAUNCHD_LABEL}.plist").exists():
        return DEFAULT_LAUNCHD_LABEL
    found = sorted(p.stem for p in agents.glob("*.corral-light.plist"))
    return found[0] if found else DEFAULT_LAUNCHD_LABEL


LAUNCHD_LABEL = installed_label()
INSTALLED_PLIST = (Path.home() / "Library" / "LaunchAgents"
                   / f"{LAUNCHD_LABEL}.plist")


def installed_service_trees(path=INSTALLED_PLIST):
    """Every tree the installed LaunchAgent would run out of, resolved.
    `[]` when no agent is installed (not a fault)."""
    try:
        with open(path, "rb") as fh:
            data = plistlib.load(fh)
    except Exception:                               # noqa: BLE001 — absent, or not a plist
        return []
    trees = set()
    for arg in data.get("ProgramArguments") or []:
        if isinstance(arg, str) and arg.endswith(".py"):
            trees.add(Path(arg).resolve().parent)
    wd = data.get("WorkingDirectory")
    if isinstance(wd, str) and wd:
        trees.add(Path(wd).resolve())
    return sorted(trees)


def running_service_tree(label=LAUNCHD_LABEL):
    """The tree the live hub is running from, or None if it isn't running."""
    if sys.platform != "darwin":
        return None
    try:
        printed = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=10).stdout
        pid = next(ln.split("=", 1)[1].strip() for ln in printed.splitlines()
                   if ln.strip().startswith("pid ="))
        args = subprocess.run(["ps", "-p", pid, "-o", "args="],
                              capture_output=True, text=True, timeout=10).stdout
    except Exception:                               # noqa: BLE001 — never a blocker
        return None
    for token in args.split():
        if token.endswith(".py"):
            return Path(token).resolve().parent
    return None


def service_tree_problem(root=None, path=INSTALLED_PLIST, label=LAUNCHD_LABEL):
    """Why the service is not running this tree, or None if it is (or if no
    service is installed here)."""
    root = Path(root or sessions.ROOT).resolve()
    stray = [t for t in installed_service_trees(path) if t != root]
    live = running_service_tree(label)
    problems = []
    if stray:
        problems.append(
            f"the installed LaunchAgent runs {stray[0]}, not {root}")
    if live is not None and live != root:
        problems.append(f"the live process is running {live}, not {root}")
    if not problems:
        return None
    return ("; ".join(problems) + ".\n"
            "  spike/node_modules/ is gitignored, so a worktree or a stale "
            "copy has NEITHER vendor adapter and the Claude and ChatGPT lanes "
            "both report 'not installed' while grok/gemini/ollama look fine.\n"
            f"  Fix: sed \"s#/Users/USER#$HOME#g\" {root}/{LAUNCHD_LABEL}.plist "
            f"> ~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist "
            f"&& launchctl kickstart -k gui/$(id -u)/{LAUNCHD_LABEL}\n"
            "  To preview a branch instead, run it on another port from that "
            "checkout (CORRAL_LIGHT_PORT=8099 python3 hub.py) and leave the "
            "service alone.")


def _service_report():
    """Report which tree the service runs; printed before any lane is touched."""
    root = Path(sessions.ROOT).resolve()
    _line("this tree", root)
    installed = installed_service_trees()
    _line("launchd will run",
          ", ".join(str(t) for t in installed) if installed
          else "(no LaunchAgent installed — running from source)")
    live = running_service_tree()
    _line("launchd is running", live if live is not None else "(not running)")
    problem = service_tree_problem(root)
    if problem:
        print(f"\n  ✗ WRONG TREE — {problem}\n", flush=True)
    return problem



def _line(k, v):
    print(f"  {k:<26} {v}", flush=True)


def _env_report(env_overrides, strip):
    """What the child process will actually see. Names only."""
    child = {k: v for k, v in os.environ.items()
             if not (strip and k.startswith(tuple(strip)))}
    child.update(env_overrides or {})
    interesting = sorted(k for k in child
                         if k.startswith(("CLAUDE", "ANTHROPIC", "OPENAI",
                                          "GEMINI", "GOOGLE", "XAI", "GROK",
                                          "CODEX", "CORRAL"))
                         )
    removed = sorted(k for k in os.environ
                     if strip and k.startswith(tuple(strip)))
    # Mark what corral-light sets, so a variable in both lists reads as replaced.
    shown = [f"{k} (set by corral-light)" if k in (env_overrides or {}) else k
             for k in interesting]
    _line("env vars reaching agent:",
          ", ".join(shown) if shown else "(none of interest)")
    _line("env vars stripped:", ", ".join(removed) if removed else "(none)")
    for k in interesting:
        if any(h in k.upper() for h in SECRET_HINTS):
            _line(f"  {k}", f"<set, {len(child[k])} chars — value not shown>")


def _credential_shape(path):
    """Which fields the credential file carries, and their lengths — never values."""
    if not path.is_file():
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _line("  credential shape", f"unparseable: {e}")
        return
    rows = []

    def walk(o, pre=""):
        for k, v in sorted(o.items()):
            if isinstance(v, dict):
                walk(v, pre + k + ".")
            elif isinstance(v, str):
                rows.append(f"{pre}{k}=<{len(v)} chars>")
            elif isinstance(v, list):
                rows.append(f"{pre}{k}=[{len(v)} items]")
            else:
                rows.append(f"{pre}{k}={v}")

    walk(doc if isinstance(doc, dict) else {"<not an object>": str(type(doc))})
    _line("  credential fields", ", ".join(rows) or "(empty)")
    # These decide whether an isolated config dir can authenticate; an empty
    # token passes every structural check, so flag it too.
    flat = " ".join(rows)
    for field in ("accessToken", "refreshToken"):
        if field not in flat:
            _line("  ⚠ MISSING", f"{field} is not in this file")
        elif f"{field}=<0 chars>" in flat:
            _line("  ⚠ EMPTY",
                  f"{field} is present but EMPTY — this file authenticates "
                  f"nothing, and copying it into an isolated config dir "
                  f"produces a directory that only looks credentialed")


def _run_once(spec, cwd, config_dir, prompt, label):
    """One full handshake+prompt. Returns (ok, error, stderr_tail)."""
    import sessions as _s
    env = _s.spawn_env(spec, config_dir)
    strip = _s.strip_prefixes()
    client = None
    try:
        client = acp.AcpClient(spec["argv"], cwd, env=env, strip_env=strip)
        client.initialize()
        new = client.new_session_full(cwd, []) or {}
        client.prompt(new.get("sessionId"), prompt)
        return True, "", list(getattr(client, "stderr_tail", []))
    except Exception as e:                          # noqa: BLE001
        return False, str(e)[:300], list(getattr(client, "stderr_tail", []))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:                       # noqa: BLE001
                pass


def _control(spec, cwd, config_dir, prompt):
    """Positive control: re-run without the private CLAUDE_CONFIG_DIR."""
    if config_dir is None:
        print("\n  (no private config dir was used, so there is no control "
              "to run — the failure is not about CLAUDE_CONFIG_DIR)",
              flush=True)
        return
    print("\nCONTROL — same run, WITHOUT the private config dir", flush=True)
    ok, err, tail = _run_once(spec, cwd, None, prompt, "control")
    if ok:
        print("\n  ✓ IT WORKS without the private config dir.\n", flush=True)
        print("  This was the STALE-COPY bug (fixed 2026-08-31): the private\n"
              "  config dir copies your credential once and, before this fix,\n"
              "  never again — so a rotated token left the copy permanently\n"
              "  behind the real one. seed_config_dir() now re-copies whenever\n"
              "  the source is newer than the copy. Re-run `diagnose` (or just\n"
              "  retry the pane): the next attempt should resync and pass.\n"
              "  If it still fails, the private-config-dir mechanism itself\n"
              "  has a different problem on this host and this control has\n"
              "  correctly told you where to keep looking.\n", flush=True)
    else:
        _line("control also failed", err)
        print("\n  So the private config dir is NOT the difference — the lane\n"
              "  fails the same way under your own ~/.claude. That points at\n"
              "  the credential itself rather than at anything Corral does.\n",
              flush=True)
    if tail:
        print("  control adapter stderr:", flush=True)
        for ln in tail[-15:]:
            print(f"  | {ln}", flush=True)


def _mode(path):
    try:
        return oct(path.stat().st_mode & 0o777)
    except OSError:
        return "?"


def _permission_audit(real_dir, config_dir):
    """Directory modes, real vs. private (some CLIs distrust loose directories)."""
    print("\n  permission audit (owner/group/other, octal)", flush=True)
    _line("    real ~/.claude", _mode(real_dir))
    _line("    real .credentials.json", _mode(real_dir / ".credentials.json"))
    _line("    private config dir", _mode(config_dir))
    _line("    private .credentials.json", _mode(config_dir / ".credentials.json"))
    home = Path.home()
    _line(f"    {home}", _mode(home))


def _content_equality(src, dst):
    """Report whether the copy is byte-identical to the source (by hash)."""
    import hashlib
    try:
        a = hashlib.sha256(Path(src).read_bytes()).hexdigest()
        b = hashlib.sha256(Path(dst).read_bytes()).hexdigest()
    except OSError as e:
        _line("  copy identical to source?", f"could not check: {e}")
        return
    _line("  copy identical to source?", "yes" if a == b else "NO — DIFFERS")


def diagnose(key="claude", cwd=None, prompt="Reply with exactly: DIAGNOSTIC OK"):
    spec = sessions.AGENTS.get(key)
    if not spec:
        print(f"no such lane {key!r}", flush=True)
        return 2
    cwd = cwd or str(Path.home())

    print(f"\n=== corral-light diagnose: {spec['label']} ===\n", flush=True)
    print("HOST", flush=True)
    _line("platform", f"{sys.platform} / {os.uname().machine}")
    _line("python", sys.version.split()[0])
    _line("cwd for this run", cwd)

    # Before the lane: a report from a tree the service isn't running is moot.
    print("\nSERVICE", flush=True)
    _service_report()

    print("\nLANE", flush=True)
    _line("argv[0]", spec["argv"][0])
    for p in spec.get("requires", ()):
        _line("requires", f"{p} {'✓' if Path(p).exists() else '✗ MISSING'}")

    print("\nCREDENTIAL / CONFIG", flush=True)
    real = Path.home() / ".claude"
    cred = real / ".credentials.json"
    _line("~/.claude exists", "yes" if real.is_dir() else "no")
    _line("~/.claude/.credentials.json",
          f"yes ({cred.stat().st_size} bytes)" if cred.is_file()
          else "NO — not a file on this host (Keychain?)")
    _line("~/.claude.json exists",
          "yes" if (Path.home() / ".claude.json").is_file() else "no")
    _credential_shape(cred)

    config_dir = None
    if spec.get("posture_via_config_dir"):
        if sessions.darwin_keychain_blocks_isolation():
            _line("private config dir",
                  "REFUSED — macOS Claude Code stores its OAuth session in "
                  "the Keychain under a service name that CLAUDE_CONFIG_DIR "
                  "itself changes (read from cli.js's own Kg() function); no "
                  "copy of .credentials.json fixes this → agent uses "
                  "~/.claude")
        config_dir = sessions.seed_config_dir(
            sessions.STATE / "diagnose-config", sessions.DEFAULT_POSTURE)
        if config_dir is None and not sessions.darwin_keychain_blocks_isolation():
            _line("private config dir",
                  "REFUSED (no usable credential to carry) → agent uses ~/.claude")
        if config_dir:
            names = sorted(p.name for p in Path(config_dir).iterdir())
            _line("  contents", ", ".join(names))
            _permission_audit(real, Path(config_dir))
            _content_equality(real / ".credentials.json",
                              Path(config_dir) / ".credentials.json")
    _line("posture enforceable", sessions.posture_enforceable(spec))

    env = sessions.spawn_env(spec, config_dir)
    strip = sessions.strip_prefixes()
    print("\nENVIRONMENT (names only — values are never printed)", flush=True)
    _env_report(env, strip)

    print("\nHANDSHAKE", flush=True)
    client, t0 = None, time.time()
    try:
        client = acp.AcpClient(spec["argv"], cwd, env=env, strip_env=strip)
        info = client.initialize() or {}
        _line("initialize", "ok — " + json.dumps(info.get("agentInfo", {}))[:120])
        new = client.new_session_full(cwd, []) or {}
        _line("session/new", f"ok — session {str(new.get('sessionId'))[:20]}")
        _line("  configOptions",
              ", ".join(str(c.get("id")) for c in new.get("configOptions") or [])
              or "(none)")

        # The step `doctor` does not take.
        print("\nPROMPT (the step that fails)", flush=True)
        r = client.prompt(new.get("sessionId"), prompt) or {}
        _line("session/prompt", f"ok — stopReason={r.get('stopReason')}")
        print("\n  ✓ this lane works end to end from here.\n", flush=True)
        return 0
    except acp.AgentError as e:
        print(f"\n  ✗ FAILED after {int(time.time() - t0)}s\n", flush=True)
        _line("error", str(e)[:400])
        _control(spec, cwd, config_dir, prompt)
        return 1
    except Exception as e:                          # noqa: BLE001
        print(f"\n  ✗ FAILED after {int(time.time() - t0)}s\n", flush=True)
        _line("error", f"{type(e).__name__}: {e}"[:400])
        return 1
    finally:
        if client is not None:
            # The adapter's own stderr, which otherwise surfaces only its last line.
            tail = getattr(client, "stderr_tail", [])
            if tail:
                print("\nADAPTER STDERR (last lines — the agent's own words)",
                      flush=True)
                for ln in tail[-25:]:
                    print(f"  | {ln}", flush=True)
            else:
                print("\n  (the adapter wrote nothing to stderr)", flush=True)
            try:
                client.close()
            except Exception:                       # noqa: BLE001
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lane", nargs="?", default="claude",
                    help="lane key (default: claude)")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--prompt", default="Reply with exactly: DIAGNOSTIC OK")
    a = ap.parse_args(argv)
    return diagnose(a.lane, a.cwd, a.prompt)


if __name__ == "__main__":
    raise SystemExit(main())
