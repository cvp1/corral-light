# Corral Light modules, and FinOps as the first one

> Status: PROPOSED 2026-10-07, rev 1. Nothing built. For panel review
> (Grok, Codex, Gemini), then the operator's decision.

## 0. The ask

The operator, 2026-10-07: make FinOps the first feature to port from full
Corral into Corral Light, as a module. It must not be part of Light's code
base. Someone who installs Corral Light and wants FinOps should be able to
install it, point it at the accounts they use, and have it work out on its
own how to read Claude Code, Codex, Gemini and the other lanes. Spec it,
plan the build and the tests, and send it to the panel.

Two deliverables follow from that, and they are separable:

1. **A module seam in Light.** Light has no extension mechanism today.
   FinOps is the first module, so it defines the seam. The seam must be
   small, safe, and useful to the second module too.
2. **The FinOps module itself**, in its own repository.

## 1. What full Corral has, and what carries over

Full Corral's FinOps is a room that renders, verbatim, the output of a
separate engine in the operator's private workspace. The engine joins:

- per-provider LLM spend rolled up into InfluxDB by scheduled jobs
  (scheduled-run cost, interactive Claude Code transcripts priced at list,
  per-call ledgers for pay-per-call APIs),
- a human-owned subscription rate table,
- account balances where a provider's API reports one,
- cloud resources (Lightsail, GCP, Hetzner) with list prices, budgets and
  purposes, into a five-signal strip: committed, billed, ceiling remaining,
  allocated, utilization.

The rules it was built under, all of which carry over:

- **Null is unreported, never zero.** No data must not render as good data.
- **Every figure says what kind it is:** billed, list (notional), declared,
  estimate, or unknown.
- **The viewer computes nothing.** Light's core renders what the module
  reports.
- **Each source degrades alone.** A broken source blanks its own tile, not
  the room.
- **Reads are cached server-side on their own clock.** A browser poll
  never becomes a vendor or disk scan.

What does not carry over: InfluxDB, Grafana, the goals evaluator, the cloud
inventory, the electricity estimate, and anything that imports the private
workspace. Light runs on a stranger's laptop with only their own vendor
logins. The cloud-cost half could become a later provider plugin of this
module; it is out of scope here.

So Light's FinOps answers a narrower, more personal set of questions:

1. What am I paying per month for these lanes? (declared subscriptions)
2. How much of each subscription have I used, and how much is left before
   I hit a limit? (vendor-reported quota windows, where the vendor reports
   them)
3. What would my usage have cost at API list price? (notional value, to
   judge whether a plan is worth it)
4. What did a given pane, panel review, role or worktree cost? (attribution,
   which only Light can do because it knows the panes)
