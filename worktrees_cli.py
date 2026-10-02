#!/usr/bin/env python3
"""corral-light worktrees — own-branch worktrees, from a terminal (plan F9).

    corral-light worktrees [list] [--json]   every entry: active, trashed,
                                             missing, purged; orphans too
    corral-light worktrees restore <id>      move a discarded worktree back
    corral-light worktrees purge <id> [--confirm <branch>]
                                             really delete a discarded one
                                             (asks you to type the branch)
    corral-light worktrees resolve <id> [--op <op_id>]
                                             show, then settle, an action
                                             whose outcome is unknown

Works on the registry directly (under the same fcntl lock as the hub), so it
runs with the hub down. `list` writes nothing. Nothing here deletes without
the typed branch name, and recovery refs are never deleted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worktrees as wt  # noqa: E402

CMD = "corral-light worktrees"
# What `list --json` and the human list may show: never common_dir.
FIELDS = ("id", "phase", "owner_pane", "path", "subdir", "branch", "repo_top", "base_ref",
          "base_sha", "created", "last_commit", "published", "trash_path", "recovery_refs",
          "error", "unreadable")


class Refused(Exception):
    """A user error: printed, exit 2."""


def tilde(p):
    home = str(Path.home())
    p = str(p or "")
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def short_branch(e):
    return (e.get("branch") or "")[len("refs/heads/"):] or "?"


def pane_state(pane_id):
    """open / archived / gone, from the pane's own meta (read-only)."""
    if not pane_id:
        return None
    try:
        meta = json.loads((wt.state_dir() / "panes" / pane_id / "meta.json").read_text())
    except (OSError, ValueError):
        return "gone"
    return "archived" if meta.get("closed") else "open"


def unknown_ops(e):
    return [o for o in e.get("ops") or [] if o.get("state") == "unknown"]


def get(reg, wt_id):
    try:
        return reg.read(wt_id)
    except (FileNotFoundError, ValueError):
        raise Refused(f"no worktree {wt_id}; `{CMD}` lists them") from None
    except wt.RegistryVersionError as e:
        raise Refused(f"{wt_id}: {e}") from None


def describe(e):
    """The human lines for one entry."""
    if e.get("unreadable"):
        return [f"{e['id']}  unreadable  {e['unreadable']}"]
    out = [f"{e['id']}  {e.get('phase', '?'):<8} {short_branch(e)}  {tilde(e.get('repo_top'))}"]
    bits = []
    if e.get("owner_pane"):
        bits.append(f"pane {e['owner_pane']} ({pane_state(e['owner_pane'])})")
    base = (e.get("base_ref") or "")[len("refs/heads/"):]
    if e.get("base_sha"):
        bits.append(f"cut from {base or 'base'} @ {e['base_sha'][:7]}")
    if e.get("last_commit"):
        bits.append(f"committed {e['last_commit'][:7]}")
    pub = e.get("published") or {}
    if pub.get("url"):
        bits.append(f"pushed to {pub['url']}")
    if pub.get("pr_url"):
        bits.append(f"PR {pub['pr_url']}")
    if bits:
        out.append("    " + " · ".join(bits))
    phase = e.get("phase")
    if phase == "trashed":
        out.append(f"    in trash: {e.get('trash_path')}")
        out.append(f"    restore: {CMD} restore {e['id']}    delete for good: {CMD} purge {e['id']}")
    elif e.get("path") and phase != "purged":
        out.append(f"    worktree: {e['path']}")
    for ref in e.get("recovery_refs") or []:
        out.append(f"    recovery ref: {ref}")
    if e.get("error"):
        out.append(f"    ! {e['error']}")
    n = len(unknown_ops(e))
    if n:
        out.append(f"    ! {n} action{'s' if n > 1 else ''} with an unknown outcome blocks this "
                   f"pane: {CMD} resolve {e['id']}")
    return out


