#!/usr/bin/python3
"""The terminal surface (cli.py) and the lane matrix, end to end.

Every test here starts a REAL hub on a private port and state dir, with the
fake ACP lane registered, and runs the CLI as a subprocess — the way a human
at a terminal would, pairing itself through auth.py like consult does.
Collected by test_corral_light.py.
"""
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

HUB_WITH_FAKE = r"""
import os, sys
sys.path.insert(0, os.environ["LIGHT_ROOT"])
import sessions
sessions.Manager.seed_catalogs = lambda self: None     # no live vendor probes
for k in list(sessions.AGENTS):                        # only the fake lane
    sessions.AGENTS[k]["unavailable"] = "not under test"
sessions.AGENTS["fake"] = {"label": "Fake", "argv": [sys.executable, os.environ["FAKE"]],
                           "posture_via_config_dir": False, "tools": True,
                           "env": {"FAKE_ACP_DIR": os.environ["FAKE_ACP_DIR"]}}
import hub
hub.serve("127.0.0.1", int(os.environ["PORT"]))
"""


class HubCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_resilience import start_hub
        cls.tmp = Path(tmpdir(cls, "corral-light-cli-"))
        (cls.tmp / "agent").mkdir()
        cls.env = {**os.environ, "CORRAL_LIGHT_STATE": str(cls.tmp / "state"),
                   "CORRAL_LIGHT_CONFIG_DIR": str(cls.tmp / "config"),
                   "CORRAL_LIGHT_ROLES_DIR": str(cls.tmp / "roles"),
                   "CORRAL_LIGHT_CONSULT_CFG": str(cls.tmp / "cfg" / "s.json"),
                   "FAKE": str(FAKE), "FAKE_ACP_DIR": str(cls.tmp / "agent")}
        cls.hub, cls.url = start_hub(cls.tmp / "state", extra_env=cls.env,
                                     script=HUB_WITH_FAKE)
        cls.env["CORRAL_LIGHT_URL"] = cls.url

    @classmethod
    def tearDownClass(cls):
        from test_resilience import stop_hub
        # SIGTERM first: the hub's own handler, then reap any adapter by pid.
        cls.hub.terminate()
        try:
            cls.hub.wait(10)
        except subprocess.TimeoutExpired:
            pass
        stop_hub(cls.hub)
        for meta in (cls.tmp / "state" / "panes").glob("*/meta.json"):
            try:
                m = json.loads(meta.read_text())
                if m.get("pgid"):
                    os.killpg(m["pgid"], 9)
            except (OSError, ValueError):
                pass

    def cli(self, *args, stdin=None, timeout=30):
        r = subprocess.run([sys.executable, str(ROOT / "cli.py"), *args],
                           env=self.env, input=stdin, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def open(self):
        rc, out, err = self.cli("open", "--lane", "fake", "--cwd", str(self.tmp / "agent"))
        self.assertEqual(rc, 0, err)
        return out.strip()


class TheCli(HubCase):
    def test_open_say_and_list(self):
        pid = self.open()
        for i in range(3):                # a fast reply must never be missed
            rc, out, err = self.cli("say", pid, f"remember plum{i}")
            self.assertEqual(rc, 0, out + err)
            self.assertIn("ok", out)
        rc, out, _ = self.cli("panes")
        self.assertIn(pid, out)
        self.cli("close", pid)

    def test_seat_binds_lists_refuses_and_unbinds(self):
        """`seat` binds, `panes` shows the column, a held name is refused with the
        holder named, and `-` unbinds.
        """
        a, b = self.open(), self.open()
        rc, out, err = self.cli("seat", a, "author")
        self.assertEqual((rc, out.strip()), (0, "@author"), err)
        rc, out, _ = self.cli("panes")
        self.assertIn("@author", out)
        rc, out, err = self.cli("seat", b, "author")
        self.assertNotEqual(rc, 0, "a held seat was bound twice")
        self.assertIn(a, out + err, "the refusal does not name the holder")
        rc, out, err = self.cli("seat", a, "-")
        self.assertEqual((rc, out.strip()), (0, "unbound"), err)
        st = json.loads(self.cli("panes", "--json")[1])
        self.assertTrue(all(p.get("seat") is None for p in st))
        for pid in (a, b):
            self.cli("close", pid)

    def test_say_prints_the_full_card_and_takes_the_answer_on_the_same_terminal(self):
        pid = self.open()
        rc, out, err = self.cli("say", "--interactive", pid, "perm", stdin="ok\n")
        self.assertEqual(rc, 0, out + err)
        self.assertIn('"command": "touch /tmp/x"', out)    # the payload, in full
        self.assertIn("digest: ", out)
        self.assertIn("approved: Allow", out)
        self.assertIn('"optionId": "allow"', out)          # the agent got it
        rc, out, err = self.cli("say", "--interactive", pid, "perm", stdin="no\n")
        self.assertIn('"optionId": "deny"', out)
        self.cli("close", pid)

    def test_without_a_terminal_say_waits_and_ok_needs_the_digest(self):
        pid = self.open()
        say = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "say", pid, "perm"],
                               env=self.env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            cards = []
            for _ in range(100):
                rc, out, _ = self.cli("pending", pid, "--json")
                cards = json.loads(out) if rc == 0 else []
                if cards:
                    break
                time.sleep(0.1)
            self.assertEqual(len(cards), 1)
            digest = cards[0]["digest"]
            time.sleep(1.5)                  # well past a poll: nothing cancelled it
            self.assertIsNone(say.poll(), "say gave up on a turn waiting for a human")
            rc, out, err = self.cli("ok", pid)
            self.assertEqual(rc, 2)
            self.assertIn("--digest", err)
            rc, out, err = self.cli("ok", pid, "--digest", "0" * 12)
            self.assertEqual(rc, 2, "a wrong digest approved a card")
            rc, out, err = self.cli("ok", pid, "--digest", digest[:12])
            self.assertEqual(rc, 0, err)
            out, err = say.communicate(timeout=20)
            self.assertEqual(say.returncode, 0, out + err)
            self.assertIn('"optionId": "allow"', out)
        finally:
            if say.poll() is None:
                say.kill()
                say.communicate()
            self.cli("close", pid)

    def test_pause_resume_keeps_the_conversation(self):
        pid = self.open()
        self.cli("say", pid, "remember quince")
        self.assertEqual(self.cli("pause", pid)[0], 0)
        rc, out, _ = self.cli("resume", pid)
        self.assertIn("ready", out)
        rc, out, _ = self.cli("say", pid, "what word?")
        self.assertIn("quince", out)
        self.cli("close", pid)

    def test_say_never_cancels_on_a_clock(self):
        src = (ROOT / "cli.py").read_text(encoding="utf-8")
        follow = src[src.index("    def follow("):src.index("# ── verbs")]
        self.assertNotIn("/api/session/cancel", follow)
        self.assertNotIn("wait_turn(", src)     # consult's cancel-on-budget wait


