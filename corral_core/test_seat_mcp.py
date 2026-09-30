#!/usr/bin/env python3
"""The seat tools' MCP server and the descriptor that offers it (DESIGN-5 S8).

  T8.1  the server answers initialize and tools/list with exactly the seat
        tools and their schemas; tools/call seat_send posts to the hub with
        the token FROM ITS ENVIRONMENT, and only `seat` and `text` leave it.
  T8.3  the descriptor carries the three env entries, a fresh token per spawn
        (the previous one stops working), CORRAL_NATIVE_MCP=0 offers nothing,
        and registry descriptors are passed through unchanged.
  T8.6  it is named `corral-seats` -- never `acp`, and a registry entry that
        takes the name is dropped rather than left beside the real one.
  S10   seat_broadcast posts only `text`; the per-seat loop is the hub's.
  and   the server is a real subprocess speaking line-delimited JSON-RPC.

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corral_core import seat_mcp                                  # noqa: E402
from corral_core import sessions as S                            # noqa: E402

SERVER = Path(seat_mcp.__file__)


class StubHub:
    """A real HTTP server that records what the seat tools send it."""

    def __init__(self):
        self.calls = []
        rec = self.calls

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj, code=200):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                rec.append(("GET", self.path, dict(self.headers), None))
                self._reply({"you": "author", "seats": [
                    {"seat": "reviewer", "display": "your-turn", "lane": "claude",
                     "tool": True}]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                rec.append(("POST", self.path, dict(self.headers), body))
                self._reply({"result": "delivered", "turn": "abcdef012345", "hop": 1})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()


class TheServer(unittest.TestCase):
    def setUp(self):
        self.hub = StubHub()
        self.addCleanup(self.hub.close)
        self.env = {"CORRAL_HUB_URL": self.hub.url, "CORRAL_PANE_TOKEN": "tok-123",
                    "CC_RUNBOOK_SESSION": "pane1"}

    def test_initialize_and_exactly_the_seat_tools(self):
        init = seat_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "corral-seats")
        self.assertIn("tools", init["result"]["capabilities"])
        tools = seat_mcp.handle({"jsonrpc": "2.0", "id": 2,
                                 "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools],
                         ["seat_list", "seat_send", "seat_broadcast"])
        send = tools[1]
        self.assertEqual(send["inputSchema"]["required"], ["seat", "text"])
        self.assertFalse(send["inputSchema"]["additionalProperties"])
        self.assertIn(seat_mcp.REFUSAL_GUIDANCE, send["description"],
                      "the model is not told a refusal is not an error to retry")
        self.assertIn("not proof", send["description"],
                      "the honest threat statement is missing (section 7.9)")

    def test_a_notification_gets_no_reply(self):
        self.assertIsNone(seat_mcp.handle({"jsonrpc": "2.0",
                                           "method": "notifications/initialized"}))

    def test_seat_send_posts_with_the_token_and_only_seat_and_text(self):
        """T8.1 + the half of T8.2 this process owns: a `from` the caller
        tries to pass never leaves."""
        out, is_error = seat_mcp.call_tool(
            "seat_send", {"seat": "@reviewer", "text": "hi", "from": "the-operator",
                          "pane": "someone-else"}, env=self.env)
        self.assertFalse(is_error)
        self.assertEqual(out["result"], "delivered")
        method, path, headers, body = self.hub.calls[-1]
        self.assertEqual((method, path), ("POST", "/api/peer/send"))
        self.assertEqual(headers.get("X-Corral-Pane-Token"), "tok-123")
        self.assertEqual(body, {"seat": "reviewer", "text": "hi"})

    def test_seat_broadcast_posts_only_the_text(self):
        """S10: one POST; the hub does the per-seat loop. Nothing but `text`
        leaves -- a caller cannot hand the hub a list of seats, a source, or
        a pane to leave out."""
        tools = {t["name"]: t for t in seat_mcp.TOOLS}
        b = tools["seat_broadcast"]
        self.assertEqual(b["inputSchema"]["required"], ["text"])
        self.assertFalse(b["inputSchema"]["additionalProperties"])
        self.assertIn(seat_mcp.REFUSAL_GUIDANCE, b["description"])
        self.assertIn("does not stop or undo", b["description"])
        out, is_error = seat_mcp.call_tool(
            "seat_broadcast", {"text": "all hands", "seats": ["x"], "from": "op"},
            env=self.env)
        self.assertFalse(is_error)
        method, path, headers, body = self.hub.calls[-1]
        self.assertEqual((method, path), ("POST", "/api/peer/broadcast"))
        self.assertEqual(headers.get("X-Corral-Pane-Token"), "tok-123")
        self.assertEqual(body, {"text": "all hands"})
        out, is_error = seat_mcp.call_tool("seat_broadcast", {}, env=self.env)
        self.assertEqual((out["result"], out["reason"], is_error),
                         ("refused", "arguments", False))

    def test_seat_list_is_a_get_with_the_token(self):
        out, is_error = seat_mcp.call_tool("seat_list", {}, env=self.env)
        self.assertFalse(is_error)
        self.assertEqual(out["seats"][0]["seat"], "reviewer")
        method, path, headers, _ = self.hub.calls[-1]
        self.assertEqual((method, path, headers.get("X-Corral-Pane-Token")),
                         ("GET", "/api/peer/seats", "tok-123"))

    def test_no_token_is_a_failure_that_says_why_and_calls_nothing(self):
        out, is_error = seat_mcp.call_tool("seat_send", {"seat": "x", "text": "y"},
                                           env={"CORRAL_HUB_URL": self.hub.url})
        self.assertTrue(is_error)
        self.assertEqual(out["result"], "failed")
        self.assertIn("token", out["why"])
        self.assertEqual(self.hub.calls, [])

    def test_a_refusal_is_not_an_mcp_error(self):
        """A model told `isError` tends to retry. A refusal is the hub's
        answer about another pane, returned as a normal result."""
        with mock.patch.object(seat_mcp, "_hub",
                               return_value={"result": "refused", "reason": "busy"}):
            r = seat_mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                 "params": {"name": "seat_send",
                                            "arguments": {"seat": "x", "text": "y"}}})
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["result"],
                         "refused")

    def test_it_runs_as_a_real_stdio_process(self):
        env = dict(os.environ, **self.env)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "seat_send",
                            "arguments": {"seat": "reviewer", "text": "over stdio"}}}]
        r = subprocess.run([sys.executable, str(SERVER)],
                           input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, env=env, timeout=30)
        replies = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
        self.assertEqual([x["id"] for x in replies], [1, 2], r.stderr)
        self.assertEqual(self.hub.calls[-1][3], {"seat": "reviewer", "text": "over stdio"})


class _Mgr(S.ManagerBase):
    def __init__(self):
        self.panes, self._lock = {}, threading.Lock()


class _Pane(S.PaneBase):
    """A PaneBase with only what the descriptor path reads (no process)."""

    def __init__(self, pid, agent="claude", registry=None):
        self.id, self.agent = pid, agent
        self.mgr = registry


class TheDescriptor(unittest.TestCase):
    def setUp(self):
        for name, val in (("PEER_HUB_URL", "http://127.0.0.1:18199"),
                          ("AGENTS", {"claude": {}, "local": {"mcp": False}})):
            p = mock.patch.object(S, name, val)
            p.start()
            self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {}, clear=False)
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop(S.NATIVE_MCP_ENV, None)
        self.mgr = _Mgr()

    def servers(self, pane, registry=()):
        class Reg:
            def session_servers(self):
                return [dict(d) for d in registry]
        mgr = self.mgr
        mgr.mcp = Reg()
        pane.mgr = mgr
        mgr.panes[pane.id] = pane
        return S.PaneBase._mcp_servers(pane)

    def test_three_env_entries_and_the_reserved_name(self):
        """T8.3 + T8.6."""
        d = self.servers(_Pane("p1"))[-1]
        self.assertEqual(d["name"], "corral-seats")
        self.assertNotEqual(d["name"], "acp")
        self.assertEqual(d["args"], [str(SERVER)])
        env = {e["name"]: e["value"] for e in d["env"]}
        self.assertEqual(set(env), {"CC_RUNBOOK_SESSION", "CORRAL_PANE_TOKEN",
                                    "CORRAL_HUB_URL"})
        self.assertEqual(env["CC_RUNBOOK_SESSION"], "p1")
        self.assertEqual(env["CORRAL_HUB_URL"], "http://127.0.0.1:18199")
        self.assertIs(self.mgr.pane_for_token(env["CORRAL_PANE_TOKEN"]),
                      self.mgr.panes["p1"])

    def test_every_spawn_gets_a_fresh_token_and_the_old_one_dies(self):
        """T8.3 + T8.5: two spawns, two tokens; the first is refused."""
        p = _Pane("p1")
        tok = lambda d: {e["name"]: e["value"] for e in d[-1]["env"]}["CORRAL_PANE_TOKEN"]
        t1 = tok(self.servers(p))
        t2 = tok(self.servers(p))
        self.assertNotEqual(t1, t2)
        self.assertIsNone(self.mgr.pane_for_token(t1), "a previous spawn's token still works")
        self.assertIs(self.mgr.pane_for_token(t2), p)

    def test_a_closed_pane_s_token_is_refused(self):
        p = _Pane("p1")
        env = {e["name"]: e["value"] for e in self.servers(p)[-1]["env"]}
        del self.mgr.panes["p1"]
        self.assertIsNone(self.mgr.pane_for_token(env["CORRAL_PANE_TOKEN"]))

    def test_opt_out_ssh_and_no_mcp_lanes_offer_nothing(self):
        os.environ[S.NATIVE_MCP_ENV] = "0"
        self.assertEqual(self.servers(_Pane("p1")), [])
        os.environ.pop(S.NATIVE_MCP_ENV)
        self.assertEqual(self.servers(_Pane("p2", agent="host:box")), [])
        self.assertEqual(self.servers(_Pane("p3", agent="local")), [])
        with mock.patch.object(S, "PEER_HUB_URL", None):
            self.assertEqual(self.servers(_Pane("p4")), [],
                             "offered a tool with no hub to dial")

    def test_registry_descriptors_pass_through_and_the_reserved_name_is_ours(self):
        reg = [{"name": "recall", "command": "x", "args": ["serve", "recall"], "env": []},
               {"name": "corral-seats", "command": "impostor", "args": [], "env": []}]
        out = self.servers(_Pane("p1"), registry=reg)
        self.assertEqual(out[0], reg[0], "a registry descriptor was changed")
        named = [d for d in out if d["name"] == "corral-seats"]
        self.assertEqual(len(named), 1)
        self.assertNotEqual(named[0]["command"], "impostor",
                            "a registry entry took the reserved name")


class TheHubsAnswer(unittest.TestCase):
    """peer_http, the logic both hubs call: the token decides the sender, and
    a body cannot say otherwise (T8.2)."""

    def setUp(self):
        self.mgr = _Mgr()
        self.a, self.b = _Pane("aaa"), _Pane("bbb")
        self.a.seat, self.b.seat = "author", "reviewer"
        self.mgr.panes = {"aaa": self.a, "bbb": self.b}
        self.calls = []
        self.mgr.deliver_peer = lambda src, seat, text: (
            self.calls.append((src, seat, text)) or {"result": "delivered"})
        self.tok = self.mgr.mint_pane_token(self.a)

    def test_no_token_unknown_token(self):
        for t in (None, "", "nope"):
            status, _ = self.mgr.peer_http("POST", "/api/peer/send", t, {})
            self.assertEqual(status, 401)
        self.assertEqual(self.calls, [])

    def test_the_token_is_the_sender_and_from_is_ignored(self):
        status, out = self.mgr.peer_http(
            "POST", "/api/peer/send", self.tok,
            {"seat": "reviewer", "text": "hi", "from": "bbb", "from_pane": "bbb"})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [("aaa", "reviewer", "hi")])

    def test_unknown_route_is_404(self):
        self.assertEqual(self.mgr.peer_http("GET", "/api/peer/anything", self.tok)[0], 404)


class TheCallerIsTheHubsOwnUser(unittest.TestCase):
    """edge.local_peer_uid (DESIGN-5 S8, after the live finding that the Claude
    adapter puts the pane token on a world-readable command line): the uid
    that owns the CLIENT end of a loopback connection, from /proc/net/tcp."""

    @unittest.skipUnless(sys.platform.startswith("linux"), "/proc/net/tcp is Linux")
    def test_a_real_loopback_connection_is_owned_by_this_user(self):
        import socket
        from corral_core import edge
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        cli = socket.create_connection(srv.getsockname())
        conn, peer = srv.accept()
        try:
            self.assertEqual(edge.local_peer_uid(peer, conn.getsockname()), os.getuid())
        finally:
            for s in (cli, conn, srv):
                s.close()

    def test_the_row_for_the_CLIENT_end_decides_not_the_servers(self):
        import tempfile
        from corral_core import edge
        d = tempfile.mkdtemp()
        # Two rows for one connection: the server's accepted socket (uid 1000)
        # and the client's (uid 4242). Only the client row may answer.
        rows = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode",
                "   0: 0100007F:4747 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 1",
                "   1: 0100007F:D431 0100007F:4747 01 00000000:00000000 00:00000000 00000000  4242        0 2"]
        Path(d, "tcp").write_text("\n".join(rows) + "\n")
        self.assertEqual(edge.local_peer_uid(("127.0.0.1", 0xD431), ("127.0.0.1", 0x4747),
                                             proc_net=d), 4242)
        self.assertIsNone(edge.local_peer_uid(("127.0.0.1", 1), ("127.0.0.1", 2), proc_net=d))
        self.assertIsNone(edge.local_peer_uid(("not-an-ip", 1), ("127.0.0.1", 2), proc_net=d))
        self.assertIsNone(edge.local_peer_uid(("127.0.0.1", 1), ("127.0.0.1", 2),
                                              proc_net=d + "/nope"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
