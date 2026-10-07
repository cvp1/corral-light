You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a DESIGN, IMPLEMENTATION AND TEST PLAN REVIEW: nothing is built yet. Your job is to find where the plan is wrong, unsafe, incomplete or over-built, and to say what you would do instead.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini, remote shell lanes, chat-only Ollama). It is the public, standalone sibling of a private "full Corral". The operator wants FinOps to be the first feature ported from full Corral into Light, as an installable module that is NOT part of Light's code base: install it, configure it with the accounts you use, and it works out by itself how to read Claude Code, Codex, Gemini and the rest. Light has no module mechanism today, so the plan also designs the seam.

The plan is docs/finops-module-plan.md. Read it first, in full.

Your working directory is a private clone of the repo with the plan committed. Code the seam would touch or depend on:
- hub.py (HTTP routes, do_GET/do_POST, _static, threads started at the bottom, /health)
- sessions.py and corral_core/sessions.py (Pane, Manager, POSTURES, the usage_update handling, the pane state dir layout and meta.json)
- corral_core/acp.py (how adapter processes are spawned and killed by process group)
- corral_core/tomlmini.py (the strict TOML reader the manifest would use)
- static/app.js (the ⌘K palette rows around openPalette, dialogs, the ctx pill that reads usage_update)
- doctor.py, update.py, the corral-light wrapper script (verb dispatch), lanes.py, codex_launcher.py (CODEX_HOME), claude_auth.py (the shared credential link)
- test_corral_light.py (StructuralIndependence bans finops.py and other heavy modules from the tree)

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files. Do not run the test suite, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this clone. In particular, do not read anything under the home directory's vendor folders (~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.config, ~/.local/share): the plan's §2 survey is the evidence, and those folders hold private conversations.
- Re-derive, do not just agree. Check at least two of the plan's claims about the current code against the code itself, and say which and what you found.
- Label each claim you make PROVEN (file:line or a reproduced computation) or SUSPECTED.

WHAT TO ANSWER, in this order
1. One line: ADOPT / AMEND / REJECT for the plan as a whole, with the strongest reason.
2. THE SEAM (§3): per decision M1 to M8, ADOPT / AMEND (say how) / REJECT (say why). Pay particular attention to: whether out-of-process collectors and declarative views are the right trade for safety versus capability; whether install pinning and digest checks actually stop a tampered or half-updated module from running; whether a module can still reach the wall or its secrets by some path the plan missed (environment, the state dir, the pane config dirs, the static route, the CLI fall-through in the wrapper); whether the snapshot limits and renderer rules close XSS.
3. THE MODULE (§4): is discovery right for each lane, given how Light actually lays out Claude and Codex homes? Are the dedupe rules (requestId for Claude, cumulative-to-delta for Codex) correct? What will be double-counted, missed, or mislabelled? Is "notional value at list price" honest next to a flat subscription fee? Are the privacy rules enforceable as written?
4. MISSING: what a stranger installing Light plus this module on a fresh Mac or Linux box will hit that the plan does not cover (first-run with no data, a vendor changing its file format, very large transcript stores, multiple machines, a second operator account).
5. TESTS (§6): the three most important missing tests, and any listed test that would pass while the property it names is broken.
6. SEQUENCING AND SCOPE (§5): what you would cut from v1, what you would move earlier, and whether Phase 0 asks the right questions.
7. The plan's six open questions (§7): a short answer to each.
8. ONE SENTENCE: the single change that most improves this plan.

FORMAT: Markdown, under 1,800 words, no preamble. Do not restate the plan; critique it.
