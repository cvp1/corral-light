#!/usr/bin/python3
"""T-ISO-1: the own-branch suites touch nothing of the user's.

Runs test_worktrees, test_worktree_routes and test_worktrees_cli in a child
process with HOME and TMPDIR pointed at a fresh temp dir and the state
variables removed. Every git call logs its working directory (worktrees._run,
active only under CORRAL_WT_TEST=1 with CORRAL_WT_ISO_LOG set); any outside the
temp dir fails the run, and so does any change to the real worktree root or
registry, listed before and after.

Collected by test_corral_light.py. Run alone: python3 -m unittest test_worktrees_iso -v
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from testkit.scratch import tmpdir

ROOT = Path(__file__).resolve().parent
SUITES = ("test_worktrees", "test_worktree_routes", "test_worktrees_cli")


def listing(d):
    """Every path under `d` with size and mtime; {} when it does not exist."""
    out = {}
    d = Path(d)
    if not d.exists():
        return out
    for r, dirs, files in os.walk(d):
        for n in dirs + files:
            p = os.path.join(r, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


class Isolation(unittest.TestCase):

    def test_T_ISO_1_the_suites_never_reach_the_users_files_or_repos(self):
        real_state = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") \
            / "corral-light"
        watched = (real_state / "worktrees", real_state / "worktree-registry")
        before = {str(d): listing(d) for d in watched}
        box = Path(tmpdir(self, "corral-iso-"))
        (box / "home").mkdir()
        (box / "tmp").mkdir()
        log = box / "git-cwds.log"
        env = {k: v for k, v in os.environ.items()
               if k not in ("CORRAL_LIGHT_STATE", "CORRAL_LIGHT_WORKTREES", "GIT_DIR",
                            "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        env.update(HOME=str(box / "home"), TMPDIR=str(box / "tmp"), CORRAL_WT_TEST="1",
                   CORRAL_WT_ISO_LOG=str(log), PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", *SUITES],
                           cwd=box / "tmp", env=env, capture_output=True, text=True,
                           timeout=480)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        cwds = [c for c in log.read_text().splitlines() if c] if log.exists() else []
        self.assertGreater(len(cwds), 100, "the spy saw almost no git calls; is it wired?")
        inside = os.path.realpath(box) + os.sep
        outside = sorted({c for c in cwds if not (c + os.sep).startswith(inside)})
        self.assertEqual(outside, [], "git ran outside the suite's temp dir")
        self.assertEqual({str(d): listing(d) for d in watched}, before,
                         "the real worktree root or registry changed")


if __name__ == "__main__":
    unittest.main()
