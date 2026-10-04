SHIP: All round 1–11 blocking defects have held against re-test variants, the double-gated review/dispatch pipeline prevents races during intent and unknown ops, and push destination verification reliably prevents unconfirmed remote routing.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. `test_worktrees.py:3401` (`test_BB6_13`): asserts `reason in ("rewrite", "transport")`, so if `rewrite_rule`'s `--null` parser fails on spaces in repo-local config, the test still passes under `transport_override` (covered in isolation only by `test_BB6_14` with global config).
2. [worktrees.py:1496](file:///home/USER/.cache/corral-bugbash-2026-10-03-r12/gemini38/corral-light/worktrees.py#L1496) (`rewrite_rule`): scans global and system git configs without scope filtering, causing Publish to refuse with `Refused("rewrite")` whenever the user has common global rewrites like `url.git@github.com:.insteadOf = https://github.com/` matching their confirmed HTTPS push URL.
3. [worktrees.py:1516](file:///home/USER/.cache/corral-bugbash-2026-10-03-r12/gemini38/corral-light/worktrees.py#L1516) (`_TRANSPORT_KEYS`): the regex `^(http\..+|ssh\..+|...)` blanket-refuses harmless repository-scoped transport configs (e.g. `http.postBuffer`, `http.lowSpeedLimit`, `http.version`) that do not alter the remote host or destination.
4. [worktrees.py:2151](file:///home/USER/.cache/corral-bugbash-2026-10-03-r12/gemini38/corral-light/worktrees.py#L2151) (`resolve_op`): an interrupted push whose remote ref moved ahead to another commit while offline resolves as `not_done` rather than `unknown` (safe against data loss, but requires re-triggering Publish).
5. [worktrees.py:1265](file:///home/USER/.cache/corral-bugbash-2026-10-03-r12/gemini38/corral-light/worktrees.py#L1265) (`read_regular`): opens the index with `O_NOFOLLOW`, so even if `verify()` allows an index symlink pointing within `want_admin` (`test_BB10_3`), subsequent snapshot reads raise `IdentityError("tampered")`.
6. [sessions.py:2603](file:///home/USER/.cache/corral-bugbash-2026-10-03-r12/gemini38/corral-light/sessions.py#L2603) (`_worktree_action`): when an action is refused (such as an uncommitted status or dirty tree), `held` is cleared with `drain=False`, which parks any user typing submitted during the modal rather than leaving it in the queue for a retry.

## ONE SENTENCE

What most likely bites the operator in the first week: Developers with standard global git config rewrites (such as `url.git@github.com:.insteadOf = https://github.com/`) will find Publish refused because `rewrite_rule` inspects global config and rejects any matching URL prefix rewrite.
