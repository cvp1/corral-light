1. **REJECT**. The plan relies on the sandboxed module reading `~/.ssh`—which the seam explicitly blocks—and establishes a state machine where an operator or buggy script deleting a file actively downgrades the most critical alarm. 

2. **NO CORE CHANGE (§3, §4.1-4.3, §7)**
- **PROVEN FALSE**: "setup finds `~/.ssh/allowed_signers`" without a core change. The existing seam strictly isolates `HOME`: `modules.py` (`build_run` around line 118) sets `HOME` inside the sandbox to the module's empty `data_dir`, and `module_sandbox.py` (`_probe` line 92) verifies that real `HOME` is inaccessible. Furthermore, `modules.py` (`resolve_reads` line 224) only maps specific system paths (like `claude-projects`); there is no `ssh-pubkeys` or `home-ssh` capability. The `setup` script cannot physically see the operator's public keys.
- **PROVEN FALSE**: `ssh-keygen -Y verify` works cleanly as described. While the binary will execute, it demands an `allowed_signers_file`. Because `~/.ssh` is isolated, the operator would have to manually duplicate their signers file into the module's `cfg/` directory.
- The claim that v1 requires no core change fails. A core permissions capability must be added in `modules.py` to allow read-only access to SSH public keys and signers.

3. **THE MODEL (§2, §4.1)**
- **PROVEN FALSE**: "deletion cannot hide a box... OVERDUE-REVOKE blocked by ORPHAN." If a charter hits `OVERDUE-REVOKE`, the board sounds the level `bad` alarm. But if an operator simply deletes the expired charter, the state table drops the grant and processes the leftover inventory box as an `ORPHAN`. `ORPHAN` is only a `warn`. The act of deletion successfully downgrades and obfuscates the critical alarm.
- **SUSPECTED**: The charter model lacks timezone enforcement reliability. `expires` parsed without a timezone falls back to `BAD-CHARTER` (good), but evaluating "past `expires`" requires the board's clock and the inbox's reporting clock to be perfectly synced.
- **SUSPECTED**: A stale inbox triggers ghost alarms. If the inbox generator cron dies, its `inbox/*.json` continues asserting `boxes: present` indefinitely. When a local charter naturally expires, the board will throw an `OVERDUE-REVOKE` alarm for a box that the orchestrator may have cleanly destroyed days ago. The model must degrade stale inventory to `unknown`, not `bad`.

4. **THE INBOX (§4.3)**
- **PROVEN FALSE**: The shadowing rule survives a buggy/hostile inbox. The plan says a local charter beats an inbox charter. However, local charters contain *no inventory state*—they only name the box. The inbox dictates if a box is `present`. A buggy or compromised inbox can simply drop the box from its `boxes` array. By severing the box presence, the locally shadowed charter transitions peacefully from `OVERDUE-REVOKE` to an `EXPIRED` or `REVOKED` state (level `unknown`), entirely silencing the alarm despite the box still running in reality.
- **Answer to Q2**: Separate `<state>/module-inbox/<name>/` is mandatory. Dropping third-party tooling output into the human-managed operator `config` folder breaks the trust model and invites tooling bugs to truncate or corrupt `boxes.toml` and local charters.

5. **PHASE 2 (§4.5)**
- **PROVEN FALSE**: The core-run `ssh-keyscan` probe is a robust reachability check. It compares standard output keys to `known_hosts` directly. First, `known_hosts` lines are frequently hashed (`HashKnownHosts yes`). The core cannot reverse-lookup a host's hash to compare the key without replicating the SSH HMAC protocol. Second, it completely bypasses `~/.ssh/config` `HostName` aliases, meaning `<host>` in the inventory might scan a different IP than what `known_hosts` expects, causing false-positive key mismatches.

6. **LANES AND FLEET (§8, §10)**
- **PROVEN**: The v1 model strictly couples a delegate 1:1 with a hostname (`box: cal-1`). This blocks modern container fleets. If multiple delegated agents run inside a single Docker host or Kubernetes node, the reachability and box accounting model collapses.
- Lanes must definitively be a core type. The module seam fundamentally enforces a read-only architecture (e.g., `modules.py` `resolve_reads`). A "write capability" lane directly violates the module boundary. Core should broker `delegate:` lanes exactly as it brokers `host:` lanes.

7. **TESTS (§12)**
- **Most important missing tests:**
  1. **Sandbox Path Visibility:** An E2E test verifying `setup` can actually resolve and read `~/.ssh/allowed_signers` in the sandbox (this will fail as currently implemented).
  2. **Hashed Known Hosts:** A Phase 2 test of `ssh-reach` attempting to match a returned key against a `known_hosts` file with `HashKnownHosts` enabled.
  3. **Inbox Staleness Dropout:** A test verifying the behavior of `OVERDUE-REVOKE` when the inbox payload's `generated_at` exceeds a 24-hour freshness threshold.
- **Flawed test:** "Deletion cannot hide a box: remove a charter ... -> ORPHAN". This test explicitly asserts that deleting a charter converts the row to `ORPHAN`—proving that deletion *successfully hides* the `OVERDUE-REVOKE` state by downgrading it to a mere warning.

8. **OPEN QUESTIONS (§9)**
- **Q1 (report name):** Add `core_reports` to the seam capabilities; shoehorning SSH reachability under `vendor_reports` corrupts the FinOps abstraction.
- **Q2 (inbox location):** Use a separate mounted `<state>/module-inbox/<name>/` (see section 4).
- **Q4 (gpg):** Defer. Do not bind GPG keyrings into the sandbox. SSH signatures already fulfill the signature need without expanding the sandbox attack surface.
- **Q5 (ORPHAN):** Retain `warn` for true orphans, but introduce a tombstone mechanic so previously chartered boxes remain `overdue` rather than instantly becoming benign orphans upon charter deletion.

9. **ONE SENTENCE:** The module seam must be amended to surface an explicitly read-only `ssh-pubkeys` resource to the sandbox, and the core state model must introduce "tombstoning" to ensure deleting an expired charter leaves a running box in `OVERDUE-REVOKE` rather than downgrading it to `ORPHAN`.