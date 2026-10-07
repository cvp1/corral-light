"""A stand-in for the Grok binary, for test_vendor_reports.py.

Installed by the test as <fake grok home>/bin/grok-9.9.9 (with a shebang
line naming the host's python), with bin/grok a symlink to it and the
login file beside it, the way the real install lays it out. It answers
`--version` and `usage <id>`; what `usage` does is chosen per session by
the `standin_mode` field in that session's summary.json:

  ok       print usage.json (the test writes it in `grok usage` shape)
  probe    try every forbidden thing; report how many worked in
           session.inputTokens (must be 0) and the control read of its own
           usage.json in session.outputTokens (must be 1)
  hang     start a child in a new session, then sleep past any timeout
  memory   allocate past the address-space limit, then print a report
  big      print a valid-looking report over 1 MiB
  garbage  print something that is not JSON
  float    print a report whose costUsdTicks is a float
"""
import glob
import json
import os
import socket
import sys
import time


def main():
    if sys.argv[1:2] == ["--version"]:
        print("grok 9.9.9 (standin) [stable]")
        return 0
    if sys.argv[1:2] != ["usage"] or len(sys.argv) < 3:
        return 2
    sid = sys.argv[2]
    home = os.path.expanduser("~")
    found = glob.glob(os.path.join(home, ".grok", "sessions", "*", sid))
    if not found:
        print("Session not found", file=sys.stderr)
        return 1
    d = found[0]
    with open(os.path.join(d, "summary.json")) as f:
        summary = json.load(f)
    with open(os.path.join(d, "usage.json")) as f:
        report = json.load(f)
    mode = summary.get("standin_mode", "ok")

    if mode == "probe":
        leaks = 0
        here = os.path.dirname(os.path.abspath(sys.argv[0]))
        paths = list(summary.get("standin_probes", []))
        paths += [os.path.join(here, "auth.json"), os.path.join(here, "..", "auth.json"),
                  os.path.join(os.path.realpath(sys.argv[0]), "..", "auth.json")]
        for p in paths:
            try:
                with open(p, "rb") as f:
                    f.read(1)
                leaks += 1
            except OSError:
                pass
        # Any session other than its own, inside the scratch home.
        for other in glob.glob(os.path.join(home, ".grok", "sessions", "*", "*")):
            if os.path.basename(other) != sid:
                leaks += 1
        for host, port in [("1.1.1.1", 443)] + [("127.0.0.1", int(p))
                                                 for p in summary.get("standin_ports", [])]:
            s = socket.socket()
            s.settimeout(1)
            try:
                s.connect((host, port))
                leaks += 1
            except OSError:
                pass
            finally:
                s.close()
        controls = 0
        try:
            with open(os.path.join(d, "usage.json"), "rb") as f:
                f.read(1)
            controls = 1
        except OSError:
            pass
        report["session"]["inputTokens"] = leaks
        report["session"]["outputTokens"] = controls
    elif mode == "hang":
        if os.fork() == 0:
            os.setsid()
            time.sleep(120)
            os._exit(0)
        time.sleep(120)
    elif mode == "memory":
        hog = bytearray(4 << 30)
        hog[-1] = 1
    elif mode == "big":
        report["pad"] = "x" * (2 << 20)
    elif mode == "garbage":
        sys.stdout.write("No usage recorded for this session\n")
        return 0
    elif mode == "float":
        report["turns"][0]["costUsdTicks"] = 1.5
    sys.stdout.write(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
