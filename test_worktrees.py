#!/usr/bin/python3
"""Tests for worktrees.py: per-pane git worktrees (plan: docs/worktree-review-plan.md).

Real git in temp repos, never the user's. Test IDs (T-GIT-*, T-REG-*, ...)
are the plan's §5.2 catalogue.

Collected by test_corral_light.py, so `python3 test_corral_light.py` runs it.
Run alone: python3 -m unittest test_worktrees -v
"""
import errno
import http.server
import os
import signal
import socketserver
import stat
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
# Before anything imports sessions (STATE binds at import time): never the live store.
from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")
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
        # realpath: on macOS mkdtemp answers /var/folders/..., a symlink into /private.
        self.tmp = Path(os.path.realpath(tmpdir(self, "corral-wt-test-")))
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

    def test_T_SNP_1b_head_tree_says_whether_the_review_is_committed(self):
        """The review dialog enables Commit or Publish from this, not from a guess."""
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("changed\n")
        snap = wt.snapshot(e)
        base_tree = wt.git(["rev-parse", e["base_sha"] + "^{tree}"], cwd=p).text.strip()
        self.assertEqual(snap["head_tree"], base_tree)
        self.assertNotEqual(snap["tree"], snap["head_tree"])
        wt.commit_tree(e, snap["tree"], snap["index_id"], "m", expect_head=snap["head"],
                       registry=self.reg)
        again = wt.snapshot(e)
        self.assertEqual(again["tree"], again["head_tree"])

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
        try:
            with open(os.path.join(os.fsencode(p), raw), "wb") as fh:
                fh.write(b"y\n")
        except OSError as err:
            # APFS refuses a name that is not UTF-8 (EILSEQ), so on macOS such a
            # file cannot exist in a worktree; the other odd names still run.
            if err.errno != errno.EILSEQ:
                raise
            raw = None
        d = self.snapdiff(e)
        got = {f["path"] for f in d["files"]}
        for n in names:
            self.assertIn(n, got)
        odd = [f for f in d["files"] if f.get("path_b64")]
        if raw is None:
            self.assertEqual(odd, [])
            return
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


class PubCase(CreateCase):

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "remote.git"
        wt.git(["init", "-q", "--bare", str(self.remote)], cwd=self.tmp)
        wt.git(["remote", "add", "origin", str(self.remote)], cwd=self.repo)
        wt.git(["push", "-q", "origin", "main"], cwd=self.repo)
        self.e = self.make()
        self.p = Path(self.e["path"])
        (self.p / "a.txt").write_text("published\n")
        snap = wt.snapshot(self.e)
        self.oid = wt.commit_tree(self.e, snap["tree"], snap["index_id"], "feat",
                                  expect_head=snap["head"], registry=self.reg)["commit"]
        self.tree = snap["tree"]
        self.url = str(self.remote)

    def push(self, **kw):
        args = dict(remote="origin", push_url=self.url, oid=self.oid, reviewed_tree=self.tree,
                    registry=self.reg)
        args.update(kw)
        return wt.push(self.e, **args)

    def remote_ref(self, ref):
        r = wt.git(["ls-remote", self.url, ref], cwd=self.tmp).text.split()
        return r[0] if r else None


class ThePush(PubCase):

    def test_T_PUB_1_push_creates_the_branch_at_the_exact_oid(self):
        r = self.push()
        self.assertEqual(r["pushed"], self.oid)
        self.assertEqual(self.remote_ref("refs/heads/corral/fix-login"), self.oid)
        pub = self.reg.read(self.e["id"])["published"]
        self.assertEqual((pub["oid"], pub["url"], pub["ref"]),
                         (self.oid, self.url, "refs/heads/corral/fix-login"))

    def test_T_PUB_2_a_changed_remote_url_refuses(self):
        other = self.tmp / "other.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "origin", str(other)], cwd=self.repo)
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertEqual(cm.exception.reason, "remote_changed")
        self.assertIsNone(self.remote_ref("refs/heads/corral/fix-login"))

    def test_T_PUB_3_uncommitted_changes_refuse(self):
        (self.p / "a.txt").write_text("not committed\n")
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertEqual(cm.exception.reason, "uncommitted")

    def test_T_PUB_3b_a_head_that_is_not_the_reviewed_tree_refuses(self):
        with self.assertRaises(wt.Refused) as cm:
            self.push(reviewed_tree="0" * 40)
        self.assertEqual(cm.exception.reason, "changed")

    def test_T_PUB_4_a_diverged_remote_branch_is_never_forced(self):
        self.push()
        (self.p / "a.txt").write_text("second\n")
        snap = wt.snapshot(self.e)
        second = wt.commit_tree(self.e, snap["tree"], snap["index_id"], "second",
                                expect_head=snap["head"], registry=self.reg)["commit"]
        # someone else rewrites the remote branch to an unrelated commit
        other = wt.git(["commit-tree", snap["tree"], "-p", self.e["base_sha"]], cwd=self.p,
                       input=b"theirs\n").text.strip()
        wt.git(["push", "-q", "--force", self.url, f"{other}:refs/heads/corral/fix-login"], cwd=self.p)
        with self.assertRaises(wt.Refused) as cm:
            self.push(oid=second, reviewed_tree=snap["tree"])
        self.assertEqual(cm.exception.reason, "non_ff")
        self.assertEqual(self.remote_ref("refs/heads/corral/fix-login"), other)

    def test_T_PUB_9_a_tag_of_the_same_name_does_not_receive_the_push(self):
        wt.git(["push", "-q", self.url, f"{self.e['base_sha']}:refs/tags/corral/fix-login"], cwd=self.p)
        self.push()
        self.assertEqual(self.remote_ref("refs/tags/corral/fix-login"), self.e["base_sha"])
        self.assertEqual(self.remote_ref("refs/heads/corral/fix-login"), self.oid)

    def test_T_PUB_11_effective_push_urls(self):
        pushto = self.tmp / "pushto.git"
        wt.git(["init", "-q", "--bare", str(pushto)], cwd=self.tmp)
        wt.git(["config", "remote.origin.pushurl", str(pushto)], cwd=self.repo)
        self.assertEqual(wt.push_urls(self.e, "origin"), [str(pushto)])
        with self.assertRaises(wt.Refused):
            self.push()                                    # confirmed the fetch URL
        self.push(push_url=str(pushto))
        self.assertIsNone(self.remote_ref("refs/heads/corral/fix-login"))
        wt.git(["config", "--unset", "remote.origin.pushurl"], cwd=self.repo)
        wt.git(["config", f"url.{pushto}.pushInsteadOf", self.url], cwd=self.repo)
        self.assertEqual(wt.push_urls(self.e, "origin"), [str(pushto)])
        wt.git(["config", "--unset", f"url.{pushto}.pushInsteadOf"], cwd=self.repo)
        wt.git(["config", "--add", "remote.origin.pushurl", self.url], cwd=self.repo)
        wt.git(["config", "--add", "remote.origin.pushurl", str(pushto)], cwd=self.repo)
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertIn("2 push URLs", cm.exception.detail)


class ThePullRequest(PubCase):

    def stub_gh(self, signed_in=True):
        state = self.tmp / "gh-state"
        state.mkdir(exist_ok=True)
        body = f"""
echo "$@" >> {state}/argv
env | grep -q '^GH_PROMPT_DISABLED=1$' || {{ echo noenv >> {state}/argv; }}
case "$1 $2" in
  "auth status") {'exit 0' if signed_in else 'echo "not logged in" >&2; exit 1'} ;;
  "pr list") cat {state}/pr 2>/dev/null || echo "[]" ;;
  "pr create") cat > {state}/body; h=""; r=""; prev="";
               for a in "$@"; do [ "$prev" = "--head" ] && h="$a"; [ "$prev" = "--repo" ] && r="$a"; prev="$a"; done;
               case "$h" in *:*) o="${{h%%:*}}";; *) o="${{r%%/*}}";; esac;
               echo '[{{"url":"https://github.com/o/r/pull/7","headRepositoryOwner":{{"login":"'"$o"'"}}}}]' > {state}/pr;
               echo https://github.com/o/r/pull/7 ;;
esac
"""
        stub = self.stub_git(body)
        return stub, state

    def pr(self, gh, **kw):
        args = dict(title="Fix login", body="body", repo="o/r", registry=self.reg)
        args.update(kw)
        with mock.patch.object(wt, "GH_BIN", gh):
            return wt.open_pr(self.e, **args)

    def test_github_repo_from_urls(self):
        for u in ("https://github.com/o/r.git", "git@github.com:o/r.git",
                  "ssh://git@github.com/o/r", "https://github.com/o/r"):
            self.assertEqual(wt.github_repo(u), "o/r", u)
        self.assertIsNone(wt.github_repo("/local/path.git"))

    def test_T_PUB_5_no_gh_gives_a_compare_url(self):
        wt.git(["remote", "set-url", "origin", "https://github.com/o/r.git"], cwd=self.repo)
        r = self.pr(str(self.tmp / "no-such-gh"))
        self.assertEqual(r["compare_url"],
                         "https://github.com/o/r/compare/main...corral/fix-login?expand=1")
        self.assertIsNone(r["pr_url"])

    def test_T_PUB_6_gh_signed_out_names_the_fix(self):
        gh, _ = self.stub_gh(signed_in=False)
        with self.assertRaises(wt.Refused) as cm:
            self.pr(gh)
        self.assertIn("gh auth login", cm.exception.detail)

    def test_T_PUB_7_success_is_stored_and_a_second_call_finds_the_same_pr(self):
        gh, state = self.stub_gh()
        r = self.pr(gh)
        self.assertEqual(r["pr_url"], "https://github.com/o/r/pull/7")
        self.assertEqual(self.reg.read(self.e["id"])["published"]["pr_url"], r["pr_url"])
        r2 = self.pr(gh)
        self.assertEqual(r2["pr_url"], r["pr_url"])
        self.assertEqual((state / "argv").read_text().count("pr create"), 1)

    def test_T_PUB_8_text_reaches_gh_literally(self):
        gh, state = self.stub_gh()
        marker = self.tmp / "x"
        self.pr(gh, title=f"$(touch {marker})", body=f"`touch {marker}`")
        self.assertFalse(marker.exists())
        self.assertIn(f"$(touch {marker})", (state / "argv").read_text())
        self.assertEqual((state / "body").read_text(), f"`touch {marker}`")

    def test_T_PUB_10_the_confirmed_repo_is_passed_and_prompts_are_off(self):
        gh, state = self.stub_gh()
        self.pr(gh, repo="parent/r")
        argv = (state / "argv").read_text()
        self.assertIn("pr create --repo parent/r --head corral/fix-login --base main", argv)
        self.assertNotIn("noenv", argv)


class TheDiscard(CreateCase):

    def setUp(self):
        super().setUp()
        self.e = self.make()
        self.p = Path(self.e["path"])

    def discard(self, **kw):
        snap = wt.snapshot(self.e)
        return wt.discard(self.e, snap["tree"], registry=self.reg, **kw), snap

    def test_T_RMV_1_the_recovery_ref_holds_the_snapshot_tree(self):
        (self.p / "wip.txt").write_text("untracked work\n")
        r, snap = self.discard()
        self.assertTrue(r["recovery_ref"].startswith(f"refs/corral/recovery/{self.e['id']}/"))
        self.assertEqual(wt.git(["rev-parse", r["recovery_ref"] + "^{tree}"], cwd=self.repo).text.strip(),
                         snap["tree"])
        self.assertEqual(wt.git(["show", r["recovery_ref"] + ":wip.txt"], cwd=self.repo).text,
                         "untracked work\n")

    def test_T_RMV_2_and_12_the_dir_moves_to_trash_with_ignored_and_dirty_files(self):
        (self.p / ".gitignore").write_text(".env\ndata/\nnested/\n")
        (self.p / ".env").write_text("SECRET=1\n")
        (self.p / "data").mkdir()
        (self.p / "data" / "big.bin").write_bytes(os.urandom(5 << 20))
        nested = self.p / "nested"
        nested.mkdir()
        wt.git(["init", "-q"], cwd=nested)
        (self.p / "a.txt").write_text("modified\n")
        (self.p / "u.txt").write_text("untracked\n")
        r, _ = self.discard()
        t = Path(r["trash_path"])
        self.assertFalse(self.p.exists())
        self.assertEqual(t.parent, wt.worktree_root() / ".trash")
        self.assertEqual((t / ".env").read_text(), "SECRET=1\n")
        self.assertEqual((t / "data" / "big.bin").stat().st_size, 5 << 20)
        self.assertTrue((t / "nested" / ".git").is_dir())
        self.assertEqual((t / "a.txt").read_text(), "modified\n")
        self.assertEqual((t / "u.txt").read_text(), "untracked\n")
        e = self.reg.read(self.e["id"])
        self.assertEqual(e["phase"], "trashed")
        self.assertIn(".env", e["ignored_at_discard"])

    def test_T_RMV_3_the_branch_survives_discard(self):
        self.discard()
        self.assertEqual(wt.git(["rev-parse", self.e["branch"]], cwd=self.repo).text.strip(),
                         self.e["base_sha"])

    def test_T_RMV_4_restore_brings_it_back_and_it_verifies(self):
        (self.p / "u.txt").write_text("keep\n")
        self.discard()
        e = wt.restore(self.reg.read(self.e["id"]), registry=self.reg)
        self.assertEqual(e["phase"], "active")
        self.assertEqual((self.p / "u.txt").read_text(), "keep\n")
        wt.verify(e)

    def test_T_RMV_5_purge_needs_the_typed_branch_name(self):
        self.discard()
        e = self.reg.read(self.e["id"])
        for wrong in ("", "fix-login", "yes", "corral/other"):
            with self.assertRaises(ValueError):
                wt.purge(e, wrong, registry=self.reg)
        self.assertTrue(Path(e["trash_path"]).exists())

    def test_purge_removes_trash_and_branch_and_keeps_recovery(self):
        r, _ = self.discard()
        e = self.reg.read(self.e["id"])
        wt.purge(e, "corral/fix-login", registry=self.reg)
        self.assertFalse(Path(e["trash_path"]).exists())
        self.assertEqual(wt.git(["branch", "--list", "corral/fix-login"], cwd=self.repo).text, "")
        self.assertEqual(self.reg.read(self.e["id"])["phase"], "purged")
        self.assertTrue(wt.git(["rev-parse", "--verify", r["recovery_ref"]], cwd=self.repo).text)  # T-RMV-8

    def test_T_RMV_6_purge_never_deletes_a_moved_branch(self):
        self.discard()
        (self.repo / "a.txt").write_text("main moved\n")
        wt.git(["commit", "-qam", "main moved"], cwd=self.repo)
        wt.git(["update-ref", "refs/heads/corral/fix-login", "main"], cwd=self.repo)
        e = self.reg.read(self.e["id"])
        r = wt.purge(e, "corral/fix-login", registry=self.reg)
        self.assertFalse(r["branch_deleted"])
        self.assertIn("corral/fix-login", wt.git(["branch", "--list", "corral/fix-login"], cwd=self.repo).text)

    def test_T_RMV_7_purge_refuses_a_path_outside_trash(self):
        self.discard()
        e = self.reg.read(self.e["id"])
        bad = dict(e, trash_path=str(self.repo))
        with self.assertRaises(ValueError):
            wt.purge(bad, "corral/fix-login", registry=self.reg)
        self.assertTrue((self.repo / "a.txt").exists())
        bad = dict(e, branch="refs/heads/main")
        with self.assertRaises(ValueError):
            wt.purge(bad, "main", registry=self.reg)

    def test_T_RMV_9_the_module_deletes_nothing_except_its_own_temp_files(self):
        import ast
        tree = ast.parse((ROOT / "worktrees.py").read_text())
        allowed = {"_unlink_own_temp"}
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and n.attr in ("rmtree", "remove", "unlink",
                                                               "rmdir", "removedirs"):
                    if isinstance(n.value, ast.Name) and n.value.id in ("os", "shutil") or n.attr == "unlink":
                        self.assertIn(fn.name, allowed, f"{fn.name} calls {n.attr}")
        src = (ROOT / "worktrees.py").read_text()
        self.assertNotIn("worktree\", \"prune", src)
        self.assertNotIn('"prune"', src)

    def test_T_RMV_10_published_but_not_integrated_is_not_merged(self):
        (self.p / "a.txt").write_text("x\n")
        snap = wt.snapshot(self.e)
        new = wt.commit_tree(self.e, snap["tree"], snap["index_id"], "m",
                             expect_head=snap["head"], registry=self.reg)["commit"]
        self.reg.update(self.e["id"], published={"oid": new, "url": "x", "ref": self.e["branch"]})
        self.assertFalse(wt.is_integrated(self.reg.read(self.e["id"])))
        wt.git(["merge", "-q", "--ff-only", "corral/fix-login"], cwd=self.repo)
        self.assertTrue(wt.is_integrated(self.reg.read(self.e["id"])))

    def test_T_RMV_13_a_process_inside_the_worktree_blocks_discard_by_name(self):
        import subprocess
        child = subprocess.Popen(["sleep", "30"], cwd=self.p, start_new_session=True)
        self.addCleanup(lambda: (child.kill(), child.wait()))
        with self.assertRaises(wt.Refused) as cm:
            self.discard()
        self.assertEqual(cm.exception.reason, "busy")
        self.assertIn(str(child.pid), cm.exception.detail)
        self.assertIn("sleep", cm.exception.detail)
        self.assertTrue(self.p.exists())

    def test_discard_refuses_when_files_changed_since_review(self):
        snap = wt.snapshot(self.e)
        (self.p / "late.txt").write_text("late\n")
        with self.assertRaises(wt.Refused) as cm:
            wt.discard(self.e, snap["tree"], registry=self.reg)
        self.assertEqual(cm.exception.reason, "changed")
        self.assertTrue(self.p.exists())


