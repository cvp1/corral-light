# Resilience roadmap — progress (v2 order, 2026-09-28)

Plan: `docs/RESILIENCE-REVIEW-2026-09-28.md` §6. One line per item: commit,
what was verified live vs by test, and anything left out and why. Suite
baseline before item 1: `python3 test_corral_light.py` ran 199 (3 skipped) —
its `__main__` block sat mid-file, so 12 tests after it never ran as a
script; item 1 moved it to the end.

| # | Item | Commit | Verified | Left out / notes |
|---|---|---|---|---|
| 1 | P0-a′ resume from `dead` | (this commit) | test: a real fake-ACP agent SIGKILLed mid-conversation, typing resumes it (`resumed` from=dead), the stale queue is parked in a `note` and never reaches the agent, context survives, `session/load` replay suppressed, a late `agent_exit` from the dead attachment is fenced by generation | UI (↻ on the dead row, dead composer, Resume on the notice card) checked by syntax + string test only — no browser run on this host |
