I'll review rev 3 against the round-one record and the code it cites, then judge each new claim on the evidence.Round-one record is in. Next I’ll check rev 3’s new code claims: rate-limit capture, the sandbox, and the egress proxy.Checking two more code paths the plan treats as already solved: when the adapter drops a rate-limit event, and where the Grok binary sits relative to its credential.**AMEND.** Rev 3 calls sparse, clobberable side events and catalog guesses vendor-authoritative, so a wrong number can render as confirmed.

## Round-one closure

Every finding of mine that the synthesis adopted is **CLOSED** in this text: no `data` field; `link.url` parsed (scheme, host, no userinfo) with a browser `href` test; `kind` and `level` mapped to fixed classes; `sys.executable -I` only, no `-c` or `-m`; clone with hooks off, no submodules, empty global git config; the Live-surface allowlist edit called out as deliberate; message-body sentinels; JSON manifest plus a tomlmini subset checked on 3.9; time-boxed resumable backfill; allowlist environment; "API-equivalent list cost" instead of a ratio; Sources instead of the Coverage tile; Codex homes collapsed by fingerprint and a negative delta starting a new baseline; feed instead of the state dir; pin treated as integrity; macOS plan detection moved into the core; no quota read with the pane's Claude login.

The synthesis was fair about `usage_update`. **PROVEN:** `sessions.py:1404–1405` stores the blob and does not emit it, and it is not in `META_KEYS` (`corral_core/sessions.py:479–499`). **PROVEN:** the last blob is written inside `turn_end` (`sessions.py:1808–1810`) through `_emit` (`corral_core/sessions.py:691–707`). My "never on disk" claim was too strong. What remains true is the bug rev 3 is chasing: only the last blob survives, and a later `usage_update` replaces it.

## Sources of truth

Checked the rate-limit claim against the adapter and `sessions.py`.

**PROVEN:** the adapter forwards `rate_limit_event` as a `usage_update` whose `_meta["_claude/rateLimit"]` is `message.rate_limit_info`, and only when `lastAssistantTotalUsage !== null` (`acp-agent.js:4919–4930`). `activateTurn` sets that variable to null (`acp-agent.js:2474–2476`, called at `2542`). An event that arrives before the first assistant usage of a turn is dropped in the adapter. The `sessions.py` split never sees it.

**PROVEN:** the result `usage_update` carries `cost` and no rate limit (`acp-agent.js:4102–4118`). The rate-limit update carries no `cost`. `attachUsageModel` merges `_meta` on that one object (`2456–2465`); it does not keep a previous window. `self.usage = data` then keeps whichever arrived last. Storing windows separately from `usage`, and writing both on `turn_end`, is the right hub fix. It does not fix the drop, and it must merge so the rate-limit update cannot erase `cost`.

**PROVEN:** the payload key is `rateLimitType`, optional, and the set is `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage` (`sdk.d.ts:5606–5610`). The same object also carries sibling overage fields (`overageStatus`, `overageResetsAt`, …). `quota.json`'s three names (`five_hour`, `seven_day`, `overage`) drop the per-model weekly windows. Keying only on `rateLimitType` drops the sibling overage fields when the event's type is `five_hour`. `resetsAt` and `utilization` are optional. Nothing in the type says utilization is 0 to 1 or present only near a warning. §2 states both as fact, while Phase 0 still asks the second. **SUSPECTED** scale. The structured `/usage` response in the same SDK is the percent-bearing source: utilization 0–100 on `five_hour`, `seven_day`, Opus, Sonnet, and `model_scoped` (`sdk.d.ts:3038–3056`, `4242–4310`; `usage-markdown.js:3–4` caps it at 100). It is marked experimental. `rate_limit_event` is a change notice ("emitted when rate limit info changes"). Rank 1 for Claude quota is mislabelled.

Checked the Codex sentence. **PROVEN:** `account/rateLimits/read` exists (`codex-acp` `index.js:35249–35250`) and is called from the `/status` handler (`35923–35928`). `account/rateLimits/updated` is handled and then returns null (`30460–30462`). `createUsageUpdate` is `{used, size}` only (`30985–30996`). Light's event log does not already contain that read. §6.5's rollout `token_count` path is the one that can feed the module. **SUSPECTED:** that those files carry `rate_limits`; this tree does not show the rollout schema. **PROVEN:** the adapter treats app-server `resetsAt` as unix seconds (`36192–36196`, `* 1e3`). One comparison against Claude's unitless `resetsAt` will mark one vendor always stale or never stale.

The staleness rule is unsound even after a unit normalization. A payload whose `resetsAt` is still in the future stays "current" for the whole window, so a 5-hour reading from hours ago still looks live. A missing `resetsAt` never goes stale, so `rejected` can stick. Codex "newest across homes" needs a normalized timestamp; a newer file with an older window wins otherwise.