class RolesOverTheCli(HubCase):
    def test_open_with_a_role_sends_its_instructions_only_with_an_ask(self):
        import roles
        roles.create({"id": "echoer", "description": "repeats what it is told",
                      "personality": "terse", "does": "echo", "expects": "the echo",
                      "data_class": "public"}, rdir=self.tmp / "roles")
        rc, out, err = self.cli("open", "--role", "echoer", "--cwd", str(self.tmp / "agent"),
                                "--lane", "fake")
        self.assertEqual(rc, 0, err)
        self.assertIn("NOT sent", err)
        self.assertIn("You are working as `echoer`", err)
        pid = out.strip()
        rc, out, _ = self.cli("panes", "--json")
        self.assertIn('"id": "' + pid, out)
        rc, out, err = self.cli("open", "--role", "echoer", "--cwd", str(self.tmp / "agent"),
                                "--lane", "fake", "--ask", "hello role")
        self.assertEqual(rc, 0, err)
        self.assertIn("echo: You are working as `echoer`", out)
        self.assertIn("hello role", out)
        for line in self.cli("panes")[1].splitlines():
            self.cli("close", line.split()[0])


class TaskFirstOverTheCli(HubCase):
    """New with a task sends it at once (the dialog and `open --task` share the
    hub path) and pins the goal; a role's instructions ride ahead of it."""

    def _wait_events(self, pid, needle, timeout=15):
        f = self.tmp / "state" / "panes" / pid / "events.jsonl"
        end = time.time() + timeout
        while time.time() < end:
            if f.exists() and needle in f.read_text(encoding="utf-8"):
                return f.read_text(encoding="utf-8")
            time.sleep(0.1)
        self.fail(f"{needle!r} never reached {pid}'s transcript")

    def test_open_with_a_task_sends_it_and_pins_the_goal(self):
        rc, out, err = self.cli("open", "--lane", "fake", "--cwd", str(self.tmp / "agent"),
                                "--task", "count the  walnuts", "--done", "a number is printed")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("NOT sent", err)
        pid = out.strip()
        evs = self._wait_events(pid, "count the  walnuts")
        users = [json.loads(l)["data"]["text"] for l in evs.splitlines()
                 if l and json.loads(l).get("kind") == "user"]
        self.assertEqual(users, ["count the  walnuts\n\nDone when: a number is printed"],
                         "what was sent is not the task plus its finish line")
        meta = json.loads((self.tmp / "state" / "panes" / pid / "meta.json").read_text())
        self.assertEqual(meta["goal"], {"task": "count the walnuts",
                                        "done": "a number is printed"})
        snap = [p for p in json.loads(self.cli("panes", "--json")[1]) if p["id"] == pid][0]
        self.assertEqual(snap["goal"]["task"], "count the walnuts")
        self.cli("close", pid)

    def test_a_role_rides_ahead_of_the_task_in_one_turn(self):
        import roles
        roles.create({"id": "counter", "description": "counts things", "personality": "terse",
                      "does": "count", "expects": "a number", "data_class": "public"},
                     rdir=self.tmp / "roles")
        rc, out, err = self.cli("open", "--role", "counter", "--lane", "fake",
                                "--cwd", str(self.tmp / "agent"), "--task", "count pecans")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("NOT sent", err, "a task-carrying role must not be held back")
        evs = self._wait_events(out.strip(), "count pecans")
        users = [json.loads(l)["data"]["text"] for l in evs.splitlines()
                 if l and json.loads(l).get("kind") == "user"]
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0].startswith("You are working as `counter`"), users[0][:80])
        self.assertTrue(users[0].endswith("---\n\ncount pecans"), users[0][-80:])
        self.cli("close", out.strip())

    def test_no_task_sends_nothing(self):
        pid = self.open()
        time.sleep(0.5)
        f = self.tmp / "state" / "panes" / pid / "events.jsonl"
        self.assertNotIn('"kind": "user"', f.read_text(encoding="utf-8") if f.exists() else "")
        meta = json.loads((self.tmp / "state" / "panes" / pid / "meta.json").read_text())
        self.assertIsNone(meta.get("goal"))
        self.cli("close", pid)


