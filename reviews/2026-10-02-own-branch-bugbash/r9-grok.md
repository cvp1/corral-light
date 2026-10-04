I'll review the round-8 Publish destination fix against the ship bar, starting with the synthesis, the new push-routing code, and the BugBash tests.The round-8 fix is the config fingerprint and include refusal. I'll read the push path and the spec's destination rules next, then try to break them.The denylist is still a name list. I'll probe repository settings git applies to a literal URL, and run the publish tests beside that.FIX FIRST — a fifo `commondir` makes Publish report success for the confirmed URL while the branch exists only in a different repository.

## BLOCKING FINDINGS

1. **High, PROVEN.** `worktrees.py:1446` (`config_fingerprint`), checked at `worktrees.py:1509` and again at `1541`. The fingerprint covers `common_dir/config` and `config.worktree` only. On a linked worktree, every git command reads `<common_dir>/worktrees/<admin>/commondir` to choose which common dir those files come from. That file is not required to be a regular file and is not hashed.

**Trigger.** Replace `commondir` with a fifo before Publish. A feeder (a background process the agent can leave running; Publish holds the pane and does not stop the agent) writes the real common dir for `git config`, `git remote`, `git ls-remote --get-url`, `git status`, and `verify`'s git calls, and writes a second common dir for `git push` and the confirming `git ls-remote`. The second dir has `url.<other>.insteadOf = <confirmed URL>` and an object alternate back to the real store.

**What goes wrong.** `transport_override`, `rewrite_rule`, and `push_urls` see the real config. `wt.push` returns success for the confirmed URL, the op is `done`, and `published.url` is that URL. The confirming `ls-remote` follows the same `insteadOf`, so the oid matches. The ref is absent on the confirmed remote and present only in the other repository. BugBash3–7 Publish and the four isolated suites (330 tests) pass; none of them touch `commondir`.

**Minimal fix.** In `config_fingerprint`, also `lstat` and hash `<admin>/commondir` and the worktree `.git` gitfile. Refuse unless each is a regular file, and treat a missing `commondir` as refusal. A fifo then dies before `git push`, the same way a fifo config does. The gitfile is the same shape (`verify` reads it once in Python at `worktrees.py:911`, then git reads it again); that variant was not run separately.

**Repro.** `scratch/probe_r9_commondir.py`. Confirmed URL `scratch/r9cd/good.git`. `wt.push` returned `{'pushed': 'b77d44252babbee94f5229ade2bd7717e1ef9e3b', 'url': '…/good.git', 'ref': 'refs/heads/corral/fix-login'}`. `good.git` has only `refs/heads/main` at `e131d672`. `evil.git` has `refs/heads/corral/fix-login` at `b77d4425`. The push op is `('done', 'done', '…/good.git')`. Before the push, `transport_override` and `rewrite_rule` were both `None` and `push_urls` was `good.git`.

## NON-BLOCKING

1. A regular `commondir` or config swapped after the checks and restored before the second fingerprint is still invisible (the round 8 suspected race). Not reproduced.
2. `hook.<name>.event=pre-push` and `hook.<name>.command` are repo-local, outside `_TRANSPORT_KEYS`, and they run on `git push <url>` (`scratch/probe_r9_dest.py` section 9). Same as a spec D5 pre-push hook: the hook can copy the commit, and the confirmed push's own URL stays the confirmed one.
3. `test_BB6_13` accepts reason `rewrite` or `transport`, so a broken `--null` parser still passes for a local rule that `transport_override` catches.
4. Any repo-local `http.*`, `url.*`, or `include.*`, including a harmless `http.postBuffer` or a shared `include.path`, refuses Publish.
5. A `.git/hooks/pre-push` hook can copy the commit elsewhere (spec D5).
6. A confirmed http(s) server can redirect the pack (`http.followRedirects` defaults to `initial`); the confirming `ls-remote` follows that redirect too.
7. Global and system git config, `GIT_SSH_COMMAND`, and `~/.ssh/config` still steer a push. Stated boundary. `remote.*.proxy`, `remote.*.receivepack`, `remote.*.mirror`, and `remote.*.push` do not apply to a literal URL (probe sections 1–6 and 14).
8. An agent that switches branches blocks its pane, Discard included, until the branch is switched back. Stated choice.

An agent that points the worktree's `commondir` at a feeder will make Publish tell the operator the branch is on the remote he confirmed, with the journal marked done, while the commits are only in another repository.That notification is the earlier one-shot `commondir` feeder, which I killed after it blocked in `git rev-parse`. The later careful run is the one the review cites.

The ship call is unchanged: **FIX FIRST**. A fifo `commondir` makes `wt.push` return success for the confirmed URL while the branch exists only in the other repository.
