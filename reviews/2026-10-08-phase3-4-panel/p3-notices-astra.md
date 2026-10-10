I’ll trace Phase 3’s validation, freshness, rail rendering, and FinOps notice generation, then check whether the listed tests cover those guarantees. I’ll only read files in this folder.
The rail renders notice text safely and counts the cards as quiet. I found two gaps to check against the spec: read-time expiry uses an unverified manifest, and a current `allowed_warning` with no percentage produces no FinOps notice.
**FIX-FIRST — read-time notice expiry trusts mutable, unverified manifest bytes, allowing notices to outlive their approved freshness limit.**

1. **Medium · PROVEN — `corral-light/modules.py:1283`, `:1310`.**  
   **Input → failure:** After a successful opted-in collection with `every_s=300`, change the installed manifest’s interval to `86400` and stop the collector. At `fresh_at + 601`, `notices()` still returns an unexpired notice: it reads the modified interval without checking the pinned digest. The approved ten-minute freshness limit becomes 24 hours. Verification on a later collector run would disable the module, but a stopped collector never performs that check.  
   **Smallest fix:** Store the verified notice opt-in and interval in core-owned metadata associated with the pinned generation, and use those values at read time instead of rereading mutable module code.

2. **Medium · PROVEN — `corral-light-finops/finops/view.py:51`, `:78`.**  
   **Input → failure:** A current Claude window with `status="allowed_warning"`, no utilization, and a future reset produces level `info`, then no notice. `_level_for(None, "current")` returns `info`, while the status override only promotes `ok`. Section 4.7 explicitly requires a notice for `allowed_warning`; utilization is optional. The tile also understates the condition.  
   **Smallest fix:** In the current-window branch, promote both `info` and `ok` to `warn` for `allowed_warning`, preserving `bad` and the stale/reset exclusions.

**TESTS**

- **Test that would still pass with its property broken:** `NOT-3` in `corral-light/selftest_inbox.mjs:288` replaces the production `el` helper with `mk`. Its “title is text” assertion would still pass if production `el` changed from `textContent` to `innerHTML`. The actual helper currently uses `textContent`; this is a test weakness, not an observed injection.
- **Most important missing test:** Collect a notice under a verified 300-second manifest, modify only the installed manifest’s interval, perform no further collection, and assert that `notices(fresh_at + 601)` returns no items.

Static inspection only; no tests, writes, network access, or hub interaction.

Bind notice eligibility and freshness to verified generation metadata so stopping collection cannot leave mutable module files in control of expiry.