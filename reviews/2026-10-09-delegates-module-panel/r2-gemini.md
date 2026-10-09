CODE CLAIMS CHECKED
- **PROVEN:** `tables cap at 200 rows` (`modules.py:1004` specifies `MAX_ROWS = 200`).
- **PROVEN:** `timeout_s` is the enforced deadline, not `budget_s` (`modules.py:1224-1225` pipes `timeout_s` directly into `run_capped` for termination, referencing `budget_s` only as advisory payload).
- **PROVEN:** `notices need "notices": true` (`modules.py:310` reads from the parsed boolean explicitly).
- **PROVEN:** `notice dismissal is keyed on module, id, level and title` (`static/app.js:3245` builds the cache token using `[n.module, n.id, n.level, n.title]`).

1. AMEND. Rev 2 solves round-one objections but introduces structural wedges into the state machine, causing legitimate renames to alarm indefinitely and allowing users to bypass alarms simply by un-listing boxes.

2. ROUND ONE
*Converged*
- Deleting an expired charter downgrades the alarm: FIXED (ledger remembers box relation).
- `setup` cannot see `~/.ssh`: FIXED (operator pastes keys manually).
- Phase 2 sandbox does not exist: FIXED (`hostkey-probe` tightens connections drastically).
- Lanes are core: FIXED (relocated to its own spec/plan).
- Q4: no gpg in v1: FIXED (Phase 0 measured OpenSSH mechanics).
- Stale or absent data is not evidence: FIXED.
*Two of three*
- Recompute inbox row: FIXED (derived using local module clock).
- Replace single state with separate axes: FIXED.
- Stable identifiers: FIXED.
- 200-row table cap: FIXED.
- `"notices": true`: FIXED.
- Signature checking explicit flags: FIXED.
- Charter grammar: PARTLY (rigid behavior acts as intended, but distance-2 typo limits mathematically break valid short terms).
- `core_reports` API 2 key: FIXED.

3. NEW DEFECTS
- **Three-axis model (§2.2) bypass:** You can trivially bypass a `bad` alarm for an ungranted box by deleting the box from `boxes.toml` (or purposefully failing the fetcher). Because the box is no longer formally covered, it drops to `unknown`. Evaluating `not valid` + `unknown` resolves to `warn`, quietly stripping the critical alarm by simply hiding the evidence.
- **The ledger (§2.3) reassignment wedge:** Reassigning a box to a new delegate creates a new ledger tracking. The old delegate charter is deleted (`ended:removed`), but because the box is actively `listed` by the new valid delegate elsewhere, the old delegate's record will loudly scream `bad` indefinitely until manually wiped from disk.
- **Charter grammar typo-squatting:** A Levenshtein distance of 2 on simple 3- or 4-letter keys is physically inescapable. Valid front-matter concepts like `bot` (1 edit from `box`), `log` (2 edits), or `note` (2 edits from `name`) will forcefully disable entirely valid charters with a false `bad-charter` rule trip. 
- **Declared-source inbox (`boxes.toml`) wedge (§4.3):** Using a source that is "always `complete` for the boxes it lists" creates a logical paradox—it is incapable of emitting a `not-listed` update. Manually tracked boxes can therefore never accrue the 30 subsequent days of `not-listed` required to leave the ledger, wedging them permanently into the view.
- **Phase 2 hostkey-probe (§4.5) `@cert-authority` failure:** Probing against `ssh-keygen -F` exclusively searches literal hostnames/IPs. It has a fundamental blindspot for `@cert-authority` wildcards inside `known_hosts`, guaranteeing a false `answers-key-unknown` status on any properly signed remote boxes.

4. Q2: INBOX FOLDER
Adopt the separate, core-mounted read-only `<state>/module-inbox/` location. Though running beneath identically scoped user privileges, formally splitting human-authored policies (`config/charters/`) from automated telemetry (`module-inbox/`) maintains a distinct defense boundary. It ensures a buggy, hijacked, or reckless external JSON converter script cannot quietly mutate valid `expires` limits or clobber valid `allowed_signers`, while fully sheltering the inbox from the writable configuration bind of the CLI tool.

5. Q6: LEDGER RETENTION
It must be deeply configurable—specifically permitting retention periods to go down to zero—since declarative files (like `boxes.toml`) possess no geometric mechanism to report `not-listed` box departures, eternally breaking the ledger exit-countdown.

6. MISSING OR WEAK TESTS
1. **Missing-Box Downgrade Bypass:** A routine verifying an `ended:removed` charter maintaining a `listed` state (`bad` alarm) does not incorrectly downgrade to merely `warn` when the manual inventory list is deleted.
2. **Reassignment Wedge Clearance:** A routine ensuring that adopting a literal `box_id` via a completely *new* signed delegator instantly clears the existing alarm, overcoming the old ledger’s static memory. 
3. **Lexical Edit-Distance Safety:** A routine checking that standardized arbitrary 3-letter strings (`log`, `bot`) are safely cataloged inside `other` and pass cleanly over the heavy `bad-charter` typo-hunter.

7. SINGLE CHANGE
Refactor the ledger paradigm to pivot state strictly onto the global `box_id` rather than retaining a per-delegate memory, instantly absolving "orphan" traces the second any authorized charter legally encompasses the active box.