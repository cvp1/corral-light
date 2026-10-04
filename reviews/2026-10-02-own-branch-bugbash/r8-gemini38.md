SHIP — All round 1 through round 7 blocking defects have been verified as resolved, and comprehensive audits of worktree lifecycle, op journalling, concurrency locks, and push transport security (including `push_urls`, `rewrite_rule`, `transport_override`, and post-push verification) revealed no unhandled vulnerabilities meeting the ship bar.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. [`worktrees.py:1509`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1509): `ls-remote` verification in `push()` omits `check=False`, so a network drop immediately following a successful push raises an unhandled `GitError` that leaves the op in `intent` instead of marking it `unknown`, requiring a hub restart or `corral-light worktrees` to reconcile.
2. [`worktrees.py:1434`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1434): `_TRANSPORT_KEYS` blankets all repository-scoped `http.*` configurations, meaning benign repo-local tuning (e.g. `http.postBuffer`) intentionally refuses Publish until relocated to global git config.
3. [`worktrees.py:1762`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1762): Discard's settle check intentionally refuses once with `Refused("changed")` while an agent is actively writing (T-RMV-11 design choice), requiring the user to trigger Discard a second time once the agent stops.
4. [`worktrees.py:1712`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1712): The process scan skips non-dumpable processes whose `/proc` cwd and file descriptors return `EACCES` rather than failing closed.
5. [`worktrees.py:1554`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1554): `open_pr` deduces `origin_repo` solely from `push_urls(entry, remote)[0]`; repositories configured with asymmetric fetch and push URLs across different GitHub organizations could misattribute fork ownership.
6. [`worktrees.py:1742`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L1742): Stale `index.lock` files identified from terminated agents are renamed to `.corral-stale-<ts>` rather than unlinked, allowing orphaned lock files to accumulate in `.git/worktrees/<name>/`.
7. [`sessions.py:2550`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/sessions.py#L2550): Synchronously running `verify()` on every turn dispatch and resume invokes three git subprocesses per message, adding latency on high-load or remote/NFS mounts.
8. [`worktrees.py:814`](file:///home/USER/.cache/corral-bugbash-2026-10-03-r8/gemini38/corral-light/worktrees.py#L814): Cross-filesystem repositories are allowed without warning, relying on `git worktree move` remaining confined to `CORRAL_LIGHT_WORKTREES`.

## ONE SENTENCE

The operator is most likely to be tripped up when Discarding an active pane whose agent is still outputting text, causing the settle preflight to refuse once with "files changed since you opened review; refresh it" before succeeding on the second click.
