#!/usr/bin/python3
"""The keeper (keeper.py): a pane's agent outlives the hub.

Real processes: the fake ACP agent (testkit/fake_acp_agent.py) under a real
keeper, driven through Pane/Manager, then "the hub dies" (shutdown_notes +
detach, or the Manager simply dropped) and a second Manager picks the pane
back up from its meta.json, as restore() does.

    K1  a new pane runs under a keeper: meta names it, the adapter is the
        keeper's child, snapshot says durable
    K2  a turn in flight when the hub lets go finishes under the next hub:
        its text and turn_end land, the ledger says completed (adopted), the
        adapter pid is unchanged
    K3  a permission card the old hub never answered is raised again on the
        new hub, with a new card id, and answering it ends the turn
    K4  what the agent said while no hub listened is spooled and replayed in
        order, nothing lost
    K5  a hub shutdown note on a durable pane says the turn continues and
        does NOT mark the ledger interrupted; what was queued is
    K6  close() ends the adapter AND the keeper, and removes the socket
    K7  restore() on a second Manager reattaches, reaps nothing, and the
        pane's open turn is not reported as interrupted
    K8  a keeper whose socket is gone: the pane comes back detached, its
        record cleared, and the old group is reaped as before
    K9  update.blockers skips a durable busy pane and keeps a plain one
    K10 the hub-side kill switch: CORRAL_LIGHT_KEEPER=0 forks directly

Collected by test_corral_light.py.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

ROOT = Path(__file__).resolve().parent
FAKE = ROOT / "testkit" / "fake_acp_agent.py"
WAIT_S = 15


def wait_for(pred, timeout=WAIT_S, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


class KeeperCase(unittest.TestCase):
    """Two Managers over one state dir: the hub that dies and the one after."""

    def setUp(self):
        import sessions
        self.sessions = sessions
        self.assertTrue(sessions.KEEPER_ENABLED, "the suite runs with the keeper on")
        self.agent_dir = tmpdir(self, "fake-acp-")
        sessions.AGENTS["fake"] = {
            "label": "Fake", "argv": [sys.executable, str(FAKE)],
            "requires": (str(FAKE),), "posture_via_config_dir": False,
            "tools": True, "env": {"FAKE_ACP_DIR": self.agent_dir}}
        self.addCleanup(sessions.AGENTS.pop, "fake", None)
        self.m1 = self._mgr()
        self.m2 = None
        self.groups = set()
        self.addCleanup(self._cleanup)

    def _mgr(self):
        s = self.sessions
        m = s.Manager.__new__(s.Manager)
        m.panes, m.subscribers, m.not_restored, m.orphans = {}, [], 0, {}
        m._lock = threading.Lock()
        m.catalog = {}
        m.mcp = None
        return m

    def _cleanup(self):
        for m in (self.m2, self.m1):
            if m is None:
                continue
            for pid in list(m.panes):
                try:
                    m.close(pid)
                except Exception:                 # noqa: BLE001
                    pass
        for g in self.groups:                      # whatever a test left behind
            try:
                os.killpg(g, signal.SIGKILL)
            except OSError:
                pass

    # ── helpers ──────────────────────────────────────────────────────────
    def _pane(self):
        p = self.m1.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        self.groups.add(p.pgid)
        return p

    def _hub_dies(self, p):
        """What a SIGTERM does to the hub: notes, detach, and the Manager is
        gone (dropped from the roster so cleanup does not close it)."""
        self.m1.shutdown_notes("SIGTERM")
        self.m1.panes.pop(p.id, None)
        return json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))

    def _next_hub(self, meta):
        self.m2 = self._mgr()
        q = self.sessions.Pane.from_meta(meta, self.m2)
        self.m2.panes[q.id] = q
        return q

    @staticmethod
    def kinds(p):
        return [e["kind"] for e in p.events]

    @staticmethod
    def texts(p):
        return "".join((e.get("data") or {}).get("text", "")
                       for e in p.events if e["kind"] == "text")

    @staticmethod
    def ends(p):
        return [e["data"] for e in p.events if e["kind"] == "turn_end"]

    # ── K1 ───────────────────────────────────────────────────────────────
    def test_k1_a_new_pane_runs_under_a_keeper(self):
        p = self._pane()
        meta = json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))
        k = meta["keeper"]
        self.assertTrue(k and k["sock"] and k["pid"] == p.pgid == p.pid, meta)
        self.assertTrue(os.path.exists(k["sock"]))
        adapter = p.client.p.adapter_pid
        self.assertNotEqual(adapter, k["pid"])
        # The adapter is the keeper's child, in the keeper's group.
        self.assertEqual(os.getpgid(adapter), k["pid"])
        self.assertTrue(p.snapshot()["durable"])
        self.assertTrue(p._durable())

    # ── K2 ───────────────────────────────────────────────────────────────
    def test_k2_a_turn_in_flight_finishes_under_the_next_hub(self):
        p = self._pane()
        adapter = p.client.p.adapter_pid
        p.send("sleep 2")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 2"))
        meta = self._hub_dies(p)
        q = self._next_hub(meta)
        self.assertTrue(q.reattach(), "keeper did not answer")
        self.assertEqual(q.state, "busy")
        self.assertEqual(q._in_flight, "sleep 2")
        self.assertTrue(q._turn_running)
        self.assertEqual(q.client.p.adapter_pid, adapter, "a different agent process")
        self.assertTrue(wait_for(lambda: self.ends(q)))
        end = self.ends(q)[-1]
        self.assertTrue(end.get("adopted"))
        self.assertEqual(end["stopReason"], "end_turn")
        self.assertEqual(q.state, "ready")
        self.assertFalse(q._turn_running)
        turns = q._turns().turns()
        self.assertEqual([t["state"] for t in turns.values()], ["completed"])
        self.assertTrue(list(turns.values())[0].get("adopted"))
        # It keeps working as the same conversation.
        q.send("remember pear")
        self.assertTrue(wait_for(lambda: len(self.ends(q)) == 2))
        q.send("what word did I ask you to remember?")
        self.assertTrue(wait_for(lambda: len(self.ends(q)) == 3))
        self.assertIn("pear", self.texts(q))

    # ── K3 ───────────────────────────────────────────────────────────────
    def test_k3_an_unanswered_card_is_raised_again_and_answering_it_ends_the_turn(self):
        p = self._pane()
        p.send("perm")
        self.assertTrue(wait_for(lambda: p.pending))
        rid = next(iter(p.pending))
        old_card = p.pending[rid]["_gate"]["card"]
        meta = self._hub_dies(p)
        q = self._next_hub(meta)
        self.assertTrue(q.reattach())
        self.assertTrue(wait_for(lambda: q.pending), "the card did not come back")
        self.assertEqual(list(q.pending), [rid])
        self.assertEqual(q.state, "needs-you")
        card = q.pending[rid]["_gate"]
        self.assertNotEqual(card["card"], old_card, "a new hub shows a new card id")
        perm = [e["data"] for e in q.events if e["kind"] == "permission"]
        self.assertTrue(perm and perm[-1]["requestId"] == rid)
        allow = next(o["optionId"] for o in q.pending[rid]["options"]
                     if str(o.get("kind", "")).startswith("allow"))
        self.assertTrue(q.answer(rid, allow, digest=card["digest"], card=card["card"]))
        self.assertTrue(wait_for(lambda: self.ends(q)))
        self.assertIn("permission:", self.texts(q))
        self.assertEqual(q.state, "ready")

    # ── K4 ───────────────────────────────────────────────────────────────
    def test_k4_output_while_no_hub_listened_is_replayed_in_order(self):
        p = self._pane()
        p.send("sleep 1")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 1"))
        meta = self._hub_dies(p)
        time.sleep(2.5)                          # the turn ends with nobody listening
        q = self._next_hub(meta)
        self.assertTrue(q.reattach())
        self.assertTrue(wait_for(lambda: self.ends(q)))
        self.assertTrue((q.client.hello or {}).get("spooled"), q.client.hello)
        self.assertEqual(self.ends(q)[-1]["stopReason"], "end_turn")
        re = [e["data"] for e in q.events if e["kind"] == "reattached"]
        self.assertTrue(re and re[0]["spooled"] >= 1)
        self.assertEqual(q._turns().turns()[meta_turn(q)]["state"], "completed")

    # ── K5 ───────────────────────────────────────────────────────────────
    def test_k5_the_shutdown_note_says_the_turn_continues(self):
        p = self._pane()
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 30"))
        p.send("queued, never sent")
        n = self.m1.shutdown_notes("SIGTERM")
        self.assertEqual(n, 1)
        note = [e["data"] for e in p.events if e["kind"] == "note"][-1]
        self.assertTrue(note.get("durable"))
        self.assertEqual(note["continues"], "sleep 30")
        self.assertEqual(note["queued"], ["queued, never sent"])
        self.assertNotIn("interrupted", note)
        states = {t["text"]: t["state"] for t in p._turns().turns().values()}
        self.assertEqual(states["sleep 30"], "dispatched", "the running turn was closed")
        self.assertEqual(states["queued, never sent"], "interrupted")
        self.assertTrue(p.client._detached)
        self.assertTrue(_alive(p.client.p.adapter_pid), "the agent died with the hub")
        self.m1.panes.pop(p.id)                  # the hub is gone; K-cleanup kills the group

    # ── K6 ───────────────────────────────────────────────────────────────
    def test_k6_close_ends_the_adapter_and_the_keeper(self):
        p = self._pane()
        keeper, adapter, sock = p.pgid, p.client.p.adapter_pid, p.keeper["sock"]
        t0 = time.time()
        self.m1.close(p.id)
        self.assertLess(time.time() - t0, 6, "close took too long")
        self.assertTrue(wait_for(lambda: not _alive(adapter)), "adapter still running")
        self.assertTrue(wait_for(lambda: not _alive(keeper)), "keeper still running")
        self.assertFalse(os.path.exists(sock))
        meta = json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["closed"])
        self.assertIsNone(meta["keeper"])
        self.assertIsNone(meta["pgid"])

    # ── K7 ───────────────────────────────────────────────────────────────
    def test_k7_restore_reattaches_and_reaps_nothing(self):
        import sessions
        state = Path(tmpdir(self, "corral-light-keeper-state-"))
        old_state = sessions.STATE
        sessions.STATE = sessions._core.STATE = state
        self.addCleanup(setattr, sessions, "STATE", old_state)
        self.addCleanup(setattr, sessions._core, "STATE", old_state)
        p = self.m1.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        self.groups.add(p.pgid)
        p.send("sleep 2")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 2"))
        self.m1.shutdown_notes("SIGTERM")
        self.m1.panes.clear()
        m2 = self._mgr()
        self.m2 = m2
        m2.restore()
        self.assertIn(p.id, m2.panes)
        q = m2.panes[p.id]
        self.assertEqual(m2.orphans, {}, "a kept agent was treated as an orphan")
        self.assertIn("reattached", self.kinds(q))
        notes = [e["data"]["text"] for e in q.events if e["kind"] == "note"]
        self.assertFalse(any("interrupted" in n for n in notes), notes)
        self.assertTrue(wait_for(lambda: self.ends(q)))
        self.assertEqual(q._turns().turns()[meta_turn(q)]["state"], "completed")

    # ── K8 ───────────────────────────────────────────────────────────────
    def test_k8_a_gone_keeper_leaves_a_detached_pane_and_reaps_the_group(self):
        import sessions
        state = Path(tmpdir(self, "corral-light-keeper-state-"))
        old_state = sessions.STATE
        sessions.STATE = sessions._core.STATE = state
        self.addCleanup(setattr, sessions, "STATE", old_state)
        self.addCleanup(setattr, sessions._core, "STATE", old_state)
        p = self.m1.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        self.groups.add(p.pgid)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 30"))
        self.m1.shutdown_notes("SIGTERM")
        self.m1.panes.clear()
        os.unlink(p.keeper["sock"])             # the keeper's door is gone
        m2 = self._mgr()
        self.m2 = m2
        m2.restore()
        q = m2.panes[p.id]
        self.assertEqual(q.state, "detached")
        self.assertIsNone(q.keeper)
        self.assertIn(m2.orphans.get(p.id), ("reaped", "killed"), m2.orphans)
        self.assertTrue(wait_for(lambda: not _alive(p.pgid)))
        notes = [e["data"]["text"] for e in q.events if e["kind"] == "note"]
        self.assertTrue(any("interrupted" in n for n in notes), notes)

    # ── K9 ───────────────────────────────────────────────────────────────
    def test_k9_update_skips_a_durable_busy_pane(self):
        import update
        st = {"panes": [{"id": "a", "title": "A", "state": "busy", "durable": True},
                        {"id": "b", "title": "B", "state": "busy"},
                        {"id": "c", "title": "C", "state": "ready",
                         "pending": ["x"], "durable": True},
                        {"id": "d", "title": "D", "state": "ready", "pending": ["x"]}]}
        self.assertEqual([b["id"] for b in update.blockers(st)], ["b", "d"])

    # ── K10 ──────────────────────────────────────────────────────────────
    def test_k10_the_kill_switch_forks_directly(self):
        s = self.sessions
        s.KEEPER_ENABLED = False
        self.addCleanup(setattr, s, "KEEPER_ENABLED", True)
        p = self.m1.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        self.groups.add(p.pgid)
        self.assertIsNone(p.keeper)
        self.assertIsNone(p.client.keeper)
        self.assertFalse(p.snapshot()["durable"])
        meta = json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))
        self.assertIsNone(meta["keeper"])
        self.assertEqual(os.getpgid(p.client.p.pid), p.pgid)


def meta_turn(q):
    """The one turn id in a pane's ledger (these tests send one prompt)."""
    turns = q._turns().turns()
    assert len(turns) == 1, turns
    return next(iter(turns))


