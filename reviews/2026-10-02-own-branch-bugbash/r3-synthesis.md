# Own-branch bug bash, round 3 (ship decision): synthesis

Reviewed: 452a623 (round 2 fixes plus the round 1 leftovers), each seat in a
private clone under ~/.cache/corral-bugbash-2026-10-03-r3/<seat>/ with every
default state path pinned into the seat sandbox. Charge: r3-charge.md, with an
explicit ship bar. Grok ran under auto with no permission cards (the hub now
has b0fda02). A tool-call audit found no write outside any sandbox; Grok's
seven path hits are text it read (the charge, the spec, code comments).

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | DO NOT SHIP | 4 PROVEN |
| Grok 4.7 | FIX FIRST | 1 PROVEN |

## Blocking, all fixed after this round (tests in BugBash4* in test_worktrees.py)
1. Codex: the first agent start held no action lock, so Discard could run
   while the agent started, leaving it alive in trash. create() now starts the
   agent under the pane's action lock, and review actions refuse `starting`.
2. Codex: a busy refusal released a hold it never took; parking the queue
   bumped the attachment generation, so the live agent's events and
   permission cards were dropped. Only a hold the call took is released, and
   type-ahead behind a failed action on a live agent is parked without
   touching the attachment.
3. Codex: push redirection by an empty insteadOf prefix, and by a URL that
   names another remote. rewrite_rule matches empty prefixes and asks git
   (`ls-remote --get-url`) what it makes of the URL; anything but the literal
   URL is refused.
4. Codex: resume and send were allowed after the worktree was switched to
   another branch. The dispatch gate runs verify() (branch, admin dir, root)
   as well as the agent-folder check.
5. Grok: restart recovery of an interrupted PR op settled on any open PR with
   that branch name. It now keeps only a PR whose head owner is the journalled
   head's owner, the same rule as open_pr; none means `not_done`.

## Non-blocking, fixed too
- fit_review also trims the too_big inventory and the ignored sample (Codex
  measured 2.36 MB of metadata against the 2 MiB cap).
- open_pr no longer reuses a PR with no head owner (Gemini, Grok).
- rewrite_rule refuses when git config cannot be read (Grok).
- The CLI resolve hint says that `git reset -q` drops index-only content (Gemini, Grok).

## Non-blocking, left as they are
- Discard's settle check can refuse once while files are still changing;
  the second Discard succeeds (by design, T-RMV-11).
- A non-dumpable process (cwd and fds unreadable) is not counted by the
  Discard scan; failing closed would block every Discard on a normal desktop.
- `create` on a GitTimeout after `worktree add` leaves a `missing` entry and
  no pane (Grok); the entry is listed by `corral-light worktrees`.
- Stale index.lock files are renamed, never deleted, so they can accumulate.
- Some tests assert on source text (BB3_21, BB3_22).
