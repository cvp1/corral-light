#!/usr/bin/python3
"""Tests for the features ported from full Corral (resilience review §3):
roles, scheduled prompts (later.py), transcript search, port.
Collected by test_corral_light.py."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("CORRAL_LIGHT_STATE",
                      tempfile.mkdtemp(prefix="corral-light-test-"))
ROOT = Path(__file__).resolve().parent


class Roles(unittest.TestCase):
    def setUp(self):
        import roles
        self.roles = roles
        self.dir = Path(tempfile.mkdtemp(prefix="corral-light-roles-"))
        self.fields = {"id": "reviewer", "description": "a strict code reviewer",
                       "personality": "blunt", "does": "reviews diffs",
                       "expects": "findings with file:line", "data_class": "internal",
                       "lane": "claude", "posture": "strict"}

    def test_create_load_and_digest(self):
        r = self.roles.create(dict(self.fields), rdir=self.dir)
        self.assertEqual((r.lane, r.posture, r.data_class), ("claude", "strict", "internal"))
        self.assertIn("You are working as `reviewer`", r.preamble)
        again = self.roles.load("reviewer", self.dir)
        self.assertEqual(r.sha256, again.sha256)
        (self.dir / "prompts" / "reviewer.md").write_text("changed instructions")
        self.assertNotEqual(self.roles.load("reviewer", self.dir).sha256, r.sha256,
                            "the digest missed a preamble-only change")

    def test_create_never_overwrites_and_leaves_nothing_on_refusal(self):
        self.roles.create(dict(self.fields), rdir=self.dir)
        with self.assertRaises(self.roles.RoleError):
            self.roles.create(dict(self.fields), rdir=self.dir)
        bad = dict(self.fields, id="bad-one", data_class="secret")
        with self.assertRaises(self.roles.RoleError):
            self.roles.create(bad, rdir=self.dir)
        self.assertFalse((self.dir / "bad-one.toml").exists())

    def test_refused_keys_are_named(self):
        (self.dir / "prompts").mkdir(parents=True)
        (self.dir / "prompts" / "x.md").write_text("do it")
        (self.dir / "sched.toml").write_text(
            'schema = 1\ndescription = "a scheduled thing"\ndata_class = "public"\n'
            'prompt_file = "prompts/x.md"\nschedule = "0 6 * * *"\n')
        with self.assertRaises(self.roles.RoleError) as ar:
            self.roles.load("sched", self.dir)
        self.assertIn("cadence belongs to the job", str(ar.exception))
        rows = self.roles.list_roles(self.dir)
        self.assertIn("error", rows[0], "a broken role was skipped, not reported")

    def test_prompt_file_cannot_escape(self):
        (self.dir / "esc.toml").parent.mkdir(parents=True, exist_ok=True)
        (self.dir / "esc.toml").write_text(
            'schema = 1\ndescription = "escaping role"\ndata_class = "public"\n'
            'prompt_file = "../../etc/passwd"\n')
        with self.assertRaises(self.roles.RoleError) as ar:
            self.roles.load("esc", self.dir)
        self.assertIn("escapes", str(ar.exception))

    def test_the_39_reader_is_strict(self):
        p = self.roles._parse_flat
        self.assertEqual(p('# c\nschema = 1\ndescription = "a \\"q\\" b"\n'),
                         {"schema": 1, "description": 'a "q" b'})
        for bad in ('[table]\n', 'a = [1,2]\n', 'a = "x"\na = "y"\n', "a = 'lit'\n"):
            with self.assertRaises(ValueError, msg=bad):
                p(bad)

    def test_a_newline_cannot_inject_a_key(self):
        f = dict(self.fields, id="inject", lane="", posture="",
                 description='quoted" schedule = "0 6 * * *')
        r = self.roles.create(f, rdir=self.dir)       # loads: the quote was escaped
        self.assertEqual(r.description, 'quoted" schedule = "0 6 * * *')
        with self.assertRaises(self.roles.RoleError):  # a newline is refused outright
            self.roles.create(dict(f, id="inject-two",
                                   description='x"\nschedule = "y'), rdir=self.dir)

    def test_resolve_says_what_it_did_not_apply(self):
        import sessions
        sessions.AGENTS["fakerole"] = {"label": "FakeRole", "argv": [sys.executable],
                                       "posture_via_config_dir": False}
        self.addCleanup(sessions.AGENTS.pop, "fakerole", None)
        self.roles.create(dict(self.fields, lane=""), rdir=self.dir)
        r = self.roles.resolve("reviewer", lane="fakerole", rdir=self.dir)
        self.assertIsNone(r.posture)
        self.assertTrue(any("NOT applied" in n for n in r.notes))
        self.assertTrue(any("recorded, not enforced" in n for n in r.notes))
        with self.assertRaises(self.roles.RoleError):
            self.roles.resolve("reviewer", lane="host:box", rdir=self.dir)

    def test_compose_refuses_rather_than_clips(self):
        with self.assertRaises(self.roles.RoleError):
            self.roles.compose("x" * 10, "y" * (self.roles.MAX_PROMPT() + 1))

    def test_the_role_survives_a_restart(self):
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        p = sessions.Pane.__new__(sessions.Pane)
        meta = {"id": "rolepane1", "agent": "claude", "cwd": str(self.dir),
                "role": "reviewer", "role_sha": "abc", "role_delivery": "preamble"}
        m.panes, m.subscribers, m._lock = {}, [], threading.Lock()
        p = sessions.Pane.from_meta(meta, m)
        self.addCleanup(lambda: p._log and p._log.close())
        self.assertEqual((p.role, p.role_sha, p.role_delivery),
                         ("reviewer", "abc", "preamble"))
        self.assertEqual(p.snapshot()["role"], "reviewer")

    def test_the_dialog_offers_roles_and_the_hub_serves_them(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="f-role"', html)
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn('"/api/session/roles"', hub)


class Later(unittest.TestCase):
    """later.py — scheduled prompts, ported from full Corral's schedule.py."""

    def setUp(self):
        from test_resilience import FakeLaneCase
        import later
        self.later = later
        # Borrow the fake-lane Manager the resilience tests use.
        self.case = FakeLaneCase("run")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.mgr = self.case.mgr
        self.dir = self.case.agent_dir
        self.s = later.Scheduler(self.mgr, Path(tempfile.mkdtemp()) / "schedule.json")

    def iso(self, **delta):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_add_refuses_now_not_at_six_am(self):
        for kw, why in ((dict(agent="nope"), "unknown agent"),
                        (dict(prompt=""), "needs a prompt"),
                        (dict(when=self.iso(hours=-5)), "well past"),
                        (dict(action="remind"), "no attention queue"),
                        (dict(posture="readonly"), "unknown posture"),
                        (dict(repeat="hourly"), "unknown repeat")):
            args = dict(agent="fake", cwd=self.dir, prompt="hi", when=self.iso(minutes=5))
            args.update(kw)
            with self.assertRaises(ValueError, msg=kw) as ar:
                self.s.add(**args)
            self.assertIn(why, str(ar.exception))

    def test_a_due_job_opens_a_pane_and_sends_then_retires(self):
        j = self.s.add("fake", self.dir, "remember fig", self.iso(seconds=-5))
        self.s.tick()
        self.assertEqual(self.s.list(), [], "a fired one-shot stayed on the queue")
        p = next(iter(self.mgr.panes.values()))
        from test_resilience import wait_for
        self.assertTrue(wait_for(lambda: "ok" in self.case.texts(p)))
        self.assertTrue(any("scheduled job" in (e["data"].get("text") or "")
                            for e in p.events if e["kind"] == "note"))
        self.assertTrue(json.loads(self.s.path.read_text()) == {"jobs": []})
        self.assertEqual(j["title"], "remember fig")

    def test_a_stale_job_is_skipped_loudly_and_kept_as_a_record(self):
        from datetime import datetime, timedelta, timezone
        j = self.s.add("fake", self.dir, "too late", self.iso(minutes=1))
        later_now = datetime.now(timezone.utc) + timedelta(hours=5)
        self.s.tick(now=later_now)
        rec = self.s.list()[0]
        self.assertTrue(rec["failed"])
        self.assertIn("missed", rec["last_error"])
        self.assertEqual(self.mgr.panes, {}, "a stale job stampeded")

    def test_a_daily_walks_to_the_next_future_slot(self):
        j = self.s.add("fake", self.dir, "morning", self.iso(seconds=-30), repeat="daily")
        self.s.tick()
        rec = self.s.list()[0]
        from datetime import datetime, timezone
        nxt = self.later.parse_when(rec["at"])
        self.assertGreater(nxt, datetime.now(timezone.utc))
        self.assertLess((nxt - datetime.now(timezone.utc)).total_seconds(), 86400)

    def test_a_nudge_never_queues_behind_a_permission(self):
        p = self.mgr.create("fake", self.dir)
        self.s.add("", "", "hello", self.iso(seconds=-5), action="nudge", pane_id=p.id)
        p.pending["r1"] = {}                 # blocked at a consent gate
        self.s.tick()
        rec = self.s.list()[0]
        self.assertIn("permission gate", rec["last_error"])
        self.assertEqual(p._queue, [])

    def test_a_role_is_inlined_when_armed(self):
        import roles
        rdir = Path(tempfile.mkdtemp())
        os.environ["CORRAL_LIGHT_ROLES_DIR"] = str(rdir)
        self.addCleanup(os.environ.pop, "CORRAL_LIGHT_ROLES_DIR", None)
        roles.create({"id": "nightly", "description": "a nightly summariser",
                      "personality": "brief", "does": "summarise", "expects": "bullets",
                      "data_class": "public"}, rdir=rdir)
        j = self.s.add("fake", self.dir, "summarise", self.iso(minutes=5), role="nightly")
        self.assertTrue(j["prompt"].startswith("You are working as `nightly`"))
        self.assertTrue(j["prompt"].endswith("summarise"))
        self.assertEqual(j["title"], "summarise")
        (rdir / "prompts" / "nightly.md").write_text("EDITED AFTER ARMING")
        self.assertNotIn("EDITED", self.s.list()[0]["prompt"])


