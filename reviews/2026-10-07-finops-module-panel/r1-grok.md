I'll read the review skill and the full plan, then check the claimed code paths against the repo.The plan is in; next I’ll verify its claims against hub, sessions, ACP, TOML, and the wrapper.I’ll read the usage path, pane layout, TOML reader, wrapper, and independence tests next.I verified the plan’s claims against hub, sessions, TOML, auth, and the wrapper. Verdict next.

# FinOps module plan — panel review (Grok)

## 1. Verdict

**AMEND** — out-of-process + declarative is the right seam, but M6 hands the collector `session.key` and every prompt log, the cited TOML reader cannot parse the sample manifest, and the 45s collector timeout fights a first scan the plan itself budgets at 60s.

## 2. The seam (§3)

**M1 ADOPT, with a clean env and a first-run timeout.** Crash isolation is the right trade: the hub is the parent of every pane (`hub.py:1027–1041`; `corral_core/acp.py:122–125` `start_new_session=True`, `432–453` `killpg`). Reuse that spawn/kill, not a long-lived child. **PROVEN:** a collector started with inherited `os.environ` still sees `CORRAL_LIGHT_PORT` / `CORRAL_LIGHT_STATE` (`update.py:42–45`). The env test in §6.1 (“no hub URL, no pane token, no cookie”) can pass while those two are enough, with `STATE/session.key`, to mint a pairing cookie (`auth.py:27–29, 49–71`). Pass an allowlist env (`PATH`, `HOME`, `LANG`, the four documented vars), not a copy of the hub’s.

**M2 ADOPT; drop `data` from the v1 wire format.** Module JS in the paired page could hit every cookie route, including permission answers (`hub.py:748–752`). `el()` already uses `textContent` (`static/app.js:5–6`). `data: {}` is an unrendered channel that will become a JS renderer. `link.url` must be `https://` via `urlparse` (scheme, host, no userinfo) before assigning `a.href` — the existing sink is `a.href = …` (`app.js:307–308, 456–457`). A prefix check of `https:` accepts `https:alert(1)`. **SUSPECTED:** `className` from module strings is CSS injection; allowlist `level`/`kind` onto fixed classes.

**M3 ADOPT.**

**M4 AMEND.** A digest of the checkout does not stop a module that also rewrites `~/.config/corral-light/modules.toml` — same UID. Treat the pin as integrity against bitrot and half-updates, not a sandbox. Do what `lanes.py` already does: stage, then atomic swap. Pin `HEAD == commit` **and** a clean worktree **and** a tree hash of tracked files with `.git` excluded (gc would churn the digest). Refuse symlinks in the install tree (a `collector.py` → `/tmp/evil.py` link keeps the pin and changes what runs). Argv “must not escape the module dir” misses `python3 -c`, `-m`, and `-I`; allow only `sys.executable` plus relative files inside the module dir. **SUSPECTED:** `git clone` of a URL with `core.hooksPath` is unused today; still disable hooks (`GIT_CONFIG_GLOBAL=/dev/null`, no submodules).

**M5 ADOPT, wait for the collector to be idle before swapping.** A running `python3 collector.py` can import a mix of old and new files.

**M6 REJECT as written.** Read-only is a comment. `STATE` is `~/.local/share/corral-light` (`sessions.py:35–36`) and holds `session.key`, `pairing.json` (`auth.py:27–31`), `panes/*/events.jsonl` (full turns, `_emit` at `corral_core/sessions.py:691–707`), and on Linux `panes/*/config/.credentials.json` as a symlink to the live OAuth file (`sessions.py:476–483`). That is the wall’s secrets plus every prompt. **Instead:** the hub writes a redacted JSON under `<state>/module-data/_feed/panes.json` (id, agent, title, cwd, worktree_id, acp_session, last `usage` blob) and passes that path. Collector cwd is the module dir; writable dir is only `<state>/module-data/<name>/`.

