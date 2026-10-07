"""The reviewer sandbox's pure parts: egress policy, sign-in lifetimes, and
the proxy itself over a real unix socket (docs/ux-10x-plan.md §2.5)."""
import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import review_egress as eg
import review_sandbox as rs


class EgressPolicy(unittest.TestCase):

    def test_a_lane_reaches_its_vendor_and_nothing_else(self):
        self.assertTrue(eg.host_allowed("api.anthropic.com", "claude"))
        self.assertTrue(eg.host_allowed("chatgpt.com", "codex"))
        self.assertTrue(eg.host_allowed("cli-chat-proxy.grok.com", "grok"))
        self.assertTrue(eg.host_allowed("cloudcode-pa.googleapis.com", "gemini"))
        self.assertFalse(eg.host_allowed("chatgpt.com", "claude"))     # another vendor
        self.assertFalse(eg.host_allowed("example.com", "codex"))
        self.assertFalse(eg.host_allowed("evilanthropic.com", "claude"))   # suffix, not substring
        self.assertFalse(eg.host_allowed("api.mixpanel.com", "grok"))
        self.assertFalse(eg.host_allowed("anything", "fake"))          # unknown lane: nothing

    def test_sign_in_hosts_of_rotating_vendors_are_never_reachable(self):
        for lane, host in (("claude", "platform.claude.com"), ("claude", "console.anthropic.com"),
                           ("codex", "auth.openai.com"), ("grok", "auth.x.ai")):
            self.assertFalse(eg.host_allowed(host, lane), host)
        self.assertTrue(eg.host_allowed("oauth2.googleapis.com", "gemini"))   # Google does not rotate

    def test_only_public_addresses(self):
        for host in ("127.0.0.1", "localhost", "10.0.0.1", "169.254.169.254", "::1"):
            self.assertIsNone(eg.public_addresses(host, 443), host)