class TranscriptSearch(unittest.TestCase):
    """transcripts.py — search and a mechanical digest over events.jsonl."""

    def setUp(self):
        import transcripts
        from datetime import datetime, timezone
        self.t = transcripts
        self.state = Path(tempfile.mkdtemp(prefix="corral-light-fts-"))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def pane(pid, events, meta=True, closed=False):
            d = self.state / "panes" / pid
            d.mkdir(parents=True)
            if meta:
                (d / "meta.json").write_text(json.dumps({
                    "id": pid, "title": f"title {pid}", "agent": "grok",
                    "cwd": "/w", "closed": closed}))
            with (d / "events.jsonl").open("w") as fh:
                for i, (k, data) in enumerate(events, 1):
                    fh.write(json.dumps({"seq": i, "at": now, "pane": pid,
                                         "kind": k, "data": data}) + "\n")
        pane("live1", [("user", {"text": "look at the cookie"}),
                       ("text", {"text": "the route "}), ("text", {"text": "refused it"}),
                       ("tool", {"id": "t1", "title": "Edit hub.py", "kind": "edit",
                                 "status": "completed", "locations": [{"path": "/w/hub.py"}]}),
                       ("permission", {"requestId": "r1", "rawInput": {"command": "SECRETPAYLOAD"}}),
                       ("permission_answered", {"requestId": "r1"}),
                       ("turn_end", {})])
        pane("arch1", [("user", {"text": "archived zebra words"})], closed=True)
        pane("nometa", [("user", {"text": "orphan quokka"})], meta=False)
        self.t.refresh(force=True, state_dir=self.state)

    def test_a_phrase_across_chunks_is_found(self):
        hits = self.t.search("route refused", state_dir=self.state)["hits"]
        self.assertEqual([h["pane"] for h in hits], ["live1"])
        self.assertIn("‹", hits[0]["snippet"])

    def test_archived_and_metaless_panes_are_searchable(self):
        h = self.t.search("zebra", state_dir=self.state)["hits"]
        self.assertTrue(h and h[0]["closed"])
        h = self.t.search("quokka", state_dir=self.state)["hits"]
        self.assertTrue(h and h[0]["metaless"])

    def test_a_consent_payload_is_not_search_material(self):
        self.assertEqual(self.t.search("SECRETPAYLOAD", state_dir=self.state)["hits"], [])

    def test_user_text_is_never_fts_syntax(self):
        out = self.t.search('cookie" OR "*', state_dir=self.state)
        self.assertNotIn("error", out)

    def test_the_digest_counts_from_events(self):
        text = self.t.digest(24, state_dir=self.state, live={"live1"})
        self.assertIn("## title live1 — grok · /w · live", text)
        self.assertIn("- turns: 1", text)
        self.assertIn("- tools: 1 calls", text)
        self.assertIn("permissions: 1 asked · 1 answered · 0 expired", text)
        self.assertNotIn("Docket", text)

    def test_it_reads_lights_state_not_the_full_corrals(self):
        src = (ROOT / "transcripts.py").read_text(encoding="utf-8")
        self.assertIn('"CORRAL_LIGHT_STATE"', src)
        self.assertNotIn("import close", src)


if __name__ == "__main__":
    unittest.main()