class TheKeeperAlone(unittest.TestCase):
    """keeper.py driven directly, without a hub: the parts a Manager hides."""

    def _start(self, work, env=None):
        sock = str(Path(work) / "s.sock")
        full = {**os.environ, "FAKE_ACP_DIR": str(work), **(env or {})}
        kp = subprocess.Popen([sys.executable, "-I", str(ROOT / "keeper.py"), "--sock", sock,
                               "--dir", str(work), "--cwd", str(work), "--",
                               sys.executable, str(FAKE)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=full, start_new_session=True)
        self.addCleanup(self._kill, kp)
        self.assertTrue(wait_for(lambda: os.path.exists(sock)), "no socket")
        return kp, sock

    @staticmethod
    def _kill(kp):
        try:
            os.killpg(kp.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            kp.wait(timeout=3)
        except Exception:                        # noqa: BLE001
            pass

    def test_a_keeper_with_no_hub_for_orphan_s_ends_its_agent(self):
        work = tmpdir(self, "keeper-alone-")
        kp, sock = self._start(work, {"CORRAL_KEEPER_ORPHAN_S": "1"})
        self.assertTrue(wait_for(lambda: kp.poll() is not None, timeout=20),
                        "keeper kept running with no hub")
        pids = [int(f.name[4:]) for f in Path(work).glob("pid-*")]
        self.assertTrue(pids)
        self.assertTrue(wait_for(lambda: not any(_alive(x) for x in pids)))
        self.assertFalse(os.path.exists(sock))

    def test_hello_then_close_frame(self):
        import socket
        work = tmpdir(self, "keeper-alone-")
        kp, sock = self._start(work)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(sock)
        r = c.makefile("rb")
        hello = json.loads(r.readline())
        self.assertEqual(hello["keeper"], "hello")
        self.assertTrue(hello["alive"])
        self.assertEqual(hello["outstanding"], [])
        self.assertEqual(json.loads(r.readline())["keeper"], "replayed")
        c.sendall(b'{"jsonrpc":"2.0","id":5,"method":"initialize","params":{}}\n')
        resp = json.loads(r.readline())
        self.assertEqual(resp["id"], 5)
        c.sendall(b'{"keeper":"close"}\n')
        exit_frame = json.loads(r.readline())
        self.assertEqual(exit_frame["keeper"], "exit")
        self.assertTrue(exit_frame["closed"])
        c.close()
        self.assertTrue(wait_for(lambda: kp.poll() is not None))
        self.assertFalse(os.path.exists(sock))


if __name__ == "__main__":
    unittest.main()


class TheKeeperAfterReview(TheKeeperAlone):
    """The five findings of the 2026-10-30 Codex panel on the keeper
    (reviews/2026-10-30-keeper-review), each with the trace it gave."""

    def _connect(self, sock):
        import socket
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(sock)
        r = c.makefile("rb")
        hello = json.loads(r.readline())
        self.assertEqual(hello["keeper"], "hello")
        return c, r, hello

    def _until_replayed(self, r):
        lines = []
        while True:
            raw = r.readline()
            self.assertTrue(raw, "stream ended before `replayed`")
            msg = json.loads(raw)
            if msg.get("keeper") == "replayed":
                return lines, msg
            lines.append(msg)

    def test_f5_a_reused_dir_does_not_replay_a_previous_keepers_spool(self):
        work = tmpdir(self, "keeper-alone-")
        (Path(work) / "spool.jsonl").write_bytes(b'{"jsonrpc":"2.0","id":1,"result":{"stale":true}}\n')
        kp, sock = self._start(work)
        c, r, hello = self._connect(sock)
        self.assertEqual(hello["spooled"], 0)
        lines, rep = self._until_replayed(r)
        self.assertEqual((lines, rep["n"]), ([], 0))
        c.close()

    def test_f4_a_replaced_hub_gets_eof_and_drives_nothing(self):
        work = tmpdir(self, "keeper-alone-")
        kp, sock = self._start(work)
        a, ra, _ = self._connect(sock)
        self._until_replayed(ra)
        b, rb, hello_b = self._connect(sock)
        self._until_replayed(rb)
        # The stale hub's reader sees EOF at once...
        a.settimeout(5)
        self.assertEqual(ra.readline(), b"", "the replaced hub still had a live stream")
        # ...and nothing it sends reaches the adapter: B never sees a response.
        try:
            a.sendall(b'{"jsonrpc":"2.0","id":77,"method":"initialize","params":{}}\n')
        except OSError:
            pass
        b.sendall(b'{"jsonrpc":"2.0","id":78,"method":"initialize","params":{}}\n')
        got = json.loads(rb.readline())
        self.assertEqual(got["id"], 78)
        b.settimeout(1.5)
        with self.assertRaises(OSError):
            rb.readline()                 # no second response (id 77) ever comes
        b.close()

    def test_f2_a_response_the_hub_never_read_is_replayed_to_the_next_hub(self):
        work = tmpdir(self, "keeper-alone-")
        kp, sock = self._start(work)
        a, ra, _ = self._connect(sock)
        self._until_replayed(ra)
        a.sendall(b'{"jsonrpc":"2.0","id":5,"method":"initialize","params":{}}\n')
        time.sleep(0.5)                   # the adapter answers; the hub dies unread
        a.close()
        b, rb, hello = self._connect(sock)
        self.assertEqual(hello["outstanding"], [{"id": 5, "method": "initialize"}])
        self.assertEqual(hello["next_id"], 6)
        lines, rep = self._until_replayed(rb)
        self.assertEqual([m.get("id") for m in lines], [5], lines)
        self.assertEqual(rep["n"], 1)
        # Acknowledged: a third hub is told nothing is outstanding.
        b.sendall(b'{"keeper":"ack","id":5}\n')
        time.sleep(0.3)
        b.close()
        c, rc, hello3 = self._connect(sock)
        self.assertEqual(hello3["outstanding"], [])
        lines, rep = self._until_replayed(rc)
        self.assertEqual(lines, [])
        c.close()

    def test_f3_a_hub_that_stops_reading_is_dropped_not_waited_on(self):
        work = tmpdir(self, "keeper-alone-")
        kp, sock = self._start(work, {"CORRAL_KEEPER_QUEUE_MAX": "20000"})
        a, ra, _ = self._connect(sock)
        self._until_replayed(ra)
        a.sendall(b'{"jsonrpc":"2.0","id":1,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}\n')
        sid = json.loads(ra.readline())["result"]["sessionId"]
        # Big answers nobody reads: the fake echoes its prompt.
        big = "x" * 6000
        for i in range(2, 12):
            try:
                a.sendall(json.dumps({"jsonrpc": "2.0", "id": i, "method": "session/prompt",
                                      "params": {"sessionId": sid, "prompt": [{"type": "text", "text": big}]}}).encode() + b"\n")
            except OSError:
                break                     # dropped already: the keeper gave up on us
            time.sleep(0.05)
        # Meanwhile a new hub can attach (the lock was never held across a blocked write).
        t0 = time.time()
        b, rb, hello = self._connect(sock)
        self.assertLess(time.time() - t0, 5, "attach blocked behind a hub that stopped reading")
        self.assertTrue(hello["spooled"] >= 1 or hello["unacked"] >= 1, hello)
        lines, rep = self._until_replayed(rb)
        self.assertTrue(rep["n"] >= 1)
        b.close()
        a.close()

    def test_f1_an_adopted_completion_survives_the_exit_frame(self):
        from corral_core import acp
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._init_state()
        c.on_event = lambda *a: None
        c.on_permission = lambda *a: None

        class P:                          # a settled _KeeperProc stand-in
            stdout = None
            rc = None

            def poll(self):
                return 0

            def close_socket(self):
                pass
        c.p = P()
        c.pgid = os.getpid()
        c._pending[7] = {"ev": threading.Event(), "result": None, "error": None}
        c._dispatch({"jsonrpc": "2.0", "id": 7, "result": {"stopReason": "end_turn"}})
        c._reader_ended()
        self.assertEqual(c.await_adopted(7), {"stopReason": "end_turn"})


class TheKeeperAfterReview2(unittest.TestCase):
    _kill = staticmethod(TheKeeperAlone._kill)
    """The 2026-10-31 two-arm panel (Codex + Grok) on the keeper: one test per
    fixed finding. In-process Keeper objects over socketpairs where a race
    must be pinned down, real processes where it need not."""

    def _keeper(self, work=None):
        import keeper
        work = work or tmpdir(self, "keeper-unit-")
        k = keeper.Keeper(["true"], work, str(Path(work) / "s.sock"), work)

        class P:                      # what _attach and _drive read of the adapter
            pid = os.getpid()
            stdin = None

            @staticmethod
            def poll():
                return None
        k.p = P()
        return keeper, k

    @staticmethod
    def _pair():
        import socket
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        return a, b

    def _attach_and_read(self, k, keeper):
        """Attach a new hub; -> (hello, every line until `replayed`)."""
        hub_side, keeper_side = self._pair()
        k._attach(keeper_side)
        r = hub_side.makefile("rb")
        hub_side.settimeout(5)
        hello = json.loads(r.readline())
        lines = []
        while True:
            msg = json.loads(r.readline())
            if msg.get("keeper") == "replayed":
                break
            lines.append(msg)
        self.addCleanup(hub_side.close)
        return hello, lines

    def _stalled_conn(self, k, keeper):
        """A connected hub whose writer never runs (it stopped reading)."""
        _, keeper_side = self._pair()
        c = keeper._Conn(keeper_side, k)
        k.conn = c
        return c

    CARD = (b'{"jsonrpc":"2.0","id":41,"method":"session/request_permission",'
            b'"params":{"sessionId":"s","options":[]}}\n')

    # F4/F5: one card, raised once, never cancelled
    def test_r2_f4_a_card_queued_for_a_stalled_hub_reaches_the_next_hub_once(self):
        keeper, k = self._keeper()
        self._stalled_conn(k, keeper)
        with mock.patch.object(keeper, "QUEUE_MAX_BYTES", 400):
            k._deliver(b'{"jsonrpc":"2.0","method":"session/update","params":{"n":1}}\n')
            k._deliver(self.CARD)
            k._deliver(b'{"jsonrpc":"2.0","method":"session/update","params":{"pad":"'
                       + b"x" * 500 + b'"}}\n')       # overflows: the hub is dropped
        self.assertIsNone(k.conn)
        hello, lines = self._attach_and_read(k, keeper)
        ids = [m["id"] for m in hello["agent_requests"]] + \
              [m["id"] for m in lines if m.get("method") == "session/request_permission"]
        self.assertEqual(ids, [41], "the card reached the next hub other than exactly once")
        # The rest of what the stalled hub never read is replayed, in order.
        self.assertEqual([m["params"].get("n", "pad") for m in lines
                          if m.get("method") == "session/update"], [1, "pad"])

    def test_r2_f4_a_replayed_duplicate_card_is_dropped_not_cancelled(self):
        from corral_core import acp
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._init_state()
        seen, written = [], []
        c.on_event = lambda kind, payload: seen.append(kind)
        c.on_permission = lambda req: seen.append("card")
        c.p = acp._KeeperProc.__new__(acp._KeeperProc)
        c._write = written.append
        c.alive = True
        msg = {"jsonrpc": "2.0", "id": 41, "method": "session/request_permission",
               "params": {"options": []}}
        c._on_request(msg)
        c._on_request(msg)                     # the keeper's replayed copy
        self.assertEqual(seen.count("card"), 1)
        self.assertNotIn("permission_expired", seen)
        self.assertEqual(written, [], "the agent was answered on a card the human holds")

    # F2/F3: a replaced hub's queue and in-flight line go to the next hub, in order
    def test_r2_f2_a_replaced_hubs_queue_reaches_the_new_hub_in_order(self):
        keeper, k = self._keeper()
        c = self._stalled_conn(k, keeper)
        with k.lock:
            c.inflight = b'{"jsonrpc":"2.0","method":"session/update","params":{"n":0}}\n'
            for n in (1, 2):
                c.enqueue_locked(b'{"jsonrpc":"2.0","method":"session/update","params":{"n":%d}}\n' % n)
        hello, lines = self._attach_and_read(k, keeper)
        self.assertEqual([m["params"]["n"] for m in lines], [0, 1, 2])
        self.assertTrue(c.closed)
        self.assertEqual((list(c.q), c.inflight), ([], None))

    # F1: a replaced hub drives nothing, even with a line already read
    def test_r2_f1_a_replaced_hubs_already_read_line_is_refused(self):
        keeper, k = self._keeper()
        wrote = []

        class In:
            def write(self, b):
                wrote.append(b)

            def flush(self):
                pass
        k.p.stdin = In()
        old = self._stalled_conn(k, keeper)
        self.assertTrue(k._drive(old, b'{"jsonrpc":"2.0","id":1,"method":"x"}\n'))
        self._attach_and_read(k, keeper)       # a new hub takes over
        self.assertFalse(k._drive(old, b'{"jsonrpc":"2.0","id":2,"method":"x"}\n'))
        self.assertFalse(k._drive(old, b'{"keeper":"close"}\n'))
        self.assertEqual(len(wrote), 1)
        self.assertFalse(k.closing, "a replaced hub closed the agent")

    def test_r2_f1_attach_waits_for_a_write_in_progress(self):
        keeper, k = self._keeper()
        k.drive_lock.acquire()                 # a write to the adapter is under way
        hub_side, keeper_side = self._pair()
        t = threading.Thread(target=k._attach, args=(keeper_side,), daemon=True)
        t.start()
        time.sleep(0.3)
        self.assertTrue(t.is_alive(), "attach swapped owners mid-write")
        k.drive_lock.release()
        t.join(5)
        self.assertFalse(t.is_alive())
        hub_side.close()

    # F10: the orphan watch and an attach cannot cross
    def test_r2_f10_an_attach_after_the_orphan_claim_is_refused(self):
        keeper, k = self._keeper()
        k._end_adapter = lambda: None
        k.last_hub_seen = time.monotonic() - keeper.ORPHAN_S - 10
        self.assertTrue(k._watch_once())
        hub_side, keeper_side = self._pair()
        k._attach(keeper_side)
        hub_side.settimeout(2)
        self.assertEqual(hub_side.recv(100), b"", "a hub attached to an agent being ended")
        self.assertIsNone(k.conn)

    def test_r2_f10_an_attach_before_the_watch_keeps_the_agent(self):
        keeper, k = self._keeper()
        ended = []
        k._end_adapter = lambda: ended.append(1)
        k.last_hub_seen = time.monotonic() - keeper.ORPHAN_S - 10
        self._attach_and_read(k, keeper)
        self.assertFalse(k._watch_once())
        self.assertEqual(ended, [])

    # F9: an exit queued for a hub that stalled and was dropped reached nobody
    def test_r2_f9_an_exit_queued_for_a_dropped_hub_keeps_the_keeper_lingering(self):
        keeper, k = self._keeper()
        c = self._stalled_conn(k, keeper)
        k.exit_rc = 3
        k._deliver(k._frame("exit", rc=3, stderr_tail=[], closed=False), exit_frame=True)
        with k.lock:
            k._drop_conn_locked("not reading")
            self.assertFalse(k._linger_done_locked(), "quit with the exit unheard")
        hello, _ = self._attach_and_read(k, keeper)
        self.assertFalse(hello["alive"])
        self.assertEqual(hello["rc"], 3)
        self.assertTrue(k._drive(k.conn, b'{"keeper":"ack-exit"}\n'))
        with k.lock:
            k._drop_conn_locked("hub closed")
            self.assertTrue(k._linger_done_locked())

    # F9 (end to end): the keeper lingers until a hub acks the exit
    def test_r2_f9_an_exit_read_but_not_acked_stays_collectable(self):
        work = tmpdir(self, "keeper-alone-")
        kp, sock = TheKeeperAlone._start(self, work)
        import socket
        a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        a.connect(sock)
        ra = a.makefile("rb")
        while json.loads(ra.readline()).get("keeper") != "replayed":
            pass
        a.sendall(b'{"jsonrpc":"2.0","id":1,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}\n')
        sid = json.loads(ra.readline())["result"]["sessionId"]
        a.sendall(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                              "params": {"sessionId": sid,
                                         "prompt": [{"type": "text", "text": "die"}]}}).encode() + b"\n")
        while True:
            m = json.loads(ra.readline())
            if m.get("keeper") == "exit":
                break
        # Read it, never acked it, gone. (Close the reader too: a socket
        # with a makefile still open keeps its descriptor, and the keeper
        # would never see this hub leave.)
        ra.close()
        a.close()
        self.assertFalse(wait_for(lambda: kp.poll() is not None, timeout=2),
                         "the keeper quit before any hub acked the exit")
        b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        b.connect(sock)
        rb = b.makefile("rb")
        hello = json.loads(rb.readline())
        self.assertFalse(hello["alive"])
        b.sendall(b'{"keeper":"ack-exit"}\n')
        time.sleep(0.3)
        rb.close()
        b.close()
        self.assertTrue(wait_for(lambda: kp.poll() is not None, timeout=10),
                        "the keeper lingered after the exit was acked")

    # F8: an old client never unlinks a successor's socket
    def test_r2_f8_closing_an_old_client_leaves_a_successors_socket(self):
        from corral_core import acp
        work = Path(tmpdir(self, "keeper-unit-"))
        path = work / "s.sock"
        path.write_text("old")
        c = acp.AcpClient.__new__(acp.AcpClient)
        c.keeper = {"sock": str(path)}
        c._sock_ino = os.stat(path).st_ino
        path.unlink()
        path.write_text("successor")           # a new keeper's door, same path
        c._unlink_own_socket()
        self.assertTrue(path.exists(), "the old client removed the successor's socket")
        c._sock_ino = os.stat(path).st_ino
        c._unlink_own_socket()
        self.assertFalse(path.exists())

    # F11: the /tmp fallback fails closed
    def test_r2_f11_a_planted_fallback_dir_means_no_keeper(self):
        import sessions
        base = Path(tmpdir(self, "keeper-base-"))
        long_dir = Path("/" + "d" * 120)
        target = base / "elsewhere"
        target.mkdir()
        (base / f"corral-light-{os.getuid()}").symlink_to(target)
        self.assertIsNone(sessions.keeper_sock_path("p1", long_dir, base=str(base)))
        (base / f"corral-light-{os.getuid()}").unlink()
        open_dir = base / f"corral-light-{os.getuid()}"
        open_dir.mkdir()
        os.chmod(open_dir, 0o777)
        self.assertIsNone(sessions.keeper_sock_path("p1", long_dir, base=str(base)))
        os.chmod(open_dir, 0o700)
        self.assertEqual(sessions.keeper_sock_path("p1", long_dir, base=str(base)),
                         open_dir / "k-p1.sock")


class TheExitedAgentsTurnSettles(KeeperCase):
    """F6: a turn that finished while no hub listened, after which the agent
    died, settles on reattach (ledger completed, turn_end) and stays dead."""

    def test_r2_f6_a_finished_turn_of_an_exited_agent_is_settled(self):
        p = self._pane()
        p.send("sleep 1")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 1"))
        adapter = p.client.p.adapter_pid
        meta = self._hub_dies(p)
        time.sleep(2.5)                          # the turn ends with nobody listening
        os.kill(adapter, signal.SIGKILL)         # then the agent dies, still alone
        time.sleep(1.0)
        q = self._next_hub(meta)
        self.assertTrue(q.reattach())
        self.assertTrue(wait_for(lambda: self.ends(q)), "the finished turn never settled")
        self.assertEqual(self.ends(q)[-1]["stopReason"], "end_turn")
        self.assertTrue(self.ends(q)[-1].get("adopted"))
        self.assertEqual(q._turns().turns()[meta_turn(q)]["state"], "completed")
        self.assertTrue(wait_for(lambda: q.state == "dead"), q.state)
