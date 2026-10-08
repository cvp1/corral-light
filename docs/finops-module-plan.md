# Corral Light modules, and FinOps as the first one

> Status: rev 6, 2026-10-08. Phase 0 done; Phase 1 (the seam) built and on
> master; Phase 2 (the FinOps module) pushed, installed live from its own
> repository, and through the §8.4 checks (§7). Phase 3 (notices) is
> specified in §4.7 and not built.
>
> Rev 6 (2026-10-08) specifies Phase 3, which rev 5 left as one table row
> pointing at tests that did not exist: the `notices` field of the
> snapshot, its bounds and expiry, where the rail shows notices, and which
> notices FinOps raises. Not yet reviewed by a panel.
>
> Rev 1 went cold to a three-vendor panel (Codex on gpt-6-astra, Grok,
> Gemini): AMEND, AMEND, REJECT. All three found the same central defect:
> rev 1 offered collectors "read-only" access to a state dir that holds the
> wall's cookie-signing key, and nothing enforced the read-only part. Rev 2
> is the reshape. What changed and why:
> `reviews/2026-10-07-finops-module-panel/synthesis.md`.
>
> Rev 3 (same day), on the operator's direction "automate this as much as
> possible": every figure now comes from the vendor's own source of truth
> wherever one exists, and local token counts priced at list are only the
> fallback. New: §2.1 (the order of sources), vendor quota for Claude, the
> vendor's own cost for Grok, automatic account setup, opt-in vendor billing
> APIs. Rev 2's survey was wrong about Grok; §2 is corrected.
>
> Rev 4 (same day) answers the second panel round on rev 3 (AMEND, AMEND,
> AMEND; synthesis, round two). Main changes: a catalogue price never
> becomes "declared" without the operator typing it; per-metric source
> lists replace the single ranking; the core, not the collector, runs
> `grok usage` in its own sandbox; all six Claude quota windows are kept,
> and the adapter's dropped events are fixed at the adapter; quota
> staleness is reworked; fetchers get exact host matching and a confined
> secret directory.
>
> Rev 5 (same day) applies the Phase 0 measurements
> (`docs/finops-phase0.md`) and decides the three open questions (§9).
> Main changes: the Claude adapter patch is required, because the
> rate-limit notice arrives before the first reply of every query;
> Codex usage is read from per-response records keyed by `response_id`;
> Grok forks are marked exactly by `forked_at`, so "may double-count" is
> gone; Gemini records tokens per call, so it gets a token source in v1;
> the core's Grok sandbox binds two files per session, not the folder.

## 0. The ask

The operator, 2026-10-07: make FinOps the first feature to port from full
Corral into Corral Light, as a module. It must not be part of Light's code
base. Someone who installs Corral Light and wants FinOps should be able to
install it, point it at the accounts they use, and have it work out on its
own how to read Claude Code, Codex, Gemini and the other lanes. Spec it,
plan the build and the tests, and send it to the panel.

Two deliverables, separable:

1. **A module seam in Light.** Light has no extension mechanism today.
   FinOps is the first module, so it defines the seam.
2. **The FinOps module itself**, in its own repository.

## 1. What full Corral has, and what carries over

Full Corral's FinOps is a room that renders, verbatim, the output of a
separate engine in the operator's private workspace. The engine joins
per-provider LLM spend rolled up into InfluxDB, a human-owned subscription
rate table, provider-reported balances, and cloud resources with list
prices and budgets, into a five-signal strip.

Rules that carry over unchanged:

- **Null is unreported, never zero.** No data must not render as good data.
- **Every figure says what kind it is:** billed, vendor-reported, declared,
  list price, estimate, or unknown.
- **The viewer computes nothing.** Light's core renders what the module
  reports.
- **Each source degrades alone.**
- **Reads are cached server-side on their own clock.** A browser poll
  never becomes a disk scan.

Not carried over: InfluxDB, Grafana, the goals evaluator, cloud inventory,
electricity, and anything that imports the private workspace. The core
suite already bans a `finops.py` from Light's tree.

Light's FinOps answers five personal questions:

1. What am I paying per month for these lanes? (declared subscriptions)
2. How much quota is left before I hit a limit? (where the vendor reports it)
3. What would this usage have cost at API list price? (a counterfactual,
   labelled as one)
4. What did a given pane, consult panel, role or worktree use?
5. What am I spending on pay-per-call keys, if any? (later phase)

## 2. What the machine exposes (surveyed 2026-10-07, corrected by the panel)

| Lane | Login and plan | Usage records | Quota | Light already records |
|---|---|---|---|---|
| Claude Code, Linux | credentials file fields `subscriptionType`, `rateLimitTier`; each Light pane's credential is a link to the shared file | JSONL transcripts in `~/.claude/projects/` AND in each pane's private config dir `<state>/panes/<id>/config/projects/` (Light links skills and plugins into pane dirs, not `projects`, so these are a second tree) | **vendor-reported, as change notices:** the Claude agent library emits `rate_limit_event` "when rate limit info changes": status `allowed`, `allowed_warning` or `rejected`; optional `rateLimitType` (`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage`); optional `resetsAt` (epoch seconds) and `utilization` (**a fraction, 0 to 1**, measured); sibling overage fields (`overageStatus`, `overageResetsAt`, `isUsingOverage`, and more); and, outside the published type, `unifiedWindows` with both the five-hour and weekly windows in one notice. One notice arrives at the start of every query, **before the first assistant message** (measured). Light's Claude adapter forwards each event as a `usage_update` carrying `_meta["_claude/rateLimit"]`, **but drops it when it arrives before the turn's first usage, which is always the case on a fresh process** | the latest ACP `usage_update` (`used`, `size`, `cost`) is kept in memory and written inside every `turn_end` event. **The rate-limit payload is lost:** the turn's final `usage_update` overwrites it before `turn_end`, so no record on this host holds one |
| Claude Code, macOS | the login is in the Keychain; no credentials file; Light does not give panes a private config dir | `~/.claude/projects/` only | same as Linux | same as Linux |
| Codex | `auth.json` in Light's own `CODEX_HOME` (`~/.config/corral-light/codex-home` by default, from `CORRAL_CODEX_HOME`), separate from a desktop `~/.codex` | rollout JSONL under `CODEX_HOME/sessions/`; one `token_usage_record` per model response, keyed by `response_id` (their sum equals the cumulative total in every file measured); `token_count` events with cumulative and last-turn usage; `session_meta.creator_account_id` names the account. Resume appends to the same file and the cumulative total continues | **vendor-reported:** each `token_count` carries `rate_limits`: `plan_type`, 5 h and weekly `used_percent`, `resets_at`, credits. Codex's app server also has an official `account/rateLimits/read` call; Light's Codex adapter calls it only for `/status` and drops quota-update notifications, so the rollout files are the source | `usage_update` with `used`, `size`, no cost, inside `turn_end` |
| Grok | `~/.grok/auth.json` | **vendor-reported cost:** `grok usage <session-id>` prints JSON with session and per-turn input, output, cached and reasoning tokens, model calls, and `costUsdTicks` (10¹⁰ ticks per USD), computed by xAI. A fork's report repeats the parent's turns with identical `turnNumber` and nanosecond `endedAt`; the fork's `summary.json` holds `parent_session_id` and `forked_at`, so inherited turns are exact. Resume appends turns and repeats nothing. Grok's docs say to use this command, not the `usage.json` file it reads. Tested: the command works in the collector sandbox with only that session's `usage.json` and `summary.json` bound, no login, no network, 160 ms | weekly pool: only inside Grok's interactive `/usage`; no command | none |
| Gemini (Antigravity) | `~/.gemini/antigravity-acp/acp_token.json` | per-conversation SQLite; each model call's usage is a protobuf message in `steps.metadata` field 9 (uncached and cached input, output, thinking, visible output), with model id and timing in `gen_metadata`; undocumented, decoded in Phase 0. No request id: a call is (`cascade_id`, step index) | no supported interface | none; the lane runs Google's own ACP server, which emits no `usage_update` |
| Ollama | none | none needed | none | none |

