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
  T11.1 seat_wait returns `timed_out` at its bound (a fake clock), and a
        bound outside 1..PEER_WAIT_MAX_S is refused, not clamped.
  T11.2 a second wait while one is in flight is refused `wait-in-flight` --
        in-process, and over real stdio, where the second call's answer
        arrives BEFORE the first's.
  T11.3 the hub's turn route: a `turn_end` with the matching id ends the
        wait, a different id does not; a turn another pane sent is unknown.
  S11   a poll that fails after the first ends the wait `interrupted`; a
        paused/dead seat ends it `blocked`; no answer carries text.
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
        self.get_answer = None        # path -> (obj, code); None = the seats
        rec = self.calls
        stub = self

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
                if stub.get_answer is not None:
                    return self._reply(*stub.get_answer(self.path))
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
        self.srv.server_close()


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
                         ["seat_list", "seat_send", "seat_broadcast", "seat_wait",
                          "ask_human"])
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

    def test_ask_human_is_offered_and_tells_the_model_prose_raises_nothing(self):
        """The tool exists because prose at the end of a turn is
        indistinguishable from a pane that simply finished: the description
        must say so, and say to END the turn after calling it."""
        a = {t["name"]: t for t in seat_mcp.TOOLS}["ask_human"]
        self.assertEqual(a["inputSchema"]["required"], ["question"])
        self.assertFalse(a["inputSchema"]["additionalProperties"])
        self.assertEqual(a["inputSchema"]["properties"]["question"]["maxLength"],
                         seat_mcp.MAX_ASK_CHARS)
        d = a["description"]
        self.assertIn("end your turn", d)
        self.assertIn("prose alone", d.lower())

    def test_ask_human_posts_only_the_question_with_the_token(self):
        """Only `question` leaves this process: a `pane` or `from` a caller
        adds cannot aim the question at another pane -- the token decides."""
        out, is_error = seat_mcp.call_tool(
            "ask_human", {"question": "re-scope?", "pane": "other", "from": "op"},
            env=self.env)
        self.assertFalse(is_error)
        method, path, headers, body = self.hub.calls[-1]
        self.assertEqual((method, path), ("POST", "/api/peer/ask"))
        self.assertEqual(headers.get("X-Corral-Pane-Token"), "tok-123")
        self.assertEqual(body, {"question": "re-scope?"})

    def test_ask_human_over_the_bound_is_refused_not_truncated(self):
        """MAX_ASK_CHARS is a refusal, never a silent clip, and nothing is
        sent: a clipped question could lose the one clause that matters."""
        for bad in ({}, {"question": 7}, {"question": "   "},
                    {"question": "x" * (seat_mcp.MAX_ASK_CHARS + 1)}):
            out, is_error = seat_mcp.call_tool("ask_human", bad, env=self.env)
            self.assertEqual((out["result"], is_error), ("refused", False), bad)
        self.assertEqual(self.hub.calls, [], "a refused ask reached the hub")
        out, _ = seat_mcp.call_tool("ask_human",
                                    {"question": "x" * seat_mcp.MAX_ASK_CHARS},
                                    env=self.env)
        self.assertEqual(len(self.hub.calls[-1][3]["question"]), seat_mcp.MAX_ASK_CHARS)

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


