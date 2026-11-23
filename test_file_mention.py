#!/usr/bin/python3
"""file_mention: `@` suggestions from a pane's folder, read only, bounded,
running nothing the repo's config names."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import file_mention


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)})


class FileMention(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="corral-fm-"))

    def test_rank_prefers_the_file_name(self):
        paths = ["docs/hub-notes.md", "hub.py", "static/app.js", "tests/test_hub.py", "zhub.txt"]
        self.assertEqual(file_mention.rank(paths, "hub"),
                         ["hub.py", "docs/hub-notes.md", "zhub.txt", "tests/test_hub.py"])
        self.assertEqual(file_mention.rank(paths, "APP"), ["static/app.js"])
        self.assertEqual(len(file_mention.rank(paths * 20, "")), file_mention.LIMIT)

    def test_a_repo_lists_tracked_and_untracked_not_ignored(self):
        r = self.tmp / "repo"; r.mkdir()
        git(r, "init", "-q")
        (r / ".gitignore").write_text("secret.env\n")
        (r / "a.py").write_text("x"); (r / "secret.env").write_text("T=1")
        (r / "sub").mkdir(); (r / "sub" / "b.py").write_text("y")
        git(r, "add", "a.py", ".gitignore")
        got = file_mention.suggest(r, "")["files"]
        self.assertIn("a.py", got); self.assertIn("sub/b.py", got)
        self.assertNotIn("secret.env", got)
        self.assertEqual(file_mention.suggest(r / "sub", "b")["files"], ["b.py"],
                         "a subfolder pane gets paths relative to itself")

    def test_outside_a_repo_a_walk_skips_hidden_and_build_folders(self):
        d = self.tmp / "plain"; d.mkdir()
        for p in ("keep.txt", ".hidden/x.txt", "node_modules/m.js", "src/main.c"):
            (d / p).parent.mkdir(parents=True, exist_ok=True); (d / p).write_text("1")
        self.assertEqual(sorted(file_mention.suggest(d, "")["files"]), ["keep.txt", "src/main.c"])

    def test_a_hostile_fsmonitor_does_not_run(self):
        r = self.tmp / "repo2"; r.mkdir()
        git(r, "init", "-q")
        marker, evil = self.tmp / "RAN", self.tmp / "evil.sh"
        evil.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n"); evil.chmod(0o755)
        git(r, "config", "core.fsmonitor", str(evil))
        (r / "a.py").write_text("x")
        git(r, "add", "a.py")
        marker.unlink(missing_ok=True)                     # `git add` above may have run it
        self.assertIn("a.py", file_mention.suggest(r, "a")["files"])
        self.assertFalse(marker.exists(), "the repo's fsmonitor ran")


if __name__ == "__main__":
    unittest.main()
