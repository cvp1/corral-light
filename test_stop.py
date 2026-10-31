"""stop.py: `corral-light stop` closes every keeper (docs/DURABLE-RUNS.md F7).

Real keeper processes over the fake agent; the service manager is mocked.
"""
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import stop                                             # noqa: E402
from test_keeper import FAKE, tmpdir, wait_for          # noqa: E402


class StopCase(unittest.TestCase):
    def setUp(self):
        self.state = Path(tmpdir(self, "stop-state-"))
        self.procs = []
        self.addCleanup(self._down)

    def _down(self):
        for kp in self.procs:
            try:
                os.killpg(kp.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                kp.wait(timeout=3)
            except Exception:                         # noqa: BLE001
                pass

    def keeper(self, pane):
        """A real keeper for `pane`, recorded the way the hub records it."""
        d = self.state / "panes" / pane / "keeper"
        d.mkdir(parents=True, mode=0o700)
        import tempfile
        short = tempfile.mkdtemp(prefix="stp-", dir="/tmp")    # AF_UNIX paths are short
        self.addCleanup(__import__("shutil").rmtree, short, True)
        sock = str(Path(short) / "s.sock")
        env = {**os.environ, "FAKE_ACP_DIR": str(d)}
        kp = subprocess.Popen([sys.executable, "-I", str(ROOT / "keeper.py"), "--sock", sock,
                               "--dir", str(d), "--cwd", str(d), "--",
                               sys.executable, str(FAKE)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env, start_new_session=True)
        self.procs.append(kp)
        self.assertTrue(wait_for(lambda: os.path.exists(sock)), "no socket")
        self.assertTrue(wait_for(lambda: (d / "keeper.json").exists()))
        # The hub keeps keeper.json beside the socket; stop.py reads it there.
        (Path(short) / "keeper.json").write_text((d / "keeper.json").read_text())
        (d.parent / "meta.json").write_text(json.dumps(
            {"keeper": {"sock": sock, "dir": "", "pid": kp.pid}}), encoding="utf-8")
        return kp

    def run_stop(self, *argv, service=("systemd", True), hub_pid=None):
        said = []
        with mock.patch.object(stop.update, "STATE", self.state), \
                mock.patch.object(stop.update, "inside_hub", return_value=False), \
                mock.patch.object(stop.update, "service_manager", return_value=service), \
                mock.patch.object(stop.update, "hub_pid", return_value=hub_pid), \
                mock.patch.object(stop.subprocess, "run", wraps=subprocess.run) as run:
            rc = stop.run(stop.argparse.Namespace(keepers_only="--keepers-only" in argv),
                          said.append)
        return rc, said, run


class TheStopVerb(StopCase):

    def test_stop_ends_every_keeper_and_its_agent_within_10s(self):
        kps = [self.keeper("aaaaaaaaaaaa"), self.keeper("bbbbbbbbbbbb")]
        agents = [json.loads((self.state / "panes" / p / "keeper" / "keeper.json")
                             .read_text())["adapter_pid"]
                  for p in ("aaaaaaaaaaaa", "bbbbbbbbbbbb")]
        self.assertTrue(all(_alive(a) for a in agents))
        with mock.patch.object(stop, "stop_service", return_value=(True, "start it")):
            rc, said, _ = self.run_stop()
        self.assertEqual(rc, 0, said)
        t0 = time.monotonic()
        for kp in kps:
            self.assertTrue(wait_for(lambda: kp.poll() is not None, timeout=10),
                            "a keeper outlived the stop")
        self.assertLess(time.monotonic() - t0, 10)
        for pid in agents:
            self.assertTrue(wait_for(lambda: not _alive(pid), timeout=10),
                            "an agent outlived its keeper")
        self.assertIn("every keeper is gone", said)

    def test_the_service_stops_before_any_keeper_is_closed(self):
        self.keeper("cccccccccccc")
        order = []
        real_close = stop.close_keeper
        with mock.patch.object(stop, "stop_service",
                               side_effect=lambda: order.append("service") or (True, "x")), \
                mock.patch.object(stop, "close_keeper",
                                  side_effect=lambda s: order.append("close") or real_close(s)):
            self.run_stop()
        self.assertEqual(order, ["service", "close"])

    def test_systemd_stop_and_launchd_unload(self):
        with mock.patch.object(stop.update, "service_manager", return_value=("systemd", True)), \
                mock.patch.object(stop.subprocess, "run") as run:
            self.assertEqual(stop.stop_service()[0], True)
        self.assertEqual(run.call_args[0][0], ["systemctl", "--user", "stop", stop.update.UNIT])
        with mock.patch.object(stop.update, "service_manager", return_value=("launchd", True)), \
                mock.patch.object(stop.update, "launchd_label", return_value="x.label"), \
                mock.patch.object(stop.subprocess, "run") as run:
            ok, again = stop.stop_service()
        self.assertEqual(run.call_args[0][0], ["launchctl", "bootout", f"gui/{os.getuid()}/x.label"])
        self.assertIn("bootstrap", again)

    def test_refused_from_inside_a_pane(self):
        kp = self.keeper("dddddddddddd")
        with mock.patch.object(stop.update, "inside_hub", return_value=True), \
                mock.patch.object(stop, "stop_service") as svc:
            with self.assertRaises(stop.Refused):
                stop.run(stop.argparse.Namespace(keepers_only=False), lambda m: None)
        svc.assert_not_called()
        time.sleep(0.5)
        self.assertIsNone(kp.poll(), "a refused stop closed a keeper")

    def test_refused_while_a_hub_still_runs(self):
        kp = self.keeper("eeeeeeeeeeee")
        with mock.patch.object(stop, "HUB_EXIT_WAIT_S", 0.3), \
                mock.patch.object(stop, "hub_running", return_value=os.getpid()):
            with self.assertRaises(stop.Refused):
                self.run_stop("--keepers-only")
        time.sleep(0.5)
        self.assertIsNone(kp.poll(), "keepers were closed under a live hub")

    def test_a_reused_pid_is_never_signalled(self):
        """A record whose pid is no longer a keeper (here: this test) is skipped."""
        d = self.state / "panes" / "ffffffffffff"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps(
            {"keeper": {"sock": str(d / "nope.sock"), "dir": "", "pid": os.getpid()}}))
        self.assertEqual(stop.keepers(self.state), [])
        with mock.patch.object(stop.os, "killpg") as killpg:
            self.assertEqual(stop.close_all(stop.keepers(self.state), lambda m: None), [])
        killpg.assert_not_called()

    def test_a_keeper_that_does_not_answer_is_signalled(self):
        kp = self.keeper("gggggggggggg")
        found = stop.keepers(self.state)
        self.assertEqual([k["pid"] for k in found], [kp.pid])
        with mock.patch.object(stop, "close_keeper", return_value=None):
            left = stop.close_all(found, lambda m: None)
        self.assertEqual(left, [])
        self.assertIsNotNone(kp.poll())


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
