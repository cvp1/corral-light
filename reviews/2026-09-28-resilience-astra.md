# Astra (gpt-6-astra, high) — review of docs/RESILIENCE-REVIEW-2026-09-28.md

pane 0d2ff619a903 · model gpt-6-astra · complete True

I’ll read the lifecycle, persistence, transport, and CLI code, then check the plan’s claims and sequencing against it.
The dead-pane resume gap is real, but graceful shutdown alone won’t protect accepted messages from crashes. I also found kill paths inside ACP error handling, and `save_meta()` silently ignores write failures. I’m checking how those affect the proposed host boundary and CLI guarantees.
Two more contradictions matter: Light already renders context usage and has find-in-pane, and Ollama’s “context lost” warning is suppressed during replay. The CLI also cancels work after a polling error or timeout. That means the pane host needs command and turn recovery semantics, not just a replayable event socket.
**1. Kill-path inventory — RESHAPE.**

- **K1 / TL;DR 1 — RESHAPE:** the failure domain is real, but pipes alone don’t guarantee every child exits. Adapters start separate sessions; distinguish lost transport, parent-death signals, and service-manager cleanup ([corral_core/acp.py:214](/home/cvande/Github/CC/corral-light/corral_core/acp.py:214)).
- **K2 / TL;DR 2 — KEEP:** dead panes explicitly refuse both resume and send ([sessions.py:1043](/home/cvande/Github/CC/corral-light/sessions.py:1043), [sessions.py:1375](/home/cvande/Github/CC/corral-light/sessions.py:1375)).
- **K3 / TL;DR 3 — RESHAPE:** shutdown handling helps SIGTERM, not SIGKILL/OOM; accepted messages already enter a volatile queue ([sessions.py:1417](/home/cvande/Github/CC/corral-light/sessions.py:1417)).
- **K4 — RESHAPE:** test remembered context, not successful `loadSession`. Ollama’s warning is emitted during load, when Light suppresses replay events—users may never see it ([ollama_acp.py:156](/home/cvande/Github/CC/corral-light/ollama_acp.py:156), [corral_core/sessions.py:477](/home/cvande/Github/CC/corral-light/corral_core/sessions.py:477)).
- **K5 — KEEP:** capacity refusal is explicit and creation reserves under lock ([sessions.py:1887](/home/cvande/Github/CC/corral-light/sessions.py:1887)).
- **K6 — RESHAPE:** resync cannot reconstruct everything: snapshots expose pending IDs, while permission bodies can leave the event ring ([sessions.py:1679](/home/cvande/Github/CC/corral-light/sessions.py:1679)).
- **K7 — RESHAPE:** a timeout note won’t unblock the sole worker; `_spawn()` waits indefinitely on its future ([corral_core/acp.py:158](/home/cvande/Github/CC/corral-light/corral_core/acp.py:158)).
- **K8 — RESHAPE:** restore also silently skips unreadable metadata and failed panes; failure isn’t reliably loud ([sessions.py:1926](/home/cvande/Github/CC/corral-light/sessions.py:1926), [sessions.py:1946](/home/cvande/Github/CC/corral-light/sessions.py:1946)).
- **K9 — KEEP:** catalog writes truncate directly; serialize writers as well as replacing atomically ([sessions.py:1850](/home/cvande/Github/CC/corral-light/sessions.py:1850)).
- **K10 — RESHAPE:** consult repairs authentication on initial connection, but subsequent GET/POST 401s simply raise ([consult.py:148](/home/cvande/Github/CC/corral-light/consult.py:148), [consult.py:245](/home/cvande/Github/CC/corral-light/consult.py:245)).

**2. Build list — RESHAPE.**