**M7 ADOPT, with `module` in the wrapper `case` before any fall-through.** Today unknown verbs die at `*)` (`corral-light:65`). Fall-through must match an **enabled** module name and must not shadow `module`, `serve`, `doctor`, `update`, `lanes`. Operator-invoked CLI is the same trust as `python3 cli.py`; that is fine.

**M8 ADOPT.**

Snapshot limits are enough for size, not for XSS, until `href` is tested as a DOM property. Unknown `kind` has no default (unknown `level` becomes `info`); **Coverage** uses `derived`, which is not in the enum.

## 3. The module (§4)

**Discovery is Linux-shaped.**

- **Claude, Linux — mostly right. PROVEN:** per-pane dir is `self.dir / "config"` (`sessions.py:1641–1642`); `LINKED_CONFIG` is skills/agents/commands/plugins/prompts/CLAUDE.md, not `projects` (`sessions.py:199`), so pane transcripts are a second tree and `requestId` dedupe is required. Credential is a symlink to `~/.claude/.credentials.json`, so “plan fields only” still maps the whole file into memory.
- **Claude, macOS — wrong as written. PROVEN:** `seed_config_dir` returns `None` on Darwin (`sessions.py:466–467, 549–554`); `claude_auth.expiry` reads Keychain only and ignores a leftover file (`claude_auth.py:102–106`). There are no pane config dirs; plan type is not on disk. Phase 0 must treat this as a design input, not a re-measure.
- **Codex — right, with a double-count. PROVEN:** Light’s home is `CORRAL_CODEX_HOME` defaulting to `~/.config/corral-light/codex-home` (`codex_launcher.py:20–21`), not `~/.codex`. Scanning both is correct for a desktop CLI; the same ChatGPT login copied into both homes becomes two accounts and two notional totals. Collapse by `auth.json` account id if the vendor puts one there, without reading tokens.
- **Grok/Gemini presence-only — right** given §2.

**Dedupe.** Claude `requestId` across homes: **ADOPT** (matches the two-tree layout). Skip `<synthetic>`: **SUSPECTED** (not in this tree). Codex cumulative→delta: **ADOPT**, but a resume that restarts the cumulative at 0 yields a **negative** delta the plan never mentions — drop negatives, do not abs(). Light `usage_update` as a 5% cross-check: **the data is not on disk**. **PROVEN:** `elif kind == "usage_update": self.usage = data` (`sessions.py:1404–1405`) does not `emit`, and `usage` is not in `META_KEYS` (`corral_core/sessions.py:479–499`; Light extras `sessions.py:856–858`). After a hub restart, or from a subprocess that only reads files, it does not exist. The ctx pill already treats it as unstable (`app.js:1327–1335`). Cross-check belongs in the hub feed, or drop it from v1.

**Missed / mislabelled.** Unpriced models as “unpriced”: good. Consult-panel panes sharing one Claude project slug: one transcript, several titles — attribution will over-split or under-merge. Ollama/host lanes in Coverage as “unreported spend” would mislabel local/no-money lanes.

**Notional vs flat fee.** Honest only as a **list** tile that is never summed with **declared**. “Value per $” is a sales ratio, not a measurement. Keep the two tiles; kill the ratio in v1.

**Privacy is not enforceable as written.** A JSONL line is the prompt. Tests seed credential sentinels only. Add message-body sentinels. Even then, M6-as-written lets the collector open `events.jsonl` and skip `discover.py` entirely.

**`finops.toml` is not tomlmini.** Floats, bools, arrays, inline tables are in `REFUSED` (`corral_core/test_tomlmini.py:26–30`). The module needs `tomllib` (3.11+) or a small parser; Light is 3.9+ (`hub.py:23–27`).

## 4. Missing (stranger on a fresh box)

