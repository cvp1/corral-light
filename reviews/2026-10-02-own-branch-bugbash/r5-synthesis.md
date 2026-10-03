# Own-branch bug bash, round 5: synthesis

Reviewed: b2d2a73 (round 4 fixes), same sandboxed seats and ship bar
(r5-charge.md). No seat wrote outside its sandbox.

| Seat | Verdict | Blocking |
|---|---|---|
| Gemini 3.8 flash high | SHIP | none |
| Codex gpt-6-astra | FIX FIRST | 2 PROVEN |
| Grok 4.7 | FIX FIRST | 1 PROVEN |

## Blocking, all fixed after this round (tests in BugBash6* in test_worktrees.py)
1. Codex: a drain retired by /clear read the new attachment's generation on
   its next loop and sent type-ahead while a review action held the pane.
   _drain is now bound to the generation it started on, stops while the pane
   is held, and re-checks generation and hold under the lock before sending.
2. Codex: a remote name with a non-breaking space escaped str.split(), so a
   URL equal to that remote's name passed. Remote names are split on newlines
   only, and a failed listing refuses.
3. Grok: a repo-local core.sshCommand sent the push and its check to another
   repository while Publish reported success for the confirmed URL. Push now
   refuses repository-scoped transport overrides (sshCommand, gitProxy,
   proxies, TLS verification and CA settings, ssh.variant).

## Non-blocking, fixed too
- A parked message from another pane reports peer_result delivered=False at
  once instead of leaving its sender to time out (Gemini).

## Non-blocking, left as they are
- Transport settings in the user's global or system config, or the hub's
  environment, are honoured.
- start()'s broad except also stops an agent whose post-start save failed;
  the pane is dead and resumable.
- The deliberate choices listed in r3 and r4.
