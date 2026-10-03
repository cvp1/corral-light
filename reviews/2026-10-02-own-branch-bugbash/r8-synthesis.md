# Own-branch bug bash, round 8: synthesis

Reviewed: f99d8b0, all three seats, same sandbox and ship bar (r8-charge.md).
No seat wrote outside its sandbox; Grok's path hits are process listings and
text it read.

| Seat | Verdict | Blocking |
|---|---|---|
| Codex gpt-6-astra | SHIP | none |
| Gemini 3.8 flash high | SHIP | none |
| Grok 4.7 | FIX FIRST | 1 PROVEN |

## Blocking, fixed after this round (tests in BugBash7Publish)
1. Grok: a repository include.path (or .git/config itself, as a symlink) that
   points at a fifo served the destination checks a clean config and served
   `git push` and its ls-remote check a rewrite to another repository;
   Publish recorded success for the confirmed URL. Now: repository-scoped
   include.* and includeIf.* keys are refused; the repository's config and
   config.worktree must be regular files; and their sha256 is taken before
   the checks and again after the push and its check. If it changed, the
   push op is `unknown` and Publish reports that, never success.

## Non-blocking, fixed too
- test_BB6_14 now uses a global pushInsteadOf, which only the --null parser
  can catch; it fails against the round 6 parser (Codex, Grok).

## Non-blocking, left as they are
- A regular config replaced and restored entirely between the two
  fingerprints is not detected (suspected, not reproduced).
- The user's global and system config, hub environment and ~/.ssh/config;
  pre-push hooks (spec D5); server redirects; harmless repo-local http.* or
  url.* keys refuse Publish.
