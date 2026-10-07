# Phase 0: Gemini (Antigravity) local usage records

Measured 2026-10-07 on the author's Linux host. Everything was opened read-only (`?mode=ro`). Only
structure and numbers were inspected. No prompt or answer text was printed, and no
credential file was opened.

## Verdict

**Yes. Every model call records its token usage locally.** Each row in `gen_metadata`
is one generation, meaning one model call. It holds uncached input, cached input,
total output, thinking and visible-output token counts, plus the model id, a start
timestamp and latency. The same usage message is also copied into the metadata of
the step that produced it (`steps.metadata` field 9). That copy also catches a second
kind of model call that has no `gen_metadata` row (step_type 23, see the caveats).
The Gemini row in §2 of the plan can move from "usage not reported" to "tokens
recorded locally (protobuf, undocumented)".

One correction to plan §2: the records have **no per-call request id**. Top-level
field 4 is the conversation's `cascade_id`. To identify a call, use
(`cascade_id`, `gen_metadata.idx`), or the step indices in field 2.

## Field map: `gen_metadata.data` (protobuf)

| Path | Wire | Meaning | Evidence |
|---|---|---|---|
| `1.4.2` | varint | **input tokens, uncached** | cached (`1.4.5`) is larger than `1.4.2` in 1210 of 1314 records, so `1.4.2` cannot include cached tokens |
| `1.4.5` | varint | **cached input tokens** | missing on cold calls (1212 of 1314 records have it) |
| `1.4.3` | varint | **output tokens, total** (thinking + visible) | `1.4.3 == 1.4.9 + 1.4.10` in 1314 of 1314 records |
| `1.4.9` | varint | **thinking tokens** | missing from 2 records |
| `1.4.10` | varint | **visible output tokens** | |
| `1.17.2` | msg | an exact copy of `1.4` | equal in 1312 of 1314 records |
| `1.19` | string | model id: `gemini-3.8-flash` (1154), `gemini-pro-default` (152), `gemini-3.7-flash` (8) | |
| `1.20` (repeated) | msg {1: key, 2: value} | labels. One value is always `MODEL_GOOGLE_GEMINI_INTERNAL_BYOM`, and a 36-character value always equals `cascade_id` | |
| `1.3` | varint | always 326 (a model enum). It also appears in `steps.metadata.11` and `executor_metadata` | |
| `1.9.4` | msg {1: seconds, 2: nanos} | **when the call started** (UTC epoch) | |
| `1.11` / `1.12` | msg {1: s, 2: ns} | time to first token / streaming time | `(step.8 − 1.9.4) − (1.11 + 1.12)`: median 0.006 s, p90 0.017 s |
| `1.15` | msg | generation config: max tokens 65535, temperature 1.0, top-k 50, stop sequences | |
| top `2` | packed varints | **indices of the steps this call produced**. The first is the model-response step (step_type 15), followed by its tool-call steps | for 1312 of 1314 records, the step whose usage matches is in this list, at position 0 in 1307 of them |
| top `4` | string(36) | `cascade_id`, the same for every record in a conversation (not a request id) | equals `trajectory_meta.cascade_id` in 1314 of 1314 records |
| `1.1`, `1.2`, `1.8`, `1.16`, top `3` | msg | only in the first record of each conversation (24 of 1314). They hold the full request setup and were not decoded, because they contain content | |

The usage message also appears at `steps.metadata.9`, with the same subfields. The step's
alias model id is at `steps.metadata.24.8`: `gemini-3.8-flash-high`, `gemini-pro-agent`
or `gemini-3.7-flash-high`, which map one-to-one to `1.19`. Other timestamps in
`steps.metadata` (all {seconds, nanos}): `1` created, `6` first token, `7`/`8` done,
`22`, `32`.

## Evidence

- **Scope:** 24 conversation DBs in `~/.gemini/antigravity-acp/conversations/`, each
  with a `.meta` JSON file (keys `cwd` and `mode_id`). They hold 1314 gen records with
  start times from 2026-09-28T02:20Z to 2026-10-07T16:34Z. `~/.gemini/antigravity/`
  holds only a binary. The `brain/<id>/` folders hold MCP tool schemas and task logs,
  with no usage data. There are no other SQLite stores.
- **One record is one call:** 1312 of the 1314 records match exactly one step by an
  identical usage message, and that step is listed in the record's own field 2. The
  number of records equals the number of step_type 15 (model response) steps per DB,
  within 0 to 3.
- **Input grows across a conversation:** uncached + cached input never decreases from
  one call to the next in 1272 of 1290 consecutive pairs. Uncached input on its own
  never decreases in only 909 pairs, which shows `1.4.2` is the uncached part. The 18
  drops fit context compaction. Example totals: first call 11,805, next call 12,862.
- **Output matches response size:** across 1312 matched calls, the correlation between
  total output tokens (`1.4.3`) and the byte size of the step's payload is 0.968. For
  visible tokens only (`1.4.10`) it is 0.48, which shows the payload also contains the
  thinking text.
- **Totals for a heavy conversation** (293fcbcc, 112 calls): 0.81M uncached input,
  8.2M cached input, 64k output, 44k of it thinking. Caching dominates.

## Schema (all 24 DBs are identical)

- `trajectory_meta(trajectory_id, cascade_id, trajectory_type, source)`: 1 row
- `steps(idx, step_type, status, has_subtrajectory, metadata, error_details, permissions, task_details, render_info, step_payload, step_format)`
- `gen_metadata(idx, data, size)`: `size == length(data)` always
- `executor_metadata(idx, data)`: 0 to 2 rows of executor/tool config
- `parent_references(idx, data)`: empty everywhere
- `trajectory_metadata_blob(id, data)`: 1 row, empty in the sample checked
- `battle_mode_infos(idx, data)`: empty everywhere

## Caveats

- **Model calls with no gen record:** 28 steps of step_type 23 carry usage (field 9)
  but have no `gen_metadata` row and no model alias. The pattern is no thinking
  tokens, about 3.5–4.5k output and 56–100k cached input, which looks like
  summarisation or checkpoint calls. 16 of 24 conversations have 1 to 3 of them. A
  collector should read usage from `steps.metadata.9`, which covers both kinds, and
  use `gen_metadata` for the model id and timing. 2 gen records have no matching step
  usage.
- The format is undocumented, and the field numbers come from inference. They are
  stable across this host's two model families and about 10 days of data. A Google
  update could change them, so the source needs a schema canary, for example the
  `1.4.3 == 1.4.9 + 1.4.10` check.
- There is no cost field and no quota or rate-limit data here. Only BYOM-labelled
  calls were seen (`MODEL_GOOGLE_GEMINI_INTERNAL_BYOM`).
- Live DBs use WAL (`42831f04` had `-wal`/`-shm` files). Read-only URI opens worked
  without copying.
- Not decoded: `1.9.10.1`, a small per-call integer of unknown meaning. `1.9.10.4` is
  always 128000, probably a context or limit value.
- The helper scripts (`pbshape.py`, `agg*.py` in this directory) print only structure
  and numbers. They print strings only when the string looks like a model id.
