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


if __name__ == "__main__":
    unittest.main()