- **P0-a — RESHAPE:** fix lifecycle ownership, not just the predicate. Reservation only transitions `detached`, and lifecycle methods live in Light, not exclusively shared core ([sessions.py:1963](/home/cvande/Github/CC/corral-light/sessions.py:1963)).
- **P0-b — RESHAPE:** stop admission/draining before checkpointing. Existing `pause()` clears the queue and saves metadata again; naïve save-then-pause risks overwriting recovery state ([corral_core/sessions.py:658](/home/cvande/Github/CC/corral-light/corral_core/sessions.py:658)).
- **P0-c — RESHAPE:** distinguish deliberate pause from shutdown, or shutdown clears `was_live`. Twelve launches staggered two seconds already exceed ten seconds; load alone permits a handshake timeout ([corral_core/acp.py:641](/home/cvande/Github/CC/corral-light/corral_core/acp.py:641)).
- **P0-d / TL;DR 4 — RESHAPE:** external observation is useful, but one pane exception prevents tick advancement; automatic restart then sacrifices healthy panes. Add failure thresholds, cooldown, and diagnostics first ([hub.py:134](/home/cvande/Github/CC/corral-light/hub.py:134)).
- **P0-e — RESHAPE:** an SSE subscriber proves connection, not human attention; terminal watchers would suppress notifications too ([hub.py:338](/home/cvande/Github/CC/corral-light/hub.py:338)).
- **P1 — KEEP:** require semantic continuity and interruption tests, since resume suppresses load output ([sessions.py:1063](/home/cvande/Github/CC/corral-light/sessions.py:1063)).
- **P2 — RESHAPE:** adopt the host direction, but remove inactivity-based killing: today silence explicitly preserves work ([corral_core/acp.py:328](/home/cvande/Github/CC/corral-light/corral_core/acp.py:328)).

**3. Feature ports — RESHAPE.** Search, roles, scheduling, porting, and optional floor integration are separate product work. **DROP** context-pill and find-in-pane ports: both already exist ([static/app.js:903](/home/cvande/Github/CC/corral-light/static/app.js:903), [static/app.js:925](/home/cvande/Github/CC/corral-light/static/app.js:925)). Brief generation must finish before pause kills its authoring process ([corral_core/sessions.py:665](/home/cvande/Github/CC/corral-light/corral_core/sessions.py:665)).

**4. CLI — RESHAPE.** Missing verbs include `cancel`, live `config`, and archived `reopen`; routes already exist ([hub.py:469](/home/cvande/Github/CC/corral-light/hub.py:469), [hub.py:479](/home/cvande/Github/CC/corral-light/hub.py:479), [hub.py:496](/home/cvande/Github/CC/corral-light/hub.py:496)). More fundamentally, provide read-only attach/detach, reconnect/backfill, explicit turn IDs, and authoritative pending-permission payload retrieval. Printing an old event isn’t sufficient when snapshots retain only pending IDs ([sessions.py:1679](/home/cvande/Github/CC/corral-light/sessions.py:1679)).

**5. Sequence — RESHAPE.** Ship dead-pane recovery and durable admission first; investigate the pane host immediately afterward, before feature ports. P0 a–e is not a dependency chain. Notifications and auto-resume cannot preserve execution: resume constructs another adapter ([sessions.py:1056](/home/cvande/Github/CC/corral-light/sessions.py:1056)). The host matters most for deploy continuity, but cannot repair adapter crashes or storage loss.

**Missed kill paths:** any prompt RPC error becomes `AgentError`, and `_drain()` kills the adapter group—even if the process remains healthy ([corral_core/acp.py:351](/home/cvande/Github/CC/corral-light/corral_core/acp.py:351), [sessions.py:1486](/home/cvande/Github/CC/corral-light/sessions.py:1486)). Reader exceptions also trigger SIGKILL ([corral_core/acp.py:424](/home/cvande/Github/CC/corral-light/corral_core/acp.py:424)). Consult cancels on timeout or transient polling failure ([consult.py:460](/home/cvande/Github/CC/corral-light/consult.py:460)). Disk-write failures silently destroy recovery guarantees ([corral_core/sessions.py:336](/home/cvande/Github/CC/corral-light/corral_core/sessions.py:336)).

**The host’s two hardest failures:**

1. **Ambiguous command delivery and competing hubs:** reconnect after send-before-ack can duplicate prompts or misapply approvals. Require command IDs, deduplication, and fenced ownership. This introduces worse duplicate-side-effect risks than today.
2. **Replay gaps and lost authority state:** bounded events cannot substitute for current pending permissions, outstanding RPCs, and turn state. Require durable sequencing, snapshots, and explicit gap recovery. Silent incomplete replay is worse than today’s obvious detachment; correctly implemented isolation improves the blast radius.

**One additional build:** a durable turn ledger: accepted, dispatched, completed, interrupted, or uncertain, keyed by turn ID. Persist acceptance before acknowledging; never automatically replay uncertain execution. Today `/send` returns success after volatile queue insertion ([hub.py:450](/home/cvande/Github/CC/corral-light/hub.py:450)). This closes a loss window neither graceful shutdown nor a socket alone addresses.