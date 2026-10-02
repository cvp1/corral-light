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


class CreateCase(RegCase):

    def make(self, title="fix login", owner="pane0001", cwd=None):
        return wt.create(wt.probe(cwd or self.repo), title, owner, registry=self.reg)


class TheCreate(CreateCase):

    def test_T_CRT_1_a_worktree_on_its_own_branch_under_the_root(self):
        e = self.make()
        self.assertEqual(e["phase"], "active")
        self.assertEqual(e["branch"], "refs/heads/corral/fix-login")
        p = Path(e["path"])
        self.assertTrue(p.is_dir())
        self.assertTrue(str(p.resolve()).startswith(str(wt.worktree_root().resolve()) + "/"))
        self.assertEqual(wt.git(["symbolic-ref", "HEAD"], cwd=p).text.strip(), e["branch"])
        self.assertEqual(wt.git(["rev-parse", "HEAD"], cwd=p).text.strip(), e["base_sha"])
        self.assertEqual(e["base_ref"], "refs/heads/main")
        self.assertEqual(self.reg.read(e["id"])["phase"], "active")
        self.assertEqual(wt.verify(e)["oid"], e["base_sha"])

    def test_T_CRT_2_the_main_checkout_is_byte_for_byte_unchanged(self):
        idx = self.repo / ".git" / "index"
        before = (idx.read_bytes(), (self.repo / ".git" / "HEAD").read_bytes(),
                  (self.repo / "a.txt").read_bytes())
        self.make()
        after = (idx.read_bytes(), (self.repo / ".git" / "HEAD").read_bytes(),
                 (self.repo / "a.txt").read_bytes())
        self.assertEqual(before, after)

    def test_T_CRT_3_a_dirty_main_stays_dirty_and_the_worktree_starts_clean(self):
        (self.repo / "a.txt").write_text("dirty\n")
        (self.repo / "new.txt").write_text("n\n")
        e = self.make()
        p = Path(e["path"])
        self.assertEqual((p / "a.txt").read_text(), "a\n")
        self.assertFalse((p / "new.txt").exists())
        self.assertEqual((self.repo / "a.txt").read_text(), "dirty\n")

    def test_T_CRT_3b_the_subdir_is_kept(self):
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "m.py").write_text("x = 1\n")
        wt.git(["add", "-A"], cwd=self.repo)
        wt.git(["commit", "-qm", "pkg"], cwd=self.repo)
        e = self.make(cwd=self.repo / "pkg")
        self.assertEqual(e["subdir"], "pkg")
        self.assertTrue(wt.agent_cwd(e).is_dir())
        self.assertEqual(wt.agent_cwd(e), Path(e["path"]) / "pkg")

    def test_T_CRT_4_two_spellings_of_one_repo_share_one_lock(self):
        alias = self.tmp / "alias"
        alias.symlink_to(self.repo)
        self.assertEqual(wt.repo_lock_key(wt.probe(alias)), wt.repo_lock_key(wt.probe(self.repo)))

    def test_T_CRT_5_twelve_concurrent_creates_give_twelve_branches(self):
        results, errors = [], []

        def go(i):
            try:
                results.append(self.make(title="same title", owner=f"pane{i:04d}"))
            except Exception as e:          # noqa: BLE001 — collected for the assert
                errors.append(e)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        self.assertEqual(errors, [])
        self.assertEqual(len({e["branch"] for e in results}), 12)
        self.assertEqual(len({e["path"] for e in results}), 12)
        listed = wt.git(["worktree", "list", "--porcelain"], cwd=self.repo).text
        self.assertEqual(listed.count("branch refs/heads/corral/same-title"), 12)

    def test_T_CRT_6_a_symlinked_root_is_refused(self):
        real = self.tmp / "realroot"
        real.mkdir()
        link = self.tmp / "linkroot"
        link.symlink_to(real)
        with mock.patch.dict(os.environ, {"CORRAL_LIGHT_WORKTREES": str(link)}):
            with self.assertRaises(ValueError) as cm:
                self.make()
        self.assertIn("symlink", str(cm.exception))
        self.assertEqual(self.reg.all(), [])

    def test_T_CRT_7_an_existing_target_path_is_refused(self):
        pr = wt.probe(self.repo)
        with mock.patch.object(wt, "plan_slug", lambda *a, **k: "taken"):
            (wt.repo_dir(pr) / "taken").mkdir(parents=True)
            with self.assertRaises(ValueError):
                wt.create(pr, "x", "pane", registry=self.reg)

    def test_a_refused_probe_creates_nothing(self):
        wt.git(["checkout", "-q", "--detach"], cwd=self.repo)
        with self.assertRaises(ValueError):
            self.make()
        self.assertEqual(self.reg.all(), [])
        self.assertFalse(wt.worktree_root().exists())

    def test_T_REG_2_the_intent_is_written_before_git_worktree_add(self):
        seen = []
        real_git = wt.git

        def spy(args, *a, **k):
            if args[:2] == ["worktree", "add"]:
                seen.append([e["phase"] for e in self.reg.all()])
            return real_git(args, *a, **k)
        with mock.patch.object(wt, "git", spy):
            self.make()
        self.assertEqual(seen, [["intent"]])

    def test_a_failed_add_leaves_the_entry_marked_not_silently_gone(self):
        real_git = wt.git

        def boom(args, *a, **k):
            if args[:2] == ["worktree", "add"]:
                raise wt.GitError(["git", "worktree", "add"], 128, "fatal: simulated")
            return real_git(args, *a, **k)
        with mock.patch.object(wt, "git", boom):
            with self.assertRaises(wt.GitError):
                self.make()
        [e] = self.reg.all()
        self.assertEqual(e["phase"], "missing")
        self.assertIn("simulated", e["error"])


