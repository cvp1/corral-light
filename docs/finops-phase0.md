# FinOps module, Phase 0: results

Run 2026-10-07 on the author's Linux host (Arch, bubblewrap 0.12.0,
claude-agent-acp 0.85.1 with agent SDK 0.3.286, codex-acp 2.1.1, grok
1.0.46). No product code was written. Scripts are in `spike/p0/`; every
one prints counts, field names and enums only, never prompt or answer
text, and none opens a credential file except to read one expiry time.

**Verdict.** The plan holds. Every vendor source is better than rev 4
assumed: Claude quota arrives on every query start, Codex has a per-call
usage record with an id, Grok marks forks exactly, and Gemini records
tokens per call. Twelve changes follow; they are listed at the end and
applied in plan rev 5.

## Claude

**Transcript records per request** (`spike/p0/claude_reqid.py`; both
homes, 165 files, 102 MB):

| Measure | Result |
|---|---|
| assistant records with usage | 7,777, all with a `requestId`; 22 `<synthetic>` skipped |
| distinct requests | 3,380 |
| records per request | 1 to 13; mostly 2 or 3 (one per content block) |
| requests with more than one record whose usage differs | 8 of 2,460; the only differing fields are `output_tokens` and its details |
| within one file, `output_tokens` across a request's records | never decreases (2,460 of 2,460); the last record is the largest |
| distinct `message.id` per request | always 1 |
| one request in two files, or in both the home and a pane tree | 0 |
| usage keys | `input_tokens`, both cache fields, `output_tokens`, `service_tier` (always `standard`), `cache_creation`, `inference_geo`, and on newer records `output_tokens_details`, `server_tool_use`, `iterations`, `speed`, `fallback_credit` |
| full scan of both trees | 0.8 s |

Rev 4's rule (largest output count wins per `requestId`) is right. The
cross-home dedupe never fired on this host, but it stays: nothing
prevents a pane transcript from being copied home.

**`usage_update.cost`.** It is the SDK's `total_cost_usd` for the
agent process. The SDK documents it as "an estimate, not a billing
statement": it resets on `/clear`, and a resume continues from a total
saved in the transcript only "when it has one". Light's records agree:
of 15 Claude panes with a cost, 4 show drops, and every drop follows a
`cleared` or `resumed` event (for example 37.49 to 0.30 after a resume,
16.36 to 13.73 after another). A compaction sends a `usage_update`
with no cost, which the next result replaces. Conclusion: the cost is
per process generation, segment it at `cleared` and `resumed`, and keep
it a diagnostic only, as §8.4 already says.

**Rate-limit notices** (`spike/p0/claude_ratelimit_probe.mjs`; one
one-word Haiku query, `persistSession: false`, no tools; the shared
login had over three hours left, and its file was unchanged after):

```
RLE  at 1.2 s  status=allowed rateLimitType=five_hour resetsAt=<epoch s>
               overageStatus=rejected overageDisabledReason=org_level_disabled
               unifiedWindows={five_hour:{utilization:0.16,resetsAt}, seven_day:{utilization:0.07,resetsAt}}
first assistant message at 4.0 s
```

- **It arrives before the first assistant message of every query.**
  The adapter forwards it only when `lastAssistantTotalUsage` is set,
  so on a fresh process (every new pane, every resume) it is always
  dropped. The adapter patch in Phase 1 is required, not optional.
- **`utilization` is a fraction from 0 to 1** (0.16 here). The
  structured usage call reports the same window as 17, a percent.
- **`unifiedWindows`** is not in the SDK's type, but it carries both
  the five-hour and weekly windows in one notice. The feed should read
  it when present and fall back to the single `rateLimitType` window.
- `resetsAt` is in epoch seconds; the structured call uses ISO strings.
- One notice per query on this account. Frequency under load is not
  measured; the freshness rules in §6.5 do not depend on it.

**Experimental structured usage call**
(`spike/p0/claude_usage_probe.mjs`, `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET`,
324 ms, needs an open query): returns `subscription_type`, five-hour
and weekly percent with ISO reset times, a model-scoped weekly window,
extra-usage state, and a seven-day breakdown by surface. It is richer
than the notices. **Not adopted for v1:** the SDK names it unstable,
and it makes a network call to the vendor with the pane's login, which
the panel ruled out. It is recorded as the upgrade path if Anthropic
stabilises it; the adapter already uses it for `/usage`.

**macOS Keychain plan fields: not measured.** It needs reading a
credential store on the other machine; the plan already takes the plan
from the feed, so nothing in v1 depends on it.

## Codex

