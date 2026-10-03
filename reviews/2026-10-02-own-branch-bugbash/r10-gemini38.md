SHIP — All prior blocking issues across rounds 1–9 are resolved, and no defects meeting the ship bar remain.

## BLOCKING FINDINGS

None.

## NON-BLOCKING

1. Any repository-scoped `http.*`, `url.*`, or `include.*` setting (such as `http.postBuffer` or team `include.path`) refuses Publish with `Refused("transport")`, requiring migration to `~/.gitconfig`.
2. A `.git/hooks/pre-push` hook or `hook.*.command` can mirror or copy commits during push (permissible under spec D5; confirmed push destination remains unchanged).
3. Pushes to HTTP/HTTPS remotes that redirect (where `http.followRedirects` defaults to `initial`) are followed transparently, and the confirming `ls-remote` follows the redirect as well.
4. Global/system git configurations, `GIT_SSH_COMMAND`, and `~/.ssh/config` govern transport routing by design.
5. In `test_BB6_13` ([test_worktrees.py:3415](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r10/gemini38/corral-light/test_worktrees.py#L3415)), the assertion `assertIn(cm.exception.reason, ("rewrite", "transport"))` permits `transport` as a fallback, though `rewrite_rule` independently catches and flags the rule as `rewrite`.
6. Processes whose `/proc` cwd and file descriptors are unreadable (e.g. non-dumpable foreign user processes) are skipped by Discard's safety scan.
7. An agent checking out another branch inside its worktree blocks the pane, including Discard, until switched back to the pane's assigned branch.
8. A transient config mutation during `git push` that is restored prior to the second fingerprint check without using a FIFO (a theoretical microsecond race) is not caught by `config_fingerprint`.

## ONE SENTENCE

Developers whose repositories rely on local `.git/config` settings like `include.path`, `url.<base>.insteadOf`, or `http.*` configurations will find Publish refused and will need to move those settings to their global `~/.gitconfig`.
