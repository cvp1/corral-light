#!/usr/bin/python3
"""diagnose — run one lane exactly as a pane does, and report every difference.

WHY THIS EXISTS
    Craig's Claude panes on dogma-2 died at `session/prompt` with
    `Authentication required` while `claude` worked fine in his terminal. I
    proposed three mechanisms in a row — a credential-file check, a private
    CLAUDE_CONFIG_DIR with no credential in it, an ambient ANTHROPIC_API_KEY —
    and shipped a fix for each. All three were defensible. None was measured.
    The third was disproved by one `env | grep` that took him five seconds.

    Three misses is the signal to stop proposing mechanisms and build the
    thing that reports what is actually happening (P18: always be prepared for
    an audit — every load-bearing claim arrives with what is needed to CHECK
    it; P1: distrust green).

WHAT MAKES THIS DIFFERENT FROM `doctor`
    `doctor` asks whether a lane can START. That question was answering `ok`
    while every conversation died, because the failure is one step further in:
    the handshake succeeds and the first PROMPT fails. So this sends a real
    prompt. It costs a token or two, which is why it is an explicit command
    and not part of every doctor run.

    It also reports the things that differ between a pane and a terminal —
    the config dir, the environment, the working directory — because that
    delta is where every one of these bugs has lived.

SECRETS
    Environment variable NAMES, never values. File names and sizes, never
    contents. The whole point is to make this safe to paste into a chat.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import acp
import sessions


SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")


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
    # Mark what CORRAL sets, so a variable appearing in both lists reads as
    # "the inherited one was removed and ours was applied" rather than as a
    # contradiction.
    shown = [f"{k} (set by corral-light)" if k in (env_overrides or {}) else k
             for k in interesting]
    _line("env vars reaching agent:",
          ", ".join(shown) if shown else "(none of interest)")
    _line("env vars stripped:", ", ".join(removed) if removed else "(none)")
    for k in interesting:
        if any(h in k.upper() for h in SECRET_HINTS):
            _line(f"  {k}", f"<set, {len(child[k])} chars — value not shown>")


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

    config_dir = None
    if spec.get("posture_via_config_dir"):
        config_dir = sessions.seed_config_dir(
            sessions.STATE / "diagnose-config", sessions.DEFAULT_POSTURE)
        _line("private config dir",
              str(config_dir) if config_dir else
              "REFUSED (no credential to carry) → agent uses ~/.claude")
        if config_dir:
            names = sorted(p.name for p in Path(config_dir).iterdir())
            _line("  contents", ", ".join(names))
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

        # THE STEP `doctor` DOES NOT TAKE, and the one that has been failing.
        print("\nPROMPT (the step that fails)", flush=True)
        r = client.prompt(new.get("sessionId"), prompt) or {}
        _line("session/prompt", f"ok — stopReason={r.get('stopReason')}")
        print("\n  ✓ this lane works end to end from here.\n", flush=True)
        return 0
    except acp.AgentError as e:
        print(f"\n  ✗ FAILED after {int(time.time() - t0)}s\n", flush=True)
        _line("error", str(e)[:400])
        return 1
    except Exception as e:                          # noqa: BLE001
        print(f"\n  ✗ FAILED after {int(time.time() - t0)}s\n", flush=True)
        _line("error", f"{type(e).__name__}: {e}"[:400])
        return 1
    finally:
        if client is not None:
            # The adapter's OWN words. acp.py has always captured these and
            # only ever used the last line, inside an exit reason — so the
            # detail behind every failure in this saga was collected and
            # thrown away. This is the whole reason the command exists.
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
