ROUND 2 — same panel, same fixed constraints C1–C4, same rules (text only; platform knowledge allowed and labelled).

Below are the other two reviewers' round-1 reviews of the v2 plan.

YOUR JOB — converge on a buildable design
1. CROSS-EXAMINE. For each other reviewer: the gap or fix of theirs you most agree with, and the claim you think is WRONG (factually or as a design), with why. Change any of your own round-1 verdicts if warranted and say so.
2. SETTLE THE DISAGREEMENTS — one recommendation each, 2–4 sentences, satisfying C1–C4:
   a. Scheduler: in-container loop with catch-up, or a host login agent / timer that triggers jobs inside the container (or both)? Include what launchd StartCalendarInterval actually does after sleep, and what happens when Docker is not running.
   b. General host commands: SSH (with a login-PATH shell wrapper and host-key pinning) or structured hostd verbs only? Where exactly is the line?
   c. Logins: device code / pasted token everywhere, a callback `expose` proxy, or both — per lane where you know it.
   d. Dev servers and ports from panes: published range, on-demand `expose`, or both.
   e. File watching over Docker Desktop virtiofs: what to do in the hub, in Seed, and for the CLIs.
3. CONSOLIDATED hostd VERB SET and its threat model in at most 10 lines.
4. PHASE 0 EXIT CRITERIA: at most 8 checks, each runnable on every lane, that together prove parity on the daily Mac.
5. ONE-LINE BOTTOM LINE for Craig: is this buildable as constrained, and what is the single biggest risk?

FORMAT: Markdown, under 1,100 words. No preamble.
