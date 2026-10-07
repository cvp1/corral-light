# FinOps module plan, rev 1: panel synthesis (2026-10-07)

Arms, cold, read-only clone, via `corral-light consult`:

| Arm | Model | Verdict | File |
|---|---|---|---|
| Codex | gpt-6-astra (read-only mode) | AMEND | r1-astra.md |
| Grok | grok-4.6 | AMEND | r1-grok.md |
| Gemini | gemini-pro-agent | REJECT | r1-gemini.md |

A first Codex arm opened on the lane default (gpt-6.1-sol) instead of Astra.
It was closed after 25 s and replaced; its partial answer is in r1.jsonl.

## Converged (all three) → adopted in rev 2

- **M6 is false as a security claim.** A collector runs as the operator's
  user, so "read-only inputs" is a comment. The state dir it was offered
  holds `session.key`, which signs wall cookies (checked: `auth.py` KEYFILE
  is `STATE/session.key`), plus every pane's full transcript. Rev 2: the
  collector never sees the state dir. The hub writes a redacted feed, and
  on Linux the collector runs in a bubblewrap allowlist sandbox with no
  network. Where no sandbox exists (macOS), the module is labelled trusted
  code and runs only after an explicit acknowledgement.
- **The pin is integrity, not security.** Same-user code can rewrite
  `modules.toml`. Rev 2 says so, and hardens the pin against the real
  threats: half-updates, symlinks, stray files, argv tricks.
- **macOS has no per-pane Claude config dir and keeps the login in the
  Keychain** (checked: `darwin_keychain_blocks_isolation`,
  `seed_config_dir`). Rev 2 moves plan detection into the core feed, which
  already reads the Keychain for expiry.
- **Declarative views are right; module JavaScript is not.**
- **No Claude quota read with the pane login, ever** (Gemini, Grok; Astra:
  defer). Dropped.

## Two of three → adopted

- **The manifest cannot be parsed by the core's strict TOML reader on
  Python 3.9 and 3.10** (Astra, Grok; checked: `[table]`, arrays, floats,
  booleans and inline tables are all in `test_tomlmini.py` REFUSED). Rev 2:
  manifest is JSON; the operator's config uses only the subset tomlmini
  reads (prices in integer cents, enums as strings).
- **The 45 s collector timeout cannot complete a first scan budgeted at
  60 s** (Astra, Grok). Rev 2: time-boxed, resumable backfill with visible
  progress.
- **Allowlist environment**, not a copy of the hub's (Astra, Grok; the ACP
  spawn copies `os.environ`).
- **Drop "value per $"** (Astra, Grok). Rev 2 names the tile "API-equivalent
  list cost". Gemini liked the ratio; the tile still answers its question
  without asserting a return.
- **Coverage tile uses a `kind` the enum lacks and mislabels local lanes**
  (Astra, Grok). Rev 2 replaces it with a Sources section that says, per
  lane, reported / credentials found but no usage on disk / local, no
  provider charge measured.
- **Codex: two homes can be one subscription, a resume can reset the
  cumulative counter** (Astra, Grok). Rev 2: homes are sources, accounts
  are operator-assigned; negative deltas start a new baseline.

## One arm each → adopted

- Astra: event-level dedupe index, transactional facts and cursors, replay
  invariance test; staged generation dirs with rollback and serialized
  update/run; `/health` must not carry module paths or errors (it is
  unauthenticated); bound notices, expire them with the snapshot; the
  Phase 3 "within a minute" target contradicted a 5 min period.
- Grok: drop the `data` field; parse link URLs (a prefix check passes
  `https:alert(1)`); allowlist `level` and `kind` onto fixed classes; refuse
  `python3 -c` and `-m` in argv and run only the core's own interpreter;
  disable git hooks and submodules at clone; the route allowlist test must
  be edited deliberately for `/api/module/`; message-body sentinels in the
  privacy test.
- Gemini: cursor resume over a partial multi-byte UTF-8 sequence; vendor
  format drift must freeze a source, not silently flood "unpriced".

## Reviewer claims that were wrong (checked)

- **Grok and Gemini: "`usage_update` is never on disk."** Wrong. The hub
  keeps the latest update in memory and writes it inside every
  `turn_end` event (`sessions.py`, the emit after a turn completes);
  `events.jsonl` on this host shows `"usage": {"sessionUpdate":
  "usage_update", "used": …}` on turn ends. Astra stated this correctly.
  Rev 1 was still wrong to say each update is logged; rev 2 describes the
  real shape and has the feed expose it.
- **Gemini: "M5 adopted because core updates restart only when idle."**
  True but beside the point; module updates need no restart.
- **Grok: reading the credential file "maps the whole file into memory".**
  True, and the reason rev 2 moves credential reads into the core feed.

## Deferred, stated

- Multi-machine and second-operator accounting: v1 is this host only, and
  says so on screen.
- A macOS sandbox (`sandbox-exec`): Phase 4 investigation; until then
  macOS runs modules only as acknowledged trusted code.