Consequences:

- Claude and Codex quota, and Grok cost, come from the vendor with no
  network and no new login.
- Gemini has token counts (list-cost metric only); no cost, quota or plan.
- A scanner that reads only `~/.claude/projects` misses Linux pane
  transcripts; one that reads both must dedupe.

### 2.1 Sources of truth, per metric

Rev 3 ranked all sources in one list. The panel showed that compares
unlike quantities: a quota notice, a vendor-computed session cost, a bill
and a declared fee are different measurements. Rev 4 defines each metric
separately. A source only ever fills its own metric; nothing from one
metric overrides another. Every value carries its account, period,
source, observation time, coverage (what share of the period's records it
covers) and kind.

| Metric | Sources, best first | Kind |
|---|---|---|
| **Quota state** (per account, per window) | Codex rollout `rate_limits`; Claude rate-limit notices from the feed | `vendor` |
| **Subscription commitment** (per account, per month) | the amount the operator typed; otherwise the `plans.toml` list price for the vendor-stated plan; otherwise null | `declared`, else `list` |
| **Vendor-computed usage cost** (per session, per turn) | `grok usage`, counting only a fork's turns after `forked_at` | `vendor` |
| **Billed spend** (per org account, per day) | vendor billing APIs (§6.7) | `billed` |
| **API-equivalent list cost** (per account, per day) | local token counts (Claude, Codex, Gemini) priced from `prices.toml` | `list` |

Where two metrics describe the same tokens (Grok's vendor cost and a list
estimate of the same turns), both may be shown, side by side and named;
the estimate's difference from the vendor figure is a doctor diagnostic,
never a correction. Vendor-computed cost is labelled "computed by the
vendor's CLI" and is not called a bill.

## 3. Trust model (new in rev 2)

A module is code the operator installs. It runs as the operator's user.
Rev 2 does not pretend otherwise. It does three things instead:

1. **Gives the module nothing it does not need.** The collector never
   receives Light's state dir, the hub's environment, or any credential
   file. It gets a redacted feed the hub writes (§4.4), the vendor usage
   directories its manifest declares, and one writable data dir.
2. **Enforces that on Linux.** The collector runs under bubblewrap, using
   a **new allowlist profile** built from the same bubblewrap helpers
   Light ships for blind reviewers (`review_sandbox.py`). It is not the
   reviewer profile with options: that profile mounts the host root
   read-only and shares the host network when no egress proxy is given.
   The collector profile has system directories
   read-only, the declared usage paths read-only, the feed read-only, its
   data dir writable, a private `/tmp`, its own pid and network namespaces,
   and no network at all. Everything else in home, including `session.key`,
   SSH keys, other logins and Light's pane transcripts, does not exist
   inside the sandbox.
3. **Says so where it cannot enforce.** On a host without bubblewrap
   (macOS, minimal Linux), installing a module shows "this module will run
   unsandboxed as your user and can read anything you can", and the hub
   runs it only after a typed acknowledgement recorded in the pin. The
   module's tile carries an "unsandboxed" face for as long as that holds.

Why the sandbox is enough for the confidentiality question: the collector
must read Claude transcripts, which contain prompts, to count tokens. It
cannot be prevented from seeing them. It can be prevented from sending
them anywhere: no network, and the only writable place is its own data dir,
which the hub reads through the snapshot contract alone.

The install pin (§5.3) is integrity, not security. It stops a half-finished
update, a stray file or a swapped symlink from running. It does not stop
same-user malware, which could rewrite the pin file; the sandbox is the
control for that.

## 4. The module seam (in Light's core)

### 4.1 Decisions

| # | Decision | Why |
|---|---|---|
| M1 | **Out of process, time-boxed.** The hub runs the collector as a fresh subprocess on its own thread, in a new session and process group, killed by group on timeout. Never a long-lived child; never imported. | The hub is the single point of failure for every live pane. A module's crash, hang or memory blow-up must cost only its own tile. All three reviewers agreed. |
| M2 | **Declarative views.** The snapshot carries typed blocks; the core renders them with `textContent`. No module JavaScript, no iframe, no free-form `data` field. | Module JS in the paired page could call any hub route, including answering permission cards. Unanimous. |
| M3 | **Not in Light's repository.** Each module is its own repository. `corral-light module add finops` resolves the short name through `modules/index.json` in core. | The operator's requirement. |
| M4 | **Pinned, staged, verified on every run.** See §5.3. | Integrity against half-updates and stray files. |
| M5 | **Explicit updates with rollback.** `module update` stages a new generation, verifies it, waits for the collector to be idle, switches atomically, and keeps the previous generation for `module rollback`. Core `update` never touches modules. | Avoids a run importing half-old, half-new files. |
| M6 | **The feed, not the state dir.** The hub writes a redacted, versioned feed (§4.4). It is the module's only view of Light. | Replaces rev 1's M6, which all three reviewers rejected. |
| M7 | **CLI under its own name only.** The wrapper dispatches core verbs first; an unknown verb reaches a module only if it is the exact name of an enabled module, verified like the collector, run with argv passed as a list, never through a shell. Core verb names are reserved. | No shadowing; one verification path. |
| M8 | **No notices in v1.** The dialog shows quota state. Rail chips come after v1, with count limits, stable ids and expiry tied to snapshot freshness. Specified for Phase 3 in §4.7. | Two reviewers wanted bounds that v1 does not need yet. |

### 4.2 The manifest (`module.json`, JSON, not TOML)

The core's strict TOML reader refuses tables, arrays, floats and booleans
on Python 3.9 and 3.10, which Light supports. JSON is in the standard
library everywhere and the manifest is written by module authors, not
hand-edited by operators.

```json
{
  "schema": "corral-light.manifest/1",
  "name": "finops",
  "title": "FinOps",
  "version": "0.1.0",
  "core_api": 1,
  "summary": "What your lanes cost, and how much quota is left",
  "collector": {"script": "collector.py", "args": ["snapshot"],
                "every_s": 300, "budget_s": 30, "timeout_s": 45},
  "cli": {"script": "cli.py"},
  "doctor": {"script": "cli.py", "args": ["doctor"]},
  "reads": ["claude-projects", "codex-sessions", "light-feed"],
  "vendor_reports": ["grok-usage"],
  "network": "none"
}
```

Rules, each refused at install with the reason:

- `name` matches `[a-z][a-z0-9-]{0,31}` and is not a core verb.
- `script` is a relative path to a regular file inside the module, with no
  `..` and no symlink anywhere on the path. The core runs it with its own
  interpreter in isolated mode (`sys.executable -I script args`). There is
  no way to say `python3 -c` or `-m`.
