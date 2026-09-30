#!/usr/bin/python3
"""The Claude login, foreseen and survived (mac-host, 2026-09-30, 05:41).

A pane died at its first prompt with `-32000 Authentication required` — the
refresh token had lapsed — and the only way back was: read the JSON-RPC
error, guess, open a terminal, `/login`, come back, press ↻. Three fixes,
each with a test that could fail:

  * claude_auth reads the credential's OWN expiry (never a token) and says
    expired / expiring / fine / cannot tell — and cannot tell is not fine.
  * a pane that dies of it says so in English with the exact remedy, and
    carries `cause: auth` for the rail and the sweep.
  * Manager.auth_sweep resumes those panes after the NEXT sign-in (the
    expiry moved), never against the same dead credential, and notifies on
    the edge exactly once.

Collected by test_corral_light.py (`from test_claude_auth import *`).
"""
import os
import sys
import time
import unittest
from pathlib import Path

import tempfile
os.environ.setdefault("CORRAL_LIGHT_STATE",
                      tempfile.mkdtemp(prefix="corral-light-test-"))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import claude_auth                                          # noqa: E402
from test_resilience import FakeLaneCase, wait_for           # noqa: E402

H = 3600.0
LIVE_REASON = ("session/prompt: {'code': -32000, "
               "'message': 'Authentication required'}")
NOW = 1_800_000_000.0


def _exp(refresh_in_h, access_in_h=1.0):
    return {"accessExpiresAt": NOW + access_in_h * H,
            "refreshExpiresAt": NOW + refresh_in_h * H, "source": "test"}


class TheCredentialRead(unittest.TestCase):

    def test_only_timestamps_leave_the_parse(self):
        raw = ('{"claudeAiOauth": {"accessToken": "sk-ant-SECRET", '
               '"refreshToken": "sk-ant-REFRESH", "expiresAt": 1700000000000, '
               '"refreshTokenExpiresAt": 1702000000000, "scopes": ["x"]}}')
        ts = claude_auth._timestamps(raw)
        self.assertEqual(set(ts), {"accessExpiresAt", "refreshExpiresAt"})
        self.assertEqual(ts["refreshExpiresAt"], 1702000000.0)
        self.assertNotIn("SECRET", repr(ts))

    def test_garbage_and_missing_keys_read_as_unknown(self):
        self.assertIsNone(claude_auth._timestamps("not json"))
        self.assertIsNone(claude_auth._timestamps('{"other": 1}'))
        ts = claude_auth._timestamps('{"claudeAiOauth": {"accessToken": "x"}}')
        self.assertEqual(ts, {"accessExpiresAt": None, "refreshExpiresAt": None})

    def test_darwin_reads_the_keychain_not_the_stale_file(self):
        calls = []
        real_kc, real_file = claude_auth._read_keychain, claude_auth._read_file
        claude_auth._read_keychain = lambda: (calls.append("kc") or None)
        claude_auth._read_file = lambda: (calls.append("file") or None)
        try:
            self.assertIsNone(claude_auth.expiry(platform="darwin"))
            self.assertEqual(calls, ["kc"], "the May-dated leftover file must not answer for the Keychain")
            calls.clear()
            self.assertIsNone(claude_auth.expiry(platform="linux"))
            self.assertEqual(calls, ["file"])
        finally:
            claude_auth._read_keychain, claude_auth._read_file = real_kc, real_file


class TheVerdict(unittest.TestCase):

    def test_expired_refresh_is_not_ok_and_names_the_command(self):
        s = claude_auth.status(now=NOW, _expiry=_exp(-1))
        self.assertIs(s["ok"], False)
        self.assertIn("claude auth login", s["why"])
        self.assertLess(s["hoursLeft"], 0)

    def test_inside_the_warning_window_is_ok_but_says_so(self):
        s = claude_auth.status(now=NOW, _expiry=_exp(30))
        self.assertIs(s["ok"], True)
        self.assertTrue(s["warn"])
        self.assertIn("expires in 1 day", s["why"])
        self.assertIn("claude auth login", s["why"])

    def test_far_out_is_quiet(self):
        s = claude_auth.status(now=NOW, _expiry=_exp(24 * 20))
        self.assertIs(s["ok"], True)
        self.assertFalse(s["warn"])
        self.assertEqual(s["why"], "")

    def test_no_data_is_unknown_never_green(self):
        s = claude_auth.status(now=NOW, _expiry={})
        self.assertIsNone(s["ok"])
        self.assertIn("could not read", s["why"])
        s = claude_auth.status(now=NOW, _expiry={"accessExpiresAt": NOW + H,
                                                 "refreshExpiresAt": None,
                                                 "source": "test"})
        self.assertIsNone(s["ok"])
        self.assertIn("no refresh expiry", s["why"])

    def test_the_live_error_is_classified_and_explained(self):
        self.assertTrue(claude_auth.is_auth_error(LIVE_REASON))
        self.assertTrue(claude_auth.is_auth_error(
            "Failed to authenticate: OAuth session expired and could not be refreshed"))
        self.assertFalse(claude_auth.is_auth_error("agent exited rc=3"))
        self.assertFalse(claude_auth.is_auth_error(None))
        why = claude_auth.explain(LIVE_REASON)
        self.assertIn("claude auth login", why)
        self.assertIn("-32000", why, "the vendor's words stay, in brackets")
        self.assertEqual(claude_auth.explain("agent exited rc=3"), "agent exited rc=3")

    def test_the_cli_exit_codes_carry_the_verdict(self):
        real = claude_auth.status
        for exp, code in ((_exp(-1), 1), (_exp(30), 3), (_exp(500), 0), ({}, 2)):
            claude_auth.status = lambda force=False, _e=exp: real(now=NOW, _expiry=_e)
            try:
                import io, contextlib
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(claude_auth.main([]), code)
            finally:
                claude_auth.status = real


