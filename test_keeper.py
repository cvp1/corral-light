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
import collections
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


class _Spawn:
    """Start a real keeper process over the fake agent (a mixin: no tests)."""

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


class TheKeeperAlone(_Spawn, unittest.TestCase):
    """keeper.py driven directly, without a hub: the parts a Manager hides."""

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
        self.assertEqual(hello["version"], 3)
        self.assertEqual(json.loads(r.readline())["keeper"], "caught-up")
        c.sendall(b'{"jsonrpc":"2.0","id":5,"method":"initialize","params":{}}\n')
        resp = json.loads(r.readline())
        self.assertEqual((resp["keeper"], resp["s"], resp["m"]["id"]), ("l", 1, 5))
        c.sendall(b'{"keeper":"close"}\n')
        exit_frame = json.loads(r.readline())
        self.assertEqual(exit_frame["m"]["keeper"], "exit")
        self.assertTrue(exit_frame["m"]["closed"])
        c.close()
        self.assertTrue(wait_for(lambda: kp.poll() is not None))
        self.assertFalse(os.path.exists(sock))




class ClientEdges(unittest.TestCase):
    """Client-side guarantees, without a keeper process."""

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
        got = sessions.keeper_sock_path("p1", long_dir, base=str(base))
        self.assertEqual(got.parent, open_dir)
        self.assertRegex(got.name, r"^k-p1-[0-9a-f]{8}\.sock$")
        # A name of its own per keeper: a second call never shares the path.
        self.assertNotEqual(got, sessions.keeper_sock_path("p1", long_dir, base=str(base)))



# ── protocol 3: the keeper's delivery contract ───────────────────────────
# docs/DURABLE-RUNS.md "The keeper's delivery contract". An in-process
# Keeper with a stub adapter; hubs are socketpairs read synchronously, so
# every frame a hub gets is known before the next step.

class _StubIn:
    """The adapter's stdin. `gate` unset: a write blocks (the agent is not
    reading)."""

    def __init__(self):
        self.lines = []
        self.gate = threading.Event()
        self.gate.set()

    def write(self, b):
        self.gate.wait()
        self.lines.append(b)

    def flush(self):
        pass


class _StubP:
    pid = 999999

    def __init__(self):
        self.stdin = _StubIn()
        self.rc = None
        self.terminated = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = -15

    def wait(self, timeout=None):
        return self.rc

    def kill(self):
        self.rc = -9


def _j(obj):
    return (json.dumps(obj) + "\n").encode("utf-8")


def _note(n):
    return _j({"jsonrpc": "2.0", "method": "session/update", "params": {"n": n}})


def _card(rid):
    return _j({"jsonrpc": "2.0", "id": rid, "method": "session/request_permission",
               "params": {"options": []}})