(`spike/p0/codex_rollouts.py`; Light's `CODEX_HOME`, 29 rollouts.)

| Measure | Result |
|---|---|
| `token_count` events with `rate_limits` | all; `plan_type` `plus`; primary window 300 min, secondary 10,080 min |
| `rate_limits` keys | `plan_type`, `primary`, `secondary`, `credits`, `limit_id`, `limit_name`, `individual_limit`, `rate_limit_reached_type`, `spend_control_reached` |
| cumulative total decreases within a session | 0; equal consecutive totals 6; timestamps out of order 0 |
| new record type `token_usage_record` | 299; one per model response, keyed by `response_id`, with `usage`, `turn_token_usage` and `thread_token_usage` |
| sum of `token_usage_record.usage` vs the last cumulative total | equal in every file |
| account id | `session_meta.creator_account_id` (plus `creator_user_id`); no token needed |
| resume (`codex exec resume`, tested) | appends to the same rollout file; the cumulative total continues (15,774 then 32,333); no reset |
| full scan | 0.2 s |

- **Use `token_usage_record` keyed by `response_id`** where present. It
  is idempotent, needs no delta arithmetic, and makes reset handling a
  fallback for older rollouts only.
- **Account fingerprint:** hash `creator_account_id` from the rollout
  header. The core never opens `auth.json` for this.
- **Codex adapter quota updates:** `account/rateLimits/updated` is
  stored in the adapter's session state and shown only by `/status`;
  nothing reaches ACP. Rollouts stay the source.
- Fork was not tested; one rollout carries `parent_thread_id` and its
  first total equals its own first turn, so it inherited no counts.
- **`rate_limits` against the CLI's own status view: not measured.** It
  needs the interactive CLI at the same minute; it stays a §8.4 live
  check.

## Grok

| Measure | Result |
|---|---|
| `grok usage` on Light's 30 Grok sessions | 24 return JSON; 6 exit 1 with "No usage recorded" (sessions with no turns) |
| time per call | 65 ms median outside the sandbox, 160 ms inside |
| per-turn keys | `turnNumber`, `endedAt` (ns), token fields, `modelCalls`, `modelUsage`, `primaryModelId`, `costUsdTicks` |
| resume (same id) | appends turn 2; nothing repeated |
| fork (`--resume A --fork-session`) | the new session's report repeats A's turns 1 and 2 with identical `turnNumber`, `endedAt` to the nanosecond, tokens and ticks, then adds turn 3 |
| fork marker | the session's `summary.json` has `parent_session_id` and `forked_at` |
| inside the collector sandbox, binding only the binary, read-only | works with the session's `usage.json` and `summary.json` only; `usage.json` alone fails "Session not found"; no `auth.json`, no network |

- **Inherited turns are exact, not guessed:** a turn of a forked session
  whose `endedAt` is at or before `forked_at` is inherited. Rev 4's flag
  ("numbering does not start at one") would have missed it, because the
  fork's numbering starts at one. The core passes `parent_session_id` and
  `forked_at` in the feed beside each report; the module counts only
  turns after `forked_at`, and drops "may double-count".
- **The core's Grok sandbox binds two files per session**, read-only:
  `usage.json` and `summary.json`, under a scratch home. The chat history
  in the same folder is never in view.
- None of Light's Grok panes had been resumed or forked; the test
  sessions were made for this measurement.

## Gemini (Antigravity)

Full report: `spike/p0/gemini/README.md`. Summary:

- **Tokens are recorded per model call.** 24 conversation databases,
  1,314 records. Fields: uncached input, cached input, output total,
  thinking, visible output; model id; start time and latency.
- **There is no request id.** The plan's §2 was wrong: the 36-character
  id is the conversation's `cascade_id`. A call is
  (`cascade_id`, step index).
- **Read `steps.metadata` field 9, not `gen_metadata`:** 28 summary-like
  calls have usage there and no `gen_metadata` row.
- No cost, quota or plan data. The format is undocumented protobuf; a
  canary (output total equals thinking plus visible, true in 1,314 of
  1,314) freezes the source if Google changes it.
- Databases use WAL; read-only URI opens worked without copying.

## Collector sandbox

Full report and tests: `spike/p0/sandbox/`. 9 of 9 tests pass on this
host:

- `session.key` (real and fixture), every lane login, `~/.ssh` and the
  other vendor folders do not exist inside.
- The hub port gives "connection refused" (the sandbox has its own empty
  loopback); an external address gives "network unreachable".
- Declared paths and the feed are read-only; the data dir is the one
  writable place; the root is remounted read-only.
- Python startup costs about 5 ms more inside.
- The Grok binary is a static executable; binding the one resolved file
  is enough.

Still open: memory, CPU and process limits (`prlimit` in the runner).

## Scan timing

A cold full scan of everything on this host takes about 1 s (Claude
0.8 s for 102 MB, Codex 0.2 s, Grok 24 reports in under 2 s). The 30 s
budget in §6.5 is ample; the first backfill fits in one run here.

## Changes to the plan (applied in rev 5)

1. §2 Claude row: the rate-limit notice arrives before the first reply,
   so the adapter drops it on every fresh process; `utilization` is
   0 to 1; `unifiedWindows` carries both windows.
2. §2 Codex row: `token_usage_record` per response; account id in
   `session_meta`.
3. §2 Grok row: fork marker in `summary.json`; inherited turns exact.
4. §2 Gemini row: tokens recorded per call; no request id.
5. §2.1: Gemini gains token counts for the list-cost metric.
6. §4.6: the adapter patch forwards a notice that arrives before usage
   (queue it until the first usage, or send it with `used` absent).
7. §6.5 Claude: the rule stands; cost segments at `cleared`/`resumed`.
8. §6.5 Codex: `response_id` records first; cumulative deltas only for
   rollouts without them.
9. §6.5 Grok: count turns after `forked_at`; "may double-count" dropped.
10. §6.5 quota: normalise Claude `utilization` (fraction) and Codex
    `used_percent` (percent) to one scale; `resetsAt` seconds.
11. §4.6 core-run Grok reports: bind `usage.json` and `summary.json`
    only; pass `parent_session_id` and `forked_at` in the feed.
12. §9: the three open questions are decided (see the plan).
