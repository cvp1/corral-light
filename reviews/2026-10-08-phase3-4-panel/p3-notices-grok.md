I'll review Phase 3 against the plan and the live notice path, starting with the spec and the code that implements it.The panel charge is a read-only review of Phase 3 itself. I'll read the spec and the notice path next.I'll read the notice validation, delivery, and rail rendering next, then check FinOps against the tiles.**FIX-FIRST** — a current Claude `allowed_warning` with no `utilization` never becomes a rail card, and its tile stays `info`.

## Findings

### 1. medium, PROVEN — `finops/view.py:18-21,48-52`
**Input:** a current quota window with `status="allowed_warning"` and no `utilization` (`bp is None`). That shape is real: utilization is optional, and `tests/test_report.py:71-77` already feeds a window with the field deleted.

**Result:** `_level_for` returns `"info"` when `bp is None`. The `allowed_warning` bump runs only when `level == "ok"`, so it misses. `notices()` then skips anything that is not `warn`/`bad`. The tile, if shown, is `info` with value `"no percent reported"` and note `"near its limit"`. Spec §4.7 / §8.2: current `allowed_warning` is `warn`, and the card must match the tile.

Same hole for a **fresh status on a carried-stale percent** (`report.py:282-293`): `state` follows the old utilization, so `quota_text` takes the stale branch and never applies `rejected` / `allowed_warning`. Fully-stale rejected is correctly silent (`status` is already nulled; `test_notices.py:51-56`). Mixed freshness is not.

**Fix:** apply status after the percent/state level, and bump any non-`bad` `allowed_warning`:

```python
if w["status"] == "rejected":
    level = "bad"
elif w["status"] == "allowed_warning" and level != "bad":
    level = "warn"
```

Do this for any window whose `status` is still set (report already clears it when status is stale).

### 2. low, PROVEN — `finops/view.py:82-83` and `report.py:289-290`
**Input:** two Claude fingerprints, both `five_hour` at 80%.

**Result:** two rail cards with the same heading `FinOps — Claude 5 h 80% used`. Ids differ (`quota.claude-<fp>.five_hour`), so Not now is per account; the operator cannot tell which card is which. Tiles use the same labels.

**Fix:** put a short fingerprint suffix on the notice title (and the tile label, so they still match).

### 3. low, PROVEN — `modules.py:1318-1327`
**Input:** a hub-written `snapshot.json` whose `notices[].id` is later rewritten to `Not-an-id` (or `"><img"`).

**Result:** `notices()` does not re-run `NOTICE_ID_RE`. The id is `_text`-capped and sent on `/api/state`. The page never puts the id in markup (`noticeSlot` is only a `localStorage` key), so this is not XSS. A notice can outlive the snapshot contract if that file is edited.

**Fix:** drop entries that fail `NOTICE_ID_RE` or have an empty title at read time, same as `_validate_notices`.

Caps, disable/remove, opt-in, 24 h / two-period expiry, quiet rail, phone fold, hot class, Not now-on-escalate, and textContent rendering all match §4.7.

## Tests

**Passes with the property broken:** `selftest_inbox.mjs` **NOT-4** (`notices never enter the hot count`) only checks that `blockedCount`'s source text lacks `otice`. Counting `S.moduleNotices` inside `railFold`'s `hot` class (or inside `blockedCount` under another name) still passes.

**Missing test that matters:** a current window with `status="allowed_warning"` and **no** `utilization` must yield a `warn` notice whose level equals the tile. Extend it with carried-stale utilization plus a fresh `rejected` status.

## One change

In `quota_text`, let a still-present `rejected` / `allowed_warning` set the level even when there is no percent and even when the percent is stale.