#!/usr/bin/python3
"""Drive end to end: two REAL hubs on private ports and state dirs, each with
the fake ACP lane. The bedroom drives the office: it opens a pane there, talks
to it, answers its card, and keeps working it across a restart of the office
hub. Driven through `corral-light` and `corral-light hubs` as subprocesses."""
import json
import os
import re
import time
import unittest

from test_hublink_e2e import HUB, FAKE, ROOT, free_port
from testkit.scratch import tmpdir
from pathlib import Path


class DriveE2E(unittest.TestCase):
    def setUp(self):
        from test_resilience import start_hub
        self.tmp = Path(tmpdir(self, "corral-light-drive-"))
        self.hubs = {}
        for name in ("office", "bedroom"):
            d = self.tmp / name
            (d / "agent").mkdir(parents=True)
            (d / "home" / "work").mkdir(parents=True)
            env = {**os.environ, "CORRAL_LIGHT_STATE": str(d / "state"),
                   "CORRAL_LIGHT_CONFIG_DIR": str(d / "config"),
                   "CORRAL_LIGHT_ROLES_DIR": str(d / "roles"),
                   "CORRAL_LIGHT_CONSULT_CFG": str(d / "cfg" / "s.json"),
                   # Each "machine" has its own home: a driven pane's folder
                   # must lie under the driven machine's.
                   "HOME": str(d / "home"),
                   "FAKE": str(FAKE), "FAKE_ACP_DIR": str(d / "agent")}
            proc, url = start_hub(d / "state", extra_env=env, script=HUB)
            env["CORRAL_LIGHT_URL"] = url
            self.hubs[name] = {"proc": proc, "env": env, "dir": d}
        self.addCleanup(self._stop)

    def _stop(self):
        from test_resilience import stop_hub
        for h in self.hubs.values():
            h["proc"].terminate()
            try:
                h["proc"].wait(10)
            except Exception:                                  # noqa: BLE001
                pass
            stop_hub(h["proc"])
            for meta in (h["dir"] / "state" / "panes").glob("*/meta.json"):
                try:
                    m = json.loads(meta.read_text())
                    if m.get("pgid"):
                        os.killpg(m["pgid"], 9)
                except (OSError, ValueError, ProcessLookupError):
                    pass

    def run_(self, hub, tool, *args, timeout=60, ok=True, stdin=None):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(ROOT / tool), *args],
                           env=self.hubs[hub]["env"], capture_output=True, text=True,
                           timeout=timeout, input=stdin)
        if ok:
            self.assertEqual(r.returncode, 0, f"{tool} {args}: {r.stdout}{r.stderr}")
        return r

    def hubs_(self, hub, *args, **kw):
        return self.run_(hub, "hubs_cli.py", *args, **kw)

    def cli(self, hub, *args, **kw):
        return self.run_(hub, "cli.py", *args, **kw)

    def panes(self, hub):
        return json.loads(self.cli(hub, "panes", "--json").stdout)

    def events(self, hub, pane):
        """The pane's events as that hub's wall has them (full state)."""
        code = ("import json, sys, consult; "
                "st = consult.connect(consult.DEFAULT_URL).get('/api/state?full=1'); "
                "p = next((p for p in st['panes'] if p['id'] == sys.argv[1]), None); "
                "print(json.dumps((p or {}).get('events') or []))")
        import subprocess
        import sys
        env = {**self.hubs[hub]["env"], "PYTHONPATH": str(ROOT)}
        r = subprocess.run([sys.executable, "-c", code, pane], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def wait_for(self, fn, timeout=20, every=0.2):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(every)
        return fn()

    def restart(self, name):
        from test_resilience import start_hub, stop_hub
        h = self.hubs[name]
        h["proc"].terminate()
        h["proc"].wait(10)
        stop_hub(h["proc"])
        env = {k: v for k, v in h["env"].items() if k != "CORRAL_LIGHT_URL"}
        # Same state dir and the same port: the paired hub dials what it knows.
        env["PORT"] = h["env"]["CORRAL_LIGHT_URL"].rsplit(":", 1)[1]
        proc, url = start_hub(h["dir"] / "state", extra_env=env, script=HUB)
        h["proc"] = proc

    def test_drive_a_pane_on_the_other_hub(self):
        office_home = self.hubs["office"]["dir"] / "home"
        po, pb = free_port(), free_port()
        self.hubs_("office", "enable", "--bind", "127.0.0.1", "--port", str(po),
                   "--name", "office")
        self.hubs_("bedroom", "enable", "--bind", "127.0.0.1", "--port", str(pb),
                   "--name", "bedroom")
        # The office lets the bedroom drive it; set at the office.
        out = self.hubs_("office", "invite", "--allow", "see,drive").stdout
        code = out.split("Pairing code:")[1].split()[0]
        self.hubs_("bedroom", "join", "127.0.0.1", code, "--port", str(po),
                   "--allow", "see")

        # Open a pane on the office, from the bedroom, in a folder under its home.
        t0 = time.time()
        pane = self.cli("bedroom", "open", "--host", "office", "--lane", "fake",
                        "--cwd", "~/work", timeout=60).stdout.strip()
        opened_s = time.time() - t0
        self.assertRegex(pane, r"^[0-9a-f]{12}$")

        # It runs there, marked as opened from the bedroom ...
        there = next(p for p in self.panes("office") if p["id"] == pane)
        self.assertEqual(there["drivenBy"]["name"], "bedroom")
        self.assertEqual(Path(there["cwd"]).resolve(), (office_home / "work").resolve())
        # ... and shows here, marked as on the office.
        here = self.wait_for(lambda: next((p for p in self.panes("bedroom")
                                           if p["id"] == pane), None))
        self.assertEqual(here["remote"]["hub"], "office")
        self.assertTrue(here["remote"]["up"])

        # Talk to it from the bedroom; the reply streams back here.
        t0 = time.time()
        out = self.cli("bedroom", "say", pane, "remember apple", timeout=60).stdout
        say_s = time.time() - t0
        self.assertIn("ok", out)
        out = self.cli("bedroom", "say", pane, "what word did I say", timeout=60).stdout
        self.assertIn("apple", out)

        # A permission card, answered from the bedroom.
        # Without a terminal `say` waits for the turn, which waits on the card:
        # start it in the background, answer, and it must then finish.
        import subprocess
        import sys
        sayer = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "say", pane, "perm"],
                                 env=self.hubs["bedroom"]["env"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: sayer.poll() is None and sayer.kill())
        cards = self.wait_for(lambda: re.search(r"digest: ([0-9a-f]{64})",
                                                self.cli("bedroom", "pending", pane).stdout))
        self.assertTrue(cards, "no card reached the bedroom")
        self.cli("bedroom", "ok", pane, "--digest", cards.group(1)[:16])
        out, _ = sayer.communicate(timeout=30)
        self.assertEqual(sayer.returncode, 0, out)
        reply = self.wait_for(lambda: "permission:" in json.dumps(
            self.events("bedroom", pane)))
        self.assertTrue(reply, "the agent never reported the answer")
        # Answered once, at the office, through the bedroom.
        self.assertEqual(self.cli("office", "pending", pane).stdout.count("digest:"), 0)

        # A folder outside the office's home is refused there.
        r = self.cli("bedroom", "open", "--host", "office", "--lane", "fake",
                     "--cwd", "/", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not under this machine's home", r.stdout + r.stderr)

        # The office hub restarts: the bedroom's mirror reconnects on its own,
        # and the conversation continues where it was.
        self.restart("office")
        back = self.wait_for(lambda: next((p for p in self.panes("bedroom")
                                           if p["id"] == pane and p["remote"]["up"]), None),
                             timeout=30)
        self.assertTrue(back, "the bedroom never reconnected to the office")
        out = self.cli("bedroom", "say", pane, "what word did I say", timeout=90).stdout
        self.assertIn("apple", out)

        # Revoked at the office: the bedroom can no longer open panes there.
        self.hubs_("office", "grant", "bedroom", "--allow", "see")
        r = self.cli("bedroom", "open", "--host", "office", "--lane", "fake",
                     "--cwd", "~/work", ok=False, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        print(f"\ndrive e2e: open {opened_s * 1000:.0f} ms, say round trip "
              f"{say_s * 1000:.0f} ms (both through the CLI and two hubs)", flush=True)


if __name__ == "__main__":
    unittest.main()
