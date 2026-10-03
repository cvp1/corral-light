# Own-branch bug bash, round 4: synthesis

Reviewed: e8a24eb (round 3 fixes), same sandboxed seats and ship bar as
round 3 (r4-charge.md). No seat wrote outside its sandbox; Grok's path hits
are text it read.

| Seat | Verdict | Blocking |
|---|---|---|
| Grok 4.7 | SHIP | none; all five round 3 blockers hold |
| Gemini 3.8 flash high | FIX FIRST | 1 PROVEN |
| Codex gpt-6-astra | none: OpenAI's safety filter ended the turn mid-review | 1 confirmed in its progress notes, 1 under probe |

Codex's partial report (r4-astra.raw) is kept as evidence, not as a verdict.

## Blocking, all fixed after this round (tests in BugBash5* in test_worktrees.py)
1. Codex: the gate ran when a message was queued, not when it was sent, so a
   queued turn ran after the turn before it switched the branch, or after an
   op became unknown. _drain now runs the gate before each queued turn; if
   blocked, that turn and the rest of the queue are not sent and are named.
2. Codex (probe cut off; reproduced here): a push URL that is also a remote's
   name, whose fetch URL is its own name, passed `ls-remote --get-url` while
   `git push` used that remote's pushurl. rewrite_rule refuses any URL equal
   to a configured remote name or a legacy .git/remotes or .git/branches file.
3. Gemini: a start() that failed with anything but AgentError (for example an
   unreadable config dir) left the pane `starting` for good, which now refuses
   every review action. start() marks any failure dead.

## Non-blocking, fixed too
- A worktree still in phase `intent` blocks dispatch (Gemini).
- release_hold parks the type-ahead in the same critical section that ends
  the hold, so a concurrent send cannot dispatch it (Grok).
- The refusal for a switched branch says how to switch it back (Grok).
- The size banner and the Publish check count trimmed too_big entries (Grok).

## Non-blocking, left as they are
- verify() runs on every dispatch (a few git calls); acceptable cost.
- An agent that switches branches blocks the pane, Discard included, until
  the branch is switched back; the message says how.
- The non-dumpable-process scan gap, the settle check, repos on other
  filesystems, and the `missing` entry after a create timeout (see r3).