class ThePickerAsksTheCredential(unittest.TestCase):

    def setUp(self):
        import sessions, lane_probe
        self.sessions, self.lane_probe = sessions, lane_probe
        self._probe, self._status = lane_probe.probe, claude_auth.status
        lane_probe.probe = lambda key, cwd=None, force=False: {
            "ok": True, "config": {}, "error": ""}
        self.addCleanup(setattr, lane_probe, "probe", self._probe)
        self.addCleanup(setattr, claude_auth, "status", self._status)
        self.addCleanup(self.sessions.AGENTS["claude"].__setitem__, "auth_status",
                        self.sessions.AGENTS["claude"]["auth_status"])

    def _claude(self, st):
        self.sessions.AGENTS["claude"]["auth_status"] = lambda: st
        return next(a for a in self.sessions.available_agents() if a["key"] == "claude")

    @unittest.skipUnless(Path(__file__).with_name("spike").joinpath(
        "node_modules", ".bin", "claude-agent-acp").exists(), "adapter not installed")
    def test_a_lapsed_login_refuses_the_lane_with_the_remedy(self):
        a = self._claude(claude_auth.status(now=NOW, _expiry=_exp(-1)))
        self.assertFalse(a["available"])
        self.assertIn("claude auth login", a["why"])

    @unittest.skipUnless(Path(__file__).with_name("spike").joinpath(
        "node_modules", ".bin", "claude-agent-acp").exists(), "adapter not installed")
    def test_an_expiring_login_is_offered_with_a_warning(self):
        a = self._claude(claude_auth.status(now=NOW, _expiry=_exp(20)))
        self.assertTrue(a["available"])
        self.assertIn("expires in 20h", a["why"])

    @unittest.skipUnless(Path(__file__).with_name("spike").joinpath(
        "node_modules", ".bin", "claude-agent-acp").exists(), "adapter not installed")
    def test_a_good_login_changes_nothing(self):
        a = self._claude(claude_auth.status(now=NOW, _expiry=_exp(500)))
        self.assertTrue(a["available"])
        self.assertNotIn("expires", a["why"])


