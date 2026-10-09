I'll read the rev 2 plan in full, then the round-one synthesis and the seam it claims to build on.The plan and the round-one synthesis are in. Next I’ll check the seam claims rev 2 cites, and the phase 0 notes those claims rest on.The severity table and the ledger retention rule disagree with each other and with the seam, so two code claims those sections rely on are checked below before the verdict.**AMEND — a grant that is not valid becomes warn or info without a fresh source showing the box gone, and a renamed or reused box stays bad for as long as the new grant keeps it listed.**

Code checked: `MAX_ROWS = 200` is `modules.py:1004` (the plan cites 1003). The CLI uses `module_sandbox.build_argv` with config writable (`modules.py:887-891`); that profile sets `HOME` to the data dir and does not mount `~/.ssh` (`module_sandbox.py:23`, `:154`; the probe asserts it at `:231-239`).

## Round one

Converged

1. Deletion stays bad until a fresh source drops the box. **PARTLY.** `ended:removed` with `listed` or `listed-stale` is bad. §12.1 also says a failed source and a stale source stay bad until a fresh complete source drops the box; §2.2 maps every non-valid grant with `unknown` to warn. A failed collector drops the rail notice at `fresh_at + 2×every_s` (`modules.py:1314`) and failure does not refresh `fresh_at` (`modules.py:1236`).
2. `setup` cannot see `~/.ssh`. **FIXED.** The operator pastes a public key. The CLI is the same bwrap profile as the collector.
3. Phase 2 sandbox. **PARTLY.** Own profile, explicit host and port, `ssh_config` unused. The check never compares key material, a mismatch has no level, and the endpoints are the module's writable config.
4. Lanes are core. **FIXED** in §8: `delegate:` is a core lane, a snapshot never authorizes a send, and a grant that is not `valid` refuses open and send.
5. No gpg in v1. **FIXED.** Phase 0: detached OpenSSH signatures.
6. Stale or absent data is not evidence. **PARTLY.** A stale listing stays out of `ok` and still alarms when the grant is not valid. A `complete: yes` flag makes omission evidence, and a failed source is `unknown`.

Two of three

- Recompute inbox rows here. **PARTLY.** `reports_state` and `verified_by` are labels; `expires` is read on this clock. A local charter shadows the inbox grant, and a ranch revocation is not a charter field.
- Three axes. **PARTLY.** The axes exist. The severity table, the level name `unknown`, the lack of a worst-box rollup, and `ended:removed` defined from the box axis are below.
- Stable ids. **PARTLY.** Delegate id and `<source>/<box>` exist, and sharing is allowed. There is no grant id separate from the delegate, so a rename leaves the old ledger row owning the box.
- 200-row cap. **FIXED.** Blocks of 200, alarm rows first, cells plain text (`modules.py:1074-1078`, `static/app.js:3986`).
- `"notices": true`. **FIXED.** Otherwise the field is dropped (`modules.py:1185-1186`, `:1304`). Dismissal is module, id, level, and title (`static/app.js:3249-3250`). The rail cap of 3 is `modules.py:1102`.
- Signature flags and cache. **FIXED** as a rule: `-I`, `-n`, `-s`, one byte buffer, cache key includes `allowed_signers`. Phase 0(b) has still not run `ssh-keygen` inside the sandbox.
- Charter grammar. **PARTLY.** Comments, CRLF, duplicates, and the filename regex are specified. The near-miss rule and the approval markers are below.
- `core_reports` at `core_api` 2. **PARTLY.** The key is named. This core accepts only `core_api == 1` (`modules.py:55`, `:295-297`) and rejects unknown manifest keys (`:69-71`, `:290-292`). A manifest that says `2` does not load, and setting `CORE_API = 2` rejects every current module.

## New defects

**Severity (§2.2).** PROVEN §12.1 says those sequences stay `bad` until a fresh complete source drops the box, while non-valid × `unknown` is warn. §2.1 defines `unknown` as missing, failed, stale, or partial. A later successful run then publishes warn. The alarm tile counts `bad` only (§5), and the alarm notice stops because its condition cleared. That is the deletion bug under a new label.

PROVEN `ended:*` × `not-listed` is the level `unknown (done)`. Snapshot levels are `ok|info|warn|bad` (`modules.py:1002`). Any other tile level is stored as `info` (`modules.py:1054`, `static/app.js:3874-3876`). A false `not-listed` renders in the same quiet class as "not granted; no box".

PROVEN grant values are not exclusive and have no precedence. Expired and `bad-sig` can both be true. On a `not-listed` box those cells differ (done/info versus warn).

SUSPECTED one delegate row cannot show several ledger boxes. The table is one pair and the view has one Box column. Nothing says the row uses the worst associated box, so the charter's current `box:` can be the cell that is drawn.

`valid` × `listed-stale` = warn is the right cell. `draft`, `unsigned`, and `none` × `not-listed` = info is fine.

**Ledger (§2.3).** PROVEN an entry lasts until every associated box has been `not-listed` for 30 days, and several delegates may share one box. A new valid grant keeps that box `listed`, so the old delegate stays `ended:removed` × `listed` = `bad` for the life of the box and the 30-day clock never starts. A rename is defined as retire-and-create, which is this case. The room has no action button (§1). Deleting `ledger.json` is the remaining way out, and that also forgets revocations.

PROVEN `ended:removed` means "no charter, and the ledger box is still listed", so that value does not exist in the `ended:*` × `not-listed` cell. `none` means there is also no ledger entry. The retention window has no grant value.

PROVEN "newer than the revocation" names no field. `created` is optional. A re-signed charter can stay `ended:revoked`, and an implementation that uses file mtime will treat a restored old file as newer. §12.1 restores only the unmodified file.

