#!/usr/bin/env python3
"""corral-light install-service — write the service file, and stop there.

Resolves paths from the running checkout, writes the systemd user unit
(Linux) or launchd agent (macOS), and prints the enable command; it never
enables or starts anything. `--print` writes nothing.
"""
from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path
from xml.parsers.expat import ExpatError
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent

# Defaults matching the shipped templates. Loopback unless explicitly exposed.
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = "8098"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

LINUX_UNIT = """\
# Corral Light — systemd USER unit.
#
# WRITTEN BY `corral-light install-service`. Every path below was resolved
# from the checkout that generated it; edit them here if you move the tree.
#
# A USER unit, not a system service, on purpose: every lane authenticates as
# the logged-in user (Claude's credentials, the Grok CLI's own state,
# Antigravity's OAuth, the codex device-auth token) and every pane runs with
# that user's filesystem access. Running this as root would hand a browser on
# the network a root shell behind a pairing gate.

[Unit]
Description=Corral Light — multi-model agent workspace
After=network.target

[Service]
Type=simple
ExecStart={python} {root}/hub.py
WorkingDirectory={root}

# Loopback. This is hub.py's default too; it is restated because a unit file
# is where someone looks to change it, and the reasoning belongs where the
# knob is. To expose it on the LAN, set 0.0.0.0 and mean it.
Environment=CORRAL_LIGHT_BIND={bind}
Environment=CORRAL_LIGHT_PORT={port}
Environment=CORRAL_OLLAMA_URL={ollama}
{extra}
Restart=always
RestartSec=3

# process: on stop/restart, SIGTERM goes to the hub ALONE and the rest of the
# cgroup is left running. Each pane's agent runs under a keeper (keeper.py) in
# its own session; the next hub reattaches to it, turn and permission cards
# included. The default, control-group, would kill the keepers with the hub.
KillMode=process

[Install]
WantedBy=default.target
"""

MACOS_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Corral Light — launchd user agent.

  WRITTEN BY `corral-light install-service`. Every path below was resolved
  from the checkout that generated it, including the interpreter: launchd
  inherits almost no environment, so nothing here may rely on a PATH lookup.

  A USER agent, not a system daemon, on purpose: every lane authenticates as
  the logged-in user, and every pane runs with that user's filesystem access.

  Do NOT repoint this at a worktree. `spike/node_modules/` is gitignored, so a
  worktree has the Python but neither vendor ACP adapter, and the Claude and
  ChatGPT lanes go dark while the others stay green — which reads as a vendor
  outage rather than a wrong folder. To try a branch, run it in the foreground
  from that checkout on another port and leave the service alone.
-->
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>

  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{root}/hub.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>{root}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>CORRAL_LIGHT_BIND</key>
    <string>{bind}</string>
    <key>CORRAL_LIGHT_PORT</key>
    <string>{port}</string>
    <key>CORRAL_OLLAMA_URL</key>
    <string>{ollama}</string>
{extra}    <!-- For the two npm-installed ACP adapters. launchd's own PATH is
         /usr/bin:/bin:/usr/sbin:/sbin and nothing else. -->
    <key>PATH</key>
    <string>{path}</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""

LABEL = "com.cvp1.corral-light"
_MAC_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


# Written only when set: Tailscale Serve's host[:port] (the pairing origin
# behind Serve) and the one tailnet login Serve traffic may come from. A
# reinstall that dropped these would silently unpair the phone.
OPTIONAL = (("serve_host", "CORRAL_LIGHT_SERVE_HOST"),
            ("tailscale_login", "CORRAL_TAILSCALE_LOGIN"))
# Every value this writes, by plan() keyword; what carry_forward() reads back.
KEYS = (("bind", "CORRAL_LIGHT_BIND"), ("port", "CORRAL_LIGHT_PORT"),
        ("ollama", "CORRAL_OLLAMA_URL")) + OPTIONAL


def _clean(name, value):
    v = str(value)
    if any(c in v for c in "\r\n\x00") or (v and v != v.strip()):
        raise SystemExit(f"install-service: {name} must be one line with no "
                         f"surrounding spaces")
    return v


