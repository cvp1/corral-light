#!/usr/bin/python3
"""key_cli — the shell half of pairing (DESIGN-6 S7).

    corral-light pair [--break-glass] <code>
    corral-light key list
    corral-light key enroll
    corral-light key rm <id>
    corral-light key policy <code|key-or-code|key-only>
    corral-light key recover <pair-code> (<id>... | --all)

`rm` and `policy` exist only here: the hub has no route for them. Every
security event lands in the state dir's key-ledger.jsonl. Running this
proves only that you are this UNIX user -- which is what pairing has always
meant (auth.py's docstring); a process running as that user can do all of
it, so `key-only` is a workflow guard, not a boundary.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth  # noqa: E402


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "never"


def cmd_list(_a):
    keys, err = auth.load_keys()
    pol, perr = auth.policy()
    state, why = auth.verifier_state()
    print(f"policy:   {pol}" + (f"  ({perr})" if perr else ""), flush=True)
    print(f"verifier: {state}  ({why})", flush=True)
    if state != "ok" and pol == "key-only":
        print("          key pairing cannot verify; only "
              "`corral-light pair --break-glass <code>` pairs until it can", flush=True)
    if err:
        print(f"keys:     ERROR -- {err}", flush=True)
        return 1
    if not keys:
        print("keys:     none enrolled (corral-light key enroll)", flush=True)
        return 0
    for k in keys:
        print(f"  {k['id']}  {k.get('label') or ''!s:<20}  {k['origin']}  "
              f"enrolled {_when(k.get('enrolledAt'))}  last used {_when(k.get('lastUsed'))}", flush=True)
    return 0


def cmd_enroll(_a):
    keys, err = auth.load_keys()
    if err:
        print(f"refused: {err}", flush=True)
        return 2
    code, ttl = auth.mint_enroll_code()
    print(f"enrollment code {code} -- good for {ttl}s, once.\n"
          f"In the browser: the Security keys button at the foot of the "
          f"left rail -> enter it -> Enroll."
          + ("" if not keys else "\n(Only a first key for an origin needs it; "
             "another key is approved by touching an enrolled one.)"), flush=True)
    return 0


def cmd_rm(a):
    ok, msg = auth.remove_key(a.id)
    print(msg, flush=True)
    return 0 if ok else 2


def cmd_policy(a):
    ok, msg = auth.set_policy(a.policy)
    print(msg, flush=True)
    return 0 if ok else 2


def cmd_recover(a):
    if a.all == bool(a.ids):
        print("name the lost key ids, or --all", flush=True)
        return 2
    ids = [k["id"] for k in auth.load_keys()[0]] if a.all else a.ids
    ok, lines = auth.recover(a.code, ids)
    print("\n".join(lines), flush=True)
    return 0 if ok else 2


def cmd_pair(a):
    ok, msg = auth.approve(a.code, break_glass=a.break_glass)
    if ok and a.break_glass:
        msg += " (break-glass, recorded in key-ledger.jsonl)"
    print(msg, flush=True)
    return 0 if ok else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corral-light")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("pair", help="authorize the browser showing <code>")
    s.add_argument("--break-glass", action="store_true",
                   help="pair even under policy key-only (audited)")
    s.add_argument("code", nargs="?", default="")
    s.set_defaults(fn=cmd_pair)
    k = sub.add_parser("key", help="security keys")
    ks = k.add_subparsers(dest="key_cmd", required=True)
    ks.add_parser("list").set_defaults(fn=cmd_list)
    ks.add_parser("enroll").set_defaults(fn=cmd_enroll)
    x = ks.add_parser("rm")
    x.add_argument("id")
    x.set_defaults(fn=cmd_rm)
    x = ks.add_parser("policy")
    x.add_argument("policy", choices=auth.POLICIES)
    x.set_defaults(fn=cmd_policy)
    x = ks.add_parser("recover")
    x.add_argument("code")
    x.add_argument("ids", nargs="*")
    x.add_argument("--all", action="store_true")
    x.set_defaults(fn=cmd_recover)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
