I’ll read the plan and seam contract, then check the implementation claims against the code. I’ll stay within the clone and use read-only inspection only.The seam supports the basic board, but the plan assumes access the sandbox does not provide: `setup` cannot discover `~/.ssh/allowed_signers`. I also found a 200-row table limit, so the proposed 500-delegate table would be truncated. I’m now checking whether deletion, conflicting sources, and stale data can suppress an overdue warning.AMEND — **PROVEN:** deleting an expired charter downgrades a bad overdue alarm to `ORPHAN`, while the proposed tier filter can hide that box altogether (`docs/delegates-module-plan.md:150`, §9 Q5).

1. **NO CORE CHANGE**

   **PROVEN:** The basic board fits, but v1 does not work exactly as written. The collector receives read-only `/usr`, selected system paths, `/module`, and its config directory; writable data and private `/tmp`; isolated networking; and `HOME` pointing at its data directory. With `reads: []`, **there is no feed mount**, and `CORRAL_MODULE_FEED` is empty (`module_sandbox.py:130–158`; `modules.py:857–896`). Phase 2 must add `light-feed`, not merely `vendor_reports`.

   **PROVEN:** The CLI gets writable config but no operator home. Therefore `setup` cannot discover `~/.ssh/allowed_signers` or an arbitrary chosen public-key file as promised. Have the operator copy public material into config or paste it through stdin; no core change is necessary (`modules.py:886–896`; plan:272).

   **SUSPECTED:** `ssh-keygen -Y verify` should work with the mounted binary/libraries, public files and charter bytes supplied on stdin. Binary availability and compatibility remain Phase 0 measurements, not established facts. Specify `-f`, signer identity `-I`, namespace `-n`, signature `-s`, and bounded execution; distinguish unavailable verification from an invalid signature.

   **PROVEN:** Additional seam mismatches:
   - Tables truncate at **200 rows**; split 500 delegates across blocks. Table cells have no individual severity styling (`modules.py:1069–1084`; `static/app.js:3982–3987`).
   - Notices require manifest `"notices": true`, omitted from the module sketch (`modules.py:309–312`).
   - A failed collector retains the previous snapshot; it does not empty the board. Source failures need an explicit error snapshot or retained observations marked unknown (`modules.py:1207–1238`).
   - `budget_s: 10` is not the enforced deadline: execution uses `timeout_s`, default 45 seconds (`modules.py:357–360,1223`).

2. **THE MODEL**

   **PROVEN:** The table has overlapping conditions without precedence: a drafted or unsigned charter can also be expired with a listed box. Moreover, `UNSIGNED` and `DRAFTED` boxes are excluded from “Needs attention” (`plan:80–91,226–227`). Represent grant validity, inventory observation and source health separately; derive severity so “listed without a currently valid grant” cannot become harmless through another status.

   **PROVEN:** The deletion guarantee preserves, at best, box visibility—not the overdue alarm or historical grant. Editing `box`, changing status to draft, or deleting the charter has no specified historical reconciliation (`plan:129,150–153`). Retain last accepted grant identity, box association and unresolved termination evidence. A local ledger helps against mistakes; it cannot resist an operator who can also erase that ledger.

   **PROVEN:** “LIVE,” “running past,” and “finished cleanly” overstate the evidence. The plan itself says inventory establishes neither uptime nor compliance (`plan:83–87,114–116,243`). Prefer “granted; box listed,” “grant ended; box still listed,” and “grant ended; box not observed.” Absence from a failed, stale or incomplete source is not termination evidence.

   **SUSPECTED:** Stale inbox verdicts can remain green after expiry unless the collector independently compares `expires` with its current clock. Require timezone-aware timestamps normalized to UTC, `now >= expires`, bounded future timestamp tolerance, and explicit clock uncertainty. Detect backward jumps where possible; an offline board cannot establish authoritative time. Neither file mtime nor producer-supplied `generated_at` proves freshness.

   **PROVEN:** The signature cache key omits `allowed_signers` and verification policy (`plan:247–250`). Removing an authorized key would not invalidate an unchanged charter/signature cache entry. Include trust-file digest, principal, namespace and relevant verifier policy; account for time-dependent signer validity.

   **SUSPECTED:** Flat front matter is workable, but the parser needs a defined grammar: duplicate keys, inline comments, delimiters, encoding, name/filename agreement, unknown statuses, numeric bounds and schema version. Preserve unknown descriptive fields, but do not silently treat misspelled security fields as harmless extensions. Verify and parse the same captured bytes. A fixed namespace separates purposes; it does not prevent replay of an older signed approval after revocation.

