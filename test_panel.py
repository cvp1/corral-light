#!/usr/bin/python3
"""Durable panel runs (panel.py) against the fake lane.

The fake agent echoes its prompt, so a charge that ends with a fenced json
verdict block comes back as the arm's answer and the code-side extraction
has something real to parse. Two fake lanes stand in for two vendors.

    P1  start returns at once; the run reaches done; one sealed file per arm
        with the whole answer; the packet names verdicts and findings; the
        author's pane gets the packet as a question; needs_ack until acked
    P2  the brake: next on identical inputs is refused; a changed charge is
        accepted and round 2 cross-feeds the sealed round-1 answers; MAX_ROUNDS
        holds
    P3  a hub restart mid-run: a second Panels on the same records resumes,
        waits on the turn already sent (adopted by the keeper), and finishes
    P4  a dead arm fails that arm, the run still completes and says so
    P5  refusals: an arm on the author's lane, a duplicate lane, an unknown
        lane, rounds 3, an empty charge
    P6  the pure parts: parse_answer, merge_findings, dissent, choose_synth_lane
    P7  cancel stops a running run
    P8  the peer routes (panel_mcp's) by pane token

Collected by test_corral_light.py.
"""
import json
import os
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

ROOT = Path(__file__).resolve().parent
FAKE = ROOT / "testkit" / "fake_acp_agent.py"
WAIT_S = 40

CHARGE = """Review the attached design. Be adversarial.

```json
{"verdict": "build-narrower", "findings": [
  {"file": "hub.py", "line": 12, "severity": "high", "claim": "the gate can be skipped", "evidence": "trace A"},
  {"file": "sessions.py", "line": 7, "severity": "low", "claim": "a typo", "evidence": "line 7"}]}
```"""


