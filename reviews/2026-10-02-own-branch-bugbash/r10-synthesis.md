# Own-branch bug bash, round 10: synthesis

Reviewed: 33b4ad2 (round 9 fixes), same sandbox and ship bar (r10-charge.md).
No seat wrote outside its sandbox; Grok's hits are text it read.

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | none: OpenAI's safety filter ended the turn | 1, named in its progress notes, reproduced here |
| Grok 4.7 | FIX FIRST | 1 PROVEN |

## Blocking, fixed after this round (tests in BugBash9*)
1. Codex (reproduced here): verify() read the worktree's .git with a plain
   Python read, so a fifo there blocked it forever, and a resume holding the
   pane's action lock with it. Every Python read of a file the agent can
   replace (the .git file, the index, the config files) now goes through
   read_regular(): opened non-blocking without following links, and refused
   unless it is a regular file.
2. Grok: since round 9, Publish runs git with cwd at the git dir, so a
   relative push URL (../proj.git) named a different repository than the
   one the user meant, and a decoy there received the push while Publish
   recorded success. A relative local push URL is now refused (reason
   `relative`); restart checks of an interrupted push run in the same
   repository context and treat a relative URL as unknown.

## Non-blocking, left as they are
- The round 8 config swap-and-restore race (suspected, not reproduced).
- Harmless repo-local http.*, url.* or include.* keys refuse Publish.
- Hooks (spec D5), server redirects, and the user's global config and
  environment.
