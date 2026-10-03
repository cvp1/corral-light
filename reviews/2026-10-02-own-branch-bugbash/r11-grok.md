I'll review the round-10 publish and read-path fixes against the ship bar, starting with the synthesis, the spec, and the commits since round 10.Round 10 closed the fifo hang and relative push URLs. Next I’ll read the publish path and look for any remaining split between the check and the push.The publish checks and the push still look like they can disagree. I’ll probe URL forms, restart settlement, and repo settings the denylist does not cover.The investigation is done. I'll confirm the settlement line numbers, then write the ship decision.FIX FIRST — hub restart settles an interrupted Publish as done and records `published.url` as the confirmed remote when only a repo named by `url.*.insteadOf` has the commit.

## BLOCKING FINDINGS

**1. High. PROVEN. `worktrees.py:2129` (`resolve_op`), opened by `worktrees.py:1614`. Restart check confirms the wrong repo.**

Trigger. `push()` journals the op as `intent` (`worktrees.py:1614`) and then runs `git push` with no handler for `GitTimeout` (`worktrees.py:1616`). A repo `pre-push` hook that sleeps past `PUSH_TIMEOUT_S` (120s; reproduced at 2s) raises `GitTimeout` and leaves the op `intent`. The same window is any kill or hang after `begin_op`. The pane stays blocked until a hub restart. Restart calls `reconcile` (`sessions.py:2525`), which calls `resolve_op`. The CLI `resolve` command only settles `unknown` ops, so it cannot close this.

What goes wrong. For a push, `resolve_op` refuses only a relative URL, then trusts `git ls-remote` of the journalled URL (`worktrees.py:2133`). `ls-remote` applies `url.*.insteadOf`. It does not re-run `rewrite_rule`, `transport_override`, or the config fingerprint. After the timeout the agent sets `url.<decoy>.insteadOf` to the confirmed absolute URL and plants the oid on the decoy at the journalled ref. Restart then marks the op `done` / `stage=done`, sets `published.url` to the confirmed URL, and reports "the remote has it". The confirmed remote has no such ref. Live `wt.push` of that same config is refused (`remote_changed`), because `git remote get-url --push` rewrites. This hole is recovery only. It is the same class as the round-10 relative-URL restart bug, which was closed only for relative paths. A user who then Discards has the only copy of the branch on a repo they never confirmed.

Minimal fix. In the `kind == "push"` branch of `resolve_op`, if `rewrite_rule(entry, op["url"])` or `transport_override(entry)` is non-empty, return `unknown`. Do not mark the op done and do not write `published`. `insteadOf` makes `ls-remote --get-url` return the decoy, so `rewrite_rule` already sees it, and `transport_override` already refuses a local `url.*` key.

Repro. `scratch/probe_r11_resolve.py` (exit 0): live push `Refused reason=remote_changed`; `resolve_op` note "the remote has it"; op `done`/`done`; `published.url` is `real.git`; `real has ref: False`; `decoy has ref: True` at `912ba41ddb46…` `refs/heads/corral/fix-login`. `scratch/probe_r11_timeout.py`: `GitTimeout` after 2s, ops `(commit, done, done)`, `(push, intent, None)`. Official suite: 342 tests OK. It does not cover this case (`BB9_22` only checks that restart `ls-remote` uses `GIT_DIR`).

## NON-BLOCKING

- `file://home/user/repo.git` (two slashes) is not treated as relative; git receive-packs `/user/repo.git`. The dialog shows the raw string. Proven by `GIT_TRACE` in `scratch/r11g`. No successful mis-publish: the stripped path was not created.
- `file:inside.git`, `:../x`, and `file://../x` are not relative. The first two are SSH. The third is absolute `/x`. Stable across cwd, so not the round-10 bug. The dialog shows the raw string.
- `test_BB6_13` accepts reason `rewrite` or `transport`, so a broken `--null` parser still passes when `transport_override` refuses `url.*`.
- A config swap after the checks and restored before the second fingerprint is still invisible. Suspected since round 8. Not reproduced.
- Any repo-local `http.*`, `url.*`, or `include.*` refuses Publish, including a harmless `http.postBuffer`. A global `pushInsteadOf` that matches an explicit pushurl also refuses, so those remotes cannot publish.
- A symlinked index is refused (`O_NOFOLLOW` in `read_regular`) even when it points at the regular file git would read. False refuse. It does not leave an op stuck.
- `push.recurseSubmodules=on-demand` under `GIT_DIR` prints `cd to sub failed` and still exits 0. The submodule is not pushed. `remote.*.receivepack`, `mirror`, and `alias.push` did not divert a literal URL (git 2.55).
- Stated choices, unchanged: global or system transport and hub env are honoured; cross-filesystem repos are allowed; a non-dumpable process is skipped by the Discard scan; Discard may refuse once while files are still changing; a branch switch blocks the pane, Discard included, until it is switched back.

The first interrupted Publish, from a slow remote, a hub restart, or an agent pre-push hook, leaves the pane blocked until restart, and that restart can record the branch published at the confirmed URL when only a repo named by `url.*.insteadOf` has the commit.
