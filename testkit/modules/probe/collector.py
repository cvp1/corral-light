"""Fixture collector. Its first config line picks the behaviour; the rest
are arguments. Test-only: never installed outside the suite."""
import json
import os
import socket
import subprocess
import sys
import time


def config():
    try:
        with open(os.environ["CORRAL_MODULE_CONFIG"], encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError:
        lines = []
    return (lines[0] if lines else "ok"), lines[1:]


def snap(view, **kw):
    out = {"schema": "corral-light.module/1", "ok": True,
           "generated_at": "2026-10-07T00:00:00Z", "error": None, "view": view}
    out.update(kw)
    return json.dumps(out)


def attempt(fn):
    try:
        fn()
        return "OPENED"
    except Exception as e:  # noqa: BLE001
        return "refused:" + type(e).__name__


def isolation(args):
    """args: lines 'read <path>', 'write <path>', 'connect <host> <port>'."""
    results = []
    for line in args:
        kind, _, rest = line.partition(" ")
        if kind == "read":
            r = attempt(lambda: open(rest, "rb").read(1))
        elif kind == "list":
            r = attempt(lambda: os.listdir(rest))
        elif kind == "write":
            def w():
                with open(rest, "w") as f:
                    f.write("x")
            r = attempt(w)
        elif kind == "connect":
            host, port = rest.split()

            def c():
                s = socket.create_connection((host, int(port)), timeout=2)
                s.close()
            r = attempt(c)
        else:
            r = "unknown"
        results.append([line, r])
    env = sorted(os.environ)
    feed = os.environ.get("CORRAL_MODULE_FEED", "")
    feed_ok = attempt(lambda: open(os.path.join(feed, "host.json"), "rb").read(1))
    data_ok = attempt(lambda: open(os.path.join(os.environ["CORRAL_MODULE_DATA"], "w"),
                                   "w").write("x"))
    return snap([{"type": "table", "title": "attempts", "columns": ["what", "result"],
                  "rows": results},
                 {"type": "note", "text": json.dumps({"env": env, "feed": feed_ok,
                                                       "data": data_ok})}])


def main():
    mode, args = config()
    if mode == "ok":
        print(snap([{"type": "tiles", "items": [
            {"label": "Runs", "value": "1", "kind": "vendor", "level": "ok"}]}]))
    elif mode == "exit1":
        print("boom", file=sys.stderr)
        sys.exit(1)
    elif mode == "nonjson":
        print("this is not json")
    elif mode == "bigout":
        sys.stdout.write("x" * (3 << 20))
    elif mode == "stderrflood":
        sys.stderr.write("e" * (1 << 20))
        print(snap([]))
    elif mode == "sleep":
        time.sleep(60)
    elif mode == "escape":
        # A child that leaves the group, then the collector hangs.
        marker = args[0] if args else "corral-escape-marker"
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker],
                         start_new_session=True)
        open(os.path.join(os.environ["CORRAL_MODULE_DATA"], "escaped"), "w").write(
            str(os.getpid()))
        time.sleep(60)
    elif mode == "isolation":
        print(isolation(args))
    elif mode == "hostile":
        print(snap([{"type": "note", "text": "<script>alert(1)</script>"},
                    {"type": "link", "label": "x", "url": "javascript:alert(1)"},
                    {"type": "tiles", "items": [{"label": "a", "kind": "evil class",
                                                 "level": "x\" onclick=\"y"}]},
                    {"type": "meter", "label": "m", "pct": 1e999},
                    {"type": "weird"}] + [{"type": "note", "text": "n"}] * 60))


if __name__ == "__main__":
    main()