class TheVerify(CreateCase):

    def test_T_VER_1_another_branch_checked_out_is_an_identity_failure(self):
        e = self.make()
        wt.git(["checkout", "-q", "-b", "elsewhere"], cwd=e["path"])
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "identity")

    def test_T_VER_2_a_moved_branch_shows_as_a_new_oid(self):
        e = self.make()
        p = Path(e["path"])
        (p / "z.txt").write_text("z\n")
        wt.git(["add", "z.txt"], cwd=p)
        wt.git(["commit", "-qm", "z"], cwd=p)
        v = wt.verify(e)
        self.assertNotEqual(v["oid"], e["base_sha"])

    def test_T_VER_3_a_path_replaced_by_a_symlink_is_tampered(self):
        e = self.make()
        p = Path(e["path"])
        p.rename(p.with_name("moved-away"))
        p.symlink_to(p.with_name("moved-away"))
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "tampered")

    def test_T_VER_4_a_dot_git_file_pointing_elsewhere_is_tampered(self):
        e = self.make()
        other = self.make(title="other", owner="pane0002")
        (Path(e["path"]) / ".git").write_text((Path(other["path"]) / ".git").read_text())
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "tampered")

    def test_a_deleted_worktree_is_missing(self):
        e = self.make()
        import shutil
        shutil.rmtree(e["path"])            # the test's own temp tree
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "missing")


