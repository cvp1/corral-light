I'll read the Delegates plan in full, then the seam contract it depends on, and check the "no core change" claims against the code.The Delegates plan is in. Next I’ll read the FinOps seam contract and the code the “no core change” claim depends on.I have the FinOps trust model. Next I’ll pull the snapshot/notices contract and the seam implementation.Notices look specified but possibly unbuilt — I’ll verify that against `modules.py`, the sandbox, and the renderer.Notices appear implemented despite the FinOps header. I’ll read spawn, sandbox, and snapshot validation next.Checked two load-bearing claims against the seam: notices are already in core, and v1 still trips on snapshot bounds, config paths, and inbox trust. Verdict follows.

**AMEND** — the board-as-module shape is right; OVERDUE-REVOKE is not a reliable alarm under inbox-in-config, trusted inbox verdicts, unsigned-by-default grants, and silent 200-row truncation.

## 2. No core change

**Mostly true for a local-files board; false as written for the v1 the plan describes.**

Checked against the code:

- **Notices exist (claim holds).** FinOps §4.7’s header still says Phase 3 is unbuilt; the seam has it. `MANIFEST_KEYS` includes `notices` (`modules.py:71`), `validate_snapshot(..., notices=)` (`1150`), `notices()` caps 3/module and 8 total (`1102`, `1287-1335`), hub puts them on the full `/api/state` (`hub.py:680-683`), rail renders them (`static/app.js:3204-3278`). v1 rail cards need `"notices": true` in `module.json`. §6’s manifest snippet omits it; without it the field is dropped (`modules.py:1185-1186`, `1304-1305`). **PROVEN.**
- **Collector view matches §3, with one path lie.** Collector binds the whole config dir read-only, data dir writable, `/usr` read-only, feed only if `light-feed` is in `reads`, no net, `HOME` = data dir (`modules.py:853-897`, `module_sandbox.py:10-23,130-159`). `CORRAL_MODULE_CONFIG` is the **file** `config.toml`, not the folder (`modules.py:864`). Charters and inbox are siblings; the module must `dirname` that env var. **PROVEN.**
- **CLI vs collector write split is real.** Interactive CLI gets `writable_extra=[config dir]`; collector does not (`modules.py:887-896`). `cli.py new` can write templates; `sign` cannot see a private key. **PROVEN.**
- **`setup` cannot find `~/.ssh/allowed_signers`.** Sandbox `HOME` is the data dir; `~/.ssh` is absent (`module_sandbox.py:23,154`; probe at `215-239`). §6 contradicts the seam it cites. Operator pastes a principal + pubkey into `<config>/allowed_signers`. **PROVEN.**
- **`ssh-keygen -Y verify` is visible, underspecified.** `/usr` is bound; `PATH` includes `/usr/bin` (`module_sandbox.py:40,133`). **SUSPECTED:** it runs without `$HOME/.ssh` if invoked with `-f allowed_signers -I <principal> -n corral-light-delegate-charter -s <sig>` and the message on stdin. The plan never names `-I`. OpenSSH requires it; without a recorded principal, every charter is `BAD-SIG` or the wrapper guesses. Phase 0 (b) is still required.
- **v1 snapshot as specified does not fit the contract.** Tables cap at **200 rows**, 12 columns (`modules.py:1004`; renderer `static/app.js:3966-3996`). §4.3 and §12.1 promise 500. Excess rows are truncated, not rejected. Table cells have no `level`; only tiles do (`app.js:3934` vs `3983-3987`). `OVERDUE-REVOKE` in the State column is uncolored text. Notes also ignore `level` (`app.js:3998-4002`). **PROVEN.**
- **`modules/index.json` is a core edit.** Intended M3 hook; say “index entry only,” not “none.”
- Seam does **not** provide: a config-dir-vs-inbox split, per-row table levels, `core_reports`, port-22 egress, extra `reads` tokens, or cloud-inventory hosts (see §5–6).

