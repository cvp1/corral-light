"""Fixture fetcher. Its behaviour is the JSON in mode.json in its own work
dir, written by the test. It holds no key (plan §6.7.2): it reaches the
vendor only through the core's fetch proxy at CORRAL_FETCH_API.
Test-only: never installed outside the suite."""
import http.client
import json
import os
import socket
import sys
import time
import urllib.parse


def attempt(fn):
    try:
        fn()
        return "OPENED"
    except Exception as e:  # noqa: BLE001
        return "refused:" + type(e).__name__


def proxy():
    u = urllib.parse.urlsplit(os.environ["CORRAL_FETCH_API"])
    return u.hostname, u.port


def call(method, url, headers=None, body=None):
    """One request through the fetch proxy, in proxy form -> (status, text)."""
    host, port = proxy()
    c = http.client.HTTPConnection(host, port, timeout=20)
    try:
        c.request(method, url, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def raw(line):
    """Send one raw request line (CONNECT, say) -> the status line."""
    host, port = proxy()
    s = socket.create_connection((host, port), timeout=10)
    s.sendall(line.encode() + b"\r\n\r\n")
    out = s.recv(200).split(b"\r\n", 1)[0].decode()
    s.close()
    return out


def main():
    data = os.environ["CORRAL_MODULE_DATA"]
    try:
        with open(os.path.join(data, "mode.json")) as f:
            mode = json.load(f)
    except OSError:
        mode = {"mode": "ok"}
    m = mode["mode"]
    if m == "ok":
        print(json.dumps({"ok": True, "vendor": os.environ["CORRAL_FETCH_VENDOR"],
                          "hosts": os.environ["CORRAL_FETCH_HOSTS"]}))
    elif m == "call":
        out = []
        for req in mode["requests"]:
            status, text = call(req.get("method", "GET"), req["url"], req.get("headers"),
                                req.get("body"))
            out.append({"status": status, "text": text[:2000]})
        print(json.dumps({"replies": out}))
    elif m == "fail":
        print("GET https://api.example.invalid/v1/x?q=1\nAuthorization: Bearer abcdefghijklmnop",
              file=sys.stderr)
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
        out["connect"] = raw("CONNECT api.anthropic.com:443 HTTP/1.1")
        out["other_host"] = call("GET", "https://example.com/")[0]
        out["sign_in_host"] = call("GET", "https://console.anthropic.com/")[0]
        out["plain_http"] = call("GET", "http://api.anthropic.com/v1/x")[0]
        out["env"] = sorted(os.environ)
        print(json.dumps(out))


if __name__ == "__main__":
    main()