class FakeClock:
    """time.monotonic + time.sleep that only move when slept."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class TheWait(unittest.TestCase):
    def setUp(self):
        self.hub = StubHub()
        self.addCleanup(self.hub.close)
        self.env = {"CORRAL_HUB_URL": self.hub.url, "CORRAL_PANE_TOKEN": "tok-123",
                    "CC_RUNBOOK_SESSION": "pane1"}
        self.clock = FakeClock()

    def seats(self, display):
        self.hub.get_answer = lambda path: ({"you": "author", "seats": [
            {"seat": "reviewer", "display": display, "lane": "claude",
             "tool": True}]}, 200)

    def wait(self, **args):
        return seat_mcp.seat_wait(args, env=self.env, clock=self.clock,
                                  sleep=self.clock.sleep)

    def test_the_tool_is_offered_with_its_bounds(self):
        w = {t["name"]: t for t in seat_mcp.TOOLS}["seat_wait"]
        self.assertEqual(w["inputSchema"]["required"], ["seat"])
        self.assertFalse(w["inputSchema"]["additionalProperties"])
        self.assertEqual(w["inputSchema"]["properties"]["timeout_s"]["maximum"],
                         seat_mcp.PEER_WAIT_MAX_S)
        self.assertEqual(w["inputSchema"]["properties"]["until"]["enum"],
                         list(seat_mcp.WAIT_UNTIL))
        self.assertIn("NEVER what the other agent wrote", w["description"])
        self.assertEqual((seat_mcp.PEER_WAIT_S, seat_mcp.PEER_WAIT_MAX_S), (120, 600))

    def test_T11_1_a_wait_times_out_at_its_bound(self):
        self.seats("working")
        out, is_error = self.wait(seat="reviewer", until="your-turn", timeout_s=5)
        self.assertFalse(is_error)
        self.assertEqual((out["result"], out["state"], out["turn_ended"],
                          out["waited_s"]), ("timed_out", "working", False, 5.0))
        self.assertAlmostEqual(sum(self.clock.slept), 5.0)
        self.assertTrue(all(s <= seat_mcp.PEER_WAIT_POLL_S for s in self.clock.slept))
        # The default bound is PEER_WAIT_S, not forever.
        self.clock = FakeClock()
        out, _ = self.wait(seat="reviewer", until="your-turn")
        self.assertEqual((out["result"], out["waited_s"]),
                         ("timed_out", float(seat_mcp.PEER_WAIT_S)))

    def test_T11_1_a_bound_out_of_range_is_refused_not_clamped(self):
        for t in (0, seat_mcp.PEER_WAIT_MAX_S + 1, "60", True, None):
            out, is_error = self.wait(seat="reviewer", until="idle", timeout_s=t)
            self.assertEqual((out["result"], out["reason"], is_error),
                             ("refused", "arguments", False), t)
        for args in ({"seat": "reviewer"},                       # neither
                     {"seat": "reviewer", "turn": "t", "until": "idle"},  # both
                     {"seat": "reviewer", "until": "working"},   # not a wait state
                     {"until": "idle"}):                          # no seat
            out, _ = self.wait(**args)
            self.assertEqual(out["reason"], "arguments", args)
        self.assertEqual(self.hub.calls, [], "a refused wait asked the hub")

    def test_T11_2_a_second_wait_in_flight_is_refused(self):
        self.seats("working")
        entered, release = threading.Event(), threading.Event()

        def slow_sleep(s):
            entered.set()
            release.wait(10)
            self.clock.t += 100
        first = {}
        t = threading.Thread(target=lambda: first.update(out=seat_mcp.seat_wait(
            {"seat": "reviewer", "until": "idle", "timeout_s": 50}, env=self.env,
            clock=self.clock, sleep=slow_sleep)[0]))
        t.start()
        self.assertTrue(entered.wait(10))
        out, is_error = seat_mcp.call_tool("seat_wait", {"seat": "reviewer",
                                                         "until": "idle"}, env=self.env)
        self.assertEqual((out["result"], out["reason"], is_error),
                         ("refused", "wait-in-flight", False))
        release.set()
        t.join(10)
        self.assertEqual(first["out"]["result"], "timed_out")
        # ... and once it is over, the next wait is allowed.
        self.seats("your-turn")
        self.assertEqual(self.wait(seat="reviewer", until="your-turn")[0]["result"],
                         "reached")

    def test_T11_2_over_real_stdio_the_second_answer_comes_first(self):
        self.seats("working")
        env = dict(os.environ, **self.env)
        call = lambda i, a: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                             "params": {"name": "seat_wait", "arguments": a}}
        p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=env)
        self.addCleanup(p.kill)
        p.stdin.write(json.dumps(call(1, {"seat": "reviewer", "until": "your-turn",
                                          "timeout_s": 2})) + "\n")
        p.stdin.flush()
        # Only send the second once the first has reached the hub.
        for _ in range(100):
            if self.hub.calls:
                break
            threading.Event().wait(0.05)
        p.stdin.write(json.dumps(call(2, {"seat": "reviewer", "until": "idle"})) + "\n")
        p.stdin.close()
        replies = [json.loads(p.stdout.readline()) for _ in range(2)]
        p.wait(10)
        p.stdout.close()
        self.assertEqual([r["id"] for r in replies], [2, 1])
        second = json.loads(replies[0]["result"]["content"][0]["text"])
        first = json.loads(replies[1]["result"]["content"][0]["text"])
        self.assertEqual(second["reason"], "wait-in-flight")
        self.assertEqual(first["result"], "timed_out")

    def test_T11_3_only_the_matching_turn_end_ends_the_wait(self):
        """The child's half: it keeps polling while the hub says the turn has
        not ended, and stops the poll after it says it has."""
        answers = iter([False, False, True])
        self.hub.get_answer = lambda path: ({
            "result": "turn", "seat": "reviewer", "turn": "t1",
            "ended": next(answers), "not_run": None, "stop_reason": "end_turn",
            "display": "your-turn"}, 200)
        out, _ = self.wait(seat="@reviewer", turn="t1", timeout_s=60)
        self.assertEqual((out["result"], out["turn_ended"], out["turn"],
                          out["stop_reason"]), ("reached", True, "t1", "end_turn"))
        self.assertEqual(len(self.hub.calls), 3)
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.hub.calls[0][1])
        self.assertEqual(u.path, "/api/peer/turn")
        self.assertEqual(parse_qs(u.query), {"seat": ["reviewer"], "turn": ["t1"]})
        self.assertEqual(self.hub.calls[0][2].get("X-Corral-Pane-Token"), "tok-123")
        self.assertNotIn("text", out)

    def test_a_withdrawn_turn_is_not_run(self):
        self.hub.get_answer = lambda path: ({
            "result": "turn", "ended": False, "not_run": "card-pending",
            "display": "needs-you"}, 200)
        out, _ = self.wait(seat="reviewer", turn="t1")
        self.assertEqual((out["result"], out["why"], out["turn_ended"]),
                         ("not-run", "card-pending", False))

    def test_until_your_turn_is_met_by_idle_and_paused_is_blocked(self):
        self.seats("idle")
        self.assertEqual(self.wait(seat="reviewer", until="your-turn")[0]["result"],
                         "reached")
        self.seats("paused")
        out, _ = self.wait(seat="reviewer", until="your-turn")
        self.assertEqual((out["result"], out["state"]), ("blocked", "paused"))
        self.seats("dead")
        self.assertEqual(self.wait(seat="reviewer", until="dead")[0]["result"],
                         "reached")

    def test_self_and_an_unknown_seat_are_refused(self):
        self.seats("working")
        out, _ = self.wait(seat="author", until="idle")
        self.assertEqual((out["result"], out["reason"]), ("refused", "self"))
        out, _ = self.wait(seat="nobody", until="idle")
        self.assertEqual((out["result"], out["reason"]), ("refused", "unknown-seat"))

    def test_the_hub_going_away_mid_wait_is_interrupted(self):
        """A restarted hub revokes every token (401) and is briefly not
        listening at all; both end the wait `interrupted`, never a retry
        loop and never `timed_out`."""
        import socket
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            dead_url = f"http://127.0.0.1:{sk.getsockname()[1]}"
        live_url = self.env["CORRAL_HUB_URL"]
        for code in (401, None):
            n = {"polls": 0}
            self.env["CORRAL_HUB_URL"] = live_url

            def answer(path, code=code):
                n["polls"] += 1
                if n["polls"] == 1:
                    if code is None:          # next poll: nothing listening
                        self.env["CORRAL_HUB_URL"] = dead_url
                    return ({"you": "author", "seats": [
                        {"seat": "reviewer", "display": "working"}]}, 200)
                return ({"error": "unknown or expired pane token"}, 401)
            self.hub.get_answer = answer
            self.clock = FakeClock()
            out, is_error = self.wait(seat="reviewer", until="idle", timeout_s=30)
            self.assertEqual((out["result"], out["state"], is_error),
                             ("interrupted", "working", False), code)
            if code is None:
                self.assertIn("unreachable", out["why"])
            else:
                self.assertIn("401", out["why"])
        # The FIRST poll failing is a failure to start, not an interruption.
        out, is_error = self.wait(seat="reviewer", until="idle")
        self.assertEqual((out["result"], is_error), ("failed", True))


class ThePeerTurnRoute(unittest.TestCase):
    """T11.3, the hub's half: ManagerBase.peer_turn over real event rings."""

    def setUp(self):
        self.m = _Mgr()
        self.a = _RingPane("aaa", "author")
        self.b = _RingPane("bbb", "reviewer")
        self.c = _RingPane("ccc", "critic")
        self.m.panes = {p.id: p for p in (self.a, self.b, self.c)}

    def ask(self, src, turn, seat="reviewer"):
        return self.m.peer_turn(src.id, seat, turn)

    def test_T11_3_a_matching_turn_end_ends_it_a_different_one_does_not(self):
        self.b.ev("peer", from_pane="aaa", turn="t1")
        r = self.ask(self.a, "t1")
        self.assertEqual((r["result"], r["ended"], r["display"]),
                         ("turn", False, "working"))
        self.b.ev("turn_end", turn="t0", stopReason="end_turn")   # someone else's
        self.b.ev("turn_end", turn=None, stopReason="end_turn")   # a human's
        self.assertFalse(self.ask(self.a, "t1")["ended"])
        self.b.ev("turn_end", turn="t1", stopReason="cancelled")
        self.b.state = "ready"
        r = self.ask(self.a, "t1")
        self.assertEqual((r["ended"], r["stop_reason"], r["display"]),
                         (True, "cancelled", "your-turn"))
        self.assertNotIn("text", r)

    def test_a_turn_another_pane_sent_is_unknown(self):
        self.b.ev("peer", from_pane="ccc", turn="t2")
        self.b.ev("turn_end", turn="t2")
        for src, turn in ((self.a, "t2"), (self.a, "nope"), (self.a, None)):
            r = self.ask(src, turn)
            self.assertEqual((r["result"], r["reason"]), ("refused", "unknown-turn"))
        self.assertTrue(self.ask(self.c, "t2")["ended"])

    def test_withdrawn_unknown_seat_and_self(self):
        self.b.ev("peer", from_pane="aaa", turn="t3")
        self.b.ev("peer_result", turn="t3", delivered=False, reason="card-pending")
        r = self.ask(self.a, "t3")
        self.assertEqual((r["ended"], r["not_run"]), (False, "card-pending"))
        self.assertEqual(self.ask(self.a, "t3", seat="nobody")["reason"], "unknown-seat")
        self.assertEqual(self.ask(self.a, "t3", seat="author")["reason"], "self")

    def test_the_route_reads_the_query_and_wants_the_token(self):
        self.b.ev("peer", from_pane="aaa", turn="t4")
        tok = self.m.mint_pane_token(self.a)
        st, r = self.m.peer_http("GET", "/api/peer/turn", tok,
                                 {"seat": "reviewer", "turn": "t4"})
        self.assertEqual((st, r["result"], r["ended"]), (200, "turn", False))
        st, _ = self.m.peer_http("GET", "/api/peer/turn", "forged",
                                 {"seat": "reviewer", "turn": "t4"})
        self.assertEqual(st, 401)


class _RingPane:
    """What peer_turn reads off a pane: a seat, an event ring, a state."""

    def __init__(self, pid, seat):
        self.id, self.seat, self.seat_withheld = pid, seat, False
        self.agent, self.state, self.pending = "claude", "busy", {}
        self.events, self._lock = [], threading.Lock()
        self.last_activity = __import__("time").time()

    def ev(self, kind, **data):
        self.events.append({"seq": len(self.events) + 1, "kind": kind, "data": data})


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

    def test_ask_goes_to_the_token_s_pane_and_only_the_question_is_read(self):
        got = []
        self.mgr.ask_human = lambda pid, q: got.append((pid, q)) or {"result": "raised"}
        status, out = self.mgr.peer_http(
            "POST", "/api/peer/ask", self.tok,
            {"question": "which way?", "pane": "bbb", "from_pane": "bbb"})
        self.assertEqual((status, out["result"]), (200, "raised"))
        self.assertEqual(got, [("aaa", "which way?")])
        self.assertEqual(self.mgr.peer_http("POST", "/api/peer/ask", "nope",
                                            {"question": "q"})[0], 401)
        self.assertEqual(len(got), 1, "an unknown token reached ask_human")

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
