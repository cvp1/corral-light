### VERIFIED CODE CLAIMS
- **PROVEN (`modules.py:295-297`):** Rev 3's claim regarding `core_api <= CORE_API` correctly flags that the core currently safely enforces strict equality (`if obj.get("core_api") != CORE_API: raise ModuleError...`) and would thus reject `core_api: 2`.
- **PROVEN (`modules.py:882-892`):** Rev 3 claims `fetched` is added to the read-only list before the interactive branch. The logic appends `fetched` to `ro` (line 887) and passes it directly to `module_sandbox.build_argv(..., read_only=ro)` (line 892), verifying the CLI explicitly sees it.
- **PROVEN (`modules.py:1350-1351`):** Q7 claims a broken collector drops its own alarm. The codebase validates this, abruptly discarding notices if `now >= fresh + 2 * every`, successfully proving a crashed snapshot loop silences itself.

### 1. VERDICT
**AMEND.** Strongest reason: The inability of the un-complete ranch converter to ever emit `not-listed` means a terminated ranch box permanently wedges the board with its last alarm level and clogs the ledger forever, forcing operators to execute manual deletion hackery via `boxes.toml`.

### 2. ROUND TWO
- **Revocation ordering:** FIXED (`issued` explicitly required as the sorting key).
- **Retention throws away replay:** FIXED (ledger is fixed to 30 days; tombstones live forever).
- **"Complete" undefined:** FIXED (explicitly defined per-file and bounded by config properties).
- **Inbox revocations lost:** FIXED (revocations correctly cross-apply unconditionally).
- **Inbox grants had no approval:** FIXED (`approved` and `signature` schema keys added).
- **Ranch markers `>` and `|`:** FIXED (mapped cleanly to draft status).
- **Phase 2 probe never compares keys:** PARTLY (scanned blobs compare against `known_hosts` `-F`, but rigid checks break dynamically rotated IPs and openly penalize valid SSH certificates).
- **Probe targets from module config:** FIXED (targets strongly deferred to core allowed pins).

### 3. NEW DEFECTS
- **Box-as-unit model (§2.2, §2.3):** If `box:` is selectively omitted, it silently defaults to the file `name`. Spelling `box:` incorrectly (e.g., `bxo: target`) drops coverage for `target` without throwing a schema error, silently masking misconfigurations. Additionally, renaming a charter implicitly renames this default box binding, immediately dragging coverage from the older box without warning. A ledger-only `unknown` box with a prior `bad` history permanently wedges.
- **Ledger and tombstones (§2.4):** Tombstones are globally keyed by delegate `name`. When distinct sources (e.g., ranch vs local) provision an identically named delegate, one's revocation will invalidly poison the other's independent grant. Furthermore, the 30-day countdown triggers exclusively against `not-listed`. Since ranch output isn't designated as complete, ranch records perpetually refuse to begin the countdown and leak continuously.
- **Required keys (§4.1):** Charters lack structural validation guaranteeing `issued` happens chronologically *before* `expires` or resides strictly in the past, injecting logically impossible time-ordering frames into the ledger's tracking mechanics.
- **`boxes.toml` (§4.2):** Expecting operators to manually assemble cross-source `aliases` within `boxes.toml` fractures the automated source hierarchy. Allowing users to inadvertently mute critical alerts merely by deleting lines threatens core audit durability.
- **Inbox grants (§4.3):** The schema explicitly establishes `signature: verified` while §12.1 blindly tests against `signature: failed`, ignoring implicit bounds like `absent`. Consequently, the ranch converter leaves abruptly terminated resources as `unknown`, forcing them to cling safely to their last known alert unconditionally.
- **The probe (§4.5):** Cloud providers dynamically cycle IPs upon redeployment. Target pins mapping strictly back to `<host>[:port]` punish transient IPs, producing continuous `resolve-failed` false alarms upon rotation. Additionally, emitting `cert-unsupported` as `warn` actively attacks internal infrastructures leaning legitimately toward SSH certificate models.

### 4. PHASE 0
Rev 3 intelligently absorbs Phase 0's findings, shifting the `namespaces=` refusal seamlessly inside the module frontend to mitigate the `ssh-keygen` anomaly and locking the `status.py` ranch converter into an explicit `may_claim_complete=no` stance. 
However, the JSON `ranch-status-fixture` forces distinct structural pivots: `box` isn't merely a string—it's a distinct nested payload structure. The literal string `box.manifest_key` must be explicitly stripped by the converter to resolve the true target. `budget_usd` actively reflects as raw telemetry and demands enforced numeric coercion. Crucially, missing instances emit identically as `box: null` rather than dropping the record outright. The converter must deliberately inspect this `null` attribute to independently synthesize proxy `not-listed` bounds, bypassing the underlying incomplete wedge trap entirely.

### 5. Q2 AND Q7
- **Q2 (separate root inbox):** ADOPT. A fully dedicated, isolated mount explicitly enforces read-only boundaries, heavily shielding core config integrity against parsed operation interactions while satisfying module sandbox policies structurally.
- **Q7 (stale-alarm card):** ADOPT. Permitting wholly failed collector instances to conveniently sweep existing footprint warnings under the rug renders severe system degradation indistinguishable from a healthy board. Introduce the stale card safely.

### 6. MISSING TESTS
1. An implicit assignment check where intentionally renaming a charter lacking an explicit `box:` key appropriately triggers a missing coverage alarm against the historically attached resource.
2. A direct converter integration scenario affirming that an intercepted `box: null` payload successfully unravels the wedged tracking matrix, triggering a clean `not-listed` teardown independently.
3. A Phase 2 network disruption simulation confirming dynamically rotating cloud IPs diverging externally from predefined `<host>[:port]` core pins degrade cleanly without needlessly flooding downstream false alarms.

### 7. ONE SENTENCE
The critical pivot that most aggressively improves rev 3 is forcing the ranch converter to independently harvest and parse Phase 0's `box: null` fixtures strictly into explicit `not-listed` assertions, permanently evaporating defunct resources without chaining administrators to unscalable `boxes.toml` file operations.