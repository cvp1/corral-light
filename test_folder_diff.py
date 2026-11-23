#!/usr/bin/python3
"""folder_diff: a plain pane's changes since HEAD, without running any program
the repository's own config names (the agent may have written that config)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import folder_diff


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)})


class FolderDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="corral-fd-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "keep.txt").write_text("one\ntwo\nthree\n")
        (self.repo / "gone.txt").write_text("bye\n")
        (self.repo / "same.txt").write_text("same\n")
        (self.repo / ".gitignore").write_text("secret.env\n")
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "deep.txt").write_text("a\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def by_path(self, r):
        return {f["path"]: f for f in r["files"]}

    def test_modified_new_and_deleted_and_nothing_else(self):
        (self.repo / "keep.txt").write_text("one\nTWO\nthree\n")
        (self.repo / "gone.txt").unlink()
        (self.repo / "new.txt").write_text("fresh\n")
        (self.repo / "secret.env").write_text("TOKEN=x\n")       # ignored: never listed
        r = folder_diff.changes(self.repo)
        f = self.by_path(r)
        self.assertEqual({k: v["status"] for k, v in f.items()},
                         {"keep.txt": "M", "gone.txt": "D", "new.txt": "A"})
        self.assertIn("-two\n+TWO\n", f["keep.txt"]["patch"])
        self.assertIn("+fresh", f["new.txt"]["patch"])
        self.assertEqual(r["branch"], "main")

    def test_a_subfolder_pane_sees_only_its_subfolder(self):
        (self.repo / "keep.txt").write_text("changed\n")
        (self.repo / "sub" / "deep.txt").write_text("b\n")
        r = folder_diff.changes(self.repo / "sub")
        self.assertEqual([f["path"] for f in r["files"]], ["deep.txt"])

    def test_not_a_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(folder_diff.changes(plain)["repo"], False)

    def test_binary_and_big_are_listed_not_diffed(self):
        (self.repo / "img.bin").write_bytes(b"\0\1\2" * 10)
        (self.repo / "big.txt").write_text("x\n" * (folder_diff.FILE_MAX_BYTES // 2 + 10))
        f = self.by_path(folder_diff.changes(self.repo))
        self.assertTrue(f["img.bin"]["binary"]); self.assertIsNone(f["img.bin"]["patch"])
        self.assertTrue(f["big.txt"]["too_big"]); self.assertIsNone(f["big.txt"]["patch"])

    def test_a_hostile_repo_config_runs_nothing(self):
        """The agent wrote .git/config and .gitattributes: fsmonitor, a clean
        filter, a textconv, an external diff, a pager and hooks all name a
        program that would leave a marker. Reading the changes runs none."""
        marker = self.tmp / "RAN"
        evil = self.tmp / "evil.sh"
        evil.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        evil.chmod(0o755)
        for k, v in (("core.fsmonitor", str(evil)), ("filter.x.clean", str(evil)),
                     ("filter.x.smudge", str(evil)), ("diff.x.textconv", str(evil)),
                     ("diff.external", str(evil)), ("core.pager", str(evil)),
                     ("core.hooksPath", str(self.tmp))):
            git(self.repo, "config", k, v)
        for hook in ("post-checkout", "pre-commit", "post-index-change"):
            (self.tmp / hook).write_text(f"#!/bin/sh\ntouch {marker}\n")
            (self.tmp / hook).chmod(0o755)
        (self.repo / ".gitattributes").write_text("* filter=x diff=x\n")
        (self.repo / "keep.txt").write_text("one\nchanged\nthree\n")
        (self.repo / "new.txt").write_text("new\n")
        import time
        t0 = time.monotonic()
        r = folder_diff.changes(self.repo)
        self.assertLess(time.monotonic() - t0, 10, "reading the changes waited on something")
        self.assertFalse(marker.exists(), "a program named by the repo's config ran")
        self.assertIn("keep.txt", self.by_path(r))
        # Control: the same config does run the program under porcelain.
        subprocess.run(["git", "diff"], cwd=self.repo, capture_output=True, timeout=30,
                       stdin=subprocess.DEVNULL)
        self.assertTrue(marker.exists(), "the control did not fire: this test proves nothing")


if __name__ == "__main__":
    unittest.main()