5. What am I spending on pay-per-call keys, if I use any? (balances and
   month-to-date, where the vendor's API reports them)

## 2. What the machine actually exposes (surveyed 2026-10-07)

Measured on one Linux host running Light with all four vendor lanes logged
in. Phase 0 re-measures each on macOS and on a second account.

| Lane | Login and plan | Usage records | Quota or headroom | Light already sees |
|---|---|---|---|---|
| Claude Code | credentials file `claudeAiOauth.subscriptionType` (e.g. `max`) and `rateLimitTier` (e.g. `default_claude_max_5x`); Light links each pane's credential to the shared file | per-session JSONL transcripts under `~/.claude/projects/` AND under every pane's private config dir (`<state>/panes/<id>/config/projects/`); each assistant line has model, token usage and a `requestId` | not on disk; an OAuth usage endpoint exists (network, uses the login token) | ACP `usage_update` with `used`, `size`, and `cost {amount, currency}`; the cost rises monotonically within a session (observed 0.60, 1.86, 1.99) |
| Codex | `auth.json` in `CODEX_HOME` (Light uses its own, `~/.config/corral-light/codex-home`, not `~/.codex`) | rollout JSONL under `CODEX_HOME/sessions/YYYY/MM/DD/`; `token_count` events with cumulative and last-turn token usage | the same `token_count` events carry `rate_limits`: `plan_type`, `primary` (5 h window) and `secondary` (weekly) `used_percent` with `resets_at`, and `credits` | `usage_update` with `used` and `size` only, no cost |
| Grok | `~/.grok/auth.json` (auth mode, team id) | session dirs under `~/.grok/sessions/`; no token or cost fields found in `chat_history.jsonl` or `summary.json` | none found | no `usage_update` observed |
| Gemini (Antigravity) | `~/.gemini/antigravity-acp/acp_token.json` | per-conversation SQLite under `~/.gemini/antigravity-acp/conversations/`; schema not yet read | unknown | none; but Light owns this adapter (`antigravity_acp.py`) and could emit `usage_update` itself |
| Ollama | none | none needed | local, no money | none |

Consequences for the design:

- **Claude and Codex are well covered from local files with no network.**
  Codex is the best case: the vendor itself reports quota used and reset
  time on every turn.
- **Grok and Gemini start as "login detected, usage unreported".** That is
  an honest tile, not a gap to paper over with guesses.
- **Light's own state dir matters.** A scanner that only reads
  `~/.claude/projects` misses every Light pane, and a scanner that reads
  both double-counts unless it dedupes by `requestId`.

## 3. The module seam (in Light's core)

### 3.1 Decisions, with reasons

| # | Decision | Why |
|---|---|---|
| M1 | A module runs **out of process**. The hub runs the module's collector as a subprocess on its own thread, with a timeout, and caches the JSON it prints. The hub never imports module code. | The resilience review's first finding is that the hub is the single point of failure for every live pane. A module's crash, hang, import error or memory blow-up must cost only its own tile. It also keeps the module's dependencies out of the hub, and matches full Corral's subprocess boundary. |
| M2 | A module's UI is **declarative**: the collector returns a `view`, a list of typed blocks (tiles, table, meter, note, link). Light's core renders them with `textContent` only. No module JavaScript runs in the wall. | Module JS in the authenticated page could call any hub route, including answering permission cards. A fixed block vocabulary removes that whole class of risk, is testable as one pure function, and works on the phone layout for free. FinOps needs nothing outside the vocabulary. |
| M3 | Modules are **not in Light's repository.** Each is its own repository, installed by `corral-light module add <name or git URL or path>`. A short name resolves through a small index file in core listing first-party modules. | The operator's requirement. The core suite already fails if `finops.py` reappears in the tree. |
| M4 | Install **pins a commit and a tree digest**, and shows both with the manifest's declared reads before a typed confirm. The hub runs a module only while its on-disk digest matches the pin; a mismatch disables it and says so in doctor and in the module's tile. | A module runs as the operator's user and reads vendor files. Installing one is installing code, so it gets the same visibility as a lane adapter pin. Tamper or a half-finished update must not run silently. |
| M5 | Module updates are **explicit**: `corral-light module update <name>` shows the digest change and log, then repins. `corral-light update` reports upstream drift for modules but never moves their pins. | Light's own update restarts the hub only when panes are idle; a module update needs no restart (next collector run picks it up), so there is no reason to couple them. |
| M6 | A module gets **read-only inputs and one private writable dir.** The hub passes, by environment: Light's state dir (documented read-only), the module's config file, the module's data dir, and the core's module API version. No hub URL, no pane token, no cookie. | Nothing a module does can act on the wall. Read access to Light's state dir is needed for attribution and is documented in the manifest's `reads`. |
| M7 | Modules **can add CLI verbs** under their own name only: `corral-light finops ...` execs the module's declared CLI entry. | A module cannot shadow a core verb. |
| M8 | Modules **can raise a quiet notice** (`notices` in the snapshot): a rail chip, never "Needs you", never a desktop notification in v1. | "Needs you" is for things blocking an agent. A quota at 85% is information, and should not compete with permission cards. |

### 3.2 The manifest (`module.toml` in the module's repository root)

```toml
name = "finops"                    # [a-z][a-z0-9-]{0,31}; must not be a core verb
title = "FinOps"
version = "0.1.0"
core_api = 1                       # the seam version this module speaks
summary = "What your lanes cost, and how much quota is left"

[collector]
argv = ["python3", "collector.py", "snapshot", "--json"]   # relative to the module dir
every_s = 300
timeout_s = 45
max_bytes = 1048576

[cli]
argv = ["python3", "cli.py"]

[doctor]
argv = ["python3", "cli.py", "doctor", "--json"]

[reads]                            # declared, shown at install, checked by the module's own tests
paths = ["~/.claude/projects", "~/.claude/.credentials.json (plan fields only)",
         "$CODEX_HOME/sessions", "$CORRAL_LIGHT_STATE/panes (read only)",
         "~/.grok/auth.json (presence and auth mode only)",
         "~/.gemini/antigravity-acp (presence only, until Phase 0)"]
network = "none by default; opt-in per account in finops.toml"
```

The core reads the manifest with the existing strict TOML reader
(`corral_core/tomlmini.py`). Unknown keys, a bad name, an argv entry that
escapes the module dir, or a `core_api` the core does not speak refuse the
install with the reason.

### 3.3 Where things live

| What | Where |
|---|---|
| Installed module checkout | `<state>/modules/<name>/` (git clone at the pinned commit) |
| Pins and enable flags | `~/.config/corral-light/modules.toml` (`name`, `source`, `commit`, `digest`, `enabled`) |
| Module config (human-owned) | `~/.config/corral-light/modules/<name>.toml` |
| Module data (module-owned) | `<state>/module-data/<name>/` |
| First-party index | `modules/index.toml` in Light's repo: name, title, git URL. The only module-related file in core besides the seam code. |

### 3.4 Snapshot contract (`corral-light.module/1`)

```json
{
  "schema": "corral-light.module/1",
  "ok": true,
  "generated_at": "2026-10-07T18:00:00Z",
  "error": null,
  "view": [
    {"type": "tiles", "items": [
      {"label": "Committed", "value": "$220 / mo", "kind": "declared",
       "note": "sum of the plans in finops.toml", "fresh_at": "..."},
      {"label": "Codex weekly quota", "value": "40% used", "kind": "vendor",
       "note": "resets Thu 14:15", "fresh_at": "...", "level": "ok"}]},
    {"type": "meter", "label": "Codex 5 h window", "pct": 0.0, "kind": "vendor",
     "note": "resets in 3 h 10 m"},
    {"type": "table", "title": "By lane, this month",
     "columns": ["Lane", "Plan", "Used", "Notional at list", "Source"],
     "rows": [["Claude", "Max 5x", "41.2 M tokens", "$311.40", "transcripts"]]},
    {"type": "note", "text": "Grok: logged in; this vendor reports no usage on disk."},
    {"type": "link", "label": "How these figures are made", "url": "https://..."}
  ],
  "notices": [{"id": "codex-weekly-85", "text": "Codex weekly quota 85% used", "level": "warn"}],
  "data": {}
}
```

Rules the core enforces, each with a test:

- Every string is rendered as text. A `value` is a display string the
  module formats; the core never parses or sums it.
- Unknown block types are dropped with a visible "unsupported block" line,
  so a newer module on an older core degrades instead of breaking.
- Limits: 1 MiB, 50 blocks, 200 table rows, 12 columns, 500 characters per
  cell. Past a limit the core truncates and says how much it dropped.
- `link.url` must be `https:`. No other URLs are rendered as links.
- `level` is one of `ok`, `info`, `warn`, `bad`; anything else is `info`.
- `kind` is one of `billed`, `vendor`, `declared`, `list`, `estimate`,
  `unknown`, and is always shown as a small face beside the value.

Failure handling, the house pattern: a failed run (timeout, non-zero exit,
non-JSON, oversize, schema refusal) keeps the last good snapshot and stamps
the error and its time on it. A module that has never succeeded shows its
error. The runner thread never dies; each run is a fresh process killed by
process group on timeout.

### 3.5 Core changes, all of them

| File | Change |
|---|---|
| `modules.py` (new, core) | manifest validation, pin and digest, `add`, `list`, `remove`, `enable`, `disable`, `update`, the runner thread and cache, snapshot validation |
| `hub.py` | start one runner per enabled module after the hub is serving; `GET /api/modules` (names, titles, status, digests); `GET /api/module/<name>` (the cached snapshot); module status on `/health` |
| `static/app.js` | `renderModuleView(view)` as a pure function; one palette row per module; one dialog that renders a module; notice chips on the rail |
| `doctor.py` | one line per installed module: pinned, digest matches, last run, last error |
| `corral-light` wrapper | `module` verb; unknown verb falls through to an installed module's CLI only when the name matches an enabled module |
| `update.py` | report module upstream drift; never repin |
| `modules/index.toml` (new) | first-party index |

No change to `corral_core/`. The sibling product is untouched.

## 4. The FinOps module (`corral-light-finops`)

### 4.1 Shape

Stdlib Python 3.9+, like Light. Its own repository, its own CI. Files:

| File | Job |
|---|---|
| `module.toml` | manifest (above) |
| `discover.py` | finds every account and data source on this machine |
| `sources/claude.py`, `sources/codex.py`, `sources/grok.py`, `sources/gemini.py`, `sources/light.py`, `sources/api_balance.py` | one reader per source; each returns facts plus its own freshness and error |
| `ledger.py` | SQLite in the module data dir: facts only, plus per-file read cursors |
| `prices.toml` | effective-dated list prices per model, with a source URL per row |
| `plans.toml` | known subscription plans and list prices, effective-dated, as suggestions only |
| `collector.py` | incremental scan, then build the snapshot and `view` |
| `cli.py` | `setup`, `doctor`, `show`, `accounts`, `--json` |

### 4.2 Discovery: "it figures out the lanes by itself"

`discover.py` looks in every place a lane's records can be, without network:

- **Claude:** `~/.claude`, `$CLAUDE_CONFIG_DIR` if set, and every
  `<state>/panes/*/config`. It reads `subscriptionType` and `rateLimitTier`
  from the credentials file and nothing else from it.
- **Codex:** `$CODEX_HOME`, `~/.codex`, and Light's
  `CORRAL_CODEX_HOME` default. Plan type comes from the newest
  `token_count.rate_limits.plan_type`, so the module never decodes a token.
- **Grok, Gemini:** presence of the login file and the session store.
- **Light:** `CORRAL_LIGHT_STATE` (passed by the hub), for pane metadata
  and `usage_update` events.

Each finding is an **account candidate**: vendor, where it was found, plan
if the vendor says, which sources can read its usage, and which cannot.
Two homes that are the same login (Light's Claude panes link the shared
credential) collapse into one account; two different Codex homes stay two
accounts.

### 4.3 Configuration: "configure it with the accounts we use"

`corral-light finops setup` runs discovery, prints each candidate, and asks
the operator one question per account: keep it, and what does it cost per
month? It offers the matching `plans.toml` price as the default, labelled
with its effective date. The answer goes to
`~/.config/corral-light/modules/finops.toml`:

```toml
timezone = "local"            # month and day boundaries; an IANA name overrides

[[account]]
id = "claude-main"
vendor = "claude"
kind = "subscription"
plan = "max-5x"
usd_month = 100.00            # declared by the operator
renews_day = 14
homes = ["~/.claude"]         # discovery fills this; Light pane dirs are implied

[[account]]
id = "codex-light"
vendor = "codex"
kind = "subscription"
plan = "plus"
usd_month = 20.00
homes = ["~/.config/corral-light/codex-home"]

[[account]]
id = "openrouter"
vendor = "openrouter"
kind = "api"
balance = { secret_file = "~/.config/keys/openrouter", endpoint = "default" }
network = true                # opt-in: this account may call the vendor
```

Rules:

- **Secrets are never in this file.** An API account names a secret file
  or an environment variable; the module reads it at the moment of the call
  and never logs, stores or prints it.
- **Network is off unless an account says `network = true`.** The default
  install makes no network calls at all. The doctor line says which
  accounts may call out, and where.
- **A discovered source with no account is still shown,** under "Not set
  up", with its usage and no price, so a new lane is visible on day one.
- `setup` is re-runnable and only proposes changes; it never deletes an
  account the operator wrote.

### 4.4 Signals

Five tiles, the same headings as full Corral where they mean the same thing:

| Tile | Figure | Kind | Source |
|---|---|---|---|
| **Committed** | sum of declared subscription prices, per month | declared | `finops.toml` |
| **Headroom** | the tightest vendor quota: e.g. "Codex weekly 40% used, resets Thu" | vendor | Codex `rate_limits`; Claude only with the opt-in usage read (Phase 3) |
| **Notional value** | this month's usage priced at API list, per subscription account, with value ÷ price as "value per $" | list | Claude transcripts, Codex rollouts, priced by `prices.toml` |
| **API spend** | month-to-date and balance for pay-per-call accounts | billed or vendor | the vendor's own balance or usage endpoint, opt-in |
| **Coverage** | share of today's active lanes whose usage is reported; the unreported ones by name | derived | discovery vs sources |

Below the tiles: a per-lane table for the month, the quota meters, and
"Most expensive this week": panes, consult panels and worktrees ranked by
notional value, from Light's pane metadata joined to usage by session id.

Rules carried from full Corral, each tested:

- Unreported is `null` in data and "unreported" on screen. Never 0.
- Notional value is never added to money spent. It is a separate tile with
  its own face.
- An unpriced model's tokens are counted under "unpriced", never at $0.
- A source that failed keeps its last facts with a frozen `fresh_at`;
  absence of a file is never read as zero usage.
- Every tile carries its own freshness.

### 4.5 Reading sources incrementally

Claude transcripts on a heavy user's machine run to gigabytes. The ledger
keeps a cursor per file (device, inode, size, byte offset of the last
complete line). Each run reads only new complete lines; a partial last line
is left for the next run. A file that shrank or changed inode is re-read
from the start, and its facts replace the old ones by `requestId`. Facts
are stored per day per account per model (tokens in, out, cache read,
cache write, request count), plus per session for attribution. Every
derived figure is computed at read time from facts and the price table, so
a price correction re-prices history.

Dedupe:

- Claude: one billing event per `requestId`, across all homes and pane
  config dirs. `<synthetic>` model lines are skipped.
- Codex: the cumulative `total_token_usage` is per session; facts take the
  difference between consecutive `token_count` events, so a resumed
  session does not double-count.
- Light `usage_update` cost (Claude): used only as a cross-check against
  the transcript price for the same session, reported in `doctor` when they
  differ by more than 5%. Its exact semantics across resume and compaction
  are a Phase 0 question.

### 4.6 Privacy and safety

- No prompt or answer text is read into memory beyond the line being
  parsed, and none is stored. The ledger holds counts, model ids, session
  ids and timestamps.
- Pane titles are read for the attribution table and stored only in the
  current snapshot, not in the ledger.
- From credential files the module reads named fields only (plan, tier,
  auth mode, presence). A test seeds fake credentials with sentinel token
  strings and fails if any sentinel appears in the ledger, the snapshot,
  the CLI output or the logs.
- The module writes only inside its data dir.

## 5. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** (no code shipped) | Read the Antigravity conversation schema for token fields; check the Grok CLI for a usage command; record Claude `usage_update.cost` across resume, compaction and `/clear`; time a full and an incremental scan of this host's transcripts; confirm Codex `rate_limits` against the Codex CLI's own status view; repeat on macOS | a short results doc with each answer and what changes in this plan |
| **1. Seam** | `modules.py`, hub routes and runner, renderer, palette row and dialog, doctor, wrapper verb, index file; tested with fixture modules that live in the test tree only | Light's full suite passes with zero modules installed and with every fixture; a hung, crashing, oversized and hostile fixture each leave the wall serving |
| **2. FinOps v1** | discovery, `setup`, Claude and Codex sources, ledger with cursors, prices and plans, Committed, Headroom (Codex), Notional value, Coverage, per-lane table, CLI | installed on this host from its git URL with one command; the figures match the cross-checks in §6.4 |
| **3. Attribution and more vendors** | most-expensive panes, panels and worktrees; Gemini and Grok sources if Phase 0 found data; API balances (opt-in); quota notices; opt-in Claude quota read | a three-lane consult panel shows its own total notional value within a minute of finishing |
| **4. Optional core help** | Light's own Antigravity adapter emits `usage_update` when its backend reports tokens; pane cost pill for Claude from `usage_update` | only if the panel and operator want core changes for this |

## 6. Testing plan

### 6.1 Seam (in Light's repository, runs in CI on every commit)

`test_modules.py`, stdlib `unittest`, fixture modules under
`testkit/modules/`:

- Manifest: a bad name, a core-verb name, unknown keys, an argv that
  escapes the module dir, a `core_api` the core does not speak. Each is
  refused with a message naming the problem; nothing is written.
- Install from a local path and from a `file://` git URL in a temp dir:
  pin and digest recorded; a dirty source tree is refused; `remove` leaves
  no files and no config entry.
- Tamper: change one byte in the installed module; the runner refuses to
  run it, the tile and doctor both say why, other modules keep running.
- Runner: collector exits non-zero, prints non-JSON, prints 2 MiB, sleeps
  past the timeout, forks a child that outlives it, prints a schema the
  core does not know. Each keeps the last good snapshot with the error
  stamped, kills the whole process group, and the thread survives to the
  next run.
- Hub: starts and serves `/api/state` while a module hangs at its first
  run; module routes require the session cookie; `/api/module/<name>`
  for an unknown or disabled name is 404; path traversal in the name is
  refused.
- Environment: the collector's environment has no hub URL, no pane token
  and no cookie; it gets exactly the documented variables.
- The "not necessary" guarantee: the existing suite runs unchanged with no
  modules, and a test asserts no core file imports anything from a module
  directory.

`selftest_modules.mjs`, the house mini-DOM pattern:

- `renderModuleView` renders every block type from a fixture.
- Strings containing `<script>`, `<img onerror>` and `javascript:` render
  as visible text; a non-`https:` link renders as plain text.
- Over-limit views are truncated with a visible count.
- Unknown block types show "unsupported block".
- Phone width (400 px) and desktop (1600 px) screenshots with synthetic
  data only, through the existing screenshot driver.

### 6.2 FinOps module (in its own repository, its own CI)

Fixtures are synthetic files built by the tests, never copies of real
transcripts.

- **Discovery:** no vendors installed; each vendor alone; Claude in both
  `~/.claude` and pane config dirs (one account, not two); two Codex homes
  (two accounts); `CLAUDE_CONFIG_DIR` set; a home that is unreadable
  (reported, not fatal).
- **Claude source:** duplicate `requestId` across homes counted once;
  `<synthetic>` skipped; `[1m]` and date suffixes normalized; an unknown
  model lands in "unpriced"; malformed lines skipped and counted; a
  partial last line left for the next run, then read once complete.
- **Codex source:** consecutive cumulative `token_count` events turned
  into deltas; a resumed session does not double-count; `rate_limits`
  absent; `resets_at` in the past marks the quota stale; plan type read
  from events, not from `auth.json`.
- **Cursors:** append, truncate, rotate (inode change), file deleted.
  Facts stay correct across each.
- **Time:** month and day boundaries in the configured timezone, including
  a DST transition zone and a run across midnight on the last day of a
  month.
- **Prices:** an effective-dated price change re-prices only days after
  the change; a missing price never yields 0.
- **Null-not-zero:** with each source broken in turn, every affected tile
  says "unreported" and every other tile is unchanged.
- **Secrets:** sentinel token strings in every credential fixture never
  appear in the ledger, snapshot, CLI output or stderr.
- **No network by default:** sockets are patched to raise; the full
  collector run with a default config succeeds.
- **Opt-in network:** a local stub server stands in for a balance
  endpoint; timeouts and HTTP errors degrade that account alone.
- **Contract:** every snapshot the collector prints validates against
  `corral-light.module/1`, using the core's own validator vendored as a
  test fixture and pinned to the core version.
- **Performance:** a synthetic 500 MB transcript set; first scan under 60
  seconds, an incremental run with 1 MB of new lines under 2 seconds, peak
  memory under 200 MB.

### 6.3 Integration (Light's repository, CI)

A private hub on a temp state dir with a fixture FinOps-shaped module
installed from a temp git repo: the dialog opens from the palette, renders,
survives a module failure, and a notice reaches the rail as a chip and
never as "Needs you".

### 6.4 Live checks on a real machine (manual, before calling v1 done)

- Codex weekly and 5 h figures match what the Codex CLI's status view says
  at the same minute.
- Claude notional value for one finished Light pane matches that pane's
  last `usage_update.cost` within 5%.
- A fresh pane's usage appears in the dialog within one collector period.
- Uninstall leaves Light's suite green and no FinOps files on disk except
  the operator's config, which `remove --purge` also deletes.

## 7. Open questions for the panel

1. Declarative views (M2) versus module JavaScript in a sandboxed iframe:
   is the block vocabulary enough for a second module, or will it be
   outgrown at once?
2. Out-of-process collectors (M1): is a subprocess every five minutes the
   right cost, or should a module be a long-lived child the hub restarts?
3. Is "notional value at list price" useful to a subscription user, or
   misleading next to a flat fee?
4. Claude's quota is only available from a network endpoint using the
   login token. Should the module ever do that, even opt-in?
5. Is reading Light's state dir directly (M6) acceptable, or should the
   core expose a read-only usage feed so modules never touch its files?
6. Anything in the current tree that makes this seam harder than §3.5
   says.
