"""Rig cases shared by both products, run against each skin's real Manager.

Mixed into a TestCase that provides `self.mgr` (a Manager with a `fake` lane
registered) and `self.agent_dir`.

    preflight  every refusal, `create` never called, every reason returned
    outcomes   resumed, rebuilt, started-fresh, fresh-primed (through send(),
               via rig), withheld, not-restored, failed; nothing rolls back
    save / up  `save` then `up` on an empty hub reproduces the seats; save
               refuses to overwrite
"""
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import sessions

core = sessions._core
from corral_core import rigs                                      # noqa: E402


def _uniq(prefix):
    """Seat names unique per case: every case in a process shares STATE, and
    an open meta left by another case would hold the name."""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


class _StubPane:
    def __init__(self, pid, seat, state="ready", agent="fake", cwd="/"):
        self.id, self.seat, self.state, self.agent, self.cwd = pid, seat, state, agent, cwd
        self.title, self.seat_withheld, self.events = pid, False, []


class StubManager:
    """Records every call a rig makes. Preflight must make none."""

    def __init__(self, panes=()):
        self.panes = {p.id: p for p in panes}
        self.calls = []

    def create(self, *a, **k):
        self.calls.append(("create", a, k))
        raise AssertionError("create() called")

    def bind_seat(self, *a):
        self.calls.append(("bind_seat", a))
        raise AssertionError("bind_seat() called")


