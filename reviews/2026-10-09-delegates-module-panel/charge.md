You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a DESIGN, IMPLEMENTATION AND TEST PLAN REVIEW: nothing is built yet. Your job is to find where the plan is wrong, unsafe, incomplete or over-built, and to say what you would do instead.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP. It is the public, standalone sibling of a private "full Corral". It now has a module seam: out-of-tree modules run as sandboxed, time-boxed collectors that emit a declarative snapshot the core renders. FinOps was the first module (docs/finops-module-plan.md, built and live). The operator wants Delegates to be the second: a generic, public board of "agents I have handed work to on other machines, under a written charter: what they may do, until when, what they cost, and whether any runs past its grant". Full Corral's Delegates (ranch-specific, summarised in the plan's §1) is one configuration of it, fed through an inbox. Lanes to delegate boxes are explicitly deferred.

The plan is docs/delegates-module-plan.md. Read it first, in full. Then read docs/finops-module-plan.md §3 to §5 and §4.7 (the seam's trust model, manifest, feed, snapshot contract, notices), because the Delegates plan claims v1 needs NO core change.

Your working directory is a private, read-only clone of the repo with the plan committed. The built seam the plan depends on:
- modules.py (manifest checks, pins, the runner, config_dir/data_dir, how the collector and the CLI are spawned and what each may write; see around the collector/interactive spawn)
- module_sandbox.py (the bubblewrap allowlist profile: what is bound read-only, writable, and what is absent)
- module_feed.py, vendor_reports.py (the core-run report pattern the plan's Phase 2 ssh-reach would copy), module_fetch.py and review_egress.py (exact-host egress for Phase 3)
- sessions.py around _live_ssh_hosts / refresh_host_lanes (Light's existing host: ssh lanes)
- static/app.js (renderModuleView and module notices), test_modules.py

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files. Do not run the test suite, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this clone. In particular, do not read ~/.ssh, ~/.config, ~/.local/share or any vendor folder.
- Re-derive, do not just agree. Check at least two of the plan's claims about the current code against the code itself (the "no core change in v1" claim is the most important one), and say which and what you found.
- Label each claim you make PROVEN (file:line or a reproduced computation) or SUSPECTED.

WHAT TO ANSWER, in this order
1. One line: ADOPT / AMEND / REJECT for the plan as a whole, with the strongest reason.
2. NO CORE CHANGE (§3, §4.1-4.3, §7): is it true that v1 runs on the seam as built? What does the collector actually see, and is `ssh-keygen -Y verify` usable inside the sandbox as described? Anything the plan assumes the seam provides that it does not.
3. THE MODEL (§2, §4.1): is the state machine right and complete? Is OVERDUE-REVOKE (grant ended, box still listed) detected reliably, including against an operator or tool that deletes or edits a charter, clock skew, time zones, and a stale inbox? Is the charter format (flat front matter, ssh signatures with a fixed namespace) sound? What is mislabelled or can read as "fine" when it is not?
4. THE INBOX (§4.3): is "any outside tool writes JSON into the config folder" a sound way to plug in an estate with no new privilege? Who else can write there, what can a hostile or buggy inbox file make the board say, and does the shadowing rule (local charter beats inbox) hold up? Answer open question Q2.
5. PHASE 2 (§4.5): is a core-run ssh-keyscan probe the right reachability check, and is its sandbox (port-22 egress to named hosts, known_hosts read by the core) specified tightly enough? What can go wrong with host-key comparison (hashed known_hosts, aliases, ssh_config HostName)?
6. LANES AND FLEET (§8, §10): does anything in the v1 data model block a later lane primitive or a generic Fleet? Should lanes be a core lane type rather than a module capability?
7. TESTS (§12): the three most important missing tests, and any listed test that would pass while the property it names is broken.
8. The plan's open questions (§9): a short answer to each.
9. ONE SENTENCE: the single change that most improves this plan.

FORMAT: Markdown, under 1,800 words, no preamble. Do not restate the plan; critique it.