3. **THE INBOX / Q2**

   **PROVEN:** This adds no collector privilege, but config is not exclusively operator-written: module CLI **and doctor** use the interactive writable-config path (`modules.py:887–891,1570`). The pin verifies module code, not charter/config integrity (`modules.py:727–761`). Same-user writers remain within the trusted boundary.

   **PROVEN:** An inbox producer chooses its verdict, source label and `verified_by`; those are assertions, not authentication (`plan:167–185`). Display “source reports verified,” keep imported assertions distinct from local verification, and recompute expiry-related warnings locally.

   **SUSPECTED:** A buggy or hostile producer can forge reassuring states, future dates and costs, omit boxes, create collisions, or overwhelm limits. Define source identity in operator config, source precedence, duplicate handling, completeness, sequence/replay rules and operator-controlled staleness limits. Use bounded regular-file reads, reject symlinks and special files, and require atomic replacement.

   **PROVEN:** “Local wins” only specifies same-name charter precedence; it does not settle conflicting boxes, multiple inboxes, or malformed local charters (`plan:181–188`). Local failure must not fall back silently to imported green; preserve conflicts and box observations.

   **SUSPECTED — recommendation:** Keep `<config>/inbox` for v1 under an explicit same-user trust model. A separate path improves organization but creates no security boundary by itself. Add separate core mounting only when producer isolation or independent ownership is an actual requirement.

4. **PHASE 2**

   **PROVEN:** The existing report sandbox has no network; existing egress accepts CONNECT only on port 443 and rejects nonpublic addresses. It cannot directly supply the proposed port-22 probe, particularly for home servers (`vendor_reports.py:243–252`; `review_egress.py:26,81–93,182–192`). “Own sandbox” needs an actual transport design.

   **SUSPECTED:** Treat `ssh-keyscan` as key observation and SSH responsiveness, not the claimed proof of private-key possession. Require a verified SSH handshake if authenticated reachability is intended.

   **SUSPECTED:** Specify operator-approved destinations independently of inbox contents, exact resolved addresses, IPv4/IPv6 policy, private-network allowances, DNS changes, global concurrency/deadline limits and hostile hostname rejection. `-T 5` alone is not a fleet-wide work budget. Rename `rtt_ms` to probe duration unless actual RTT is measured.

   **SUSPECTED:** Naive known-hosts comparison mishandles hashed entries, aliases, `HostName`, `HostKeyAlias`, nonstandard ports, multiple keys, revocations and certificates. Explicit endpoint plus trust identity is safer than automatically evaluating arbitrary SSH configuration. Distinguish unknown key, mismatch, revoked key, unsupported algorithm and unreachable; ed25519-only failure is not proof the host is down.

5. **LANES AND FLEET**

   **PROVEN:** Existing `host:` lanes are shell adapters with `tools: False`; removed hosts are tombstoned (`sessions.py:327–377`). The plan correctly distinguishes these from charter-controlled chat lanes.

   **SUSPECTED — recommendation:** Make delegate lanes a core capability with independent destination authorization and send-time grant checks. A module snapshot must never authorize communication.

   **PROVEN:** Name-based joins and mutable `box` references provide no stable machine/grant identity (`plan:76,126–129,157`). Add immutable delegate, grant and provider-scoped machine IDs, with display names separate. That supports renames, replacement machines and multiple delegates per host. Share a versioned observation format with future Fleet; avoid a runtime dependency on the Delegates module.

6. **TESTS**

   **SUSPECTED — three priority additions:**
   - **Alarm continuity:** cross-product transitions involving expiry, draft/unsigned/bad-signature states, deletion, changed box references, stale/failed inventory, source conflicts and tier filtering; unresolved listed machines must not silently disappear.
   - **Trust changes and replay:** remove/change `allowed_signers` without changing charter bytes; replay an older signed approval; race file replacement; require verification of exactly the displayed bytes.
   - **End-to-end degradation:** cold-cache maximum input, clock rollback/future timestamps, collector timeout, and 500 rendered delegates—including the last overdue row—with notice dismissal/reappearance.

   **PROVEN:** “Validator accepts it” can pass while 300 rows disappear: truncation is successful validation and explicitly tested (`test_modules.py:784–794`). Assert preservation of every expected row.

   **PROVEN:** The proposed deletion test can pass while the bad alarm becomes a warning (`plan:150–153`, §12.1). Assert severity and retained history, not merely `ORPHAN`.

   **PROVEN:** A dismissed aggregate notice remains hidden when only its body changes; dismissal identity uses module, id, level and title (`static/app.js:3250–3272`). Test changed membership with unchanged count, and encode an incident generation in its identity.

7. **OPEN QUESTIONS**

   - **Q1 — SUSPECTED recommendation:** Keep `vendor_reports` for compatibility; document its broader meaning. Neither a second synonymous key nor an API-version bump is justified solely by naming.
   - **Q2 — SUSPECTED recommendation:** Config inbox for v1, with the trust and validation restrictions above.
   - **Q3 — PROVEN:** Printing the signing command fits the sandbox (`modules.py:887–896`). Validate names and shell-quote every printed argument.
   - **Q4 — SUSPECTED recommendation:** Defer GPG. Later import public verification material into module config; binding the operator’s existing keyring is unnecessary.
   - **Q5 — PROVEN:** Current schemas omit the proposed `tier` field (`plan:157–173`). Explicitly scope inventory to delegates or add that field now; never hide a previously associated machine merely because its tier disappears.

8. **SUSPECTED — single best change:** Replace the single state with independently tracked grant validity, machine observations and source health, retaining unresolved grant-to-machine history so deletion or stale data cannot turn an overdue condition into apparent safety.