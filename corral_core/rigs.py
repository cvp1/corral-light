#!/usr/bin/python3
"""rigs — a saved set of seats, brought back with one verb (DESIGN-5 S12).

A RIG IS
    `$STATE/rigs/<name>.toml`, `version = 1`, one `[[seat]]` per pane:

        [[seat]]
        id      = "reviewer"            # the seat name (the pane's address)
        agent   = "claude"              # the lane
        cwd     = "/path/to/repo"       # an existing directory
        posture = "strict"              # optional; the pane's permission mode
        role    = "reviewer"            # optional; only where the product has roles
        prompt  = "Read the diff."      # optional, hand-written; the opening turn

    `rig save <name>` writes it from the seated panes on the roster — agent,
    cwd, seat, posture and role; never a prompt. A prompt is only ever
    something a human typed into the file.

A RIG IS NOT
    A topology, a culture file or a snapshot. No edges, no routing, nothing
    beyond the meta each pane already keeps. Nothing in it grants authority:
    every pane it starts goes through the same create(), the same lane checks
    and the same permission rail as a click.

`rig up <name>`
    1. PREFLIGHT refuses the whole file before any create() — parse, version,
       grammar, unknown key, unknown agent, cwd not a directory, too many
       seats, a seat twice in the file, a role that does not resolve, a seat
       name already live. Every reason is returned, not just the first (P4).
    2. Then per seat, in file order, exactly one outcome:
         resumed       an open pane already held the seat; its lane reloaded
                       the conversation (session/load)
         rebuilt       an open pane held the seat; its meta and transcript are
                       back but the lane could not reload the conversation
         started-fresh no pane held the seat: a new pane, seated
         fresh-primed  ...and the rig's opening prompt was sent to it
         withheld      the seat was taken between the check and this seat
         not-restored  the product's pane cap would be exceeded, or the pane
                       holding the seat is on disk but not on this roster
         failed        the pane could not be started or resumed (reason given)
       Nothing rolls back: a seat that failed does not undo the ones before
       it, and every outcome says what is now true.

AN OPENING PROMPT IS THE HUMAN'S TURN (P17, P20)
    It goes through the pane's own `send()` with `via: rig` — never a peer
    message, never a system instruction. It is sent only to a pane this rig
    STARTED; a resumed conversation is not re-prompted. A seat with a role and
    a prompt sends the role's instructions then the prompt, as a role's first
    turn always is; a role with no prompt sends nothing — the role file's
    bytes were not written into the rig by the human who ran it.

BOUNDS (P8): MAX_RIG_SEATS seats, MAX_RIG_PROMPT characters per prompt,
MAX_RIG_BYTES per file, MAX_RIGS files.

REMOVAL (P23): delete this module, tomlmini.py's rig use, the verbs, and the
`rigs/` directory. Trigger: DESIGN-5 §5.
"""
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from corral_core import sessions as _s
from corral_core import tomlmini

RIG_VERSION = 1
MAX_RIG_SEATS = _s.MAX_PANES        # a rig bigger than the live cap cannot come up
MAX_RIG_PROMPT = 8000               # the smaller of the two prompt caps (a
                                    # scheduled prompt's; a live pane takes more)
MAX_RIG_BYTES = 128 * 1024          # 12 seats x 8,000 chars of prompt, with room
MAX_RIGS = 100                      # files in the directory: a list, not a store
TOP_KEYS = ("version", "seat")
SEAT_KEYS = ("id", "agent", "cwd", "posture", "role", "prompt")
OUTCOMES = ("resumed", "rebuilt", "started-fresh", "fresh-primed", "withheld",
            "not-restored", "failed")
LIVE_EXCLUDES = ("dead", "detached")

# One rig verb at a time per hub: two concurrent `up`s would each pass
# preflight for the same seat and race to create it.
_LOCK = threading.Lock()


class RigRefused(ValueError):
    """Preflight said no. `.reasons` is every reason, in file order."""

    def __init__(self, name, reasons):
        self.name, self.reasons = name, list(reasons)
        super().__init__(f"rig {name!r} refused, nothing was started:"
                         + "".join(f"\n  - {r}" for r in self.reasons))


def rigs_dir():
    return Path(_s.STATE) / "rigs"


