#!/usr/bin/python3
"""Tests for worktrees.py: per-pane git worktrees (plan: docs/worktree-review-plan.md).

Real git in temp repos, never the user's. Test IDs (T-GIT-*, T-REG-*, ...)
are the plan's §5.2 catalogue.

Collected by test_corral_light.py, so `python3 test_corral_light.py` runs it.
Run alone: python3 -m unittest test_worktrees -v
"""
import http.server
import os
import socketserver
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "testkit" / "fixtures" / "merge-tree"

# Isolate every git call in this suite from the user's config.
TEST_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

import worktrees as wt  # noqa: E402


class GitCase(unittest.TestCase):
    """A temp dir with one initialised repo; the env is isolated per test."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, TEST_ENV)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="corral-wt-test-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        wt.git(["init", "-q", "-b", "main"], cwd=self.repo)
        (self.repo / "a.txt").write_text("a\n")
        wt.git(["add", "-A"], cwd=self.repo)
        wt.git(["commit", "-qm", "base"], cwd=self.repo)

    def stub_git(self, body):
        """A fake git binary (a shell script) for wrapper behaviour tests."""
        p = self.tmp / "stub-git"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)


class TheGitWrapper(GitCase):

    def test_T_GIT_1_nonzero_raises_with_rc_and_capped_stderr(self):
        with self.assertRaises(wt.GitError) as cm:
            wt.git(["rev-parse", "--verify", "no-such-ref"], cwd=self.repo)
        e = cm.exception
        self.assertEqual(e.rc, 128)
        self.assertIn("rev-parse", " ".join(e.cmd))
        self.assertLessEqual(len(e.err), 400)
        r = wt.git(["rev-parse", "--verify", "no-such-ref"], cwd=self.repo, check=False)
        self.assertEqual(r.rc, 128)

    def test_T_GIT_2_a_push_that_wants_credentials_fails_fast_and_never_asks(self):
        class Deny(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="x"')
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = socketserver.TCPServer(("127.0.0.1", 0), Deny)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        marker = self.tmp / "askpass-was-called"
        askpass = self.stub_git(f"touch {marker}; echo secret")
        wt.git(["config", "core.askPass", askpass], cwd=self.repo)
        url = f"http://127.0.0.1:{srv.server_address[1]}/r.git"
        t0 = time.monotonic()
        r = wt.git(["push", url, "HEAD:refs/heads/x"], cwd=self.repo,
                   check=False, timeout=30)
        self.assertNotEqual(r.rc, 0)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertFalse(marker.exists(), "an askpass helper was run")

    def test_T_GIT_3_a_hung_git_is_killed_with_its_whole_group(self):
        pidfile = self.tmp / "child.pid"
        stub = self.stub_git(f"sleep 300 & echo $! > {pidfile}; wait")
        with mock.patch.object(wt, "GIT_BIN", stub):
            t0 = time.monotonic()
            with self.assertRaises(wt.GitTimeout):
                wt.git(["status"], cwd=self.repo, timeout=1)
        self.assertLess(time.monotonic() - t0, 8)
        child = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.05)
        self.assertFalse(_alive(child), "the grandchild survived the timeout")

    def test_T_GIT_4_a_path_beginning_with_a_dash_is_a_path(self):
        (self.repo / "-n").write_text("x\n")
        wt.git(["add", "--", "-n"], cwd=self.repo)
        r = wt.git(["ls-files", "-z", "--", "-n"], cwd=self.repo)
        self.assertEqual(r.out, b"-n\0")

    def test_T_GIT_5_routing_variables_in_the_hub_env_never_reach_git(self):
        decoy = self.tmp / "decoy"
        decoy.mkdir()
        wt.git(["init", "-q"], cwd=decoy)
        decoy_index = decoy / ".git" / "index"
        poison = {
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy_index),
            "GIT_CONFIG_PARAMETERS": "'user.name'='Decoy'",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.email",
            "GIT_CONFIG_VALUE_0": "decoy@example.invalid",
            "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        }
        with mock.patch.dict(os.environ, poison):
            top = wt.git(["rev-parse", "--show-toplevel"], cwd=self.repo).text.strip()
            (self.repo / "b.txt").write_text("b\n")
            wt.git(["add", "b.txt"], cwd=self.repo)
            name = wt.git(["config", "--get", "user.name"], cwd=self.repo,
                          check=False).text.strip()
        self.assertEqual(Path(top).resolve(), self.repo.resolve())
        self.assertNotEqual(name, "Decoy")
        self.assertFalse(decoy_index.exists(), "the decoy index was written")
        self.assertIn(b"b.txt", wt.git(["ls-files", "-z"], cwd=self.repo).out)

    def test_T_GIT_5b_the_sanitised_env_sets_the_no_prompt_variables(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_CONFIG_KEY_3": "a"}):
            env = wt.git_env()
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_CONFIG_KEY_3", env)
        for k, v in {"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat",
                     "GIT_EDITOR": "true", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
                     "LC_ALL": "C", "GH_PROMPT_DISABLED": "1"}.items():
            self.assertEqual(env.get(k), v, k)
        self.assertNotIn("GIT_OPTIONAL_LOCKS", env)
        self.assertEqual(wt.git_env(optional_locks_off=True)["GIT_OPTIONAL_LOCKS"], "0")

    def test_T_GIT_6_output_past_the_cap_is_truncated_while_streaming(self):
        stub = self.stub_git("head -c 209715200 /dev/zero")
        with mock.patch.object(wt, "GIT_BIN", stub):
            r = wt.git(["log"], cwd=self.repo, max_out=1 << 20, timeout=60)
        self.assertTrue(r.truncated)
        self.assertEqual(len(r.out), 1 << 20)
        self.assertEqual(r.rc, 0)

    def test_T_GIT_7_merge_tree_conflicts_are_a_result_not_an_error(self):
        for ver in ("2.38", "2.55"):
            clean = wt.parse_merge_tree(
                int((FIXTURES / f"merge-tree-{ver}-clean.rc").read_text()),
                (FIXTURES / f"merge-tree-{ver}-clean.bin").read_bytes())
            self.assertTrue(clean["clean"])
            self.assertEqual(clean["conflicts"], [])
            self.assertRegex(clean["tree"], r"^[0-9a-f]{40}$")
            for case, paths in (("conflict", ["f.txt"]), ("rendel", ["r2.txt"])):
                res = wt.parse_merge_tree(
                    int((FIXTURES / f"merge-tree-{ver}-{case}.rc").read_text()),
                    (FIXTURES / f"merge-tree-{ver}-{case}.bin").read_bytes())
                self.assertFalse(res["clean"], case)
                self.assertEqual(res["conflicts"], paths, case)
                self.assertIsNone(res["tree"], "a conflicted tree is never a result")
                self.assertTrue(any("CONFLICT" in m["message"] for m in res["messages"]))
        with self.assertRaises(wt.GitError):
            wt.parse_merge_tree(128, b"fatal: bad\n")

    def test_T_GIT_8_input_goes_on_a_closed_pipe_and_nothing_inherits_stdin(self):
        tree = wt.git(["write-tree"], cwd=self.repo).text.strip()
        msg = "hub commit\n\nbody line\n"
        oid = wt.git(["commit-tree", tree], cwd=self.repo, input=msg.encode()).text.strip()
        body = wt.git(["cat-file", "commit", oid], cwd=self.repo).text
        self.assertTrue(body.endswith("\n\n" + msg), body)
        stub = self.stub_git("cat")         # would block forever on an inherited tty
        with mock.patch.object(wt, "GIT_BIN", stub):
            r = wt.git(["x"], cwd=self.repo, timeout=5)
        self.assertEqual(r.out, b"")

    def test_T_GIT_9_a_lock_is_cleared_only_when_our_recorded_child_is_gone(self):
        lock = self.repo / ".git" / "index.lock"
        lock.write_text("")
        me = {"pid": os.getpid(), "start": "proc:not-my-start-token", "lock": str(lock)}
        self.assertFalse(wt.lock_is_ours_and_stale(str(lock), me),
                         "a live pid with another start token is not ours to clear")
        self.assertFalse(wt.lock_is_ours_and_stale(str(lock), None))
        self.assertFalse(wt.lock_is_ours_and_stale(
            str(lock), dict(me, lock=str(self.repo / "other.lock"))))
        import subprocess
        p = subprocess.Popen(["true"])
        p.wait()
        gone = {"pid": p.pid, "start": "proc:whatever", "lock": str(lock)}
        if not _alive(p.pid):
            self.assertTrue(wt.lock_is_ours_and_stale(str(lock), gone))


class RegCase(GitCase):
    def setUp(self):
        super().setUp()
        self.state = self.tmp / "state"
        self._st = mock.patch.dict(os.environ, {"CORRAL_LIGHT_STATE": str(self.state),
                                                 "CORRAL_LIGHT_WORKTREES": str(self.tmp / "wtroot")})
        self._st.start()
        self.addCleanup(self._st.stop)
        # The suite's temp dirs live on /tmp, which is tmpfs here; probe would
        # rightly refuse it. Pretend a disk, except where a test says otherwise.
        self._fs = mock.patch.object(wt, "_fstype", lambda p: "btrfs")
        self._fs.start()
        self.addCleanup(self._fs.stop)
        self.reg = wt.Registry()


class TheRegistry(RegCase):

    def test_paths_follow_the_env_and_the_registry_sits_beside_the_root(self):
        self.assertEqual(wt.registry_dir(), self.state / "worktree-registry")
        self.assertEqual(wt.worktree_root(), self.tmp / "wtroot")
        with mock.patch.dict(os.environ, {"CORRAL_LIGHT_WORKTREES": ""}):
            self.assertEqual(wt.worktree_root(), self.state / "worktrees")

    def test_round_trip_and_ops_journal(self):
        e = self.reg.create(owner_pane="p1", path="/x/y", branch="refs/heads/corral/y")
        self.assertRegex(e["id"], r"^wt-[0-9a-f]{6}$")
        self.assertEqual(e["phase"], "intent")
        self.assertEqual(e["v"], 1)
        op = self.reg.begin_op(e["id"], "commit", expect_old="a" * 40)
        got = self.reg.read(e["id"])
        self.assertEqual(got["ops"][-1]["state"], "intent")
        self.reg.set_op(e["id"], op, state="done", stage="done", new="b" * 40)
        got = self.reg.read(e["id"])
        self.assertEqual(got["ops"][-1], dict(got["ops"][-1], state="done", new="b" * 40))
        self.reg.update(e["id"], phase="active")
        self.assertEqual([x["id"] for x in self.reg.all()], [e["id"]])
        self.assertEqual(self.reg.read(e["id"])["phase"], "active")

    def test_ids_never_reach_outside_the_registry(self):
        for bad in ("../x", "wt-zz", "wt-abc/../../etc", ""):
            with self.assertRaises(ValueError):
                self.reg.read(bad)
        with self.assertRaises(ValueError):
            self.reg.update(self.reg.create(owner_pane="p")["id"], phase="nonsense")

    def test_T_REG_1_a_killed_writer_leaves_the_old_file_or_the_new_one(self):
        e = self.reg.create(owner_pane="p", path="/p")
        f = wt.registry_dir() / f"{e['id']}.json"
        import subprocess
        import sys
        script = (
            "import os,sys; sys.path.insert(0, %r); import worktrees as wt\n"
            "r = wt.Registry()\n"
            "i = 0\n"
            "while True:\n"
            "    i += 1; r.update(%r, note='x' * (i %% 50000))\n" % (str(ROOT), e["id"]))
        for _ in range(8):
            p = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ))
            time.sleep(0.15 + 0.05 * _)
            p.kill()
            p.wait()
            got = wt.json.loads(f.read_text())           # parses, never partial
            self.assertEqual(got["id"], e["id"])
        self.assertEqual([x["id"] for x in self.reg.all()], [e["id"]],
                         "a leftover temp file is not an entry")

    def test_T_REG_3_an_unknown_future_version_is_refused_and_never_rewritten(self):
        e = self.reg.create(owner_pane="p")
        f = wt.registry_dir() / f"{e['id']}.json"
        f.write_text(wt.json.dumps(dict(e, v=2)))
        before = f.read_bytes()
        with self.assertRaises(wt.RegistryVersionError):
            self.reg.read(e["id"])
        with self.assertRaises(wt.RegistryVersionError):
            self.reg.update(e["id"], phase="active")
        self.assertEqual(f.read_bytes(), before)
        listed = self.reg.all(include_unreadable=True)
        self.assertEqual(listed[0]["unreadable"], "registry v2 is newer than this hub (v1)")

    def test_the_lock_serialises_writers(self):
        order = []
        with self.reg.lock():
            t = threading.Thread(target=lambda: (self.reg.lock().__enter__(), order.append("second")))
            t.start()
            time.sleep(0.2)
            order.append("first")
        t.join(5)
        self.assertEqual(order, ["first", "second"])


def _tree_snapshot(d):
    """Every file under `d` with size and mtime, to prove nothing was written."""
    out = {}
    for root, dirs, files in os.walk(d):
        for f in files:
            st = os.stat(os.path.join(root, f))
            out[os.path.join(root, f)] = (st.st_size, st.st_mtime_ns)
    return out


class TheProbe(RegCase):

    def refusal(self, pr, word):
        self.assertTrue(any(word in r for r in pr["refusals"]), (word, pr["refusals"]))

    def test_T_PRB_1_a_plain_dir_is_not_inside(self):
        d = self.tmp / "plain"
        d.mkdir()
        pr = wt.probe(d)
        self.assertFalse(pr["inside"])
        self.refusal(pr, "not inside a git")

    def test_T_PRB_2_repo_top(self):
        pr = wt.probe(self.repo)
        self.assertTrue(pr["inside"])
        self.assertEqual(pr["refusals"], [])
        self.assertEqual(Path(pr["top"]), self.repo.resolve())
        self.assertEqual(Path(pr["repo_top"]), self.repo.resolve())
        self.assertEqual(pr["subdir"], "")
        self.assertEqual(pr["branch"], "refs/heads/main")
        self.assertRegex(pr["head"], r"^[0-9a-f]{40}$")
        self.assertFalse(pr["dirty"])
        self.assertGreaterEqual(tuple(pr["git_version"]), (2, 38))

    def test_T_PRB_3_a_subdir_gives_subdir(self):
        (self.repo / "pkg" / "deep").mkdir(parents=True)
        pr = wt.probe(self.repo / "pkg" / "deep")
        self.assertEqual(pr["subdir"], "pkg/deep")
        self.assertEqual(Path(pr["top"]), self.repo.resolve())

    def test_T_PRB_4_detached_head_is_refused(self):
        wt.git(["checkout", "-q", "--detach"], cwd=self.repo)
        pr = wt.probe(self.repo)
        self.assertTrue(pr["detached"])
        self.refusal(pr, "detached")

    def test_T_PRB_5_unborn_head_is_refused(self):
        d = self.tmp / "unborn"
        d.mkdir()
        wt.git(["init", "-q", "-b", "main"], cwd=d)
        pr = wt.probe(d)
        self.assertTrue(pr["unborn"])
        self.refusal(pr, "no commits")

    def test_T_PRB_6_bare_is_refused(self):
        d = self.tmp / "bare.git"
        wt.git(["init", "-q", "--bare", str(d)], cwd=self.tmp)
        pr = wt.probe(d)
        self.assertTrue(pr["bare"])
        self.refusal(pr, "bare")

    def test_T_PRB_7_submodules_are_refused(self):
        sub = self.tmp / "sub"
        sub.mkdir()
        wt.git(["init", "-q", "-b", "main"], cwd=sub)
        (sub / "s").write_text("s")
        wt.git(["add", "-A"], cwd=sub)
        wt.git(["commit", "-qm", "s"], cwd=sub)
        wt.git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "sub"],
               cwd=self.repo)
        wt.git(["commit", "-qm", "add sub"], cwd=self.repo)
        pr = wt.probe(self.repo)
        self.assertTrue(pr["submodules"])
        self.refusal(pr, "submodule")

    def test_T_PRB_8_sparse_checkout_is_refused(self):
        wt.git(["config", "core.sparseCheckout", "true"], cwd=self.repo)
        pr = wt.probe(self.repo)
        self.assertTrue(pr["sparse"])
        self.refusal(pr, "sparse")

    def test_T_PRB_9_lfs_attributes_without_git_lfs_are_refused(self):
        (self.repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        wt.git(["add", ".gitattributes"], cwd=self.repo)
        wt.git(["commit", "-qm", "lfs"], cwd=self.repo)
        with mock.patch.object(wt, "_lfs_installed", lambda cwd: False):
            pr = wt.probe(self.repo)
        self.assertTrue(pr["lfs_needed"])
        self.refusal(pr, "LFS")
        with mock.patch.object(wt, "_lfs_installed", lambda cwd: True):
            self.assertEqual(wt.probe(self.repo)["refusals"], [])

    def test_T_PRB_10_a_root_on_tmpfs_is_refused(self):
        with mock.patch.object(wt, "_fstype", lambda p: "tmpfs"):
            pr = wt.probe(self.repo)
        self.assertTrue(pr["root_tmpfs"])
        self.refusal(pr, "tmpfs")
        with mock.patch.object(wt, "_fstype", lambda p: "btrfs"):
            self.assertEqual(wt.probe(self.repo)["refusals"], [])

    def test_T_PRB_11_probe_writes_nothing(self):
        (self.repo / "a.txt").write_text("changed\n")          # dirty: status would refresh
        (self.repo / "u.txt").write_text("untracked\n")
        before = _tree_snapshot(self.repo / ".git")
        pr = wt.probe(self.repo)
        self.assertTrue(pr["dirty"])
        self.assertEqual(_tree_snapshot(self.repo / ".git"), before)
        self.assertFalse(wt.registry_dir().exists())
        self.assertFalse(wt.worktree_root().exists())

    def test_T_PRB_12_a_linked_worktree_resolves_to_the_main_repo(self):
        linked = self.tmp / "linked"
        wt.git(["worktree", "add", "-q", "-b", "feature", str(linked)], cwd=self.repo)
        pr = wt.probe(linked)
        self.assertEqual(Path(pr["repo_top"]), self.repo.resolve())
        self.assertEqual(Path(pr["top"]), linked.resolve())
        self.assertEqual(pr["branch"], "refs/heads/feature")
        self.assertEqual(Path(pr["common_dir"]), (self.repo / ".git").resolve())

    def test_a_branch_named_corral_blocks_the_namespace(self):
        wt.git(["branch", "corral"], cwd=self.repo)
        self.refusal(wt.probe(self.repo), "corral")

    def test_old_git_is_refused(self):
        with mock.patch.object(wt, "git_version", lambda: (2, 37, 0)):
            self.refusal(wt.probe(self.repo), "2.38")


class TheSlug(RegCase):

    def slug(self, title, fallback="11d02ed1a81d"):
        return wt.plan_slug(title, wt.probe(self.repo), fallback=fallback)

    def test_T_SLG_1_ascii_titles_become_readable_slugs(self):
        self.assertEqual(self.slug("Fix the login bug!"), "fix-the-login-bug")
        self.assertEqual(self.slug("  --Weird__name..here--  "), "weird-name-here")

    def test_T_SLG_2_unicode_folds_to_ascii_else_the_fallback(self):
        self.assertEqual(self.slug("Café résumé"), "cafe-resume")
        self.assertEqual(self.slug("日本語"), "11d02ed1a81d")
        self.assertEqual(self.slug(""), "11d02ed1a81d")

    def test_T_SLG_3_a_taken_name_gets_a_numeric_suffix(self):
        wt.git(["branch", "corral/fix"], cwd=self.repo)
        self.assertEqual(self.slug("fix"), "fix-2")
        wt.git(["branch", "corral/fix-2"], cwd=self.repo)
        self.assertEqual(self.slug("fix"), "fix-3")

    def test_T_SLG_4_reserved_names_are_refused(self):
        for t in ("HEAD", "head"):
            self.assertNotEqual(self.slug(t), "head")

    def test_T_SLG_5_length_is_capped_with_room_for_the_suffix(self):
        long = "a" * 80
        s1 = self.slug(long)
        self.assertEqual(len(s1), 40)
        wt.git(["branch", f"corral/{s1}"], cwd=self.repo)
        s2 = self.slug(long)
        self.assertLessEqual(len(s2), 40)
        self.assertTrue(s2.endswith("-2"), s2)
        self.assertRegex(s2, r"^[a-z0-9-]{1,40}$")

    def test_T_SLG_6_a_stale_admin_dir_or_existing_path_forces_a_new_name(self):
        (self.repo / ".git" / "worktrees" / "fix").mkdir(parents=True)
        self.assertEqual(self.slug("fix"), "fix-2")
        rd = wt.repo_dir(wt.probe(self.repo))
        (rd / "fix-2").mkdir(parents=True)
        self.assertEqual(self.slug("fix"), "fix-3")

    def test_the_repo_dir_is_named_and_hashed_under_the_root(self):
        rd = wt.repo_dir(wt.probe(self.repo))
        self.assertEqual(rd.parent, wt.worktree_root())
        self.assertRegex(rd.name, r"^repo-[0-9a-f]{6}$")
        linked = self.tmp / "l"
        wt.git(["worktree", "add", "-q", "-b", "f", str(linked)], cwd=self.repo)
        self.assertEqual(wt.repo_dir(wt.probe(linked)), rd, "one repo, one dir")

    def test_a_branch_named_corral_refuses_slugging(self):
        wt.git(["branch", "corral"], cwd=self.repo)
        with self.assertRaises(ValueError):
            self.slug("x")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                   # a zombie counts as gone
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
