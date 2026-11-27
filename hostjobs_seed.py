#!/usr/bin/python3
"""The Seed side of hostjobs.py: one verb per run, one JSON object on stdout.

    hostjobs_seed.py <seed-root> list
    hostjobs_seed.py <seed-root> off|on|rm <job> [<removed-dir>]

Run by the hub as a fresh subprocess, never imported into it. It uses Seed's
own code for everything Seed already decides: `scheduler/sync.py` reads the
manifest and reconciles the real scheduler, `observability/switches.py` is the
on/off switch. This file only adds the read of the last run and the removal of
one manifest entry.

Exit 0 with {"ok": true, ...}; exit 1 with {"ok": false, "error": "..."}.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
OFF_WHY = "turned off from Corral's Scheduled jobs page"
ENTRY_RE = re.compile(r"^\s*-\s+name:\s*(.*?)\s*$")


class Refused(Exception):
    pass


def _seed(root):
    """Seed's own sync and switches modules, loaded from `root`."""
    for sub in ("scheduler", "observability"):
        if not (root / sub).is_dir():
            raise Refused(f"no Seed {sub}/ folder in {root}")
        sys.path.insert(0, str(root / sub))
    try:
        import sync
        import switches
    except Exception as e:           # noqa: BLE001
        raise Refused(f"could not load Seed's scheduler: {e}") from None
    return sync, switches


def _jobs(sync, manifest=None):
    """The manifest's jobs through Seed's own reader (it exits on a bad file)."""
    keep = sync.MANIFEST
    if manifest is not None:
        sync.MANIFEST = Path(manifest)
    try:
        return [j for j in sync.load_jobs() if isinstance(j, dict) and j.get("name")]
    except SystemExit as e:
        raise Refused(str(e.code)[:300]) from None
    except Exception as e:           # noqa: BLE001
        raise Refused(f"the manifest could not be read: {e}"[:300]) from None
    finally:
        sync.MANIFEST = keep


def _last_runs(root):
    """job -> its newest run, from Seed's run log. Empty when there is none."""
    db = Path(os.environ.get("CC_OBS_DB") or root / "observability" / "data" / "runs.db")
    if not db.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute(
                "SELECT job, started_at, ok, summary, error_tail FROM runs "
                "WHERE id IN (SELECT MAX(id) FROM runs GROUP BY job)").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {r[0]: {"last": r[1], "last_ok": None if r[2] is None else bool(r[2]),
                   "last_note": ((r[3] if r[2] else r[4] or r[3]) or "")[:200]}
            for r in rows}


def v_list(root):
    sync, switches = _seed(root)
    jobs = _jobs(sync)
    sw, err = switches.load_strict()
    off = set((sw or {}).get("disabled") or [])
    reasons = (sw or {}).get("reasons") or {}
    runs = _last_runs(root)
    out = []
    for j in jobs:
        name = str(j["name"])
        r = reasons.get(name) if isinstance(reasons.get(name), dict) else {}
        out.append({"name": name, "schedule": str(j.get("schedule") or ""),
                    "command": str(j.get("command") or "")[:400],
                    "paused": name in off,
                    "why": (r.get("why") or "") if name in off else "",
                    "until": (r.get("until") or "") if name in off else "",
                    **runs.get(name, {"last": None, "last_ok": None, "last_note": ""})})
    return {"jobs": out, "switchError": err or ""}


def _known(sync, name):
    if not NAME_RE.match(name or ""):
        raise Refused("that is not a job name")
    jobs = _jobs(sync)
    if name not in {str(j["name"]) for j in jobs}:
        raise Refused(f"no job {name} in Seed's manifest")
    return jobs


def v_switch(root, name, paused):
    sync, switches = _seed(root)
    _known(sync, name)
    if paused:
        # No owner passed: Seed records its own default (the operator).
        switches.set_disabled(name, True, why=OFF_WHY)
    else:
        switches.set_disabled(name, False)
    return {"name": name, "paused": paused}


def cut_entry(text, name):
    """`text` without the manifest entry `name`, and the lines cut. Comment
    lines directly above an entry travel with it; None when it is not there."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ENTRY_RE.match(ln)
              and not ln.lstrip().startswith("#")]
    hit = [i for i in starts
           if ENTRY_RE.match(lines[i]).group(1).strip("'\"") == name]
    if len(hit) != 1:
        return None, None

    def lead(i):                      # first of the comment lines above entry i
        while i > 0 and lines[i - 1].lstrip().startswith("#") \
                and lines[i - 1].startswith(" "):
            i -= 1
        return i

    begin = lead(hit[0])
    after = [i for i in starts if i > hit[0]]
    end = lead(after[0]) if after else len(lines)
    return "".join(lines[:begin] + lines[end:]), "".join(lines[begin:end])


def v_rm(root, name, removed_dir):
    sync, switches = _seed(root)
    before = _known(sync, name)
    manifest = Path(sync.MANIFEST)
    old = manifest.read_text(encoding="utf-8")
    new, cut = cut_entry(old, name)
    if new is None:
        raise Refused(f"could not find one entry for {name} in the manifest — "
                      f"nothing was changed; remove it by hand")
    # Prove the cut before it lands: every other job unchanged, this one gone.
    probe = manifest.with_name(manifest.name + ".corral-probe")
    try:
        probe.write_text(new, encoding="utf-8")
        after = _jobs(sync, probe)
    finally:
        probe.unlink(missing_ok=True)
    if after != [j for j in before if str(j["name"]) != name]:
        raise Refused(f"removing {name} would change other jobs in the manifest — "
                      f"nothing was changed; remove it by hand")
    saved = ""
    if removed_dir:
        d = Path(removed_dir)
        d.mkdir(parents=True, exist_ok=True)
        keep = d / f"{name}-{time.strftime('%Y%m%dT%H%M%S')}.yml"
        keep.write_text(cut, encoding="utf-8")
        saved = str(keep)
    tmp = manifest.with_name(manifest.name + ".corral-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.chmod(tmp, manifest.stat().st_mode & 0o777)
    tmp.replace(manifest)
    r = subprocess.run([sys.executable, str(root / "scheduler" / "sync.py")],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        manifest.write_text(old, encoding="utf-8")     # put the entry back
        raise Refused("Seed's sync refused, so the job was put back: "
                      + (r.stderr or r.stdout).strip()[-300:])
    # Seed's freshness check and switch no longer have a job to speak for.
    fresh = root / "observability" / "freshness.json"
    try:
        d = json.loads(fresh.read_text(encoding="utf-8"))
        if isinstance(d.get("jobs"), dict) and name in d["jobs"]:
            del d["jobs"][name]
            t = fresh.with_name(fresh.name + ".corral-tmp")
            t.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
            t.replace(fresh)
    except (OSError, ValueError):
        pass
    try:
        if name in switches.disabled_jobs():
            switches.set_disabled(name, False)
    except Exception:                # noqa: BLE001
        pass
    return {"name": name, "removed": True, "saved": saved}


def main(argv):
    try:
        if len(argv) < 3:
            raise Refused("usage: hostjobs_seed.py <seed-root> list|off|on|rm [job]")
        root, verb = Path(argv[1]), argv[2]
        name = argv[3] if len(argv) > 3 else ""
        if verb == "list":
            out = v_list(root)
        elif verb in ("off", "on"):
            out = v_switch(root, name, verb == "off")
        elif verb == "rm":
            out = v_rm(root, name, argv[4] if len(argv) > 4 else "")
        else:
            raise Refused(f"unknown verb {verb!r}")
    except Refused as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    except Exception as e:           # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}))
        return 1
    print(json.dumps({"ok": True, **out}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