- `reads` names entries from a fixed vocabulary the core resolves to real
  paths per platform; a module cannot ask for an arbitrary path. v1
  vocabulary: `claude-projects`, `codex-sessions`, `gemini-store`,
  `light-feed`. None of them contains a login file.
- `vendor_reports` names reports the **core** produces by running a
  vendor's own command, from a fixed vocabulary (v1: `grok-usage`). The
  module never sees or runs the vendor binary; rev 3's design let it, and
  a collector that can see a binary can run it with any arguments.
  For `grok-usage`, the core, before each collector run:
  1. stats the Grok session dirs and picks sessions changed since the
     last report, at most 20, newest first;
  2. runs `grok usage <id>` once per session in its own sandbox: the
     **resolved binary file only** (`~/.grok/bin/grok` is a symlink to a
     versioned file in the same folder as `auth.json`, so the folder is
     never bound), `HOME` a private scratch dir holding read-only binds
     of that one session's `usage.json` and `summary.json` and nothing
     else (the chat history beside them is never in view; Phase 0: the
     command needs exactly these two), no network, a 10 s timeout,
     memory and process limits, killed by group;
  3. checks the output is one JSON object under 1 MiB with the expected
     keys, keeps only the numeric usage fields, session id, turn numbers
     and timestamps, adds `parent_session_id` and `forked_at` from
     `summary.json`, and records the binary's version beside them;
  4. writes the result to `module-feed/v1/vendor/grok-usage/<id>.json`.
  A failed or hung call marks that session stale and does not delay the
  others or the collector. Tested on this host (Phase 0): the command
  answers inside the collector profile with those two files bound.
- `network` is `none` for the collector, always. Vendor billing APIs
  (§6.7) run in a second, separate entry, `fetcher`, declared with
  `"network": ["api.anthropic.com", ...]`: it gets egress to those exact
  hosts only, and none of the `reads` (§6.7).
- Unknown keys or a `core_api` the core does not speak are refused.

### 4.3 Where things live

| What | Where |
|---|---|
| Module generations | `<state>/modules/<name>/<commit>/`, with `current` naming the active one |
| Pins | `~/.config/corral-light/modules.json`: source, commit, tree digest, enabled, `unsandboxed_ack` |
| Operator config | `~/.config/corral-light/modules/<name>/config.toml`, in the subset tomlmini reads. A folder, not a file, so the module's own `setup` can replace it atomically from inside its sandbox: the CLI gets that folder writable, the collector gets it read-only (Phase 1) |
| Module data | `<state>/module-data/<name>/`, the only writable dir |
| Feed | `<state>/module-feed/v1/`, written by the hub, read-only to modules |
| First-party index | `modules/index.json` in Light's repo |

### 4.4 The feed (`module-feed/v1`)

Written by the hub atomically (temp file and rename) on pane changes and
every observer tick when something changed. Contents:

- `panes.json`: per pane, open or archived within 35 days: `id`, `agent`,
  `model`, `title`, `created`, `closed`, `acp_session`, `worktree_id`,
  `role`, origin (`consult`, `challenge`, `rig`, human), `challenge_of`,
  and `usage`: a list of `{turn, at, used, size, cost}` taken from that
  pane's `turn_end` events. No prompt text, no tool payloads, no paths
  beyond `cwd`. `title` is published only when the operator typed it
  (`title_named`, set by rename); otherwise it is the lane's label. An
  untitled pane is named after its first prompt, and a port copies and
  locks that name, so neither the title nor `title_locked` is safe alone.
- `quota.json`: per **account** (by login fingerprint), per window type,
  the newest quota observation from any pane, merged per field: a newer
  notice updates the fields it carries, and a vendor field it lacks keeps
  its last value, with `carried: {field: observed_at}` saying when that
  value was seen. Every validated field the
  vendor sent is kept: for Claude, `status`, `rateLimitType` (a missing
  type is keyed `_unknown`; any other odd name keeps a sanitised form of
  itself plus a short hash, so two never share a key), `resetsAt`,
  `utilization` exactly as sent, and the overage fields. Each observation
  carries `observed_at` (hub clock) and `resets_at_s` (the reset
  normalized to epoch seconds, with the unit the vendor used recorded).
  Written **on arrival**, not at `turn_end`, so an interrupted turn or a
  hub restart keeps it.
- `vendor/grok-usage/<id>.json`: the core-run reports (§4.2).
- `logins.json`: per lane, the facts the core already reads for its own
  login checks: present, plan and tier for Claude (credentials file on
  Linux, Keychain on macOS), account fingerprint (a hash, never the id) and
  plan for Codex, auth mode for Grok. Never a token, never a raw id.
- `host.json`: platform, timezone, sandbox available, Light version.

The feed is the one place modules learn about Light. Its version is in the
path; a `v2` would be written beside `v1` for a release.

### 4.5 Snapshot contract (`corral-light.module/1`)

```json
{
  "schema": "corral-light.module/1",
  "ok": true,
  "generated_at": "2026-10-07T18:00:00Z",
  "error": null,
  "progress": {"phase": "backfill", "done_pct": 42, "note": "reading history"},
  "view": [
    {"type": "tiles", "items": [
      {"label": "Committed", "value": "$120 / mo", "kind": "declared",
       "note": "from your finops config", "fresh_at": "..."},
      {"label": "Codex weekly", "value": "40% used", "kind": "vendor",
       "note": "resets Thu 14:15", "fresh_at": "...", "level": "ok"}]},
    {"type": "meter", "label": "Codex 5 h window", "pct": 0, "kind": "vendor",
     "note": "resets in 3 h 10 m"},
    {"type": "table", "title": "By lane, this month",
     "columns": ["Lane", "Plan", "Tokens", "API-equivalent list cost", "Source"],
     "rows": [["Claude", "Max 5x", "41.2 M", "$311", "transcripts"]]},
    {"type": "note", "text": "Grok: credentials found; usage not reported on disk."},
    {"type": "link", "label": "How these figures are made", "url": "https://..."}
  ]
}
```

Enforced by the core, each with a test:

- Every string is text. The core never parses or sums a value.
- `kind` ∈ `billed`, `vendor`, `declared`, `list`, `estimate`, `unknown`;
  `level` ∈ `ok`, `info`, `warn`, `bad`. Both map to fixed CSS classes;
  anything else becomes `unknown` or `info`. No module string reaches a
  class name or a style.
- `link.url` is parsed: scheme `https`, a host, no userinfo, else it is
  shown as plain text. Links open with `noopener noreferrer`.
- `meter.pct` is a finite number clamped to 0 to 100.
- Bounds: 1 MiB total, 50 blocks, 24 tiles per block, 200 rows, 12
  columns, 200 characters per label, 500 per cell or note, nesting depth
  one. Past a bound the core truncates and says how much it dropped.
- Unknown block types render as one "unsupported block" line.
- The collector's stdout and stderr are read with caps (1 MiB and 64 KiB);
  past the cap the process is killed and the run fails.

Failure handling: a failed run keeps the last good snapshot, stamped with
the error and time. Errors and paths are shown only on authenticated
routes; `/health` carries a count of modules and of failing ones, nothing
else.

### 4.6 Core changes, all of them

