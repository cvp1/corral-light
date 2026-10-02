You are one of three independent reviewers on a design-and-code panel. The other two are different models from different vendors; you will see their reviews in a second round. The author is a Claude agent working for the operator, who owns this system and makes the final call.

WHAT YOU ARE REVIEWING
A first-release Linux installer for Corral Light — a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini) — together with the AI-OS Seed workspace it sits on. The goal, in the operator's words: an installer "my mom could use", where installing is "as easy as clicking on a link and the next thing is a running corral session", "100% reliable and repeatable", Linux only for now.

Three artifacts are pasted below, in full:
  A. install.sh — the installer (bash).
  B. The new top of README.md — what a first-time user reads.
  C. launch.py — a new hub verb that opens the browser already paired, plus the 20-line browser-side change it relies on.

FIXED CONSTRAINTS (solve within them, do not argue against them):
  C1. Linux only, this release. x86-64 and arm64.
  C2. Native install, not a container. (A container design exists separately and is not in scope.)
  C3. No vendor API keys anywhere. Each assistant signs in with its own tool, as the user.
  C4. The hub must never run as root; assistants run as the user.
  C5. AI-OS Seed's gated writes (CLAUDE.md, mesh bootstrap, memory hooks) go through `install.py --approve`, never a direct write.

Everything you need is in the paste. Do not open files or run commands. If a claim looks wrong but you cannot check it from the text, say "unverifiable from the text" — but you MAY draw on your own platform knowledge (bash, systemd user units, Debian/Ubuntu/Fedora/Arch packaging, npm, Node, curl|bash patterns, each CLI's login flow) and should say when you do.

YOUR JOB — break it, then fix it
1. VERDICT, one line each with the strongest reason, BUILD / RESHAPE / KILL: (a) the overall approach, (b) install.sh, (c) the README top, (d) launch.py and the ?pair= page change.
2. THE MOM TEST. Walk the first run as a non-technical person on a fresh Ubuntu 24.04 desktop with none of these tools installed. List every point where she would be stuck, confused, or asked something she cannot answer. Be concrete: quote the line she sees.
3. BUGS AND RELIABILITY FAILURES, ranked most severe first, at most 12. For each: the exact line or construct, the input or machine state that triggers it, what the user sees, and a FIX. Look hard at: idempotence on a second run; partial failure and resume; the trap/ERR and `set -e` interaction with `||`, pipes and subshells; stdin when piped through `curl | bash`; `/dev/tty`; the tee/exec redirection; PATH for the systemd user service; the sudo/package step; the Seed `--detect` grep; the `claude auth status` check; the `--yes` and `--skip-logins` semantics; arm64; distros where `python3` is 3.8 or where `cron` is not systemd-managed; what happens if port 8098 is taken; what happens with no display.
4. SECURITY: the curl|bash pattern, what is pinned and what is not (Claude Code's installer is not pinned), the pre-approved pairing code in a URL, the PATH edits to ~/.profile and ~/.bashrc, the drop-in Environment=PATH line. For each: real risk or not, and the smallest fix.
5. WHAT IS MISSING for "100% reliable and repeatable": at most 6 items, each with how to add it.
6. TESTS: up to 6 automated checks (shell or Python) that would catch regressions in this installer without a human, and how each would run in CI.
7. ONE SENTENCE: what would most likely make this fail on the operator's mother's machine.

FORMAT: Markdown, under 1,500 words. Start with the four verdict lines. No preamble.