class RigCases:

    # ── helpers ──────────────────────────────────────────────────────────
    def setUp(self):
        super().setUp()
        rigs_dir = rigs.rigs_dir()
        shutil.rmtree(rigs_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rigs_dir, True)
        self.cwd2 = tempfile.mkdtemp(prefix="rig-cwd-")
        self.addCleanup(shutil.rmtree, self.cwd2, True)

    def write(self, name, text):
        d = rigs.rigs_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.toml").write_text(text, encoding="utf-8")

    def seat_block(self, seat, agent="fake", cwd=None, **extra):
        s = {"id": seat, "agent": agent, "cwd": cwd or self.agent_dir, **extra}
        return rigs.compose([s]).split("version = 1\n", 1)[1]

    def rig(self, name, *blocks):
        self.write(name, "version = 1\n" + "".join(blocks))

    def refused(self, name, mgr=None):
        mgr = mgr or StubManager()
        with self.assertRaises(rigs.RigRefused) as ar:
            rigs.up(mgr, name)
        self.assertEqual(mgr.calls, [], "preflight touched the manager")
        return ar.exception.reasons

    # ── preflight ────────────────────────────────────────────────────────
    def test_every_preflight_refusal_starts_nothing(self):
        s = _uniq("s")
        cases = {
            "parse": ("version = 1\n[[seat]\n", "not valid TOML"),
            "version": ("version = 2\n" + self.seat_block(s), "version = 2"),
            "grammar": ("version = 1\n" + self.seat_block("Bad_Name"), "a seat is"),
            "agent": ("version = 1\n" + self.seat_block(s, agent="nope"),
                      "unknown agent 'nope'"),
            "cwd": ("version = 1\n" + self.seat_block(s, cwd="/no/such/dir"),
                    "cwd is not a directory"),
            "seats": ("version = 1\n" + "".join(self.seat_block(f"s{i}")
                                                for i in range(rigs.MAX_RIG_SEATS + 1)),
                      f"a rig holds at most {rigs.MAX_RIG_SEATS}"),
            "twice": ("version = 1\n" + self.seat_block(s) + self.seat_block(s),
                      "named twice"),
            "key": ("version = 1\n" + self.seat_block(s) + 'model = "x"\n',
                    "unknown key 'model'"),
            "topkey": ("version = 1\nname = \"x\"\n" + self.seat_block(s), "unknown key 'name'"),
            "posture": ("version = 1\n" + self.seat_block(s, posture="yolo"),
                        "unknown posture"),
            "prompt": ("version = 1\n" + self.seat_block(
                s, prompt="x" * (rigs.MAX_RIG_PROMPT + 1)), "the cap is"),
            "empty": ("version = 1\n", "no [[seat]]"),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name):
                self.write(name, text)
                reasons = self.refused(name)
                self.assertTrue(any(want in r for r in reasons), (name, reasons))

    def test_a_seat_already_live_refuses_the_whole_rig(self):
        a, b = _uniq("a"), _uniq("b")
        self.rig("live", self.seat_block(a), self.seat_block(b))
        mgr = StubManager([_StubPane("p1", b, state="busy")])
        reasons = self.refused("live", mgr)
        self.assertTrue(any(f"@{b}: already live on pane p1" in r for r in reasons), reasons)
        # A paused holder is not live: it is what `up` resumes.
        rigs.preflight(StubManager([_StubPane("p1", b, state="detached")]), "live",
                       {"version": 1, "seat": [{"id": b, "agent": "fake",
                                                "cwd": self.agent_dir}]})

    def test_a_role_that_does_not_resolve_and_a_role_with_no_resolver(self):
        s = _uniq("s")
        self.rig("role", self.seat_block(s, role="nosuchrole"))

        def boom(role, agent, posture):
            raise ValueError(f"no role {role!r}")
        with mock.patch.object(core, "ROLE_RESOLVER", boom):
            reasons = self.refused("role")
        self.assertTrue(any("does not resolve: no role 'nosuchrole'" in r
                            for r in reasons), reasons)
        with mock.patch.object(core, "ROLE_RESOLVER", None):
            reasons = self.refused("role")
        self.assertTrue(any("this product has no roles" in r for r in reasons), reasons)

    def test_every_reason_is_returned_not_just_the_first(self):
        s = _uniq("s")
        self.write("many", "version = 3\n" + self.seat_block(s, agent="nope",
                                                            cwd="/no/such"))
        reasons = self.refused("many")
        self.assertEqual(len(reasons), 3, reasons)

    def test_a_rig_name_is_a_seat_name(self):
        for bad in ("../etc", "Up", "a/b", ""):
            with self.assertRaises(ValueError):
                rigs.up(StubManager(), bad)
            with self.assertRaises(ValueError):
                rigs.save(StubManager(), bad)

    # ── outcomes ─────────────────────────────────────────────────────────
    def _seated(self, seat, **kw):
        p = self.mgr.create("fake", self.agent_dir, **kw)
        self.assertEqual(p.state, "ready", p.error)
        self.mgr.bind_seat(p.id, seat)
        return p

    def _by_seat(self, out):
        return {o["seat"]: o for o in out["outcomes"]}

    def test_resumed_and_rebuilt(self):
        a, b = _uniq("a"), _uniq("b")
        pa, pb = self._seated(a), self._seated(b)
        pa.pause()
        pb.pause()
        self.rig("pair", self.seat_block(a), self.seat_block(b, prompt="never sent"))
        # b's lane will refuse session/load from here on.
        real_resume = type(pb).resume

        def no_load(pane):
            if pane is pb:
                env = dict(sessions.AGENTS["fake"].get("env") or {}, FAKE_ACP_NO_LOAD="1")
                with mock.patch.dict(sessions.AGENTS["fake"], {"env": env}):
                    return real_resume(pane)
            return real_resume(pane)
        with mock.patch.object(type(pb), "resume", no_load):
            out = rigs.up(self.mgr, "pair")
        o = self._by_seat(out)
        self.assertEqual(o[a]["outcome"], "resumed", o[a])
        self.assertEqual(o[a]["pane"], pa.id)
        self.assertEqual(pa.state, "ready")
        self.assertEqual(o[b]["outcome"], "rebuilt", o[b])
        self.assertIn("did not reload", o[b]["why"])
        self.assertIn("opening prompt was not sent", o[b]["why"])
        self.assertIn(pb.id, self.mgr.panes, "the rebuilt pane left the roster")
        self.assertFalse([e for e in pb.events if e["kind"] == "user"
                          and e["data"]["text"] == "never sent"])
        # one line per seat, and the transcript says what the rig did
        self.assertEqual(len(out["lines"]), 2)
        self.assertTrue(out["lines"][0].startswith(f"@{a}  resumed"))
        self.assertTrue(any(e["kind"] == "note" and "rig pair:" in e["data"]["text"]
                            for e in pa.events))

    def test_started_fresh_and_fresh_primed_through_send(self):
        a, b = _uniq("a"), _uniq("b")
        self.rig("fresh", self.seat_block(a),
                 self.seat_block(b, cwd=self.cwd2, posture="strict",
                                 prompt="remember plum"))
        out = rigs.up(self.mgr, "fresh")
        o = self._by_seat(out)
        self.assertEqual(o[a]["outcome"], "started-fresh", o[a])
        self.assertEqual(o[b]["outcome"], "fresh-primed", o[b])
        pb = self.mgr.panes[o[b]["pane"]]
        self.assertEqual((pb.seat, pb.cwd, pb.agent, pb.posture),
                         (b, str(Path(self.cwd2)), "fake", "strict"))
        users = [e["data"] for e in pb.events if e["kind"] == "user"]
        self.assertEqual(users, [{"text": "remember plum", "turn": o[b]["turn"],
                                  "via": "rig"}])
        self.assertFalse(any(e["kind"] == "peer" for e in pb.events),
                         "an opening prompt went down the peer path")
        pa = self.mgr.panes[o[a]["pane"]]
        self.assertFalse(any(e["kind"] == "user" for e in pa.events))

    def test_a_role_preset_and_its_first_turn(self):
        """A role's preset applies; with a prompt, the role's instructions then
        the prompt are the first turn (through send, via rig); with no prompt
        nothing is sent and the outcome says so."""
        a, b = _uniq("a"), _uniq("b")
        seen = []

        def resolver(role, agent, posture):
            seen.append((role, agent, posture))
            return {"agent": agent, "posture": "edits", "effort": None,
                    "sha": "f" * 64, "notes": ["a note from the role"],
                    "compose": lambda prompt: f"ROLE {role}\n\n---\n\n{prompt}"}
        self.rig("roles", self.seat_block(a, role="critic", prompt="look at x"),
                 self.seat_block(b, role="critic"))
        with mock.patch.object(core, "ROLE_RESOLVER", resolver):
            out = rigs.up(self.mgr, "roles")
        self.assertEqual(seen, [("critic", "fake", None), ("critic", "fake", None)],
                         "a role is resolved once per seat, at preflight")
        o = self._by_seat(out)
        self.assertEqual(o[a]["outcome"], "fresh-primed", o[a])
        pa = self.mgr.panes[o[a]["pane"]]
        self.assertEqual((pa.role, pa.role_sha, pa.posture), ("critic", "f" * 64, "edits"))
        self.assertEqual([e["data"]["text"] for e in pa.events if e["kind"] == "user"],
                         ["ROLE critic\n\n---\n\nlook at x"])
        self.assertEqual(o[b]["outcome"], "started-fresh", o[b])
        self.assertIn("instructions were not sent", o[b]["why"])
        self.assertIn("a note from the role", o[b]["why"])
        pb = self.mgr.panes[o[b]["pane"]]
        self.assertFalse(any(e["kind"] == "user" for e in pb.events))

    def test_withheld_when_the_seat_is_taken_after_the_check(self):
        a, b = _uniq("a"), _uniq("b")
        self.rig("race", self.seat_block(a), self.seat_block(b))
        real = rigs.preflight

        def racing(mgr, name, doc):
            plan = real(mgr, name, doc)
            self._seated(b)                         # someone else, just now
            return plan
        with mock.patch.object(rigs, "preflight", racing):
            out = rigs.up(self.mgr, "race")
        o = self._by_seat(out)
        self.assertEqual(o[a]["outcome"], "started-fresh")
        self.assertEqual(o[b]["outcome"], "withheld", o[b])
        self.assertEqual(sum(1 for p in self.mgr.panes.values() if p.seat == b), 1)

    def test_not_restored_past_the_cap_and_for_a_seat_held_on_disk(self):
        a, b, c = _uniq("a"), _uniq("b"), _uniq("c")
        self._seated(_uniq("x"))                   # one live pane already
        d = core.STATE / "panes" / ("disk" + uuid.uuid4().hex[:8])
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "id": d.name, "agent": "fake", "cwd": self.agent_dir, "seat": c,
            "created": "2026-09-30T00:00:00Z", "closed": False}))
        self.addCleanup(shutil.rmtree, d, True)
        self.rig("cap", self.seat_block(a), self.seat_block(b), self.seat_block(c))
        with mock.patch.object(core, "MAX_PANES", 2):
            out = rigs.up(self.mgr, "cap")
        o = self._by_seat(out)
        self.assertEqual(o[a]["outcome"], "started-fresh")
        self.assertEqual(o[b]["outcome"], "not-restored", o[b])
        self.assertIn("2 live panes is the cap", o[b]["why"])
        self.assertEqual(o[c]["outcome"], "not-restored", o[c])
        self.assertIn("on disk", o[c]["why"])
        self.assertIsNone(o[b]["pane"])

    def test_failed_does_not_roll_back_the_others(self):
        a, b, c = _uniq("a"), _uniq("b"), _uniq("c")
        sessions.AGENTS["fakeoff"] = dict(sessions.AGENTS["fake"],
                                         unavailable="switched off for the test")
        self.addCleanup(sessions.AGENTS.pop, "fakeoff", None)
        self.rig("mixed", self.seat_block(a), self.seat_block(b, agent="fakeoff"),
                 self.seat_block(c))
        out = rigs.up(self.mgr, "mixed")
        self.assertEqual([o["outcome"] for o in out["outcomes"]],
                         ["started-fresh", "failed", "started-fresh"])
        self.assertIn("switched off", out["outcomes"][1]["why"])
        self.assertEqual({p.seat for p in self.mgr.panes.values()} & {a, b, c}, {a, c})

    # ── save / up ────────────────────────────────────────────────────────
    def test_save_then_up_on_an_empty_hub_reproduces_the_seats(self):
        a, b = _uniq("a"), _uniq("b")
        pa = self._seated(a)
        pb = self.mgr.create("fake", self.cwd2, "strict")
        self.mgr.bind_seat(pb.id, b)
        self.mgr.create("fake", self.agent_dir)            # unseated: not saved
        saved = rigs.save(self.mgr, "team")
        self.assertEqual(saved["seats"], [a, b])
        with self.assertRaises(ValueError):
            rigs.save(self.mgr, "team")                    # no silent overwrite
        rigs.save(self.mgr, "team", replace=True)
        text = (rigs.rigs_dir() / "team.toml").read_text()
        self.assertNotIn("prompt", text.split("version = 1", 1)[1],
                         "rig save wrote a prompt")
        want = {(p.seat, p.cwd, p.agent, p.posture) for p in (pa, pb)}
        for pid in list(self.mgr.panes):                   # an empty hub
            self.mgr.close(pid)
        self.assertEqual(rigs.list_rigs(), [{"name": "team", "seats": [a, b]}])
        out = rigs.up(self.mgr, "team")
        self.assertEqual([o["outcome"] for o in out["outcomes"]],
                         ["started-fresh", "started-fresh"])
        got = {(p.seat, p.cwd, p.agent, p.posture) for p in self.mgr.panes.values()}
        self.assertEqual(got, want)
        self.assertEqual(rigs.rm("team"), "team")
        self.assertEqual(rigs.list_rigs(), [])