class NextUpOverTheCli(HubCase):
    """`next` lists what waits behind a running turn; `withdraw` takes one back
    through the hub route the browser uses, and prints it."""

    def test_next_lists_and_withdraw_takes_back(self):
        pid = self.open()
        run = lambda *a: subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "say", pid, *a],
                                          env=self.env, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        def until(pred, what):
            end = time.time() + 15
            while time.time() < end:
                if pred():
                    return
                time.sleep(0.2)
            self.fail(what)
        state = lambda: [p for p in json.loads(self.cli("panes", "--json")[1])
                         if p["id"] == pid][0]["state"]
        procs = [run("sleep 30")]
        try:
            until(lambda: state() == "busy", "the pane never started its turn")
            procs.append(run("walnut one"))
            until(lambda: "1." in self.cli("next", pid)[1], "walnut one never queued")
            procs.append(run("walnut two"))
            until(lambda: "2." in self.cli("next", pid)[1], "walnut two never queued")
            rc, out, err = self.cli("next", pid)
            self.assertEqual(rc, 0, err)
            self.assertRegex(out, r"(?s)1\. \S+  walnut one.*2\. \S+  walnut two")
            rc, out, err = self.cli("withdraw", pid, "1")
            self.assertEqual((rc, out.strip()), (0, "walnut one"), err)
            rc, out, _ = self.cli("next", pid)
            self.assertNotIn("walnut one", out)
            self.assertIn("1. ", out)
            rc, out, err = self.cli("withdraw", pid, "5")
            self.assertEqual(rc, 1, "an out-of-range number must fail")
            rc, out, err = self.cli("withdraw", pid, "t-not-queued")
            self.assertNotEqual(rc, 0)
            self.assertIn("409", out + err)
        finally:
            self.cli("cancel", pid)
            for pr in procs:
                pr.kill()
                pr.wait(5)
            self.cli("close", pid)


