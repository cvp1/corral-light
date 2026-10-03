I’ll review the prior findings and round 7 changes, run the isolated test suite, then probe Publish for destination changes that could still bypass confirmation.The suite is running. The round 7 parser fix looks sound on inspection. I’m now checking Git’s other destination-selection paths, particularly legacy remote files and settings outside the blocked transport sections.The suite finished: 310 tests passed; the other 17 failed because the sandbox denies sockets. The Publish regressions passed. I’m checking a few remaining configuration and lifecycle variants before making the ship decision.The additional destination checks passed, including the space-containing global rewrite rule with the local-config guard inactive. I found one test weakness: `test_BB6_14` also passes with the old broken parser because `ls-remote` catches its `insteadOf` case. The new scratch test exercises `pushInsteadOf` and distinguishes the old parser from the fixed one. I haven’t reproduced a ship-bar blocker.SHIP

No ship-bar blocker reproduced. The round 7 destination fixes hold under the existing Publish regressions and additional configuration variants.

**BLOCKING FINDINGS**

None proven.

The prescribed suite ran **327 tests: 310 passed; 17 failed solely because the sandbox denies sockets**. Existing round 1–7 regression tests passed, including `BugBash3Publish`, `BugBash4Publish`, `BugBash5Publish`, and `BugBash6*`. [Suite log](/home/cvande/.cache/corral-bugbash-2026-10-03-r8/astra/scratch/suite.log).

Seven additional checks passed, covering global rewrite bases containing spaces, included repository transport settings, remote names with only transport options, truncated configuration reads, and ordinary nontransport settings. The global `pushInsteadOf` case distinguishes the old broken parser from the fixed parser without relying on the repository transport guard. [Tests and repro](/home/cvande/.cache/corral-bugbash-2026-10-03-r8/astra/scratch/test_r8_publish.py).

**NON-BLOCKING**

- **Low · PROVEN — misleading parser regression test:** [test_worktrees.py:3405](/home/cvande/.cache/corral-bugbash-2026-10-03-r8/astra/corral-light/test_worktrees.py:3405) passes with the old parser because its `insteadOf` rule is caught by the later `ls-remote` check; use the global `pushInsteadOf` variant in the scratch tests to test parsing independently.
- **Low · PROVEN — conservative refusal:** harmless repository-local `http.*`, `ssh.*`, or unrelated `url.*` settings prevent Publish; this is the documented tradeoff, with the offending setting identified.
- **Validation limitation:** HTTP routes and socket-dependent resilience behavior remain unverified in this sandbox; their failures are environmental, not product findings.

**ONE SENTENCE**

Craig is most likely to encounter Publish refusing a legitimate repository-local Git setting and requiring configuration cleanup before retrying.
