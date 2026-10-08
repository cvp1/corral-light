You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a QUICK CODE REVIEW of what was built and is now live. Keep it focused; security first.

WHAT YOU ARE REVIEWING
This folder holds two read-only clones. `corral-light/` is Corral Light, a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine, with a module seam so features live in their own repositories. `corral-light-finops/` is FinOps, the first module. The plan is `corral-light/docs/finops-module-plan.md` (rev 7); Phase 4 is §6.7 as refined in §6.7.1. Earlier panel rounds are in `corral-light/reviews/2026-10-07-finops-module-panel/`.

Phase 4, BILLING API FETCHERS, lets a module read organization billing APIs with a key the operator grants. Built:
- corral-light/module_fetch.py (new, read all of it): key storage and checks (`key_path`, `key_add`), grants with non-secret params, `build_fetch` (the fetch sandbox profile: one key bound read-only, the egress shim and socket, CA dirs, a work dir), `run_fetch` (exact-host proxy per run, capped run, `needles` and `_check_result` refusing results that contain the key, `scrub` for errors, storage, backoff), `due`, `summary`.
- corral-light/review_egress.py: `exact_allowed`, `denied_everywhere`, the `allow=` hook on `Egress` (the reviewer lane rule must be unchanged).
- corral-light/modules.py: the `fetcher` manifest entry and `FETCH_VENDORS`, `fetch_dir` bound read-only into the collector, `build_run` refusing "fetcher", `Runner.fetch_tick`, remove/purge cleanup, the `module key|grant|revoke|fetch` CLI. module_sandbox.py is the profile the fetch reuses.
- corral-light/update.py `patch_adapters` and lanes.py `probe_client` (two fixes shipped beside Phase 4), adapter_patches.py `versions`.
- corral-light-finops/fetcher.py and finops/fetch/ (http.py client host checks; anthropic.py, openai.py, xai.py, gcp.py; rsa.py, a stdlib RSASSA-PKCS1-v1_5 SHA-256 signer), finops/sources/billed.py (ingest: a newer complete fetch replaces only days in its range), finops/view.py billed tiles (kind billed, never in Committed).
- Tests: corral-light/test_module_fetch.py, test_update.py (the three re-patch tests), test_lanes.py (TheProbeReadsTheFullState); corral-light-finops/tests/test_fetch.py, tests/test_billed.py; fixture corral-light/testkit/modules/probe/fetcher.py.
- Also, briefly: corral-light/docs/finops-macos-sandbox.md, a design for a macOS Seatbelt profile (not built yet).

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files, do not run tests, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this folder. Do not read anything under the home directory's vendor folders (~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.config, ~/.local/share): they hold private conversations and logins. Do not run vendor CLIs or bwrap.
- Label each finding PROVEN (file:line, and the input that triggers it) or SUSPECTED. Prefer a few real defects over many style notes.

WHAT TO ANSWER
1. One line: SHIP / FIX-FIRST / REWORK for Phase 4, with the strongest reason.
2. Up to EIGHT findings, most severe first. For each: severity (high/medium/low), PROVEN or SUSPECTED, file:line, the concrete failure (input → wrong result), and the smallest fix. Look hardest at: a way for a key to leave its vendor's hosts, reach the module's collector, a result, a log, an error or another grant; a way for a fetcher to reach any other host, the hub, a lane's sign-in host, the usage reads or the collector's data; key-file checks a symlink, hard link, race or mode can defeat; a result or grant param that can inject into SQL, a URL path or the page; a partial or older fetch replacing a complete one; money parsed or summed wrongly (units, currency, sign, float); the RSA signer; the update re-patch rolling back wrongly.
3. TESTS: one listed test that would still pass with its property broken, and the one missing test that matters most.
4. ONE SENTENCE: the single change that most improves Phase 4.

FORMAT: Markdown, under 1,200 words, no preamble.
