# Corral Light modules, and FinOps as the first one

> Status: PROPOSED, rev 2, 2026-10-07. Nothing built.
> Rev 1 went cold to a three-vendor panel (Codex on gpt-6-astra, Grok,
> Gemini): AMEND, AMEND, REJECT. All three found the same central defect:
> rev 1 offered collectors "read-only" access to a state dir that holds the
> wall's cookie-signing key, and nothing enforced the read-only part. Rev 2
> is the reshape. What changed and why:
> `reviews/2026-10-07-finops-module-panel/synthesis.md`.

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
| Claude Code, Linux | credentials file fields `subscriptionType`, `rateLimitTier`; each Light pane's credential is a link to the shared file | JSONL transcripts in `~/.claude/projects/` AND in each pane's private config dir `<state>/panes/<id>/config/projects/` (Light links skills and plugins into pane dirs, not `projects`, so these are a second tree) | not on disk | the latest ACP `usage_update` (`used`, `size`, `cost`) is kept in memory and written inside every `turn_end` event in the pane's `events.jsonl`; individual updates are not logged |
| Claude Code, macOS | the login is in the Keychain; no credentials file; Light does not give panes a private config dir | `~/.claude/projects/` only | not on disk | same as Linux |
| Codex | `auth.json` in Light's own `CODEX_HOME` (`~/.config/corral-light/codex-home` by default, from `CORRAL_CODEX_HOME`), separate from a desktop `~/.codex` | rollout JSONL under `CODEX_HOME/sessions/`; `token_count` events with cumulative and last-turn usage | each `token_count` carries `rate_limits`: `plan_type`, 5 h and weekly `used_percent`, `resets_at`, credits | `usage_update` with `used`, `size`, no cost, inside `turn_end` |
| Grok | `~/.grok/auth.json` | session dirs; no token or cost fields found | none found | none |
| Gemini (Antigravity) | `~/.gemini/antigravity-acp/acp_token.json` | per-conversation SQLite, schema not yet read | unknown | none; Light owns this adapter and could emit `usage_update` |
| Ollama | none | none needed | none | none |

Consequences:

- Claude and Codex are covered from local files with no network. Codex
  reports its own quota on every turn.
- Grok and Gemini start as "credentials found, usage not reported on disk".
- A scanner that reads only `~/.claude/projects` misses Linux pane
  transcripts; one that reads both must dedupe.

## 3. Trust model (new in rev 2)

A module is code the operator installs. It runs as the operator's user.
Rev 2 does not pretend otherwise. It does three things instead:

1. **Gives the module nothing it does not need.** The collector never
   receives Light's state dir, the hub's environment, or any credential
   file. It gets a redacted feed the hub writes (§4.4), the vendor usage
   directories its manifest declares, and one writable data dir.
2. **Enforces that on Linux.** The collector runs under bubblewrap, reusing
   the machinery Light already ships for blind reviewers
   (`review_sandbox.py`), but as an **allowlist**: system directories
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
| M8 | **No notices in v1.** The dialog shows quota state. Rail chips come after v1, with count limits, stable ids and expiry tied to snapshot freshness. | Two reviewers wanted bounds that v1 does not need yet. |

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
  `grok-store`, `light-feed`.
- `network` is `none` in v1. Opt-in egress for balance reads is a later
  phase (§7), through Light's existing egress proxy, to named domains.
- Unknown keys or a `core_api` the core does not speak are refused.

### 4.3 Where things live

| What | Where |
|---|---|
| Module generations | `<state>/modules/<name>/<commit>/`, with `current` naming the active one |
| Pins | `~/.config/corral-light/modules.json`: source, commit, tree digest, enabled, `unsandboxed_ack` |
| Operator config | `~/.config/corral-light/modules/<name>.toml`, in the subset tomlmini reads |
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
  beyond `cwd`.
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
| `review_sandbox.py` | factor out the bubblewrap builder so the collector can use an allowlist profile; the reviewer profile is unchanged |
| `hub.py` | start runners after serving; `GET /api/modules`, `GET /api/module/<name>`, `POST /api/module/<name>/refresh` (rate-limited, one queued run); counts on `/health` |
| `test_corral_light.py` | the Live-surface route allowlist gains the `/api/module/` prefix, with a dated reason; this is a deliberate edit, not an incidental one |
| `static/app.js` | `renderModuleView(view)`, a pure function; a palette row per module; a module dialog |
| `doctor.py` | per module: pinned, verified, sandboxed or acknowledged, last run, last error |
| `corral-light` wrapper | `module` verb; enabled-module fall-through after every core verb |
| `modules/index.json` (new) | first-party index |

No change to `corral_core/`. The sibling product is untouched.

## 5. Installing and running a module

### 5.1 The operator's experience

```
corral-light module add finops
```

prints the source URL, the commit, the tree digest, what the manifest
reads, that it makes no network calls, and whether this host can sandbox
it. The operator types the module name to confirm (and, on an unsandboxed
host, types `unsandboxed`). Then:

```
corral-light finops setup
```

