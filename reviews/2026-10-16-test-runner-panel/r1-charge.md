You are reviewing a design before any code is written. Be adversarial. Find what breaks, what is unsafe, and what is missing. Do not edit any files; answer in text only.

# Context: corral-light
A Python (stdlib) hub that runs several coding agents (Claude, Codex, Grok, Gemini) in panes. Each agent can work in its own git worktree. When an agent finishes, the operator gets a REVIEW CARD: the diff from base to a FROZEN tree (a specific git tree oid). Actions on the card (merge, push) are bound to that exact reviewed tree oid; if the tree changes, the review is stale. A rival vendor's agent can "blind review" the diff inside a sandbox.

Two sandboxes already exist:
1. review_sandbox.py: Linux-only bubblewrap. Root read-only, home behind throwaway overlays, secrets dirs hidden, own netns with an HTTPS egress proxy to one vendor. Built for a reviewer AGENT that needs its lane login.
2. module_sandbox.py: allowlist profile. Linux: bubblewrap with an empty tmpfs root, only /usr, a few /etc files, declared read-only paths, ONE writable dir (also HOME and cwd), no network at all, cleared env, rlimits (2 GiB address space, nproc headroom 128, 1 GiB fsize, 1024 fds), die-with-parent. macOS: a deny-by-default Seatbelt profile via /usr/bin/sandbox-exec with the same interface (available(), build_argv(argv, read_only=, data_dir=, env=, ...)); paths passed as profile parameters, never spliced. The primary hub host is macOS (Apple Silicon).

# The external spec we are NOT implementing as written
"Epic 1: auto-detect test runner (package.json -> npm test, Cargo.toml -> cargo test, pyproject -> pytest, go.mod -> go test), or read .corral.json {"test_command": ...} from the worktree; run `cd worktree && eval "$TEST_CMD"`, timeout 180s, log to worktree/.test.log; states IDLE->RUNNING->PASSED|FAILED|TIMED_OUT; pill badge on the diff modal; approve button warns on failure; ANSI log viewer."
Rejected because: the command would come from a file the agent controls (agent writes .corral.json or package.json "test" script = arbitrary code execution on the operator's machine, outside any sandbox, with the operator's keys), eval, and an unsandboxed log written into the reviewed tree.

# Proposed redesign (review this)
1. The test command comes ONLY from the operator's config on the hub (per repo, keyed by repo path or remote), as an argv list, never a shell string, never from any file in the agent's worktree. No auto-detection that executes anything; detection may at most SUGGEST a command in the UI that the operator must confirm into config.
2. It runs against the frozen reviewed tree (the same tree oid the card shows), checked out into a fresh scratch dir, inside module_sandbox (Linux bwrap / macOS Seatbelt): no network, cleared env, no access to $HOME, keys, hub state or other worktrees; the scratch copy is the one writable dir.
3. Wall-clock timeout (default 180s, operator-configurable, hard cap), whole process group killed on timeout; stdout+stderr captured to a log in hub state (not in the tree), capped in size (e.g. last 256 KiB kept, truncation marked).
4. Result bound to the tree oid: {tree, state: running|passed|failed|timed_out|error, exit, duration, log_tail}. Card shows a pass/fail badge for that tree only; a new tree invalidates it. Advisory only: it never blocks merge/push, but a failed/timed-out/missing result is shown next to the approve action.
5. Triggered automatically when a card is created for a tree, at most one run per (repo, tree), concurrency-limited across the hub; operator can re-run.

# Questions — answer each, with concrete failure scenarios
A. Is "operator config supplies argv" actually sufficient? The operator's command (e.g. `npm test`, `pytest`, `make test`) still executes code the AGENT wrote (test files, conftest.py, package.json scripts, Makefile). Given that, is the sandbox the real security boundary, and is module_sandbox's profile strong enough for hostile code (macOS Seatbelt especially)? What escapes or exfiltration paths remain (e.g. writing to the scratch dir that the hub later reads, symlinks in the checked-out tree, log content injected into the UI, resource exhaustion, timing)?
B. Practicality: with no network and no $HOME, real suites need dependencies (node_modules, a venv, cargo registry, go module cache). How should dependencies be provided without reopening the hole? Options: operator-declared read-only dependency paths; a pre-built dependency dir; allowing network to a package registry only. Which would you choose and why?
C. Checkout: copy the frozen tree via `git archive <tree>` / `git worktree add --detach` / `git checkout-index` into scratch? What about submodules, LFS, untracked-but-needed build artifacts, and gitignored files the tests need?
D. Binding and staleness: anything wrong with binding to the tree oid? Same tree in two worktrees? Base branch moving?
E. UX: is "advisory, never blocks" right? What should the badge say when there is no configured command, when the sandbox is unavailable on this host, or when results are flaky?
F. What is missing entirely? Anything you would cut?

Format: numbered findings, most severe first. Each: severity (blocker/major/minor), the concrete scenario, and the fix you recommend. Then a short verdict: build as proposed / build with changes (list them) / rethink. Keep it under 900 words.