PROVEN a stale source that still contains the box forces `listed-stale` even after a fresh complete source omits it (§2.1). The clock also waits until that source is removed from `config.toml`.

`boxes.toml` is "always complete for the boxes it lists" (§4.2). §4.3 uses `complete` to mean absence is evidence. SUSPECTED which reading gets implemented. One never reaches `not-listed` on a manual install. The other treats deleting a line as the box being gone.

**Edit distance.** PROVEN by Levenshtein against `name`, `status`, `approval`, `expires`, and `box`: `note`, `date`, `lane`, and `base` are distance 2 from `name`; `job`, `log`, `os`, and `boxes` are distance 2 from `box`. Each is `bad-charter`, and with a listed box that is a standing `bad` row. Ranch keys from Phase 0 (`mission`, `payload`, `ssh-key-file`, `credential-1-vault-file`) are farther than 2. `expiration` is distance 3 from `expires` and would be kept as "other"; a missing `expires` is already `bad-charter`, so the fence is doing its work on `status`, `approval`, `box`, and `name`.

PROVEN the enum has no value for "neither `status` nor `approval`". SUSPECTED a signed, in-date charter whose `status` is a distance-3 typo is then implemented as `valid`. The plan also never says whether distance runs before `_` is folded to `-`, and a line that fails `[a-z][a-z0-9-]*` (a capitalized `Status:`) is not defined as `bad-charter`.

PROVEN Phase 0 treats approval values `>` and `|` as not approved (`docs/delegates-phase0.md:49-50`). Rev 2's markers are `""`, `unsigned`, `pending`, and `proposed`, and any other non-empty approval means approved (§4.1). Those ranch charters become approved.

**Inbox and `complete` (§4.3).** PROVEN `complete` is the operator's flag. The plan never says an over-cap file (2,000 rows, 1 MiB, 16 files) loses `complete` or becomes `partial`. A truncated complete file makes every omitted ledger box `not-listed`.

PROVEN Q5 says a tier change does not clear a ledger box, and `not-listed` means no complete source lists it. The converter sets `complete` from `gcp_visible` and is not required to emit other tiers. A tier change and a termination are the same omission, so the §12.1 tier test has no schema to run on.

PROVEN a local charter beats an inbox grant with the same name, and two sources are also supposed to keep the more restrictive expiry or revocation. Phase 0 and §1: revocation is an audit event, not a charter field (`delegates-phase0.md:72-74`). A folder of ranch charters, which is how local verification works, has nothing to revoke, and the shadow rule drops inbox `revoked_at`. The grant stays `valid`.

The last good copy kept and marked `failed` (§4.3) feeds the warn cell: the boxes are still in the copy, and the axis is `unknown`.

**Host-key probe (§4.5).** PROVEN the check is `ssh-keyscan`, then `ssh-keygen -F <alias or host> -f known_hosts`. `-F` searches for a name. The scanned key is not an argument, so "matches" and "mismatch" are one procedure. `@cert-authority` and `@revoked` are honoured with no step that reads the marker.

PROVEN the board remaps two outcomes: a matching key keeps the ok label, and "no answer for 3 checks" is warn. Mismatch, revoked, unknown, and resolve-failed leave a granted listed box at `ok`. "The probe never clears an alarm" still allows a key change to read as fine.

PROVEN targets are `boxes.toml`, which the module CLI mounts writable (`modules.py:887-891`). Phase 2 has the core open those address and port pairs, private addresses included, from the module's config. The collector cannot write that directory; a run of the CLI can. Caps are 32 targets, 4 at a time, `-T 5`, and 30 s for the run. Eight waves of 5 s is 40 s, so §12.3's "within 30 s" describes a run these caps forbid. Targets killed at the deadline have no outcome; storing them as `no-answer` warns on boxes that were only late.

## Q2

Keep the separate core-mounted inbox, and bind it only into the collector. The collector sees config read-only; the CLI gets that directory as `writable_extra` (`modules.py:886-896`). An inbox inside config is a directory the module CLI can rewrite, which is the writer this split removes. `fetched` is appended to the read-only list before the interactive branch (`modules.py:882-891`), so the CLI sees it too. "Collector only, the same way as `fetched`" does not match that code, and it fails the §12.2 requirement that the CLI cannot see the inbox. Omit the inbox from the interactive argv. The directory stays writable by the operator on the host. The boundary that changes is the sandbox around the public module. A config-folder inbox leaves inventory on the writable mount `setup` already has.

## Q6

Fixed at 30 days, with no setting: zero is a switch that lets deleting a charter quiet the alarm.

## Tests

1. **Reuse, rename, and a real successor.** A second delegate with a valid grant on the same box, and a renamed charter, must leave the old id able to drop that box while the box stays listed. A newly signed charter the plan calls newer must become `valid`; the old file restored afterward must stay `ended:revoked`. §12.1 restores only the unmodified file.
2. **Quiet alarms and false bad-charters.** The only source fails, its last good copy still lists the box, and the row stays `bad`. A local approved charter plus inbox `revoked_at` stays revoked. `approval: >` and `approval: |` are `draft`. `note`, `date`, `job`, and `os` are accepted, and a charter with neither `status` nor `approval` is not `valid`. A `complete` file that drops `tier` or is cut at 2,000 rows does not produce `not-listed`.
3. **The probe compares keys.** A listener that answers with a different key, for a name `ssh-keygen -F` finds, must be `answers-key-mismatch`, and the row must leave `ok`. `@revoked` and `@cert-authority` need their own fixtures. Targets still running when the 30 s cap hits must not be stored as `no-answer`.

## The change

Keep every grant that is not `valid` at `bad` until a fresh complete source reports that box `not-listed`, and when a newer valid grant covers the box, close the old ledger row.