class LaterOverTheCli(HubCase):
    def test_add_list_rm(self):
        rc, out, err = self.cli("later", "add", "--at", "2099-01-01T06:00", "--lane", "fake",
                                "--cwd", str(self.tmp / "agent"), "--prompt", "summarise",
                                "--repeat", "daily")
        self.assertEqual(rc, 0, err)
        jid = out.split()[0]
        rc, out, _ = self.cli("later", "list")
        self.assertIn(jid, out)
        self.assertIn("daily", out)
        rc, out, _ = self.cli("later", "rm", jid)
        self.assertIn("removed", out)
        rc, out, _ = self.cli("later", "list")
        self.assertIn("nothing scheduled", out)


class SearchOverTheCli(HubCase):
    def test_say_then_find_it(self):
        pid = self.open()
        self.cli("say", pid, "remember xylophone")
        import transcripts
        transcripts.refresh(force=True, state_dir=self.tmp / "state")   # skip the hub's throttle
        rc, out, err = self.cli("search", "xylophone")
        self.assertEqual(rc, 0, err)
        self.assertIn(pid, out)
        rc, out, _ = self.cli("digest", "--hours", "1")
        self.assertIn("What the agents did", out)
        self.cli("close", pid)


class PortOverTheCli(HubCase):
    def test_port_needs_the_sha_and_delivers(self):
        pid = self.open()
        self.cli("say", pid, "remember pecan")
        rc, out, err = self.cli("port", pid, "--lane", "fake")
        self.assertEqual(rc, 2)
        self.assertIn("--sha", err)
        sha = out.strip().splitlines()[-1].split("sha ")[-1]
        self.assertIn("## The original ask", out)
        rc, out, err = self.cli("port", pid, "--lane", "fake", "--sha", sha[:12])
        self.assertEqual(rc, 0, err)
        self.assertIn("delivered", out.splitlines()[-1])
        for line in self.cli("panes")[1].splitlines():
            self.cli("close", line.split()[0])

    def test_port_carries_the_exact_model_through_the_hub(self):
        pid = self.open()
        self.cli("say", pid, "remember hazel")
        _, out, _ = self.cli("port", pid, "--lane", "fake")
        sha = out.strip().splitlines()[-1].split("sha ")[-1]
        rc, out, err = self.cli("port", pid, "--lane", "fake", "--sha", sha[:12],
                                "--model", "opus", "--effort", "high")
        self.assertEqual(rc, 0, err)
        new = out.splitlines()[-1].split()[0]
        meta = json.loads((self.tmp / "state" / "panes" / new / "meta.json").read_text())
        self.assertEqual((meta["want_model"], meta["want_effort"]), ("opus", "high"))
        for line in self.cli("panes")[1].splitlines():
            self.cli("close", line.split()[0])


class TheLaneMatrix(HubCase):
    def test_the_fake_lane_remembers_and_refuses(self):
        r = subprocess.run([sys.executable, str(ROOT / "lane_matrix.py"), "--json", "--lane", "fake",
                            "--cwd", str(self.tmp / "agent")],
                           env=self.env, capture_output=True, text=True, timeout=120)
        rows = json.loads(r.stdout)
        self.assertEqual([x["lane"] for x in rows], ["fake"], r.stderr)
        row = rows[0]
        self.assertEqual((row["open"], row["first"], row["resume"], row["remembered"]),
                         ("yes", "yes", "yes", "yes"), row)
        self.assertEqual(row["permission"], "asked, refused")
        self.assertNotIn("PROBE FILE WAS CREATED", row["notes"])
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