Empty `reads` and `budget_s: 10` are valid (`modules.py:313-319,355-360`).

## 3. The model

The table is the right join (charter × box) and `OVERDUE-REVOKE` is the right alarm. It is incomplete and can read as fine.

**Missing / mislabelled**

- `BAD-CHARTER` is defined in prose, absent from the §2 table. Put it in the table, level `bad`.
- `DRAFTED`/`UNSIGNED` + box present stay `warn`. That is a running machine with no grant. Treat box-present as the alarm axis: grant ended **or never took effect**, box still listed.
- `LIVE` is “charter valid + inventory row,” as §3 admits. The State column still says `LIVE`. Until Phase 2, the honesty line has to sit on the **tile**, not only in Notes.
- Default `require_signature = "no"` plus an editable `expires` lets anyone who can write config extend a grant and keep `LIVE`. `BAD-SIG` then deleting `.sig` becomes `LIVE`. **SUSPECTED** from §2×§4.1; default `yes` once `allowed_signers` exists.
- Inbox `state` is displayed as the source’s verdict. Expiry is not re-evaluated. A stale or buggy converter can emit `LIVE` forever. **Recompute** from `expires`/`status` (and `verified_by` as a label only).

**OVERDUE-REVOKE reliability**

| Attack / fault | Result |
|---|---|
| Delete local charter, box still listed | `ORPHAN` (good) unless Q5 hides non-`tier: delegate` boxes; inbox schema has no `tier` |
| Edit local `expires` forward, unsigned | `LIVE` (hides alarm) |
| Inbox omits the box | row gone or `EXPIRED`/`REVOKED` (hides alarm) |
| Inbox lists box under a different `name` than charter `box` | `READY` + `ORPHAN` (join miss) |
| Two inbox files, same name, different states | undefined |
| Host clock wrong | every TTL wrong; no second clock |
| `expires` with offset vs `Z` | OK if the module parses aware datetimes; **SUSPECTED** until tests pin `+00:00` vs `Z` vs naive |
| Stale inbox (default 2 h) | still drives `LIVE`/`OVERDUE-REVOKE`; 2 h ≠ notice freshness (10 min) |
| Snapshot 300 s cadence | grant can be dead for one full period before the row changes |

Charter format is sound: flat keys, no YAML, unknown keys kept, fixed namespace, signature over raw bytes (verify, then parse). Specify comment syntax, quoting, `CRLF` vs `LF`, and name charset (`[a-z0-9-]` matching the filename). Refuse `..` and `/` in `name`.

## 4. The inbox

“No new privilege” is true only as “same uid as config.” That is the grant-writing directory.

Who can write: the operator; any same-uid process (cron, a wall pane with home); `cli.py` (config is writable when interactive). Collector cannot. Mode 0700 on mkdir (`modules.py:855-856`) is umask-masked and not re-chmodmed.

A hostile/buggy inbox file can: invent `LIVE` boxes; stamp `verified_by: gpg` on fiction; omit boxes and hide overdue; duplicate names; flood 16×1 MiB (collector-enforced, good). XSS in names dies at `textContent` (**PROVEN** M2). Shadowing by **charter name** holds for one local vs one inbox charter; it fails for local `name` ≠ inbox `name` with the same `box`, and for two inbox files. Local charter does not stop an inbox **box** from keeping a revoked grant in `OVERDUE-REVOKE` (wanted) or from attaching a box to the wrong charter (not wanted).

**Q2:** Put the inbox at `<state>/module-inbox/<name>/`, mounted read-only, never next to charters. That is a small, justified core bind (new `reads` token or an automatic per-module mount). Config stays operator text. A converter that can write inbox then cannot rewrite `expires` or `allowed_signers`. Do this in v1; it is cheaper than a false `LIVE`.

## 5. Phase 2

The idea is right: core-run, no login, no private key, compare host keys. The sandbox copy-from-`grok-usage` is wrong.