CRASH_SCRIPT = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
import worktrees as wt
entry = wt.Registry().read(sys.argv[2])
action = sys.argv[3]
snap = wt.snapshot(entry)
if action == "commit":
    wt.commit_tree(entry, snap["tree"], snap["index_id"], "crash test", expect_head=snap["head"])
elif action == "discard":
    wt.discard(entry, snap["tree"])
print(json.dumps(snap))
"""


class TheReconcile(CreateCase):

    def notes(self):
        return wt.reconcile(self.reg)

    def test_T_REC_1_a_healthy_registry_has_no_notes(self):
        self.make()
        self.assertEqual(self.notes(), [])

    def test_T_REC_2_a_deleted_dir_is_missing_reported_once_and_never_pruned(self):
        e = self.make()
        import shutil
        shutil.rmtree(e["path"])                    # the test's own temp tree
        calls = []
        real = wt.git

        def spy(args, *a, **k):
            calls.append(list(args))
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", spy):
            n1 = self.notes()
            n2 = self.notes()
        self.assertEqual([n["id"] for n in n1], [e["id"]])
        self.assertEqual(n2, [], "reported once")
        self.assertEqual(self.reg.read(e["id"])["phase"], "missing")
        self.assertFalse(any("prune" in c for c in calls))
        self.assertIn(Path(e["path"]).name, real(["worktree", "list"], cwd=self.repo).text)

    def test_T_REC_3_a_branch_deleted_by_hand_is_reported(self):
        e = self.make()
        wt.git(["checkout", "-q", "--detach"], cwd=e["path"])
        wt.git(["branch", "-D", "corral/fix-login"], cwd=self.repo)
        [n] = self.notes()
        self.assertIn("corral/fix-login", n["note"])

    def test_T_REC_4_an_unknown_dir_under_the_root_is_an_orphan(self):
        e = self.make()
        stray = Path(e["path"]).parent / "stray"
        stray.mkdir()
        [n] = self.notes()
        self.assertEqual(n["kind"], "orphan")
        self.assertEqual(n["path"], str(stray))
        self.assertTrue(stray.exists())

    def test_T_REC_5_the_users_own_worktrees_are_untouched_and_unlisted(self):
        mine = self.tmp / "users-own"
        wt.git(["worktree", "add", "-q", "-b", "mine", str(mine)], cwd=self.repo)
        self.make()
        self.assertEqual(self.notes(), [])
        self.assertTrue(mine.exists())

    def test_T_REC_6_a_vanished_repo_marks_entries_missing_and_does_not_raise(self):
        e = self.make()
        import shutil
        shutil.rmtree(self.repo)                     # the test's own temp repo
        notes = self.notes()
        self.assertEqual(self.reg.read(e["id"])["phase"], "missing")
        self.assertTrue(any(n["id"] == e["id"] for n in notes))

    def test_an_intent_left_by_a_crash_before_add_becomes_missing(self):
        pr = wt.probe(self.repo)
        e = self.reg.create(owner_pane="p", path=str(wt.repo_dir(pr) / "never"), branch="refs/heads/corral/never",
                            admin_name="never", repo_top=pr["repo_top"], common_dir=pr["common_dir"],
                            base_ref=pr["branch"], base_sha=pr["head"], subdir="")
        [n] = self.notes()
        self.assertEqual(self.reg.read(e["id"])["phase"], "missing")
        self.assertIn("did not finish", n["note"])

    def test_an_intent_left_by_a_crash_after_add_becomes_active(self):
        real_update = self.reg.update

        def die_on_active(wt_id, **f):
            if f.get("phase") == "active":
                raise KeyboardInterrupt("simulated crash")
            return real_update(wt_id, **f)
        with mock.patch.object(self.reg, "update", die_on_active):
            with self.assertRaises(KeyboardInterrupt):
                self.make()
        [e] = self.reg.all()
        self.reg.update(e["id"], phase="intent", error=None)   # as a SIGKILL would have left it
        self.notes()
        self.assertEqual(self.reg.read(e["id"])["phase"], "active")

    # ── crash runs: a real process SIGKILLed at each journalled boundary ──

    def crash(self, action, at, e):
        import subprocess
        import sys
        env = dict(os.environ, CORRAL_WT_TEST="1", CORRAL_WT_CRASH_AT=at)
        r = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(ROOT), e["id"], action],
                           env=env, capture_output=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stderr.decode()[-500:])

    def test_T_CRS_2_commit_killed_at_each_stage_finishes_on_restart(self):
        for i, at in enumerate(("commit:prepared", "commit:ref_moved", "commit:index")):
            e = self.make(title=f"crash {i}", owner=f"pane{i:04d}")
            p = Path(e["path"])
            (p / "a.txt").write_text(f"reviewed {i}\n")
            want_tree = wt.snapshot(e)["tree"]
            self.crash("commit", at, e)
            notes = self.notes()
            got = self.reg.read(e["id"])
            op = got["ops"][-1]
            self.assertEqual(op["state"], "done", (at, op, notes))
            tip = wt.git(["rev-parse", e["branch"]], cwd=p).text.strip()
            self.assertEqual(tip, op["new"], at)
            self.assertEqual(wt.git(["rev-parse", tip + "^{tree}"], cwd=p).text.strip(), want_tree)
            self.assertEqual(wt.git(["status", "--porcelain"], cwd=p).text, "", at)
            self.assertEqual(wt.git(["rev-list", "--count", f"{e['base_sha']}..{tip}"], cwd=p).text.strip(), "1")
            snap = wt.snapshot(e)
            again = wt.commit_tree(e, snap["tree"], snap["index_id"], "retry",
                                   expect_head=snap["head"], registry=self.reg)
            self.assertTrue(again["noop"], f"{at}: a retry made a second commit")

    def test_T_RMV_15_T_CRS_5_discard_killed_mid_move_reports_the_true_location(self):
        for i, at in enumerate(("discard:journalled", "discard:moved")):
            e = self.make(title=f"dcrash {i}", owner=f"pane{i:04d}")
            (Path(e["path"]) / "w.txt").write_text("work\n")
            self.crash("discard", at, e)
            self.notes()
            got = self.reg.read(e["id"])
            if at == "discard:journalled":
                self.assertEqual(got["phase"], "active")
                self.assertTrue(Path(e["path"]).exists())
            else:
                self.assertEqual(got["phase"], "trashed")
                self.assertTrue(Path(got["trash_path"], "w.txt").exists())
            self.assertEqual(got["ops"][-1]["state"], "done")


# Create, push and pull request, killed mid-way in a real subprocess (T-CRS-1, 3, 4).
CRASH_SCRIPT_PUB = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
import worktrees as wt
wt._fstype = lambda p: "btrfs"          # the suite's temp dirs are on tmpfs
a = json.loads(sys.argv[2])
if a["action"] == "create":
    wt.create(wt.probe(a["repo"]), a["title"], a["owner"])
else:
    e = wt.Registry().read(a["id"])
    if a["action"] == "push":
        wt.push(e, "origin", a["url"], a["oid"], a["tree"])
    elif a["action"] == "pr":
        wt.open_pr(e, "Fix login", "body", "o/r")
"""


class TheCrashes(PubCase):
    """Every journalled step of create, push and PR survives a SIGKILL: the
    restart reports the truth, finishes nothing by guessing, repeats nothing."""

    stub_gh = ThePullRequest.stub_gh

    def crash(self, at, gh=None, **a):
        import json
        import subprocess
        import sys
        env = dict(os.environ, CORRAL_WT_TEST="1", CORRAL_WT_CRASH_AT=at)
        if gh:
            env["CORRAL_TEST_GH"] = gh
        r = subprocess.run([sys.executable, "-c", CRASH_SCRIPT_PUB, str(ROOT), json.dumps(a)],
                           env=env, capture_output=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGKILL, (at, r.stderr.decode()[-800:]))

    def test_T_CRS_1_create_killed_leaves_a_live_branch_or_a_listed_entry(self):
        before = {e["id"] for e in self.reg.all()}
        for i, at in enumerate(("create:intent", "create:added")):
            self.crash(at, action="create", repo=str(self.repo), title=f"crash create {i}",
                       owner=f"crash{i}")
            notes = wt.reconcile(self.reg)
            [e] = [x for x in self.reg.all() if x["id"] not in before]
            before.add(e["id"])
            self.assertEqual(wt.orphans(registry=self.reg), [], (at, notes))
            if at == "create:intent":
                self.assertEqual(e["phase"], "missing", at)
                self.assertIn("did not finish", " ".join(n["note"] for n in notes))
                self.assertFalse(os.path.lexists(e["path"]))
            else:
                self.assertEqual(e["phase"], "active", (at, notes))
                wt.verify(e)
                self.assertEqual(wt.git(["rev-parse", e["branch"]], cwd=self.repo).text.strip(),
                                 e["base_sha"])

    def test_T_CRS_3_push_killed_is_recorded_only_if_the_remote_has_it(self):
        ref = self.e["branch"]
        self.crash("push:before", action="push", id=self.e["id"], url=self.url, oid=self.oid,
                   tree=self.tree)
        wt.reconcile(self.reg)
        got = self.reg.read(self.e["id"])
        self.assertEqual((got["ops"][-1]["state"], got["ops"][-1]["stage"]), ("done", "not_done"))
        self.assertIsNone(got["published"])
        self.assertIsNone(self.remote_ref(ref))
        self.crash("push:after", action="push", id=self.e["id"], url=self.url, oid=self.oid,
                   tree=self.tree)
        self.assertEqual(self.remote_ref(ref), self.oid)
        self.assertEqual(self.reg.read(self.e["id"])["ops"][-1]["state"], "intent")
        notes = wt.reconcile(self.reg)
        got = self.reg.read(self.e["id"])
        self.assertEqual(got["ops"][-1]["state"], "done", notes)
        self.assertEqual((got["published"]["oid"], got["published"]["url"]), (self.oid, self.url))
        self.assertIn("outcome checked after restart", " ".join(n["note"] for n in notes))

    def test_T_CRS_4_pr_killed_is_found_on_restart_and_never_made_twice(self):
        gh, state = self.stub_gh()
        self.crash("pr:before", gh=gh, action="pr", id=self.e["id"])
        with mock.patch.object(wt, "GH_BIN", gh):
            wt.reconcile(self.reg)
        got = self.reg.read(self.e["id"])
        self.assertEqual(got["ops"][-1]["stage"], "not_done")
        self.assertNotIn("pr create", (state / "argv").read_text())
        self.crash("pr:after", gh=gh, action="pr", id=self.e["id"])
        self.assertEqual((state / "argv").read_text().count("pr create"), 1)
        with mock.patch.object(wt, "GH_BIN", gh):
            notes = wt.reconcile(self.reg)
            got = self.reg.read(self.e["id"])
            self.assertEqual(got["ops"][-1]["state"], "done", notes)
            self.assertEqual(got["published"]["pr_url"], "https://github.com/o/r/pull/7")
            again = wt.open_pr(got, "Fix login", "body", "o/r", registry=self.reg)
        self.assertEqual(again["pr_url"], "https://github.com/o/r/pull/7")
        self.assertEqual((state / "argv").read_text().count("pr create"), 1,
                         "a restart made a second pull request")


from test_resilience import FakeLaneCase, wait_for  # noqa: E402


class LifecycleCase(RegCase):
    """A Manager with the `fake` lane (a real ACP process) allowed own branches."""

    def setUp(self):
        RegCase.setUp(self)
        FakeLaneCase.setUp(self)
        self._lanes = mock.patch.dict(os.environ, {"CORRAL_LIGHT_WORKTREE_LANES": "fake",
                                                   "CORRAL_LIGHT_REVIEW_AT_END_LANES": "fake"})
        self._lanes.start()
        self.addCleanup(self._lanes.stop)

    _close_all = FakeLaneCase._close_all
    texts = FakeLaneCase.texts
    turn_ends = FakeLaneCase.turn_ends

    def pane(self, cwd=None, **kw):
        p = self.mgr.create("fake", str(cwd or self.repo), worktree=True, **kw)
        self.assertEqual(p.state, "ready", p.error)
        return p

    def say(self, p, text, timeout=15):
        n = self.turn_ends(p)
        p.send(text)
        self.assertTrue(wait_for(lambda: self.turn_ends(p) > n and p.state != "busy",
                                 timeout=timeout), self.texts(p)[-300:])

    def entry(self, p):
        return self.reg.read(p.worktree_id)

    def events(self, p, kind):
        return [e for e in p.events if e["kind"] == kind]


