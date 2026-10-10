You are one of three independent reviewers on a panel, round three. The other two are different models from different vendors. The author is a Claude agent working for the operator, who makes the final call. Nothing is built yet.

WHAT YOU ARE REVIEWING
docs/delegates-module-plan.md, now rev 3: a generic, public Delegates module for Corral Light (a board of agents run on other machines under written charters), built on the module seam FinOps established. Round two on rev 2 returned AMEND, AMEND, AMEND; its record is reviews/2026-10-09-delegates-module-panel/synthesis-r2.md and r2-*.md. Rev 3 changed the model: the BOX is now the unit of the alarm, a box is "covered" only when a valid grant names it in `box:`, `unknown` presence keeps the level a box last had, revocation is ordered by a signed `issued` key with tombstones kept forever, the edit-distance typo rule is gone and security keys are required instead, "complete" is a per-file claim the operator must allow, the Phase 2 probe compares key blobs and takes its targets from a core-side allow, and the core is to accept any `core_api` up to its own.

Since round two, Phase 0 has been MEASURED on the operator's ranch host and inside the real collector sandbox on this host: docs/delegates-phase0.md (read it) and docs/fixtures/ranch-status-fixture.json (16 rows from the ranch's own selftest, with the real value types). Two measured facts bear on the plan: `ssh-keygen -Y verify` accepts an `allowed_signers` line that has no `namespaces=` option, and the ranch's `status.py --json` lists charters, not boxes, so its output can never be a complete inventory.

The built seam is in this clone: modules.py, module_sandbox.py, vendor_reports.py, module_fetch.py, review_egress.py, static/app.js (renderModuleView, notices), test_modules.py.

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files, do not run tests, do not start or contact a hub, no network, no git push.
- Do not open files outside this clone (no ~/.ssh, ~/.config, ~/.local/share or vendor folders).
- Check at least two of rev 3's claims about the code yourself, and say which.
- Label each claim PROVEN (file:line or a reproduced computation) or SUSPECTED.

WHAT TO ANSWER, in this order
1. One line: ADOPT / AMEND / REJECT for rev 3, with the strongest reason. ADOPT means Phase 1 can be built from this text as written.
2. ROUND TWO: for each "Converged" and "Two of three" item in synthesis-r2.md, FIXED / PARTLY (what is left) / NOT FIXED. Be brief.
3. NEW DEFECTS rev 3 introduced, especially in:
   - the box-as-unit model (§2.2, §2.3): what "names it in `box:`" means when `box:` is absent and defaults to `name` (does a default count as naming? does a renamed charter then cover the old box or not?); a box seen under two names; a box `unknown` that was never seen listed; a box in scope only through the ledger;
   - the ledger and tombstones (§2.4): tombstones keyed by delegate NAME with `issued` ordering, when ranch charters have `created` but no `issued`, and when two charters share a name across sources; the 30-day window counted from `not-listed` when the ranch inbox can never say `not-listed`; `ack-ledger`;
   - required keys (§4.1): what a charter with `issued` later than `expires`, or `issued` in the future, does; a misspelled `box` silently defaulting to `name`;
   - `boxes.toml` (§4.2): deleting a line lowers an alarm; `aliases` joining boxes across sources;
   - inbox grants (§4.3): `signature: verified` in the schema versus `signature: failed` in §12.1; what else can `signature` be; whether an inbox grant can cover a box; the ranch converter never claiming complete, so a terminated ranch box leaves the board only when the operator edits `boxes.toml` by hand;
   - the probe (§4.5): `allow-probe` pins and what happens when a box's address changes; `cert-unsupported`; `core_api <= CORE_API`;
   - Q7 (a stale-alarm card in the core).
4. PHASE 0: does rev 3 account for the two measured facts above? What else in docs/delegates-phase0.md or the fixture should change the plan?
5. Q2 (separate core-mounted inbox) and Q7 (stale-alarm card): one line each, with your recommendation.
6. The three most important missing or weak tests in §12.
7. ONE SENTENCE: the single change that most improves rev 3, or "none: build it" if you said ADOPT.

FORMAT: Markdown, under 1,500 words, no preamble. Do not restate the plan.
