I'll review Phase 4 as a read-only security pass: start with the plan and the fetch/key path, then hunt for key leakage, host escape, and money/ingest bugs.The review skill is a PR workflow; this is a panel pass on live code. Next I’ll read the plan, prior findings, and the fetch/key path.Key storage looks careful; next I’ll check egress, sandbox binds, and whether open() can still follow a swapped path.**FIX-FIRST** — a fetcher can land the grant key in the stored result (and then the collector snapshot) because `_check_result` only substring-matches raw stdout.

## Findings

1. **High. PROVEN.** `module_fetch.py:346-358` then `406-409`. Needles run on stdout bytes, then `json.loads` + `_write_json` rewrite the object. JSON `\uXXXX` of the key is not in `needles()` (those add the raw key, PEM lines, JSON-string escaping, and standard base64 only). Input: a fetcher prints `{"org":{"name":"\u0073k-ant-admin01-FIXTUREKEY-0123456789abcdefghijklmnopqrstuvwxyz"}}` (the fixture key with the first `s` as `\u0073`). `_check_result` accepts it; the stored `module-fetch/<module>/<key>.json` contains the key in plaintext; `billed.parse` copies `org.name` onto the tile. Plan §6.7 also says the snapshot is refused if it contains the key; nothing scans the snapshot. Same hole for urlsafe-base64 and hex. Smallest fix: after `json.loads`, refuse if any needle appears in `json.dumps(obj).encode()`, and run the same check on the collector snapshot before it is accepted.

2. **Medium. PROVEN.** `module_fetch.py:84-99`, `323-325`; `module_sandbox.py:81`. `key_add` uses `O_NOFOLLOW`; `key_path` `lstat`s; `build_fetch` then `open(kp)` and `_real()` follow a symlink. Input: after `key_path`/`open` and before `bwrap --ro-bind`, replace `keys/<name>` with a symlink to another 0600 file the operator owns (a second vendor key, a lane login). The sandbox is bound to the target; that secret is sent to this grant’s vendor; needles are from the bytes already read (or from the new file, which does not stop the vendor POST). Smallest fix: `os.open(..., O_RDONLY|O_NOFOLLOW)`, `fstat` that fd (refuse `nlink != 1`), and bind the opened path only if it still matches.

3. **Medium. PROVEN.** `finops/view.py:163-166`, `finops/util.py:119-124`. Billed days are UTC `YYYY-MM-DD`; “this month” / “last month” use `month_days(now, tz)` local calendar strings. Input: `tz=Pacific/Auckland`, `now=2026-10-01T01:00+13:00`, a `billed_day` of `2026-09-30` with `100.10`. Local October’s tile omits it (filter is `day>='2026-10-01'`), though that UTC day is local October morning. Tests pin `Host.tz = "UTC"`. Smallest fix: convert local month start/end to UTC dates (or sum by UTC month and say so).

4. **Medium. PROVEN.** `finops/sources/billed.py:91-94` with `finops/fetch/anthropic.py:33-34`. A complete fetch with an empty `days` object still `DELETE`s `[start, end)`. Input: ledger has `2026-10-01 = 12.3456`; next accepted wrap has `range` covering October and `"days": {}` (200 + empty `data`, which Anthropic does not treat as an error). Those days are gone. Smallest fix: skip the DELETE when `rows` is empty, or require at least one day unless the previous fetch was also empty.

5. **Low. PROVEN.** `module_fetch.py:276-277`. Needles shorter than 12 bytes are dropped. Input: `key_add("k", "shortsecret")` (11 bytes) and a result that contains `shortsecret`. `needles` is empty; the result is stored; `scrub` leaves it in errors. Smallest fix: always include the stripped key, whatever its length.

6. **Low. PROVEN.** `finops/fetch/xai.py:23-24,34`. Team id from the vendor is placed in the URL path with only “no `/`, len≤80”. Input: validation returns `scopeId: "abc?x=1"`. Request becomes `/v1/billing/teams/abc?x=1/usage` on `management-api.x.ai` (still that host, Authorization header still sent). Smallest fix: allow only `[A-Za-z0-9_-]` and `urllib.parse.quote`.

RSA (`rsa.py:62-76`) matches PKCS#1 v1.5 + CRT and is checked against OpenSSL. GCP `` `{table}` `` is constrained by `TABLE_RE`. Exact-host CONNECT plus `denied_everywhere` holds. `patch_adapters` rollback code is right (`update.py:264-272`).

## Tests

`test_the_key_in_a_result_is_refused` (`test_module_fetch.py:243`) still passes with finding 1: it only covers raw and standard-base64 leaks (`leak`, `leak_b64`).

Missing test that matters most: fixture mode `leak_u` (JSON unicode-escape of the key, and urlsafe-base64) must fail the run and leave no `k1.json`; plus a collector snapshot that embeds the key must be refused.

## One change

Re-scan needles on the canonical JSON after parse (and on the snapshot), and open/bind the key from an `O_NOFOLLOW` fd.