def _resp(rid):
    return _j({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


class _Hub:
    """One hub connection to an in-process keeper, read synchronously."""

    def __init__(self, k):
        import socket
        a, b = socket.socketpair()
        b.settimeout(5)
        self.k, self.sock, self.r = k, b, b.makefile("rb")
        self.conn = k.attach(a)
        self.applied = 0
        self.got = []                       # (seq, inner message) in arrival order

    def frame(self):
        line = self.r.readline()
        return json.loads(line) if line else None

    def hello_and_backlog(self):
        """-> (hello, [(seq, msg)] up to caught-up)."""
        hello = self.frame()
        assert hello["keeper"] == "hello", hello
        out = []
        while True:
            f = self.frame()
            assert f is not None, "EOF before caught-up"
            if f["keeper"] == "caught-up":
                return hello, out
            out.append(self._apply(f))

    def one(self):
        return self._apply(self.frame())

    def _apply(self, f):
        assert f["keeper"] == "l", f
        self.applied = max(self.applied, f["s"])
        self.got.append((f["s"], f["m"]))
        return f["s"], f["m"]

    def send(self, obj):
        return self.k.accept_from_hub(self.conn, _j(obj))

    def ack(self, n=None):
        return self.send({"keeper": "ack", "s": self.applied if n is None else n})

    def eof(self, timeout=3):
        self.sock.settimeout(timeout)
        try:
            return self.r.read() == b""     # a shut-down peer: what is left, then EOF
        except OSError:
            return False

    def close(self):
        for x in (self.r, self.sock):
            try:
                x.close()
            except OSError:
                pass


class _InProc(unittest.TestCase):
    """An in-process keeper with a stub agent (setup only; no tests)."""

    def setUp(self):
        import keeper
        self.km = keeper
        work = Path(tmpdir(self, "keeper-v3-"))
        self.k = keeper.Keeper(["true"], str(work), str(work / "s.sock"), str(work))
        self.k.p = _StubP()
        self.hubs = []
        # The keeper logs to stderr; keep the test output readable.
        patcher = mock.patch.object(keeper, "_log", lambda msg: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._down)              # runs first: still quiet

    def _down(self):
        self.k.p.stdin.gate.set()
        self.k.stop.set()
        with self.k.lock:
            self.k.drop_conn_locked("test over")
        for h in self.hubs:
            h.close()

    def hub(self):
        h = _Hub(self.k)
        self.hubs.append(h)
        return h

    def stdin(self):
        """Lines accepted for the adapter's stdin (no writer thread here)."""
        return [json.loads(b) for b in self.k.stdin_q]


class KeeperContract(_InProc):

    # C1
    def test_c1_lines_are_numbered_in_order(self):
        for i in range(3):
            self.k.append(_note(i))
        h = self.hub()
        hello, lines = h.hello_and_backlog()
        self.assertEqual(hello["version"], 3)
        self.assertEqual([s for s, _ in lines], [1, 2, 3])
        self.assertEqual([m["params"]["n"] for _, m in lines], [0, 1, 2])
        self.assertEqual(hello["spooled"], 3)
        self.k.append(_note(3))
        self.assertEqual(h.one()[0], 4, "a live line after the backlog")

    # C2
    def test_c2_an_ack_trims_and_a_new_hub_gets_acked_plus_one_onward(self):
        for i in range(4):
            self.k.append(_note(i))
        h = self.hub()
        h.hello_and_backlog()
        h.ack(2)
        self.assertEqual((self.k.acked, self.k.base, len(self.k.entries)), (2, 3, 2))
        h2 = self.hub()
        hello, lines = h2.hello_and_backlog()
        self.assertEqual(hello["acked"], 2)
        self.assertEqual([s for s, _ in lines], [3, 4])
        self.assertTrue(h.eof(), "the replaced hub was not shut down")

    # C3
    def test_c3_held_cards_come_first_and_an_answered_one_never_again(self):
        self.k.append(_card(9))
        self.k.append(_note(0))
        self.k.append(_card(10))
        h = self.hub()
        h.hello_and_backlog()
        h.ack()                                   # all three acked; 9 and 10 unanswered
        self.assertEqual(sorted(self.k.held), [1, 3])
        self.assertTrue(h.send({"jsonrpc": "2.0", "id": 10, "result": {"outcome": "x"}}))
        self.assertEqual(sorted(self.k.held), [1], "an answered card is held no more")
        self.k.append(_note(1))                   # seq 4, unacked
        h2 = self.hub()
        hello, lines = h2.hello_and_backlog()
        self.assertEqual(hello["held"], 1)
        self.assertEqual([(s, m.get("id")) for s, m in lines], [(1, 9), (4, None)])

    # C4
    def test_c4_an_unacked_answered_card_is_skipped_on_replay(self):
        self.k.append(_card(9))
        self.k.append(_note(0))
        h = self.hub()
        h.hello_and_backlog()
        h.send({"jsonrpc": "2.0", "id": 9, "result": {"outcome": "x"}})   # answered, never acked
        h2 = self.hub()
        hello, lines = h2.hello_and_backlog()
        self.assertEqual([s for s, _ in lines], [2], "the answered card came back")
        self.assertEqual(hello["spooled"], 1)

    # C5
    def test_c5_a_second_answer_for_one_card_is_dropped(self):
        self.k.append(_card(9))
        h = self.hub()
        h.hello_and_backlog()
        ans = {"jsonrpc": "2.0", "id": 9, "result": {"outcome": "allow"}}
        self.assertTrue(h.send(ans))
        self.assertTrue(h.send(ans))
        h2 = self.hub()
        h2.hello_and_backlog()
        self.assertTrue(h2.send(ans))             # nor from the next hub
        self.assertEqual(len([m for m in self.stdin() if m.get("id") == 9]), 1)
        self.assertTrue(h2.send({"jsonrpc": "2.0", "id": 77, "result": {}}))
        self.assertEqual(len(self.stdin()), 1, "an answer to a card never asked reached the agent")

    # C6
    def test_c6_a_request_is_outstanding_until_its_response_is_acked(self):
        h = self.hub()
        h.hello_and_backlog()
        h.send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {}})
        self.assertEqual(self.k.outstanding, {"3": (3, "session/prompt")})
        self.k.append(_resp(3))
        s, _ = h.one()
        h2 = self.hub()                           # h crashed before acking
        hello, lines = h2.hello_and_backlog()
        self.assertEqual(hello["outstanding"], [{"id": 3, "method": "session/prompt"}])
        self.assertEqual(hello["next_id"], 4)
        self.assertEqual(lines[-1][1]["id"], 3, "the response is replayed")
        h2.ack()
        self.assertEqual(self.k.outstanding, {})
        h3 = self.hub()
        self.assertEqual(h3.hello_and_backlog()[0]["outstanding"], [])

    # C7
    def test_c7_a_replaced_hub_drives_nothing(self):
        h = self.hub()
        h.hello_and_backlog()
        h2 = self.hub()
        h2.hello_and_backlog()
        self.assertFalse(h.send({"jsonrpc": "2.0", "id": 5, "method": "x"}))
        self.assertFalse(h.ack(0))
        self.assertEqual(self.k.outstanding, {})
        self.assertEqual(self.stdin(), [])
        self.assertTrue(h.eof())

    # C8
    def test_c8_a_blocked_stdin_does_not_block_an_attach(self):
        self.k.p.stdin.gate.clear()               # the agent stops reading
        threading.Thread(target=self.k._write_stdin, daemon=True).start()
        line = {"jsonrpc": "2.0", "method": "session/cancel", "params": {"pad": "x" * 64}}
        with mock.patch.object(self.km, "STDIN_MAX_BYTES", 100):
            h = self.hub()
            h.hello_and_backlog()
            self.assertTrue(h.send(line))         # the writer takes it, then blocks
            self.assertTrue(wait_for(lambda: not self.k.stdin_q))
            self.assertTrue(h.send(line))         # queued
            res = []
            t = threading.Thread(target=lambda: res.append(h.send(line)), daemon=True)
            t.start()                             # waits for stdin space
            time.sleep(0.3)
            self.assertTrue(t.is_alive(), "the third line did not wait for space")
            t0 = time.monotonic()
            h2 = self.hub()
            h2.hello_and_backlog()
            self.assertLess(time.monotonic() - t0, 1.0, "a wedged agent blocked the attach")
            t.join(3)
            self.assertEqual(res, [False], "the replaced hub's waiting line was accepted")
            self.assertEqual(len(self.k.stdin_q), 1)

    # C9
    def test_c9_overflow_drops_notes_only(self):
        with mock.patch.object(self.km, "OUTBOX_MAX_BYTES", 400):
            self.k.append(_card(1))
            self.k.append(_resp(2))
            for i in range(20):
                self.k.append(_note(i))
            self.k.append(self.k.frame("exit", rc=0, stderr_tail=[], closed=False),
                          kind=self.km.EXIT)
        kept = [e.kind for e in self.k.entries if e is not None]
        self.assertEqual(kept.count("card"), 1)
        self.assertEqual(kept.count("resp"), 1)
        self.assertEqual(kept.count("exit"), 1)
        self.assertGreater(self.k.dropped, 0)
        self.assertLessEqual(self.k.bytes, 400)
        h = self.hub()
        hello, lines = h.hello_and_backlog()
        self.assertEqual(hello["dropped"], self.k.dropped)
        seqs = [s for s, _ in lines]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(lines[-1][1]["keeper"], "exit", "the exit is the last line")

    # C10
    def test_c10_a_line_that_is_not_a_json_object_is_dropped(self):
        for bad in (b"not json\n", b"[1, 2]\n", b'"str"\n'):
            self.assertIsNone(self.k.append(bad))
        self.assertEqual((self.k.dropped, self.k.next_seq), (3, 1))
        self.assertEqual(self.k.append(_note(0)), 1)

    # C11
    def test_c11_after_the_exit_the_keeper_lingers_until_a_hub_acked_it_and_left(self):
        self.k.append(_note(0))
        seq = self.k.append(self.k.frame("exit", rc=0, stderr_tail=[], closed=False),
                            kind=self.km.EXIT)
        self.assertEqual(self.k.exit_seq, seq)
        with self.k.lock:
            self.assertFalse(self.k.linger_done_locked(), "no hub has it yet")
        h = self.hub()
        h.hello_and_backlog()
        with self.k.lock:
            self.assertFalse(self.k.linger_done_locked(), "received is not acked")
        h.ack()
        with self.k.lock:
            self.assertFalse(self.k.linger_done_locked(), "the hub is still attached")
            self.k.drop_conn_locked("hub left")
            self.assertTrue(self.k.linger_done_locked())

    # C12
    def test_c12_the_orphan_watch_and_an_attach_never_both_win(self):
        with mock.patch.object(self.km, "ORPHAN_S", 1), \
                mock.patch.object(self.km.os, "killpg") as killpg:
            h = self.hub()
            h.hello_and_backlog()
            self.assertFalse(self.k.watch_once(time.monotonic() + 100), "a hub is attached")
            with self.k.lock:
                self.k.drop_conn_locked("hub left")
            self.assertTrue(self.k.watch_once(time.monotonic() + 100))
            self.assertTrue(self.k.p.terminated)
            self.assertTrue(killpg.called)
            import socket
            a, b = socket.socketpair()
            self.addCleanup(b.close)
            self.assertIsNone(self.k.attach(a), "an attach after the close was claimed")
            b.settimeout(2)
            self.assertEqual(b.recv(10), b"", "the refused hub was not hung up on")
            self.assertFalse(self.k.watch_once(time.monotonic() + 100), "claimed twice")


