# Own-branch bug bash, round 9: synthesis

Reviewed: 65c9810 (round 8 fix), all three seats, same sandbox and ship bar
(r9-charge.md). No seat wrote outside its sandbox; Grok's hits are text it read.

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | FIX FIRST | 1 PROVEN |
| Grok 4.7 | FIX FIRST | 1 PROVEN (the same file, a different attack) |

## Blocking, fixed after this round (tests in BugBash8Hub, BugBash8Publish)
1. Codex: verify() trusted the admin dir's `commondir` file. An agent could
   point it at another clone holding the same branch, pass the dispatch gate,
   and commit there. verify() now asks git for the real common dir and
   requires it to be the registered one.
2. Grok: a fifo `commondir` served the real common dir to the destination
   checks and another one to `git push` and its check, so Publish recorded
   success for the confirmed URL. Every git call that decides or performs a
   push now runs with GIT_DIR set to the repository's git dir, so the
   worktree's .git file, its commondir and its config.worktree are never
   consulted for the push. The fingerprint also covers commondir and the .git
   file (regular files, and they must exist); a linked worktree's own
   config.worktree with a transport key is still refused.

## Non-blocking, left as they are
- A regular config swapped and restored between the two fingerprints
  (suspected, not reproduced).
- hook.*.event/command and .git/hooks/pre-push run on push (spec D5): a hook
  can copy the commit, but not change where the confirmed push goes.
- Harmless repo-local http.*, url.* or include.* keys refuse Publish.
- The user's global and system config, environment and ~/.ssh/config.
