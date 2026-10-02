#!/usr/bin/env python3
"""corral-light launch — open the wall in your browser, already paired.

    corral-light launch            mint a code, approve it, open the browser
    corral-light launch --print    print the URL and open nothing

Pairing normally takes two steps: the browser shows a code, and you type
`corral-light pair <code>` in a shell. Both halves prove the same thing —
that you own this UNIX account — so a command that runs as that account can
do both halves itself: mint the code, approve it, and hand it to the browser
in the URL (`/?pair=<code>`). The page claims the code once and drops it from
the address bar. The code is single-use and expires in five minutes.

What this does not change: a browser on another machine still needs
`corral-light pair`, and nothing here reads or relays a session cookie — the
browser gets its cookie from the hub, as always.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import auth  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8098"


def hub_url(env=None):
    """Where the hub is: CORRAL_LIGHT_URL, else bind/port, else the default."""
    env = os.environ if env is None else env
    explicit = (env.get("CORRAL_LIGHT_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    bind = (env.get("CORRAL_LIGHT_BIND") or "127.0.0.1").strip()
    port = (env.get("CORRAL_LIGHT_PORT") or "8098").strip()
    if bind in ("0.0.0.0", "", "::"):
        bind = "127.0.0.1"
    return f"http://{bind}:{port}"


def hub_alive(base, timeout=3.0):
    """True when /health answers. Never raises."""
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def paired_url(base):
    """A URL that pairs the browser that opens it: the code is minted and
    approved here, by the account that owns the hub."""
    code, _ttl = auth.new_code()
    ok, msg = auth.approve(code)
    if not ok:
        raise RuntimeError(msg)
    return f"{base}/?pair={code}"


def opener(platform=None, env=None):
    """The command that opens a URL on this machine, or None."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "darwin":
        return [shutil.which("open")] if shutil.which("open") else None
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return None
    for name in ("xdg-open", "gio"):
        found = shutil.which(name)
        if found:
            return [found, "open"] if name == "gio" else [found]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="corral-light launch",
        description="Open Corral Light in your browser, already paired.")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the paired URL and open nothing")
    ap.add_argument("--url", default=None,
                    help=f"hub address (default: CORRAL_LIGHT_URL or {DEFAULT_URL})")
    a = ap.parse_args(argv)
    base = (a.url or hub_url()).rstrip("/")

    if not hub_alive(base):
        print(f"Corral Light is not answering at {base}.\n"
              f"Start it with one of:\n"
              f"  systemctl --user start corral-light     # if installed as a service\n"
              f"  corral-light serve                      # in this terminal\n"
              f"then run `corral-light launch` again.", file=sys.stderr, flush=True)
        return 2

    try:
        url = paired_url(base)
    except auth.TooMany as e:
        print(f"could not mint a pairing code: {e}", file=sys.stderr, flush=True)
        return 3

    if a.show:
        print(url, flush=True)
        return 0

    cmd = opener()
    if not cmd:
        print("No browser opener found (no display, or no xdg-open).\n"
              "Open this address yourself within five minutes:\n"
              f"  {url}", flush=True)
        return 0
    try:
        # Detached: a browser that inherits this terminal would hold it open.
        subprocess.Popen(cmd + [url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        print(f"could not start {cmd[0]}: {e}\nOpen this address yourself:\n  {url}",
              flush=True)
        return 0
    print(f"opening {base}/ in your browser (paired)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
