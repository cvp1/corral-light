# Own-branch bug bash, round 12: synthesis (ship decision)

Reviewed: 6c13414, same sandbox and ship bar (r12-charge.md).

| Seat | Verdict | Blocking |
|---|---|---|
| Grok 4.7 | SHIP | none |
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | none: its ChatGPT usage limit ended the turn before a verdict | none reported |

Decision (the operator, 2026-10-03): accepted on the two SHIP verdicts. Codex's
last completed review (round 11) had three blocking findings, all fixed in
6c13414 with tests (BugBash10*).

Non-blocking items carried forward: harmless repo-local http/url/include
keys refuse Publish; an agent that switches branches blocks its pane until
switched back; open_pr trusts the client's pr.repo on a crafted POST;
push.recurseSubmodules can leave a submodule commit undelivered; the
round 8 config swap-and-restore race (suspected, not reproduced).
