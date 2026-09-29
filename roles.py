#!/usr/bin/python3
"""roles — a named preset for starting a conversation, and nothing more.

Ported from full Corral's roles.py (2026-09-10 design, reviewed by GPT-6-Astra
and Grok-4.6) for Corral Light on 2026-09-29, resilience review §3. What it
is and is not is unchanged, and it is the point of the thing:

A ROLE IS
    A named preset over the pane-creation call (lane, effort, posture) plus a
    PREAMBLE — the role's instructions — that becomes the first bytes of the
    first prompt. One click to open "reviewer on Grok, strict" instead of
    five fields.

A ROLE IS NOT
    * Authority. Same lanes, same permission rail. A role cannot grant a
      tool, widen a posture, or answer a permission.
    * A schedule, a model pin, a working directory, a file contract or a
      persona — each refused BY NAME in the file (REFUSED_KEYS says why).
    * A system prompt. The preamble lands as user turn 0 under the vendor's
      own system prompt; `role_delivery = "preamble"` records that.

CONSENT BINDS TO BYTES (P17)
    A role is resolved ONCE, when the pane is started, and its digest (TOML +
    preamble) is stored on the pane (`role_sha`). In Light the preamble goes
    INTO THE COMPOSER, visible, and nothing is sent until you press send.

WHAT CHANGED IN THE PORT
    * No data-class gate. Full Corral asks _lib/merit_policy whether a
      role's data class may reach a lane's vendor; Light is standalone and
      ships no trust registry, the same stance the core takes for
      TRANSFER_GATE. `data_class` is still required and validated, recorded
      on the role, and SAID in the picker — never silently treated as
      enforced.
    * Roles live in the operator's config dir (CORRAL_LIGHT_ROLES_DIR,
      default ~/.config/corral-light/roles), not in this public repository.
    * TOML: `tomllib` where it exists (3.11+); on 3.9/3.10 a strict reader for
      the only shape a role file has — flat `key = "string"` / integer lines
      and comments — that REFUSES anything else rather than guessing.

BOUNDS (P8): MAX_ROLES files, MAX_ROLE_BYTES per TOML, MAX_PREAMBLE_BYTES per
preamble, and MIN_ASK_ROOM characters left under the tightest prompt cap.

Run: python3 roles.py {list,show,check,resolve,create} …
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MAX_ROLES = 40              # a picker longer than this is not a picker
MAX_ROLE_BYTES = 8 * 1024   # a role file is a handful of keys; 8 KB is 20x that
MAX_PREAMBLE_BYTES = 6000   # instructions, not a document — and must fit the
                            # scheduler's 8,000-char prompt cap with room to ask
MIN_ASK_ROOM = 1500         # chars that must remain under the prompt cap for the ask
SEPARATOR = "\n\n---\n\n"

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
RESERVED_IDS = {"none", "any", "role"}
FIELD_CAPS = {"description": 120, "personality": 800, "does": 2000,
              "expects": 1500}
DESCRIPTION_MIN = 8

PREAMBLE_TEMPLATE_V1 = """You are working as `{id}`: {description}

## How you work
{personality}

## What you do
{does}

## What a finished answer looks like
{expects}

