You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is ROUND TWO of a DESIGN, IMPLEMENTATION AND TEST PLAN REVIEW. Nothing is built yet.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, Codex, Grok, Antigravity/Gemini, remote shells, Ollama). The plan, docs/finops-module-plan.md, designs a module seam for Light and a FinOps module as its first user, kept out of Light's repository.

Round one reviewed rev 1. Its record is in reviews/2026-10-07-finops-module-panel/: r1-astra.md, r1-grok.md, r1-gemini.md (the three reviews) and synthesis.md (what was adopted, and which reviewer claims were checked and found wrong). Rev 2 answered round one. Rev 3, the current file, adds the operator's new direction: "automate this as much as possible", taking every figure from each vendor's own source of truth wherever one exists. Rev 3's changes are in §2 (corrected survey), §2.1 (order of sources), §4.2 (`tools` binding for running `grok usage` in the sandbox, and a separate network `fetcher`), §4.4 (quota in the feed), §4.6 (a `sessions.py` fix so Claude's rate-limit payload is no longer overwritten), §6.3 (automatic proposed accounts), §6.4 (tiles), §6.5 (Grok and quota reading rules), §6.7 (opt-in vendor billing APIs), §6.8 (telemetry streams, not adopted), §7 and §8.

Read the plan in full, then the round-one record.

Code worth checking against the plan: sessions.py (the `usage_update` branch in the event handler, the `turn_end` emit, darwin_keychain_blocks_isolation), corral_core/sessions.py (META_KEYS, emit), review_sandbox.py (the bubblewrap builder rev 2 and rev 3 reuse), review_egress.py (the egress proxy the fetcher would use), hub.py, codex_launcher.py, claude_auth.py, corral_core/tomlmini.py, test_corral_light.py, and the Claude adapter in spike/node_modules/@agentclientprotocol/claude-agent-acp/dist/acp-agent.js (search for `rate_limit_event`).

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files. Do not run the test suite, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this clone. Do not read anything under the home directory's vendor folders (~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.config, ~/.local/share). The plan's §2 is the evidence about vendor files, and those folders hold private conversations and logins. Do not run vendor CLIs.
- Re-derive, do not just agree. Check at least two of rev 3's new claims about the code against the code itself, and say which and what you found.
- Label each claim PROVEN (file:line or a reproduced computation) or SUSPECTED.

WHAT TO ANSWER, in this order
1. One line: ADOPT / AMEND / REJECT for rev 3 as a whole, with the strongest reason.
2. ROUND-ONE CLOSURE: for each finding YOU raised in round one that the synthesis says was adopted, say CLOSED or STILL OPEN (and why). Also say whether the synthesis judged any of your round-one claims wrong unfairly.
3. SOURCES OF TRUTH (§2, §2.1): is the order right? Is any "vendor-reported" figure not actually authoritative, or mislabelled? Is the Claude rate-limit capture fix (§4.6) correct given how the adapter sends the payload? Is the staleness rule for quota sound?
4. THE GROK TOOL BINDING (§4.2, §6.5): is running a vendor binary inside the collector sandbox, with HOME reduced to the sessions dir and an argv wrapper, safe? What can that binary still do? Is the inherited-turn dedupe rule sound?
5. AUTOMATIC SETUP (§6.3): does proposing accounts and prices automatically create any way for a wrong figure to look confirmed?
6. BILLING API FETCHERS (§6.7): scope, key handling, sandboxing. What is missing?
7. TESTS: the two most important missing tests for rev 3's additions, and any listed test that would pass while its property is broken.
8. ONE SENTENCE: the single change that most improves rev 3.

FORMAT: Markdown, under 1,500 words, no preamble. Do not restate the plan; critique it.