| File | Change |
|---|---|
| `modules.py` (new) | manifest and pin checks, staging, generations, add, list, remove, enable, disable, update, rollback; the runner thread; snapshot validation |
| `module_feed.py` (new) | writes `module-feed/v1` from the Manager's panes and the existing login checks |
| `sessions.py` | on a `usage_update` that carries `_meta["_claude/rateLimit"]`, merge that payload into a per-window store on the pane and hand it to the feed at once; merge, never replace, the rest of `usage`, so a rate-limit update cannot erase `cost` and a later plain update cannot erase the window. Write both into `turn_end`. Nothing is computed |
| Claude adapter (pinned in `spike/`) | forward `rate_limit_event` even before the turn's first usage, as a `usage_update` with `used` omitted. Today it is dropped when `lastAssistantTotalUsage` is null, and Phase 0 showed the notice always arrives first, so without this patch Light never sees one. Offered upstream first; until it lands, a small pinned patch applied at install and re-checked by `lanes update`. `sessions.py` must then accept a `usage_update` without `used` |
| `claude_auth.py`, `codex_launcher.py`, `grok_launcher.py` | sanitized login facts for the feed: Claude plan and tier (file on Linux, Keychain on macOS); Codex plan and account fingerprint (from `session_meta.creator_account_id` in the rollouts; `auth.json` is not opened); Grok auth mode. The fingerprint is a salted hash of a stable account id, never of a token, so a token refresh never looks like a new account. Today these files return only expiry and presence |
| `vendor_reports.py` (new) | runs `grok usage` per §4.2 in its own sandbox profile |
| `review_egress.py` | an exact-host mode for fetchers, with the lanes' sign-in deny lists always applied |
| `review_sandbox.py` | factor out the bubblewrap builder so the collector can use an allowlist profile; the reviewer profile is unchanged |
| `hub.py` | start runners after serving; `GET /api/modules`, `GET /api/module/<name>`, `POST /api/module/<name>/refresh` (rate-limited, one queued run); counts on `/health` |
| `test_corral_light.py` | the Live-surface route allowlist gains the `/api/module/` prefix, with a dated reason; this is a deliberate edit, not an incidental one |
| `static/app.js` | `renderModuleView(view)`, a pure function; a palette row per module; a module dialog |
| `doctor.py` | per module: pinned, verified, sandboxed or acknowledged, last run, last error |
| `corral-light` wrapper | `module` verb; enabled-module fall-through after every core verb |
| `modules/index.json` (new) | first-party index |

No change to `corral_core/`. The sibling product is untouched.

### 4.7 Notices (Phase 3)

A notice is a short line a module asks the rail to show while a condition
holds, such as "Claude weekly 92% used". It is information, never a
request: it blocks nothing, answers nothing, and carries no link or
action of its own. The rule from M2 holds: the core renders it as text
through fixed classes.

**Opt-in.** The manifest gains `"notices": true`. A snapshot's notices are
ignored unless the verified manifest says so, and `module add` shows "can
show notices in your rail" beside the reads. `core_api` stays 1: the field
is optional, and a core without Phase 3 drops it, since the validator
copies only fields it knows.

**The field.** A snapshot may carry a top-level list:

```json
"notices": [
  {"id": "quota.claude-3f2a.seven_day", "level": "warn",
   "title": "Claude weekly 82% used", "text": "resets Tue 06:52",
   "expires_at": "2026-10-14T13:52:00Z"}
]
```

Enforced by the core at validation, each with a test:

- `id` matches `[a-z0-9][a-z0-9._-]{0,63}`; a notice without a valid id is
  dropped, and a repeated id keeps its first notice only.
- `level` ∈ `info`, `warn`, `bad`; anything else, `ok` included, becomes
  `info`.
- `title` up to 80 characters, `text` up to 300, both through the same
  text rule as §4.5; an empty title drops the notice.
- `expires_at`, if present, is an ISO time; unparseable means absent.
- At most 5 notices per snapshot are kept, ordered `bad`, `warn`, `info`,
  then by id; the snapshot records how many were dropped.

**Expiry, at read time.** A stored notice is shown only while all of these
hold, so a module cannot keep a notice up by going quiet:

- the module is enabled and its sandbox state is acceptable (§9.1);
- now is before the notice's `expires_at`, if it has one;
- now is before the snapshot's `fresh_at` plus twice the manifest's
  `every_s`, so a failing or stopped collector loses its notices after two
  missed runs (10 minutes for FinOps);
- now is before `fresh_at` plus 24 hours, whatever the module asked.

Removing or disabling a module removes its notices at the next poll. A
failed run keeps the last good snapshot (§4.5), and its notices then age
out by the rule above; the failure itself is shown in the dialog, as now,
not as a notice.

**Delivery.** `modules.notices(now)` returns the live notices of every
enabled module, at most 3 per module and 8 in all, ordered by level then
module name. The hub adds them to `/api/state` as `moduleNotices` on the
full form only, beside `claudeAuth`, and to the poller's omit list.
`/health` and unauthenticated routes never carry them.

**The rail.** Each notice is one quiet card after the review cards and
before the paused list: the module's title and the notice's title as the
heading, its text below, and a level class (`nmod info`, `warn`, `bad`)
from a fixed table. Two buttons: **Open** opens the module's dialog;
**Not now** hides the card until its content changes, keyed in
`localStorage` by module, id, level and title, the same pattern as the
review card's Not now, so a `warn` that turns `bad` comes back. Notices
count as quiet items: they open the rail on a wide screen like the review
cards, never pop the rail on a phone, never count in the hot number, and
never send a phone notification. Past the 8-card cap the rail says how
many more there are and that the dialogs have them.

**What FinOps raises.** Only conditions the operator can act on, from data
already in the snapshot:

| Notice | id | Level | Expires |
|---|---|---|---|
| a quota window that is current and at or over 75% used, or whose status is `allowed_warning` | `quota.<account>.<window>` | `warn`; `bad` at 90% or when `rejected` | the window's reset, or the observation plus the window's length when no reset was sent |
| a source frozen by format drift (§8.2) | `source.<name>.frozen` | `warn` | none; ends when a module update unfreezes the source |

`<account>` is FinOps's account key (`claude:<fingerprint>`, `codex:<fingerprint>`,
`config:<id>`) lowercased with every character outside the id alphabet
mapped to `-`; an id that would pass 64 characters keeps its first 55
and adds `-` and an 8-character hash of the whole.

Stale and reset windows raise nothing, since the vendor has said nothing
new. Thresholds are the tile levels FinOps already uses, so a card and its
tile always agree. `[notices] enabled = false` in the module's config
turns them all off.

## 5. Installing and running a module

### 5.1 The operator's experience

```
corral-light module add finops
```

prints the source URL, the commit, the tree digest, what the manifest
reads, that its collector makes no network calls, the exact hosts any
opt-in fetcher may reach, and whether this host can sandbox
it. The operator types the module name to confirm (and, on an unsandboxed
host, types `unsandboxed`). Then:

```
corral-light finops setup
```

discovers accounts and asks about each one (§6.3). The FinOps row appears
in the ⌘K palette. Removing it is `corral-light module remove finops`;
`--purge` also deletes its config and data.

### 5.2 Running