Report plainly anything you were asked for that you could not do or verify,
rather than presenting it as done."""

SCHEMA = 1
DATA_CLASSES = ("public", "internal", "sensitive")
ALLOWED_KEYS = {"schema", "description", "lane", "effort", "posture",
                "data_class", "prompt_file"}
REFUSED_KEYS = {
    "schedule": "cadence belongs to the job, not the role — schedule it with "
                "`corral-light later`; a file with a cadence would fire "
                "wherever it is copied and never decay",
    "inputs": "a declared input is ceremony until something validates it; "
              "pass paths in the prompt",
    "outputs": "a declared output is a promise nobody keeps until an artifact "
               "check exists; pass paths in the prompt",
    "cwd": "the dialog owns the working directory",
    "name": "the filename is the id; a second name only drifts",
    "model": "a role pins a lane, not a model — a stale pin outlives the model",
    "persona": "personas are not built (one object, not three)",
    "default_fork_context": "Corral panes do not fork a parent conversation",
    "default_capability_mode": "Corral cannot enforce a capability mode; "
                               "posture is the only control it has",
}
REFUSED_LANE_PREFIX = {
    "host:": "a host: lane is a plain ssh shell — no model and no rail; the "
             "typed command IS the approved artifact (P17). A role has "
             "nothing to preset there.",
}
DATA_CLASS_NOTE = ("Corral Light has no data-class registry: this role's "
                   "data class is recorded, not enforced on the lane")


class RoleError(ValueError):
    """A role could not be read, or could not be applied to this lane.
    Raised at the dialog / CLI, never at fire time."""


# ── TOML, 3.9-safe ───────────────────────────────────────────────────────
_LINE = re.compile(r'^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.+?)\s*$')


def _parse_toml(text):
    try:
        import tomllib                                  # 3.11+
    except ImportError:
        return _parse_flat(text)
    return tomllib.loads(text)


def _parse_flat(text):
    """The 3.9/3.10 reader: flat `key = "string" | integer` lines only."""
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"line {n}: only `key = value` lines are allowed "
                             f"in a role file on this Python")
        key, val = m.group(1), m.group(2)
        if key in out:
            raise ValueError(f"line {n}: duplicate key {key!r}")
        if val.startswith('"'):
            try:
                s = json.loads(val)             # TOML basic strings ⊂ JSON strings
            except ValueError:
                raise ValueError(f"line {n}: not a plain quoted string") from None
            if not isinstance(s, str):
                raise ValueError(f"line {n}: not a string")
            out[key] = s
        elif re.fullmatch(r"-?\d+", val):
            out[key] = int(val)
        else:
            raise ValueError(f"line {n}: value must be a quoted string or an integer")
    return out


# ── the role object ──────────────────────────────────────────────────────
def _postures():
    try:
        import sessions as _s                           # noqa: WPS433
        return set(_s.POSTURES)
    except Exception:                                   # noqa: BLE001
        return {"strict", "edits", "auto"}


def _valid_posture(value, where):
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if v not in _postures():
        raise RoleError(f"{where}: unknown posture {v!r} — one of "
                        f"{', '.join(sorted(_postures()))}")
    return v


class Role:
    __slots__ = ("id", "description", "lane", "effort", "posture",
                 "data_class", "prompt_file", "preamble", "sha256", "path")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self):
        return {"id": self.id, "description": self.description,
                "lane": self.lane, "effort": self.effort,
                "posture": self.posture, "data_class": self.data_class,
                "prompt_file": self.prompt_file,
                "preamble_bytes": len(self.preamble.encode("utf-8")),
                "sha256": self.sha256}


def roles_dir():
    env = os.environ.get("CORRAL_LIGHT_ROLES_DIR")
    return Path(env) if env else Path.home() / ".config/corral-light/roles"


def _digest(toml_bytes, preamble_bytes):
    """Both halves: prompt_file can change while the TOML does not."""
    h = hashlib.sha256()
    h.update(b"toml:")
    h.update(toml_bytes)
    h.update(b"\npreamble:")
    h.update(preamble_bytes)
    return h.hexdigest()


def _read_preamble(role_path, rel):
    if not rel:
        raise RoleError("prompt_file is required — a role with no instructions "
                        "is a lane default with extra steps")
    if Path(rel).is_absolute():
        raise RoleError(f"prompt_file must be relative, got {rel}")
    base = role_path.parent.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise RoleError(f"prompt_file escapes the roles directory: {rel}") from None
    if not target.is_file():
        raise RoleError(f"prompt_file not found: {target}")
    raw = target.read_bytes()
    if len(raw) > MAX_PREAMBLE_BYTES:
        raise RoleError(f"{target.name} is {len(raw)} bytes; the cap is "
                        f"{MAX_PREAMBLE_BYTES}")
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise RoleError(f"{target.name} is empty")
    return text, raw


def load(role_id, rdir=None):
    """Parse and validate one role. Fails closed on anything unexpected."""
    role_id = (role_id or "").strip()
    if not role_id:
        raise RoleError("no role named")
    if role_id != Path(role_id).name or role_id.startswith("."):
        raise RoleError(f"{role_id!r} is not a role id")
    d = Path(rdir) if rdir else roles_dir()
    path = d / f"{role_id}.toml"
    if not path.is_file():
        known = ", ".join(sorted(r.stem for r in d.glob("*.toml"))) or "none"
        raise RoleError(f"no role {role_id!r} in {d} — known roles: {known}")
    raw = path.read_bytes()
    if len(raw) > MAX_ROLE_BYTES:
        raise RoleError(f"{path.name} is {len(raw)} bytes; the cap is {MAX_ROLE_BYTES}")
    try:
        doc = _parse_toml(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RoleError(f"{path.name} is not valid TOML: {e}") from None
    for key in doc:
        if key in REFUSED_KEYS:
            raise RoleError(f"{path.name}: `{key}` is not a role field — "
                            f"{REFUSED_KEYS[key]}")
    unknown = set(doc) - ALLOWED_KEYS
    if unknown:
        raise RoleError(f"{path.name}: unknown field(s) {', '.join(sorted(unknown))}")
    if doc.get("schema") != SCHEMA:
        raise RoleError(f"{path.name}: schema = {doc.get('schema')!r}; this "
                        f"reads schema = {SCHEMA}")
    desc = str(doc.get("description") or "").strip()
    if not desc:
        raise RoleError(f"{path.name}: description is required")
    dc = str(doc.get("data_class") or "").strip().lower()
    if dc not in DATA_CLASSES:
        raise RoleError(f"{path.name}: data_class must be one of "
                        f"{', '.join(DATA_CLASSES)} — unknown fails loud, never open")
    lane = doc.get("lane")
    if lane is not None:
        lane = str(lane).strip()
        if lane == "any":
            raise RoleError(f'{path.name}: lane = "any" is a routing hole — '
                            f"omit `lane` to let the caller pick")
        if not lane:
            raise RoleError(f"{path.name}: lane is empty — omit the key instead")
    posture = _valid_posture(doc.get("posture"), path.name)
    effort = doc.get("effort")
    effort = str(effort).strip() if effort is not None else None
    preamble, pre_raw = _read_preamble(path, str(doc.get("prompt_file") or ""))
    return Role(id=role_id, description=desc, lane=lane, effort=effort or None,
                posture=posture, data_class=dc,
                prompt_file=str(doc["prompt_file"]), preamble=preamble,
                sha256=_digest(raw, pre_raw), path=path)


def list_roles(rdir=None):
    """Every role that parses, plus the reason for each that does not."""
    d = Path(rdir) if rdir else roles_dir()
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.toml"))
    if len(files) > MAX_ROLES:
        raise RoleError(f"{len(files)} role files in {d}; the cap is {MAX_ROLES}")
    out = []
    for f in files:
        try:
            out.append(load(f.stem, d).to_dict())
        except RoleError as e:
            out.append({"id": f.stem, "error": str(e)})
    return out


class Resolved:
    __slots__ = ("agent", "posture", "effort", "preamble", "role", "sha256",
                 "notes", "data_class")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def MAX_PROMPT():                                      # noqa: N802
    """The TIGHTEST prompt cap a composed role must survive: a live pane
    (sessions.MAX_PROMPT) and an armed job (later.MAX_PROMPT) differ 25x, and
    a role that fits only the larger starts fine and refuses to schedule."""
    caps = []
    for mod in ("sessions", "later"):
        try:
            caps.append(int(getattr(__import__(mod), "MAX_PROMPT")))
        except Exception:                               # noqa: BLE001
            pass
    return min(caps) if caps else 8000


def resolve(role_id, *, lane=None, posture=None, effort=None, agents=None,
            rdir=None):
    """A role plus the caller's overrides -> create() arguments. Everything
    that can refuse, refuses here, before any process exists."""
    role = load(role_id, rdir)
    import sessions                                     # noqa: WPS433
    if agents is None:
        agents = sessions.AGENTS
    agent = (lane or role.lane or "").strip()
    if not agent:
        raise RoleError(f"role {role.id!r} declares no lane, so one must be chosen")
    for prefix, why in REFUSED_LANE_PREFIX.items():
        if agent.startswith(prefix):
            raise RoleError(f"roles do not apply to `{agent}`: {why}")
    if agent not in agents:
        raise RoleError(f"unknown lane {agent!r}")
    spec = agents[agent] or {}
    if spec.get("unavailable"):
        raise RoleError(f"{spec.get('label', agent)}: {spec['unavailable']}")
    notes = []
    want_posture = _valid_posture(posture, f"role {role.id!r}") or role.posture
    if want_posture and not sessions.posture_enforceable(spec):
        # A control that does nothing, then displays its imaginary result: the
        # role must not reintroduce what the dialog was fixed for.
        notes.append(f"{spec.get('label', agent)} manages its own permissions — "
                     f"the role's posture ({want_posture}) was NOT applied")
        want_posture = None
    if role.data_class != "public":
        notes.append(f"{role.data_class}: {DATA_CLASS_NOTE}")
    room = MAX_PROMPT() - len(role.preamble) - len(SEPARATOR)
    if room < MIN_ASK_ROOM:
        raise RoleError(f"role {role.id!r}'s instructions leave only {room} "
                        f"characters for the ask (floor is {MIN_ASK_ROOM})")
    return Resolved(agent=agent, posture=want_posture,
                    effort=(effort or role.effort or "").strip() or None,
                    preamble=role.preamble, role=role.id, sha256=role.sha256,
                    notes=notes, data_class=role.data_class)


def compose(preamble, prompt):
    """The role's instructions, then the ask. Refused past the cap, never clipped."""
    preamble, prompt = (preamble or "").strip(), (prompt or "").strip()
    text = f"{preamble}{SEPARATOR}{prompt}" if (preamble and prompt) else (preamble or prompt)
    cap = MAX_PROMPT()
    if len(text) > cap:
        raise RoleError(f"the role's instructions plus this prompt come to "
                        f"{len(text)} characters; the cap is {cap}")
    return text


