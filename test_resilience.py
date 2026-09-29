#!/usr/bin/python3
"""Resilience tests — the kill paths from docs/RESILIENCE-REVIEW-2026-09-28.md.

Every test here drives a REAL agent process (testkit/fake_acp_agent.py) through
the real Pane/Manager code: spawned, killed with SIGKILL, resumed, interrupted.
A stub client cannot prove a kill path, because the thing under test is what
happens when a process the hub does not control goes away.

Collected by test_corral_light.py (`from test_resilience import *`), so the
one command `python3 test_corral_light.py` runs these too.
"""
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("CORRAL_LIGHT_STATE",
                      tempfile.mkdtemp(prefix="corral-light-test-"))

ROOT = Path(__file__).resolve().parent
FAKE = ROOT / "testkit" / "fake_acp_agent.py"
WAIT_S = 15          # generous for a loaded CI box; each wait ends on success


def wait_for(pred, timeout=WAIT_S, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class FakeLaneCase(unittest.TestCase):
    """A Manager (not the hub's global one) with a `fake` lane registered."""

    def setUp(self):
        import sessions
        self.sessions = sessions
        self.agent_dir = tempfile.mkdtemp(prefix="fake-acp-")
        sessions.AGENTS["fake"] = {
            "label": "Fake", "argv": [sys.executable, str(FAKE)],
            "requires": (str(FAKE),), "posture_via_config_dir": False,
            "tools": True, "env": {"FAKE_ACP_DIR": self.agent_dir}}
        self.addCleanup(sessions.AGENTS.pop, "fake", None)
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog = {}
        m.mcp = None
        self.mgr = m
        self.addCleanup(self._close_all)

    def _close_all(self):
        for pid in list(self.mgr.panes):
            try:
                self.mgr.close(pid)
            except Exception:                     # noqa: BLE001
                pass

    def kinds(self, pane):
        return [e["kind"] for e in pane.events]

    def texts(self, pane):
        return "".join((e.get("data") or {}).get("text", "")
                       for e in pane.events if e["kind"] == "text")

    def turn_ends(self, pane):
        return sum(1 for e in pane.events if e["kind"] == "turn_end")


class ResumeFromDead(FakeLaneCase):
    """P0-a' (Astra/Grok 2026-09-28): a pane whose agent died comes back."""

    def _kill_agent(self, pane):
        os.kill(pane.client.p.pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: pane.state == "dead"),
                        f"pane never noticed its agent died: {pane.state}")

    def test_kill9_then_type_resumes_and_the_stale_queue_is_not_sent(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("remember apple")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        self._kill_agent(p)
        # What `agent_exit` leaves behind when a process dies with type-ahead
        # waiting: the drain loop sees `dead` and returns, queue untouched.
        p._queue = ["stale type-ahead that must never run"]
        p.send("what word did I ask you to remember?")
        self.assertIn("resumed", self.kinds(p))
        resumed = next(e for e in p.events if e["kind"] == "resumed")
        self.assertEqual(resumed["data"]["from"], "dead")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 2))
        self.assertIn("apple", self.texts(p))
        self.assertNotIn("stale type-ahead", self.texts(p),
                         "the dead attachment's queue was drained into the new process")
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("stale type-ahead" in n and "will not be sent" in n
                            for n in notes), notes)
        self.assertNotIn("REPLAYED HISTORY", self.texts(p),
                         "session/load replay leaked into the transcript")

    def test_explicit_resume_from_dead_and_the_old_exit_is_fenced(self):
        p = self.mgr.create("fake", self.agent_dir)
        old_bind = p._bind(p._generation)
        self._kill_agent(p)
        self.mgr.resume(p.id)
        self.assertEqual(p.state, "ready", p.error)
        self.assertEqual(self.kinds(p).count("resumed"), 1)
        # A late agent_exit from the dead attachment's reader must not flip
        # the resumed pane back to dead.
        old_bind["on_event"]("agent_exit", {"reason": "late", "closed": False})
        self.assertEqual(p.state, "ready")
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))

    def test_resume_refuses_a_live_pane(self):
        p = self.mgr.create("fake", self.agent_dir)
        with self.assertRaises(ValueError):
            p.resume()

    def test_a_dead_pane_with_no_session_says_so(self):
        p = self.mgr.create("fake", self.agent_dir)
        self._kill_agent(p)
        p.acp_session = None
        with self.assertRaises(ValueError):
            p.send("hi")
        self.assertFalse(p.snapshot()["resumable"])

    def test_the_ui_offers_resume_on_a_dead_row(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("'↻'", js)
        self.assertIn("p.state === 'dead') return p.resumable ? 'live' : 'none'", js)


class OrphansFromAPreviousHubAreReaped(FakeLaneCase):
    """P0-pid (Grok 2026-09-28): the pid is on disk, and restore() uses it."""

    def setUp(self):
        super().setUp()
        import subprocess
        self.subprocess = subprocess
        self.state = Path(tempfile.mkdtemp(prefix="corral-light-restore-"))
        self._real_state = self.sessions.STATE
        self.sessions.STATE = self.state
        self.addCleanup(setattr, self.sessions, "STATE", self._real_state)
        self.procs = []
        self.addCleanup(self._kill_procs)

    def _kill_procs(self):
        for pr in self.procs:
            try:
                os.killpg(pr.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                pr.wait(5)
            except Exception:                     # noqa: BLE001
                pass
            for s in (pr.stdin, pr.stdout, pr.stderr):
                if s:
                    s.close()

    def _spawn(self, argv):
        pr = self.subprocess.Popen(argv, stdin=self.subprocess.PIPE,
                                   stdout=self.subprocess.DEVNULL,
                                   stderr=self.subprocess.DEVNULL,
                                   start_new_session=True,
                                   env={**os.environ, "FAKE_ACP_DIR": self.agent_dir})
        self.procs.append(pr)
        # A real orphan is reparented to init, which reaps it the moment it
        # dies. This one is OUR child, so without a waiter it lingers as a
        # zombie that still "exists" to killpg(0). Stand in for init.
        threading.Thread(target=pr.wait, daemon=True).start()
        return pr

    def _meta(self, pid, pr, start, agent="fake"):
        d = self.state / "panes" / pid
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "id": pid, "agent": agent, "cwd": self.agent_dir,
            "created": "2026-09-28T00:00:00Z", "acp_session": "s1",
            "role": "reviewer",
            "pid": pr.pid, "pgid": pr.pid, "pid_start": start}))
        return d

    def test_spawn_writes_pid_pgid_and_start_and_pause_clears_them(self):
        self.sessions.STATE = self._real_state      # create() uses the core dir
        p = self.mgr.create("fake", self.agent_dir)
        m = json.loads((p.dir / "meta.json").read_text())
        self.assertEqual(m["pid"], p.client.p.pid)
        self.assertEqual(m["pgid"], p.client.pgid)
        self.assertTrue(m["pid_start"])
        p.pause()
        m = json.loads((p.dir / "meta.json").read_text())
        self.assertIsNone(m["pid"])
        self.assertIsNone(m["pgid"])

    def test_a_surviving_adapter_is_stopped_before_restore_returns(self):
        import acp
        pr = self._spawn([sys.executable, str(FAKE)])
        self.assertTrue(wait_for(lambda: acp.process_start_token(pr.pid)))
        d = self._meta("orph1", pr, acp.process_start_token(pr.pid))
        self.mgr.restore()
        self.assertIsNotNone(pr.poll(), "the orphaned adapter is still running")
        self.assertIn(self.mgr.orphans.get("orph1"), ("reaped", "killed"))
        p = self.mgr.panes["orph1"]
        self.assertTrue(any(e["kind"] == "note" and "still running" in
                            e["data"]["text"] for e in p.events))
        m = json.loads((d / "meta.json").read_text())
        self.assertIsNone(m["pid"])
        self.assertEqual(m["role"], "reviewer", "clearing the pid blanked other keys")

    def test_a_reused_pid_is_never_signalled(self):
        pr = self._spawn(["sleep", "30"])
        self._meta("stranger", pr, "proc:0-not-this-process")
        self.mgr.restore()
        self.assertIsNone(pr.poll(), "a process that is not our adapter was signalled")
        self.assertTrue(self.mgr.orphans["stranger"].startswith("left alone"))

    def test_with_no_start_time_an_argv_mismatch_is_never_signalled(self):
        pr = self._spawn(["sleep", "30"])
        self._meta("nostart", pr, None)
        self.mgr.restore()
        self.assertIsNone(pr.poll())
        self.assertIn("not this lane", self.mgr.orphans["nostart"])

    def test_with_no_start_time_a_matching_argv_is_reaped(self):
        pr = self._spawn([sys.executable, str(FAKE)])
        self.assertTrue(wait_for(lambda: (Path(self.agent_dir) / f"pid-{pr.pid}").exists()))
        self._meta("oldmeta", pr, None)
        self.mgr.restore()
        self.assertIsNotNone(pr.poll())

    def test_unreadable_metas_are_counted_not_skipped(self):
        d = self.state / "panes" / "broken"
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{not json")
        (self.state / "panes" / "nometa").mkdir()
        self.mgr.restore()
        self.assertEqual(self.mgr.not_restored, 1)


HUB_SCRIPT = r"""
import os, sys, time, json
sys.path.insert(0, os.environ["LIGHT_ROOT"])
import sessions
sessions.Manager.seed_catalogs = lambda self: None     # no live vendor probes
sessions.AGENTS["fake"] = {"label": "Fake", "argv": [sys.executable, os.environ["FAKE"]],
                           "posture_via_config_dir": False, "tools": True,
                           "env": {"FAKE_ACP_DIR": os.environ["FAKE_ACP_DIR"]}}
import hub
p = hub.MGR.create("fake", os.environ["FAKE_ACP_DIR"])
p.send("sleep 60")
for _ in range(200):
    if getattr(p, "_in_flight", None):
        break
    time.sleep(0.05)
p.send("queued behind it")
print("PANE " + p.id, flush=True)
hub.serve("127.0.0.1", 0)
"""


class ShutdownWritesNotesNotPauses(FakeLaneCase):
    """P0-b' (Astra/Grok 2026-09-28): SIGTERM names the interrupted turn."""

    def _busy_pane(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: p._in_flight == "sleep 30"))
        p.send("second, queued")
        return p

    def test_the_note_names_the_popped_prompt_and_the_queue(self):
        p = self._busy_pane()
        n = self.mgr.shutdown_notes("SIGTERM")
        self.assertEqual(n, 1)
        note = [e for e in p.events if e["kind"] == "note"][-1]["data"]
        self.assertEqual(note["interrupted"], "sleep 30")
        self.assertEqual(note["queued"], ["second, queued"])
        self.assertIn("SIGTERM", note["text"])
        self.assertNotIn("paused", self.kinds(p), "shutdown must not pause()")
        self.assertEqual(p._queue, ["second, queued"], "the queue was touched")

    def test_an_idle_pane_gets_no_note(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(self.mgr.shutdown_notes("SIGTERM"), 0)
        self.assertNotIn("note", self.kinds(p))

    def test_a_real_hub_process_writes_the_note_on_sigterm_and_exits(self):
        import subprocess
        state = tempfile.mkdtemp(prefix="corral-light-sigterm-")
        env = {**os.environ, "CORRAL_LIGHT_STATE": state, "LIGHT_ROOT": str(ROOT),
               "FAKE": str(FAKE), "FAKE_ACP_DIR": self.agent_dir}
        pr = subprocess.Popen([sys.executable, "-c", HUB_SCRIPT], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
        pane_id, lines = None, []
        try:
            for line in pr.stdout:
                lines.append(line)
                if line.startswith("PANE "):
                    pane_id = line.split()[1]
                if line.startswith("corral-light: http://"):
                    break
            self.assertIsNotNone(pane_id, "".join(lines))
            pr.send_signal(signal.SIGTERM)
            rc = pr.wait(15)
            err = pr.stderr.read()
        finally:
            if pr.poll() is None:
                pr.kill()
                pr.wait(5)
            pr.stdout.close()
            pr.stderr.close()
            meta = json.loads((Path(state) / "panes" / str(pane_id) / "meta.json").read_text())
            if meta.get("pgid"):                  # the adapter outlived the hub
                try:
                    os.killpg(meta["pgid"], signal.SIGKILL)
                except OSError:
                    pass
        self.assertEqual(rc, 0, err)
        self.assertIn("wrote 1 interrupted-turn note", err)
        events = [json.loads(l) for l in
                  (Path(state) / "panes" / pane_id / "events.jsonl").read_text().splitlines()]
        notes = [e["data"] for e in events if e["kind"] == "note" and e["data"].get("shutdown")]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["interrupted"], "sleep 60")
        self.assertEqual(notes[0]["queued"], ["queued behind it"])
        self.assertNotIn("paused", [e["kind"] for e in events])

    def test_the_unit_signals_the_hub_before_its_children(self):
        unit = (ROOT / "corral-light.service").read_text(encoding="utf-8")
        self.assertIn("\nKillMode=mixed\n", unit)


class TheObserverTickSurvivesABadPane(unittest.TestCase):
    """Astra/Grok 2026-09-28: one snapshot() exception froze tick_age_s."""

    def test_tick_advances_past_a_raising_pane(self):
        import hub
        import types
        calls = []

        def boom(**kw):
            raise RuntimeError("bad pane")
        bad = types.SimpleNamespace(snapshot=boom)
        good = types.SimpleNamespace(snapshot=lambda **kw: calls.append(1))
        real = hub.MGR.panes
        hub.MGR.panes = {"bad": bad, "good": good}
        hub._TICK["at"], before = 0.0, hub._TICK["errors"]
        try:
            hub._observe_once()
        finally:
            hub.MGR.panes = real
        self.assertGreater(hub._TICK["at"], 0)
        self.assertEqual(calls, [1], "a pane after the bad one was never observed")
        self.assertEqual(hub._TICK["errors"], before + 1)


class SpawnIsBounded(unittest.TestCase):
    """K7 (Astra/Grok 2026-09-28): one hung Popen froze every lane."""

    def test_a_stuck_spawn_raises_and_the_late_process_is_reaped(self):
        import acp
        import subprocess
        box = {}

        def slow():
            time.sleep(0.6)
            box["p"] = subprocess.Popen(["sleep", "30"], start_new_session=True)
            return box["p"]
        with self.assertRaises(acp.AgentError) as ar:
            acp._spawn(slow, timeout=0.1)
        self.assertIn("did not start", str(ar.exception))
        self.assertTrue(wait_for(lambda: "p" in box and box["p"].poll() is not None),
                        "a spawn that finished after its caller gave up was left running")


class CatalogWriteIsAtomic(FakeLaneCase):
    def test_concurrent_writers_leave_valid_json_and_no_tmp(self):
        import sessions
        ths = [threading.Thread(target=self.mgr.remember_catalog,
                                args=(f"lane{i}", {"model": {"name": "M", "value": "x",
                                                             "options": [{"value": "x"}]}}))
               for i in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        data = json.loads(sessions.CATALOG.read_text())
        self.assertIsInstance(data, dict)
        self.assertFalse(sessions.CATALOG.with_name(sessions.CATALOG.name + ".tmp").exists())


class ALostContextIsSaid(FakeLaneCase):
    """K4 (Astra/Grok 2026-09-28): Ollama's "context lost" chunk arrived inside
    session/load and was swallowed with the replay."""

    def test_a_load_notice_survives_replay_suppression(self):
        self.sessions.AGENTS["fake"]["env"]["FAKE_ACP_NOTICE"] = "the model starts fresh"
        p = self.mgr.create("fake", self.agent_dir)
        p.pause()
        p.resume()
        notes = [e["data"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any(n["text"] == "the model starts fresh" and n["contextLost"]
                            for n in notes), notes)
        self.assertNotIn("REPLAYED HISTORY", self.texts(p))

    def test_ollama_puts_the_notice_in_the_load_result(self):
        import ollama_acp
        sent = []

        class Out:
            def write(self, s):
                if s.strip():
                    sent.append(json.loads(s))

            def flush(self):
                pass
        srv = ollama_acp.Server(out=Out())
        srv._config_options = lambda: []
        srv.handle({"jsonrpc": "2.0", "id": 5, "method": "session/load",
                    "params": {"sessionId": "s"}})
        r = [m for m in sent if m.get("id") == 5][0]["result"]
        self.assertEqual(r["_meta"]["corral/notice"], ollama_acp.RESUME_NOTICE)
        self.assertTrue(r["_meta"]["corral/contextLost"])


class TheTurnLedger(FakeLaneCase):
    """P0-ledger (Astra 2026-09-28): accepted is durable before the ack."""

    def setUp(self):
        super().setUp()
        from corral_core import sessions as core
        self.core = core
        self.state = Path(tempfile.mkdtemp(prefix="corral-light-ledger-"))
        for mod in (self.sessions, core):
            real = mod.STATE
            mod.STATE = self.state
            self.addCleanup(setattr, mod, "STATE", real)

    def ledger(self, p):
        import ledger
        return ledger.TurnLedger(p.dir / "turns.jsonl").turns()

    def test_accepted_is_on_disk_when_send_returns_then_completed(self):
        p = self.mgr.create("fake", self.agent_dir)
        tid = p.send("hello ledger")
        self.assertTrue(tid)
        lines = (p.dir / "turns.jsonl").read_text().splitlines()
        first = json.loads(lines[0])
        self.assertEqual((first["turn"], first["state"]), (tid, "accepted"))
        self.assertEqual(first["text"], "hello ledger")
        self.assertTrue(wait_for(lambda: self.ledger(p)[tid]["state"] == "completed"))
        states = [json.loads(l)["state"] for l in
                  (p.dir / "turns.jsonl").read_text().splitlines()]
        self.assertEqual(states, ["accepted", "dispatched", "completed"])

    def test_a_turn_that_cannot_be_recorded_is_refused_not_acked(self):
        p = self.mgr.create("fake", self.agent_dir)

        def boom(text):
            raise OSError("disk full")
        p._turns().accept = boom
        with self.assertRaises(ValueError) as ar:
            p.send("never recorded")
        self.assertIn("durably", str(ar.exception))
        self.assertEqual(p._queue, [])
        self.assertNotIn("user", self.kinds(p))

    def test_a_dead_hubs_dispatched_turn_is_interrupted_and_noted_never_resent(self):
        p = self.mgr.create("fake", self.agent_dir)
        tid = p.send("sleep 30")
        self.assertTrue(wait_for(lambda: self.ledger(p)[tid]["state"] == "dispatched"))
        queued = p.send("typed ahead")
        # The first hub is DEAD in the case under test, so none of its threads
        # may react to what happens next. Retiring its attachment generation
        # silences them exactly as a process exit would.
        with p._turn_lock:
            p._generation += 1
        # A second hub boots on the same state dir without the first one
        # having exited cleanly — the SIGKILL/OOM case.
        import sessions
        m2 = sessions.Manager.__new__(sessions.Manager)
        m2.panes, m2.subscribers, m2.not_restored = {}, [], 0
        m2._lock = threading.Lock()
        m2.catalog, m2.mcp = {}, None
        m2.restore()
        self.addCleanup(lambda: [q._log and q._log.close() for q in m2.panes.values()])
        q = m2.panes[p.id]
        led = self.ledger(q)
        self.assertEqual(led[tid]["state"], "interrupted")
        self.assertEqual(led[tid]["was"], "dispatched")
        self.assertEqual(led[queued]["state"], "interrupted")
        note = [e["data"] for e in q.events if e["kind"] == "note"
                and "interrupted when the hub stopped" in e["data"]["text"]]
        self.assertEqual(len(note), 1)
        self.assertIn("sleep 30", note[0]["text"])
        self.assertEqual(q._queue, [], "a recovered turn was queued for re-sending")
        # A second boot finds nothing open: the note is said once (the one
        # from the first boot is read back from disk; no second is written).
        for x in m2.panes.values():
            x._log and x._log.close()
        m2.restore()
        self.assertEqual(sum(1 for e in m2.panes[p.id].events if e["kind"] == "note"
                             and "interrupted when the hub stopped" in e["data"]["text"]), 1)

    def test_pause_closes_the_in_flight_turn(self):
        p = self.mgr.create("fake", self.agent_dir)
        tid = p.send("sleep 30")
        self.assertTrue(wait_for(lambda: self.ledger(p)[tid]["state"] == "dispatched"))
        p.pause()
        r = self.ledger(p)[tid]
        self.assertEqual((r["state"], r["why"]), ("interrupted", "paused"))

    def test_the_ledger_is_bounded(self):
        import ledger
        lg = ledger.TurnLedger(self.state / "bound" / "turns.jsonl")
        ids = []
        for i in range(ledger.LEDGER_TURNS + 150):
            t = lg.accept(f"t{i}")
            lg.mark(t, "dispatched")
            lg.mark(t, "completed")
            ids.append(t)
        with (self.state / "bound" / "turns.jsonl").open() as fh:
            n = sum(1 for _ in fh)
        self.assertLessEqual(n, ledger.LEDGER_MAX_LINES)
        turns = lg.turns()
        self.assertLessEqual(len(turns), ledger.LEDGER_MAX_LINES)
        self.assertIn(ids[-1], turns)
        self.assertEqual(turns[ids[-1]]["state"], "completed")
        self.assertNotIn(ids[0], turns)


if __name__ == "__main__":
    unittest.main()
