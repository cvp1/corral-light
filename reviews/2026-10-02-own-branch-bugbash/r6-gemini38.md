SHIP — All round 5 blocking defects (drain generation leakage across `/clear`, remote name tokenization, and repository-level transport hijacking via `core.sshCommand`) are verified fixed and held under repro, and no ship-bar violations (data loss, gate bypass, unconfirmed push destination, or pane deadlock) remain in the codebase.

### BLOCKING FINDINGS

None.

None of the investigated candidates crossed the ship bar:
- **`_drain` generation binding and hold handling** ([sessions.py:1479-1530](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L1479-L1530)): The drain loop is bound to `born = self._generation` at spawn. If `/clear` or `pause()` runs, `self._generation` advances and `self._generation != born` causes the old drain worker to terminate immediately without touching the replacement attachment's queue. While a review action holds the pane (`p.held = True`), incoming prompts and peer turns queue behind the hold in `_dispatch()`; once the action finishes, `release_hold(drain=True)` restarts the drain under `_turn_lock`. No queue is stranded and no dispatch bypasses the hold.
- **Push destination confirmation and transport overrides** ([worktrees.py:1406-1520](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/worktrees.py#L1406-L1520)): `rewrite_rule()` splits remote names on `\n` to prevent whitespace-tokenized remote hijacking, and `transport_override()` queries `git config --show-scope` to block repository-local overrides of `core.sshcommand`, `core.gitproxy`, `http.*proxy`, TLS configs, and `ssh.variant`. Direct push to `push_url` ignores remote-specific settings like `remote.<name>.proxy`, and the post-push `ls-remote` check confirms the pushed commit reached the intended URL.
- **Turn ledger and parked peer messages** ([sessions.py:804-835](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L804-L835)): `_report_parked()` immediately emits `peer_result` with `delivered=False` and closes turns in the ledger as `interrupted`, preventing peer panes from hanging until timeout.
- **Concurrency and crash gates**: All mutating actions verify the absence of `unknown` or `intent` ops via `refuse_open_ops()` and `_worktree_blocked_reason()`. Actions acquire non-blocking `_action_lock` to prevent concurrent operations, and all 325 test cases pass cleanly under the isolated environment.

### NON-BLOCKING

1. [sessions.py:2627](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L2627): `worktree_remotes` still parses remote names using `.text.split()` instead of `split("\n")`; a remote name containing non-newline whitespace will fail to list in the Publish UI dialog, though git push itself remains secure.
2. [worktrees.py:1426](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/worktrees.py#L1426): `_TRANSPORT_KEYS` does not include `remote.<name>.proxy` or `remote.<name>.proxyAuthMethod`; while direct URL pushes do not resolve per-remote proxy settings in Git, adding them would provide defense-in-depth.
3. [sessions.py:828](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L828): In `_report_parked`, turns parked by a clean Discard (`drain_after=False`) on a live agent are marked interrupted in the ledger with `why="a review action failed while it waited"` rather than noting an intentional discard.
4. [worktrees.py:1412](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/worktrees.py#L1412): `rewrite_rule()` splits `git remote` on `\n` without filtering empty strings, leaving a trailing `""` in `names`; harmless because empty push URLs are rejected earlier, but `[n for n in rr.text.split("\n") if n]` is cleaner.
5. [sessions.py:1516](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L1516): The post-verification `if self.held:` guard inside `_drain` is defensive dead code because `_worktree_action` checks `p._turn_running`, which remains `True` throughout the drain iteration unless generation advances.
6. [sessions.py:2496](file:///home/USER/.cache/corral-bugbash-2026-10-03-r6/gemini38/corral-light/sessions.py#L2496): `_refuse_queued_into_blocked_branch()` calls `_worktree_blocked_reason()` with default `include_intent=True`, dropping queued turns if an action previously crashed into an unrecovered `intent` op.

### ONE SENTENCE

An agent that checks out a foreign Git branch during autonomous work locks the pane and blocks subsequent turns as well as Discard until the operator manually runs `git switch` back to the own-branch ref.