Each run: copy the active generation into a private run tree and verify
the copy (§5.3), build the sandbox (or the acknowledged unsandboxed
spawn) with the copy bound read-only at `/module`, pass the allowlisted
environment, run with a wall budget, read capped output in one
deadline-driven loop, validate, cache, delete the copy. Resource limits
are computed before the fork; the child only calls `setrlimit`.
On an unsandboxed host a process that leaves the run's process group can
outlive a timeout; `module add` says so there. The environment
is built from nothing: `PATH`, `HOME`, `LANG`, `TZ`, plus
`CORRAL_MODULE_API`, `CORRAL_MODULE_CONFIG`, `CORRAL_MODULE_DATA`,
`CORRAL_MODULE_FEED`, and one variable per resolved `reads` entry (for
example `CORRAL_READ_CODEX_SESSIONS`, set to Light's resolved
`CORRAL_CODEX_HOME`, not the ambient one). Nothing from the hub's own
environment crosses.

### 5.3 Pin and verification

- Clone with hooks disabled, no submodules, an empty global git config.
- Install only a commit whose working tree is clean and whose `HEAD`
  equals the pinned commit.
- Refuse any symlink in the tree, any file outside the git index, and any
  `.pth` or any file whose stem is `sitecustomize` or `usercustomize`
  (source or compiled) anywhere in it.
- Digest: SHA-256 over the sorted list of tracked paths, their modes and
  their contents. Install removes `.git`; a `.git` found later is refused.
- Verify before every execution path: collector, CLI, doctor, on the
  private copy that then runs, so a file swapped in the generation after
  the check is not what runs. A mismatch
  disables the module, keeps its last snapshot marked "disabled: changed
  on disk", and says so in doctor.
- Update, remove and run take one lock per module, so none overlaps; an
  interactive CLI or doctor run holds it until its process exits. Update
  and rollback re-read the pin under that lock and refuse if it moved.
- Every read-modify-write of `modules.json` holds an interprocess lock
  file, so the hub and a CLI never lose each other's writes.

## 6. The FinOps module (`corral-light-finops`)

### 6.1 Shape

Stdlib Python 3.9+. Its own repository and CI.

| File | Job |
|---|---|
| `module.json` | manifest |
| `discover.py` | turns the feed and the declared read paths into account candidates |
| `sources/claude.py`, `sources/codex.py`, `sources/grok.py`, `sources/light.py` | one reader each; each returns facts plus its own source rank (§2.1), freshness, parse rate and error |
| `fetchers/anthropic.py`, `fetchers/openai.py`, `fetchers/xai.py`, `fetchers/gcp_billing.py` | opt-in vendor billing API readers (§6.7), run by the separate `fetcher` entry |
| `ledger.py` | SQLite in the data dir: an event index, daily facts, per-file cursors, all written in one transaction per batch |
| `prices.toml` | effective-dated list prices per raw model id and pricing dimension (input, output, cache read, cache write, long-context tier), with a source URL per row, in integer micro-dollars per token |
| `plans.toml` | known plans and list prices, effective-dated, offered as defaults only |
| `collector.py` | time-boxed incremental scan, then the snapshot |
| `cli.py` | `setup`, `doctor`, `show`, `accounts`, `--json` |

### 6.2 Discovery

From the feed's `logins.json`: which lanes have credentials, the Claude
plan and tier, the Codex account fingerprint and plan. From the declared
read paths: which usage stores exist and how much history they hold.

Each finding is a **source**. Accounts are the operator's: `setup`
proposes one account per distinct fingerprint and lets the operator merge
or split. Two Codex homes with the same fingerprint are proposed as one
account. A home that changes fingerprint mid-month is shown as a switch
with its date, never silently merged.

### 6.3 Configuration

`~/.config/corral-light/modules/finops.toml`, in the tomlmini subset:

```toml
timezone = "local"

[[account]]
id = "claude-main"
vendor = "claude"
kind = "subscription"
plan = "max-5x"
usd_cents_month = 10000
renews_day = 14
sources = "claude-projects"

[[account]]
id = "codex-light"
vendor = "codex"
kind = "subscription"
plan = "plus"
usd_cents_month = 2000
sources = "codex-sessions:3f9a1c"
```

**Setup is automatic; the operator only confirms.** The module needs no
setup to start: on its first run it builds **proposed accounts** from the
feed and the vendor's own plan reports, and the dialog shows them at once
with their usage and quota. A proposed account carries:

- the vendor and the source it was found in;
- the plan as the vendor states it (Claude `subscriptionType` and
  `rateLimitTier`; Codex `plan_type`; Grok and Gemini: unknown, since
  neither reports a plan locally);
- a price from `plans.toml` for that plan, labelled "list price as of
  <date>, not confirmed".

`corral-light finops setup` (or one button in the dialog) lists the
proposals. **Accepting a proposal accepts the account, never its price.**
A price becomes `declared` only when the operator types the amount; the
catalogue figure is shown beside the prompt as a hint and is never
written as the operator's. `setup --yes`, for scripted installs, accepts
accounts and leaves every price at its `list` hint. A plan the vendor
does not state, or states ambiguously, gets no price at all, not a guess.
The config records, per account, where each field came from (`vendor`,
`catalogue`, `operator`) and when.

A plan change the vendor reports later (Codex `plan_type` moves from
`plus` to `pro`) shows as "plan changed, price not confirmed", and that
line reverts to `list` until the operator types the new amount. A login
whose fingerprint changes mid-month is a new account from that day; the
old account keeps its earlier records. Setup is re-runnable and never
deletes an account the operator wrote.

### 6.4 What the dialog shows

| Tile | Figure | Kind |
|---|---|---|
| **Committed** | subscription prices per month; the tile shows the typed total and, separately, "plus $N in unconfirmed list prices" | declared, list shown apart |
| **Quota** | each vendor-reported window: Codex 5 h and weekly with used percent; Claude, every window type it reported, with status and reset time, and `utilization` only as the vendor sent it (no rescaling until Phase 0 establishes its scale). A window with no percent says "no percent reported", never 0% | vendor |
| **Vendor-computed cost** | Grok's own computed cost for turns dated this month, labelled "computed by the Grok CLI"; a fork's inherited turns are not counted again | vendor |
| **API-equivalent list cost** | Claude and Codex usage priced at API list, per account, beside the plan's price, never added to it | list |
| **Billed** | pay-per-call spend from vendor billing APIs, only when an opt-in fetcher account exists (§6.7) | billed |

Then: a per-lane table for the month; a **Sources** list saying, per
lane, "reported", "credentials found, usage not reported on disk", or
"local, provider charge not measured"; and **Most used this week**: panes,
consult panels and worktrees ranked by API-equivalent list cost, from the
feed joined to usage by session id. A "this host only" note sits under the
tiles. While the first backfill runs, the dialog shows its progress.

### 6.5 Reading incrementally

- **Budget.** Each run works for at most `budget_s` (30 s), then commits
  and writes a snapshot with `progress`. The first backfill spans as many
  runs as it needs, newest files first, so the current month fills in
  first.
- **Cursors.** Per file: device, inode, size, offset of the last complete
  line. Reads stop at the last newline; a partial last line, including a
  split multi-byte character, waits for the next run. A shrunk file or a
  new inode is re-read from the start. Lines over 4 MiB are skipped and
  counted.
- **Claude dedupe.** An event index keyed by `requestId` holds, per
  request, the day, model, token counts and which file supplied them.
  When the same `requestId` appears again (another home, a re-read file,
  a streamed revision), the record with the largest output token count
  wins and the daily facts are adjusted in the same transaction. Phase 0
  confirmed it: a request writes one record per content block, all with
  the same usage except a growing output count, and the last is largest. `<synthetic>`
  model lines are skipped. Lines with no `requestId` are counted apart and
  priced, but marked "not deduplicated".