def wait_for(pred, timeout=WAIT_S, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class _PanelBase(unittest.TestCase):
    """Setup and helpers only; the tests are in the subclasses."""

    def setUp(self):
        import sessions
        import panel
        self.sessions, self.panel = sessions, panel
        self.agent_dir = tmpdir(self, "fake-acp-")
        for lane in ("fake", "fake2", "fake3", "fake4"):
            sessions.AGENTS[lane] = {
                "label": lane.title(), "argv": [sys.executable, str(FAKE)],
                "requires": (str(FAKE),), "posture_via_config_dir": False,
                "tools": False, "env": {"FAKE_ACP_DIR": self.agent_dir}}
            self.addCleanup(sessions.AGENTS.pop, lane, None)
        self.state = Path(tmpdir(self, "corral-light-panel-state-"))
        for mod in (sessions, sessions._core):
            real = mod.STATE
            mod.STATE = self.state
            self.addCleanup(setattr, mod, "STATE", real)
        self.work = Path(tmpdir(self, "panel-work-"))
        self.mgr = self._mgr()
        self.addCleanup(self._close_all)
        # The synthesizer must be a lane that is neither an arm's nor the author's.
        self._avail = panel.available_lanes
        panel.available_lanes = lambda mgr: ["fake", "fake2", "fake3", "fake4"]
        self.addCleanup(setattr, panel, "available_lanes", self._avail)
        self._timeout = panel.ARM_TIMEOUT_S
        panel.ARM_TIMEOUT_S = 30
        self.addCleanup(setattr, panel, "ARM_TIMEOUT_S", self._timeout)

    def _mgr(self):
        s = self.sessions
        m = s.Manager.__new__(s.Manager)
        m.panes, m.subscribers, m.not_restored, m.orphans = {}, [], 0, {}
        m._lock = threading.Lock()
        m.catalog = {}
        m.mcp = None
        m.panels = self.panel.Panels(m, self.state / "panels")
        return m

    def _close_all(self):
        for m in (getattr(self, "mgr2", None), self.mgr):
            if m is None:
                continue
            for pid in list(m.panes):
                try:
                    m.close(pid)
                except Exception:                 # noqa: BLE001
                    pass

    def _start(self, **kw):
        args = dict(charge=CHARGE, lanes=["fake", "fake2"], cwd=str(self.work),
                    topic="gate design", rounds=1)
        args.update(kw)
        return self.mgr.panels.start(**args)

    def _done(self, panels, pid):
        return wait_for(lambda: panels.get(pid)["status"] in ("done", "failed", "cancelled"))


class PanelCase(_PanelBase):
    # ── P1 ───────────────────────────────────────────────────────────────
    def test_p1_a_run_seals_synthesizes_and_reports_to_the_author(self):
        author = self.mgr.create("fake3", str(self.work))
        self.assertEqual(author.state, "ready", author.error)
        t0 = time.time()
        v = self._start(author=author.id)
        self.assertLess(time.time() - t0, 5, "start did not return at once")
        self.assertEqual(v["status"], "running")
        pid = v["id"]
        self.assertTrue(self._done(self.mgr.panels, pid))
        rec = self.mgr.panels.get(pid)
        self.assertEqual(rec["status"], "done", rec.get("error"))
        self.assertEqual(rec["round"], 1)
        out = Path(rec["out_dir"])
        self.assertTrue(str(out).startswith(str(self.work / "reviews")))
        for a in rec["arms"]:
            r = a["rounds"]["1"]
            self.assertEqual(r["status"], "sealed", r)
            body = Path(r["file"]).read_text(encoding="utf-8")
            self.assertIn(f"lane: {a['lane']}", body)
            self.assertIn("the gate can be skipped", body, "the sealed file lacks the answer")
            self.assertEqual(r["verdict"], "build-narrower")
            self.assertEqual(len(r["findings"]), 2)
            self.assertTrue(self.mgr.panes[a["pane"]].minimized, "an arm came up on the wall")
        # Code-side table: two findings, both arms for each.
        table = rec["findings"]
        self.assertEqual([f["severity"] for f in table], ["high", "low"])
        self.assertEqual(sorted(table[0]["arms"]), ["fake", "fake2"])
        self.assertEqual(table[0]["status"], "new")
        syn = rec["synth"]
        # Neither an arm's lane nor the author's (fake3): the one left over.
        self.assertEqual(syn["lane"], "fake4", syn)
        self.assertEqual(syn["status"], "sealed", syn)
        self.assertTrue((out / "synthesis.md").exists())
        pk = (out / "packet.md").read_text(encoding="utf-8")
        self.assertIn("| fake | build-narrower |", pk)
        self.assertIn("hub.py:12", pk)
        self.assertIn("Plurality verdict (round 1): **build-narrower**", pk)
        self.assertIn("r1-fake.md", pk)
        self.assertTrue(rec["needs_ack"])
        self.assertTrue(rec["packet_path"].endswith("packet.md"))
        # The author's pane got the packet as a question, with the path.
        self.assertTrue(wait_for(lambda: author.question))
        self.assertIn(pid, author.question["text"])
        self.assertIn("packet.md", author.question["text"])
        self.assertEqual(author.question.get("source"), "panel")
        self.assertEqual(author.snapshot()["display"], "needs-you")
        # The arms' turns were the panel's, not the human's.
        arm = self.mgr.panes[rec["arms"][0]["pane"]]
        self.assertEqual(arm.turn_via, "panel")
        self.assertEqual(arm.snapshot()["display"], "idle")
        # Ack clears the rail entry; the wire view carries no bodies.
        view = self.mgr.panels.ack(pid)
        self.assertFalse(view["needs_ack"])
        self.assertNotIn("findings", view["arms"][0]["rounds"]["1"])
        self.assertEqual(view["arms"][0]["rounds"]["1"]["findings_n"], 2)
        self.assertEqual(self.mgr.state()["panels"][0]["id"], pid)
        # The record is on disk and reloads.
        again = self.panel.Panels(self.mgr, self.state / "panels")
        self.assertEqual(again.get(pid)["status"], "done")

    # ── P2 ───────────────────────────────────────────────────────────────
    def test_p2_the_brake_refuses_identical_inputs_and_round_2_crossfeeds(self):
        v = self._start()
        pid = v["id"]
        self.assertTrue(self._done(self.mgr.panels, pid))
        with self.assertRaises(self.panel.PanelError) as cm:
            self.mgr.panels.next(pid)
        self.assertIn("neither the charge nor the tree changed", str(cm.exception))
        charge2 = CHARGE.replace("a typo", "a typo, now a crash").replace('"low"', '"medium"')
        v2 = self.mgr.panels.next(pid, charge=charge2)
        self.assertEqual(v2["status"], "running")
        self.assertTrue(self._done(self.mgr.panels, pid))
        rec = self.mgr.panels.get(pid)
        self.assertEqual(rec["status"], "done", rec.get("error"))
        self.assertEqual(rec["round"], 2)
        for a in rec["arms"]:
            r2 = a["rounds"]["2"]
            self.assertEqual(r2["status"], "sealed", r2)
            body = Path(r2["file"]).read_text(encoding="utf-8")
            other = "fake2" if a["lane"] == "fake" else "fake"
            self.assertIn(f'lane="{other}" round="1"', body, "round 2 did not carry the other arm's sealed answer")
            self.assertIn("The charge changed", body)
        # Same file:line in both rounds: repeated, never re-counted as new.
        table = {f["file"]: f for f in rec["findings"]}
        self.assertEqual(table["hub.py"]["status"], "repeated")
        self.assertEqual(table["sessions.py"]["status"], "repeated")
        self.assertEqual(len(rec["history"]), 2)
        pk = (Path(rec["out_dir"]) / "packet.md").read_text(encoding="utf-8")
        self.assertIn("| r1 | r2 |", pk)
        # The cap.
        rec["round"] = self.panel.MAX_ROUNDS
        with self.assertRaises(self.panel.PanelError) as cm:
            self.mgr.panels.next(pid, charge=charge2 + "\nmore")
        self.assertIn("cap", str(cm.exception))

    # ── P3 ───────────────────────────────────────────────────────────────
    def test_p3_a_hub_restart_mid_run_resumes_from_the_records(self):
        slow = CHARGE + "\nsleep 4"          # the fake agent sleeps on a `sleep N` line
        v = self._start(charge=slow)
        pid = v["id"]
        self.assertTrue(wait_for(lambda: all(
            (a["rounds"].get("1") or {}).get("status") == "sent"
            for a in self.mgr.panels.get(pid)["arms"])))
        # The hub dies: notes, detach, records stay on disk.
        self.mgr.panels._cancel[pid].set()       # stop the old thread touching anything
        self.mgr.shutdown_notes("SIGTERM")
        metas = {p.id: json.loads((p.dir / "meta.json").read_text()) for p in self.mgr.panes.values()}
        self.mgr.panes.clear()
        self.mgr.panels.records[pid]["status"] = "running"   # as the old thread left it on disk
        # The next hub.
        self.mgr2 = m2 = self._mgr()
        for pane_id, meta in metas.items():
            q = self.sessions.Pane.from_meta(meta, m2)
            m2.panes[q.id] = q
            self.assertTrue(q.reattach(), f"pane {q.id} did not reattach")
        rec2 = m2.panels.get(pid)
        self.assertEqual(rec2["status"], "running")
        sent = [a["rounds"]["1"]["turn"] for a in rec2["arms"]]
        m2.panels.resume()
        self.assertTrue(self._done(m2.panels, pid))
        rec2 = m2.panels.get(pid)
        self.assertEqual(rec2["status"], "done", rec2.get("error"))
        for a, tid in zip(rec2["arms"], sent):
            self.assertEqual(a["rounds"]["1"]["turn"], tid, "the arm was re-sent after the restart")
            self.assertEqual(a["rounds"]["1"]["status"], "sealed")
            # The fake's `sleep` verb answers "sleeping" (no echo): the whole
            # answer, written by the turn the FIRST hub sent, is in the seal.
            self.assertIn("sleeping", Path(a["rounds"]["1"]["file"]).read_text(encoding="utf-8"))
        self.assertTrue((Path(rec2["out_dir"]) / "packet.md").exists())

    # ── P4 ───────────────────────────────────────────────────────────────
    def test_p4_a_dead_arm_fails_that_arm_and_the_run_still_completes(self):
        import os as _os
        import signal as _signal
        v = self._start(charge="sleep 30")
        pid = v["id"]
        self.assertTrue(wait_for(lambda: all(
            (a["rounds"].get("1") or {}).get("status") == "sent"
            for a in self.mgr.panels.get(pid)["arms"])))
        for a in self.mgr.panels.get(pid)["arms"]:          # both agents die mid-turn
            _os.kill(self.mgr.panes[a["pane"]].client.p.adapter_pid, _signal.SIGKILL)
        self.assertTrue(self._done(self.mgr.panels, pid))
        rec = self.mgr.panels.get(pid)
        self.assertEqual(rec["status"], "done", rec.get("error"))
        for a in rec["arms"]:
            self.assertEqual(a["rounds"]["1"]["status"], "failed", a)
            self.assertTrue(a["rounds"]["1"]["why"], a)
        self.assertEqual(rec["synth"]["status"], "skipped")
        pk = (Path(rec["out_dir"]) / "packet.md").read_text(encoding="utf-8")
        self.assertIn("0 of 2 arms answered", pk)
        self.assertIn("fake r1: failed", pk)

    # ── P5 ───────────────────────────────────────────────────────────────
    def test_p5_refusals(self):
        P = self.panel.PanelError
        author = self.mgr.create("fake", str(self.work))
        with self.assertRaises(P):
            self._start(author=author.id)                       # author's lane is an arm
        with self.assertRaises(P):
            self._start(lanes=["fake", "fake"])
        with self.assertRaises(P):
            self._start(lanes=["nope"])
        with self.assertRaises(P):
            self._start(rounds=3)
        with self.assertRaises(P):
            self._start(charge="   ")
        with self.assertRaises(P):
            self._start(lanes=["fake", "fake2", "fake3", "fake", "fake2"])
        with self.assertRaises(P):
            self._start(cwd=str(self.work / "missing"))
        with self.assertRaises(P):
            self._start(synth_lane="fake")                       # an arm's lane
        self.assertEqual(self.mgr.panels.list(), [])

    # ── P6 ───────────────────────────────────────────────────────────────
    def test_p6_pure_parts(self):
        pa = self.panel.parse_answer
        self.assertIsNone(pa("no block"))
        self.assertIsNone(pa("```json\nnot json\n```"))
        r = pa('x ```json\n{"verdict": "SHIP it", "findings": [{"file": "a", "line": "3", '
               '"severity": "HIGH", "claim": "c", "evidence": "e", "status": "Repeated"}, 7]}\n```')
        self.assertEqual(r["verdict"], "ship it")
        self.assertEqual(r["findings"], [{"file": "a", "line": 3, "severity": "high", "claim": "c",
                                          "evidence": "e", "status": "repeated"}])
        arms = [{"lane": "a", "rounds": {"1": {"verdict": "ship", "findings": [
                    {"file": "x.py", "line": 1, "severity": "low", "claim": "k"}]},
                                         "2": {"verdict": "ship", "findings": [
                    {"file": "x.py", "line": 1, "severity": "high", "claim": "k"},
                    {"file": "y.py", "line": 2, "severity": "medium", "claim": "n"}]}}},
                {"lane": "b", "rounds": {"1": {"verdict": "amend", "findings": []},
                                         "2": {"verdict": "ship", "findings": [
                    {"file": "X.PY", "line": 1, "severity": "low", "claim": "k", "status": "resolved"}]}}}]
        t = self.panel.merge_findings(arms, 2)
        self.assertEqual([(f["file"], f["severity"], sorted(f["arms"]), f["status"]) for f in t],
                         [("x.py", "high", ["a", "b"], "resolved"), ("y.py", "medium", ["a"], "new")])
        self.assertEqual(self.panel.dissent(arms, 1), ("ship", [{"lane": "b", "verdict": "amend"}]))
        self.assertEqual(self.panel.dissent(arms, 2), ("ship", []))
        cs = self.panel.choose_synth_lane
        self.assertEqual(cs(["codex", "grok"], "claude", ["claude", "codex", "grok", "gemini"]), "gemini")
        self.assertEqual(cs(["codex", "grok"], "claude", ["claude", "codex", "grok"]), "codex")
        self.assertIsNone(cs(["codex"], "claude", ["claude"]))
        self.assertEqual(self.panel.slug("Gate Design: v2!"), "gate-design-v2")
        pk = self.panel.parse_packet('```json\n{"verdicts": {"codex": "ship"}, "findings": [], '
                                     '"dissent": [{"lane": "grok", "quote": "no"}], '
                                     '"questions_for_operator": ["q"], "next_action": "merge"}\n```')
        self.assertEqual(pk["verdicts"], {"codex": "ship"})
        self.assertEqual(pk["dissent"][0]["quote"], "no")
        self.assertIsNone(self.panel.parse_packet('```json\n{"verdict": "x"}\n```'))

    # ── P7 ───────────────────────────────────────────────────────────────
    def test_p7_cancel_stops_a_running_run(self):
        v = self._start(charge="sleep 30")
        pid = v["id"]
        self.assertTrue(wait_for(lambda: all(
            (a["rounds"].get("1") or {}).get("status") == "sent"
            for a in self.mgr.panels.get(pid)["arms"])))
        view = self.mgr.panels.cancel(pid)
        self.assertEqual(view["status"], "cancelled")
        time.sleep(1)
        rec = self.mgr.panels.get(pid)
        self.assertEqual(rec["status"], "cancelled")
        self.assertFalse(rec["needs_ack"])
        self.assertFalse((Path(rec["out_dir"]) / "packet.md").exists())
        with self.assertRaises(self.panel.PanelError):
            self.mgr.panels.next(pid)

    # ── P8 ───────────────────────────────────────────────────────────────
    def test_p8_the_peer_routes_by_pane_token(self):
        author = self.mgr.create("fake3", str(self.work))
        tok = self.mgr.mint_pane_token(author)
        st, r = self.mgr.peer_http("POST", "/api/peer/panel/start", tok,
                                   {"topic": "t", "lanes": ["fake", "fake2"], "charge": CHARGE})
        self.assertEqual(st, 200)
        self.assertEqual(r["result"], "started", r)
        pid = r["panel"]["id"]
        self.assertEqual(r["panel"]["author"], author.id)
        st, r = self.mgr.peer_http("GET", "/api/peer/panel/status", tok, {"id": pid})
        self.assertEqual((st, r["result"]), (200, "status"))
        st, r = self.mgr.peer_http("GET", "/api/peer/panel/status", tok, {})
        self.assertEqual(r["result"], "list")
        self.assertEqual(r["panels"][0]["id"], pid)
        st, r = self.mgr.peer_http("POST", "/api/peer/panel/start", tok,
                                   {"topic": "t", "lanes": ["fake3"], "charge": CHARGE})
        self.assertEqual(r["result"], "refused")           # the author's own lane
        st, r = self.mgr.peer_http("POST", "/api/peer/panel/start", "bogus", {})
        self.assertEqual(st, 401)
        self.assertTrue(self._done(self.mgr.panels, pid))
        st, r = self.mgr.peer_http("POST", "/api/peer/panel/ack", tok, {"id": pid})
        self.assertEqual(r["result"], "acked")
        # The MCP server's tool layer, offline: arguments are checked before any call.
        import panel_mcp
        out, err = panel_mcp.call_tool("panel_start", {"topic": "t", "lanes": ["x"]}, env={})
        self.assertEqual(out["result"], "refused")
        out, err = panel_mcp.call_tool("panel_status", {}, env={})
        self.assertEqual((out["result"], err), ("failed", True))
        self.assertEqual([t["name"] for t in panel_mcp.TOOLS],
                         ["panel_start", "panel_status", "panel_await", "panel_next", "panel_ack"])
        d = panel_mcp.descriptor(author, "http://127.0.0.1:1", "tok")
        self.assertEqual(d["name"], "corral-panel")
        self.assertIn({"name": "CORRAL_PANE_TOKEN", "value": "tok"}, d["env"])


if __name__ == "__main__":
    unittest.main()


class PanelAfterReview2(_PanelBase):
    """The 2026-10-31 run: synthesized by the CALLER's own lane, because the
    command line named no author; and a packet that named the host."""

    def test_r2_the_callers_lane_never_synthesizes_without_an_author(self):
        caller = self.mgr.create("fake4", str(self.work))
        self.assertEqual(caller.state, "ready", caller.error)
        v = self._start(caller=caller.id)
        self.assertTrue(self._done(self.mgr.panels, v["id"]))
        rec = self.mgr.panels.get(v["id"])
        self.assertEqual(rec["status"], "done", rec.get("error"))
        self.assertIsNone(rec["author"], "a caller is excluded, not reported to")
        self.assertNotEqual(rec["synth"]["lane"], "fake4", rec["synth"])
        self.assertEqual(rec["synth"]["lane"], "fake3")
        self.assertIsNone(caller.question)
        with self.assertRaises(self.panel.PanelError):
            self._start(caller=caller.id, lanes=["fake4", "fake"])   # nor arms

    def test_r2_the_packet_names_no_host(self):
        import socket
        v = self._start()
        self.assertTrue(self._done(self.mgr.panels, v["id"]))
        out = Path(self.mgr.panels.get(v["id"])["out_dir"])
        host = socket.gethostname()
        for name in ("packet.md", "packet.json"):
            body = (out / name).read_text(encoding="utf-8")
            self.assertNotIn(host, body, f"{name} names this host")
            self.assertNotIn('"home"', body)

    def test_r2_same_hub_treats_localhost_as_loopback(self):
        import consult
        with unittest.mock.patch.dict(os.environ, {"CORRAL_HUB_URL": "http://127.0.0.1:8098"}):
            self.assertTrue(consult._same_hub("http://localhost:8098/"))
            self.assertFalse(consult._same_hub("http://127.0.0.1:8199"))