def plan(platform=None, root=None, python=None, home=None,
         bind=DEFAULT_BIND, port=DEFAULT_PORT, ollama=DEFAULT_OLLAMA_URL,
         serve_host=None, tailscale_login=None):
    """Where the file goes, what is in it, and what to type next. Pure, so
    any platform's answer can be rendered on any host."""
    platform = sys.platform if platform is None else platform
    root = Path(root or HERE).resolve()
    # Not resolved: following symlinks yields a versioned path an upgrade deletes.
    python = str(python or sys.executable)
    home = Path(home or Path.home())
    vals = {"bind": bind, "port": str(port), "ollama": ollama,
            "serve_host": serve_host, "tailscale_login": tailscale_login}
    vals = {k: (_clean(k, v) if v else None) for k, v in vals.items()}
    optional = [(env, vals[k]) for k, env in OPTIONAL if vals[k]]
    common = {"root": root, "python": python, "bind": vals["bind"],
              "port": vals["port"], "ollama": vals["ollama"]}
    if platform == "darwin":
        extra = "".join(f"    <key>{env}</key>\n    <string>{escape(v)}</string>\n"
                        for env, v in optional)
        x = {k: escape(str(v)) for k, v in common.items()}
        return {
            "path": home / "Library/LaunchAgents" / f"{LABEL}.plist",
            "text": MACOS_PLIST.format(
                label=LABEL, path=_MAC_PATH, extra=extra,
                log=escape(str(home / "Library/Logs/corral-light.log")), **x),
            "next": [f"launchctl bootstrap gui/$(id -u) "
                     f"{home / 'Library/LaunchAgents' / (LABEL + '.plist')}",
                     f"launchctl print gui/$(id -u)/{LABEL}"],
        }
    if platform.startswith("linux"):
        return {
            "path": home / ".config/systemd/user/corral-light.service",
            "text": LINUX_UNIT.format(
                extra="".join(f'Environment="{env}={v}"\n' for env, v in optional),
                **common),
            "next": ["systemctl --user daemon-reload",
                     "systemctl --user enable --now corral-light",
                     "systemctl --user status corral-light",
                     "# on a headless box, so it survives logout:",
                     "loginctl enable-linger $USER"],
        }
    raise SystemExit(
        f"install-service: no service format for platform {platform!r} — "
        f"this writes a systemd user unit on Linux and a launchd agent on "
        f"macOS. Run the hub yourself with `corral-light serve`.")


def carry_forward(path):
    """The values an installed service file already sets, by plan() keyword,
    so a --force reinstall keeps them unless a flag says otherwise. An
    unreadable file carries nothing (the caller shows what it will write)."""
    env = {}
    try:
        if path.suffix == ".plist":
            with open(path, "rb") as f:
                env = plistlib.load(f).get("EnvironmentVariables") or {}
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("Environment="):
                    k, _, v = line[len("Environment="):].strip('"').partition("=")
                    env[k] = v
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException, ExpatError):
        return {}
    return {k: str(env[e]) for k, e in KEYS if env.get(e)}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="corral-light install-service",
        description="Write the service file for this checkout. Does not "
                    "enable it and does not start it — that is yours.")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the file and write nothing")
    # No defaults here: unset means "keep what the installed file says, else
    # the shipped default" (resolved below).
    ap.add_argument("--port")
    ap.add_argument("--bind")
    ap.add_argument("--ollama-url")
    ap.add_argument("--serve-host", help="Tailscale Serve host[:port] the hub "
                    "is reached through (CORRAL_LIGHT_SERVE_HOST)")
    ap.add_argument("--tailscale-login", help="the one tailnet login Serve "
                    "traffic may come from (CORRAL_TAILSCALE_LOGIN)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing file (it is shown first "
                         "otherwise, and nothing is written)")
    a = ap.parse_args(argv)
    import install_kind
    kind = install_kind.detect(HERE)
    if kind["kind"] == "homebrew":
        # brew writes and owns this service; a second agent would fight it
        # for the port.
        print(f"This is a Homebrew install: its service is brew's.\n"
              f"  brew services start {kind['formula']}\n"
              f"  brew services info {kind['formula']}", flush=True)
        return 0
    flags = {"bind": a.bind, "port": a.port, "ollama": a.ollama_url,
             "serve_host": a.serve_host, "tailscale_login": a.tailscale_login}
    target = plan()["path"]
    kept = carry_forward(target) if target.exists() else {}
    vals = {"bind": DEFAULT_BIND, "port": DEFAULT_PORT, "ollama": DEFAULT_OLLAMA_URL,
            **kept, **{k: v for k, v in flags.items() if v is not None}}
    p = plan(**vals)
    carried = sorted(k for k in kept if flags[k] is None)

    if a.show:
        sys.stdout.write(p["text"])
        print(f"\n# would be written to: {p['path']}", flush=True)
        return 0

    if p["path"].exists() and not a.force:
        # Never silently replace a possibly hand-edited installed file.
        print(f"{p['path']} already exists — left alone.\n"
              f"Compare it with `corral-light install-service --print`, "
              f"or pass --force to replace it.", file=sys.stderr, flush=True)
        return 1

    p["path"].parent.mkdir(parents=True, exist_ok=True)
    p["path"].write_text(p["text"], encoding="utf-8")
    print(f"wrote {p['path']}\n", flush=True)
    if carried:
        # Names only: a login is an account identifier, kept off the terminal.
        print("kept from the file it replaced: " + ", ".join(carried) +
              " (pass the flag to change one)\n", flush=True)
    print("It is NOT enabled and NOT running. To start it:", flush=True)
    for line in p["next"]:
        print(f"  {line}", flush=True)
    print(flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