discovers accounts and asks about each one (§6.3). The FinOps row appears
in the ⌘K palette. Removing it is `corral-light module remove finops`;
`--purge` also deletes its config and data.

### 5.2 Running

Each run: verify the active generation (§5.3), build the sandbox (or the
acknowledged unsandboxed spawn), pass the allowlisted environment, run
with a wall budget, read capped output, validate, cache. The environment
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
  `.pth`, `sitecustomize.py` or `usercustomize.py` anywhere in it.
- Digest: SHA-256 over the sorted list of tracked paths, their modes and
  their contents, `.git` excluded.
- Verify before every execution path: collector, CLI, doctor. A mismatch
  disables the module, keeps its last snapshot marked "disabled: changed
  on disk", and says so in doctor.
- Update, remove and run take one lock per module, so none overlaps.

## 6. The FinOps module (`corral-light-finops`)

### 6.1 Shape

Stdlib Python 3.9+. Its own repository and CI.

| File | Job |
|---|---|
| `module.json` | manifest |
| `discover.py` | turns the feed and the declared read paths into account candidates |
| `sources/claude.py`, `sources/codex.py`, `sources/light.py` | one reader each; each returns facts plus its own freshness, parse rate and error |
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

`setup` runs discovery, prints each candidate, offers the `plans.toml`
price with its effective date as the default, and writes only what the
operator confirms. It is re-runnable and never deletes an account the
operator wrote. A source with no account still shows in the dialog under
"Not set up", with usage and no price.

### 6.4 What the dialog shows

| Tile | Figure | Kind |
|---|---|---|
| **Committed** | declared subscription prices per month | declared |
| **Quota** | each vendor-reported window: Codex 5 h and weekly, used percent and reset time | vendor |
| **API-equivalent list cost** | this month's usage priced at API list, per account, beside the plan's price, never added to it | list |

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
  checks that rule against real multi-record requests. `<synthetic>`
  model lines are skipped. Lines with no `requestId` are counted apart and
  priced, but marked "not deduplicated".
- **Codex deltas.** Per session, the last cumulative total. A new event's
  delta is the difference. A negative difference means the counter reset:
  that event's own total starts a new baseline. A session first seen
  mid-history takes its first total as a baseline, marked "history before
  this point not read". Duplicate or out-of-order events (same or lower
  timestamp and total) are ignored.
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

### 6.6 Privacy

- No prompt or answer text is kept beyond the line being parsed.
- The ledger holds counts, model ids, session ids and timestamps.
- Titles come from the feed and live only in the current snapshot.
- The module never reads a credential file; the feed carries the plan.
- On Linux, the sandbox makes the rest of this enforceable rather than
  promised.

## 7. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** | Claude: how many records share a `requestId`, and which is final; whether `usage_update.cost` survives resume, compaction and `/clear`; plan fields from the macOS Keychain. Codex: counter resets on resume and fork; where an account id lives that the core can hash without exposing the token; `rate_limits` against the Codex CLI's own status. Gemini schema; Grok usage command. Scan timing on this host. A prototype collector sandbox profile, with a test that it cannot open `session.key` or reach the hub port. | a results doc; this plan updated where an answer changes it |
| **1. Seam** | §4 and §5: modules, feed, sandbox profile, runner, routes, renderer, dialog, doctor, wrapper, index; tested with fixture modules in the test tree only | the seam tests in §8.1 pass; Light's suite passes with zero modules and with each fixture |
| **2. FinOps v1** | discovery, setup, Claude and Codex sources, ledger, prices and plans, the three tiles, per-lane table, Sources, Most used this week, CLI | installed here with one command; §8.4 checks pass |
| **3. More vendors** | Gemini and Grok sources if Phase 0 found data; rail notices with bounds and expiry | each new source passes the §8.2 source tests |
| **4. Network and macOS** | opt-in API balances through the egress proxy, in a separate fetcher with no usage mounts; a macOS sandbox investigation | the operator decides each |

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

### 8.3 Integration (Light's repository, CI)

A private hub on a temp state dir, a fixture FinOps-shaped module from a
temp git repo, installed through `module add`: the dialog opens from the
palette, renders, shows backfill progress, survives a module failure, and
`module remove --purge` leaves nothing behind.

### 8.4 Live checks on this host (manual, before calling v1 done)

- Codex quota figures match the Codex CLI's status view at the same
  minute.
- For one finished Claude pane, the module's list cost and the pane's
  last `usage_update.cost` are compared and the difference is reported.
  This is a diagnostic until Phase 0 establishes what that cost covers.
- A new pane's usage appears within one collector period, or at once
  after "Refresh now".
- Uninstall leaves Light's suite green.

## 9. Open questions for the operator

1. **Unsandboxed hosts.** Rev 2 lets macOS run a module after a typed
   acknowledgement. The stricter choice is to refuse modules on macOS
   until a sandbox exists. The author recommends the acknowledgement:
   FinOps declares no network, and refusing would leave Mac users with
   nothing.
2. **Where the module repository lives.** The author recommends a public
   repository beside Corral Light under the same owner, so the index can
   point to it.
