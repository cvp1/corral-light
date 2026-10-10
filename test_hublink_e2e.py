#!/usr/bin/python3
"""Hub links end to end: two REAL hubs on private ports and state dirs, each
with the fake ACP lane, driven by `corral-light hubs` and the CLI as
subprocesses. One hub plays the office machine, the other the bedroom."""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from testkit.scratch import tmpdir

ROOT = Path(__file__).resolve().parent
FAKE = ROOT / "testkit" / "fake_acp_agent.py"

HUB = r"""
import os, sys
sys.path.insert(0, os.environ["LIGHT_ROOT"])
import sessions
sessions.Manager.seed_catalogs = lambda self: None
for k in list(sessions.AGENTS):
    sessions.AGENTS[k]["unavailable"] = "not under test"
sessions.AGENTS["fake"] = {"label": "Fake", "argv": [sys.executable, os.environ["FAKE"]],
                           "posture_via_config_dir": False, "tools": True,
                           "env": {"FAKE_ACP_DIR": os.environ["FAKE_ACP_DIR"]}}
import hub
hub.HUBS.notify = lambda title, body: None          # no desktop banners from tests
hub.serve("127.0.0.1", int(os.environ["PORT"]))
"""


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TwoHubs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_resilience import start_hub
        cls.tmp = Path(tmpdir(cls, "corral-light-hubs-"))
        cls.hubs = {}
        for name in ("office", "bedroom"):
            d = cls.tmp / name
            (d / "agent").mkdir(parents=True)
            env = {**os.environ, "CORRAL_LIGHT_STATE": str(d / "state"),
                   "CORRAL_LIGHT_CONFIG_DIR": str(d / "config"),
                   "CORRAL_LIGHT_ROLES_DIR": str(d / "roles"),
                   "CORRAL_LIGHT_CONSULT_CFG": str(d / "cfg" / "s.json"),
                   "FAKE": str(FAKE), "FAKE_ACP_DIR": str(d / "agent")}
            proc, url = start_hub(d / "state", extra_env=env, script=HUB)
            env["CORRAL_LIGHT_URL"] = url
            cls.hubs[name] = {"proc": proc, "env": env, "dir": d}
        # one repository, cloned on each "machine"
        origin = cls.tmp / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", cwd=origin)
        git("config", "user.email", "t@t", cwd=origin)
        git("config", "user.name", "t", cwd=origin)
        (origin / "README").write_text("hello\n")
        git("add", ".", cwd=origin)
        git("commit", "-qm", "first", cwd=origin)
        for name in cls.hubs:
            git("clone", "-q", str(origin), str(cls.tmp / f"{name}-repo"), cwd=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        from test_resilience import stop_hub
        for h in cls.hubs.values():
            h["proc"].terminate()
            try:
                h["proc"].wait(10)
            except subprocess.TimeoutExpired:
                pass
            stop_hub(h["proc"])
            for meta in (h["dir"] / "state" / "panes").glob("*/meta.json"):
                try:
                    m = json.loads(meta.read_text())
                    if m.get("pgid"):
                        os.killpg(m["pgid"], 9)
                except (OSError, ValueError, ProcessLookupError):
                    pass

    def run_(self, hub, tool, *args, timeout=60, ok=True):
        r = subprocess.run([sys.executable, str(ROOT / tool), *args],
                           env=self.hubs[hub]["env"], capture_output=True,
                           text=True, timeout=timeout)
        if ok:
            self.assertEqual(r.returncode, 0, f"{tool} {args}: {r.stdout}{r.stderr}")
        return r

    def hubs_(self, hub, *args, **kw):
        return self.run_(hub, "hubs_cli.py", *args, **kw)

    def cli(self, hub, *args, **kw):
        return self.run_(hub, "cli.py", *args, **kw)

    def test_see_take_over_and_delegate(self):
        po, pb = free_port(), free_port()
        self.hubs_("office", "enable", "--bind", "127.0.0.1", "--port", str(po),
                   "--name", "office")
        self.hubs_("bedroom", "enable", "--bind", "127.0.0.1", "--port", str(pb),
                   "--name", "bedroom")
        out = self.hubs_("office", "invite", "--allow", "see,watch,takeover").stdout
        code = out.split("Pairing code:")[1].split()[0]
        out = self.hubs_("bedroom", "join", "127.0.0.1", code, "--port", str(po),
                         "--allow", "see,delegate").stdout
        self.assertIn("paired with office", out)
        self.assertIn("we may do there: see, watch, takeover", out)

        # Work on the office hub: a pane in its checkout leaves an uncommitted file.
        repo = self.tmp / "office-repo"
        pane = self.cli("office", "open", "--lane", "fake", "--cwd",
                        str(repo)).stdout.strip()
        self.cli("office", "say", pane, "write notes.txt the plan is ready", timeout=60)
        self.assertEqual((repo / "notes.txt").read_text().strip(), "the plan is ready")

        # From the bedroom: see it, and look inside it.
        out = self.hubs_("bedroom", "ls").stdout
        self.assertIn(pane, out)
        self.assertIn("office-repo", out)
        out = self.hubs_("bedroom", "show", "office", pane).stdout
        self.assertIn("write notes.txt", out)
        self.assertIn("notes.txt", out.split("untracked:")[1])

        # The office has not granted delegation to the bedroom yet.
        out = self.hubs_("bedroom", "offer", "office", "too early").stdout
        self.assertIn("refused", out)
        self.assertIn("may not delegate", out)

        # Take it over into the bedroom's own clone.
        out = self.hubs_("bedroom", "take", "office", pane, "--cwd",
                         str(self.tmp / "bedroom-repo"), timeout=120).stdout
        self.assertIn("took over", out)
        live = out.split("live pane here: ")[1].split()[0]
        work = Path(out.split("code: ")[1].split()[0])
        self.assertEqual((work / "notes.txt").read_text().strip(), "the plan is ready")
        self.assertEqual(git("status", "--porcelain", cwd=self.tmp / "bedroom-repo"), "")

        # The office pane is closed and cannot be reopened there.
        panes = self.cli("office", "panes", "--json").stdout
        self.assertNotIn(pane, panes)
        r = self.cli("office", "reopen", pane, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("handed to hub bedroom", r.stdout + r.stderr)

        # The live bedroom pane got the brief and continues on the same lane.
        st = json.loads(self.cli("bedroom", "panes", "--json").stdout)
        mine = next(p for p in st if p["id"] == live)
        self.assertEqual(mine["agent"], "fake")
        self.assertEqual(Path(mine["cwd"]).resolve(), work.resolve())

        # Delegation the other way: the bedroom offers, the office operator accepts.
        self.hubs_("office", "grant", "bedroom", "--allow", "see,watch,takeover,delegate")
        out = self.hubs_("bedroom", "offer", "office", "--lane", "fake", "--title",
                         "release notes", "--cwd-hint", str(repo),
                         "Draft the release notes").stdout
        self.assertIn("offered at office", out)
        oid = out.split("offer ")[1].split()[0]
        out = self.hubs_("office", "inbox").stdout
        self.assertIn("release notes", out)
        self.assertIn("Draft the release notes", out)
        out = self.hubs_("office", "accept", oid).stdout
        new = out.split("pane ")[1].split()[0]
        time.sleep(2)
        out = self.hubs_("bedroom", "offers", oid).stdout
        self.assertIn("accepted", out)
        self.assertIn(new, out)

        # Trust changes are refused from anywhere but the machine itself is
        # covered in hub.py; here: unpair, and the link is gone.
        self.hubs_("office", "forget", "bedroom")
        r = self.hubs_("bedroom", "ls", "office", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not paired with yours", r.stderr)

    def test_trust_changes_only_from_this_machine(self):
        """Pairing and grants refuse a request that came through a proxy
        (Tailscale Serve, a reverse proxy): the operator must be at the box."""
        import http.client
        env = self.hubs["office"]["env"]
        self.cli("office", "panes")                    # pairs the CLI, caches the cookie
        keys = ("CORRAL_LIGHT_STATE", "CORRAL_LIGHT_CONSULT_CFG", "CORRAL_LIGHT_URL")
        saved = {k: os.environ.get(k) for k in keys}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ.update({k: env[k] for k in keys})
        import importlib
        import consult
        importlib.reload(consult)
        hub = consult.connect(env["CORRAL_LIGHT_URL"])
        for route in ("/api/hubs/invite", "/api/hubs/grant", "/api/hubs/enable"):
            conn = http.client.HTTPConnection(hub.host, hub.port, timeout=10)
            h = hub._headers()
            h.update({"Content-Type": "application/json", "X-Forwarded-For": "100.64.0.9"})
            conn.request("POST", route, body=b"{}", headers=h)
            r = conn.getresponse()
            body = json.loads(r.read())
            conn.close()
            self.assertEqual(r.status, 403, route)
            self.assertIn("only at this machine", body["error"])


if __name__ == "__main__":
    unittest.main()