def resolvable_on(role_id, agents=None, rdir=None):
    """{lane: None | reason} — what the picker greys out, and why."""
    if agents is None:
        import sessions                                 # noqa: WPS433
        agents = sessions.AGENTS
    out = {}
    for lane in list(agents):
        try:
            resolve(role_id, lane=lane, agents=agents, rdir=rdir)
            out[lane] = None
        except RoleError as e:
            out[lane] = str(e)
    return out


# ── creating a role from fields ──────────────────────────────────────────
def _toml_basic(value):
    """One TOML basic string; the whole injection defence (a newline must never
    be able to introduce a refused key such as `schedule`)."""
    simple = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
              "\n": "\\n", "\f": "\\f", "\r": "\\r"}
    out = ['"']
    for ch in value:
        if ch in simple:
            out.append(simple[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _clean_field(name, value, allow_newlines=False):
    v = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not v:
        raise RoleError(f"{name} is required")
    if not allow_newlines and "\n" in v:
        raise RoleError(f"{name} is one line — it is shown in a picker row")
    bad = [c for c in v if ord(c) < 0x20 and c not in ("\n", "\t")]
    if bad:
        raise RoleError(f"{name} contains a control character (U+{ord(bad[0]):04X})")
    cap = FIELD_CAPS.get(name)
    if cap is not None and len(v) > cap:
        raise RoleError(f"{name} is {len(v)} characters; the cap is {cap}")
    return v


def validate_fields(fields):
    unknown = set(fields) - {"id", "description", "personality", "does",
                             "expects", "data_class", "lane", "effort", "posture"}
    if unknown:
        raise RoleError(f"unknown field(s): {', '.join(sorted(unknown))}")
    rid = str(fields.get("id") or "").strip()
    if not ROLE_ID_RE.match(rid):
        raise RoleError(f"{rid!r} is not a role id — lowercase letters, digits "
                        f"and hyphens, starting with a letter, 2-32 characters")
    if rid in RESERVED_IDS:
        raise RoleError(f"{rid!r} is reserved")
    desc = _clean_field("description", fields.get("description"))
    if len(desc) < DESCRIPTION_MIN:
        raise RoleError(f"description needs at least {DESCRIPTION_MIN} characters")
    out = {"id": rid, "description": desc}
    for k in ("personality", "does", "expects"):
        out[k] = _clean_field(k, fields.get(k), allow_newlines=True)
    dc = str(fields.get("data_class") or "").strip().lower()
    if dc not in DATA_CLASSES:
        raise RoleError(f"data_class must be one of {', '.join(DATA_CLASSES)}")
    out["data_class"] = dc
    lane = str(fields.get("lane") or "").strip()
    if lane == "any":
        raise RoleError('lane = "any" is a routing hole — leave it blank')
    for prefix, why in REFUSED_LANE_PREFIX.items():
        if lane.startswith(prefix):
            raise RoleError(f"roles do not apply to `{lane}`: {why}")
    out["lane"] = lane
    out["posture"] = _valid_posture(fields.get("posture"), "the role form") or ""
    out["effort"] = str(fields.get("effort") or "").strip()
    return out


def compose_preamble(f):
    return PREAMBLE_TEMPLATE_V1.format(**{k: f.get(k, "") for k in (
        "id", "description", "personality", "does", "expects")}) + "\n"


def compose_toml(f):
    """Pure: no clock, no environment — a preview and a save write the same bytes."""
    lines = [f"# {f['id']} — written by `roles.py create`. Edit this file to change it.",
             "schema      = 1",
             f"description = {_toml_basic(f['description'])}"]
    for k in ("lane", "effort", "posture"):
        if f.get(k):
            lines.append(f"{k:<11} = {_toml_basic(f[k])}")
    lines.append(f"data_class  = {_toml_basic(f['data_class'])}")
    lines.append(f'prompt_file = "prompts/{f["id"]}.md"')
    return "\n".join(lines) + "\n"


def create(fields, rdir=None):
    """Validate, write both files exclusively, prove they load — or leave nothing."""
    f = validate_fields(fields)
    d = Path(rdir) if rdir else roles_dir()
    (d / "prompts").mkdir(parents=True, exist_ok=True)
    if len(list(d.glob("*.toml"))) + 1 > MAX_ROLES:
        raise RoleError(f"the cap is {MAX_ROLES} roles — delete one first")
    toml_text, preamble = compose_toml(f), compose_preamble(f)
    if len(preamble.encode("utf-8")) > MAX_PREAMBLE_BYTES:
        raise RoleError(f"the instructions exceed {MAX_PREAMBLE_BYTES} bytes")
    written = []
    try:
        for path, text in ((d / "prompts" / f"{f['id']}.md", preamble),
                           (d / f"{f['id']}.toml", toml_text)):
            try:
                fh = path.open("x", encoding="utf-8")        # O_EXCL: no races
            except FileExistsError:
                raise RoleError(f"{path.name} already exists — create never "
                                f"overwrites") from None
            written.append(path)
            with fh:
                fh.write(text)
        role = load(f["id"], d)
        if role.lane:
            resolve(f["id"], lane=role.lane, rdir=d)
        return role
    except Exception:
        for p in written:
            try:
                p.unlink()
            except OSError:
                pass
        raise


# ── CLI ──────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(prog="roles", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("id")
    sub.add_parser("check")
    s = sub.add_parser("resolve")
    s.add_argument("id")
    s.add_argument("--lane")
    s = sub.add_parser("create")
    for k in ("id", "description", "personality", "does", "expects", "data_class"):
        s.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    for k in ("lane", "effort", "posture"):
        s.add_argument("--" + k, dest=k, default="")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            print(json.dumps(list_roles(), indent=2), flush=True)
        elif a.cmd == "show":
            r = load(a.id)
            print(json.dumps(r.to_dict(), indent=2) + "\n\n" + r.preamble, flush=True)
        elif a.cmd == "check":
            bad = [r for r in list_roles() if r.get("error")]
            for r in bad:
                print(f"{r['id']}: {r['error']}", flush=True)
            return 1 if bad else 0
        elif a.cmd == "resolve":
            print(json.dumps(resolve(a.id, lane=a.lane).to_dict(), indent=2), flush=True)
        elif a.cmd == "create":
            fields = {k: getattr(a, k) for k in ("id", "description", "personality",
                                                 "does", "expects", "data_class",
                                                 "lane", "effort", "posture")}
            print(json.dumps(create(fields).to_dict(), indent=2), flush=True)
    except RoleError as e:
        print(f"roles: {e}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
