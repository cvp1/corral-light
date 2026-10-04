#!/usr/bin/env python3
"""The performance plan's contracts (docs/PERF-REVIEW-2026-10-04.md).

Over a real socket where the contract is the hub's: the light delta, the
full document, /api/lanes, the batched seen mark, /health's code fields.
In-process for the availability worker, the cached cookie key, and consult's
lane read and fan-out.

    python3 -m unittest test_perf_paths
"""
import http.client
import io
import json
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("light-perf-paths-")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sessions                                                   # noqa: E402

LANES = [{"key": "claude", "label": "Claude", "available": True, "why": ""}]
AUTH = {"ok": True}


def write_meta(root, pid, **kw):
    d = root / "panes" / pid
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": pid, "agent": "claude", "cwd": str(Path.home()), "posture": "auto",
         "title": f"t {pid}", "title_locked": True,
         "created": "2026-10-04T10:00:00Z", "closed": False}
    m.update(kw)
    (d / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "events.jsonl").write_text("", encoding="utf-8")


class HubContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import auth
        import claude_auth
        import hub
        from http.server import ThreadingHTTPServer
        cls.hub = hub
        cls.root = Path(tmpdir(cls, "perf-paths-"))
        (cls.root / "panes").mkdir()
        cls.patches = [mock.patch.object(m, "STATE", cls.root)
                       for m in (sessions, sessions._core)]
        # No live lane probes from a test: the availability source is stubbed.
        cls.patches += [mock.patch.object(sessions, "available_agents", lambda: LANES),
                        mock.patch.object(claude_auth, "status", lambda *a, **k: AUTH)]
        for p in cls.patches:
            p.start()
        write_meta(cls.root, "aaa", created="2026-10-04T10:00:00Z")
        write_meta(cls.root, "bbb", created="2026-10-04T10:00:01Z")
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog, m.mcp, m.orphans = {}, None, {}
        m.restore()
        for p in m.panes.values():
            p.emit("note", {"text": "one"})
            p.emit("note", {"text": "two"})
        cls.mgr = m
        cls._old, hub.MGR = hub.MGR, m
        cls.cookie = f"{hub.COOKIE}={auth.mint()}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.hub.MGR = cls._old
        for p in cls.patches:
            p.stop()

    def req(self, method, path, body=None, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            h.update({"Content-Type": "application/json",
                      "Content-Length": str(len(raw)),
                      "Origin": f"http://127.0.0.1:{self.port}"})
        if cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def since(self, d, full=False):
        from urllib.parse import quote
        return "/api/state?" + ("full=1&" if full else "") + "since=" + quote(json.dumps(d))

    def test_no_cursor_is_the_whole_document(self):
        st, out = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(out["agents"], LANES)
        for k in sessions.Manager.LIGHT_OMITS:
            self.assertIn(k, out, k)
        for p in out["panes"]:
            self.assertIn("commands", p)
            self.assertIn("config", p)
        self.assertNotIn("light", out)

    def test_a_cursor_without_full_is_the_light_delta(self):
        seq = self.mgr.panes["aaa"].snapshot(1 << 40)["seq"]
        st, out = self.req("GET", self.since({"aaa": seq}))
        self.assertEqual(st, 200)
        self.assertTrue(out.get("light"))
        for k in sessions.Manager.LIGHT_OMITS:
            self.assertNotIn(k, out, k)
        panes = {p["id"]: p for p in out["panes"]}
        # A pane the client holds: no commands or config, and no old events.
        self.assertNotIn("commands", panes["aaa"])
        self.assertNotIn("config", panes["aaa"])
        self.assertEqual(panes["aaa"]["events"], [])
        # A pane new to the client (cursor 0) still arrives whole.
        self.assertIn("commands", panes["bbb"])
        self.assertIn("config", panes["bbb"])
        self.assertTrue(panes["bbb"]["events"])
        # What pollers read is still there.
        for k in ("state", "seq", "pending", "model", "effort"):
            self.assertIn(k, panes["aaa"], k)

    def test_full_with_a_cursor_is_whole_but_still_a_delta_of_events(self):
        seq = self.mgr.panes["aaa"].snapshot(1 << 40)["seq"]
        st, out = self.req("GET", self.since({"aaa": seq, "bbb": seq}, full=True))
        self.assertEqual(st, 200)
        self.assertEqual(out["agents"], LANES)
        for p in out["panes"]:
            self.assertIn("commands", p)
            self.assertEqual(p["events"], [])

    def test_a_cursor_that_does_not_parse_gets_the_whole_document(self):
        st, out = self.req("GET", "/api/state?since=not-json")
        self.assertEqual(st, 200)
        self.assertIn("agents", out)
        self.assertNotIn("light", out)

    def test_lanes_route_is_the_lane_list_behind_the_cookie(self):
        self.assertEqual(self.req("GET", "/api/lanes", cookie=False)[0], 401)
        st, out = self.req("GET", "/api/lanes")
        self.assertEqual(st, 200)
        self.assertEqual(out["agents"], LANES)
        self.assertIn("checkedAt", out)
        self.assertNotIn("panes", out)

    def test_health_names_the_running_code(self):
        st, out = self.req("GET", "/health", cookie=False)
        self.assertEqual(st, 200)
        self.assertEqual(out["code"], self.hub.CODE_FINGERPRINT)
        self.assertRegex(out["code"], r"^[0-9a-f]{12}$")
        self.assertIsInstance(out["started_at"], int)
        self.assertFalse(out["code_stale"])
        with mock.patch.object(self.hub, "CODE_STAT", {"hub.py": (0, 0)}):
            self.assertTrue(self.req("GET", "/health", cookie=False)[1]["code_stale"])

    def test_consult_wakes_on_the_stream_not_the_poll_interval(self):
        """A turn that ends 0.4 s in is seen in well under POLL_S (set to 5 s):
        the doorbell, not the sleep, ended the wait."""
        import consult
        saved = consult.POLL_S
        consult.POLL_S = 5
        try:
            h = consult.Hub(f"http://127.0.0.1:{self.port}")
            h.token = self.cookie.split("=", 1)[1]
            p = self.mgr.panes["aaa"]
            seq0 = p.snapshot(1 << 40)["seq"]
            consult._waker(h)
            time.sleep(0.3)                   # let the stream connect

            def talk():
                time.sleep(0.4)
                p.emit("user", {"text": "q"})
                p.emit("text", {"text": "pong"})
                p.emit("turn_end", {"stopReason": "end_turn"})
            threading.Thread(target=talk, daemon=True).start()
            t = time.time()
            r = consult.wait_turn(h, "aaa", seq0, timeout_s=20, own="q")
            took = time.time() - t
            self.assertTrue(r["complete"], r)
            self.assertEqual(r["text"], "pong")
            self.assertIsNone(h.waker.dead)
            self.assertLess(took, 2.0, f"took {took:.2f}s: slept the poll interval")
        finally:
            consult.POLL_S = saved

    def test_seen_marks_many_panes_in_one_post_and_skips_strangers(self):
        st, out = self.req("POST", "/api/session/seen",
                           {"seen": {"aaa": 2, "bbb": 1, "nosuch": 9}})
        self.assertEqual(st, 200, out)
        self.assertEqual(out["seen"], {"aaa": 2, "bbb": 1})
        self.assertEqual(self.hub.SEEN["aaa"], 2)
        # The single form still works.
        st, out = self.req("POST", "/api/session/seen", {"pane": "aaa", "seq": 3})
        self.assertEqual((st, out["seen"]), (200, 3), out)


class AvailabilityWorker(unittest.TestCase):
    def test_without_start_every_read_is_fresh(self):
        calls = []
        with mock.patch.object(sessions, "available_agents",
                               lambda: calls.append(1) or LANES), \
                mock.patch.object(sessions.claude_auth, "status", lambda *a, **k: AUTH):
            a = sessions.Availability()
            a.read()
            a.read()
        self.assertEqual(len(calls), 2)

    def test_started_reads_serve_the_last_result_without_probing(self):
        calls = []
        with mock.patch.object(sessions, "available_agents",
                               lambda: calls.append(1) or LANES), \
                mock.patch.object(sessions.claude_auth, "status", lambda *a, **k: AUTH):
            a = sessions.Availability(period=3600)
            a.start()
            deadline = time.time() + 5
            while a.agents is None and time.time() < deadline:
                time.sleep(0.01)
            n = len(calls)
            for _ in range(50):
                agents, auth_, at = a.read()
            self.assertEqual(agents, LANES)
            self.assertEqual(auth_, AUTH)
            self.assertGreater(at, 0)
            self.assertEqual(len(calls), n, "a read ran the probe")
            a.invalidate()                     # a spawn failed: recheck now
            deadline = time.time() + 5
            while len(calls) == n and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(calls), n + 1)

    def test_a_probe_that_raises_keeps_the_previous_answer(self):
        state = {"boom": False}

        def agents():
            if state["boom"]:
                raise RuntimeError("probe failed")
            return LANES
        with mock.patch.object(sessions, "available_agents", agents), \
                mock.patch.object(sessions.claude_auth, "status", lambda *a, **k: AUTH):
            a = sessions.Availability(period=3600)
            a._thread = object()               # "started", without a thread
            self.assertEqual(a.read()[0], LANES)
            state["boom"] = True
            a.refresh()
            self.assertEqual(a.read()[0], LANES)
            self.assertEqual(a.errors, 1)


