# merge-tree fixtures (Phase 0, item 0.3)

`git merge-tree --write-tree -z --name-only main <branch>` on a three-branch
scratch repo, captured on git 2.38.5 (built from source) and git 2.55.0.
Output bytes and exit codes are identical across the two versions.

| Case | Branch does | rc |
|---|---|---|
| clean | adds `g.txt` | 0 |
| conflict | edits line 2 of `f.txt`, as main does | 1 |
| rendel | renames `r.txt` to `r2.txt`; main deletes `r.txt` | 1 |

Record layout (`-z`): the tree OID, NUL; then each conflicted path, NUL; then
an empty NUL that closes that section; then informational messages, each as
`<path count> NUL <path> NUL … <type> NUL <message> NUL`. A conflicted
tree's OID is never a result.
