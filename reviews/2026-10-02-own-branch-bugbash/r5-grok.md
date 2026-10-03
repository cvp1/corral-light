I'll review the round-4 fixes against the ship bar: prior repros first, then the new gate, publish, and start paths.The round-4 gate sits on the send path. I'll read that drain, the hold release, and the push rewrite check before trying the old repros.The new gate and the push rewrite check are the likely holes. I'll run the publish tests and try URL variants that git would still redirect.The rewrite check is the place a confirmed push can still be redirected. I'll probe aliases and URL forms against the real `git` this machine runs.`alias.push` does not hide the real `push` on this git. `core.sshCommand` did run. I'll check whether that sends the pack somewhere other than the URL the user confirmed.FIX FIRST — a repo-local `core.sshCommand` makes Publish store the commit in another repository and report success for the SSH URL the user confirmed.

## BLOCKING FINDINGS

### 1. High — PROVEN: SSH transport hijack bypasses `rewrite_rule`
- **File:line:** `worktrees.py:1394` (`rewrite_rule` never looks at the SSH command), `worktrees.py:1458` (the only gate before `git push`), `worktrees.py:1475` (`ls-remote` uses the same command), `worktrees.py:56` (`GIT_SSH` / `GIT_SSH_COMMAND` are not stripped in `git_env`).
- **Trigger:** The confirmed push URL is an SSH URL such as `git@github.com:somebody/repo.git`, and `core.sshCommand` is set in that repo's config. An agent shell can set it with `git config`; `git status --porcelain` stays empty, so the review diff and the uncommitted-files check both pass.
- **What goes wrong:** `push_urls` still returns the GitHub URL, `rewrite_rule` returns None, and `wt.push` returns `{"pushed": <oid>, "url": "git@github.com:somebody/repo.git"}`. The pack is stored in the repository the ssh command chooses. The follow-up `ls-remote` of the confirmed URL runs the same command (`git-upload-pack` after `git-receive-pack`), so the "remote shows the commit" check passes.
- **Minimal fix:** Before `begin_op`, refuse with `rewrite` when the URL is SSH (`git@` or `ssh://`) and `git config --get core.sshCommand` is non-empty, or `GIT_SSH` / `GIT_SSH_COMMAND` is in the environment `git()` actually passes. Name the command in the error. A legitimate `ssh -i …` is refused too; the message has to show it.
- **Repro:** `scratch/ssh_redirect.py` (env as in the charge). It prints `LANDED IN EVIL`, an empty origin bare tip, and a success result whose `url` is the GitHub string. Round-4's remote-name and legacy-remotes checks still hold: `BugBash5Publish` passed, and `git push dest/` does not select a remote named `dest`.

The round-4 drain re-check, the remote-name refusal, the legacy remotes file, and `start()` marking the pane dead all held. `BugBash3Publish`, `BugBash4Publish`, `BugBash5Hub`, `BugBash5Publish`, and the full run of 320 tests passed.

## NON-BLOCKING

1. A switched or detached HEAD blocks Discard as well as send; the refusal now names `git -C <path> switch <branch>` (`sessions.py:2484`). Deliberate.
2. `http.proxy` is not checked: a local proxy receives `CONNECT github.com:443` while `rewrite_rule` is None, and the push errors (`Proxy CONNECT aborted`) rather than reporting success.
3. A process whose `/proc` cwd and fds are both unreadable is still omitted from the Discard scan. Deliberate.
4. Discard's settle check can refuse once while files are still changing (T-RMV-11); the next Discard succeeds.
5. A repo on another filesystem than the worktree root is allowed (spec D2).
6. `test_BB4_33` only checks `rewrite_rule`'s return value; `test_BB4_10` treats `remote_changed` as proof the empty prefix was caught.
7. `start()`'s bare `except Exception` (`sessions.py:1251`) also reaps the agent if a step after the process is up, such as `save_meta`, raises.
8. On git 2.55, `alias.push` cannot hide the builtin (`aliases that hide existing Git commands are ignored`); a shell `alias.push` did not redirect `wt.push`.

An agent that checks out another branch, which these tools do on ordinary tasks, locks that pane including Discard until Craig runs the `git switch` printed in the refusal.