class EmitPerf(unittest.TestCase):
    def test_off_by_default_and_bounded_when_on(self):
        core = sessions._core
        self.assertIsNone(core.emit_perf_snapshot())   # CORRAL_PERF unset in tests
        with mock.patch.object(core, "EMIT_PERF", True), \
                mock.patch.dict(core.EMIT_STATS, {"n": 0, "total_ms": 0.0, "max_ms": 0.0,
                                                  "over_ms": {"1": 0, "10": 0, "100": 0}}):
            for ms in (0.5, 5, 50, 500):
                core._emit_perf_record(ms)
            s = core.emit_perf_snapshot()
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["max_ms"], 500)
        self.assertEqual(s["over_ms"], {"1": 3, "10": 2, "100": 1})


class CookieKey(unittest.TestCase):
    def setUp(self):
        import auth
        self.auth = auth
        self.saved = (auth.STATE, auth.KEYFILE)
        d = Path(tmpdir(self, "perf-key-"))
        auth.STATE, auth.KEYFILE = d, d / "session.key"
        auth._SECRET_CACHE.clear()

    def tearDown(self):
        self.auth.STATE, self.auth.KEYFILE = self.saved
        self.auth._SECRET_CACHE.clear()

    def test_cached_key_still_honours_a_replaced_key_file(self):
        tok = self.auth.mint()
        self.assertEqual(self.auth.verify(tok), "owner")
        with mock.patch.object(Path, "read_bytes",
                               side_effect=AssertionError("re-read an unchanged key")):
            self.assertEqual(self.auth.verify(tok), "owner")
        # Revoke every session by replacing the key: the old cookie must fail.
        self.auth.KEYFILE.write_bytes(b"\x01" * 32)
        self.assertIsNone(self.auth.verify(tok))
        # And by deleting it: a fresh key, the old cookie still fails.
        self.auth.KEYFILE.unlink()
        self.assertIsNone(self.auth.verify(tok))