class LifecycleBasics(LifecycleCase):

    def test_T_LIF_1_2_worktree_id_round_trips_and_old_metas_load(self):
        p = self.pane()
        meta = wt.json.loads((p.dir / "meta.json").read_text())
        self.assertEqual(meta["worktree_id"], p.worktree_id)
        q = self.sessions.Pane.from_meta(meta, self.mgr)
        self.assertEqual(q.worktree_id, p.worktree_id)
        old = {k: v for k, v in meta.items() if k != "worktree_id"}
        self.assertIsNone(self.sessions.Pane.from_meta(old, self.mgr).worktree_id)

    def test_T_LIF_3_the_agent_runs_in_the_worktree_plus_subdir(self):
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "m.py").write_text("x\n")
        wt.git(["add", "-A"], cwd=self.repo)
        wt.git(["commit", "-qm", "pkg"], cwd=self.repo)
        p = self.pane(cwd=self.repo / "pkg")
        self.say(p, "pwd")
        e = self.entry(p)
        self.assertIn(os.path.realpath(Path(e["path"]) / "pkg"), self.texts(p))
        self.assertEqual(p.snapshot()["worktree"]["subdir"], "pkg")

    def test_T_LIF_4_a_full_roster_refuses_before_any_registry_entry_or_branch(self):
        with mock.patch.object(self.sessions, "MAX_PANES", 0):
            with self.assertRaises(ValueError):
                self.mgr.create("fake", str(self.repo), worktree=True)
        self.assertEqual(self.reg.all(), [])
        self.assertEqual(wt.git(["branch", "--list", "corral/*"], cwd=self.repo).text, "")

    def test_T_LIF_5_16_a_failed_start_leaves_a_dead_owner_and_resume_retries_there(self):
        real = self.sessions.AGENTS["fake"]
        broken = dict(real, env=dict(real["env"], FAKE_ACP_AUTH_FAIL="1"))
        bad_argv = dict(real, argv=[sys.executable, "-c", "import sys; sys.exit(3)"])
        self.sessions.AGENTS["fake"] = bad_argv
        p = self.mgr.create("fake", str(self.repo), worktree=True)
        self.assertEqual(p.state, "dead")
        self.assertIn(p.id, self.mgr.panes)
        self.assertEqual(self.entry(p)["phase"], "active")
        self.assertEqual(self.entry(p)["owner_pane"], p.id)
        self.sessions.AGENTS["fake"] = real
        p.resume()
        self.assertEqual(p.state, "ready", p.error)
        self.say(p, "pwd")
        self.assertIn(os.path.realpath(self.entry(p)["path"]), self.texts(p))
        del broken

    def test_T_LIF_6_the_manager_lock_is_never_held_during_git(self):
        held = []
        real = wt.git

        def spy(args, *a, **k):
            held.append(self.mgr._lock.locked())
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", spy):
            p = self.pane()
            self.say(p, "write a.txt hi")
            wait_for(lambda: p.worktree_summary is not None, timeout=10)
            self.mgr.worktree_snapshot(p.id)
        self.assertTrue(held)
        self.assertFalse(any(held), "Manager._lock was held during a git call")

    def test_T_LIF_7_a_turn_end_gives_one_worktree_event_and_a_failure_is_a_note(self):
        p = self.pane()
        self.say(p, "write a.txt changed")
        self.assertTrue(wait_for(lambda: len(self.events(p, "worktree")) == 1, timeout=10))
        s = self.events(p, "worktree")[0]["data"]["summary"]
        self.assertEqual((s["files"], s["added"], s["deleted"]), (1, 1, 1))
        with mock.patch.object(wt, "summary", side_effect=wt.GitError(["git"], 128, "boom")):
            self.say(p, "pwd")
            self.assertTrue(wait_for(lambda: any("could not count" in (e["data"] or {}).get("text", "")
                                                 for e in self.events(p, "note")), timeout=10))
        self.assertEqual(p.state, "ready")

    def test_T_LIF_8_no_git_call_on_the_observe_tick(self):
        p = self.pane()
        calls = []
        with mock.patch.object(wt, "git", lambda *a, **k: calls.append(a)):
            for _ in range(5):
                snap = p.snapshot()
                self.mgr.state() if hasattr(self.mgr, "state") else None
        self.assertEqual(calls, [])
        self.assertEqual(snap["worktree"]["branch"], "corral/" + Path(self.entry(p)["path"]).name)

    def test_T_LIF_9_after_a_restart_the_pane_resumes_on_its_branch(self):
        p = self.pane()
        self.say(p, "write a.txt before restart")
        meta = wt.json.loads((p.dir / "meta.json").read_text())
        self.mgr.panes[p.id].pause()
        q = self.sessions.Pane.from_meta(meta, self.mgr)
        self.mgr.panes[p.id] = q
        self.mgr._worktree_restore()
        self.mgr._wt_reconcile_thread.join(10)
        self.assertEqual(q.state, "detached")
        self.assertIsNone(q.worktree_blocked)
        q.resume()
        self.say(q, "pwd")
        self.assertIn(os.path.realpath(self.entry(q)["path"]), self.texts(q))

    def test_T_LIF_9b_after_a_restart_the_change_summary_is_recounted(self):
        p = self.pane()
        self.say(p, "write a.txt before restart")
        self.assertTrue(wait_for(lambda: (p.worktree_summary or {}).get("files")))
        meta = wt.json.loads((p.dir / "meta.json").read_text())
        self.mgr.panes[p.id].pause()
        q = self.sessions.Pane.from_meta(meta, self.mgr)
        self.assertIsNone(q.worktree_summary)          # memory only, lost with the hub
        self.mgr.panes[p.id] = q
        self.mgr._worktree_restore()
        self.mgr._wt_reconcile_thread.join(10)
        self.assertTrue(wait_for(lambda: (q.worktree_summary or {}).get("files")),
                        "no review card after a restart until the next turn ends")

    def test_T_LIF_10_a_missing_worktree_at_restart_refuses_resume_with_the_reason(self):
        p = self.pane()
        meta = wt.json.loads((p.dir / "meta.json").read_text())
        p.pause()
        import shutil
        shutil.rmtree(self.entry(p)["path"])          # the test's own temp tree
        q = self.sessions.Pane.from_meta(meta, self.mgr)
        self.mgr.panes[p.id] = q
        self.mgr._worktree_restore()
        self.mgr._wt_reconcile_thread.join(10)
        with self.assertRaises(ValueError) as cm:
            q.resume()
        self.assertIn("missing", str(cm.exception))

    def test_T_LIF_11_close_keeps_the_worktree_and_reopen_restores_it(self):
        p = self.pane()
        path = self.entry(p)["path"]
        self.mgr.close(p.id)
        self.assertTrue(Path(path).is_dir())
        q = self.mgr.reopen(p.id)
        self.assertEqual(q.worktree_id, p.worktree_id)
        self.assertIsNone(q.worktree_blocked)

    def test_T_LIF_12_port_of_a_worktree_pane_is_refused(self):
        p = self.pane()
        with self.assertRaises(ValueError) as cm:
            self.mgr.port(p.id, "fake", sha="x")
        self.assertIn("own branch", str(cm.exception))

    def test_T_LIF_13_lanes_that_cannot_use_an_own_branch_are_refused(self):
        for lane in ("host:box", "ollama", "gemini"):
            self.assertIsNotNone(self.sessions.worktree_refusal(lane), lane)
        self.assertIsNone(self.sessions.worktree_refusal("fake"))
        with mock.patch.dict(os.environ, {"CORRAL_LIGHT_WORKTREE_LANES": ""}):
            # The platform default decides; pin the platform so the test does too.
            with mock.patch.object(self.sessions.sys, "platform", "linux"):
                self.assertIsNone(self.sessions.worktree_refusal("claude"))
                self.assertIsNotNone(self.sessions.worktree_refusal("gemini"))
            with mock.patch.object(self.sessions.sys, "platform", "darwin"):
                why = self.sessions.worktree_refusal("claude")
                self.assertIn("lane matrix has not run", why or "")
        with mock.patch.dict(os.environ, {"CORRAL_LIGHT_WORKTREES_ENABLED": "0"}):
            with self.assertRaises(ValueError):
                self.mgr.create("fake", str(self.repo), worktree=True)

    def test_T_LIF_14_the_hub_writes_no_claude_trust_for_worktrees(self):
        src = (ROOT / "sessions.py").read_text() + (ROOT / "worktrees.py").read_text()
        self.assertNotIn("hasTrustDialogAccepted", src)

    def test_T_LIF_15_concurrent_creates_at_the_cap_admit_one(self):
        out, errs = [], []
        live = len([p for p in self.mgr.panes.values() if p.state not in ("dead", "detached")])

        def go():
            try:
                out.append(self.mgr.create("fake", str(self.repo), worktree=True))
            except ValueError as e:
                errs.append(e)
        with mock.patch.object(self.sessions, "MAX_PANES", live + 1):
            ts = [threading.Thread(target=go) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
        self.assertEqual((len(out), len(errs)), (1, 1))
        self.assertEqual(len(self.reg.all()), 1)

    def test_T_LIF_17_a_plain_create_inside_the_root_is_refused(self):
        p = self.pane()
        with self.assertRaises(ValueError):
            self.mgr.create("fake", self.entry(p)["path"])

    def test_T_LIF_18_the_preamble_comes_first_and_once(self):
        p = self.pane()
        self.say(p, "hello")
        self.say(p, "again")
        t = self.texts(p)
        self.assertTrue(t.startswith("preamble received; echo: hello"), t[:120])
        self.assertEqual(t.count("preamble received"), 1)
        self.assertNotIn("[Corral]", " ".join((e["data"] or {}).get("text", "")
                                              for e in self.events(p, "user")))

    def test_T_LIF_19_an_edit_outside_the_worktree_stops_the_turn(self):
        p = self.pane()
        e = self.entry(p)
        outside = str(self.tmp / "elsewhere.txt")
        admin = str(Path(e["common_dir"]) / "worktrees" / e["admin_name"] / "index")
        inside = "notes.txt"
        for path in (inside, admin):
            self.say(p, f"tool-edit {path}", timeout=10)
        self.say(p, f"tool-read {outside}", timeout=10)
        self.assertEqual([x for x in self.events(p, "worktree") if "escape" in x["data"]], [])
        t0 = time.monotonic()
        self.say(p, f"tool-edit {outside}", timeout=10)
        self.assertLess(time.monotonic() - t0, 2.5, "the turn was not cancelled")
        [esc] = [x for x in self.events(p, "worktree") if "escape" in x["data"]]
        self.assertEqual(esc["data"]["escape"]["paths"], [outside])
        self.assertTrue(any("outside its own branch" in (n["data"] or {}).get("text", "")
                            for n in self.events(p, "note")))

    def test_allow_always_is_never_offered_on_a_worktree_pane(self):
        p = self.pane()
        p.send("perm-always")
        self.assertTrue(wait_for(lambda: p.pending, timeout=10))
        [perm] = self.events(p, "permission")
        kinds = [o["kind"] for o in perm["data"]["options"]]
        self.assertNotIn("allow_always", kinds)
        rid = next(iter(p.pending))
        with self.assertRaises(ValueError):
            p.answer(rid, "always", perm["data"]["digest"])
        p.answer(rid, "deny", perm["data"]["digest"])


class LifecycleSafety(LifecycleCase):

    def test_T_SNP_4_review_is_refused_mid_turn(self):
        p = self.pane()
        p.send("sleep 5")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_snapshot(p.id)
        self.assertEqual(cm.exception.reason, "busy")
        p.cancel()

    def test_T_SAFE_1_an_agent_write_between_review_and_commit_refuses(self):
        p = self.pane()
        self.say(p, "write a.txt reviewed")
        snap = self.mgr.worktree_snapshot(p.id)
        self.say(p, "write a.txt sneaky")
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        self.assertEqual(cm.exception.reason, "changed")

    def test_T_SAFE_2_a_background_writer_after_the_turn_blocks_commit(self):
        p = self.pane()
        self.say(p, "bg-write-setsid bg.txt 40 0.05")
        snap = self.mgr.worktree_snapshot(p.id)
        time.sleep(0.3)
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        self.assertEqual(cm.exception.reason, "changed")

    def test_T_SAFE_3_a_send_during_an_action_is_queued_then_delivered(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        snap = self.mgr.worktree_snapshot(p.id)
        real = wt.commit_tree
        sent = []

        def slow(*a, **k):
            sent.append(p.send("during commit"))
            time.sleep(0.3)
            self.assertNotIn("echo: during commit", self.texts(p))
            return real(*a, **k)
        with mock.patch.object(wt, "commit_tree", slow):
            self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        self.assertTrue(wait_for(lambda: "echo: during commit" in self.texts(p), timeout=10))

    def test_T_SAFE_4_two_actions_at_once_the_second_is_busy(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        gate = threading.Event()
        real = wt.snapshot
        errs = []

        def slow(*a, **k):
            gate.wait(5)
            return real(*a, **k)
        with mock.patch.object(wt, "snapshot", slow):
            t = threading.Thread(target=lambda: self.mgr.worktree_snapshot(p.id))
            t.start()
            time.sleep(0.2)
            try:
                self.mgr.worktree_snapshot(p.id)
            except wt.Refused as e:
                errs.append(e.reason)
            gate.set()
            t.join(10)
        self.assertEqual(errs, ["busy"])

    def test_T_SAFE_5_the_main_checkout_is_untouched_by_every_action(self):
        remote = self.tmp / "remote.git"
        wt.git(["init", "-q", "--bare", str(remote)], cwd=self.tmp)
        wt.git(["remote", "add", "origin", str(remote)], cwd=self.repo)
        (self.repo / "untracked-in-main.txt").write_text("mine\n")
        idx = self.repo / ".git" / "index"
        before = (idx.read_bytes(), (self.repo / ".git" / "HEAD").read_bytes(),
                  wt.git(["ls-files", "--others", "-z"], cwd=self.repo).out)
        decoy = self.tmp / "decoy-index"
        with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(decoy)}):
            p = self.pane()
            self.say(p, "write a.txt changed")
            s = self.mgr.worktree_snapshot(p.id)
            c = self.mgr.worktree_commit(p.id, s["tree"], s["head"], s["index_id"], "m")
            self.mgr.worktree_publish(p.id, c["commit"], s["tree"], "origin", str(remote))
            self.say(p, "write b.txt more")
            s2 = self.mgr.worktree_snapshot(p.id)
            self.mgr.worktree_discard(p.id, s2["tree"])
        after = (idx.read_bytes(), (self.repo / ".git" / "HEAD").read_bytes(),
                 wt.git(["ls-files", "--others", "-z"], cwd=self.repo).out)
        self.assertEqual(before, after)
        self.assertFalse(decoy.exists())

    def test_T_RMV_11_discard_ends_the_agent_and_its_group_first(self):
        """A writer in the agent's group keeps changing files after review. The
        first Discard stops the agent (and so the writer), then refuses because
        the files moved on; review refreshes, and the second Discard succeeds."""
        p = self.pane()
        self.say(p, "bg-write bg.txt 400 0.05")
        [bg] = [int(f.name[3:]) for f in Path(self.agent_dir).glob("bg-*")]
        snap = self.mgr.worktree_snapshot(p.id)
        time.sleep(0.2)
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(cm.exception.reason, "changed")
        self.assertTrue(wait_for(lambda: not _alive(bg), timeout=5),
                        "the agent's background child survived")
        fresh = self.mgr.worktree_snapshot(p.id)
        r = self.mgr.worktree_discard(p.id, fresh["tree"])
        self.assertTrue(Path(r["trash_path"], "bg.txt").exists())
        with self.assertRaises(ValueError):
            p.resume()

    def test_T_RMV_11b_discard_kills_a_live_agents_group(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        agent_pids = [int(f.name[4:]) for f in Path(self.agent_dir).glob("pid-*")]
        snap = self.mgr.worktree_snapshot(p.id)
        self.mgr.worktree_discard(p.id, snap["tree"])
        for pid in agent_pids:
            self.assertFalse(_alive(pid))
        self.assertEqual(self.entry(p)["phase"], "trashed")

    def test_T_RMV_4b_restored_from_another_process_the_pane_resumes(self):
        """The CLI restores through the registry, not the hub: Resume must read
        the entry as it is now, not the reason cached at discard."""
        p = self.pane()
        self.say(p, "write a.txt kept")
        snap = self.mgr.worktree_snapshot(p.id)
        self.mgr.worktree_discard(p.id, snap["tree"])
        with self.assertRaises(ValueError):
            p.resume()
        wt.restore(self.entry(p), registry=wt.Registry())     # as `corral-light worktrees restore`
        p.resume()
        self.assertEqual(p.state, "ready", p.error)
        self.assertEqual(Path(self.entry(p)["path"], "a.txt").read_text().strip(), "kept")

    def test_T_RMV_14_a_message_sent_during_discard_is_never_dispatched(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        snap = self.mgr.worktree_snapshot(p.id)
        real = wt.discard

        def with_send(*a, **k):
            with contextlib.suppress(Exception):
                p._queue.append(self.sessions._QueuedText("during discard", "t-x"))
            return real(*a, **k)
        import contextlib
        with mock.patch.object(wt, "discard", with_send):
            self.mgr.worktree_discard(p.id, snap["tree"])
        time.sleep(0.3)
        self.assertNotIn("echo: during discard", self.texts(p))
        self.assertTrue(any("not be sent" in (e["data"] or {}).get("text", "")
                            for e in self.events(p, "note")))


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



class TheMacAddendum(unittest.TestCase):
    """docs/worktree-plan-macos.md M1-M4, checked here by forcing the darwin paths.

    The real proof is Phase 0.7 on the Mac host; these keep the code honest until then.
    """

    def setUp(self):
        import sessions
        self.sessions = sessions
        env = {k: v for k, v in os.environ.items() if k != "CORRAL_LIGHT_WORKTREE_LANES"}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)

    def platform(self, name):
        return mock.patch.object(self.sessions.sys, "platform", name)

    def test_M2_lanes_are_enabled_per_platform(self):
        with self.platform("linux"):
            for lane in ("claude", "codex", "grok"):
                self.assertIsNone(self.sessions.worktree_refusal(lane), lane)
        with self.platform("darwin"):
            for lane in ("claude", "codex", "grok"):
                why = self.sessions.worktree_refusal(lane)
                self.assertIsNotNone(why, lane)
                self.assertIn("macOS", why)
                self.assertIn("lane matrix", why)
        with self.platform("win32"):
            self.assertIsNotNone(self.sessions.worktree_refusal("claude"))

    def test_M2_an_explicit_lane_list_still_opts_in_for_the_matrix_run(self):
        with self.platform("darwin"), mock.patch.dict(os.environ,
                                                      {"CORRAL_LIGHT_WORKTREE_LANES": "claude"}):
            self.assertIsNone(self.sessions.worktree_refusal("claude"))
            self.assertIsNotNone(self.sessions.worktree_refusal("codex"))


class TheMacDiscardScan(CreateCase):
    """M1: off Linux there is no /proc; discard scans with lsof and fails closed."""

    def setUp(self):
        super().setUp()
        self.e = self.make()
        self.p = Path(self.e["path"])
        self._noproc = mock.patch.object(wt, "_have_proc", lambda: False)
        self._noproc.start()
        self.addCleanup(self._noproc.stop)
        if not wt._lsof_bin():
            self.skipTest("lsof absent: the darwin scan path did NOT run here")

    def child(self, argv, cwd):
        import subprocess
        c = subprocess.Popen(argv, cwd=cwd, start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (c.kill(), c.wait()))
        return c

    def discard(self):
        snap = wt.snapshot(self.e)
        return wt.discard(self.e, snap["tree"], registry=self.reg)

    def test_M1_T_RMV_13_darwin_a_process_whose_cwd_is_inside_blocks_discard(self):
        c = self.child(["sleep", "30"], self.p)
        self.assertTrue(wait_for(lambda: any(pid == c.pid for pid, _ in wt.processes_in(self.p)), 5))
        with self.assertRaises(wt.Refused) as cm:
            self.discard()
        self.assertEqual(cm.exception.reason, "busy")
        self.assertIn(str(c.pid), cm.exception.detail)
        self.assertIn("sleep", cm.exception.detail)
        self.assertTrue(self.p.exists())

    def test_M1_an_open_file_inside_counts_too(self):
        import sys
        (self.p / "held.txt").write_text("x\n")
        c = self.child([sys.executable, "-c",
                        "import sys,time; f=open(sys.argv[1]); print('open', flush=True); time.sleep(30)",
                        str(self.p / "held.txt")], self.tmp)
        c.stdout.readline()
        self.assertIn(c.pid, [pid for pid, _ in wt.processes_in(self.p)])

    def test_M1_a_quiet_worktree_scans_clean_and_discards(self):
        self.assertEqual(wt.processes_in(self.p), [])
        r = self.discard()
        self.assertTrue(Path(r["trash_path"]).is_dir())

    def stub_lsof(self, body):
        stub = self.stub_git(body)
        return mock.patch.object(wt, "_lsof_bin", lambda: stub)

    def test_M1_a_failed_scan_refuses_and_moves_nothing(self):
        for body in ('echo "lsof: status error on x: Permission denied" >&2; exit 1',
                     "exit 2"):
            with self.stub_lsof(body):
                with self.assertRaises(wt.Refused) as cm:
                    self.discard()
            self.assertEqual(cm.exception.reason, "busy", body)
            self.assertIn("could not check", cm.exception.detail)
            self.assertTrue(self.p.exists(), body)
            self.assertEqual(self.reg.read(self.e["id"])["phase"], "active")

    def test_M1_a_scan_that_times_out_refuses(self):
        with self.stub_lsof("sleep 10"), mock.patch.object(wt, "SCAN_TIMEOUT_S", 1):
            with self.assertRaises(wt.Refused) as cm:
                self.discard()
        self.assertIn("could not check", cm.exception.detail)
        self.assertTrue(self.p.exists())

    def test_M1_no_lsof_at_all_refuses(self):
        with mock.patch.object(wt, "_lsof_bin", lambda: None):
            with self.assertRaises(wt.Refused) as cm:
                self.discard()
        self.assertIn("lsof", cm.exception.detail)

    def test_M1_on_linux_an_unreadable_proc_fails_closed_too(self):
        # Take the /proc path whatever this host is, so the test runs on macOS too.
        self._noproc.stop()
        with mock.patch.object(wt, "_have_proc", lambda: True), \
                mock.patch.object(wt.os, "listdir", side_effect=PermissionError("no")):
            with self.assertRaises(wt.ScanFailed):
                wt.processes_in(self.p)
        self._noproc.start()


class TheMacPaths(CreateCase):
    """M3: letter case on case-insensitive APFS, and /private temp paths."""

    def test_M3_T_CRT_8_one_repo_under_two_letter_cases_gives_one_repo_dir(self):
        pr = wt.probe(self.repo)
        other = dict(pr, common_dir=str(self.repo / ".GIT"), repo_top=str(self.repo))
        with mock.patch.object(wt.sys, "platform", "darwin"):
            self.assertEqual(wt._true_case(str(self.repo / ".GIT")), str(self.repo / ".git"))
            self.assertEqual(wt.repo_dir(other), wt.repo_dir(pr))
        with mock.patch.object(wt.sys, "platform", "linux"):
            self.assertNotEqual(wt.repo_dir(other), wt.repo_dir(pr),
                                "Linux is case-sensitive: two spellings are two paths")

    def test_M3_true_case_prefers_an_exact_match_and_leaves_the_unknown_alone(self):
        (self.tmp / "ab").mkdir()
        try:
            (self.tmp / "AB").mkdir()
            both = True
        except FileExistsError:
            # A case-insensitive volume (default APFS) holds one of the two;
            # there the exact spelling is absent and the on-disk case wins.
            both = False
        with mock.patch.object(wt.sys, "platform", "darwin"):
            want = self.tmp / ("AB" if both else "ab")
            self.assertEqual(wt._true_case(str(self.tmp / "AB")), str(want))
            self.assertEqual(wt._true_case(str(self.tmp / "nope" / "x")), str(self.tmp / "nope" / "x"))

    def test_M3_the_suite_realpaths_its_temp_roots(self):
        self.assertEqual(str(self.tmp), os.path.realpath(self.tmp),
                         "on macOS mkdtemp is behind /var -> /private/var; T-CRT-6 would refuse")


class TheMacClaudeHome(LifecycleCase):
    """M4: on macOS Claude panes share the real ~/.claude; the hub must not touch it."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".claude" / "projects").mkdir(parents=True)
        (self.home / ".claude.json").write_text('{"projects": {}, "numStartups": 3}\n')
        (self.home / ".claude" / "settings.json").write_text('{"permissions": {}}\n')
        h = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        h.start()
        self.addCleanup(h.stop)
        real = self.sessions.AGENTS["fake"]
        # A Claude-shaped lane: posture through a config dir, which darwin cannot give.
        self.sessions.AGENTS["fake"] = dict(real, posture_via_config_dir=True)

    def test_M4_T_LIF_14_darwin_the_real_claude_home_is_byte_identical(self):
        before = (_tree_snapshot(self.home / ".claude"),
                  (self.home / ".claude.json").read_bytes())
        with mock.patch.object(self.sessions.sys, "platform", "darwin"):
            p = self.pane()
            self.assertNotIn("CLAUDE_CONFIG_DIR", self.sessions.spawn_env(
                self.sessions.AGENTS["fake"], self.sessions.seed_config_dir(self.tmp / "cfg", "auto")))
            self.say(p, "write a.txt from a worktree")
            snap = self.mgr.worktree_snapshot(p.id)
            self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        after = (_tree_snapshot(self.home / ".claude"),
                 (self.home / ".claude.json").read_bytes())
        self.assertEqual(after, before)


# ── Bug bash 2026-10-02, round 1 (reviews/2026-10-02-own-branch-bugbash) ──────
# Each test below was written from a reviewer's repro and failed at b6f7dfc.

def _unknown_op(reg, wid, kind="commit"):
    op = reg.begin_op(wid, kind, stage="ref_moved")
    reg.set_op(wid, op, state="unknown")
    return op


def _staged_blob(p, rel):
    return wt.git(["rev-parse", ":" + rel], cwd=p).text.strip()


def _reachable(p, oid):
    objs = wt.git(["rev-list", "--objects", "--all", "--reflog"], cwd=p,
                  max_out=64 << 20).text
    return oid in objs or wt.git(["rev-parse", ":a.txt"], cwd=p).text.strip() == oid


# A discard killed right after its first registry write once the move is done.
CRASH_AFTER_MOVE = r"""
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import worktrees as wt
reg = wt.Registry()
e = reg.read(sys.argv[2])
snap = wt.snapshot(e)
dst_prefix = str(wt.trash_dir() / e["id"])
real = wt.atomic_write_json

def write_then_die(path, obj):
    real(path, obj)
    moved = any(str(c).startswith(dst_prefix) for c in wt.trash_dir().iterdir()) \
        if wt.trash_dir().is_dir() else False
    if moved and str(path).endswith(e["id"] + ".json"):
        os.kill(os.getpid(), signal.SIGKILL)
wt.atomic_write_json = write_then_die
wt.discard(e, snap["tree"])
"""


class BugBashLibrary(CreateCase):
    """worktrees.py findings: the gate, the index swap, discard, purge, create."""

    # Converged 1: an `unknown` or `intent` op blocks every mutating action.
    def test_BB_1_actions_refuse_while_an_op_is_unknown(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("despite unknown\n")
        snap = wt.snapshot(e)
        _unknown_op(self.reg, e["id"])
        e = self.reg.read(e["id"])
        with self.assertRaises(wt.Refused) as cm:
            wt.commit_tree(e, snap["tree"], snap["index_id"], "m", expect_head=snap["head"],
                           registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")
        with self.assertRaises(wt.Refused) as cm:
            wt.discard(e, snap["tree"], registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")
        self.assertTrue(p.exists())
        self.assertEqual(wt.git(["rev-parse", e["branch"]], cwd=p).text.strip(), e["base_sha"])

    def test_BB_1b_an_intent_op_from_another_process_also_blocks(self):
        e = self.make()
        (Path(e["path"]) / "a.txt").write_text("x\n")
        snap = wt.snapshot(e)
        self.reg.begin_op(e["id"], "commit", stage="prepared")
        with self.assertRaises(wt.Refused) as cm:
            wt.commit_tree(self.reg.read(e["id"]), snap["tree"], snap["index_id"], "m",
                           expect_head=snap["head"], registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")

    # Converged 2 (Astra): work staged while the hub was down is never replaced.
    def test_BB_2_restart_never_replaces_an_index_staged_while_the_hub_was_down(self):
        import subprocess
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("reviewed\n")
        env = dict(os.environ, CORRAL_WT_TEST="1", CORRAL_WT_CRASH_AT="commit:ref_moved")
        r = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(ROOT), e["id"], "commit"],
                           env=env, capture_output=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stderr.decode()[-500:])
        (p / "a.txt").write_text("unique staged content\n")
        wt.git(["add", "a.txt"], cwd=p)
        blob = _staged_blob(p, "a.txt")
        (p / "a.txt").write_text("reviewed\n")
        wt.reconcile(self.reg)
        self.assertEqual(_staged_blob(p, "a.txt"), blob, "the staged work was replaced")
        self.assertEqual(self.reg.read(e["id"])["ops"][-1]["state"], "unknown")

    # Converged 2 (Astra, live): staging between the check and the swap.
    def test_BB_3_staging_during_the_swap_is_never_lost(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("reviewed\n")
        snap = wt.snapshot(e)
        real = wt._reconcile_index
        blobs = []

        def stage_first(*a, **k):
            (p / "a.txt").write_text("unique staged content\n")
            wt.git(["add", "a.txt"], cwd=p)
            blobs.append(_staged_blob(p, "a.txt"))
            (p / "a.txt").write_text("reviewed\n")
            return real(*a, **k)
        with mock.patch.object(wt, "_reconcile_index", stage_first):
            with self.assertRaises(wt.Refused) as cm:
                wt.commit_tree(e, snap["tree"], snap["index_id"], "m",
                               expect_head=snap["head"], registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")
        self.assertEqual(_staged_blob(p, "a.txt"), blobs[0], "the staged work was replaced")
        self.assertEqual(self.reg.read(e["id"])["ops"][-1]["state"], "unknown")

    # Converged 2 (Grok): a failed swap after update-ref is `unknown`, not a stuck intent.
    def test_BB_4_a_failed_index_swap_after_the_ref_moved_is_unknown(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("reviewed line\n")
        snap = wt.snapshot(e)

        calls = []

        def boom(idx, content, expect_id=None):     # the real signature (round 2)
            calls.append(expect_id)
            raise wt.Refused("busy", "index.lock appeared while swapping the index")
        with mock.patch.object(wt, "_replace_index", boom):
            with self.assertRaises(wt.Refused) as cm:
                wt.commit_tree(e, snap["tree"], snap["index_id"], "feat",
                               expect_head=snap["head"], registry=self.reg)
        self.assertEqual(calls, [snap["index_id"]], "the stub never ran")
        self.assertEqual(cm.exception.reason, "unknown")
        op = self.reg.read(e["id"])["ops"][-1]
        self.assertEqual(op["state"], "unknown", op)

    # Converged 2 (Grok): one entry that cannot be resolved does not stop the rest.
    def test_BB_5_one_failing_entry_does_not_abort_reconcile(self):
        a = self.make(title="first", owner="pane0001")
        b = self.make(title="second", owner="pane0002")
        self.reg.begin_op(a["id"], "push", url="https://example.invalid/x.git",
                          ref=a["branch"], oid=a["base_sha"])
        missing = Path(b["path"])
        import shutil
        shutil.rmtree(missing)                      # the test's own temp tree
        real = wt.git

        def timeout(args, *x, **k):
            if args and args[0] == "ls-remote":
                raise wt.GitTimeout(["git", "ls-remote"], 1)
            return real(args, *x, **k)
        with mock.patch.object(wt, "git", timeout):
            notes = wt.reconcile(self.reg)
        self.assertEqual(self.reg.read(b["id"])["phase"], "missing", notes)
        self.assertEqual(self.reg.read(a["id"])["ops"][-1]["state"], "unknown")

    # Single seat (Astra): op `done` and phase `trashed` land in one write.
    def test_BB_6_discard_killed_after_its_first_post_move_write_stays_restorable(self):
        import subprocess
        e = self.make()
        (Path(e["path"]) / "w.txt").write_text("work to keep\n")
        r = subprocess.run([sys.executable, "-c", CRASH_AFTER_MOVE, str(ROOT), e["id"]],
                           env=dict(os.environ), capture_output=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stderr.decode()[-500:])
        wt.reconcile(self.reg)
        got = self.reg.read(e["id"])
        self.assertEqual(got["phase"], "trashed", got)
        back = wt.restore(got, registry=self.reg)
        self.assertEqual(Path(back["path"], "w.txt").read_text(), "work to keep\n")

    # Single seat (Gemini): an interrupted purge still deletes the branch.
    def test_BB_7_purge_resolved_after_a_restart_deletes_the_branch(self):
        e = self.make()
        snap = wt.snapshot(e)
        wt.discard(e, snap["tree"], registry=self.reg)
        e = self.reg.read(e["id"])
        wt.git(["worktree", "remove", "--force", "--", e["trash_path"]], cwd=self.repo)
        self.reg.begin_op(e["id"], "purge", path=e["trash_path"])   # as a SIGKILL left it
        wt.reconcile(self.reg)
        self.assertEqual(self.reg.read(e["id"])["phase"], "purged")
        self.assertIsNone(wt._ref(e, e["branch"]), "the corral branch was left behind")

    # Single seat (Grok): a failing post-checkout hook does not orphan a live worktree.
    def test_BB_8_a_failing_post_checkout_hook_leaves_a_usable_worktree(self):
        hook = self.repo / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\necho hook says no >&2\nexit 1\n")
        hook.chmod(0o755)
        e = self.make()
        self.assertEqual(e["phase"], "active")
        self.assertIn("hook says no", e.get("warning") or "")
        wt.verify(e)

    # Single seat (Gemini): a lock our own stopped agent left is cleared, not waited out.
    def test_BB_9_a_stale_lock_from_our_stopped_agent_does_not_block_discard(self):
        import subprocess
        e = self.make()
        snap = wt.snapshot(e)
        gone = subprocess.Popen(["true"])
        gone.wait()
        lock = wt._index_path(e["path"]) + ".lock"
        Path(lock).write_bytes(b"partial")
        rec = {"pid": gone.pid, "start": "x", "lock": lock, "stopped_at": time.time()}
        with mock.patch.object(wt, "LOCK_WAIT_S", 0.3):
            r = wt.discard(e, snap["tree"], registry=self.reg, stale_lock=rec)
        self.assertTrue(Path(r["trash_path"]).exists())
        self.assertFalse(os.path.exists(lock))

    def test_BB_9b_a_lock_whose_owner_is_alive_is_never_cleared(self):
        e = self.make()
        snap = wt.snapshot(e)
        lock = wt._index_path(e["path"]) + ".lock"
        Path(lock).write_bytes(b"partial")
        rec = {"pid": os.getpid(), "start": "x", "lock": lock, "stopped_at": time.time()}
        with mock.patch.object(wt, "LOCK_WAIT_S", 0.3):
            with self.assertRaises(wt.Refused) as cm:
                wt.discard(e, snap["tree"], registry=self.reg, stale_lock=rec)
        self.assertEqual(cm.exception.reason, "busy")
        self.assertEqual(Path(lock).read_bytes(), b"partial")


class BugBashHub(LifecycleCase):
    """sessions.py findings: the gate on sends, Discard's order, Forget."""

    # Converged 1 (Astra, Grok): no review action runs on an `unknown` op.
    def test_BB_10_every_review_action_refuses_while_an_op_is_unknown(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        snap = self.mgr.worktree_snapshot(p.id)
        _unknown_op(self.reg, p.worktree_id)
        acts = {
            "snapshot": lambda: self.mgr.worktree_snapshot(p.id),
            "commit": lambda: self.mgr.worktree_commit(p.id, snap["tree"], snap["head"],
                                                       snap["index_id"], "m"),
            "publish": lambda: self.mgr.worktree_publish(p.id, snap["head"], snap["tree"],
                                                         "origin", "x"),
            "discard": lambda: self.mgr.worktree_discard(p.id, snap["tree"]),
        }
        for name, act in acts.items():
            with self.subTest(action=name):
                with self.assertRaises(wt.Refused) as cm:
                    act()
                self.assertEqual(cm.exception.reason, "unknown")
        self.assertEqual(p.state, "ready", "a refused action stopped the agent")
        self.assertTrue(p.client and p.client.alive)

    # Converged 1 (Astra): nothing is dispatched into a pane whose op is unknown.
    def test_BB_11_a_send_to_a_pane_with_an_unknown_op_never_runs(self):
        p = self.pane()
        _unknown_op(self.reg, p.worktree_id)
        with self.assertRaises(ValueError):
            p.send("write unknown.txt writing while blocked")
        time.sleep(0.3)
        self.assertFalse(Path(self.entry(p)["path"], "unknown.txt").exists())

    # Converged 4 (Astra, PROVEN): a real send during discard never reaches the trash.
    def test_BB_12_a_real_send_during_discard_never_writes_into_trash(self):
        p = self.pane()
        self.say(p, "write a.txt x")
        snap = self.mgr.worktree_snapshot(p.id)
        real = wt.processes_in

        def scan_then_send(path):
            found = real(path)
            try:
                p.send("write AFTER_DISCARD.txt this should be impossible")
            except Exception:                    # noqa: BLE001 — refusing is fine
                pass
            return found
        with mock.patch.object(wt, "processes_in", scan_then_send):
            r = self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertFalse(p.client and p.client.alive, "an agent survived the discard")
        with self.assertRaises(Exception):
            p.send("write AFTER_DISCARD.txt this should be impossible")
        time.sleep(0.5)
        self.assertFalse(Path(r["trash_path"], "AFTER_DISCARD.txt").exists())

    # Converged 3 (Grok, Gemini): a refused discard leaves the agent running.
    def test_BB_13_a_discard_refused_by_an_outside_process_leaves_the_agent_alone(self):
        import subprocess
        p = self.pane()
        self.say(p, "write a.txt x")
        snap = self.mgr.worktree_snapshot(p.id)
        outsider = subprocess.Popen(["sleep", "30"], cwd=self.entry(p)["path"],
                                    start_new_session=True)
        self.addCleanup(outsider.wait)
        self.addCleanup(outsider.kill)
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(cm.exception.reason, "busy")
        self.assertIn(str(outsider.pid), str(cm.exception.detail))
        self.assertEqual(p.state, "ready")
        self.assertTrue(p.client and p.client.alive, "the refused discard killed the agent")
        self.say(p, "write b.txt still working")

    # Single seat (Gemini): Forget on an own-branch pane offers Discard first.
    def test_BB_14_forget_on_an_own_branch_pane_asks_first(self):
        p = self.pane()
        p.client.close()
        p.state = "dead"
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.forget(p.id)
        self.assertEqual(cm.exception.reason, "own_branch")
        self.assertIn(p.id, self.mgr.panes)
        self.mgr.forget(p.id, keep_branch=True)
        self.assertNotIn(p.id, self.mgr.panes)
        self.assertEqual(self.reg.read(p.worktree_id)["phase"], "active")


# ── round 2 bug bash (reviews/2026-10-02-own-branch-bugbash/r2-synthesis.md) ──

def _hold_lock_open(test, idx, cwd):
    """A real git that holds `idx`.lock open from OUTSIDE the worktree."""
    import subprocess
    live = subprocess.Popen(["git", "--git-dir=" + str(Path(idx).parent), "update-index",
                             "--index-info"], cwd=cwd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=wt.git_env())
    test.addCleanup(lambda: live.communicate(timeout=5))
    test.assertTrue(wait_for(lambda: os.path.exists(idx + ".lock")), "git did not take index.lock")
    return live


class BugBash2Library(CreateCase):
    """worktrees.py findings from round 2."""

    # Both seats: restore moved a trashed worktree while an op was unsettled.
    def test_BB2_1_restore_refuses_while_an_op_is_unknown(self):
        e = self.make()
        wt.discard(e, wt.snapshot(e)["tree"], registry=self.reg)
        e = self.reg.read(e["id"])
        op = self.reg.begin_op(e["id"], "purge", path=e["trash_path"])
        self.reg.set_op(e["id"], op, state="unknown")
        with self.assertRaises(wt.Refused) as cm:
            wt.restore(self.reg.read(e["id"]), registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")
        self.assertEqual(self.reg.read(e["id"])["phase"], "trashed")
        self.assertTrue(Path(e["trash_path"]).is_dir())

    # Astra: a commit journal written before index_id existed lost staged work.
    def test_BB2_2_an_old_commit_journal_never_rebuilds_the_index(self):
        e = self.make()
        p = Path(e["path"])
        (p / "a.txt").write_text("reviewed\n")
        snap = wt.snapshot(e)
        new = wt.git(["commit-tree", snap["tree"], "-p", snap["head"]], cwd=p,
                     input=b"reviewed commit\n").text.strip()
        op = self.reg.begin_op(e["id"], "commit", stage="ref_moved", new=new,
                               expect_old=snap["head"], tree=snap["tree"])   # no index_id
        wt.git(["update-ref", e["branch"], new, snap["head"]], cwd=p)
        (p / "a.txt").write_text("unique staged work during downtime\n")
        wt.git(["add", "a.txt"], cwd=p)
        blob = _staged_blob(p, "a.txt")
        wt.reconcile(self.reg)
        self.assertEqual(_staged_blob(p, "a.txt"), blob, "staged work was overwritten")
        got = [o for o in self.reg.read(e["id"])["ops"] if o["op_id"] == op][0]
        self.assertEqual(got["state"], "unknown")

    # Astra: an exception in the post-update-ref check left the op `intent`.
    def test_BB2_3_a_failing_post_commit_check_is_unknown(self):
        e = self.make()
        (Path(e["path"]) / "a.txt").write_text("review\n")
        snap = wt.snapshot(e)
        real = wt.git

        def git(args, *a, **k):
            if args == ["rev-parse", e["branch"]]:
                raise wt.GitTimeout(["git", *args], 20)
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", git):
            with self.assertRaises(wt.Refused) as cm:
                wt.commit_tree(e, snap["tree"], snap["index_id"], "review", snap["head"],
                               registry=self.reg)
        self.assertEqual(cm.exception.reason, "unknown")
        self.assertEqual(self.reg.read(e["id"])["ops"][-1]["state"], "unknown")

    # Astra: a repository scan that raised aborted reconcile for every repository.
    def test_BB2_4_a_failing_repository_scan_flags_its_entries_and_goes_on(self):
        e = self.make()
        op = self.reg.begin_op(e["id"], "commit", stage="prepared")
        other = self.tmp / "other"
        other.mkdir()
        wt.git(["init", "-q", "-b", "main"], cwd=other)
        (other / "b.txt").write_text("b\n")
        wt.git(["add", "-A"], cwd=other)
        wt.git(["commit", "-qm", "base"], cwd=other)
        f = self.make(title="second", owner="pane0002", cwd=other)
        import shutil
        shutil.rmtree(f["path"])                     # the test's own temp tree
        real = wt._registered

        def boom(common):
            if os.path.realpath(common) == os.path.realpath(e["common_dir"]):
                raise wt.GitTimeout(["git", "worktree", "list"], 20)
            return real(common)
        with mock.patch.object(wt, "_registered", boom):
            notes = wt.reconcile(self.reg)
        got = [o for o in self.reg.read(e["id"])["ops"] if o["op_id"] == op][0]
        self.assertEqual(got["state"], "unknown", "an op that could not be checked stayed intent")
        self.assertEqual(self.reg.read(e["id"])["phase"], "active", "a scan failure is not 'missing'")
        self.assertEqual(self.reg.read(f["id"])["phase"], "missing", "the other repo was never checked")
        self.assertTrue(any(n["kind"] == "error" and n["id"] == e["id"] for n in notes), notes)

    # Astra: a lock a live process holds open was set aside on a dead pid's say-so.
    def test_BB2_5_a_lock_a_live_process_holds_is_never_set_aside(self):
        e = self.make()
        idx = wt._index_path(e["path"])
        live = _hold_lock_open(self, idx, self.tmp)
        rec = {"pid": 999999, "start": "proc:1", "lock": idx + ".lock",
               "stopped_at": time.time() + 60}
        os.utime(idx + ".lock", (time.time() - 30, time.time() - 30))
        self.assertFalse(wt._set_aside_stale_lock(idx, rec))
        self.assertTrue(os.path.exists(idx + ".lock"))
        self.assertIsNone(live.poll())

    def test_BB2_6_an_abandoned_lock_is_set_aside_without_a_writer_pid(self):
        e = self.make()
        idx = wt._index_path(e["path"])
        Path(idx + ".lock").write_bytes(b"left by a killed agent")
        os.utime(idx + ".lock", (time.time() - 30, time.time() - 30))
        self.assertTrue(wt._set_aside_stale_lock(idx, {"lock": idx + ".lock",
                                                       "stopped_at": time.time()}))
        self.assertFalse(os.path.exists(idx + ".lock"))


class BugBash2Publish(PubCase):
    stub_gh = ThePullRequest.stub_gh
    pr = ThePullRequest.pr

    # Gemini: with a PR already open, open_pr skipped the gate and wrote the registry.
    def test_BB2_7_open_pr_refuses_an_unsettled_op_even_when_the_pr_exists(self):
        wt.git(["remote", "set-url", "origin", "https://github.com/o/r.git"], cwd=self.repo)
        gh, state = self.stub_gh()
        (state / "pr").write_text('[{"url":"https://github.com/o/r/pull/7"}]')
        _unknown_op(self.reg, self.e["id"])
        before = self.reg.read(self.e["id"]).get("published")
        with self.assertRaises(wt.Refused) as cm:
            self.pr(gh)
        self.assertEqual(cm.exception.reason, "unknown")
        self.assertEqual(self.reg.read(self.e["id"]).get("published"), before)


class BugBash2Hub(LifecycleCase):
    """sessions.py findings from round 2."""

    # Both seats: `intent` did not block sends, peer dispatch or resume.
    def test_BB2_10_an_intent_op_blocks_send_and_resume(self):
        p = self.pane()
        e = self.entry(p)
        self.reg.begin_op(e["id"], "commit", stage="ref_moved")
        target = Path(e["path"]) / "intent-bypass.txt"
        with self.assertRaises(ValueError):
            p.send("write intent-bypass.txt dispatch while intent")
        time.sleep(0.5)
        self.assertFalse(target.exists(), "the agent got work during an unsettled op")
        p.pause()
        with self.assertRaises(ValueError):
            p.resume()

    # Astra: /clear started a replacement agent despite an unknown op.
    def test_BB2_11_clear_refuses_while_an_op_is_unknown(self):
        p = self.pane()
        old = p.client.p.pid
        _unknown_op(self.reg, p.worktree_id)
        with self.assertRaises(ValueError):
            p.send("/clear")
        self.assertEqual(p.client.p.pid, old)

    # Astra: /clear during Discard left an agent alive in the trash.
    def test_BB2_12_clear_during_discard_is_refused(self):
        p = self.pane()
        snap = self.mgr.worktree_snapshot(p.id)
        real, calls, errs = wt.processes_in, [], []

        def scan(path):
            found = real(path)
            calls.append(1)
            if len(calls) == 2:
                try:
                    p.send("/clear")
                except ValueError as err:
                    errs.append(str(err))
            return found
        with mock.patch.object(wt, "processes_in", scan):
            self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertTrue(errs, "/clear was accepted while Discard held the pane")
        self.assertFalse(p.client and p.client.alive, "an agent survived the move to trash")

    # Astra: a resume that passed its hold check before Discard started an agent inside it.
    def test_BB2_13_resume_and_discard_never_interleave(self):
        p = self.pane()
        snap = self.mgr.worktree_snapshot(p.id)
        p.pause()
        entered, release = threading.Event(), threading.Event()
        reserve = self.mgr._reserve_live

        def reserve_wait(pane):
            entered.set()
            release.wait(5)
            return reserve(pane)
        errs = []

        def resume():
            try:
                p.resume()
            except Exception as err:                 # noqa: BLE001
                errs.append(repr(err))
        with mock.patch.object(self.mgr, "_reserve_live", reserve_wait):
            t = threading.Thread(target=resume)
            t.start()
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(wt.Refused) as cm:
                    self.mgr.worktree_discard(p.id, snap["tree"])
            finally:
                release.set()
                t.join(10)
        self.assertEqual(cm.exception.reason, "busy")
        self.assertEqual(errs, [])
        self.assertEqual(self.entry(p)["phase"], "active", "the folder moved under a resuming agent")

    # Both seats: Discard on an outdated review stopped the agent before refusing.
    def test_BB2_14_an_outdated_review_refuses_before_the_agent_is_stopped(self):
        p = self.pane()
        snap = self.mgr.worktree_snapshot(p.id)
        old = p.client
        Path(self.entry(p)["path"], "a.txt").write_text("changed since review\n")
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(cm.exception.reason, "changed")
        self.assertTrue(old.alive, "the refusal cost the agent")
        self.assertIs(p.client, old)

    # Astra: Discard renamed a live outside git's index.lock and moved the folder.
    def test_BB2_15_a_live_git_outside_the_worktree_refuses_discard(self):
        p = self.pane()
        e = self.entry(p)
        snap = self.mgr.worktree_snapshot(p.id)
        idx = wt._index_path(e["path"])
        live = _hold_lock_open(self, idx, self.tmp)
        old = p.client
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(cm.exception.reason, "busy")
        self.assertTrue(os.path.exists(idx + ".lock"))
        self.assertIsNone(live.poll())
        self.assertTrue(old.alive, "the refusal cost the agent")
        self.assertEqual(self.entry(p)["phase"], "active")

    # Gemini: with the agent already dead, its abandoned lock made Discard time out.
    def test_BB2_16_a_dead_agents_abandoned_lock_does_not_block_discard(self):
        p = self.pane()
        snap = self.mgr.worktree_snapshot(p.id)
        os.kill(p.client.p.pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: p.state == "dead"))
        idx = wt._index_path(self.entry(p)["path"])
        Path(idx + ".lock").write_bytes(b"left by the killed agent")
        os.utime(idx + ".lock", (time.time() - 30, time.time() - 30))
        with mock.patch.object(wt, "LOCK_WAIT_S", 1):
            r = self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(self.entry(p)["phase"], "trashed")
        self.assertTrue(Path(r["trash_path"]).is_dir())

    # Gemini: Forget offered review for a branch whose review cannot open.
    def test_BB2_17_forget_offers_review_only_for_an_active_branch(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        body = js[js.index("async function forgetPane(p)"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("p.worktree.phase === 'active'", body)



# ── round 1 leftovers (synthesis.md: deferred, then medium and low) ──────────

class BugBash3Library(CreateCase):

    # Astra r1-4: a committed symlink makes the chosen subdir land outside the worktree.
    def test_BB3_1_an_agent_folder_outside_the_worktree_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / "pkg")
        wt.git(["add", "pkg"], cwd=self.repo)
        wt.git(["commit", "-qm", "pkg link"], cwd=self.repo)
        os.unlink(self.repo / "pkg")
        (self.repo / "pkg").mkdir()                   # uncommitted: a real dir now
        e = self.make(cwd=self.repo / "pkg")
        self.assertEqual(e["subdir"], "pkg")
        with self.assertRaises(wt.Refused) as cm:
            wt.check_agent_cwd(e)
        self.assertEqual(cm.exception.reason, "identity")
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        self.assertIn("outside", m._worktree_blocked_reason(e))

    def test_BB3_1b_a_normal_subdir_passes(self):
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "f.txt").write_text("f\n")
        wt.git(["add", "-A"], cwd=self.repo)
        wt.git(["commit", "-qm", "sub"], cwd=self.repo)
        e = self.make(cwd=self.repo / "sub")
        wt.check_agent_cwd(e)

    # Astra r1-7: one deadline covers a child that keeps git's pipes open.
    def test_BB3_2_the_timeout_covers_inherited_pipes(self):
        stub = self.stub_git("sleep 30 &\nexit 0")
        t0 = time.monotonic()
        with self.assertRaises(wt.GitTimeout):
            wt._run([str(stub)], self.tmp, timeout=0.5)
        self.assertLess(time.monotonic() - t0, 8, "the reader join outlived the timeout")

    # Astra r1-8: Unicode patches expand under JSON escaping past the 2 MiB cap.
    def test_BB3_3_the_encoded_review_stays_under_its_cap(self):
        import json
        e = self.make()
        p = Path(e["path"])
        for i in range(10):
            (p / f"u{i}.txt").write_text(("é" * 99 + "\n") * 1200, encoding="utf-8")
        snap = wt.snapshot(e)
        d = wt.diff(e, snap["tree"])
        self.assertTrue(d["truncated"])
        self.assertLessEqual(len(json.dumps(d)), wt.DIFF_JSON_MAX)
        full = wt.fit_review(dict(snap, diff=d,
                                  summary={"files": 10}))
        self.assertLessEqual(len(json.dumps(full)), wt.REVIEW_JSON_MAX)

    def test_BB3_3b_fit_review_drops_patches_then_files_and_says_so(self):
        import json
        files = [{"path": f"f{i}", "patch": "一" * 40000} for i in range(30)]
        files += [{"path": "x" * 900 + str(i), "patch": None} for i in range(4000)]
        out = wt.fit_review({"tree": "t", "diff": {"files": files, "truncated": False}})
        self.assertLessEqual(len(json.dumps(out)), wt.REVIEW_JSON_MAX)
        self.assertTrue(out["diff"]["truncated"])
        self.assertTrue(out["diff"].get("files_omitted"))

    # Grok r1-9: a pid whose cwd cannot be read was skipped before its fds were read.
    def test_BB3_4_an_unreadable_cwd_still_scans_the_fds(self):
        import subprocess
        e = self.make()
        held = Path(e["path"]) / "held.txt"
        held.write_text("x\n")
        c = subprocess.Popen([sys.executable, "-c",
                              "import sys,time; f=open(sys.argv[1]); print('open', flush=True); "
                              "time.sleep(30)", str(held)], cwd=self.tmp,
                             stdout=subprocess.PIPE, start_new_session=True)
        self.addCleanup(lambda: (c.kill(), c.wait(), c.stdout.close()))
        c.stdout.readline()
        real = os.readlink

        def rl(path, *a, **k):
            if str(path) == f"/proc/{c.pid}/cwd":
                raise PermissionError(13, "denied")
            return real(path, *a, **k)
        if not wt._have_proc():
            self.skipTest("no /proc: the Linux scan did not run here")
        with mock.patch.object(wt.os, "readlink", rl):
            found = [pid for pid, _ in wt.processes_in(e["path"])]
        self.assertIn(c.pid, found)

    # Gemini r1: restore failed when the repo's folder under the root was gone.
    def test_BB3_5_restore_recreates_the_repo_folder_under_the_root(self):
        e = self.make()
        wt.discard(e, wt.snapshot(e)["tree"], registry=self.reg)
        parent = Path(e["path"]).parent
        os.rmdir(parent)                              # empty once the worktree moved
        got = wt.restore(self.reg.read(e["id"]), registry=self.reg)
        self.assertEqual(got["phase"], "active")
        self.assertTrue(Path(e["path"]).is_dir())

    # Gemini r1: a harmless lsof warning on stderr failed the macOS scan.
    def test_BB3_6_an_lsof_warning_with_no_records_is_none(self):
        stub = self.stub_git('echo "lsof: WARNING: can\'t stat() devfs file system /dev" >&2\nexit 1')
        e = self.make()
        with mock.patch.object(wt, "_lsof_bin", lambda: str(stub)):
            self.assertEqual(wt._procs_lsof(os.path.realpath(e["path"])), [])

    def test_BB3_6b_an_lsof_error_about_the_path_still_fails(self):
        e = self.make()
        root = os.path.realpath(e["path"])
        stub = self.stub_git(f'echo "lsof: status error on {root}: No such file" >&2\nexit 1')
        with mock.patch.object(wt, "_lsof_bin", lambda: str(stub)):
            with self.assertRaises(wt.ScanFailed):
                wt._procs_lsof(root)


class BugBash3Publish(PubCase):
    stub_gh = ThePullRequest.stub_gh
    pr = ThePullRequest.pr

    # Astra r1-3: a confirmed URL that another rewrite rule would redirect.
    def test_BB3_10_a_url_git_would_rewrite_again_is_refused(self):
        # alias:r -> the confirmed URL (one rewrite, what the dialog shows);
        # the confirmed URL -> another repo (the second rewrite git applies).
        other = self.tmp / "unconfirmed.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "origin", "alias:r"], cwd=self.repo)
        wt.git(["config", f"url.{self.url}.insteadOf", "alias:r"], cwd=self.repo)
        wt.git(["config", f"url.{other}.insteadOf", self.url], cwd=self.repo)
        self.assertEqual(wt.push_urls(self.e, "origin"), [self.url], "the dialog's URL")
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertEqual(cm.exception.reason, "rewrite")
        self.assertIsNone(wt.git(["ls-remote", str(other), self.e["branch"]],
                                 cwd=self.tmp).text.strip() or None)

    def test_BB3_10b_a_push_rewrite_counts_too(self):
        wt.git(["config", "url.ssh://elsewhere/.pushInsteadOf", self.url], cwd=self.repo)
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertIn(cm.exception.reason, ("rewrite", "remote_changed"))

    # Grok r1-8: an open PR from another owner's fork is not this branch's PR.
    def test_BB3_11_a_pr_from_another_head_owner_is_not_ours(self):
        wt.git(["remote", "set-url", "origin", "https://github.com/me/r.git"], cwd=self.repo)
        gh, state = self.stub_gh()
        (state / "pr").write_text('[{"url":"https://github.com/o/r/pull/3",'
                                  '"headRepositoryOwner":{"login":"stranger"}}]')
        r = self.pr(gh, repo="o/r")
        self.assertEqual(r["pr_url"], "https://github.com/o/r/pull/7", "a stranger's PR was reused")
        argv = (state / "argv").read_text()
        self.assertIn("pr create", argv)
        self.assertIn("--head me:", argv)

    def test_BB3_11b_our_own_open_pr_is_reused(self):
        wt.git(["remote", "set-url", "origin", "https://github.com/me/r.git"], cwd=self.repo)
        gh, state = self.stub_gh()
        (state / "pr").write_text('[{"url":"https://github.com/o/r/pull/3",'
                                  '"headRepositoryOwner":{"login":"me"}}]')
        r = self.pr(gh, repo="o/r")
        self.assertEqual(r["pr_url"], "https://github.com/o/r/pull/3")
        self.assertNotIn("pr create", (state / "argv").read_text())


class BugBash3Hub(LifecycleCase):

    def test_BB3_20_an_agent_folder_outside_the_worktree_never_starts(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / "pkg")
        wt.git(["add", "pkg"], cwd=self.repo)
        wt.git(["commit", "-qm", "pkg link"], cwd=self.repo)
        os.unlink(self.repo / "pkg")
        (self.repo / "pkg").mkdir()
        with self.assertRaises(Exception):
            self.mgr.create("fake", str(self.repo / "pkg"), worktree=True)
        live = [p for p in self.mgr.panes.values() if p.client and p.client.alive]
        self.assertEqual(live, [], "an agent started outside its worktree")

    # Gemini r1-8 / Grok r1-6: the browser's review text.
    def test_BB3_21_review_text_for_noop_commits_and_staged_differs(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("r.noop", js)
        body = js[js.index("function reviewBanners(snap)"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("staged_differs", body)

    # Gemini r1-5: create's failure paths mutate the roster under its lock.
    def test_BB3_22_create_failure_paths_pop_under_the_lock(self):
        import inspect
        import sessions
        lines = inspect.getsource(sessions.Manager.create).splitlines()
        pops = [i for i, l in enumerate(lines) if "self.panes.pop(" in l]
        self.assertTrue(pops)
        for i in pops:
            self.assertTrue(lines[i - 1].strip().startswith("with self._lock:"), lines[i])


# ── round 3 panel (reviews/2026-10-02-own-branch-bugbash/r3-*.md) ─────────────

class BugBash4Hub(LifecycleCase):

    # Astra r3-1: the first agent start did not hold the action lock.
    def test_BB4_1_review_refuses_while_the_first_agent_starts(self):
        import sessions
        entered, release, done, errs = (threading.Event(), threading.Event(),
                                         threading.Event(), [])
        start = sessions.Pane.start

        def slow(pane):
            entered.set()
            release.wait(10)
            return start(pane)

        def create():
            try:
                self.pane()
            except Exception as err:                 # noqa: BLE001
                errs.append(repr(err))
            finally:
                done.set()
        with mock.patch.object(sessions.Pane, "start", slow):
            t = threading.Thread(target=create)
            t.start()
            self.assertTrue(entered.wait(5))
            p = next(iter(self.mgr.panes.values()))
            try:
                with self.assertRaises(wt.Refused) as cm:
                    self.mgr.worktree_snapshot(p.id)
                self.assertEqual(cm.exception.reason, "busy")
            finally:
                release.set()
                t.join(15)
        self.assertEqual(errs, [])
        self.assertEqual(self.entry(p)["phase"], "active")
        self.assertTrue(p.client and p.client.alive)

    # Astra r3-2: a busy refusal released a hold it never took and retired the agent.
    def test_BB4_2_a_busy_refusal_leaves_the_running_turn_alone(self):
        p = self.pane()
        p.send("sleep 0.7")
        self.assertTrue(wait_for(lambda: p._turn_running))
        p.send("write queued.txt q")
        gen = p._generation
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_snapshot(p.id)
        self.assertEqual(cm.exception.reason, "busy")
        self.assertEqual(p._generation, gen, "the refusal retired the live attachment")
        self.assertTrue(wait_for(lambda: (Path(self.entry(p)["path"]) / "queued.txt").exists(),
                                 timeout=15), "the queued message was dropped")
        self.assertTrue(wait_for(lambda: p.state == "ready", timeout=15), p.state)
        p.send("perm")
        self.assertTrue(wait_for(lambda: bool(p.pending), timeout=10),
                        "a permission card never reached the pane")

    # The same for an action that held the pane and then failed: the type-ahead
    # is parked, but the live agent keeps its attachment.
    def test_BB4_3_a_failed_action_parks_type_ahead_but_keeps_the_agent(self):
        p = self.pane()
        gen = p._generation

        def boom(e, tmp_dir=None):
            p.send("write never.txt n")                # queued behind the hold
            raise wt.Refused("busy", "simulated failure")
        with mock.patch.object(wt, "snapshot", boom):
            with self.assertRaises(wt.Refused):
                self.mgr.worktree_snapshot(p.id)
        self.assertEqual(p._generation, gen)
        self.assertEqual(p._queue, [])
        self.assertFalse((Path(self.entry(p)["path"]) / "never.txt").exists())
        self.say(p, "write after.txt a")
        self.assertTrue((Path(self.entry(p)["path"]) / "after.txt").exists())

    # Astra r3-4: resume and send after the worktree was switched to another branch.
    def test_BB4_4_a_worktree_on_another_branch_blocks_resume_and_send(self):
        p = self.pane()
        p.pause()
        wt.git(["switch", "-qc", "foreign-branch"], cwd=self.entry(p)["path"])
        wt.reconcile(self.reg)
        with self.assertRaises(ValueError) as cm:
            p.resume()
        self.assertIn("foreign-branch", str(cm.exception))
        with self.assertRaises(ValueError):
            p.send("write branch-bypass.txt x")
        self.assertFalse((Path(self.entry(p)["path"]) / "branch-bypass.txt").exists())


class BugBash4Publish(PubCase):
    stub_gh = ThePullRequest.stub_gh
    pr = ThePullRequest.pr

    # Astra r3-3a: git accepts an empty insteadOf prefix; it matches every URL.
    def test_BB4_10_an_empty_rewrite_prefix_is_refused(self):
        wt.git(["config", "url.dst/.insteadOf", ""], cwd=self.repo)
        with self.assertRaises(wt.Refused) as cm:
            self.push(push_url=wt.push_urls(self.e, "origin")[0])
        self.assertIn(cm.exception.reason, ("rewrite", "remote_changed"))

    # Astra r3-3b: a URL that git reads as another remote's name.
    def test_BB4_11_a_url_that_names_another_remote_is_refused(self):
        other = self.tmp / "unconfirmed.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "origin", "destination"], cwd=self.repo)
        wt.git(["remote", "add", "destination", str(other)], cwd=self.repo)
        url = wt.push_urls(self.e, "origin")[0]
        self.assertEqual(url, "destination")
        with self.assertRaises(wt.Refused) as cm:
            self.push(push_url=url)
        self.assertEqual(cm.exception.reason, "rewrite")
        self.assertEqual(wt.git(["ls-remote", str(other)], cwd=self.tmp).text.strip(), "")

    # Gemini r3-1: a listed PR with no head owner (a deleted fork) is not ours.
    def test_BB4_12_a_pr_with_no_head_owner_is_not_reused(self):
        wt.git(["remote", "set-url", "origin", "https://github.com/o/r.git"], cwd=self.repo)
        gh, state = self.stub_gh()
        (state / "pr").write_text('[{"url":"https://github.com/o/r/pull/3",'
                                  '"headRepositoryOwner":null}]')
        r = self.pr(gh)
        self.assertEqual(r["pr_url"], "https://github.com/o/r/pull/7")


class BugBash4Library(CreateCase):

    # Astra r3 non-blocking: a huge too_big inventory overflowed the cap.
    def test_BB4_20_fit_review_trims_inventories_too(self):
        import json
        obj = {"tree": "a" * 40,
               "too_big": [{"path": "一" * 60 + str(i), "size": 600000} for i in range(6000)],
               "diff": {"files": [], "truncated": False}}
        out = wt.fit_review(obj)
        self.assertLessEqual(len(json.dumps(out)), wt.REVIEW_JSON_MAX)
        self.assertTrue(out.get("too_big_omitted"))



class BugBash4Restart(PubCase):
    stub_gh = ThePullRequest.stub_gh

    # Grok r3-1: restart settled an interrupted PR op on any open PR with that branch name.
    def _interrupted_pr(self, listed, head="me:corral/fix-login", repo="o/r"):
        gh, state = self.stub_gh()
        (state / "pr").write_text(listed)
        op = self.reg.begin_op(self.e["id"], "pr", repo=repo, head=head)
        with mock.patch.object(wt, "GH_BIN", gh):
            note = wt.resolve_op(self.reg.read(self.e["id"]),
                                 [o for o in self.reg.read(self.e["id"])["ops"]
                                  if o["op_id"] == op][0], self.reg)
        got = [o for o in self.reg.read(self.e["id"])["ops"] if o["op_id"] == op][0]
        return note, got, (state / "argv").read_text()

    def test_BB4_30_a_strangers_pr_does_not_settle_ours(self):
        note, got, argv = self._interrupted_pr(
            '[{"url":"https://github.com/o/r/pull/1","headRepositoryOwner":{"login":"stranger"}}]')
        self.assertEqual((got["state"], got.get("stage")), ("done", "not_done"), note)
        self.assertIsNone((self.reg.read(self.e["id"]).get("published") or {}).get("pr_url"))
        self.assertIn("headRepositoryOwner", argv)

    def test_BB4_31_our_pr_settles_it(self):
        note, got, _ = self._interrupted_pr(
            '[{"url":"https://github.com/o/r/pull/1","headRepositoryOwner":{"login":"stranger"}},'
            '{"url":"https://github.com/o/r/pull/9","headRepositoryOwner":{"login":"me"}}]')
        self.assertEqual((got["state"], got.get("url")), ("done", "https://github.com/o/r/pull/9"))
        self.assertEqual(self.reg.read(self.e["id"])["published"]["pr_url"],
                         "https://github.com/o/r/pull/9")

    def test_BB4_32_a_same_repo_head_uses_the_repo_owner(self):
        note, got, _ = self._interrupted_pr(
            '[{"url":"https://github.com/o/r/pull/4","headRepositoryOwner":{"login":"o"}}]',
            head="corral/fix-login")
        self.assertEqual(got.get("url"), "https://github.com/o/r/pull/4")

    # Grok r3 non-blocking: an unreadable git config must not skip the rewrite check.
    def test_BB4_33_an_unreadable_config_refuses_the_push(self):
        real = wt.git

        def git(args, *a, **k):
            if args[:1] == ["config"] and "--get-regexp" in args:
                return wt.GitResult(128, b"", b"fatal: bad config line 3", False)
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", git):
            self.assertTrue(wt.rewrite_rule(self.e, self.url))



# ── round 4 panel ─────────────────────────────────────────────────────────────

class BugBash5Hub(LifecycleCase):

    # Astra r4: queued turns were sent without the gate being checked again.
    def test_BB5_1_a_queued_turn_after_a_branch_switch_is_not_sent(self):
        p = self.pane()
        path = Path(self.entry(p)["path"])
        p.send("sleep 0.6")
        self.assertTrue(wait_for(lambda: p._turn_running))
        p.send("checkout foreign-branch")                # both queued while on our branch
        p.send("write after.txt on the foreign branch")
        self.assertTrue(wait_for(lambda: p.state == "ready" and not p._turn_running,
                                 timeout=15), p.state)
        time.sleep(0.3)
        self.assertFalse((path / "after.txt").exists(), "a queued turn ran on another branch")
        self.assertIn("foreign-branch", p.worktree_blocked or "")

    def test_BB5_2_a_queued_turn_after_an_op_went_unknown_is_not_sent(self):
        p = self.pane()
        path = Path(self.entry(p)["path"])
        p.send("sleep 0.6")
        self.assertTrue(wait_for(lambda: p._turn_running))
        p.send("write late.txt x")
        _unknown_op(self.reg, p.worktree_id)
        self.assertTrue(wait_for(lambda: p.state == "ready" and not p._turn_running,
                                 timeout=15), p.state)
        time.sleep(0.3)
        self.assertFalse((path / "late.txt").exists(), "a queued turn ran with an unknown op")
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("not sent" in n for n in notes), notes)


    # Gemini r4: a start that failed with anything but AgentError left the pane
    # `starting` for good, and `starting` now refuses every review action.
    def test_BB5_3_a_failed_start_leaves_the_pane_dead_not_starting(self):
        import sessions
        p = self.pane()
        p.pause()
        p.acp_session = None                          # the D14 retry path
        with mock.patch.object(sessions, "spawn_env",
                               mock.Mock(side_effect=OSError("config dir unreadable"))):
            try:
                p.resume()
            except Exception:                         # noqa: BLE001
                pass
        self.assertEqual(p.state, "dead", p.state)
        self.assertIn("config dir unreadable", p.error or "")
        snap = self.mgr.worktree_snapshot(p.id)      # not refused as busy
        self.mgr.worktree_discard(p.id, snap["tree"])
        self.assertEqual(self.entry(p)["phase"], "trashed")

    def test_BB5_4_a_failed_clear_leaves_the_pane_dead_not_starting(self):
        import sessions
        p = self.pane()
        with mock.patch.object(sessions, "spawn_env",
                               mock.Mock(side_effect=OSError("config dir unreadable"))):
            try:
                p.send("/clear")
            except Exception:                         # noqa: BLE001
                pass
        self.assertEqual(p.state, "dead", p.state)


    def test_BB5_5_a_worktree_still_in_intent_blocks(self):
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        self.assertIn("never finished", m._worktree_blocked_reason({"phase": "intent", "ops": []}))


class BugBash5Publish(PubCase):

    # Astra r4: a URL that is also a remote's name, whose fetch URL is its own name.
    def test_BB5_10_a_url_equal_to_a_remote_name_is_refused(self):
        other = self.tmp / "unconfirmed.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "origin", "dest"], cwd=self.repo)
        wt.git(["remote", "add", "dest", "dest"], cwd=self.repo)
        wt.git(["remote", "set-url", "--push", "dest", str(other)], cwd=self.repo)
        url = wt.push_urls(self.e, "origin")[0]
        self.assertEqual(url, "dest")
        with self.assertRaises(wt.Refused) as cm:
            self.push(push_url=url)
        self.assertEqual(cm.exception.reason, "rewrite")
        self.assertEqual(wt.git(["ls-remote", str(other)], cwd=self.tmp).text.strip(), "")

    def test_BB5_11_a_legacy_remotes_file_counts_as_a_remote_name(self):
        other = self.tmp / "unconfirmed.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "origin", "legacy"], cwd=self.repo)
        d = Path(self.e["common_dir"]) / "remotes"
        d.mkdir(exist_ok=True)
        (d / "legacy").write_text(f"URL: {other}\n")
        with self.assertRaises(wt.Refused) as cm:
            self.push(push_url="legacy")
        self.assertEqual(cm.exception.reason, "rewrite")



# ── round 5 panel ─────────────────────────────────────────────────────────────

class BugBash6Hub(LifecycleCase):

    # Astra r5-1: a drain retired by /clear picked up the new generation and
    # dispatched type-ahead while a review action held the pane.
    def test_BB6_1_a_retired_drain_never_dispatches_during_a_hold(self):
        p = self.pane()
        at_tail, release_tail = threading.Event(), threading.Event()
        in_review, release_review = threading.Event(), threading.Event()
        tail, snap_real, calls, errs = self.mgr.worktree_turn_ended, wt.snapshot, [], []

        def slow_tail(pane):
            calls.append(1)
            if len(calls) == 1:
                at_tail.set()
                release_tail.wait(10)
            return tail(pane)

        def slow_snapshot(*a, **k):
            in_review.set()
            release_review.wait(10)
            return snap_real(*a, **k)

        def review():
            try:
                self.mgr.worktree_snapshot(p.id)
            except BaseException as err:              # noqa: BLE001
                errs.append(err)
        target = Path(self.entry(p)["path"]) / "during-review.txt"
        with mock.patch.object(self.mgr, "worktree_turn_ended", slow_tail):
            p.send("pwd")
            self.assertTrue(at_tail.wait(10))
            p.send("/clear")
            with mock.patch.object(wt, "snapshot", slow_snapshot):
                th = threading.Thread(target=review)
                th.start()
                try:
                    self.assertTrue(in_review.wait(10))
                    self.assertTrue(p.held)
                    p.send("write during-review.txt queued behind the hold")
                    release_tail.set()
                    time.sleep(1.5)
                    self.assertFalse(target.exists(), "a retired drain dispatched during the hold")
                finally:
                    release_tail.set()
                    release_review.set()
                    th.join(10)
        self.assertEqual(errs, [])
        self.assertTrue(wait_for(target.exists, timeout=10),
                        "the queued message never ran after the hold ended")

    # Gemini r5: a parked peer message never told its sender.
    def test_BB6_2_a_parked_peer_message_reports_not_delivered(self):
        import sessions
        p = self.pane()
        item = sessions._core.QueuedText("from another pane", "t-peer-1")
        item.peer = True
        p._report_parked([item], live=True)
        res = [e["data"] for e in p.events if e["kind"] == "peer_result"]
        self.assertTrue(any(r.get("turn") == "t-peer-1" and r.get("delivered") is False
                            for r in res), res)


class BugBash6Publish(PubCase):

    # Astra r5-2: a remote name with a non-breaking space escaped str.split().
    def test_BB6_10_a_remote_name_with_unicode_space_is_refused(self):
        other = self.tmp / "unconfirmed.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        name = "release mirror"
        wt.git(["remote", "set-url", "origin", name], cwd=self.repo)
        wt.git(["remote", "add", name, name], cwd=self.repo)
        wt.git(["remote", "set-url", "--push", name, str(other)], cwd=self.repo)
        url = wt.push_urls(self.e, "origin")[0]
        self.assertEqual(url, name)
        with self.assertRaises(wt.Refused) as cm:
            self.push(push_url=url)
        self.assertEqual(cm.exception.reason, "rewrite")
        self.assertEqual(wt.git(["ls-remote", str(other)], cwd=self.tmp).text.strip(), "")

    # Grok r5-1: a repo-local core.sshCommand (or proxy, or TLS override) sends
    # the push, and its check, somewhere the user did not confirm.
    def test_BB6_11_repo_local_transport_overrides_are_refused(self):
        for key, value in (("core.sshCommand", "sh -c 'exit 1'"),
                           ("core.gitProxy", "evil-proxy"),
                           ("http.proxy", "http://127.0.0.1:9"),
                           ("http.https://github.com/.sslVerify", "false"),
                           # Grok r6: maps the confirmed host to another address.
                           ("http.curloptResolve", "confirmed.invalid:80:127.0.0.2"),
                           ("http.http://confirmed.invalid/repo.git.curloptResolve",
                            "confirmed.invalid:80:127.0.0.2"),
                           ("http.followRedirects", "true"),
                           ("ssh.variant", "simple")):
            with self.subTest(key=key):
                wt.git(["config", key, value], cwd=self.repo)
                try:
                    with self.assertRaises(wt.Refused) as cm:
                        self.push()
                    self.assertEqual(cm.exception.reason, "transport")
                    self.assertIn(key.rsplit(".", 1)[-1].lower(), cm.exception.detail.lower())
                finally:
                    wt.git(["config", "--unset", key], cwd=self.repo)
        self.assertEqual(self.push()["pushed"], self.oid)     # nothing set: it pushes

    # Gemini r7: a rewrite rule whose base holds a space was misparsed and missed.
    def test_BB6_13_a_rewrite_rule_with_a_space_in_its_base_is_refused(self):
        other = self.tmp / "repo path.git"
        wt.git(["init", "-q", "--bare", str(other)], cwd=self.tmp)
        wt.git(["remote", "set-url", "--push", "origin", self.url], cwd=self.repo)
        wt.git(["config", f"url.{other}.pushInsteadOf", self.url], cwd=self.repo)
        self.assertEqual(wt.push_urls(self.e, "origin"), [self.url])
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertIn(cm.exception.reason, ("rewrite", "transport"))
        self.assertEqual(wt.git(["ls-remote", str(other)], cwd=self.tmp).text.strip(), "")

    def test_BB6_14_rewrite_rule_parses_keys_with_spaces(self):
        # A GLOBAL pushInsteadOf: transport_override does not see it and
        # ls-remote --get-url does not apply it, so only the parser can.
        g = self.tmp / "global.gitconfig"
        g.write_text(f'[url "https://x.invalid/a b/"]\n\tpushInsteadOf = {self.url}\n')
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(g)}):
            rule = wt.rewrite_rule(self.e, self.url)
        self.assertIsNotNone(rule)
        self.assertIn("pushinsteadof", rule.lower())
        self.assertIn("a b", rule)

    def test_BB6_12_a_worktree_scoped_override_counts_too(self):
        wt.git(["config", "extensions.worktreeConfig", "true"], cwd=self.repo)
        wt.git(["config", "--worktree", "core.sshCommand", "evil"], cwd=self.e["path"])
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertEqual(cm.exception.reason, "transport")



# ── round 8 panel ─────────────────────────────────────────────────────────────

class BugBash7Publish(PubCase):

    # Grok r8: an include (or the config itself) that serves the checks one
    # thing and `git push` another.
    def test_BB7_1_a_repository_include_is_refused(self):
        inc = self.tmp / "extra.gitconfig"
        inc.write_text("[core]\n\tautocrlf = false\n")
        for key in ("include.path", "includeIf.onbranch:corral/**.path"):
            with self.subTest(key=key):
                wt.git(["config", key, str(inc)], cwd=self.repo)
                try:
                    with self.assertRaises(wt.Refused) as cm:
                        self.push()
                    self.assertEqual(cm.exception.reason, "transport")
                    self.assertIn("include", cm.exception.detail.lower())
                finally:
                    wt.git(["config", "--unset", key], cwd=self.repo)

    def test_BB7_2_a_config_that_is_not_a_regular_file_is_refused(self):
        cfg = Path(self.e["common_dir"]) / "config"
        real = cfg.with_name("config.real")
        os.replace(cfg, real)
        os.symlink(real, cfg)
        self.addCleanup(lambda: (os.unlink(cfg), os.replace(real, cfg)))
        with self.assertRaises(wt.Refused) as cm:
            self.push()
        self.assertEqual(cm.exception.reason, "transport")
        self.assertIn("regular file", cm.exception.detail)

    def test_BB7_3_a_config_changed_during_publish_is_unknown_not_done(self):
        cfg = Path(self.e["common_dir"]) / "config"
        real = wt.git

        def git(args, *a, **k):
            r = real(args, *a, **k)
            if args[:1] == ["push"]:
                with open(cfg, "a") as f:
                    f.write("[core]\n\tbare = false\n")
            return r
        with mock.patch.object(wt, "git", git):
            with self.assertRaises(wt.Refused) as cm:
                self.push()
        self.assertEqual(cm.exception.reason, "unknown")
        op = [o for o in self.reg.read(self.e["id"])["ops"] if o["op"] == "push"][-1]
        self.assertEqual(op["state"], "unknown")
        self.assertNotIn("url", self.reg.read(self.e["id"]).get("published") or {})



# ── round 9 panel ─────────────────────────────────────────────────────────────

class BugBash8Hub(LifecycleCase):

    # Astra r9: the admin dir's `commondir` file was trusted, so an agent could
    # point its worktree at another clone and commit there.
    def _retarget(self, e):
        other = self.tmp / "other.git"
        wt.git(["clone", "-q", "--bare", str(self.repo), str(other)], cwd=self.tmp)
        link = Path(wt._admin_dir(e)) / "commondir"
        old = link.read_bytes()
        self.addCleanup(link.write_bytes, old)
        link.write_text(str(other) + "\n")
        return other

    def test_BB8_1_a_retargeted_commondir_fails_verify_and_blocks_dispatch(self):
        p = self.pane()
        e = self.entry(p)
        tip = wt.git(["rev-parse", e["branch"]], cwd=self.repo).text.strip()
        other = self._retarget(e)
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "tampered")
        self.assertIsNotNone(self.mgr._worktree_blocked_reason(e))
        with self.assertRaises(ValueError):
            p.send("commit agent commit after commondir change")
        self.assertEqual(wt.git(["rev-parse", e["branch"]], cwd=other).text.strip(), tip)

    def test_BB8_3_the_commondir_link_is_fingerprinted(self):
        p = self.pane()
        e = self.entry(p)
        link = Path(wt._admin_dir(e)) / "commondir"
        old = link.read_bytes()
        self.addCleanup(link.write_bytes, old)
        before = wt.config_fingerprint(e)
        link.write_bytes(old + b"\n")
        self.assertNotEqual(wt.config_fingerprint(e), before, "commondir is not fingerprinted")

    def test_BB8_2_publish_refuses_a_retargeted_commondir(self):
        p = self.pane()
        e = self.entry(p)
        self._retarget(e)
        tree = wt.git(["rev-parse", "HEAD^{tree}"], cwd=e["path"]).text.strip()
        with self.assertRaises(wt.Refused):
            wt.push(e, "origin", str(self.tmp / "x.git"), e["base_sha"], tree, registry=self.reg)



class BugBash8Publish(PubCase):

    # Grok r9: every git call that decides or performs the push runs against
    # the repository's git dir, so worktree links cannot redirect any of them.
    def test_BB8_10_publish_decides_and_pushes_from_the_repositorys_git_dir(self):
        real, seen = wt.git, []

        def spy(args, *a, **k):
            seen.append((args[0], (k.get("env_extra") or {}).get("GIT_DIR"), k.get("cwd")))
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", spy):
            self.push()
        decide = [x for x in seen if x[0] in ("push", "ls-remote", "remote", "config")]
        self.assertTrue(decide)
        for verb, gd, cwd in decide:
            self.assertEqual((gd, str(cwd)), (self.e["common_dir"], self.e["common_dir"]), verb)

    def test_BB8_11_a_missing_commondir_refuses(self):
        link = Path(wt._admin_dir(self.e)) / "commondir"
        old = link.read_bytes()
        link.unlink()
        self.addCleanup(link.write_bytes, old)
        with self.assertRaises(wt.Refused):
            self.push()



# ── round 10 panel ────────────────────────────────────────────────────────────

def _bounded(test, fn, seconds=10):
    """Run fn in a thread; fail (not hang) if it does not return in time."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as err:                  # noqa: BLE001
            box["error"] = err
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(seconds)
    test.assertFalse(th.is_alive(), "blocked reading a file the agent controls")
    return box


class BugBash9Library(CreateCase):

    # Astra r10: verify() read the worktree's .git with a plain Python read; a
    # fifo there blocked it forever (and with it a resume holding the lock).
    def _fifo(self, path):
        path = Path(path)
        old = path.read_bytes() if path.exists() else None
        path.unlink(missing_ok=True)
        os.mkfifo(path)

        def restore():
            path.unlink(missing_ok=True)
            if old is not None:
                path.write_bytes(old)
        self.addCleanup(restore)

    def test_BB9_1_a_fifo_dotgit_fails_verify_without_blocking(self):
        e = self.make()
        self._fifo(Path(e["path"]) / ".git")
        box = _bounded(self, lambda: wt.verify(e))
        self.assertIsInstance(box.get("error"), wt.IdentityError)

    def test_BB9_2_a_fifo_index_refuses_snapshot_without_blocking(self):
        e = self.make()
        self._fifo(wt._index_path(e["path"]))
        box = _bounded(self, lambda: wt.snapshot(e))
        self.assertIsInstance(box.get("error"), wt.Refused)

    def test_BB9_3_an_index_symlinked_to_a_fifo_is_refused_without_blocking(self):
        # git resolves a symlinked index itself, so a link to a regular file
        # is read the same by git and by the hub; a link to a fifo is the hazard.
        e = self.make()
        idx = Path(wt._index_path(e["path"]))
        real = idx.with_name("index.real")
        fifo = self.tmp / "index.fifo"
        os.replace(idx, real)
        os.mkfifo(fifo)
        os.symlink(fifo, idx)
        self.addCleanup(lambda: (idx.unlink(), os.replace(real, idx)))
        box = _bounded(self, lambda: wt.snapshot(e))
        self.assertIsInstance(box.get("error"), (wt.Refused, wt.GitError))

class BugBash9Hub(LifecycleCase):

    def test_BB9_10_resume_with_a_fifo_dotgit_refuses_and_releases_the_lock(self):
        p = self.pane()
        p.pause()
        dotgit = Path(self.entry(p)["path"]) / ".git"
        old = dotgit.read_bytes()
        dotgit.unlink()
        os.mkfifo(dotgit)
        self.addCleanup(lambda: (dotgit.unlink(), dotgit.write_bytes(old)))
        box = _bounded(self, p.resume)
        self.assertIsInstance(box.get("error"), ValueError)
        self.assertFalse(p._action_lock.locked(), "resume left the action lock held")



class BugBash9Publish(PubCase):

    # Grok r10: a relative push URL resolves against git's cwd, which is not
    # the folder the user meant; a decoy there received the push.
    def test_BB9_20_a_relative_local_push_url_is_refused(self):
        decoy = Path(self.e["common_dir"]) / "proj.git"
        wt.git(["init", "-q", "--bare", str(decoy)], cwd=self.tmp)
        for rel in ("../proj.git", "proj.git", "./x/../proj.git"):
            with self.subTest(url=rel):
                wt.git(["remote", "set-url", "origin", rel], cwd=self.repo)
                with self.assertRaises(wt.Refused) as cm:
                    self.push(push_url=rel)
                self.assertEqual(cm.exception.reason, "relative")
        self.assertEqual(wt.git(["ls-remote", str(decoy)], cwd=self.tmp).text.strip(), "")

    def test_BB9_21_absolute_paths_and_urls_are_not_relative(self):
        for url in ("/srv/git/r.git", "file:///srv/git/r.git", "https://github.com/o/r.git",
                    "ssh://git@host/r.git", "git@github.com:o/r.git", "host:path/r.git"):
            with self.subTest(url=url):
                self.assertFalse(wt.is_relative_local_url(url))
        for url in ("../r.git", "r.git", "./r", "sub/dir/r.git", "./a:b"):
            with self.subTest(url=url):
                self.assertTrue(wt.is_relative_local_url(url))

    def test_BB9_22_restart_checks_an_interrupted_push_from_the_repository(self):
        real, seen = wt.git, []

        def spy(args, *a, **k):
            seen.append((args[0], (k.get("env_extra") or {}).get("GIT_DIR")))
            return real(args, *a, **k)
        op = self.reg.begin_op(self.e["id"], "push", url=self.url, ref=self.e["branch"],
                               oid=self.oid)
        with mock.patch.object(wt, "git", spy):
            wt.resolve_op(self.reg.read(self.e["id"]),
                          [o for o in self.reg.read(self.e["id"])["ops"] if o["op_id"] == op][0],
                          self.reg)
        self.assertIn(("ls-remote", self.e["common_dir"]), seen)



# ── round 11 panel ────────────────────────────────────────────────────────────

class BugBash10Library(CreateCase):

    # Astra r11-1: a worktree-scoped core.worktree routed the pane's git to the
    # main checkout while verify() still passed.
    def test_BB10_1_a_redirected_work_tree_fails_verify(self):
        e = self.make()
        wt.git(["config", "extensions.worktreeConfig", "true"], cwd=self.repo)
        wt.git(["config", "--worktree", "core.worktree", str(self.repo)], cwd=e["path"])
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "tampered")
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        self.assertIsNotNone(m._worktree_blocked_reason(e))

    # Astra r11-2: an index symlinked to the main checkout's index.
    def test_BB10_2_an_index_outside_the_admin_dir_fails_verify(self):
        e = self.make()
        idx = Path(wt._index_path(e["path"]))
        real = idx.with_name("index.saved")
        os.replace(idx, real)
        os.symlink(self.repo / ".git" / "index", idx)
        self.addCleanup(lambda: (idx.unlink(), os.replace(real, idx)))
        with self.assertRaises(wt.IdentityError) as cm:
            wt.verify(e)
        self.assertEqual(cm.exception.reason, "tampered")

    def test_BB10_3_an_index_symlinked_inside_the_admin_dir_still_passes(self):
        e = self.make()
        idx = Path(wt._index_path(e["path"]))
        real = idx.with_name("index.real")
        os.replace(idx, real)
        os.symlink(real, idx)
        self.addCleanup(lambda: (idx.unlink(), os.replace(real, idx)))
        wt.verify(e)


class BugBash10Hub(CreateCase):

    # Astra r11-3: a review action checked the gates before taking the pane's
    # action lock, so an overlapping Commit could leave an op unknown first.
    def test_BB10_10_review_rechecks_the_gates_after_taking_the_lock(self):
        import sessions
        import types
        e = self.make()
        p = types.SimpleNamespace(id="review", worktree_id=e["id"],
                                  _action_lock=threading.Lock(), _turn_lock=threading.Lock(),
                                  _turn_running=False, pending={}, state="ready", held=False,
                                  dir=self.tmp / "pane")
        p.dir.mkdir()
        p.emit = lambda *a, **k: None
        p.release_hold = lambda **k: setattr(p, "held", False)
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m._wt_registry, m._lock = {p.id: p}, self.reg, threading.Lock()
        (Path(e["path"]) / "a.txt").write_text("reviewed change\n")
        snap = wt.snapshot(e)
        passed, go, results, errors = threading.Event(), threading.Event(), [], []
        real_gate = m._worktree_blocked_reason

        def delayed_gate(entry, *a, **k):
            r = real_gate(entry, *a, **k)
            if threading.current_thread().name == "delayed-review" and not passed.is_set():
                passed.set()
                go.wait(10)
            return r
        m._worktree_blocked_reason = delayed_gate

        def review():
            try:
                results.append(m.worktree_snapshot(p.id))
            except BaseException as err:              # noqa: BLE001
                errors.append(err)
        th = threading.Thread(target=review, name="delayed-review")
        th.start()
        try:
            self.assertTrue(passed.wait(10))
            with mock.patch.object(wt, "_replace_index",
                                   side_effect=wt.Refused("busy", "injected")):
                with self.assertRaises(wt.Refused):
                    m.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "c")
            self.assertEqual(self.reg.read(e["id"])["ops"][-1]["state"], "unknown")
        finally:
            go.set()
            th.join(10)
        self.assertEqual(results, [], "review ran while an op was unknown")
        self.assertTrue(errors and isinstance(errors[0], wt.Refused), errors)



class BugBash10Publish(PubCase):

    # Grok r11: restart settled an interrupted push by ls-remote, which a
    # url.*.insteadOf added since then sends to a decoy holding the oid.
    def test_BB10_20_restart_does_not_settle_a_push_through_a_rewrite(self):
        decoy = self.tmp / "decoy.git"
        wt.git(["clone", "-q", "--bare", str(self.repo), str(decoy)], cwd=self.tmp)
        wt.git(["push", "-q", str(decoy), f"{self.oid}:{self.e['branch']}"], cwd=self.e["path"])
        op = self.reg.begin_op(self.e["id"], "push", url=self.url, ref=self.e["branch"],
                               oid=self.oid)
        wt.git(["config", f"url.{decoy}.insteadOf", self.url], cwd=self.repo)
        note = wt.resolve_op(self.reg.read(self.e["id"]),
                             [o for o in self.reg.read(self.e["id"])["ops"]
                              if o["op_id"] == op][0], self.reg)
        got = [o for o in self.reg.read(self.e["id"])["ops"] if o["op_id"] == op][0]
        self.assertEqual(got["state"], "unknown", note)
        self.assertNotIn("url", self.reg.read(self.e["id"]).get("published") or {})

    # Grok r11: a push that timed out stayed `intent` until a restart.
    def test_BB10_21_a_push_that_times_out_is_unknown(self):
        real = wt.git

        def git(args, *a, **k):
            if args[:1] == ["push"]:
                raise wt.GitTimeout(["git", "push"], 120)
            return real(args, *a, **k)
        with mock.patch.object(wt, "git", git):
            with self.assertRaises(wt.Refused) as cm:
                self.push()
        self.assertEqual(cm.exception.reason, "unknown")
        op = [o for o in self.reg.read(self.e["id"])["ops"] if o["op"] == "push"][-1]
        self.assertEqual(op["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
