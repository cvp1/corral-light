# Own-branch bug bash, round 7: synthesis

Reviewed: d673d22 (round 6 fix), same sandboxed seats and ship bar
(r7-charge.md). Codex did not run this round (its usage window had not
reopened). No seat wrote outside its sandbox.

| Seat | Verdict | Blocking |
|---|---|---|
| Grok 4.7 | SHIP | none |
| Gemini 3.8 flash high | FIX FIRST | 1 PROVEN |

## Blocking, fixed after this round
1. Gemini: rewrite_rule parsed `git config --get-regexp` on the first space,
   so a rewrite rule whose base URL held a space was misread and missed. With
   an explicit remote pushurl, a repo-local pushInsteadOf then sent the push
   elsewhere. rewrite_rule now reads `--null` output, and any
   repository-scoped `url.*` key is refused outright by transport_override.

## Non-blocking, left as they are
- A repo-local http.* or ssh.* key, even a harmless one, refuses Publish
  until it moves to the global config (named in the message).
- A pre-push hook (spec D5) runs during Publish and could also copy the
  commit elsewhere; it cannot change where the confirmed push goes.
- A confirmed http(s) server can redirect (git's default followRedirects).
- The user's global and system config, hub environment and ~/.ssh/config.