class KeeperProperty(unittest.TestCase):
    """Seeded random runs against the contract's invariants."""

    SEEDS = (20261031, 1, 2, 3, 4, 5, 6, 7)
    STEPS = 250

    def test_random_runs_hold_the_invariants(self):
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                self._run(seed)

    def _run(self, seed):
        import random
        import keeper
        rng = random.Random(seed)
        work = Path(tmpdir(self, "keeper-prop-"))
        k = keeper.Keeper(["true"], str(work), str(work / "s.sock"), str(work))
        k.p = _StubP()
        hubs = []
        with mock.patch.object(keeper, "_log", lambda msg: None):
            try:
                self._drive(rng, k, hubs, keeper)
            finally:
                k.stop.set()
                with k.lock:
                    k.drop_conn_locked("test over")
                for h in hubs:
                    h.close()

    def _drive(self, rng, k, hubs, keeper):
        kinds = {}                  # seq -> kind ("note", "card", "resp", "exit")
        card_of = {}                # seq -> card id
        answered = set()            # card ids answered (accepted by the keeper)
        answers_to_stdin = collections.Counter()
        cards_asked = []
        hub_reqs = {}               # hub request id -> has a response line
        resp_at = {}                # hub request id -> seq of its response
        next_card, next_req = 1, 1
        windows = []                # (acked, applied] of each hub that crashed
        h = None

        def attach():
            nonlocal h
            nh = _Hub(k)
            hubs.append(nh)
            hello, lines = nh.hello_and_backlog()
            h = nh
            return hello, lines

        def drain_stdin():
            while k.stdin_q:
                m = json.loads(k.stdin_q.popleft())
                k.stdin_bytes = 0
                if "id" in m and "method" not in m:
                    answers_to_stdin[str(m["id"])] += 1

        attach()
        for _ in range(self.STEPS):
            op = rng.choice(["note", "note", "card", "req", "resp", "answer", "answer",
                             "ack", "attach", "detach", "crash"])
            before = k.next_seq
            if op == "note":
                s = k.append(_note(rng.random()))
                kinds[s] = "note"
            elif op == "card":
                s = k.append(_card(next_card))
                kinds[s], card_of[s] = "card", next_card
                cards_asked.append(next_card)
                next_card += 1
            elif op == "req" and h is not None:
                self.assertTrue(h.send({"jsonrpc": "2.0", "id": next_req, "method": "m"}))
                hub_reqs[next_req] = False
                next_req += 1
            elif op == "resp":
                open_ = [r for r, done in hub_reqs.items() if not done]
                if open_:
                    r = rng.choice(open_)
                    s = k.append(_resp(r))
                    kinds[s], hub_reqs[r] = "resp", True
                    resp_at[r] = s
            elif op == "answer" and h is not None:
                seen = [m["id"] for _, m in h.got if "method" in m and "id" in m]
                if seen:
                    cid = rng.choice(seen)
                    for _ in range(rng.choice([1, 1, 2])):          # sometimes twice
                        self.assertTrue(h.send({"jsonrpc": "2.0", "id": cid,
                                                "result": {"outcome": "x"}}))
                    answered.add(cid)
            elif op == "ack" and h is not None:
                h.ack()
            elif op == "attach":
                if h is not None:
                    windows.append((k.acked, h.applied))            # replaced = crashed
                attach()
            elif op == "detach" and h is not None:
                h.ack()
                with k.lock:
                    if k.conn is h.conn:
                        k.drop_conn_locked("detached")
                h = None
            elif op == "crash" and h is not None:
                windows.append((k.acked, h.applied))
                h.close()
                with k.lock:
                    if k.conn is h.conn:
                        k.drop_conn_locked("crashed")
                h = None
            # A hub reads what the keeper sends it live: one frame per line
            # appended this step (the backlog was read at attach).
            if h is not None and k.next_seq > before:
                for _ in range(k.next_seq - before):
                    h.one()
            drain_stdin()

        # The exit, and a final hub.
        s = k.append(k.frame("exit", rc=0, stderr_tail=[], closed=False), kind=keeper.EXIT)
        kinds[s] = "exit"
        if h is not None:
            windows.append((k.acked, h.applied))
        hello, final = attach()
        drain_stdin()

        # I1: no answer reaches the agent twice for one card.
        self.assertTrue(all(n == 1 for n in answers_to_stdin.values()), answers_to_stdin)
        self.assertEqual(set(answers_to_stdin), {str(c) for c in answered})
        # I2: the final hub gets exactly the unanswered cards, once each.
        final_cards = [m["id"] for _, m in final if "method" in m and "id" in m]
        self.assertEqual(sorted(final_cards), sorted(set(cards_asked) - answered))
        # I3: every note, response and exit reached some hub at least once.
        applied = collections.Counter(s for hub in hubs for s, _ in hub.got)
        for s, kind in kinds.items():
            if kind != "card":
                self.assertGreaterEqual(applied[s], 1, f"seq {s} ({kind}) never applied")
        self.assertEqual(final[-1][1].get("keeper"), "exit", "the exit is the last line")
        # I4: a non-card line comes twice only from a crashed hub's (acked, applied].
        for s, n in applied.items():
            if kinds[s] != "card" and n > 1:
                self.assertTrue(any(a < s <= b for a, b in windows),
                                f"seq {s} duplicated outside every crash window")
        # I5: per hub, seqs strictly increase (held cards, all <= acked, first).
        for hub in hubs:
            seqs = [s for s, _ in hub.got]
            self.assertEqual(seqs, sorted(set(seqs)), "a hub saw seqs out of order")
        # I6: outstanding is every hub request whose response is not acked.
        self.assertEqual({o["id"] for o in hello["outstanding"]},
                         {r for r in hub_reqs if resp_at.get(r, k.acked + 1) > k.acked})