class TheSummary(CreateCase):

    def objects(self):
        return sum(len(f) for _, _, f in os.walk(self.repo / ".git" / "objects"))

    def test_T_SUM_1_zero_changes(self):
        s = wt.summary(self.make())
        self.assertEqual((s["files"], s["added"], s["deleted"], s["untracked"]), (0, 0, 0, []))

    def test_T_SUM_2_modify_add_delete_are_counted(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("a\nmore\n")              # +1
        (p / "b.txt").write_text("b1\nb2\n")
        wt.git(["add", "b.txt"], cwd=p)                     # staged new file: +2
        wt.git(["commit", "-qm", "b"], cwd=p)               # committed by the agent: still counted vs base
        (p / "b.txt").unlink()                              # and deleted again
        s = wt.summary(e)
        self.assertEqual(s["files"], 1)
        self.assertEqual((s["added"], s["deleted"]), (1, 0))
        (p / "a.txt").unlink()
        s = wt.summary(e)
        self.assertEqual((s["files"], s["added"], s["deleted"]), (1, 0, 1))

    def test_T_SUM_3_untracked_files_are_counted_with_sizes(self):
        e = self.make()
        (Path(e["path"]) / "new.txt").write_text("12345")
        s = wt.summary(e)
        self.assertEqual(s["untracked"], [{"path": "new.txt", "size": 5}])
        self.assertEqual(s["files"], 1)

    def test_T_SUM_4_no_objects_are_written(self):
        e = self.make()
        p = Path(e["path"])
        (p / "big.bin").write_bytes(os.urandom(10 << 20))
        (p / "a.txt").write_text("edited\n")
        before = self.objects()
        for _ in range(20):
            wt.summary(e)
        self.assertEqual(self.objects(), before)

    def test_T_SUM_5_the_real_index_is_byte_identical(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("edited\n")
        idx = Path(wt.git(["rev-parse", "--path-format=absolute", "--git-path", "index"],
                          cwd=p).text.strip())
        before = (idx.read_bytes(), idx.stat().st_mtime_ns)
        wt.summary(e)
        self.assertEqual((idx.read_bytes(), idx.stat().st_mtime_ns), before)

    def test_T_SUM_6_an_edit_changes_the_digest_even_when_totals_do_not(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("x\n")
        d1 = wt.summary(e)["digest"]
        (p / "a.txt").write_text("y\n")                     # same numstat: 1 added, 1 deleted
        st = (p / "a.txt").stat()
        os.utime(p / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        s2 = wt.summary(e)
        self.assertEqual((s2["added"], s2["deleted"]), (1, 1))
        self.assertNotEqual(s2["digest"], d1)

    def test_binary_files_are_named_and_counted(self):
        e = self.make()
        (Path(e["path"]) / "a.txt").write_bytes(b"\0\1\2")
        s = wt.summary(e)
        self.assertEqual(s["binary"], ["a.txt"])


class TheSnapshot(CreateCase):

    def objects(self):
        return sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(self.repo / ".git" / "objects") for f in fs)

    def test_T_SNP_1_the_tree_is_what_add_all_and_write_tree_would_give(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("changed\n")
        (p / "n.txt").write_text("new\n")
        snap = wt.snapshot(e)
        wt.git(["add", "-A"], cwd=p)
        self.assertEqual(snap["tree"], wt.git(["write-tree"], cwd=p).text.strip())
        self.assertEqual(snap["head"], e["base_sha"])

    def test_T_SNP_2_same_content_same_tree_one_byte_different_tree(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("x\n")
        t1 = wt.snapshot(e)["tree"]
        self.assertEqual(wt.snapshot(e)["tree"], t1)
        (p / "a.txt").write_text("y\n")
        self.assertNotEqual(wt.snapshot(e)["tree"], t1)

    def test_T_SNP_7_a_racily_clean_same_size_edit_is_seen(self):
        """git trusts stat data unless an entry is as new as the index file
        itself ("racy clean"), then it re-reads content. A copied index must
        keep the real one's mtime or that check silently stops working."""
        e = self.make()
        p = Path(e["path"])
        a = p / "a.txt"
        st = a.stat()
        a.write_text("z\n")                               # same size as "a\n"
        os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))   # same mtime as checkout
        idx = Path(wt._index_path(p))
        os.utime(idx, ns=(st.st_mtime_ns, st.st_mtime_ns))  # entry is racily clean
        time.sleep(1.1)                                    # a copy stamped now is a later second
        base_tree = wt.git(["rev-parse", e["base_sha"] + "^{tree}"], cwd=p).text.strip()
        self.assertNotEqual(wt.snapshot(e)["tree"], base_tree, "the edit was missed")

    def test_T_SNP_2b_the_real_index_is_never_touched(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("x\n")
        idx = Path(wt.git(["rev-parse", "--path-format=absolute", "--git-path", "index"],
                          cwd=p).text.strip())
        before = idx.read_bytes()
        snap = wt.snapshot(e)
        self.assertEqual(idx.read_bytes(), before)
        self.assertEqual(snap["index_id"], wt.hashlib.sha256(before).hexdigest())

    def test_T_SNP_3_ignored_files_are_inventoried_and_excluded(self):
        e = self.make()
        p = Path(e["path"])
        (p / ".gitignore").write_text(".env\nout/\n")
        (p / ".env").write_text("SECRET=1\n")
        (p / "out").mkdir()
        (p / "out" / "x.bin").write_text("x")
        snap = wt.snapshot(e)
        names = wt.git(["ls-tree", "-r", "--name-only", snap["tree"]], cwd=p).text.split()
        self.assertNotIn(".env", names)
        self.assertIn(".gitignore", names)
        self.assertEqual(snap["ignored"]["count"], 2)
        self.assertIn(".env", snap["ignored"]["sample"])

    def test_T_SNP_5_a_big_untracked_file_is_inventoried_not_added(self):
        e = self.make()
        p = Path(e["path"])
        (p / "data.bin").write_bytes(os.urandom(3 << 20))
        before = self.objects()
        snap = wt.snapshot(e)
        names = wt.git(["ls-tree", "-r", "--name-only", snap["tree"]], cwd=p).text.split()
        self.assertNotIn("data.bin", names)
        self.assertEqual(snap["too_big"], [{"path": "data.bin", "size": 3 << 20}])
        self.assertLess(self.objects() - before, 3 << 20)

    def test_T_SNP_6_the_review_ref_pins_the_tree_across_gc(self):
        e = self.make()
        p = Path(e["path"])
        (p / "pinned.txt").write_text("only in the snapshot\n")
        snap = wt.snapshot(e)
        (p / "pinned.txt").unlink()
        wt.git(["gc", "-q", "--prune=now"], cwd=self.repo, timeout=120)
        self.assertEqual(wt.git(["cat-file", "-t", snap["tree"]], cwd=p).text.strip(), "tree")
        ref = wt.git(["rev-parse", f"refs/corral/review/{e['id']}^{{tree}}"], cwd=p).text.strip()
        self.assertEqual(ref, snap["tree"])

    def test_force_added_and_intent_to_add_files_are_in_the_tree(self):
        e = self.make()
        p = Path(e["path"])
        (p / ".gitignore").write_text("*.log\n")
        (p / "keep.log").write_text("forced\n")
        wt.git(["add", "-f", "keep.log"], cwd=p)
        (p / "ita.txt").write_text("intent\n")
        wt.git(["add", "-N", "ita.txt"], cwd=p)
        names = wt.git(["ls-tree", "-r", "--name-only", wt.snapshot(e)["tree"]], cwd=p).text.split()
        self.assertIn("keep.log", names)
        self.assertIn("ita.txt", names)

    def test_staged_content_that_differs_from_the_file_is_flagged(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("staged\n")
        wt.git(["add", "a.txt"], cwd=p)
        (p / "a.txt").write_text("working\n")
        self.assertEqual(wt.snapshot(e)["staged_differs"], ["a.txt"])

    def test_the_temp_index_is_cleaned_up(self):
        e = self.make()
        tmp = self.tmp / "panedir"
        tmp.mkdir()
        wt.snapshot(e, tmp_dir=tmp)
        self.assertEqual(list(tmp.iterdir()), [])


class TheDiff(CreateCase):

    def snapdiff(self, e):
        return wt.diff(e, wt.snapshot(e)["tree"])

    def by_path(self, d):
        return {f["path"]: f for f in d["files"]}

    def test_T_DIF_1_unified_output_with_worktree_relative_paths(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("a\nb\n")
        (p / "n.txt").write_text("n\n")
        f = self.by_path(self.snapdiff(e))
        self.assertEqual(f["a.txt"]["status"], "M")
        self.assertEqual((f["a.txt"]["add"], f["a.txt"]["del"]), (1, 0))
        self.assertIn("--- a/a.txt\n+++ b/a.txt\n", f["a.txt"]["patch"])
        self.assertIn("@@", f["a.txt"]["patch"])
        self.assertEqual(f["n.txt"]["status"], "A")

    def test_T_DIF_2_caps_hold_and_the_json_stays_under_2_mib(self):
        e = self.make()
        p = Path(e["path"])
        for i in range(120):
            (p / f"f{i}.txt").write_text(("line %d\n" % i) * 3000)
        d = self.snapdiff(e)
        self.assertLessEqual(len(wt.json.dumps(d)), 2 << 20)
        self.assertTrue(d["truncated"])
        self.assertEqual(len(d["files"]), 120, "every file is listed even past the caps")
        self.assertTrue(any(f["patch"] is None for f in d["files"]))

    def test_T_DIF_3_binary_and_oversize_files_have_no_hunks(self):
        e = self.make()
        p = Path(e["path"])
        (p / "bin.dat").write_bytes(b"\0\1\2\3" * 10)
        wt.git(["add", "-f", "bin.dat"], cwd=p)
        (p / "big.txt").write_text("x\n" * 400_000)        # 800 KB tracked via add
        wt.git(["add", "big.txt"], cwd=p)
        f = self.by_path(self.snapdiff(e))
        self.assertTrue(f["bin.dat"]["binary"])
        self.assertIsNone(f["bin.dat"]["patch"])
        self.assertTrue(f["big.txt"]["too_big"])
        self.assertIsNone(f["big.txt"]["patch"])

    def test_T_DIF_4_a_symlink_out_of_the_worktree_is_a_symlink_change(self):
        e = self.make()
        p = Path(e["path"])
        secret = self.tmp / "outside-secret.txt"
        secret.write_text("TOP SECRET\n")
        (p / "link").symlink_to(secret)
        f = self.by_path(self.snapdiff(e))["link"]
        self.assertTrue(f["symlink"])
        self.assertNotIn("TOP SECRET", f["patch"] or "")
        self.assertIn(str(secret), f["patch"])

    def test_T_DIF_5_hostile_user_config_does_not_change_output(self):
        e = self.make()
        p = Path(e["path"])
        (p / ".gitattributes").write_text("*.txt diff=evil\n")
        for k, v in (("diff.external", "false"), ("color.ui", "always"),
                     ("color.diff", "always"), ("diff.evil.textconv", "tr a-z A-Z"),
                     ("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true")):
            wt.git(["config", k, v], cwd=self.repo)
        (p / "a.txt").write_text("hello\n")
        f = self.by_path(self.snapdiff(e))["a.txt"]
        self.assertIn("+hello\n", f["patch"])
        self.assertNotIn("\x1b[", f["patch"])
        self.assertIn("--- a/a.txt", f["patch"])

    def test_T_DIF_6_odd_names_round_trip(self):
        e = self.make()
        p = Path(e["path"])
        names = ["with space.txt", 'quote"d.txt', "new\nline.txt"]
        for n in names:
            (p / n).write_text("x\n")
        raw = b"latin1-\xe9.txt"
        with open(os.path.join(os.fsencode(p), raw), "wb") as fh:
            fh.write(b"y\n")
        d = self.snapdiff(e)
        got = {f["path"] for f in d["files"]}
        for n in names:
            self.assertIn(n, got)
        odd = [f for f in d["files"] if f.get("path_b64")]
        self.assertEqual(len(odd), 1)
        self.assertEqual(wt.base64.b64decode(odd[0]["path_b64"]), raw)

    def test_T_DIF_7_a_rename_is_a_rename_and_the_argv_puts_c_first(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("a\n" * 50)
        wt.git(["add", "a.txt"], cwd=p)
        wt.git(["commit", "-qm", "grow"], cwd=p)
        e2 = dict(e, base_sha=wt.git(["rev-parse", "HEAD"], cwd=p).text.strip())
        wt.git(["mv", "a.txt", "b.txt"], cwd=p)
        calls = []
        real = wt.git

        def spy(args, *a, **k):
            calls.append(list(args))
            return real(args, *a, **k)
        tree = wt.snapshot(e2)["tree"]
        with mock.patch.object(wt, "git", spy):
            d = wt.diff(e2, tree)
        [f] = d["files"]
        self.assertEqual((f["status"], f["old_path"], f["path"]), ("R", "a.txt", "b.txt"))
        raw = [c for c in calls if "diff-tree" in c and "--raw" in c][0]
        self.assertEqual(raw[:3], ["-c", "diff.renameLimit=1000", "diff-tree"])


class TheCommit(CreateCase):

    def setUp(self):
        super().setUp()
        self.e = self.make()
        self.p = Path(self.e["path"])

    def review(self):
        return wt.snapshot(self.e)

    def commit(self, snap, message="hub commit", **kw):
        return wt.commit_tree(self.e, snap["tree"], snap["index_id"], message,
                              expect_head=snap["head"], **kw)

    def status(self):
        return wt.git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=self.p).out

    def test_T_CMT_1_the_commit_tree_is_exactly_the_reviewed_tree(self):
        (self.p / "a.txt").write_text("reviewed\n")
        (self.p / "n.txt").write_text("new\n")
        snap = self.review()
        r = self.commit(snap)
        self.assertEqual(wt.git(["rev-parse", r["commit"] + "^{tree}"], cwd=self.p).text.strip(),
                         snap["tree"])
        self.assertEqual(wt.git(["rev-parse", self.e["branch"]], cwd=self.p).text.strip(), r["commit"])
        self.assertEqual(wt.git(["rev-parse", r["commit"] + "^"], cwd=self.p).text.strip(), snap["head"])
        self.assertEqual(self.reg.read(self.e["id"])["last_commit"], r["commit"])
        self.assertEqual(self.reg.read(self.e["id"])["ops"][-1]["state"], "done")

    def test_T_CMT_2_a_file_changed_after_review_refuses(self):
        (self.p / "a.txt").write_text("reviewed\n")
        snap = self.review()
        (self.p / "a.txt").write_text("changed after\n")
        with self.assertRaises(wt.Refused) as cm:
            self.commit(snap)
        self.assertEqual(cm.exception.reason, "changed")
        self.assertEqual(wt.git(["rev-parse", self.e["branch"]], cwd=self.p).text.strip(), snap["head"])

    def test_T_CMT_3_a_moved_branch_refuses(self):
        (self.p / "a.txt").write_text("reviewed\n")
        snap = self.review()
        moved = wt.git(["commit-tree", snap["tree"], "-p", snap["head"]], cwd=self.p,
                       input=b"external\n").text.strip()
        wt.git(["update-ref", self.e["branch"], moved], cwd=self.p)
        with self.assertRaises(wt.Refused) as cm:
            self.commit(snap)
        self.assertEqual(cm.exception.reason, "identity")
        self.assertEqual(wt.git(["rev-parse", self.e["branch"]], cwd=self.p).text.strip(), moved)

    def test_T_CMT_4_hooks_are_not_run(self):
        hooks = self.repo / ".git" / "hooks"
        marker = self.tmp / "hook-ran"
        for h in ("pre-commit", "commit-msg", "post-commit"):
            f = hooks / h
            f.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
            f.chmod(0o755)
        (self.p / "a.txt").write_text("x\n")
        self.commit(self.review())
        self.assertFalse(marker.exists())

    def test_T_CMT_5_status_is_clean_for_the_reviewed_paths_and_later_files_stay(self):
        (self.p / "a.txt").write_text("x\n")
        snap = self.review()
        self.commit(snap)
        (self.p / "late.txt").write_text("after\n")
        self.assertEqual(self.status(), b"?? late.txt\0")

    def test_T_CMT_6_empty_message_refused_and_nothing_to_commit_is_a_noop(self):
        (self.p / "a.txt").write_text("x\n")
        with self.assertRaises(ValueError):
            self.commit(self.review(), message="  \n")
        r = self.commit(self.review(), message="m")
        self.assertTrue(r["commit"])
        r2 = self.commit(self.review(), message="again")
        self.assertTrue(r2["noop"])
        self.assertIsNone(r2["commit"])

    def test_T_CMT_7_a_force_added_file_is_committed_and_a_second_commit_is_a_noop(self):
        (self.p / ".gitignore").write_text("*.log\n")
        (self.p / "keep.log").write_text("forced\n")
        wt.git(["add", "-f", "keep.log"], cwd=self.p)
        r = self.commit(self.review())
        names = wt.git(["ls-tree", "-r", "--name-only", r["commit"]], cwd=self.p).text.split()
        self.assertIn("keep.log", names)
        self.assertEqual(self.status(), b"")
        self.assertTrue(self.commit(self.review())["noop"])

    def test_T_CMT_8_gpg_sign_configured_refuses_fast(self):
        wt.git(["config", "commit.gpgSign", "true"], cwd=self.repo)
        (self.p / "a.txt").write_text("x\n")
        snap = self.review()
        t0 = time.monotonic()
        with self.assertRaises(wt.Refused) as cm:
            self.commit(snap)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(cm.exception.reason, "signing")
        idx = Path(wt._index_path(self.p))
        self.assertFalse(Path(str(idx) + ".lock").exists())

    def test_T_CMT_9_no_tracked_file_shows_modified_afterwards(self):
        for i in range(30):
            (self.p / f"f{i}.txt").write_text(f"{i}\n")
        (self.p / "a.txt").write_text("x\n")
        self.commit(self.review())
        self.assertEqual(self.status(), b"")
        diff = wt.git(["diff", "--name-only"], cwd=self.p).out
        self.assertEqual(diff, b"")

    def test_T_CMT_10_a_staged_blob_that_differs_is_kept_as_a_recovery_ref(self):
        (self.p / "a.txt").write_text("staged version\n")
        wt.git(["add", "a.txt"], cwd=self.p)
        (self.p / "a.txt").write_text("working version\n")
        snap = self.review()
        self.assertEqual(snap["staged_differs"], ["a.txt"])
        r = self.commit(snap)
        [ref] = r["recovery_refs"]
        self.assertTrue(ref.startswith(f"refs/corral/recovery/{self.e['id']}/"))
        self.assertEqual(wt.git(["show", f"{ref}:a.txt"], cwd=self.p).text, "staged version\n")
        self.assertEqual(wt.git(["show", f"{r['commit']}:a.txt"], cwd=self.p).text, "working version\n")
        self.assertIn(ref, self.reg.read(self.e["id"])["recovery_refs"])

    def test_T_CMT_11_the_real_index_changing_after_review_refuses(self):
        (self.p / "a.txt").write_text("x\n")
        (self.p / "b.txt").write_text("b\n")
        snap = self.review()
        wt.git(["add", "b.txt"], cwd=self.p)      # same tree via add -A, different index
        with self.assertRaises(wt.Refused) as cm:
            self.commit(snap)
        self.assertEqual(cm.exception.reason, "changed")

    def test_T_CMT_12_an_index_lock_refuses_and_is_never_removed(self):
        (self.p / "a.txt").write_text("x\n")
        snap = self.review()
        lock = Path(wt._index_path(self.p) + ".lock")
        lock.write_text("")
        with self.assertRaises(wt.Refused) as cm:
            self.commit(snap)
        self.assertEqual(cm.exception.reason, "busy")
        self.assertTrue(lock.exists())


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
