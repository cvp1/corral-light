You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a PERFORMANCE PLAN REVIEW: the author measured a running system, ranked the friction, and proposed changes. Your job is to find where the measurements, inferences or plan are wrong, incomplete or risky, and to say what you would do instead.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini, remote shell lanes, chat-only Ollama). The operator reports that hand-offs between assistants hang and the wall is slow to respond, and wants a 10x improvement in speed. The author's findings and plan are in docs/PERF-REVIEW-2026-10-04.md. Read it first, in full.

Your working directory is a private read-only clone of the repo at its current HEAD. The code the plan touches:
- hub.py (HTTP server, /api/state, /api/stream SSE, /api/session/* routes, the 5 s observer tick)
- sessions.py (Pane, Manager.state(), available_agents(), cwd_suggestions(), create(), close path) and corral_core/sessions.py (PaneBase.emit, ManagerBase.broadcast/subscribe, archived(), deliver_peer, peer_turn)
- corral_core/acp.py (the ACP JSON-RPC client: request(), the reader threads, close())
- consult.py (the hand-off client: open_pane, wait_turn, _await_ready, fanout, _parallel)
- corral_core/seat_mcp.py (seat_wait polling and PEER_WAIT_SEEN_S semantics)
- static/app.js (refresh(), connect(), scheduleRender, updatePane/logSignature, markSeen)
- ollama_acp.py (unavailable_reason: the HTTP probe called from available_agents), lane_probe.py, claude_auth.py, auth.py, ledger.py
- reviews/*/*.raw: prior panel arms with wall_s and needs_you_s per arm; the evidence behind the panel-timing table

RULES OF ENGAGEMENT
- READ ONLY. Do not edit files. Do not run the test suite, do not start a hub, do not talk to the running hub on port 8098, no network, no git push. You may run small read-only Python snippets that import modules from this clone to re-derive a number (for example, the size of a state() payload component), as long as they create no processes and open no sockets.
- Re-derive, do not just agree. At least two of the author's numbers or inferences must be checked against the code by you, and you must say which and what you found.
- Label each claim you make PROVEN (line numbers or a reproduced computation) or SUSPECTED.

WHAT TO ANSWER, in this order
1. One line: ADOPT / AMEND / REJECT for the plan as a whole, with the strongest reason.
2. DISCONFIRMING EVIDENCE: where a measurement is misattributed, an inference does not follow, or a "friction source" is not actually on the hot path. Be specific (file:line).
3. MISSED FRICTION: anything in the listed files that costs more time on the hand-off or wall-responsiveness path than what the plan lists, and that the plan does not address. Think about: the GIL and what runs inside the reader threads; what blocks inside HTTP handlers; what the browser does on each SSE event; what happens when a lane is slow or down (Ollama down, a vendor adapter hung at session/new); restart and resume paths.
4. PER-ITEM VERDICT for P0, P1 (each row), P2 (each row), P3, P4, P5: ADOPT / AMEND (say how) / REJECT (say why). Pay particular attention to the risks of: long-poll on ThreadingHTTPServer (thread count, the 30 s cookie re-check, the Condition wake-up fan-out from broadcast()); asynchronous close (forget and reopen racing the teardown, MAX_PANES accounting, orphan reaping on restart); a 10 s cache of lane availability; delta responses that omit per-pane fields (what in app.js or consult.py reads them).
5. SEQUENCING: the order you would do it in, what you would cut, and whether P3 (warm standby adapters) is worth the complexity.
6. ONE SENTENCE: the plan item most likely to under-deliver on its stated number, and why.

FORMAT: Markdown, under 1,500 words, no preamble. Do not restate the plan; critique it.