class KeeperProcess(_Spawn, unittest.TestCase):
    """The contract through a real keeper process and real sockets."""

    def _connect(self, sock):
        import socket
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(10)
        c.connect(sock)
        self.addCleanup(c.close)
        return c, c.makefile("rb")

    def test_a_replaced_hub_gets_eof(self):
        work = tmpdir(self, "keeper-proc-")
        kp, sock = self._start(work)
        c1, r1 = self._connect(sock)
        self.assertEqual(json.loads(r1.readline())["keeper"], "hello")
        c2, r2 = self._connect(sock)
        self.assertEqual(json.loads(r2.readline())["keeper"], "hello")
        rest = r1.read()
        self.assertTrue(all(json.loads(x)["keeper"] == "caught-up"
                            for x in rest.splitlines() if x.strip()), rest)
        c2.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        while True:
            f = json.loads(r2.readline())
            if f["keeper"] == "l":
                break
        self.assertEqual(f["m"]["id"], 1, "the new hub drives the agent")

    def test_a_stalled_hub_does_not_block_a_new_attach(self):
        work = Path(tmpdir(self, "keeper-proc-"))
        n = 3000
        upd = json.dumps({"sessionUpdate": "agent_message_chunk",
                          "content": {"type": "text", "text": "z" * 1000}})
        (work / "big.jsonl").write_text((upd + "\n") * n, encoding="utf-8")
        kp, sock = self._start(work, {"FAKE_ACP_REPLAY": str(work)})
        c1, r1 = self._connect(sock)
        c1.sendall(b'{"jsonrpc":"2.0","id":1,"method":"session/new","params":{"cwd":"/","mcpServers":[]}}\n')
        sid = None
        while sid is None:
            f = json.loads(r1.readline())
            if f["keeper"] == "l" and f["m"].get("id") == 1:
                sid = f["m"]["result"]["sessionId"]
        c1.sendall(_j({"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                       "params": {"sessionId": sid,
                                  "prompt": [{"type": "text", "text": "replay big"}]}}))
        time.sleep(1.5)                          # c1 reads no more: its writer wedges
        t0 = time.monotonic()
        c2, r2 = self._connect(sock)
        self.assertEqual(json.loads(r2.readline())["keeper"], "hello")
        self.assertLess(time.monotonic() - t0, 2.0, "a stalled hub blocked the attach")
        chunks, done = 0, False
        while not done:
            f = json.loads(r2.readline())
            if f["keeper"] != "l":
                continue
            m = f["m"]
            if m.get("id") == 2:
                done = True
            elif "zzzz" in json.dumps(m):
                chunks += 1
        self.assertEqual(chunks, n, "the new hub lost lines")