class SignInLifetime(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="corral-home-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        env = mock.patch.dict(os.environ, {"CORRAL_CODEX_HOME": str(self.home / "codex"),
                                           "CORRAL_GROK_HOME": str(self.home / ".grok")})
        env.start()
        self.addCleanup(env.stop)

    def put(self, rel, doc):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc))

    def test_each_lane_reads_its_own_expiry(self):
        now = 1_800_000_000
        self.put(".claude/.credentials.json",
                 {"claudeAiOauth": {"accessToken": "a", "expiresAt": (now + 600) * 1000}})
        payload = base64.urlsafe_b64encode(json.dumps({"exp": now + 7200}).encode()).decode().rstrip("=")
        self.put("codex/auth.json", {"tokens": {"access_token": f"h.{payload}.s"}})
        self.put(".grok/auth.json", {"x": {"key": "k", "expires_at": "2027-01-15T09:00:00Z"}})
        self.assertEqual(rs.login_seconds_left("claude", self.home, now), 600)
        self.assertEqual(rs.login_seconds_left("codex", self.home, now), 7200)
        self.assertEqual(rs.login_seconds_left("grok", self.home, now), 3600)
        self.assertIsNone(rs.login_seconds_left("gemini", self.home, now))   # renews safely

    def test_a_grok_expiry_with_any_fraction_or_offset_reads(self):
        # Ship-gate panel: a 7- or 8-digit fraction used to be cut into a
        # broken offset, parse as nothing, and let a lapsing sign-in through.
        now = 1_800_000_000                                  # 2027-01-15T08:00:00Z
        for stamp in ("2027-01-15T09:00:00Z", "2027-01-15T09:00:00.1234567Z",
                      "2027-01-15T09:00:00.12345678Z", "2027-01-15T09:00:00.123456789Z",
                      "2027-01-15T10:00:00.5+01:00", "2027-01-15T09:00:00"):
            self.put(".grok/auth.json", {"x": {"key": "k", "expires_at": stamp}})
            left = rs.login_seconds_left("grok", self.home, now)
            self.assertIsNotNone(left, stamp)
            self.assertAlmostEqual(left, 3600, delta=1, msg=stamp)

    def test_an_unreadable_grok_expiry_fails_closed(self):
        self.put(".grok/auth.json", {"x": {"key": "k", "expires_at": "next tuesday"}})
        self.assertEqual(rs.login_seconds_left("grok", self.home, 1_800_000_000), 0.0)
        self.put(".grok/auth.json", {"x": {"key": "k"}})      # says nothing: as before
        self.assertIsNone(rs.login_seconds_left("grok", self.home, 1_800_000_000))

    def test_signed_out_or_unreadable_is_zero(self):
        self.put(".claude/.credentials.json",
                 {"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}})
        self.assertEqual(rs.login_seconds_left("claude", self.home), 0.0)
        (self.home / ".claude/.credentials.json").write_text("{not json")
        self.assertEqual(rs.login_seconds_left("claude", self.home), 0.0)


class TheGate(unittest.TestCase):

    def test_a_lapsing_or_absent_sign_in_refuses_with_the_way_out(self):
        import sessions
        with mock.patch.object(sessions._sbx, "login_seconds_left", lambda lane: 600):
            why = sessions.review_login_refusal("claude", "Claude Code", 2700)
        self.assertIn("lapses in 10 minutes", why)
        self.assertIn("Use Claude Code in any pane", why)
        with mock.patch.object(sessions._sbx, "login_seconds_left", lambda lane: 0.0):
            self.assertIn("is signed out", sessions.review_login_refusal("claude", "Claude", 2700))
        with mock.patch.object(sessions._sbx, "login_seconds_left", lambda lane: 9000):
            self.assertIsNone(sessions.review_login_refusal("claude", "Claude", 2700))
        with mock.patch.object(sessions._sbx, "login_seconds_left", lambda lane: None):
            self.assertIsNone(sessions.review_login_refusal("gemini", "Gemini", 2700))


class TheArgv(unittest.TestCase):
    """wrap()'s shape, checked without running bubblewrap (CI hosts may lack it)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="corral-home-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        for rel in (".claude/.credentials.json", ".grok/auth.json", ".netrc"):
            (self.home / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.home / rel).write_text("{}")
        (self.home / ".ssh").mkdir()
        self.state = self.home / "state"
        self.rw = self.state / "panes" / "p1" / "egress"
        self.rw.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"CORRAL_GROK_HOME": str(self.home / ".grok")})
        env.start()
        self.addCleanup(env.stop)

    def wrap(self, **kw):
        return rs.wrap(["lane"], {"CORRAL_POSTURE": "strict", "SSH_AUTH_SOCK": "/x"},
                       lane="grok", cwd="/t", state=self.state, ro_dirs=[self.rw],
                       scratch_dirs=[self.state / "panes" / "p1" / "config"],
                       tree_dir="/t", home=self.home,
                       base_env={"PATH": "/bin", "GH_TOKEN": "x", "LC_ALL": "C"}, **kw)

    def test_with_egress_there_is_no_shared_network(self):
        argv, _ = self.wrap(egress=str(self.rw / "s.sock"))
        self.assertNotIn("--share-net", argv)
        self.assertIn("review_egress.py", " ".join(argv))
        self.assertEqual(argv[-1], "lane")
        self.assertIn("--share-net", self.wrap()[0])     # without, the probe's shape

    def test_the_environment_is_an_allowlist(self):
        argv, env = self.wrap()
        self.assertIn("--clearenv", argv)
        self.assertEqual(set(env), {"PATH", "LC_ALL", "CORRAL_POSTURE", "XDG_RUNTIME_DIR",
                                    "CORRAL_REVIEW_SANDBOX"})
        self.assertNotIn("GH_TOKEN", argv)
        self.assertNotIn("SSH_AUTH_SOCK", argv)

    def test_state_is_hidden_before_anything_is_bound_back(self):
        argv, _ = self.wrap()
        hide = argv.index(str(self.state), argv.index("--tmpfs", argv.index("--tmp-overlay")))
        back = argv.index(str(self.rw))
        self.assertLess(hide, back)
        self.assertNotIn(str(self.state / "panes" / "p1"), argv)   # never the pane dir itself
        s = " ".join(argv)
        self.assertIn(f"--ro-bind {self.rw} {self.rw}", s)       # the socket dir: read-only
        cfg = self.state / "panes" / "p1" / "config"
        self.assertIn(f"--overlay-src {cfg} --tmp-overlay {cfg}", s)
        self.assertNotIn(" --bind ", s)                            # nothing persists

    def test_secrets_and_other_lanes_logins_are_hidden_own_login_is_not_writable(self):
        argv, _ = self.wrap()
        s = " ".join(argv)
        self.assertIn(f"--tmpfs {self.home / '.ssh'}", s)
        self.assertIn(f"--ro-bind /dev/null {self.home / '.netrc'}", s)
        self.assertIn(f"--ro-bind /dev/null {self.home / '.claude/.credentials.json'}", s)
        self.assertNotIn(str(self.home / ".grok/auth.json"), s)   # read via the overlay only
        self.assertNotIn("--bind " + str(self.home / ".grok"), s)


class Availability(unittest.TestCase):

    def setUp(self):
        old = (rs._AVAILABLE, rs._FAILED_AT)
        self.addCleanup(lambda: setattr(rs, "_AVAILABLE", old[0]) or setattr(rs, "_FAILED_AT", old[1]))

    def test_a_failure_is_tried_twice_and_kept_briefly_a_pass_for_good(self):
        calls = []
        with mock.patch.object(rs, "_probe", lambda: calls.append(1) or (False, "busy")):
            self.assertEqual(rs.available(refresh=True), (False, "busy"))
            self.assertEqual(len(calls), 2)
            rs.available()
            self.assertEqual(len(calls), 2)               # within the TTL
            with mock.patch.object(rs, "FAIL_TTL_S", 0):
                rs.available()
            self.assertEqual(len(calls), 4)               # after it, tried again
        with mock.patch.object(rs, "_probe", lambda: calls.append(1) or (True, "")):
            self.assertTrue(rs.available(refresh=True)[0])
        n = len(calls)
        with mock.patch.object(rs, "_probe", lambda: calls.append(1) or (False, "x")), \
                mock.patch.object(rs, "FAIL_TTL_S", 0):
            self.assertTrue(rs.available()[0])            # a pass is kept
        self.assertEqual(len(calls), n)


class TheProxy(unittest.TestCase):
    """The Egress proxy over its unix socket: refusals answer 403 and are
    recorded; nothing but CONNECT to an allowed host on 443 gets through."""

    def setUp(self):
        d = tempfile.mkdtemp(prefix="corral-egress-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.seen = []
        self.proxy = eg.Egress(os.path.join(d, "e.sock"), "claude",
                               lambda h, ok: self.seen.append((h, ok)))
        self.addCleanup(self.proxy.close)

    def ask(self, line):
        c = socket.socket(socket.AF_UNIX)
        c.settimeout(10)
        c.connect(self.proxy.path)
        c.sendall(line.encode() + b"\r\n\r\n")
        out = c.recv(200)
        c.close()
        return out.split(b"\r\n", 1)[0].decode()

    def test_refusals(self):
        self.assertIn("403", self.ask("GET http://api.anthropic.com/ HTTP/1.1"))
        self.assertIn("403", self.ask("CONNECT api.anthropic.com:80 HTTP/1.1"))
        self.assertIn("403", self.ask("CONNECT example.com:443 HTTP/1.1"))
        self.assertIn("403", self.ask("CONNECT platform.claude.com:443 HTTP/1.1"))
        self.assertIn("403", self.ask("CONNECT localhost:443 HTTP/1.1"))
        self.assertTrue(wait(lambda: ("example.com", False) in self.seen
                             and ("platform.claude.com", False) in self.seen))
        self.assertFalse(any(ok for _h, ok in self.seen))

    def test_an_allowed_name_that_resolves_privately_is_refused(self):
        with mock.patch.object(eg, "public_addresses", lambda host, port: None):
            self.assertIn("403", self.ask("CONNECT api.anthropic.com:443 HTTP/1.1"))

    def test_a_flood_is_capped_per_reviewer(self):
        held = []
        for _ in range(eg.MAX_CONNECTIONS):
            c = socket.socket(socket.AF_UNIX)
            c.connect(self.proxy.path)
            held.append(c)                      # each holds a slot, sending nothing
        self.addCleanup(lambda: [c.close() for c in held])
        time.sleep(0.2)
        extra = socket.socket(socket.AF_UNIX)
        extra.settimeout(5)
        extra.connect(self.proxy.path)
        self.assertEqual(extra.recv(10), b"")    # closed at once
        extra.close()
        for c in held:
            c.close()
        self.assertTrue(wait(lambda: self.ask("CONNECT example.com:443 HTTP/1.1").startswith("HTTP")))

    def test_closing_removes_the_socket(self):
        self.proxy.close()
        self.assertFalse(os.path.exists(self.proxy.path))


def wait(pred, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


if __name__ == "__main__":
    unittest.main()