- Empty first run: no `~/.claude`, no Codex login — tiles must say unreported, and `setup` must have a “nothing found; log into a lane and re-run” path.
- `timeout_s = 45` vs a 500 MB scan “under 60 s” (§6.2): the first run never completes, last-good is empty, FinOps is a permanent error chip. Checkpoint per file and return a partial snapshot.
- Vendor format change: one new JSONL shape should freeze last facts for that source, not blank the room. Pin a golden event fixture per vendor and fail doctor on parse-rate collapse.
- Multi-machine: this host only. Say so on the Coverage tile.
- Second UNIX user: separate `STATE` and Keychain; shared `~/.claude` if they use the same home — document it.
- Container install (`container/`): vendor homes may not be mounted.
- `timezone = "local"` plus DST: already tested; still document that “month” is the operator’s zone, not UTC.

## 5. Tests (§6)

**Three missing**

1. **Feed isolation:** collector process cannot open `session.key` / `pairing.json` / `events.jsonl`; a forged cookie must not authenticate. This is the property M6 claims and does not implement.
2. **Manifest round-trip:** the published `module.toml` parses with `tomlmini.loads_strict` on 3.9 and 3.11. Today `[collector]` and `argv = [...]` are refused (`test_tomlmini.py:26`).
3. **DOM `href`:** after `renderModuleView`, every `<a>.href` is `https://` with a host; `javascript:`, `https:alert(1)`, and `http://` never become links. Text content is the wrong assertion.

**Listed tests that can pass while the property is broken**

- `test_hub_serves_only_the_live_control_plane` only greps `p == "/…"` (`test_corral_light.py:210–222`). `p.startswith("/api/module/")` never appears, so the “Live-surface only” rule survives in CI after the new route exists.
- Env test “no hub URL” while `CORRAL_LIGHT_STATE` + `session.key` remain.
- Tamper test that hashes `collector.py` but not `sources/*.py`.
- Secrets test that never puts a sentinel in a transcript **message** body.
- “Existing suite unchanged with zero modules”: adding `/api/modules` to `hub.py` **requires** editing that allowlist; the suite will not stay unchanged.

## 6. Sequencing (§5)

**Cut from v1:** API balances, Claude quota-over-OAuth, Grok/Gemini beyond presence, “value per $”, the Coverage tile (kind not in the enum; Ollama will look broken), Phase 4 core `usage_update` emission.

**Move earlier (into Phase 1):** redacted pane feed (M6), manifest rewritten to tomlmini’s subset, first-scan checkpoint + timeout, symlink/`-c` install refusals, explicit StructuralIndependence allowlist change, Darwin discovery.

**Phase 0 is incomplete.** Keep Gemini schema, Grok CLI, Codex `rate_limits` vs CLI, scan timings, Darwin re-measure. Add: (a) is `usage_update` anywhere on disk? (no); (b) can `tomlmini.loads_strict` read the manifest? (no); (c) Darwin Keychain vs file for plan type; (d) Codex resume cumulative reset; (e) what of `STATE` a subprocess can read. Do not ship Phase 1 until (a)–(c) change the plan.

## 7. Open questions (§7)

1. **Keep declarative.** Grow the block list when the second module needs a chart. An iframe is a second origin, postMessage, and a click-jacking surface; the hub already sends `X-Frame-Options: DENY` (`hub.py:99–101`).
2. **Stay with a timed subprocess.** A long-lived child reintroduces the leak/hang the hub cannot afford. Fix first-scan with cursors and a longer first timeout, not a daemon.
3. **Useful if labelled list-not-billed.** Drop the ratio. The question “is Max worth it vs API” is why the tile exists.
4. **No.** The live login token is in Keychain or a 0600 file the panes need. A leaked opt-in usage call is a session steal. If ever: a separate token the operator pastes, never `claudeAiOauth`.
5. **Core feed. Never the state dir.** See M6.
6. **Yes:** tomlmini subset; route-allowlist regex; `usage_update` RAM-only; `session.key` in `STATE`; Darwin has no pane `CLAUDE_CONFIG_DIR`; wrapper `*)` has no fall-through; `python3` on PATH vs `sys.executable`.

## 8. One sentence

Replace “module gets the state dir” with a hub-written redacted pane-usage feed, and write `module.toml` in the TOML subset `tomlmini.loads_strict` actually accepts.