- **Codex records.** Where a rollout has `token_usage_record` lines,
  each is a fact keyed by `response_id`; a repeat is ignored, so no
  delta arithmetic is needed. The delta rules below apply only to
  rollouts without them.
- **Codex deltas (older rollouts).** Per session, the last cumulative total. A new event's
  delta is the difference. A negative difference means the counter reset:
  that event's own total starts a new baseline. A session first seen
  mid-history takes its first total as a baseline, marked "history before
  this point not read". Duplicate or out-of-order events (same or lower
  timestamp and total) are ignored.
- **Grok.** The module reads the core's `grok-usage` reports from the
  feed. It records per-turn facts keyed by session and turn, dated by
  the turn's own timestamp, and dollars as integer ticks, never floats.
  Session totals are never summed. A forked session's report repeats its
  parent's turns; the feed carries the fork's `forked_at`, and the module
  counts only turns that ended after it. A resumed session (same id)
  repeats nothing. Phase 0 measured both. No turns are matched across
  sessions by value: two real turns can match.
- **Gemini.** Usage per call from `steps.metadata` field 9, keyed by
  (`cascade_id`, step index); model id from `gen_metadata` or the
  step's alias. A canary (output equals thinking plus visible) runs on
  every batch; a failure freezes the source as "format changed".
- **Quota scale.** Claude `utilization` is a fraction and Codex
  `used_percent` a percent; both are stored as integer basis points.
  Claude's `unifiedWindows` is read when present, else the one
  `rateLimitType` window. Reset times are epoch seconds after
  normalisation; a value over 10¹¹ is milliseconds.
- **Quota freshness.** One rule for every vendor, on normalized times:
  - a window whose reset time is past **now** shows "reset since last
    report" and no status;
  - an observation older than the window's own length (5 h for a 5 h
    window, 7 days for a weekly one) shows "stale" even if its reset is
    in the future;
  - an observation with no reset time is stale after the shortest window
    of its vendor;
  - a `rejected` status is never shown as current without a fresh
    observation;
  - a field listed in `carried` is as old as its own time there, not the
    observation's `observed_at`.
  Quota belongs to an account, so a fingerprint change starts the new
  account's quota empty.
- **Codex quota.** The newest `token_count.rate_limits` across the Codex
  homes of one account, newest by the event's own timestamp, not by file
  time.
- **Codex ordering.** Within a session, events are sorted by timestamp
  and sequence before deltas are taken, so the result does not depend on
  the order files are read. Exact duplicates are dropped; a lower
  cumulative total after sorting is a reset.
- **Pricing.** Raw model ids are kept. Normalization to a price row is a
  lookup, not a rewrite, so a long-context or dated variant can carry its
  own price. An unpriced model's tokens are counted under "unpriced",
  never at $0.
- **Format drift.** Each source tracks its parse rate per file. If it
  falls below 90% on new lines, that source freezes its last facts, shows
  "format changed" in the dialog and doctor, and stops adding to
  "unpriced".
- **Replay invariance.** All derived figures come from the event index
  and prices at read time, so the same history read in any order, with any
  duplicates and any crash between batches, yields the same totals.
- **Ledger schema.** The ledger records its schema version. A module
  update that changes the schema copies the ledger aside, migrates in one
  transaction, and `module rollback` restores the copy with the previous
  generation. A ledger newer than the running code is never opened for
  writing; the module rebuilds from sources instead.

### 6.6 Privacy

- No prompt or answer text is kept beyond the line being parsed.
- The ledger holds counts, model ids, session ids and timestamps.
- Titles come from the feed and live only in the current snapshot.
- The module never reads a credential file; the feed carries the plan.
- On Linux, the sandbox makes the rest of this enforceable rather than
  promised.

### 6.7 Vendor billing APIs (opt-in, pay-per-call accounts only)

Every vendor has an official billing or usage API for API-key accounts.
None covers a consumer subscription, and each needs a key the operator
creates once by hand. The module supports them as opt-in `api` accounts:

| Vendor | API | Key | Limits |
|---|---|---|---|
| Anthropic | Usage and Cost Admin API (`/v1/organizations/usage_report/messages`, `/v1/organizations/cost_report`) | Admin API key | organization accounts only; individual accounts cannot create one |
| OpenAI | Usage and Costs API (`/v1/organization/costs`) | Admin key, restricted to read on Usage | organization owner or admin |
| xAI | Management API (`management-api.x.ai`): usage and prepaid credit | Management key | team accounts |
| Google | Cloud Billing export to BigQuery | a service account with read on the export dataset | needs a Google Cloud project with billing export enabled; heaviest setup |

Rules:

- **Keys.** `setup` explains, per vendor, where the key is created, the
  least scope the vendor offers, and says plainly when the vendor has no
  read-only scope. Keys live only in `~/.config/corral-light/keys/`, one
  regular file each, mode 0600, owned by the operator, no symlinks
  (realpath must stay in that directory). The config names a file there,
  never a path elsewhere.
- **Network.** Exact host names only, from a fixed per-vendor list in the
  core, never a suffix match and never chosen by the module: for example
  `api.anthropic.com`, `api.openai.com`, `management-api.x.ai`,
  `oauth2.googleapis.com` and `bigquery.googleapis.com`. The lanes'
  sign-in hosts are always denied. Install shows the host list, and an
  update that changes it asks again.
- **Sandbox.** A fetcher gets its one key file read-only, egress to its
  hosts, a writable dir of its own, and none of the usage `reads` or the
  collector's data. It returns results to the core as one bounded JSON
  document; the core stores them in the feed for the collector. The core
  refuses a fetcher result or a snapshot that contains the key's bytes,
  and strips authorization headers and request URLs from errors.
- **Completeness.** A fetch pages to the end before anything is stored.
  A re-reported day replaces the earlier figure only when the new fetch
  for that day is complete. Retries back off on 429 and 5xx, at most
  hourly per vendor.
- **Scope.** Billing APIs report an organization, not a person. Each is
  its own `api` account with its own tiles, labelled with the org or
  project it covers; it is never joined to a personal subscription
  account or summed with one.
- Billing figures are kind `billed`, with currency as reported.

### 6.8 An alternative not adopted: vendor telemetry streams

Claude Code, Codex and Grok can each export usage metrics over
OpenTelemetry (Grok's is documented as content-free by default and off
unless switched on). Light could switch it on for the panes it launches
and the module could receive it. Not adopted for v1: it needs a
long-lived receiver (against M1), covers only panes Light launched, and
changes each vendor CLI's configuration. It is listed for the panel.