def v_list(a, reg, out, err, stdin):
    entries = reg.all(include_unreadable=True)
    strays = wt.orphans(registry=reg)
    if a.json:
        rows = [{k: e.get(k) for k in FIELDS if k in e} | {"unknownOps": len(unknown_ops(e))}
                for e in entries]
        out.write(json.dumps({"worktrees": rows, "orphans": [str(p) for p in strays]}, indent=2)
                  + "\n")
        return 0
    if not entries and not strays:
        out.write(f"no own-branch worktrees (root: {tilde(wt.worktree_root())})\n")
        return 0
    for e in entries:
        out.write("\n".join(describe(e)) + "\n")
    if strays:
        out.write("orphans (under the worktree root, in no entry; left alone):\n")
        for p in strays:
            out.write(f"    {p}\n")
    return 0


def v_restore(a, reg, out, err, stdin):
    e = get(reg, a.id)
    try:
        e = wt.restore(e, registry=reg)
    except ValueError as x:
        raise Refused(str(x)) from None
    out.write(f"restored {short_branch(e)} to {e['path']}\n")
    if e.get("owner_pane"):
        out.write(f"resume pane {e['owner_pane']} to carry on there\n")
    return 0


def v_purge(a, reg, out, err, stdin):
    e = get(reg, a.id)
    if e.get("phase") != "trashed":
        raise Refused(f"{a.id} is {e.get('phase')}; only a discarded (trashed) worktree can be "
                      f"purged")
    short = short_branch(e)
    out.write(f"purge {a.id}: deletes {e.get('trash_path')} and the branch {short} "
              f"(only if it has not moved since discard)\n")
    for ref in e.get("recovery_refs") or []:
        out.write(f"kept: {ref}\n")
    typed = a.confirm
    if typed is None:
        if not stdin.isatty():
            raise Refused(f"type the branch name to confirm: --confirm {short}")
        out.write(f"type the branch name ({short}) to delete it for good: ")
        out.flush()
        typed = stdin.readline().strip()
    try:
        r = wt.purge(e, typed, registry=reg)
    except ValueError as x:
        raise Refused(str(x)) from None
    out.write(f"purged {short}" + ("" if r["branch_deleted"] else
              "; the branch was kept (it moved since discard, or is gone)") + "\n")
    return 0


def v_resolve(a, reg, out, err, stdin):
    e = get(reg, a.id)
    ops = unknown_ops(e)
    if a.op is None:
        if not ops:
            out.write(f"{a.id}: nothing to resolve\n")
            return 0
        out.write(f"{a.id}: actions whose outcome Corral could not check. Look, then settle "
                  f"each with --op:\n")
        for o in ops:
            facts = {k: v for k, v in o.items() if k not in ("op_id", "op", "state")}
            out.write(f"    {o['op_id']}  {o.get('op')}  {json.dumps(facts, sort_keys=True)}\n")
        return 0
    match = [o for o in e.get("ops") or [] if o.get("op_id") == a.op]
    if not match:
        raise Refused(f"{a.id} has no action {a.op}")
    if match[0].get("state") != "unknown":
        raise Refused(f"{a.op} is {match[0].get('state')}, not unknown; nothing to resolve")
    reg.set_op(a.id, a.op, state="done", stage="resolved_by_user")
    out.write(f"{a.op} marked resolved; the pane can act again\n")
    return 0


def main(argv=None, out=None, err=None, stdin=None):
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    ap = argparse.ArgumentParser(prog=CMD, description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("list", help="every entry and orphan (writes nothing)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("restore", help="move a discarded worktree back")
    s.add_argument("id")
    s = sub.add_parser("purge", help="delete a discarded worktree for good")
    s.add_argument("id")
    s.add_argument("--confirm", metavar="BRANCH", help="the branch name, typed")
    s = sub.add_parser("resolve", help="show or settle actions with an unknown outcome")
    s.add_argument("id")
    s.add_argument("--op", metavar="OP_ID")
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv or ["list"])
    verb = {"list": v_list, "restore": v_restore, "purge": v_purge, "resolve": v_resolve}
    try:
        return verb[a.cmd or "list"](a, wt.Registry(), out, err, stdin)
    except Refused as x:
        err.write(f"{CMD}: {x}\n")
        return 2
    except wt.GitError as x:
        err.write(f"{CMD}: git failed: {x}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
