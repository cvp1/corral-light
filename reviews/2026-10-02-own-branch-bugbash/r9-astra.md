FIX FIRST

A real agent can pass the dispatch gate and commit into another repository after its worktree’s `commondir` is changed. The registered branch remains untouched. This meets the ship bar for writing outside the pane’s own branch.

## BLOCKING FINDINGS

1. **High · PROVEN — Repository identity verification trusts the admin directory without checking where its `commondir` points.**

   **Locations:** [worktrees.py:909](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/corral-light/worktrees.py:909), [sessions.py:2502](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/corral-light/sessions.py:2502), [worktrees.py:1446](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/corral-light/worktrees.py:1446).

   **Trigger:** Create an own-branch pane and a local clone containing the same branch. Change the pane’s admin-directory `commondir` file to that clone. This file is inside the admin directory explicitly allowed by the agent’s write-location guard.

   **What goes wrong:** The `.git` pointer, original repository’s worktree registration, branch name, and initial OID all still satisfy `verify()`. It never compares Git’s actual common directory with the registered one. The dispatch gate returns `None`.

   The reproduction sends normal `write` and `commit` prompts through a real fake-ACP agent. It reports `commit rc=0`; the original branch stays at `f3e97bd…`, while the other repository’s branch advances to `ab3db214…`. A separate library probe makes hub Commit return success while likewise updating only the other repository.

   **Round-8 fix implication:** `config_fingerprint()` hashes the registered repository’s config, while Git reads the redirected repository’s config. A second test gives that actual config a symlink: every Publish check passes and execution reaches the push call. The test intercepts that call before execution; **an actual redirected push is not claimed**.

   **Minimal fix:** In `verify()`, resolve Git’s actual common directory from the worktree and require it to match `entry["common_dir"]`. Bind Publish’s config validation to that verified repository context.

   **Reproduction:** [test_r9_identity.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/scratch/test_r9_identity.py), [captured output](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/scratch/identity-test.log), [exact isolated command and supporting evidence](/home/cvande/.cache/corral-bugbash-2026-10-03-r9/astra/scratch/README.md).

## NON-BLOCKING

- Baseline validation: **330 tests; 313 passed**. All 16 errors and the single failure resulted from sandbox-denied sockets; HTTP behavior remains unverified.
- The run included the round-1/2 regressions, `BugBash3Publish`, `BugBash4Publish`, `BugBash5Publish`, `BugBash6*`, and `BugBash7Publish`; those passed.
- **PROVEN:** The revised `BB6_14` checks the parsed global `pushInsteadOf` rule specifically, closing its previous false-positive weakness.
- **PROVEN:** Even a harmless repository-local include refuses Publish; `BB7_1` explicitly exercises an include containing only `core.autocrlf=false`.
- **SUSPECTED:** A config changed and restored between fingerprints remains undetected; I did not reproduce that race and do not count it as another blocker.

The most likely first-week annoyance is Publish refusing Craig’s ordinary repository-local include configuration.
