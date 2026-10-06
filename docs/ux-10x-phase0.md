# 10x UX Phase 0 (WS-B0) — results

Run 2026-10-06 on the Linux host, live hub, plan `docs/ux-10x-plan.md` §3.
No Part B code was written. Scratch repo: a tiny Python package `calc` with
one test file, outside any real repo. Each lane got one own-branch pane and
one ordinary task: add `mul(a, b)` to `calc/ops.py`, export it from
`calc/__init__.py`, add a test in `tests/test_ops.py`, run
`python3 -m unittest` once.

**Two rounds.** In the first, a probe script refused every card. The
operator objected: the transcript showed those refusals as the operator's.
That led to the `via` label on permission answers (wall, terminal, script),
and the round's card counts stop at the first refusal. In the second round
the operator answered every card on the wall, and a recorder that never
answers cards logged them. Every answer in round two carries `via: wall`.

**Verdict.** Part B is worth building. Its auto-allow pays off mainly on
Grok, which has no `edits` posture. Claude's `edits` posture already gives
the same reduction without B. Codex raises no edit cards to remove. Every
lane's edit cards that exist carry a reliable absolute path.

Paths below are written relative to the pane's worktree, `<wt>`. Every path
the lanes reported was absolute.

## What an edit permission request carries

| Lane, posture | Edit cards raised | `kind` | `locations` | `content` diff path | `rawInput` keys | Options offered (after the hub strips allow-always) |
|---|---|---|---|---|---|---|
| Claude, strict | yes, one per edit | `edit` | `<wt>/calc/ops.py` | same path | `file_path`, `old_string`, `new_string`, `replace_all` | allow_once, reject_once |
| Claude, edits | none: the posture auto-accepts edits in cwd | — | — | — | — | — |
| Grok, strict | yes, one per edit | `edit` | **empty** | **none** (no diff item) | `file_path`, `old_string`, `new_string`, `replace_all`, `variant` | allow_once, reject_once |
| Codex (no enforceable posture) | **none**: its sandbox applies edits and runs the tests unasked | — | — | — | — | — |

Grok's shell card differs from its edit card: `rawInput` keys `command`,
`description`, `is_background`, `variant`, and it also offers
`reject_always`, which the hub does not strip.

Tool events, which `_worktree_guard` reads after the fact:

| Lane | Edit tool event carries a path |
|---|---|
| Claude | yes, in `locations` and the diff item, on a later update of the same tool call |
| Grok | yes, in `locations` and the diff item |
| Codex | no: "Editing files" with no locations, as in the worktree Phase 0 |

## Cards per finished task (round two, operator answering)

| Lane, posture | Cards | Edits | Shell | With Part B (estimate) | Outcome |
|---|---|---|---|---|---|
| Grok, strict | 5 | 4 | 1 | 1 shell card + 1 review card | all allowed once; tests pass; three files changed |
| Claude, strict | 4 | 3 | 1 | 1 shell card + 1 review card | all allowed once; tests pass; three files changed |
| Claude, edits (round one) | 1 | 0 | 1 | unchanged, plus 1 review card | three files written without cards |
| Codex (round one) | 0 | 0 | 0 | 0, plus 1 review card | three files written and tests run without cards |

Grok edited `tests/test_ops.py` twice, so it raised four edit cards for
three files. The estimates assume every edit card passes §2.2's in-tree test,
which every card recorded here would.

Nothing was written outside the worktrees. The scratch repo's main checkout
stayed clean in every run.

## What this means for Part B

1. **Grok is where the auto-allow pays off.** Corral cannot give Grok an
   `edits` posture (its launcher realises only `strict` and `auto`), so
   today every Grok edit is a card: four of five cards on this task. Its
   edit cards carry the path only in `rawInput.file_path`; `locations` is
   empty and there is no diff item. §2.2's `_in_tree_write` already reads
   `rawInput.file_path`, so Grok passes. Its extra key `variant` held
   `"SearchReplace"`, which is not path-like, so the unknown-key rule does
   not trip on it.
2. **Claude: the `edits` posture already gives B's main benefit.** It cut
   three edit cards to zero and left the shell card. What B adds for Claude
   is the hub-verified path check, the `permission_auto` transcript record
   and the blocking review card.
3. **Codex gains nothing from the auto-allow.** It raises no edit cards, and
   its tool events carry no paths, so the hub could not verify them anyway.
   B's blocking review card still matters for Codex, since nothing else
   puts its in-tree edits in front of the operator.
4. **Paths are reliable where they exist.** Claude names the same absolute
   path in three places; Grok in one. Neither reported a relative path.
5. **No lane fails the §4 step 2 stop condition.** Every edit card seen
   carried a usable path. Codex is excluded by having no cards, not by bad
   paths.

## Other observations

- **Grok ends its turn on a refused edit.** In round one the hub sent no
  cancel; the lane itself ended the turn `cancelled` right after the
  refusal. Seen once.
- **A background pane surfaces when a card arrives.** Every TEST pane was
  opened in the background and was restored on the wall at its first card,
  as designed.
- **The hub ignored the requested pane title on Claude worktree panes**,
  which showed as `repo`. Not investigated.

## Leftovers on disk

- Six worktrees `test-b0-*` registered to the scratch repo under the
  worktree root. Four carry uncommitted edits from the runs that finished.
- All TEST panes are closed.
- The probe and recorder scripts and the raw records sit beside the scratch
  repo, outside this repository, because they carry local paths.
