#!/usr/bin/env python3
"""`corral-light update` against throwaway git repositories. No hub, no
service manager: both are mocked at update.py's own seams.

    python3 test_update.py     (also collected by test_corral_light.py)
"""
import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("light-update-")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update                                                     # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def sh(cwd, *args):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                       env=dict(os.environ, **GIT_ENV))
    if r.returncode != 0:
        raise AssertionError(f"{args}: {r.stderr}")
    return r.stdout.strip()


class Repo(unittest.TestCase):
    """An origin with master, a clone of it (the install), and a second clone
    that pushes new commits (the developer)."""

    def setUp(self):
        base = Path(tmpdir(self, "update-"))
        self.state = base / "state"
        self.state.mkdir()
        for name, val in (("STATE", self.state),
                          ("STATUS_FILE", self.state / "update-status.json"),
                          ("POLL_S", 0)):
            p = mock.patch.object(update, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.origin = base / "origin.git"
        sh(base, "git", "init", "-q", "--bare", "-b", "master", str(self.origin))
        self.dev = base / "dev"
        sh(base, "git", "clone", "-q", str(self.origin), str(self.dev))
        sh(self.dev, "git", "checkout", "-q", "-b", "master")
        self.commit("hub.py", "v1", "first")
        (self.dev / "spike").mkdir()
        self.commit("spike/package-lock.json", "{}", "lock")
        sh(self.dev, "git", "push", "-q", "origin", "master")
        self.inst = base / "install"
        sh(base, "git", "clone", "-q", str(self.origin), str(self.inst))
        self.addCleanup(mock.patch.stopall)

    def commit(self, rel, text, msg, repo=None):
        repo = repo or self.dev
        (repo / rel).write_text(text, encoding="utf-8")
        sh(repo, "git", "add", rel)
        sh(repo, "git", "commit", "-q", "-m", msg)

    def push(self, rel="hub.py", text="v2", msg="second"):
        self.commit(rel, text, msg)
        sh(self.dev, "git", "push", "-q", "origin", "master")

    def head(self, repo=None):
        return sh(repo or self.inst, "git", "rev-parse", "HEAD")

    def args(self, **kw):
        a = update.build_parser().parse_args([])
        a.dir = str(self.inst)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def run_update(self, st=None, **kw):
        """`st` is the hub before; after a restart it reports the new HEAD."""
        lines = []

        def state(url):
            if st is None or not getattr(state, "seen", False):
                state.seen = True
                return st
            return dict(st, hub={"root": str(self.inst), "commit": self.head()})
        with mock.patch.object(update, "hub_state", side_effect=state):
            code, res = update.run(self.args(**kw), lines.append)
        return code, res, lines


class Plan(Repo):
    def test_up_to_date(self):
        p = update.plan(self.inst)
        self.assertEqual((p["behind"], p["changed"], p["lockfile"]), (0, [], False))

    def test_behind_lists_the_commits_and_flags_the_lockfile(self):
        self.push("spike/package-lock.json", '{"v": 2}', "bump adapters")
        p = update.plan(self.inst)
        self.assertEqual(p["behind"], 1)
        self.assertTrue(p["lockfile"])
        self.assertIn("bump adapters", p["subjects"][0])
        self.assertEqual(p["head"], self.head(), "plan moved HEAD")

    def test_a_changed_service_template_is_named(self):
        self.push("corral-light.service", "[Unit]", "unit")
        self.assertEqual(update.plan(self.inst)["templates"], ["corral-light.service"])

    def test_every_refusal_changes_nothing(self):
        self.push()
        before = self.head()
        (self.inst / "hub.py").write_text("local edit", encoding="utf-8")
        with self.assertRaisesRegex(update.Refused, "local changes"):
            update.plan(self.inst)
        sh(self.inst, "git", "checkout", "-q", "--", "hub.py")
        self.commit("mine.txt", "x", "mine", repo=self.inst)
        with self.assertRaisesRegex(update.Refused, "1 commit"):
            update.plan(self.inst)
        sh(self.inst, "git", "reset", "-q", "--hard", before)
        sh(self.inst, "git", "checkout", "-q", "-b", "other")
        with self.assertRaisesRegex(update.Refused, "not 'master'"):
            update.plan(self.inst)
        sh(self.inst, "git", "checkout", "-q", "--detach", "master")
        with self.assertRaisesRegex(update.Refused, "detached"):
            update.plan(self.inst)
        self.assertEqual(self.head(), before)

    def test_untracked_files_do_not_block(self):
        self.push()
        (self.inst / "scratch.log").write_text("x", encoding="utf-8")
        self.assertEqual(update.plan(self.inst)["behind"], 1)


class Blockers(unittest.TestCase):
    def test_mid_turn_and_waiting_panes_block_and_the_callers_pane_does_not(self):
        st = {"panes": [
            {"id": "a", "state": "busy", "title": "A", "pending": []},
            {"id": "b", "state": "starting", "pending": []},
            {"id": "c", "state": "needs-you", "pending": ["r1"]},
            {"id": "d", "state": "ready", "pending": []},
            {"id": "e", "state": "detached", "pending": []},
            {"id": "f", "state": "ready", "pending": [], "question": {"text": "?"}},
            {"id": "me", "state": "busy", "pending": []}]}
        got = {b["id"]: b["why"] for b in update.blockers(st, own="me")}
        self.assertEqual(set(got), {"a", "b", "c"})
        self.assertIn("permission", got["c"])

    def test_own_pane_from_either_variable(self):
        with mock.patch.dict(os.environ, {"CORRAL_PANE_ID": "abc123abc123"}):
            self.assertEqual(update.own_pane(), "abc123abc123")
        env = {k: v for k, v in os.environ.items() if k != "CORRAL_PANE_ID"}
        env["CLAUDE_CONFIG_DIR"] = "/x/corral-light/panes/0123456789ab/config"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(update.own_pane(), "0123456789ab")
        env["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(update.own_pane())


class Run(Repo):
    def hub(self, *panes, commit=None):
        return {"hub": {"root": str(self.inst), "commit": commit or self.head()},
                "panes": list(panes)}

    def test_check_changes_nothing(self):
        self.push()
        before = self.head()
        code, res, _ = self.run_update(self.hub(), check=True)
        self.assertEqual((code, res["action"], self.head()), (0, "update", before))

    def test_busy_panes_defer_and_nothing_is_pulled(self):
        self.push()
        before = self.head()
        with mock.patch.object(update, "restart") as rs:
            code, res, lines = self.run_update(
                self.hub({"id": "x", "state": "busy", "title": "Big job", "pending": []}))
        self.assertEqual(code, update.EXIT_DEFERRED)
        self.assertEqual(res["outcome"], "deferred")
        self.assertEqual(self.head(), before)
        rs.assert_not_called()
        self.assertTrue(any("Big job" in line for line in lines), lines)

    def test_idle_hub_is_updated_and_restarted(self):
        self.push()
        target = sh(self.dev, "git", "rev-parse", "HEAD")
        with mock.patch.object(update, "inside_hub", return_value=False), \
                mock.patch.object(update, "restart", return_value=(True, "hub restarted")) as rs, \
                mock.patch.object(update, "wait_healthy", return_value=True):
            code, res, _ = self.run_update(self.hub())
        self.assertEqual((code, res["outcome"]), (0, "updated"))
        self.assertEqual(self.head(), target)
        rs.assert_called_once_with(False)

    def test_now_overrides_busy_panes(self):
        self.push()
        with mock.patch.object(update, "inside_hub", return_value=False), \
                mock.patch.object(update, "restart", return_value=(True, "ok")), \
                mock.patch.object(update, "wait_healthy", return_value=True):
            code, res, _ = self.run_update(
                self.hub({"id": "x", "state": "busy", "pending": []}), now=True)
        self.assertEqual(res["outcome"], "updated")

    def test_from_inside_a_pane_the_restart_is_queued_not_awaited(self):
        self.push()
        with mock.patch.object(update, "inside_hub", return_value=True), \
                mock.patch.object(update, "restart", return_value=(True, "queued")) as rs, \
                mock.patch.object(update, "wait_healthy") as wh:
            code, _, _ = self.run_update(self.hub())
        self.assertEqual(code, 0)
        rs.assert_called_once_with(True)
        wh.assert_not_called()

    def test_a_stale_hub_is_restarted_even_when_the_checkout_is_current(self):
        st = self.hub(commit="0" * 40)
        with mock.patch.object(update, "inside_hub", return_value=False), \
                mock.patch.object(update, "restart", return_value=(True, "ok")) as rs, \
                mock.patch.object(update, "wait_healthy", return_value=True):
            code, res, _ = self.run_update(st)
        self.assertEqual((code, res["outcome"], res["hub_stale"]), (0, "restarted", True))
        rs.assert_called_once()

    def test_current_and_fresh_does_nothing(self):
        with mock.patch.object(update, "restart") as rs:
            code, res, _ = self.run_update(self.hub())
        self.assertEqual((code, res["outcome"]), (0, "current"))
        rs.assert_not_called()

    def test_no_hub_means_pull_only(self):
        self.push()
        with mock.patch.object(update, "restart") as rs:
            code, res, _ = self.run_update(None)
        self.assertEqual((code, res["outcome"]), (0, "updated"))
        rs.assert_not_called()

    def test_a_failed_adapter_install_rolls_back(self):
        self.push("spike/package-lock.json", '{"v": 2}', "bump adapters")
        before = self.head()
        with mock.patch.object(update, "node_bin", return_value=Path("/x")), \
                mock.patch.object(update, "npm_ci",
                                  side_effect=update.Refused("npm ci failed: boom")), \
                mock.patch.object(update, "restart") as rs:
            with self.assertRaisesRegex(update.Refused, "rolled back"):
                self.run_update(None)
        self.assertEqual(self.head(), before)
        rs.assert_not_called()

    PATCHER_OK = ("import pathlib, sys\n"
                  "pathlib.Path(sys.argv[2], 'patched-by').write_text('{tag}')\n"
                  "print('rate-limit-before-usage: patched')\n")
    PATCHER_BAD = ("print('rate-limit-before-usage: drift')\n"
                   "raise SystemExit(1)\n")

    def test_adapters_are_patched_by_the_pulled_patcher(self):
        """npm ci installs unpatched adapters; the checkout's OWN patcher (the
        code just pulled, not this running script) must then apply."""
        self.commit("adapter_patches.py", self.PATCHER_OK.format(tag="v1"), "patcher v1")
        sh(self.dev, "git", "push", "-q", "origin", "master")
        sh(self.inst, "git", "pull", "-q", "--ff-only")
        self.commit("adapter_patches.py", self.PATCHER_OK.format(tag="v2"), "patcher v2")
        self.push("spike/package-lock.json", '{"v": 2}', "bump adapters")
        lines = []
        with mock.patch.object(update, "node_bin", return_value=Path("/x")), \
                mock.patch.object(update, "npm_ci") as ci:
            update.apply(update.plan(self.inst), lines.append)
        ci.assert_called_once()
        self.assertEqual((self.inst / "spike" / "patched-by").read_text(), "v2")
        self.assertTrue(any("adapter patches" in ln and "patched" in ln for ln in lines), lines)

    def test_a_patch_that_does_not_take_rolls_back(self):
        self.commit("adapter_patches.py", self.PATCHER_BAD, "patcher that drifts")
        self.push("spike/package-lock.json", '{"v": 2}', "bump adapters")
        before = self.head()
        with mock.patch.object(update, "node_bin", return_value=Path("/x")), \
                mock.patch.object(update, "npm_ci") as ci:
            with self.assertRaisesRegex(update.Refused, "patches did not apply.*rolled back"):
                update.apply(update.plan(self.inst), lambda m: None)
        self.assertEqual(self.head(), before)
        self.assertEqual(ci.call_count, 2)           # the new install, then the old again

    def test_a_patcher_change_alone_is_applied(self):
        self.push("adapter_patches.py", self.PATCHER_OK.format(tag="v3"), "patcher only")
        with mock.patch.object(update, "npm_ci") as ci:
            update.apply(update.plan(self.inst), lambda m: None)
        ci.assert_not_called()
        self.assertEqual((self.inst / "spike" / "patched-by").read_text(), "v3")

    def test_no_npm_refuses_before_pulling(self):
        self.push("spike/package-lock.json", '{"v": 2}', "bump adapters")
        before = self.head()
        with mock.patch.object(update, "node_bin", return_value=None):
            with self.assertRaisesRegex(update.Refused, "no npm"):
                update.apply(update.plan(self.inst), lambda m: None)
        self.assertEqual(self.head(), before)

    def test_unattended_records_the_outcome(self):
        self.push()
        argv = ["--dir", str(self.inst), "--unattended", "--json"]
        with mock.patch.object(update, "hub_state", return_value=None), \
                mock.patch("sys.stdout"):
            self.assertEqual(update.main(argv), 0)
        import json
        rec = json.loads((self.state / "update-status.json").read_text())
        self.assertEqual(rec["outcome"], "updated")


class Installers(unittest.TestCase):
    def test_skill_link_and_refusal(self):
        home = Path(tmpdir(self, "update-home-"))
        said = []
        update.install_skill(HERE, said.append, home=home)
        dst = home / ".claude/skills/corral-update"
        self.assertTrue(dst.is_symlink())
        self.assertTrue((dst / "SKILL.md").is_file())
        update.install_skill(HERE, said.append, home=home)
        self.assertIn("already linked", said[-1])
        dst.unlink()
        dst.mkdir()
        with self.assertRaisesRegex(update.Refused, "not this checkout's link"):
            update.install_skill(HERE, said.append, home=home)

    def test_the_skill_names_itself_and_the_verb(self):
        text = (HERE / "skills/corral-update/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: corral-update\ndescription: "))
        self.assertIn("corral-light update --check", text)

    def test_timer_units_point_at_the_checkout_and_accept_deferral(self):
        service, timer = update.timer_units(Path("/opt/cl"))
        self.assertIn("ExecStart=/usr/bin/python3 /opt/cl/update.py --unattended", service)
        self.assertIn("SuccessExitStatus=3", service)
        self.assertIn("Persistent=true", timer)
        self.assertIn("/opt/cl/update.py", update.launchd_plist(Path("/opt/cl")))

    def test_the_launcher_routes_update(self):
        text = (HERE / "corral-light").read_text(encoding="utf-8")
        self.assertIn('update) shift; exec "$PY" "$D/update.py" "$@" ;;', text)


if __name__ == "__main__":
    unittest.main()