Order of ranks is right for money: a bill above a declaration above a list estimate above null. It is wrong to put this Claude event in rank 1, and wrong to let a heuristic Grok sum outrank the list estimate under kind `vendor` (§2.1's "lower never overrides").

## Grok tool binding

**PROVEN:** the usual binary is `~/.grok/bin/grok`, and the credential is `~/.grok/auth.json` (`grok_launcher.py:19`, `48–49`). Binding "the binary and its runtime" can mount the tree the plan says the sandbox will not see. Setting `HOME` to a throwaway does not hide that path. `CORRAL_TOOL_GROK` is the binary path (§4.2), so the collector can exec it and skip the wrapper. The §8.1 tool test (HOME has no `auth.json`; the wrapper rejects flags) passes in both cases.

With a true allowlist, no network, a read-only sessions mount, and a wrapper that is the only exec, `grok usage` can still read session files (prompts), burn the 30 s budget, and write its stdout into the snapshot. There is no per-call timeout, so one hang fails the whole run despite "degrades Grok alone." **SUSPECTED:** the plan's "works with only a session dir" test; I did not run the CLI. Absolute-path opens and `usage ../auth.json` depend on the mount being the sessions directory alone.

The inherited-turn rule is not sound. It matches any recorded turn on timestamp, tokens, and ticks, with no parent id (§6.5). Two ordinary turns collide and one is dropped. A resumed turn whose ticks or timestamp differ is counted twice. The month sum is then still kind `vendor`. Until Phase 0 names the vendor's own inheritance marker, that sum is an estimate.

## Automatic setup

Yes. `setup --yes`, and Enter accepting every proposal, writes `plans.toml` prices into config, and accepted config is kind `declared` (§6.3). A wrong plan join becomes "the operator's price." The Committed tile shows proposed dollars before that, under a label that means a commitment, with kind `list` only as a chip. A later plan change correctly drops back to `list`; the initial accept does not. An unknown or ambiguous plan needs no price at all. The account fingerprint is still a Phase 0 question (§7). Hashing a rotating token makes every refresh look like a new account, and `setup --yes` will then declare it.

## Billing fetchers

The split (fetcher has the secret and egress, collector has the transcripts, neither has both) is the right shape. Missing:

- `secret_file` is any path the config names. A symlink to `session.key` or `auth.json`, plus module-chosen hosts, is a network path for a credential. Constrain it to a regular 0600 file in a dedicated directory, realpath-checked.
- `reads` are a fixed vocabulary. `network` is not. `host_allowed` is a suffix match (`review_egress.py:53–60`), so `api.anthropic.com` also allows `evil.api.anthropic.com`. Reuse `LANE_DENY` so a manifest cannot add `auth.x.ai` or `claude.ai`. Google needs `oauth2.googleapis.com` and `bigquery.googleapis.com`; allowing `googleapis.com` allows every Google API.
- The proxy pipe has no byte cap and runs in the hub (`review_egress.py:79–97`). A partial or truncated day must not replace a complete billed day.
- §5.1 still tells the operator the module makes no network calls. `module update` must re-prompt when the host list changes.
- The core should reject a snapshot that contains the secret bytes. Scrub `Authorization` and request URLs from errors. The module-side sentinel test does not cover that.
- Anthropic and xAI are described as admin or management keys. The dialog has to say so when the vendor has no read-only scope. **SUSPECTED** for those two products; this tree does not show their IAM.

## Tests

The two that are missing:

1. Quota capture in the adapter's real shape: event dropped while `lastAssistantTotalUsage` is null; result-then-rate-limit keeps `cost`; rate-limit-then-result keeps the window; `seven_day_opus` and sibling `overageStatus` both survive; missing `resetsAt` does not render as fresh; utilization is not rescaled.
2. Tool bind: with the realpath under `~/.grok`, `~/.grok/auth.json` is not openable; exec of `CORRAL_TOOL_GROK` with other argv fails; `usage ../auth.json` fails; one hung `grok usage` leaves Claude and Codex progress intact.

Listed tests that pass while the property is broken: the HOME/`auth.json` tool test above; "payload past `resetsAt` is stale," which ignores a future `resetsAt`, a missing one, and seconds versus milliseconds; the Grok fixture whose inherited turns already share timestamp, tokens, and ticks; `setup --yes` "writes exactly those," which treats promotion to `declared` as success.

## One sentence

Persist Claude's structured usage windows in the core, beside `usage` so neither update erases the other, and leave a `plans.toml` price as `list` until the operator types the amount.