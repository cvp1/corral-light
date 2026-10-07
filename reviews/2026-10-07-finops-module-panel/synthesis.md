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

---

# Round two: rev 3 (vendor sources of truth first)

Arms, cold, read-only worktree, given the round-one record:

| Arm | Model | Verdict | File |
|---|---|---|---|
| Codex | gpt-6-astra (read-only mode) | AMEND | r2-astra.md |
| Grok | grok-4.7 | AMEND | r2-grok.md |
| Gemini | gemini-pro-agent | AMEND (was REJECT) | r2-gemini.md |

Round-one closure: Astra and Grok mark every adopted round-one finding
closed in the design, except Astra's account history, Codex out-of-order
replay and ledger migration rollback, which stay open. Gemini marks its
four closed. All three say the synthesis judged them fairly.

## Converged (all three) → adopted in rev 4

- **`setup --yes` turns a catalogue guess into a "declared" price.** Rev 4:
  accepting a proposal accepts the account, never the price; a price
  becomes `declared` only when the operator types the amount.
- **The Grok binary needs its own limits.** A hang or memory blow-up in
  `grok usage` must not stall the collector. Rev 4: per-call timeout and
  resource limits, in a separate tool sandbox.

## Two of three, proven → adopted

- **The Claude adapter drops rate-limit events that arrive before the
  turn's first usage** (Astra, Grok; checked `acp-agent.js` 4919-4930,
  `lastAssistantTotalUsage !== null`). The hub fix alone cannot recover
  them. Rev 4 adds an adapter change (upstream, or a pinned local patch).
- **Claude reports six window types plus sibling overage fields** (Astra,
  Grok; checked `sdk.d.ts` 5606-5625: `five_hour`, `seven_day`,
  `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`,
  `overage`; `overageStatus`, `overageResetsAt` and more). Rev 3 kept
  three. Rev 4 keeps every validated field, keyed by type, unknown types
  included. `utilization` semantics are not stated in the type; rev 3's
  "0 to 1, only near a limit" moves to Phase 0 as unverified.
- **The Grok binary lives beside its login** (Grok; checked
  `grok_launcher.py`: `~/.grok/bin/grok` is the default, a symlink to a
  versioned file, and `~/.grok/auth.json` is the credential). Binding "the
  binary and its runtime" could expose the login; and a collector that can
  see the binary can run it with any argv, so the wrapper was validation,
  not a control (Astra, Grok). Rev 4: the core runs `grok usage` itself in
  a separate tool sandbox binding only the resolved binary file and the
  sessions dir, and hands the collector the JSON.
- **The inherited-turn tuple rule can merge two real turns** (Astra, Grok;
  Gemini judged it sound). Rev 4 drops the heuristic: until Phase 0 finds
  the vendor's ancestry marker, Grok month totals that include resumed or
  forked sessions are labelled as possibly double-counted, never silently
  deduplicated.
- **Quota staleness was unsound** (Astra, Grok; Gemini judged it sound):
  "older than its own reset" never expires an observation whose reset is
  in the future, never expires one with no reset, and Claude's and
  Codex's reset units differ. Rev 4: compare now to reset, cap by
  observation age, treat a missing reset as stale after one window,
  normalize units, scope quota per account.
- **One global source ranking compares unlike quantities** (Astra; Grok on
  the Claude and Grok ranks). Rev 4 replaces it with per-metric source
  lists.
- **The existing sandbox builder shares the host network when no egress
  proxy is given and mounts the host root read-only** (Astra, checked
  `review_sandbox.py` 229-231). Rev 4 states the collector profile is a new
  allowlist profile, not the reviewer profile with options.
- **The egress proxy matches subdomains** (Grok, checked
  `review_egress.py` `host_allowed`). Rev 4: fetchers use exact host match
  and the lane deny lists.
- **Codex: the adapter calls the quota read only from `/status`, and
  drops quota-update notifications** (Grok). Rev 3 overstated it; rollout
  files stay the source.
- **The existing login checks return expiry and presence, not plan or a
  fingerprint** (Astra). Rev 4 lists the extraction as core work; the
  fingerprint is a hash of a stable account id, never of a rotating token
  (Grok).

## One arm each → adopted

- Astra: sort Codex events per session before deltas (replay invariance
  contradicted "ignore out-of-order"); ledger schema migrations with
  rollback; fetcher-to-collector handoff through the core; replace a
  billed day only after a complete fetch; month attribution by turn date.
- Grok: `secret_file` confined to one directory, regular file, 0600,
  realpath-checked; install and update show and re-confirm the fetcher's
  host list; the core refuses a snapshot containing secret bytes; say when
  a vendor offers no read-only key scope.
- Gemini: org-level billing data is shown as its own account and never
  joined to a personal subscription tile; retry with back-off on 429;
  record the vendor binary's version with each Grok figure.

## Reviewer claims that were wrong or weak (checked)

- Gemini cited `acp-agent.js:522` for the rate-limit forwarding; it is at
  4919-4930. The substance was right.
- Gemini judged the tuple dedupe and the staleness rule sound; both were
  shown unsound with concrete counterexamples by the other two arms.