## 7. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** | **Done 2026-10-07**: `docs/finops-phase0.md`. Not measured, with reasons there: macOS Keychain plan fields, Codex `rate_limits` against the CLI's status view at the same minute (kept as a §8.4 live check), Codex fork | a results doc; this plan updated where an answer changes it |
| **1. Seam** | §4 and §5: modules, feed (with account-scoped quota and login facts), the rate-limit merge in `sessions.py`, the Claude adapter patch (offered upstream), the collector sandbox profile, core-run vendor reports, runner, routes, renderer, dialog, doctor, wrapper, index; tested with fixture modules in the test tree only | the seam tests in §8.1 pass; Light's suite passes with zero modules and with each fixture |
| | **Built 2026-10-07** on branch `modules-seam`: §4 and §5 as specified, except: the exact-host fetcher proxy moves to Phase 4 with the fetchers; Python 3.9 is checked by its grammar only (no 3.9 interpreter on the build host; CI runs 3.14); the Claude adapter patch is applied by `install.sh` and `lanes update`, not yet to a live install. Real-browser check and screenshots (synthetic fixture data) in `docs/img/module-dialog-*.png`. Round-three review: all eleven findings applied with a failing-first test each (`reviews/2026-10-07-finops-module-panel/synthesis-r3-seam.md`) | |
| **2. FinOps v1** | automatic proposed accounts and one-step setup; Claude, Codex, Grok and Gemini sources; ledger, prices and plans; Committed, Quota, Vendor-computed cost and API-equivalent list cost tiles; per-lane table, Sources, Most used this week; CLI | installed here with one command, accounts accepted in one step, prices typed once; §8.4 checks pass |
| | **Built 2026-10-07** in `cvp1/corral-light-finops` (local commits, not yet pushed): every Phase 2 deliverable above, tested by the §8.2 list except the fetcher items (Phase 4). Installed and run through this seam in a throwaway state on the build host, sandboxed: collector, `setup --yes`, `show` and `doctor` all pass. Differences from this plan: the config is `<config>/modules/finops/config.toml`, the folder Phase 1 built, not `finops.toml`; list prices are integer micro-dollars per **million** tokens, since cache-read rates are fractions of a micro-dollar per token; the code is one `finops/` package (sources under `finops/sources/`, discovery in `report.py`); Antigravity databases are copied (database and WAL) into the data dir to be read, because SQLite cannot open a WAL database on a read-only bind; Claude usage belongs to the login fingerprint Light reports, from the moment the module first saw it, since transcripts name no account. Measured: a 500 MB synthetic history backfills over budgeted runs at 35 MB peak memory; a full cold scan of the build host takes 1.3 s. | |
| | **Live 2026-10-07**: pushed to `cvp1/corral-light-finops` (CI green on Python 3.9, 3.12 and 3.14) and installed on the live hub with `module add finops`, sandboxed, every 300 s. §8.4 passed: Codex quota matched the CLI's status view at the same minute; Claude's five-hour figure matched `/usage`, and its weekly figure showed the vendor's last notice (11%) while `/usage` said 12%, because a pane gets a new notice only at the start of a turn; Grok vendor cost matched `grok usage` by hand for three sessions to the tick, with all 33 sessions reporting; a new pane's usage reached the ledger within one run. Two core bugs found on the way, both fixed on master: the hub's PATH resolved `grok` to a version manager's shim, and Light's own test hubs read the live module pins and disabled the live module. Not run on this host: the uninstall check, which Light's CI covers by running with no module installed | |
| **3. Notices** | §4.7: the snapshot's `notices` field, validated and bounded by the core; expiry tied to the module's own freshness; quiet rail cards with Open and Not now; FinOps quota and source-frozen notices | the §8.1 notice tests and the §8.2 notice tests pass; on this host, a forced near-limit window shows one rail card that clears when the window resets, the module is disabled, or the collector stops reporting |
| **4. Billing APIs and macOS** | opt-in fetchers for the four vendor billing APIs (§6.7); a macOS sandbox investigation | each fetcher tested against a local stub; the operator decides which to enable |

Dropped: any use of the pane's Claude login to read quota. All three
reviewers said no.

## 8. Testing plan

### 8.1 Seam (Light's repository, CI on every commit)

`test_modules.py`, fixture modules under `testkit/modules/`:

- **Isolation (Linux, fails rather than skips where bubblewrap exists).** A
  fixture collector tries to open `session.key`, `pairing.json`, a pane's
  `events.jsonl`, `~/.ssh`, the modules pin file; to write outside its data
  dir; to connect to the hub port and to an outside address; to read the
  hub's environment through `/proc`. Every attempt fails, and the run still
  succeeds with what it may read.
- **Unsandboxed host.** With bubblewrap unavailable, the module does not
  run until acknowledged, and its tile says "unsandboxed".
- **Manifest.** Bad name, core-verb name, unknown key, `..` path, symlinked
  script, a `-c` or `-m` attempt, unknown `reads`, network not `none`,
  unsupported `core_api`. Each is refused with the reason; nothing written.
- **Install and pin.** From a local path and a `file://` git repo; a dirty
  tree, a stray untracked file, a symlink, a `.pth` file, and a git hook
  are each refused; the digest ignores `.git`.
- **Tamper on every path.** A changed byte in a non-entry source file is
  caught before collector, CLI and doctor runs alike.
- **Races.** Update and remove while a run is in flight; an interrupted
  update at each stage leaves the previous generation active; rollback
  restores it.
- **Runner.** Non-zero exit, non-JSON, oversized stdout, a stderr flood, a
  sleep past the timeout, a child that starts a new session to escape the
  group. Each keeps the last good snapshot with the error, and the thread
  survives.
- **Environment.** The collector's environment is exactly the documented
  set; a hub started with a secret in its environment does not pass it.
- **Routes.** Module routes need the cookie; unknown or disabled names are
  404; traversal in a name is refused; `/health` carries counts only.
- **Feed.** Written atomically; carries usage from `turn_end` events; a
  sentinel prompt string and a sentinel token never appear in it.
- **Quota capture, in the adapter's real shapes.** Using recorded
  adapter output, not hand-made updates: rate-limit then result keeps
  `cost` and the window; result then rate-limit keeps both; a rate-limit
  event before the turn's first usage reaches the feed once the adapter
  patch is in, and the test fails without it; `seven_day_opus` and the
  sibling overage fields survive; a missing `rateLimitType` is kept under
  its own key; a missing `resetsAt` is stale by the rule, not fresh;
  `utilization` is passed through unscaled; a hub restart mid-turn keeps
  the last observation.
- **Vendor report sandbox.** With the real Grok layout (binary symlinked
  into the folder holding `auth.json`), `auth.json` cannot be opened from
  inside the `grok usage` sandbox; a stand-in binary that tries to read
  `../auth.json`, other session dirs, the network, or to run past 10 s or
  past its memory limit fails or is killed, and Claude and Codex
  collection that run is unaffected. The collector's sandbox contains no
  Grok binary at all.
- **Login facts.** A token refresh leaves the Codex and Claude
  fingerprints unchanged; a different login changes them; no token byte
  appears in the feed.
- **Exact hosts.** The fetcher proxy refuses `evil.api.anthropic.com`
  while allowing `api.anthropic.com`, and refuses every lane sign-in host
  even if a manifest names it.
- **Parsing on every supported Python.** The fixture `module.json` and a
  full `finops.toml` example load under the core's readers on 3.9 and on
  the newest Python in CI.

`selftest_modules.mjs`, plus a real-browser check:

- Every block type renders from a fixture; over-bound views truncate with
  a count; unknown blocks show one line.
- Text with `<script>`, `<img onerror>` and `javascript:` renders as text.
- In a real browser, every rendered link's `href` property is `https:`
  with a host; `javascript:`, `https:alert(1)`, `http:` and userinfo URLs
  never become links. Unknown `kind` and `level` map to fixed classes.
- Screenshots at 400 px and 1600 px wide, synthetic data only.

Notices (Phase 3, §4.7), in `test_modules.py` and `selftest_inbox.mjs`:

- **Opt-in.** A snapshot with notices from a manifest without
  `"notices": true` yields none; the same snapshot with it yields them.
- **Validation.** Invalid and repeated ids, unknown levels, an empty
  title, over-long text, a bad `expires_at`, a non-list field and a
  non-object entry each behave as §4.7 says; six notices keep five, in
  level order, with the drop counted.
- **Expiry.** With a fake clock: past `expires_at` hides a notice; two
  missed `every_s` periods hide every notice of that module even with no
  `expires_at` and a far `expires_at`; the 24-hour cap holds; a failed
  run does not refresh `fresh_at`, so its old notices still age out.
- **Lifecycle.** Disable, remove and `remove --purge` each clear the
  module's notices on the next `/api/state`; an unacknowledged
  unsandboxed module shows none.
- **Caps.** Four modules with five notices each give 3 per module and 8
  in all, ordered by level then module name.
- **Exposure.** `moduleNotices` appears only on the full `/api/state`;
  the light poll, `/health` and unauthenticated routes never carry it.
- **Rail.** A notice card has no permission buttons, counts as quiet and
  not in the hot number, does not pop the rail on a phone, and its Not
  now holds until the level or title changes. Title and text with
  `<script>` and `<img onerror>` render as text.
- **Old core.** A notices-bearing snapshot through the Phase 2 validator
  loses the field and nothing else.

### 8.2 FinOps module (its own repository and CI)

All fixtures are synthetic.

- **Discovery:** no vendors; each vendor alone; Linux with pane transcript
  trees; macOS layout with none; two Codex homes, same fingerprint (one
  account proposed) and different (two); a mid-month fingerprint switch.
- **Claude:** one `requestId` across two trees counted once; a streamed
  revision replaces its earlier record; missing `requestId` counted apart;
  `<synthetic>` skipped; unknown model to "unpriced".
- **Codex:** cumulative to delta; a reset gives a new baseline, never a
  negative; out-of-order and duplicate events ignored; a session first
  seen mid-history marked; `rate_limits` absent; a past `resets_at` marks
  the quota stale.
- **Cursors:** append, truncate, rotate, delete; a partial line and a
  split UTF-8 character at end of file are read once, complete, next run.
- **Replay invariance:** the same history delivered in shuffled order,
  with duplicates, and with a crash injected between every batch, gives
  identical totals and markers.
- **Budget:** a synthetic 500 MB history completes over several 30 s runs,
  current month first, with progress shown each run; an incremental run
  with 1 MB of new lines takes under 2 s; peak memory under 200 MB.
- **Time:** month boundaries in the configured zone, a DST zone, the last
  night of a month.
- **Grok:** recorded `grok usage` reports become per-turn facts dated by
  turn; ticks stay integers end to end; two independent turns with the
  same timestamp, tokens and ticks are both counted; a fork's turns at or
  before `forked_at` are not counted, a resumed session's are counted
  once; a turn from last
  month in this month's session lands in last month.
- **Metrics stay apart:** a Grok vendor cost and a list estimate of the
  same turns are shown side by side and neither changes the other; a
  billed org figure never enters a subscription tile.
- **Quota freshness, on normalized times:** reset in the past shows
  "reset since last report"; reset in the future but observation older
  than the window shows "stale"; no reset at all goes stale after the
  shortest window; Claude milliseconds and Codex seconds compare
  correctly; a stale `rejected` is not shown as current; a fingerprint
  change starts the new account empty.
- **Automatic setup:** with no config, the first run proposes accounts;
  `setup --yes` writes accounts with **no declared price**, and Committed
  shows the catalogue figures only as "unconfirmed list prices"; an
  unknown plan gets no price; a typed amount becomes `declared` with its
  provenance; a later vendor plan change reverts that line to `list`.
- **Codex ordering:** the same events in shuffled file order give the
  same deltas.
- **Ledger schema:** a migration interrupted mid-way leaves the old
  ledger usable; rollback restores it.
- **Fetchers:** each billing API against a local stub: success, pagination
  to the end, a partial response that must not replace a complete day,
  401, 429 with back-off, timeout; a key file that is a symlink, group
  readable, or outside the key directory is refused; the key's bytes in a
  stub response are caught by the core and the result refused; the
  fetcher cannot read usage paths or collector data.
- **Prices:** an effective-dated change re-prices only later days; a long
  context variant uses its own row; a missing price never yields 0.
- **Format drift:** a new line shape below the parse-rate threshold
  freezes that source and says why.
- **Null-not-zero:** each source broken in turn; affected tiles say
  "unreported", others are unchanged.
- **Privacy:** sentinel strings placed in transcript message bodies and in
  the feed's title field never reach the ledger; message-body sentinels
  never reach the snapshot, CLI output or stderr.
- **Contract:** every snapshot validates with the core's validator,
  vendored and pinned to the core version.
- **Notices (Phase 3):** a current window at 74%, 75%, 89% and 90% gives
  none, `warn`, `warn` and `bad`; `allowed_warning` below 75% gives
  `warn`; `rejected` gives `bad`; stale and reset windows give none, a
  stale `rejected` included; each notice's level equals its tile's level;
  `expires_at` is the reset when sent, else observation plus window
  length; a frozen source gives one `warn` that goes when a module update unfreezes it; a key with `:` or an over-long key gives a valid, stable id; ids
  are stable across runs and contain no path, title or account id beyond
  the fingerprint key; `[notices] enabled = false` gives none; message-body
  sentinels never reach a notice.

### 8.3 Integration (Light's repository, CI)

A private hub on a temp state dir, a fixture FinOps-shaped module from a
temp git repo, installed through `module add`: the dialog opens from the
palette, renders, shows backfill progress, survives a module failure, and
`module remove --purge` leaves nothing behind.

### 8.4 Live checks on this host (manual, before calling v1 done)

- Codex quota figures match the Codex CLI's status view at the same
  minute; Claude quota status matches Claude Code's own status line.
- Grok vendor cost for three of Light's Grok panes matches `grok usage`
  run by hand.
- For one finished Claude pane, the module's list cost and the pane's
  last `usage_update.cost` are compared and the difference is reported.
  This is a diagnostic until Phase 0 establishes what that cost covers.
- A new pane's usage appears within one collector period, or at once
  after "Refresh now".
- Uninstall leaves Light's suite green.

## 9. Decisions (were open questions in rev 4)

Decided by the author in rev 5 under the operator's standing direction to
make design calls rather than return them. Each can be reversed before
Phase 1 without rework.

1. **Unsandboxed hosts: run after a typed acknowledgement.** On macOS
   and on Linux without bubblewrap, install shows "this module will run
   unsandboxed as your user and can read anything you can" and runs it
   only after the operator types `unsandboxed`; the pin records it and
   the tile keeps an "unsandboxed" face. Refusing would leave Mac users
   with nothing, and FinOps declares no network. Phase 4 investigates a
   macOS sandbox; when one exists, the acknowledgement is no longer
   offered for modules that fit it.
2. **Module repository: public, beside Corral Light, same owner**, named
   `corral-light-finops`, so the first-party index can point to it.
   Creating it is an outward action and waits for the operator.
3. **Grok: run the vendor's binary, not read `usage.json`.** It is the
   interface the vendor documents. Phase 0 showed it needs only two
   files per session, read-only, with no login and no network.
