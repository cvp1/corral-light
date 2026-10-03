SHIP — All blocking defects from rounds 1 through 8 are verified fixed; Publish destination checks now fingerprint repository config against fifos and symlinks, reject repo-scoped includes and transport redirects, and confirm pushes with post-push `ls-remote` while review and dispatch gates strictly serialize and enforce journalled op states.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. [`worktrees.py:1537`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1537): `ls-remote` in `push()` runs with `check=True`, so a network drop immediately following a successful push raises `GitError` and leaves the journal op in `intent` instead of marking it `unknown`, requiring hub restart or CLI reconcile.
2. [`worktrees.py:1431`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1431): `_TRANSPORT_KEYS` rejects all repository-scoped `http.*` configurations, meaning harmless repo-local tuning (e.g. `http.postBuffer`) refuses Publish until relocated to global git config.
3. [`worktrees.py:1762`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1762): Discard's settle check intentionally refuses once with `Refused("changed")` while an agent is actively writing (T-RMV-11 design choice), requiring the user to trigger Discard a second time once the agent stops.
4. [`worktrees.py:1712`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1712): Process scans in Discard intentionally omit non-dumpable processes whose `/proc` cwd and file descriptors return `EACCES` rather than failing closed.
5. [`worktrees.py:1587`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1587): `open_pr` deduces `origin_repo` solely from `push_urls(entry, remote)[0]`; repositories configured with asymmetric fetch and push URLs across different GitHub organizations could misattribute fork ownership.
6. [`worktrees.py:1742`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L1742): Stale `index.lock` files from terminated agents are renamed to `.corral-stale-<ts>` rather than unlinked, allowing orphaned lock files to accumulate in `.git/worktrees/<name>/`.
7. [`sessions.py:2550`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/sessions.py#L2550): Synchronously running `verify()` on every turn dispatch and resume invokes three git subprocesses per message, adding latency on high-load or network mounts.
8. [`worktrees.py:814`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r9/gemini38/corral-light/worktrees.py#L814): Repositories on a different filesystem than the worktree root are permitted without warning, relying on `git worktree move` remaining confined within `CORRAL_LIGHT_WORKTREES`.

## ONE SENTENCE

Craig is most likely to be tripped up when Discarding an active pane whose agent is still outputting text, causing the settle preflight to refuse once with "files changed since you opened review; refresh it" before succeeding on the second click.