def check_name(name):
    """A rig name follows the seat grammar: it is also a filename, so the
    grammar is what keeps it inside rigs/."""
    if not isinstance(name, str) or not _s.SEAT_RE.match(name.strip()):
        raise ValueError(_s.SEAT_RULE.replace("a seat", "a rig name")
                         + (f" — not {name[:40]!r}" if isinstance(name, str) else ""))
    return name.strip()


def _path(name):
    return rigs_dir() / f"{check_name(name)}.toml"


def _read(name):
    """-> (text, doc). Raises ValueError with the reason."""
    path = _path(name)
    if not path.is_file():
        known = ", ".join(r["name"] for r in list_rigs()) or "none"
        raise ValueError(f"no rig {name!r} — saved rigs: {known}")
    size = path.stat().st_size
    if size > MAX_RIG_BYTES:
        raise ValueError(f"{path.name} is {size} bytes; the cap is {MAX_RIG_BYTES}")
    try:
        text = path.read_text(encoding="utf-8")
        return text, tomlmini.loads(text)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"{path.name} is not valid TOML: {e}") from None


def list_rigs():
    """Every rig file, with its seats, or the reason it does not read."""
    d = rigs_dir()
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.toml"))[:MAX_RIGS]:
        row = {"name": f.stem}
        try:
            if not _s.SEAT_RE.match(f.stem):
                raise ValueError("the file name is not a rig name")
            size = f.stat().st_size
            if size > MAX_RIG_BYTES:
                raise ValueError(f"{size} bytes; the cap is {MAX_RIG_BYTES}")
            doc = tomlmini.loads(f.read_text(encoding="utf-8"))
            seats = doc.get("seat") if isinstance(doc, dict) else None
            row["seats"] = [str(s.get("id")) for s in seats
                            if isinstance(s, dict)] if isinstance(seats, list) else []
        except (OSError, ValueError, UnicodeDecodeError) as e:
            row["error"] = str(e)[:200]
        out.append(row)
    return out


def rm(name):
    path = _path(name)
    with _LOCK:
        if not path.is_file():
            raise ValueError(f"no rig {name!r}")
        path.unlink()
    return check_name(name)


# ── save ─────────────────────────────────────────────────────────────────
def _roster(mgr):
    """The roster in its displayed order: pinned, explicit order, age."""
    panes = list(mgr.panes.values())
    panes.sort(key=lambda p: (0 if getattr(p, "pinned", False) else 1,
                              p.order if getattr(p, "order", None) is not None
                              else 10_000,
                              getattr(p, "created", "") or ""))
    return panes


def compose(seats, note=None):
    """The file `rig save` writes, as text. Every value through tomlmini.basic."""
    lines = ["# A Corral rig. `rig up` checks the whole file before it starts "
             "anything.",
             "# Hand-edit freely: add `prompt = \"...\"` to a seat to send it "
             "as that pane's",
             "# first turn when the rig starts it fresh."]
    if note:
        lines.append(f"# {note}")
    lines += ["", f"version = {RIG_VERSION}"]
    for s in seats:
        lines += ["", "[[seat]]"]
        for k in SEAT_KEYS:
            if s.get(k) is not None:
                lines.append(f"{k:<7} = {tomlmini.basic(s[k])}")
    return "\n".join(lines) + "\n"