- Collector/vendor-report profile is `--unshare-all` with no net share (`module_sandbox.py:130-132`). **PROVEN.**
- Fetcher egress is `CONNECT` to port **443** only (`review_egress.py:26`). **PROVEN.**
- Reports live under the feed (`hub.py` `vendor/grok-usage`). Delegates `reads: []` does not bind the feed (`modules.py:859-867`). Adding `light-feed` to see `ssh-reach` also exposes panes, quota, logins.

Need a **new** profile: resolve DNS in the core, connect to that IP:22 only, bind `ssh-keyscan` the way `grok-usage` binds a binary, write to a narrow `core_reports` path, cap hosts and wall time. Do not share net.

Host-key comparison will misfire without a spec: hashed `known_hosts` (`HashKnownHosts`) needs `ssh-keygen -F`, not a string grep; `Host` vs `HostName` vs `ssh-hosts.json` `ip` aliases; only `~/.ssh/known_hosts` vs `known_hosts2` / `UserKnownHostsFile`; `-t ed25519` vs rsa-only or `sk-ssh-ed25519`; `@cert-authority` lines; IPv4/IPv6. “Unreachable for 3 checks” needs data-dir memory and must not keep the State cell as unqualified `LIVE`.

Keep Phase 2 out of v1. Specify it as its own sandbox, not a `vendor_reports.py` delta.

## 6. Lanes and Fleet

v1 does not block a later `delegate:` **if** every row keeps a stable box id, connect target, grant fields (`data_classes`, `expires`, `status`), and inventory `kind`. Name-only join and inbox-only rows with no `host`/`user` will. Do not reuse `host:` (`sessions.py:314-378`: shell, `tools: False`, typed commands, max 8, `ssh-hosts.json`).

Lanes are a **core** lane type. A module cannot send the operator’s words or files; that is a write capability, and design-6’s “needing more is core” still applies. Feed the lane list from the module’s inventory the way `host:` is fed from a file, with `OVERDUE-REVOKE` (and unsigned-with-box) blocking open.

Fleet: publish the box JSON schema; do not make Fleet import this module. Signing stays out.

## 7. Tests

**Missing**

1. Inbox `state: LIVE` with `expires` in the past → board is `EXPIRED`/`OVERDUE-REVOKE`, not `LIVE`; same for a second inbox file that disagrees.
2. Alarm rows survive truncation: 201 delegates, the 201st is `OVERDUE-REVOKE` → tile count 1 and that row is visible (sort `bad` first).
3. `ssh-keygen -Y verify` inside the real collector argv: missing `-I`, wrong namespace, `allowed_signers` without `namespaces=`, `HOME` has no `.ssh`.

**Listed tests that pass while the property is broken**

- `require_signature = no` with no signature → `LIVE` passes with **no box**; the real state is `READY`.
- “Snapshot with 500 delegates stays within every core bound” / “validator accepts it”: validator **truncates** at 200 (`modules.py:1074,1081-1082`). Accept ≠ shown.
- Converter “rows equal to full Corral” passes by copying inbox `state` through, never exercising the local machine (`UNAPPROVED` vs `UNSIGNED`, no `ORPHAN`).
- “Deletion cannot hide a box” on `boxes.toml` only: inbox that omits the box still hides it.

## 8. Open questions

1. **Add sibling `core_reports`**, `core_api` 2. Leave `vendor_reports: ["grok-usage"]` for FinOps. Do not name ssh-keyscan a vendor.
2. **Separate inbox dir**, core-mounted RO. See §4.
3. Settled: print the sign command.
4. **No gpg in v1.** Inbox `verified_by` is enough for ranch. A keyring bind is a new secret-adjacent surface.
5. **`ORPHAN` only for `tier: delegate`.** Add `tier` to `boxes.toml` and the inbox schema; converter sets it. Other inventory is Fleet’s.

## 9. One sentence

Move the inbox out of the grant directory, recompute every inbox row’s state from `expires`/`status` on this host’s clock, default to signed charters, and emit alarming rows first inside the existing 200-row table cap.