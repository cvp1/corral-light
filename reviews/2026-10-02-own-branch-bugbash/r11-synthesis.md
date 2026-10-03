# Own-branch bug bash, round 11: synthesis

Reviewed: 58f4af0 (round 10 fixes), same sandbox and ship bar (r11-charge.md).
No seat wrote outside its sandbox; Grok's hits are text it read.

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | FIX FIRST | 3 PROVEN |
| Grok 4.7 | FIX FIRST | 1 PROVEN |

## Blocking, fixed after this round (tests in BugBash10*)
1. Codex: a worktree-scoped core.worktree pointing at the main checkout made
   the pane's ordinary git commands write there while verify() passed.
2. Codex: an index symlinked to the main checkout's index did the same for
   staging. verify() now checks git's effective layout: the toplevel must be
   the worktree, the git dir its own admin dir, and the resolved index must
   lie inside that admin dir.
3. Codex: a review action checked the gates before taking the pane's action
   lock, so an overlapping Commit could leave an op unknown in between. The
   gates are read again after the lock is taken.
4. Grok: a push that timed out (a slow pre-push hook) stayed `intent`, and
   restart settled it by an ls-remote that a url.*.insteadOf added since could
   send to a decoy. A push that fails to finish is now `unknown`, and restart
   runs rewrite_rule and transport_override before trusting ls-remote.

## Also fixed
- The check after a push: a failed ls-remote is `unknown`, never `intent`
  (Gemini).
- _dead() records the reason before the state, so no observer sees a dead
  pane with no reason (a pre-existing race one test hit once).

## Non-blocking, left as they are
- `file://host/path` URLs are passed as written; the dialog shows them raw.
- The round 8 swap-and-restore race; harmless repo-local http/url/include
  keys refusing Publish; hooks (D5); the user's global config and environment.
