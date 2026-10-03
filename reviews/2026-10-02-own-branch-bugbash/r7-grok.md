I'll review the round-6 fixes against the ship bar, starting with the synthesis, the spec, and the HEAD~1 diff.The transport check is a denylist of config sections. I'll probe scope parsing and any repo-local setting outside those sections that can still move a confirmed push.The denylist is whole `http.*` and `ssh.*` sections. Next I’ll check whether included config is scoped so that check misses it, and whether any other repo-local key still moves a literal-URL push.SHIP — a repository-local `http.curloptResolve` (including through `include.path` and `includeIf.onbranch`), and a second `pushInsteadOf` of a confirmed URL, are refused before `git push`. Nothing new meets the ship bar.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. Any repository-local `http.*` or `ssh.*` key refuses Publish and is named (`worktrees.py:1429`), including harmless ones such as `http.postBuffer`.
2. `core.hooksPath`, or a `pre-push` hook in the common `.git/hooks`, runs during the literal-URL push (spec D5). A hook can copy the commit into a second repo while the confirmed URL also receives it and the push exits 0 (`scratch/probe_r7b.py`).
3. Git's default `http.followRedirects` is `initial`, with no config key. A confirmed `http(s)` server can redirect the pack, and the `ls-remote` check follows that same redirect. Setting the key locally is refused.
4. Global and system git config, the hub environment (`GIT_SSH_COMMAND`, `http_proxy`), and `~/.ssh/config` still steer a push. That boundary is unchanged.
5. An agent that switches branches blocks the pane, Discard included, until the branch is switched back.
6. Discard still skips a process whose `/proc` cwd and fds are both unreadable, and its settle check can refuse once while files are still changing.
7. On git 2.55, `alias.push` cannot hide the builtin. `remote.<name>.proxy` and `receivepack` do not affect a URL argument. `ls-remote --get-url` does not apply `pushInsteadOf`; the regexp parser does, and a truncated config read fails closed in `transport_override`.
8. `BugBash6Publish` calls real `wt.push` and requires reason `transport` for `http.curloptResolve`, the URL-scoped spelling, `http.followRedirects`, and `ssh.variant`. Isolated suite: 325 tests, OK, 103s.

The first repo that already has any local `http.*` line will refuse every Publish until that line is moved to the global config, even when the push URL is exactly the one on screen.
