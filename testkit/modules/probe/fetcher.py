"""Fixture fetcher. Its behaviour is the JSON in mode.json in its own work
dir, written by the test. Test-only: never installed outside the suite."""
import base64
import json
import os
import socket
import sys
import time


def attempt(fn):
    try:
        fn()
        return "OPENED"
    except Exception as e:  # noqa: BLE001
        return "refused:" + type(e).__name__


def via_proxy(host):
    """CONNECT through the shim; -> the proxy's status line."""
    proxy = os.environ.get("HTTPS_PROXY", "")
    hp = proxy.rsplit("/", 1)[-1]
    h, p = hp.rsplit(":", 1)
    s = socket.create_connection((h, int(p)), timeout=10)
    s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    line = s.recv(200).split(b"\r\n", 1)[0].decode()
    s.close()
    return line


def main():
    data = os.environ["CORRAL_MODULE_DATA"]
    try:
        with open(os.path.join(data, "mode.json")) as f:
            mode = json.load(f)
    except OSError:
        mode = {"mode": "ok"}
    key = open(os.environ["CORRAL_FETCH_KEY"], "rb").read()
    m = mode["mode"]
    if m == "ok":
        print(json.dumps({"ok": True, "vendor": os.environ["CORRAL_FETCH_VENDOR"],
                          "hosts": os.environ["CORRAL_FETCH_HOSTS"]}))
    elif m == "leak":
        print(json.dumps({"oops": key.decode().strip()}))
    elif m == "leak_b64":
        print(json.dumps({"oops": base64.b64encode(key.strip()).decode()}))
    elif m == "fail":
        print("GET https://api.example.invalid/v1/x?key=" + key.decode().strip() +
              "\nAuthorization: Bearer abcdefghijklmnop", file=sys.stderr)
        sys.exit(1)
    elif m == "big":
        sys.stdout.write("x" * (2 << 20))
    elif m == "sleep":
        time.sleep(60)
    elif m == "isolation":
        out = {line: attempt(lambda line=line: open(line, "rb").read(1))
               for line in mode.get("read", [])}
        out["list_home"] = attempt(lambda: os.listdir(os.path.expanduser("~/..")))
        out["direct"] = attempt(lambda: socket.create_connection(("1.1.1.1", 443), timeout=2))
        out["proxy"] = {h: via_proxy(h) for h in mode.get("connect", [])}
        out["env"] = sorted(os.environ)
        print(json.dumps(out))


if __name__ == "__main__":
    main()
