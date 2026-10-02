#!/usr/bin/env python3
"""Sign in from Corral: the login launcher, its hub route, and its judgement
of when the login actually succeeded.

    python3 test_claude_login.py
"""
import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CORRAL_LIGHT_STATE", tempfile.mkdtemp(prefix="light-login-"))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claude_login                                               # noqa: E402

FAKE_CLAUDE = "/opt/fake/bin/claude"
OPEN = ["/usr/bin/open"]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def fake_login(tmp, **kw):
    """A Login whose every collaborator is a recorder or a dial."""
    rec = types.SimpleNamespace(spawned=[], alive=True, ref=100.0, logged=True,
                                success=0, clock=Clock())
    rec.login = claude_login.Login(
        tmp, on_success=lambda: setattr(rec, "success", rec.success + 1),
        claude=FAKE_CLAUDE, find_terminal=kw.pop("find_terminal", lambda: OPEN),
        refresh_expiry=lambda: rec.ref, status=lambda c: rec.logged,
        is_alive=lambda s: rec.alive, clock=rec.clock,
        spawn=lambda argv: rec.spawned.append(argv), **kw)
    # The watch thread is driven by poll() in these tests, never by time.
    rec.login.watch = lambda: None
    return rec


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="login-")
        self.r = fake_login(self.tmp)
        self.L = self.r.login

    def finish(self, code):
        self.L.exit_file.write_text(f"{code}\n", encoding="utf-8")
        return self.L.poll()

    # The launcher ----------------------------------------------------------
    def test_a_local_start_launches_once_with_the_fixed_argv(self):
        r = self.L.start("browser (rail)", local=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "running")
        self.assertEqual(self.r.spawned, [OPEN + [str(self.L.script)]])
        body = self.L.script.read_text(encoding="utf-8")
        self.assertIn(f"{FAKE_CLAUDE} auth login\n", body)
        self.assertNotIn("exec ", body)          # the exit code must survive
        self.assertIn("browser (rail)", body)
        self.assertEqual(stat.S_IMODE(os.stat(self.L.script).st_mode), 0o700)
        led = [json.loads(x) for x in self.L.ledger.read_text().splitlines()]
        self.assertEqual((led[-1]["event"], led[-1]["requester"]), ("running", "browser (rail)"))

    def test_a_remote_start_is_refused_with_the_command_and_launches_nothing(self):
        r = self.L.start("browser (rail)", local=False)
        self.assertEqual((r["ok"], r["state"], r["command"]),
                         (False, "refused", "claude auth login"))
        self.assertIn("claude auth login", r["why"])
        self.assertEqual(self.r.spawned, [])
        self.assertFalse(self.L.script.exists())
        self.assertEqual(self.L.snapshot()["state"], "idle")

    def test_requester_bytes_cannot_reach_the_shell(self):
        self.L.start("x'; touch /tmp/pwn; echo $(id) `id` \"\n", local=True)
        body = self.L.script.read_text(encoding="utf-8")
        for bad in ("'; touch", "$(", "`", "\n\""):
            self.assertNotIn(bad, body.split("auth login")[0].split("echo ", 1)[1])
        out = subprocess.run(["/bin/sh", "-n", str(self.L.script)], capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_no_overlap_then_a_closed_window_plus_cooldown_allows_a_new_start(self):
        self.assertTrue(self.L.start("a", local=True)["ok"])
        again = self.L.start("b", local=True)
        self.assertEqual((again["ok"], again["state"]), (False, "running"))
        self.assertEqual(len(self.r.spawned), 1)
        # Inside the startup grace a missing process is `open` still working.
        self.r.alive = False
        self.r.clock.t += claude_login.STARTUP_GRACE_S - 1
        self.assertEqual(self.L.poll(), "running")
        self.r.clock.t += 2
        self.assertEqual(self.L.poll(), "closed")
        soon = self.L.start("c", local=True)
        self.assertEqual((soon["ok"], soon["state"]), (False, "cooldown"))
        self.assertEqual(len(self.r.spawned), 1)
        self.r.clock.t += claude_login.LOGIN_COOLDOWN_S
        self.assertTrue(self.L.start("d", local=True)["ok"])
        self.assertEqual(len(self.r.spawned), 2)

    def test_headless_is_refused_with_the_command_and_launches_nothing(self):
        r = fake_login(self.tmp, find_terminal=lambda: None)
        out = r.login.start("a", local=True)
        self.assertEqual((out["ok"], out["state"]), (False, "refused"))
        self.assertIn("claude auth login", out["why"])
        self.assertEqual(r.spawned, [])

    def test_terminal_needs_a_display_off_macos(self):
        self.assertEqual(claude_login.terminal("darwin", {}), ["/usr/bin/open"])
        self.assertIsNone(claude_login.terminal("linux", {}))
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(claude_login.terminal("linux", {"DISPLAY": ":0"}))
        with mock.patch("shutil.which", return_value="/usr/bin/x-terminal-emulator"):
            self.assertEqual(claude_login.terminal("linux", {"DISPLAY": ":0"}),
                             ["/usr/bin/x-terminal-emulator", "-e"])

    def test_success_needs_exit_zero_an_advance_and_logged_in(self):
        self.L.start("a", local=True)
        self.r.ref = 200.0
        self.assertEqual(self.finish(0), "signed-in")
        self.assertEqual(self.r.success, 1)

    def test_a_rotation_without_a_launch_is_not_a_sign_in(self):
        self.r.ref = 999.0
        self.assertEqual(self.L.poll(), "idle")
        self.assertEqual(self.r.success, 0)

    def test_a_non_zero_exit_is_not_a_sign_in_even_if_the_credential_moved(self):
        self.L.start("a", local=True)
        self.r.ref = 200.0
        self.assertEqual(self.finish(1), "check-status")
        self.assertIn("exited 1", self.L.snapshot()["why"])
        self.assertEqual(self.r.success, 0)

    def test_exit_zero_without_an_advance_is_not_a_sign_in(self):
        self.L.start("a", local=True)
        self.assertEqual(self.finish(0), "check-status")
        self.assertEqual(self.r.success, 0)

    def test_exit_zero_and_an_advance_without_logged_in_is_not_a_sign_in(self):
        self.L.start("a", local=True)
        self.r.ref, self.r.logged = 200.0, False
        self.assertEqual(self.finish(0), "check-status")
        self.assertEqual(self.r.success, 0)

    def test_a_login_past_the_watch_gives_up_and_still_blocks_a_new_start(self):
        self.L.start("a", local=True)
        self.r.clock.t += claude_login.LOGIN_WATCH_S + 1
        self.assertEqual(self.L.poll(), "gave-up")
        again = self.L.start("b", local=True)
        self.assertEqual((again["ok"], again["state"]), (False, "running"))
        self.assertEqual(len(self.r.spawned), 1)
        self.r.alive = False                   # the operator closed the window
        self.assertTrue(self.L.start("c", local=True)["ok"])
        self.assertEqual(len(self.r.spawned), 2)


class RealLauncherPath(unittest.TestCase):
    """The real wrapper, spawn, exit file and pgrep; `/bin/sh` stands in for
    Terminal, so no display is needed.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="login-real-"))
        self.cred = self.tmp / "cred.json"
        self.cred.write_text(json.dumps({"refresh": 100}), encoding="utf-8")
        self.saved = (claude_login.LOGIN_POLL_S, claude_login.STARTUP_GRACE_S)
        claude_login.LOGIN_POLL_S, claude_login.STARTUP_GRACE_S = 0.05, 5

    def tearDown(self):
        claude_login.LOGIN_POLL_S, claude_login.STARTUP_GRACE_S = self.saved

    def fake_claude(self, code, advance=True, logged=True):
        f = self.tmp / "claude"
        new = '{"refresh": 200}' if advance else '{"refresh": 100}'
        f.write_text(
            "#!/bin/sh\n"
            "if [ \"$1 $2\" = \"auth status\" ]; then "
            f"echo '{{\"loggedIn\": {'true' if logged else 'false'}}}'; exit 0; fi\n"
            f"echo '{new}' > {self.cred}\n"
            f"exit {code}\n", encoding="utf-8")
        f.chmod(0o755)
        return str(f)

    def run_login(self, claude, on_success):
        L = claude_login.Login(
            self.tmp / "state", on_success=on_success, claude=claude,
            find_terminal=lambda: ["/bin/sh"],
            refresh_expiry=lambda: json.loads(self.cred.read_text())["refresh"])
        self.assertTrue(L.start("browser (rail)", local=True)["ok"])
        deadline = time.time() + 15
        while L.snapshot()["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        return L

    def test_T4_4_a_real_run_signs_in_and_the_hub_sweeps_once(self):
        import hub
        mgr = mock.Mock()
        with mock.patch.object(hub, "MGR", mgr), \
                mock.patch.object(hub.claude_auth, "status") as st:
            L = self.run_login(self.fake_claude(0), hub._login_signed_in)
        self.assertEqual(L.snapshot()["state"], "signed-in", L.snapshot())
        self.assertEqual(mgr.auth_sweep.call_count, 1)
        st.assert_called_once_with(force=True)    # the new login read now, not in 60 s
        self.assertEqual(L.exit_file.read_text().strip(), "0")

    def test_T4_5_a_real_non_zero_exit_never_sweeps(self):
        swept = []
        L = self.run_login(self.fake_claude(3), lambda: swept.append(1))
        self.assertEqual(L.snapshot()["state"], "check-status")
        self.assertEqual(swept, [])

    def test_T4_5_a_real_exit_zero_without_logged_in_never_sweeps(self):
        swept = []
        L = self.run_login(self.fake_claude(0, logged=False), lambda: swept.append(1))
        self.assertEqual(L.snapshot()["state"], "check-status")
        self.assertEqual(swept, [])

    def test_T4_7_the_spawn_holds_no_pipes(self):
        out = self.tmp / "stdin-was"
        p = claude_login.Login._spawn(["/bin/sh", "-c", f"cat > {out}; echo leaked"])
        self.assertIsNone(p.stdin)
        self.assertIsNone(p.stdout)
        self.assertIsNone(p.stderr)
        self.assertEqual(p.wait(timeout=5), 0)     # stdin is /dev/null: EOF at once
        self.assertEqual(out.read_text(), "")


class Route(unittest.TestCase):
    """The login route over a real socket, through hub.Handler."""

    @classmethod
    def setUpClass(cls):
        import auth
        import hub
        from http.server import ThreadingHTTPServer
        cls.hub = hub
        cls.cookie = f"{hub.COOKIE}={auth.mint()}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.r = fake_login(tempfile.mkdtemp(prefix="login-route-"))
        self.patches = [
            mock.patch.object(self.hub, "LOGIN", self.r.login),
            mock.patch.object(self.hub, "MGR", types.SimpleNamespace(
                panes={"p1": object()}, state=lambda since: {"panes": []}))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def post(self, body, cookie=True, **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json", **headers}
        if cookie:
            h["Cookie"] = self.cookie
        c.request("POST", "/api/claude/login", json.dumps(body), h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_no_cookie_is_401_and_launches_nothing(self):
        self.assertEqual(self.post({"from": "rail"}, cookie=False)[0], 401)
        self.assertEqual(self.r.spawned, [])

    def test_cross_origin_is_403_and_launches_nothing(self):
        st, _ = self.post({"from": "rail"}, Origin="http://evil.example",
                          Host=f"127.0.0.1:{self.port}")
        self.assertEqual(st, 403)
        self.assertEqual(self.r.spawned, [])

    def test_serve_and_forwarded_requests_are_refused_with_the_command(self):
        for h in ({"Tailscale-User-Login": "someone@example.com"},
                  {"X-Forwarded-For": "100.64.0.9"}, {"Forwarded": "for=10.0.0.2"}):
            st, body = self.post({"from": "rail"}, **h)
            self.assertEqual((st, body["state"]), (403, "refused"), h)
            self.assertIn("claude auth login", body["why"])
        self.assertEqual(self.r.spawned, [])

    def test_a_non_loopback_peer_is_refused(self):
        with mock.patch.object(self.hub.Handler, "_peer", lambda s: "192.168.1.9"):
            st, body = self.post({"from": "rail"})
        self.assertEqual((st, body["state"]), (403, "refused"))
        self.assertEqual(self.r.spawned, [])

    def test_loopback_and_paired_starts_once_and_state_carries_it(self):
        st, body = self.post({"from": "banner", "pane": "p1"})
        self.assertEqual((st, body["state"]), (200, "running"))
        self.assertEqual(self.r.spawned, [OPEN + [str(self.r.login.script)]])
        self.assertEqual(self.r.login.snapshot()["requester"], "pane p1 (banner)")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/state", headers={"Cookie": self.cookie})
        cl = json.loads(c.getresponse().read())["claudeLogin"]
        self.assertEqual((cl["state"], cl["requester"]), ("running", "pane p1 (banner)"))

    def test_request_words_are_not_the_requester(self):
        self.post({"from": "rail; rm -rf ~", "pane": "nope$(id)"})
        self.assertEqual(self.r.login.snapshot()["requester"], "browser")
        self.assertNotIn("rm -rf", self.r.login.script.read_text())


class Tick(unittest.TestCase):
    def test_T4_8_a_slow_status_never_slows_the_tick(self):
        import hub
        tmp = Path(tempfile.mkdtemp(prefix="login-tick-"))
        calls = []

        def slow_status(c):
            calls.append(time.time())
            time.sleep(3)
            return True
        L = claude_login.Login(tmp, claude=FAKE_CLAUDE, find_terminal=lambda: OPEN,
                               refresh_expiry=lambda: 100.0, status=slow_status,
                               is_alive=lambda s: True, spawn=lambda a: None)
        L.watch = lambda: None
        L.start("a", local=True)
        L.refresh_expiry = lambda: 200.0
        L.exit_file.write_text("0\n")
        threading.Thread(target=L.poll, daemon=True).start()
        deadline = time.time() + 2
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(calls, "the slow status never started")
        mgr = types.SimpleNamespace(panes={}, auth_sweep=lambda: None)
        with mock.patch.object(hub, "MGR", mgr), mock.patch.object(hub, "LOGIN", L):
            t0 = time.time()
            hub._observe_once()
            took = time.time() - t0
        self.assertLess(took, 0.5)


class FindsTheCli(unittest.TestCase):
    """A hub run as a service has launchd's PATH, which lacks ~/.local/bin,
    where the vendor's native installer puts `claude`.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="login-bin-"))
        self.home = self.tmp / "home"
        (self.home / ".local/bin").mkdir(parents=True)
        self.onpath = self.tmp / "path"
        self.onpath.mkdir()

    def exe(self, where, mode=0o755):
        f = where / "claude"
        f.write_text("#!/bin/sh\n", encoding="utf-8")
        f.chmod(mode)
        return str(f.resolve())

    def find(self, **env):
        env.setdefault("PATH", str(self.onpath))
        return claude_login.claude_bin(env=env, home=self.home)

    def test_service_path_falls_back_to_native_install_dir(self):
        want = self.exe(self.home / ".local/bin")
        self.assertEqual(self.find(PATH="/usr/bin:/bin"), want)

    def test_path_wins_over_the_fallback(self):
        self.exe(self.home / ".local/bin")
        want = self.exe(self.onpath)
        self.assertEqual(self.find(), want)

    def test_nothing_anywhere_is_none(self):
        self.assertIsNone(self.find())

    def test_not_executable_is_not_found(self):
        self.exe(self.home / ".local/bin", mode=0o644)
        self.assertIsNone(self.find())

    def test_a_bad_override_does_not_fall_back(self):
        self.exe(self.onpath)
        self.exe(self.home / ".local/bin")
        self.assertIsNone(self.find(CORRAL_CLAUDE_BIN=str(self.tmp / "nope")))

    def test_a_good_override_wins(self):
        self.exe(self.onpath)
        alt = self.tmp / "alt"
        alt.mkdir()
        want = self.exe(alt)
        self.assertEqual(self.find(CORRAL_CLAUDE_BIN=want), want)


class TheBrowserHalf(unittest.TestCase):
    def test_T4_9_and_the_four_doors(self):
        """Skipped LOUDLY when node is absent: a check that did not run must
        not read as a check that passed."""
        import shutil
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node absent: selftest_login.mjs did NOT run")
        r = subprocess.run([node, str(HERE / "selftest_login.mjs")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