def save(mgr, name, replace=False):
    """Write `name` from every seated pane on the roster (a withheld seat is
    not the pane's to save). Refuses to overwrite unless `replace`."""
    name = check_name(name)
    path = _path(name)
    seats = []
    for p in _roster(mgr):
        if not getattr(p, "seat", None) or getattr(p, "seat_withheld", False):
            continue
        s = {"id": p.seat, "agent": p.agent, "cwd": str(p.cwd)}
        if getattr(p, "posture", None):
            s["posture"] = p.posture
        if getattr(p, "role", None):
            s["role"] = p.role
        seats.append(s)
    if not seats:
        raise ValueError("no seated panes to save — name a pane first (seat)")
    if len(seats) > MAX_RIG_SEATS:
        raise ValueError(f"{len(seats)} seated panes; a rig holds at most "
                         f"{MAX_RIG_SEATS}")
    text = compose(seats, note="saved " + datetime.now(timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ"))
    if len(text.encode("utf-8")) > MAX_RIG_BYTES:
        raise ValueError(f"the rig would be over {MAX_RIG_BYTES} bytes")
    with _LOCK:
        d = rigs_dir()
        if path.exists() and not replace:
            raise ValueError(f"rig {name!r} exists — remove it first, or save "
                             f"with replace")
        if not path.exists() and len(list(d.glob("*.toml")) if d.is_dir() else []) >= MAX_RIGS:
            raise ValueError(f"{MAX_RIGS} rigs are saved; remove one first")
        d.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return {"name": name, "seats": [s["id"] for s in seats], "bytes": len(text)}


# ── up ───────────────────────────────────────────────────────────────────
def _is_live(p):
    return p.state not in LIVE_EXCLUDES


def _holder(mgr, seat):
    """The pane on this roster answering to `seat`, or None."""
    for p in list(mgr.panes.values()):
        if p.seat == seat and not getattr(p, "seat_withheld", False):
            return p
    return None


def preflight(mgr, name, doc):
    """-> the plan (one dict per seat), or RigRefused with every reason.
    Touches nothing: no process, no file, no pane."""
    reasons = []
    if not isinstance(doc, dict):
        raise RigRefused(name, ["the file is not a table"])
    for k in doc:
        if k not in TOP_KEYS:
            reasons.append(f"unknown key {k!r} (a rig has: {', '.join(TOP_KEYS)})")
    if doc.get("version") != RIG_VERSION:
        reasons.append(f"version = {doc.get('version')!r}; this reads "
                       f"version = {RIG_VERSION}")
    seats = doc.get("seat")
    if not isinstance(seats, list) or not seats:
        reasons.append("no [[seat]] entries")
        raise RigRefused(name, reasons)
    if len(seats) > MAX_RIG_SEATS:
        reasons.append(f"{len(seats)} seats; a rig holds at most {MAX_RIG_SEATS} "
                       f"(the live pane cap)")
    plan, seen = [], set()
    for i, s in enumerate(seats, 1):
        where = f"seat {i}"
        if not isinstance(s, dict):
            reasons.append(f"{where}: not a table")
            continue
        for k in s:
            if k not in SEAT_KEYS:
                reasons.append(f"{where}: unknown key {k!r} (a seat has: "
                               f"{', '.join(SEAT_KEYS)})")
        for k in s:
            if k in SEAT_KEYS and not isinstance(s[k], str):
                reasons.append(f"{where}: {k} must be a string")
        seat = s.get("id")
        try:
            seat = _s.check_seat(seat if isinstance(seat, str) else None)
            if seat is None:
                raise ValueError("id is required")
        except ValueError as e:
            reasons.append(f"{where}: {e}")
            seat = None
        if seat:
            where = f"@{seat}"
            if seat in seen:
                reasons.append(f"{where}: named twice in this rig")
            seen.add(seat)
            h = _holder(mgr, seat)
            if h is not None and _is_live(h):
                reasons.append(f"{where}: already live on pane {h.id} "
                               f"({h.title}) — pause or close it, or remove the "
                               f"seat from the rig")
        agent = s.get("agent")
        if not isinstance(agent, str) or agent not in (_s.AGENTS or {}):
            reasons.append(f"{where}: unknown agent {agent!r}")
            agent = None
        cwd = s.get("cwd")
        cwd_p = Path(cwd).expanduser() if isinstance(cwd, str) and cwd.strip() else None
        if cwd_p is None or not cwd_p.is_dir():
            reasons.append(f"{where}: cwd is not a directory: {cwd!r}")
        posture = s.get("posture")
        if posture is not None and posture not in _s.POSTURES:
            reasons.append(f"{where}: unknown posture {posture!r} — one of "
                           f"{', '.join(sorted(_s.POSTURES))}")
        prompt = s.get("prompt")
        if isinstance(prompt, str):
            prompt = prompt.strip() or None
            if prompt and len(prompt) > MAX_RIG_PROMPT:
                reasons.append(f"{where}: prompt is {len(prompt)} characters; "
                               f"the cap is {MAX_RIG_PROMPT}")
        role, resolved, opening = s.get("role"), None, prompt
        if role is not None:
            if _s.ROLE_RESOLVER is None:
                reasons.append(f"{where}: role {role!r} — this product has no "
                               f"roles")
            elif agent:
                try:
                    resolved = _s.ROLE_RESOLVER(role, agent, posture)
                    opening = resolved["compose"](prompt) if prompt else None
                except Exception as e:                      # noqa: BLE001
                    reasons.append(f"{where}: role {role!r} does not resolve: "
                                   f"{str(e)[:200]}")
        plan.append({"seat": seat, "agent": agent,
                     "cwd": str(cwd_p) if cwd_p else None,
                     "posture": (resolved or {}).get("posture") or posture
                     or _s.DEFAULT_POSTURE,
                     "effort": (resolved or {}).get("effort"),
                     "role": role if resolved else None,
                     "role_sha": (resolved or {}).get("sha"),
                     "notes": list((resolved or {}).get("notes") or []),
                     "prompt": opening,
                     "role_unsent": bool(resolved and not prompt)})
    if reasons:
        raise RigRefused(name, reasons)
    return plan


def _live_count(mgr):
    return sum(1 for p in list(mgr.panes.values()) if _is_live(p))


def _cap_refusal(mgr, new_pane):
    if _live_count(mgr) >= _s.MAX_PANES:
        return f"{_s.MAX_PANES} live panes is the cap"
    if new_pane and _s.ROSTER_CAP is not None and len(mgr.panes) >= _s.ROSTER_CAP:
        return f"{_s.ROSTER_CAP} panes are on the roster, the cap"
    return None


def _outcome(step, outcome, why="", pane=None, **extra):
    o = {"seat": step["seat"], "agent": step["agent"], "cwd": step["cwd"],
         "outcome": outcome, "why": why, "pane": getattr(pane, "id", None)}
    o.update(extra)
    return o


def _resume(mgr, step, p):
    notes = []
    if p.agent != step["agent"]:
        notes.append(f"the pane runs {p.agent}, not {step['agent']} as the rig "
                     f"says; it was resumed as it is")
    if step["prompt"]:
        notes.append("the opening prompt was not sent: this conversation "
                     "already exists")
    if not p.acp_session:
        return _outcome(step, "rebuilt", "; ".join(
            ["the transcript is back, but the pane has no agent session to "
             "reload"] + notes), p)
    cap = _cap_refusal(mgr, new_pane=False)
    if cap:
        return _outcome(step, "not-restored", cap + " — resume it by hand when "
                        "there is room", p)
    seq0 = p.events[-1]["seq"] if p.events else 0
    try:
        p.resume()
    except Exception as e:                                  # noqa: BLE001
        return _outcome(step, "failed", f"resume refused: {str(e)[:200]}", p)
    if p.state == "dead":
        return _outcome(step, "rebuilt", "; ".join(
            [f"the transcript is back; the lane did not reload the "
             f"conversation: {(p.error or '')[:200]}"] + notes), p)
    lost = any(e.get("kind") == "note" and (e.get("data") or {}).get("contextLost")
               for e in list(p.events) if e.get("seq", 0) > seq0)
    if lost:
        return _outcome(step, "rebuilt", "; ".join(
            ["the lane reloaded the session but says the model starts "
             "fresh"] + notes), p)
    return _outcome(step, "resumed", "; ".join(notes), p)


def _start(mgr, step):
    cap = _cap_refusal(mgr, new_pane=True)
    if cap:
        return _outcome(step, "not-restored", cap)
    try:
        p = mgr.create(step["agent"], step["cwd"], step["posture"], None,
                       step["effort"], role=step["role"], role_sha=step["role_sha"])
    except Exception as e:                                  # noqa: BLE001
        return _outcome(step, "failed", f"could not start: {str(e)[:200]}")
    if p.state == "dead":
        return _outcome(step, "failed", f"the agent did not start: "
                        f"{(p.error or '')[:200]}", p)
    try:
        mgr.bind_seat(p.id, step["seat"])
    except ValueError as e:
        return _outcome(step, "withheld", f"started, but not seated: {e}", p)
    notes = list(step["notes"])
    if step["role_unsent"]:
        notes.append(f"role {step['role']}'s instructions were not sent: the "
                     f"rig has no opening prompt for this seat")
    if not step["prompt"]:
        return _outcome(step, "started-fresh", "; ".join(notes), p)
    try:
        turn = p.send(step["prompt"], via="rig")
    except Exception as e:                                  # noqa: BLE001
        return _outcome(step, "started-fresh", "; ".join(
            [f"the opening prompt was NOT sent: {str(e)[:200]}"] + notes), p)
    return _outcome(step, "fresh-primed", "; ".join(notes), p, turn=turn)


def _step(mgr, step):
    h = _holder(mgr, step["seat"])
    if h is not None:
        if _is_live(h):
            return _outcome(step, "withheld", f"pane {h.id} went live holding "
                            f"@{step['seat']} after the check; left as it is", h)
        return _resume(mgr, step, h)
    on_disk = [m for m in _s.open_metas() if m.get("seat") == step["seat"]
               and m["id"] not in mgr.panes]
    if on_disk:
        return _outcome(step, "not-restored", f"pane {on_disk[0]['id']} holds "
                        f"@{step['seat']} on disk but is not on this roster "
                        f"(the restore cap); reopen it by hand")
    return _start(mgr, step)


def up(mgr, name, by=None):
    """Preflight the whole rig, then bring each seat up in file order.
    -> {"rig", "outcomes", "lines"}. RigRefused before anything is touched."""
    name = check_name(name)
    with _LOCK:
        try:
            _text, doc = _read(name)
        except ValueError as e:
            raise RigRefused(name, [str(e)]) from None
        plan = preflight(mgr, name, doc)
        outcomes = []
        for step in plan:
            try:
                o = _step(mgr, step)
            except Exception as e:                          # noqa: BLE001
                o = _outcome(step, "failed", f"{type(e).__name__}: {str(e)[:200]}")
            outcomes.append(o)
            p = mgr.panes.get(o["pane"]) if o["pane"] else None
            if p is not None:
                p.emit("note", {"text": f"rig {name}: {render(o)}"}, activity=False)
    print(f"corral: rig up {name} by {by or 'local'}: "
          + ", ".join(f"@{o['seat']} {o['outcome']}" for o in outcomes),
          file=sys.stderr, flush=True)
    return {"rig": name, "outcomes": outcomes,
            "lines": [render(o) for o in outcomes]}


def render(o):
    """One roster line per seat — the one rendering every surface shows."""
    line = f"@{o['seat']}  {o['outcome']}  {o['agent']}"
    if o.get("pane"):
        line += f"  pane {o['pane']}"
    if o.get("turn"):
        line += f"  turn {o['turn']}"
    if o.get("why"):
        line += f" — {o['why']}"
    return line


def resolve_with(roles_mod, role_id, agent, posture):
    """The ROLE_RESOLVER both products inject, over their own `roles` module
    (same `resolve` / `compose` shape in each): the role's preset for this
    seat's lane, and how its first turn is composed."""
    r = roles_mod.resolve(role_id, lane=agent, posture=posture)
    return {"agent": r.agent, "posture": r.posture, "effort": r.effort,
            "sha": r.sha256, "notes": list(r.notes or []),
            "compose": lambda prompt: roles_mod.compose(r.preamble, prompt)}


# ── one HTTP surface for both hubs, behind their pairing check ───────────
def route(mgr, method, path, body=None, by=None):
    """-> (status, payload), or None for a path that is not a rig route.
    Called only AFTER the hub's auth check (401 before routing)."""
    body = body or {}
    if method == "GET" and path == "/api/session/rigs":
        return 200, {"rigs": list_rigs(), "dir": "rigs/"}
    if method != "POST" or path not in ("/api/session/rigs/save",
                                        "/api/session/rigs/up",
                                        "/api/session/rigs/rm"):
        return None
    try:
        if path == "/api/session/rigs/save":
            return 200, {"ok": True, **save(mgr, body.get("name", ""),
                                            replace=bool(body.get("replace")))}
        if path == "/api/session/rigs/rm":
            return 200, {"ok": True, "removed": rm(body.get("name", ""))}
        return 200, {"ok": True, **up(mgr, body.get("name", ""), by=by)}
    except RigRefused as e:
        return 400, {"error": str(e)[:2000], "refused": e.reasons}
    except ValueError as e:
        return 400, {"error": str(e)[:400]}