class V2KeeperCompat(_Spawn, unittest.TestCase):
    """The new client drives a keeper still speaking protocol 2 (every live
    pane at the first update after this ships)."""

    def _start_v2(self, work):
        sock = str(Path(work) / "s.sock")
        full = {**os.environ, "FAKE_ACP_DIR": str(work)}
        kp = subprocess.Popen([sys.executable, "-I", str(ROOT / "testkit" / "keeper_v2.py"),
                               "--sock", sock, "--dir", str(work), "--cwd", str(work), "--",
                               sys.executable, str(FAKE)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=full, start_new_session=True)
        self.addCleanup(self._kill, kp)
        self.assertTrue(wait_for(lambda: os.path.exists(sock)), "no socket")
        return kp, sock

    def test_the_new_client_drives_a_v2_keeper_through_a_reattach(self):
        from corral_core import acp
        work = tmpdir(self, "keeper-v2-")
        kp, sock = self._start_v2(work)
        events = []
        on_event = lambda kind, payload: events.append((kind, payload))  # noqa: E731
        c1 = acp.AcpClient.reattach(sock, kp.pid, work, on_event=on_event,
                                    on_permission=lambda req: None)
        self.assertEqual(c1._kv, 2)
        c1.initialize()
        sid = c1.new_session(work)
        r = c1.prompt(sid, "remember pear")
        self.assertEqual(r["stopReason"], "end_turn")
        # Mid-turn: the hub lets go, the next one adopts the turn.
        box = {}
        threading.Thread(target=lambda: box.setdefault(
            "r", _swallow(lambda: c1.prompt(sid, "sleep 1"))), daemon=True).start()
        self.assertTrue(wait_for(lambda: any(o for o in [c1._pending] if len(o) >= 1)))
        time.sleep(0.3)
        c1.detach()
        c2 = acp.AcpClient.reattach(sock, kp.pid, work, on_event=on_event,
                                    on_permission=lambda req: None)
        self.assertEqual(c2._kv, 2)
        prompt = [o for o in c2.adopted if o["method"] == "session/prompt"]
        self.assertEqual(len(prompt), 1, c2.adopted)
        self.assertEqual(c2.await_adopted(prompt[0]["id"])["stopReason"], "end_turn")
        r = c2.prompt(sid, "what word did I ask you to remember?")
        self.assertEqual(r["stopReason"], "end_turn")
        text = "".join(str(p) for k_, p in events if k_ != "keeper_replayed")
        self.assertIn("pear", text)
        # A clean exit.
        c2._write({"keeper": "close"})
        self.assertTrue(wait_for(lambda: kp.poll() is not None), "the v2 keeper kept running")
        self.assertTrue(wait_for(lambda: not c2.alive))
        self.assertTrue(c2._closed, "a requested close read as a crash")


def _swallow(fn):
    try:
        return fn()
    except Exception as e:                     # noqa: BLE001
        return e


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


class KeeperAfterReview3(_InProc):
    """The 2026-10-31 panel on protocol 3 (Codex + Grok). Each test fails
    on 65f9ca0."""

    # R3-1: a replaced hub's close
    def test_r3_1_a_close_is_claimed_with_the_owner_check(self):
        h = self.hub()
        h.hello_and_backlog()
        late, ends = [], []

        def end_adapter():
            ends.append(1)
            # The window the review traced: the lock is released, a new hub
            # attaches, then the old hub's close runs.
            import socket
            a, b = socket.socketpair()
            self.addCleanup(b.close)
            late.append(self.k.attach(a))
        # Whichever runs after the owner check's lock is released (65f9ca0
        # called close_adapter, which claimed the close only then).
        self.k.end_adapter = self.k.close_adapter = end_adapter
        self.assertTrue(h.send({"keeper": "close"}))
        self.assertTrue(self.k.closing)
        self.assertEqual(late, [None], "a hub attached to an agent already being closed")
        h.send({"keeper": "close"})
        self.assertEqual(len(ends), 1, "closed twice")

    # R3-2: a held card answered after the attach snapshot
    def test_r3_2_a_held_card_answered_before_its_resend_is_not_resent(self):
        self.k.append(_card(7))
        h = self.hub()
        h.hello_and_backlog()
        h.ack()
        gate = threading.Event()
        real = self.km.Conn._run

        def gated(conn):
            gate.wait(5)
            return real(conn)
        with mock.patch.object(self.km.Conn, "_run", gated):
            h2 = self.hub()
            self.assertTrue(h2.send({"jsonrpc": "2.0", "id": 7, "result": {"outcome": "x"}}))
            gate.set()
            _, lines = h2.hello_and_backlog()
        self.assertEqual(lines, [], "an answered card was re-sent")

    # R3-4: a hub line nobody expected
    def test_r3_4_a_malformed_request_id_is_dropped_not_fatal(self):
        h = self.hub()
        h.hello_and_backlog()
        for bad in ([], {}, 1.5, True):
            self.assertTrue(h.send({"jsonrpc": "2.0", "id": bad, "method": "session/prompt"}))
        self.assertEqual(self.k.outstanding, {})
        self.assertEqual(self.stdin(), [])
        self.assertEqual(self.k.dropped, 4)
        self.assertTrue(h.send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt"}))
        self.assertEqual(self.k.outstanding, {"3": (3, "session/prompt")})

    def test_r3_4_a_reader_that_fails_still_lets_go_of_the_agent(self):
        h = self.hub()
        h.hello_and_backlog()
        # Data lines take offer_from_hub since the split reader; both paths.
        with mock.patch.object(self.k, "accept_from_hub", side_effect=TypeError("boom")), \
                mock.patch.object(self.k, "offer_from_hub", side_effect=TypeError("boom")):
            h.sock.sendall(b'{"jsonrpc":"2.0","method":"x"}\n')
            self.assertTrue(wait_for(lambda: self.k.conn is None, timeout=3),
                            "a dead reader still owns the agent")


class ClientAfterReview3(unittest.TestCase):

    def _client(self):
        from corral_core import acp
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._init_state()
        c._kv = 3
        c.alive = True
        written = []

        class In:
            def write(self, s):
                written.append(json.loads(s))

            def flush(self):
                pass
        c.p = acp._KeeperProc.__new__(acp._KeeperProc)
        c.p.stdin = In()
        c.p.close_socket = lambda: None
        return c, written

    # R3-3: detach while a line is being applied
    def test_r3_3_detach_acks_the_line_being_applied(self):
        c, written = self._client()
        c._applied_line(1, False)
        inside = threading.Event()

        def reader():
            with c._apply_lock:              # applying seq 2, as _read_stdout does
                inside.set()
                time.sleep(0.3)
                c._applied_line(2, False)
        t = threading.Thread(target=reader)
        t.start()
        inside.wait(2)
        c.detach()
        t.join(2)
        acks = [m["s"] for m in written if m.get("keeper") == "ack"]
        self.assertEqual(acks[-1:], [2], f"detach acked {acks}, not the line in hand")
        self.assertTrue(c._stop_reading)

    # R3-2, client side: a card this client answered is never raised again
    def test_r3_2_a_card_answered_here_is_not_raised_again(self):
        c, written = self._client()
        raised = []
        c.on_event = lambda *a: None
        c.on_permission = lambda req: raised.append(req["requestId"])
        msg = {"jsonrpc": "2.0", "id": 9, "method": "session/request_permission",
               "params": {"options": [{"optionId": "allow"}]}}
        c._on_request(msg)
        self.assertTrue(c.answer_permission("9", "allow"))
        self.assertTrue(wait_for(lambda: any(m.get("id") == 9 for m in written), timeout=3))
        c._on_request(msg)                     # the keeper's late re-send
        self.assertEqual(raised, ["9"], "an answered card was raised again")
        self.assertEqual(len([m for m in written if m.get("id") == 9]), 1)


class KeeperAfterReview3Grok(_InProc):
    """Grok's arm of the 2026-10-31 panel."""

    def test_g3_an_overflowing_ack_and_a_too_deep_line_are_survived(self):
        h = self.hub()
        h.hello_and_backlog()
        self.k.append(_note(0))
        h.one()
        self.assertTrue(self.k.accept_from_hub(h.conn, b'{"keeper":"ack","s":1e309}\n'))
        self.assertEqual(self.k.acked, 0)
        self.assertIs(self.k.conn, h.conn)
        deep = b"[" * 200000 + b"]" * 200000 + b"\n"
        self.assertIsNone(self.k.append(deep), "a too-deep line was numbered")
        self.assertTrue(h.send({"jsonrpc": "2.0", "method": "x"}))

    def test_g4_a_reply_id_of_another_json_type_still_clears_the_request(self):
        h = self.hub()
        h.hello_and_backlog()
        h.send({"jsonrpc": "2.0", "id": 1, "method": "session/prompt"})
        self.k.append(_j({"jsonrpc": "2.0", "id": "1", "result": {}}))
        h.one()
        h.ack()
        self.assertEqual(self.k.outstanding, {})
        h2 = self.hub()
        self.assertEqual(h2.hello_and_backlog()[0]["outstanding"], [])

    def test_g7_a_capped_line_fits_the_clients_cap_once_framed(self):
        from corral_core import acp
        framed = self.km.envelope(10 ** 12, b"x" * self.km.MAX_LINE)
        self.assertLessEqual(len(framed), acp.MAX_STDOUT_LINE)


class ClientAfterReview3Grok(ClientAfterReview3):

    def test_r3_2_a_card_answered_here_is_not_raised_again(self):
        pass                                  # inherited; run once, above

    def test_r3_3_detach_acks_the_line_being_applied(self):
        pass

    def test_g2_no_ack_passes_a_line_the_pane_has_not_written_out(self):
        c, written = self._client()
        acks = lambda: [m["s"] for m in written if m.get("keeper") == "ack"]  # noqa: E731
        c._cur_seq = 5
        c.hold("text")                         # seq 5's text is buffered
        c._applied_line(7, False)
        c._send_ack()
        self.assertEqual(acks(), [4])
        c.release("text")
        c._send_ack()
        self.assertEqual(acks(), [4, 7])

    def test_g2_detach_writes_out_the_buffer_before_its_final_ack(self):
        c, written = self._client()
        c._cur_seq = 3
        c.hold("thought")
        c._applied_line(4, False)
        order = []
        c.detach(flush=lambda: (order.append("flush"), c.release("thought")))
        self.assertEqual(order, ["flush"])
        self.assertEqual([m["s"] for m in written if m.get("keeper") == "ack"], [4])


class TheDeadPaneHasItsReason(KeeperCase):

    def test_g5_dead_is_never_seen_before_its_reason(self):
        p = self._pane()
        seen = []
        real = p._dead

        def dead(reason):
            seen.append(p.state)
            return real(reason)
        p._dead = dead
        p.send("die")
        self.assertTrue(wait_for(lambda: p.state == "dead"))
        self.assertTrue(seen)
        self.assertNotEqual(seen[0], "dead", f"dead was set before its reason: {seen}")
        self.assertTrue(p.error)


if __name__ == "__main__":
    unittest.main()


class StdinBackpressure(_InProc):
    """Deferred from the 2026-10-31 panel (Grok): with the agent's stdin
    blocked and the queue full, the hub's reader waited in accept_from_hub,
    so a later ack or close on that connection waited too. Each test fails
    on 35a67fb. Lines go through the real reader thread, over the socket."""

    def _wedge(self):
        """The agent stops reading; fill the queue so the next line waits."""
        self.k.p.stdin.gate.clear()
        threading.Thread(target=self.k._write_stdin, daemon=True).start()
        patcher = mock.patch.object(self.km, "STDIN_MAX_BYTES", 100)
        patcher.start()
        self.addCleanup(patcher.stop)
        for i in range(3):
            self.k.append(_note(i))
        h = self.hub()
        h.hello_and_backlog()                     # applied = 3, none acked
        line = _j({"jsonrpc": "2.0", "method": "session/cancel",
                   "params": {"pad": "x" * 64}})
        h.sock.sendall(line)                      # the writer takes it, then blocks
        self.assertTrue(wait_for(lambda: self.k.p.stdin.gate is not None
                                 and not self.k.stdin_q and self.k.stdin_bytes == 0))
        h.sock.sendall(line)                      # queued
        self.assertTrue(wait_for(lambda: len(self.k.stdin_q) == 1))
        h.sock.sendall(line)                      # waits for stdin space
        time.sleep(0.3)
        self.assertEqual(len(self.k.stdin_q), 1, "the third line did not wait")
        return h

    def test_bp_1_an_ack_after_a_waiting_line_lands_at_once(self):
        h = self._wedge()
        h.sock.sendall(_j({"keeper": "ack", "s": 3}))
        self.assertTrue(wait_for(lambda: self.k.acked == 3, timeout=0.5),
                        "an ack waited behind a line the agent is not reading")
        self.assertEqual(self.k.base, 4, "the outbox did not trim")

    def test_bp_2_a_close_after_a_waiting_line_ends_the_agent(self):
        h = self._wedge()
        with mock.patch.object(self.km.os, "killpg"):
            h.sock.sendall(_j({"keeper": "close"}))
            self.assertTrue(wait_for(lambda: self.k.p.terminated,
                                     timeout=self.km.TERM_WAIT_S),
                            "a close waited behind a line the agent is not reading")
        self.assertTrue(self.k.closing)

    def test_bp_3_order_holds_for_lines_that_reach_the_agent(self):
        h = self._wedge()
        nxt = _j({"jsonrpc": "2.0", "method": "session/cancel", "params": {"n": 4}})
        h.sock.sendall(nxt)
        h.sock.sendall(_j({"keeper": "ack", "s": 3}))
        self.assertTrue(wait_for(lambda: self.k.acked == 3, timeout=0.5))
        self.k.p.stdin.gate.set()                 # the agent reads again
        self.assertTrue(wait_for(lambda: len(self.k.p.stdin.lines) == 4, timeout=3))
        got = [json.loads(b)["params"] for b in self.k.p.stdin.lines]
        self.assertEqual([p.get("n") for p in got], [None, None, None, 4],
                         "lines reached the agent out of order")

    def test_bp_4_a_replaced_hubs_waiting_lines_never_reach_the_agent(self):
        h = self._wedge()
        h.sock.sendall(_j({"jsonrpc": "2.0", "method": "x", "params": {"late": 1}}))
        time.sleep(0.1)
        h2 = self.hub()
        h2.hello_and_backlog()
        self.k.p.stdin.gate.set()
        time.sleep(0.5)
        late = [b for b in self.k.p.stdin.lines if b"late" in b]
        self.assertEqual(late, [], "a replaced hub's waiting line reached the agent")
        self.assertLessEqual(len(self.k.p.stdin.lines), 2,
                             "a replaced hub's waiting line reached the agent")

    def test_bp_5_lines_read_before_a_hang_up_still_reach_the_agent(self):
        self.k.p.stdin.gate.clear()               # slow agent: the drain lags the reader
        threading.Thread(target=self.k._write_stdin, daemon=True).start()
        h = self.hub()
        h.hello_and_backlog()
        for n in range(5):
            h.sock.sendall(_j({"jsonrpc": "2.0", "method": "session/cancel",
                               "params": {"n": n}}))
        h.sock.shutdown(__import__("socket").SHUT_WR)   # the hub hangs up
        self.assertTrue(wait_for(lambda: self.k.conn is None, timeout=3))
        self.k.p.stdin.gate.set()
        self.assertTrue(wait_for(lambda: len(self.k.p.stdin.lines) == 5, timeout=3),
                        "a line read before the hang-up was lost")


class StdinBackpressureAfterReview(StdinBackpressure):
    """The panel on c4fe8f5 (Codex). Each test fails on c4fe8f5."""

    def _hang_up_with_a_waiting_prompt(self, rid=7):
        h = self._wedge()
        h.sock.sendall(_j({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                           "params": {}}))
        h.sock.shutdown(__import__("socket").SHUT_WR)    # a clean hang-up
        conn = h.conn
        self.assertTrue(wait_for(lambda: conn.read_done is not None, timeout=3))
        return h

    def test_bpr_1_a_write_failure_after_a_hang_up_keeps_the_pending_lines(self):
        h = self._hang_up_with_a_waiting_prompt()
        h.close()                                 # the hub is gone for good
        self.k.append(_note(99))                  # the writer fails on this
        time.sleep(0.3)
        self.k.p.stdin.gate.set()
        self.assertTrue(wait_for(lambda: any(b"session/prompt" in b
                                             for b in self.k.p.stdin.lines), timeout=3),
                        "a prompt sent before a clean hang-up was lost")
        self.assertEqual(list(self.k.outstanding), ["7"])

    def test_bpr_2_a_hung_up_hub_does_not_block_the_orphan_watch(self):
        self._hang_up_with_a_waiting_prompt()
        self.assertTrue(wait_for(lambda: self.k.conn is None, timeout=3),
                        "a hub that hung up still owns the agent")
        with mock.patch.object(self.km.os, "killpg"):
            self.assertTrue(self.k.watch_once(
                now=time.monotonic() + self.km.ORPHAN_S + 1))
        self.assertTrue(self.k.p.terminated)

    def test_bpr_3_a_new_hub_drops_a_gone_hubs_leftover_lines(self):
        self._hang_up_with_a_waiting_prompt()
        h2 = self.hub()
        h2.hello_and_backlog()
        self.k.p.stdin.gate.set()
        time.sleep(0.5)
        self.assertFalse(any(b"session/prompt" in b for b in self.k.p.stdin.lines),
                         "a gone hub's line reached the agent after a new hub attached")
        self.assertEqual(self.k.outstanding, {})
        self.assertTrue(h2.send({"jsonrpc": "2.0", "id": 8, "method": "session/prompt"}))
        self.assertEqual(list(self.k.outstanding), ["8"], "the new hub drives")

    # The panel on c4fe8f5 (Grok)
    def test_bpr_4_a_line_read_while_stdin_has_room_is_accepted_as_read(self):
        """A drain that never runs: on c4fe8f5 the answer sat unapplied in
        the inbound queue, and a replacement dropped it and re-raised the
        card."""
        self.k.append(_card(9))
        with mock.patch.object(self.km.Keeper, "_drain_hub", lambda k, c: None):
            h = self.hub()
            h.hello_and_backlog()
            h.ack()
            h.sock.sendall(_j({"jsonrpc": "2.0", "id": 9, "result": {"outcome": "allow"}}))
            self.assertTrue(wait_for(lambda: "9" not in self.k.cards, timeout=1),
                            "a line read with stdin room was not accepted")
            h2 = self.hub()
            hello, lines = h2.hello_and_backlog()
        self.assertEqual(hello["held"], 0, "an answered card was raised again")
        self.assertEqual([m.get("id") for m in self.stdin()], [9])

    def test_bpr_5_a_close_lets_a_waiting_drain_go_at_once(self):
        h = self._wedge()
        conn = h.conn
        h.sock.sendall(_j({"jsonrpc": "2.0", "id": 7, "result": {"outcome": "x"}}))
        self.assertTrue(wait_for(lambda: len(conn.inq) == 2, timeout=1))
        with mock.patch.object(self.km.os, "killpg"):
            h.sock.sendall(_j({"keeper": "close"}))
            self.assertTrue(wait_for(lambda: self.k.closing, timeout=0.5))
            self.assertTrue(wait_for(lambda: not conn.inq, timeout=0.5),
                            "the drain did not see the close")
        h.sock.shutdown(__import__("socket").SHUT_RDWR)
        self.assertTrue(wait_for(lambda: self.k.conn is None, timeout=1),
                        "a closed, hung-up hub still owns the agent")
