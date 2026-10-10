I'll review rev 3 against the round-two record, the Phase 0 measurements, and the built seam. Starting with the plan, synthesis, and code claims.Reading the plan, round-two synthesis, Phase 0 notes, and the fixture next.Checking the plan’s code citations against the seam, then the round-two reviews.Line numbers in the plan look off. Locating `MAX_ROWS`, notice expiry, and `CORE_API` in the current seam.**AMEND** — coverage and presence are still not implementable: “names it in `box:`” is undefined when `box:` is absent, and a ranch-only box has no path to `not-listed`.

**Code checked.** `CORE_API` equality is `modules.py:56,297–299` (plan cites 55, 295–297). `fetched` is appended to `ro` before the interactive branch (`modules.py:887–895`); CLI `HOME` is the data dir (`module_sandbox.py:23,179`). Notice expiry is `fresh_at` on success only (`:1279`) then drop at `fresh + 2*every_s` or `NOTICE_MAX_AGE_S` (`:1142,1350`); plan’s `:1236,:1314` are stale. `MAX_ROWS=200` is `:1042` (plan `:1004`); `NOTICES_PER_MODULE=3` is `:1140` (plan `:1102`). Dismissal key is `static/app.js:3249–3250` (PROVEN). `inbox` is absent from `MANIFEST_KEYS` (`:70–72`) and unknown keys are refused (`:292–294`) (PROVEN; Phase 1 must add it).

---

## 2. Round two

**Converged**

| Item | Status |
|---|---|
| Reused/renamed box alarms forever | **PARTLY** — box is the unit; default/`box:` and dual names still split identity (below). |
| `unknown` was warn (deletion bug) | **PARTLY** — last listed level is kept; never-listed is warn; ranch-only boxes never reach `not-listed`. |
| Edit-distance rejects ordinary keys | **FIXED** as a rule; misspelled `box` still defaults to `name`. |

**Two of three**

| Item | Status |
|---|---|
| Revocation ordering / `issued` | **PARTLY** — required locally; ranch fills from `created`; tombstones keyed by **name**; future `issued` unspecified. |
| Retention vs tombstones | **PARTLY** — tombstones forever; 30 days starts only on `not-listed`; `ack-ledger` can empty tombstones. |
| `complete` / ranch converter | **PARTLY** — per-file + allow; ranch never complete; no exit for ranch-only boxes. |
| Inbox revocations under shadow | **FIXED** in text. |
| Inbox approval evidence | **PARTLY** — `verified` vs §12.1 `failed`; other values and ranch `sig_ok` map unspecified. |
| Ranch `>` and `\|` | **FIXED**. |
| Probe compares keys | **FIXED** in spec. |
| Probe targets from module config | **FIXED** (`allow-probe` in pins). |

---

## 3. New defects

**Box as unit (§2.2–2.3).** PROVEN §4.1: a missing or misspelled `box` “falls back to the default (`name`)”. §2.3 covers only a grant that “names it in `box:`”. An implementer cannot tell whether the default **is** naming. A rename that changes `name` (and the filename) and omits `box:` then covers the new name and leaves the old box uncovered. A misspelling (`boxes:`, `boxx:`) is kept as “other” and silently binds `name`.

SUSPECTED identity `<source-id>/<box-name>` plus “joined when a grant’s `box:` names it”: one grant string merges every source’s same box-name; two names for one machine stay two boxes. Phase 0 keys live Lightsail rows as `<name>-node` and GCP as `<name>` (`docs/delegates-phase0.md:131`; fixture `manifest_key` `overdue-1-node` vs `gcp-live-1`). A local charter with default `box: cal-1` does not cover ranch `cal-1-node`.

PROVEN `unknown` never-listed → warn. A grant that only names a box, never listed, stays warn.

PROVEN in-scope via ledger only, with ranch `partial`: presence stays `unknown`, last listed level is kept, 30-day clock never starts.

**Ledger / tombstones (§2.4).** PROVEN tombstones are per **delegate name**, grants per SHA-256. Two sources sharing a name share one high-water `issued`. Ranch has `created`, not `issued` (§4.3 fills it); an in-place renew that keeps `created` and equal `issued` stays revoked.

PROVEN 30 days run from `not-listed`. Ranch inbox can never emit that. Unknown boxes never age out.

