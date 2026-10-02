# Panel synthesis — isolated branches and review (2026-10-01/02)

Plan reviewed: `docs/worktree-review-plan.md` v1 (round 1) and v2 (round 2).
Result folded into v3 of the same file. Each reviewer read pasted text only,
from an empty scratch directory.

| Seat | Model (verified) | How | Notes |
|---|---|---|---|
| Sol | gpt-6.1-sol / high | hub echo | both rounds clean |
| Gemini | gemini-3.8-flash-high | hub echo | both rounds clean |
| Grok | grok-4.7 / high | hub echo | Round 1 at xhigh stalled overnight with thoughts and no text (pane went `uncertain`); retried at high. Twice a human message typed into the pane ended the consult wait early; the answer was collected from the pane's events after its turn ended. Round 2 at high was clean. |

## Verdicts

| Part | Round 1 (Sol / Gemini / Grok) | Round 2 on v2 |
|---|---|---|
| Approach | BUILD / RESHAPE / RESHAPE | BUILD / BUILD / BUILD |
| worktrees.py | RESHAPE ×3 | RESHAPE ×3 (commit epilogue, discard) — fixed in v3 |
| Lifecycle | RESHAPE / BUILD / RESHAPE | RESHAPE / BUILD / RESHAPE (queue drain, writers) — fixed in v3 |
| Review UI | RESHAPE / BUILD / BUILD | BUILD ×3 |
| Test plan | RESHAPE ×3 | RESHAPE / BUILD / RESHAPE (contradictory tests) — fixed in v3 |

## Converged

- Do not merge into a checkout the user has open; v1 lands work with Push & PR and a copied merge command.
- Never automatic `git worktree prune`; it cannot be scoped.
- Discard must not delete: recovery ref, move to trash, typed purge.
- Actions bind to an immutable reviewed tree; counts never validate content.
- Hub commit by plumbing, no hooks, said on the button.
- Stop writers before discard; do not drain queued messages into a trashed pane.
- The v2 `read-tree` epilogue was unsafe (all three); v3 replaces it with a journalled protocol.
- Pre-trusting Claude must be proven narrow or not done.
- No `safe.directory=*` (Sol and Grok against Gemini's round-1 suggestion; Gemini did not defend it).

## Still split

- Location. Sol and Grok: state dir. Gemini: sibling of the repo, for editors, monorepos and Docker mounts. v3 keeps the state dir; Craig decides.
- Whether `git worktree move` refuses a dirty worktree (Gemini says yes, Grok says it only refuses locked, submodule or cross-device cases). Phase 0 step 0.5 settles it; v3 handles both.
- Whether `merge-tree --write-tree` accepts `--name-only`. Phase 0 freezes the argv from fixtures.

## Bottom lines (round 2)

- Sol: build v2 after fixing index reconciliation and disposal dispatch.
- Gemini: architecturally sound; fix `worktree move` on dirty trees and the post-commit index refresh.
- Grok: build v2's shape; do not implement Commit or Discard until the index step is pinned and replayable and Discard kills writers and drops the queue first.

All three conditions are addressed in v3 §2.2 (commit protocol, discard) and the ship gate items 6–8.

## Status after the build (2026-10-02, branch `worktrees`)

Every converged finding, with where it now lives and the test that holds it.

| Finding | Status | Evidence |
|---|---|---|
| No merge into an open checkout | Fixed | Review offers Commit, Push (& PR) and Copy merge command only; nothing merges. README "Own branches". |
| Never automatic `git worktree prune` | Fixed | T-RMV-9 (source has no prune), T-REC-2 and T-REC-5 (spy: no prune call). |
| Discard must not delete | Fixed | Recovery ref, move to trash, typed purge: T-RMV-1..8, T-CLI-11..13. |
| Actions bind to an immutable reviewed tree | Fixed | T-SNP-*, T-SAFE-1/2, T-RTE-8/12, T-UI-9/10; the browser posts the tree it showed. |
| Hub commit by plumbing, no hooks, said on the button | Fixed | `commit_tree`; enabled Commit's tooltip and the README say commit hooks do not run (selftest_review). |
| Stop writers before discard; no drain into a trashed pane | Fixed | T-RMV-11, T-RMV-11b, T-RMV-13, T-RMV-14. |
| Replace the `read-tree` epilogue with a journalled protocol | Fixed | T-CMT-9..12; T-CRS-2 kills at every journalled stage. |
| Pre-trusting Claude only if narrow | Fixed (not done) | T-LIF-14: no trust entry, no permission-mode change. |
| No `safe.directory=*` | Fixed | Not set anywhere in product code. |

Still split, settled:

- **Location.** Craig chose the state dir (D2, v3.1). `CORRAL_LIGHT_WORKTREES` moves it; tmpfs is refused.
- **`worktree move` on a dirty tree.** Phase 0 step 0.5: it moves without `--force`; T-RMV-12 keeps it so.
- **`merge-tree --name-only`.** Phase 0 step 0.3 froze the argv from fixtures, on git 2.38.5 and 2.55.

Round 2 conditions: Sol (index reconciliation, disposal dispatch), Gemini (dirty move, post-commit index) and Grok (pinned, replayable index step; Discard kills writers and drops the queue) are covered by the rows above.

Crash coverage: T-CRS-1 (create), T-CRS-2 (commit), T-CRS-3 (push), T-CRS-4 (PR) and T-CRS-5 (discard) kill a real subprocess at each journalled point. They exercise `worktrees.py` and `reconcile`. They do not kill a running hub. The plan's "real hub restart with live worktree panes" stays a P4 manual check.

Open before ship (plan §5.4), none of them code:

- Lane matrix rerun on the built feature (P4), including a `gh` PR on a throwaway GitHub repo.
- One week of dogfood with no lost work.