class ConsultPaths(unittest.TestCase):
    def setUp(self):
        import consult
        self.c = consult
        consult.POLL_S = 0

    def test_lanes_falls_back_to_state_on_an_older_hub(self):
        c = self.c

        class Old:
            def get(self, path, timeout=None):
                if path == "/api/lanes":
                    raise c.ConsultError("GET /api/lanes -> 404: not found")
                return {"agents": LANES, "panes": []}
        self.assertEqual(c.lanes(Old()), LANES)

        class New:
            def get(self, path, timeout=None):
                assert path == "/api/lanes", path
                return {"agents": LANES}
        self.assertEqual(c.lanes(New()), LANES)

    def _fanout(self, argv, arms):
        """Run fanout with open/send stubbed: arms = {lane: (delay_s, ok)}."""
        c = self.c
        opened = []

        class Stub:
            def get(self, path, timeout=None):
                return {"agents": [{"key": k, "label": k, "available": True}
                                   for k in arms]}

            def post(self, path, body, timeout=None):
                return {"ok": True}

        def open_pane(hub, lane, *a, live=None, **k):
            assert live is not None, "fan-out re-read the lanes per arm"
            opened.append(lane)
            return {"id": f"pane-{lane}"}

        def send_and_wait(hub, pid, text, timeout, label=""):
            delay, ok = arms[label]
            time.sleep(delay)
            return {"pane": pid, "lane": label, "complete": ok, "text": "x" if ok else "",
                    "ok": ok}
        out = io.StringIO()
        with mock.patch.object(c, "connect", lambda url: Stub()), \
                mock.patch.object(c, "open_pane", open_pane), \
                mock.patch.object(c, "send_and_wait", send_and_wait), \
                redirect_stdout(out):
            t = time.time()
            rc = c.main(["fanout", "--prompt", "q"] + argv)
            took = time.time() - t
        return rc, out.getvalue(), took, opened

    def test_arms_open_in_parallel(self):
        rc, out, took, opened = self._fanout(
            ["--lane", "a", "--lane", "b", "--lane", "c"],
            {"a": (0.3, True), "b": (0.3, True), "c": (0.3, True)})
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(opened), ["a", "b", "c"])
        self.assertLess(took, 0.8, "arms ran one after another")
        self.assertEqual(len(json.loads(out)["arms"]), 3)

    def test_min_arms_returns_early_and_lists_the_rest_running(self):
        rc, out, took, _ = self._fanout(
            ["--lane", "fast", "--lane", "slow", "--min-arms", "1"],
            {"fast": (0.05, True), "slow": (5, True)})
        self.assertEqual(rc, 0)
        self.assertLess(took, 2, "waited for the slow arm")
        doc = json.loads(out)
        self.assertEqual([a["lane"] for a in doc["arms"]], ["fast"])
        self.assertEqual(doc["running"], [{"pane": "pane-slow", "lane": "slow",
                                           "phase": "answering"}])

    def test_a_failed_arm_does_not_count_toward_min_arms(self):
        rc, out, took, _ = self._fanout(
            ["--lane", "bad", "--lane", "good", "--min-arms", "1"],
            {"bad": (0.01, False), "good": (0.2, True)})
        doc = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(a["lane"] for a in doc["arms"]), ["bad", "good"])
        self.assertNotIn("running", doc)

    def test_stream_prints_each_arm_as_it_lands_then_done(self):
        rc, out, took, _ = self._fanout(
            ["--lane", "a", "--lane", "b", "--stream"],
            {"a": (0.01, True), "b": (0.2, True)})
        lines = [json.loads(x) for x in out.strip().splitlines()]
        self.assertEqual([x["arm"]["lane"] for x in lines[:2]], ["a", "b"])
        self.assertEqual(lines[2], {"done": True, "ok": 2, "running": []})

    def test_min_arms_out_of_range_is_refused(self):
        rc, out, took, _ = self._fanout(["--lane", "a", "--min-arms", "2"],
                                        {"a": (0, True)})
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