SUSPECTED `ack-ledger`: either file corrupt → moved aside → after ack, empty tombstones replay old revoked files.

PROVEN §2.1 dropped `ended:removed` (still in the r2 precedence list). Deleted-charter rows in the Grants table have no status; §12.1 still wants “A shown as ended”.

**Required keys (§4.1).** SUSPECTED `issued` after `expires`, and `issued` in the future, parse and can be `valid`. A future `issued` is “greater” than a tombstone and reopens the name. Misspelled `box` is above.

**`boxes.toml` (§4.2).** PROVEN deleting a line is the operator off-switch and lowers `bad`. Intended, and the only complete source in v1.

SUSPECTED `aliases` apply before coverage: one valid grant then covers every aliased identity, including a second listed machine.

**Inbox (§4.3).** PROVEN example `signature: "verified"`; §12.1 tests `failed`; body requires `verified` for `valid`. `absent`, unknown strings, and the ranch map from `sig_ok`/`signed` are unspecified. An inbox grant **can** cover a box when `approved: true` and `signature: verified`.

PROVEN ranch converter never claims complete, so a terminated ranch box that was never a `boxes.toml` line cannot become `not-listed` by deleting a line that does not exist. Add-then-delete is the only ritual, and it is not specified.

**Probe (§4.5).** SUSPECTED `allow-probe` pins `<box> <host>[:port]`. An address change keeps probing the old pair until the operator edits the pin; `key-matches` can attach to a moved box. `cert-unsupported` is warn even when `known_hosts` has `@cert-authority`. Host presenting cert + raw key is unspecified. `core_api <= CORE_API` is the right Phase 2 change (today equality, PROVEN `:297`).

**Q7.** PROVEN a failed collector leaves `fresh_at` still (`:1280–1283`) and the rail drops notices after `2 × every_s` = 10 min here (`:1350`). Dialog still has the last snapshot.

---

## 4. Phase 0

Both measured facts are in the text (`§4.1` `namespaces=`; `§4.3` ranch never complete). The second fact’s consequence (no `not-listed` from ranch, no ledger expiry) is not.

Also from `docs/delegates-phase0.md` / the fixture:

- Inbox `generated_at` is `%Y-%m-%dT%H:%M:%S%z` (`-0700`). Plan accepts that for `issued`/`expires`; inbox parse is unstated.
- `budget_usd` is a **string**; `capabilities` is raw text (`[llm.chat]`) or null; `spend.box.amount_usd` is float or null. Converter types are unspecified.
- `naive-1` `expires` has no zone (`expires_unparseable: true`). Here that is `bad-charter`; §12.1’s only known difference is `unapproved-1` with a live box.
- Captured `unapproved-1` has `box: null`. The known-difference test cannot run on this fixture.
- Fixture has no passing `LIVE`/`READY`/`UNAPPROVED` (skip-verify). Converter “matches full Corral” is almost all `BAD-SIG`.
- `status.py` exit 2 on missing `ACTIVE-BOXES.md`; converter “stdout only” on non-zero is unstated.

---

## 5. Q2 / Q7

**Q2:** Adopt the separate core-mounted inbox, collector-only; omit it from the CLI `ro` list (the `fetched` pattern binds it into the CLI). Same-user processes can still write it.

**Q7:** Adopt: while `state=failing`, keep serving that module’s last `bad` notices until `NOTICE_MAX_AGE_S`. A new card type is unnecessary.

---

## 6. Weakest §12 tests

1. **Naming/coverage:** absent `box:`, misspelled `box`, rename with and without explicit `box:`, ranch `<name>-node` vs local `name`, aliases covering a second listed machine.
2. **Presence exit:** ranch-incomplete omission stays `unknown`/`bad` and does not start 30 days; operator gone-path for a box never in `boxes.toml`; `ack-ledger` then restored revoked charter.
3. **`issued` / inbox signature:** `issued` > `expires`; future `issued` vs tombstone; two sources, one name; `signature: absent` and an unknown value; ranch `sig_ok`/`signed` mapping; `naive-1` timezone.

---

## 7.

Make `box` a required signed key (no default), define join/coverage on that binding, and give every in-scope box an operator gone-record so ranch-only rows can reach `not-listed`.