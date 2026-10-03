I'll review the round-3 fixes against the ship bar, starting with the history, the synthesis, and the new code paths that can lose work or bypass a gate.The round-3 diff is in the action lock, park path, push URL check, dispatch `verify()`, and PR owner match. I'll read those paths and the tests that claim they hold.The push check is the one that can still send a commit somewhere else. I'll test how Git resolves `insteadOf` and `pushInsteadOf` against the new code.SHIP — Round 3's five blockers hold on the current tree (313 isolated tests OK), and nothing new loses work, leaves an agent outside its branch or in trash, bypasses an unknown/intent gate, redirects a confirmed push, or deadlocks a pane.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. `verify()` now sits in `_worktree_blocked_reason` (`sessions.py:2424`), so a switched or detached HEAD blocks send, resume, commit, publish, and Discard together. Repair is a terminal `git switch` back onto `refs/heads/corral/…`.
2. `fit_review` empties `too_big` before shrinking `ignored.sample` (`worktrees.py:1207`). A one-entry `too_big` list becomes `[]` with `too_big_omitted: 1` while the JSON still fits; `app.js:1342` banners only when `big.length`, and the Publish button's size check (`app.js:1293`) goes green. The hub still refuses that push on dirty status.
3. `release_hold` clears `held` before `_park_held_queue` (`sessions.py:1627`). A concurrent send can dispatch type-ahead a failed action meant to park. The message is sent.
4. That same `verify()` runs under `_turn_lock` with locks enabled (`sessions.py:1430`, `worktrees.py:919`). A HEAD lock from the agent's own git stalls the send or fails it as "detached HEAD".
5. A process whose `/proc` cwd and fds are both unreadable is still omitted from the Discard scan (`worktrees.py:1602`). A non-dumpable writer can be moved with the tree.
6. Discard's settle check can refuse once while files are still changing (T-RMV-11). The next Discard succeeds.
7. `test_BB4_10` accepts `remote_changed` as proof the empty prefix was caught; `test_BB4_33` checks `rewrite_rule`'s return value and never calls `push()`. `push()` does call it (`worktrees.py:1450`).
8. `create` on `GitTimeout` after `worktree add` still leaves a `missing` registry entry and no pane.

An agent that checks out another branch or detaches HEAD, which these tools do routinely, makes the pane refuse every later prompt and Discard until symbolic HEAD is put back from a terminal.
