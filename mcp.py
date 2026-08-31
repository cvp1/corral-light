#!/usr/bin/python3
"""Corral-owned MCP registry and diagnostics.

The registry owns server names, transport metadata, and policy. Credentials are
referenced by handle only; values never belong in this file or in model input.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# corral-light, NOT corral: sharing one MCP config with the full build would
# mean a server added on ranch silently appears in every pane here, on a host
# that may not have its credential or its network path. Same reasoning as the
# separate state dir.
CONFIG = Path(os.environ.get("CORRAL_MCP_CONFIG",
                             Path.home() / ".config/corral-light/mcp.json"))
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class McpError(Exception):
    pass


def _read(path=CONFIG):
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        if not text:
            return {"servers": {}}
        raw = json.loads(text)
    except FileNotFoundError:
        return {"servers": {}}
    except (OSError, ValueError) as e:
        raise McpError(f"cannot read {path}: {e}")
    if not isinstance(raw, dict) or not isinstance(raw.get("servers", {}), dict):
        raise McpError("MCP config must contain an object named 'servers'")
    return raw


def _write(raw, path=CONFIG):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Registry:
    def __init__(self, path=CONFIG):
        self.path = Path(path)

    def raw(self):
        return _read(self.path)

    def list(self):
        out = []
        for name, item in self.raw()["servers"].items():
            out.append({"name": name, **item})
        return sorted(out, key=lambda x: x["name"])

    def add(self, name, item):
        if not NAME.match(name):
            raise McpError("server name must be lowercase kebab/snake case")
        item = dict(item)
        transport = item.get("transport")
        if transport not in ("stdio", "http"):
            raise McpError("transport must be stdio or http")
        if transport == "stdio" and not item.get("command"):
            raise McpError("stdio servers require command")
        if transport == "http" and not item.get("url"):
            raise McpError("http servers require url")
        item.setdefault("enabled", True)
        item.pop("name", None)
        # Direct secrets are an unsafe configuration shape. Use auth_ref and
        # let the eventual keyring adapter resolve it at connection time.
        if "headers" in item or "env" in item:
            raise McpError("store secret handles as auth_ref; direct headers/env are refused")
        raw = self.raw()
        raw["servers"][name] = item
        _write(raw, self.path)
        return {"name": name, **item}

    def remove(self, name):
        raw = self.raw()
        if name not in raw["servers"]:
            raise McpError(f"no MCP server named {name}")
        del raw["servers"][name]
        _write(raw, self.path)

    def set_enabled(self, name, enabled):
        raw = self.raw()
        if name not in raw["servers"]:
            raise McpError(f"no MCP server named {name}")
        raw["servers"][name]["enabled"] = bool(enabled)
        _write(raw, self.path)

    def set_auth(self, name, handle):
        if not handle.startswith("secret://"):
            raise McpError("auth must be a secret:// handle")
        raw = self.raw()
        if name not in raw["servers"]:
            raise McpError(f"no MCP server named {name}")
        raw["servers"][name]["auth_ref"] = handle
        _write(raw, self.path)

    def get(self, name):
        for item in self.list():
            if item["name"] == name:
                return item
        raise McpError(f"no MCP server named {name}")

    def session_servers(self):
        """Return descriptors for Corral's proxy, never the upstream server.

        ACP's McpServer type is an untagged enum (McpServerHttp | McpServerSse
        | McpServerStdio); a Rust-strict agent (Grok Build) tries each variant
        and rejects the WHOLE session/new call if none match exactly -- no
        partial credit for "close enough". McpServerStdio requires `env`
        (an array, empty is fine) alongside name/command/args; omitting it
        entirely, not just leaving it empty, was enough to fail every variant
        and kill session creation for every agent this registry serves, the
        moment any server was registered in it.
        """
        out = []
        for item in self.list():
            if not item.get("enabled", True):
                continue
            d = {"name": item["name"], "command": sys.executable,
                 "args": [str(Path(__file__).resolve()), "serve", item["name"]],
                 "env": []}
            out.append(d)
        return out

    def probe(self, name, timeout=15):
        item = self.get(name)
        started = time.time()
        try:
            result = _rpc(item, "tools/list", {}, timeout)
            tools = (result or {}).get("tools", [])
            return {"ok": True, "name": name, "transport": item["transport"],
                    "tools": [{"name": t.get("name"), "description":
                               (t.get("description") or "")[:200]}
                              for t in tools],
                    "elapsedMs": round((time.time() - started) * 1000)}
        except McpError as e:
            return {"ok": False, "name": name, "error": str(e),
                    "elapsedMs": round((time.time() - started) * 1000)}


def _rpc(item, method, params, timeout):
    rid = 1
    msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    if item["transport"] == "http":
        req = urllib.request.Request(item["url"],
                                      data=(json.dumps(msg) + "\n").encode(),
                                      headers={"Content-Type": "application/json",
                                               "Accept": "application/json, text/event-stream"},
                                      method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                body = res.read(2 * 1024 * 1024).decode("utf-8", "replace")
        except (OSError, urllib.error.URLError) as e:
            raise McpError(f"HTTP probe failed: {e}")
        if body.lstrip().startswith("data:"):
            body = next((line[5:].strip() for line in body.splitlines()
                         if line.startswith("data:") and line[5:].strip()), "")
        try:
            response = json.loads(body)
        except ValueError as e:
            raise McpError(f"MCP returned non-JSON response: {e}")
    else:
        try:
            proc = subprocess.Popen([item["command"], *item.get("args", [])],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {}, "clientInfo":
                               {"name": "corral", "version": "0.1"}}}
            proc.stdin.write(json.dumps(init) + "\n")
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            response = None
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                candidate = json.loads(line)
                if candidate.get("id") == rid:
                    response = candidate
                    break
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    stream.close()
            if response is None:
                raise McpError("stdio server did not answer tools/list")
        except (OSError, ValueError) as e:
            raise McpError(f"stdio probe failed: {e}")
    if response.get("error"):
        raise McpError(str(response["error"]))
    return response.get("result") or {}


class Proxy:
    """One MCP server process owned by Corral, forwarding safe JSON-RPC calls."""
    def __init__(self, item):
        self.item = item
        self.proc = None
        if item["transport"] == "stdio":
            try:
                self.proc = subprocess.Popen(
                    [item["command"], *item.get("args", [])],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
                self._stdio({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05",
                                        "capabilities": {}, "clientInfo":
                                        {"name": "corral", "version": "0.1"}}})
            except OSError as e:
                raise McpError(f"cannot start MCP server: {e}")

    def _stdio(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        wanted = message.get("id")
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise McpError("MCP server closed stdout")
            response = json.loads(line)
            if response.get("id") == wanted:
                if response.get("error"):
                    raise McpError(str(response["error"]))
                return response.get("result") or {}

    def request(self, method, params):
        message = {"jsonrpc": "2.0", "id": 1, "method": method,
                   "params": params or {}}
        if self.proc:
            return self._stdio(message)
        if self.item.get("auth_ref"):
            return _broker_http(self.item, message)
        return _rpc(self.item, method, params, 30)

    def close(self):
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass


def _broker_http(item, message):
    """Use secret's action-only fetch verb; the token never enters Corral."""
    broker = Path(__file__).resolve().parents[2] / "bin" / "secret"
    fd, body = __import__("tempfile").mkstemp(prefix="corral-mcp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(message, f)
        proc = subprocess.run(
            [str(broker), "fetch", item["auth_ref"], "--url", item["url"],
             "--inject", "bearer", "--method", "POST", "--data-file", body,
             "--content-type", "application/json"],
            capture_output=True, text=True, timeout=60)
    finally:
        try:
            os.unlink(body)
        except OSError:
            pass
    if proc.returncode:
        raise McpError((proc.stderr or "authenticated MCP request failed").strip())
    try:
        response = json.loads(proc.stdout)
    except ValueError as e:
        raise McpError(f"authenticated MCP returned non-JSON: {e}")
    if response.get("error"):
        raise McpError(str(response["error"]))
    return response.get("result") or {}


def serve(name):
    proxy = Proxy(Registry().get(name))
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                method = message.get("method")
                rid = message.get("id")
                if rid is None:
                    continue
                if method == "initialize":
                    result = {"protocolVersion": "2024-11-05",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "corral-" + name,
                                              "version": "0.1"}}
                elif method in ("tools/list", "tools/call"):
                    result = proxy.request(method, message.get("params") or {})
                else:
                    raise McpError(f"unsupported MCP method: {method}")
                print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}),
                      flush=True)
            except (ValueError, McpError) as e:
                if "rid" in locals() and rid is not None:
                    print(json.dumps({"jsonrpc": "2.0", "id": rid,
                                      "error": {"code": -32000, "message": str(e)}}),
                          flush=True)
    finally:
        proxy.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corral mcp")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--stdio")
    add.add_argument("--url")
    add.add_argument("--arg", action="append", default=[])
    add.add_argument("--auth-ref")
    rem = sub.add_parser("remove")
    rem.add_argument("name")
    test = sub.add_parser("test")
    test.add_argument("name")
    tools = sub.add_parser("tools")
    tools.add_argument("name")
    call = sub.add_parser("call")
    call.add_argument("name")
    call.add_argument("tool")
    call.add_argument("--arguments", default="{}")
    auth = sub.add_parser("auth")
    auth.add_argument("name")
    auth.add_argument("--handle", required=True)
    enable = sub.add_parser("enable")
    enable.add_argument("name")
    disable = sub.add_parser("disable")
    disable.add_argument("name")
    serve_cmd = sub.add_parser("serve")
    serve_cmd.add_argument("name")
    args = ap.parse_args(argv)
    reg = Registry()
    try:
        if args.action == "list":
            print(json.dumps(reg.list(), indent=2), flush=True)
        elif args.action == "add":
            if bool(args.stdio) == bool(args.url):
                ap.error("choose exactly one of --stdio or --url")
            item = {"transport": "stdio" if args.stdio else "http"}
            if args.stdio:
                item.update(command=args.stdio, args=args.arg)
            else:
                item["url"] = args.url
            if args.auth_ref:
                item["auth_ref"] = args.auth_ref
            print(json.dumps(reg.add(args.name, item), indent=2), flush=True)
        elif args.action == "remove":
            reg.remove(args.name)
            print(f"removed {args.name}", flush=True)
        elif args.action == "serve":
            serve(args.name)
        elif args.action == "auth":
            reg.set_auth(args.name, args.handle)
            print(f"bound {args.handle} to {args.name}", flush=True)
        elif args.action == "enable":
            reg.set_enabled(args.name, True)
            print(f"enabled {args.name}", flush=True)
        elif args.action == "disable":
            reg.set_enabled(args.name, False)
            print(f"disabled {args.name}", flush=True)
        elif args.action == "call":
            try:
                params = {"name": args.tool, "arguments": json.loads(args.arguments)}
            except ValueError as e:
                raise McpError(f"--arguments must be JSON: {e}")
            result = Proxy(reg.get(args.name)).request("tools/call", params)
            print(json.dumps(result, indent=2), flush=True)
        else:
            result = reg.probe(args.name)
            print(json.dumps(result, indent=2), flush=True)
            if not result["ok"]:
                return 1
    except McpError as e:
        print(f"corral mcp: {e}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