class ADeadLoginIsSurvived(FakeLaneCase):
    """The pane dies in English, and comes back after the next sign-in."""

    def setUp(self):
        super().setUp()
        self._status = claude_auth.status
        self.addCleanup(setattr, claude_auth, "status", self._status)
        self.said = []

    def _login(self, ok, ref, warn=False, why=""):
        st = {"ok": ok, "warn": warn, "why": why, "refreshExpiresAt": ref,
              "accessExpiresAt": None, "hoursLeft": None, "source": "test",
              "checkedAt": "now"}
        claude_auth.status = lambda now=None, force=False, **_: st

    def _auth_dead_pane(self):
        self.sessions.AGENTS["fake"]["env"]["FAKE_ACP_AUTH_FAIL"] = "1"
        self._login(False, "2026-09-01T00:00:00Z")
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("hello")
        self.assertTrue(wait_for(lambda: p.state == "dead"), p.state)
        return p

    def test_it_dies_with_the_remedy_and_a_cause(self):
        p = self._auth_dead_pane()
        self.assertEqual(p.dead_cause, "auth")
        self.assertIn("claude auth login", p.error)
        self.assertIn("-32000", p.error)
        dead = next(e for e in p.events if e["kind"] == "dead")
        self.assertEqual(dead["data"]["cause"], "auth")
        self.assertIn("claude auth login", dead["data"]["reason"])
        snap = p.snapshot()
        self.assertEqual(snap["deadCause"], "auth")
        self.assertTrue(snap["resumable"])

    def test_a_plain_crash_is_not_an_auth_death(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("die")
        self.assertTrue(wait_for(lambda: p.state == "dead"))
        self.assertIsNone(p.dead_cause)
        self.assertNotIn("claude auth login", p.error or "")

    def test_the_sweep_waits_for_a_new_login_then_resumes(self):
        p = self._auth_dead_pane()
        # Same dead credential: nothing to do, and NOT a resume loop.
        acted = self.mgr.auth_sweep(notify_fn=lambda t, b: self.said.append(b))
        self.assertEqual(acted["resumed"], [])
        self.assertEqual(p.state, "dead")
        # Still the old expiry but now "ok" — e.g. a clock skew or an early
        # revoke: the pane died UNDER this login, so it is not resumed.
        self._login(True, "2026-09-01T00:00:00Z")
        acted = self.mgr.auth_sweep(notify_fn=lambda t, b: self.said.append(b))
        self.assertEqual(acted["resumed"], [])
        # The operator signs in: the refresh expiry moves. The fake stops failing.
        self.sessions.AGENTS["fake"]["env"].pop("FAKE_ACP_AUTH_FAIL")
        self._login(True, "2026-10-27T00:00:00Z")
        acted = self.mgr.auth_sweep(notify_fn=lambda t, b: self.said.append(b))
        self.assertEqual(acted["resumed"], [p.id], acted)
        self.assertEqual(p.state, "ready", p.error)
        self.assertIsNone(p.dead_cause)
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("login is back" in n for n in notes), notes)
        # It works again, and the lost prompt was NOT re-sent for him.
        self.assertNotIn("echo: hello", self.texts(p))
        p.send("hello again")
        self.assertTrue(wait_for(lambda: "echo: hello again" in self.texts(p)))
        # And the sweep is idle now.
        acted = self.mgr.auth_sweep(notify_fn=lambda t, b: self.said.append(b))
        self.assertEqual(acted, {"resumed": [], "failed": [], "notified": None})

    def test_a_pane_that_dies_again_is_not_resumed_in_a_loop(self):
        p = self._auth_dead_pane()
        # A new login arrives but the lane STILL refuses (say, a revoked
        # account): the resumed pane dies again under the NEW expiry and the
        # next sweep leaves it alone.
        self._login(True, "2026-10-27T00:00:00Z")
        acted = self.mgr.auth_sweep(notify_fn=lambda t, b: None)
        self.assertEqual(acted["resumed"], [p.id])
        p.send("hello")
        self.assertTrue(wait_for(lambda: p.state == "dead"))
        self.assertEqual(p.dead_login, "2026-10-27T00:00:00Z")
        for _ in range(3):
            acted = self.mgr.auth_sweep(notify_fn=lambda t, b: None)
            self.assertEqual(acted["resumed"], [], "resumed against the same login")
        self.assertEqual(self.kinds(p).count("resumed"), 1)

    def test_notified_once_per_edge_and_silent_when_fine(self):
        say = lambda t, b: self.said.append(b)              # noqa: E731
        self._login(True, "2026-10-27T00:00:00Z")
        self.mgr.auth_sweep(notify_fn=say)
        self.assertEqual(self.said, [], "a fine login says nothing")
        self._login(True, "2026-10-27T00:00:00Z", warn=True,
                    why="Claude login expires in 30h — run `claude auth login`")
        self.mgr.auth_sweep(notify_fn=say)
        self.mgr.auth_sweep(notify_fn=say)
        self.assertEqual(len(self.said), 1, self.said)
        self._login(False, "2026-10-27T00:00:00Z", why=claude_auth.remedy())
        self.mgr.auth_sweep(notify_fn=say)
        self.mgr.auth_sweep(notify_fn=say)
        self.assertEqual(len(self.said), 2, self.said)
        self.assertIn("claude auth login", self.said[-1])
        self._login(True, "2026-11-30T00:00:00Z")
        self.mgr.auth_sweep(notify_fn=say)
        self.assertEqual(len(self.said), 2, "signing in is not news to page about")

    def test_a_sweep_survives_a_credential_read_that_raises(self):
        def boom(**_):
            raise RuntimeError("keychain locked")
        claude_auth.status = boom
        self.assertEqual(self.mgr.auth_sweep(), {"resumed": [], "failed": [], "notified": None})

    def test_state_carries_the_login_and_the_ui_reads_it(self):
        src = (ROOT / "sessions.py").read_text(encoding="utf-8")
        self.assertIn('"claudeAuth": claude_auth.status()', src)
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("S.claudeAuth = d.claudeAuth", js)
        self.assertIn("'Claude login expired'", js)
        self.assertIn("'Claude login expiring'", js)
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn("MGR.auth_sweep()", hub)


if __name__ == "__main__":
    unittest.main()
