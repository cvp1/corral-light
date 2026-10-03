# Own-branch bug bash, round 6: synthesis

Reviewed: 466da50 (round 5 fixes), same sandboxed seats and ship bar
(r6-charge.md). No seat wrote outside its sandbox.

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Grok 4.7 | FIX FIRST | 1 PROVEN |
| Codex gpt-6-astra | none: its ChatGPT usage limit ended the turn after it confirmed the round 5 fixes held | none reported |

## Blocking, fixed after this round
1. Grok: a repo-local `http.curloptResolve` (plain or URL-scoped) sent a
   confirmed `http://` push to another address, and the check after it used
   the same mapping. The transport rule no longer lists known keys: any
   repository-scoped key in the `http` or `ssh` sections, plus
   core.sshCommand and core.gitProxy, refuses Publish and is named.

## Non-blocking, fixed too
- The Publish dialog lists remote names split on newlines only (Gemini).

## Non-blocking, left as they are
- Global and system git config, the hub environment and ~/.ssh/config can
  still steer a push; they are the user's own (stated in the charge).
- A legitimate repo-local http.* setting (for example http.postBuffer) now
  refuses Publish until it moves to the global config; the message names it.
- The deliberate choices from r3 to r5.
