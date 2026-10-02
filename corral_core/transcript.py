#!/usr/bin/python3
"""transcript — the single reader of a pane's conversation log.

Every read is byte-bounded and reports `complete=False` when the bound stopped
it. Must not import from `corral/`.
"""
import json
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024
# Longer lines are skipped and counted, never waited on (would stall the offset).
MAX_LINE_BYTES = 8 * 1024 * 1024
# Per file, per pass; well above the 64 MiB rotation cap so it rarely binds.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

LOG_NAMES = ("events.jsonl.1", "events.jsonl")   # oldest generation first


class Reading:
    """Events, where the read stopped, and whether it saw everything.

    `offsets[i]` is the byte offset where the i-th event's line starts in `path`.
    """

    __slots__ = ("events", "offsets", "path", "offset", "complete",
                 "bytes_read", "skipped_lines")

    def __init__(self, events=None, offsets=None, path=None, offset=0,
                 complete=True, bytes_read=0, skipped_lines=0):
        self.events = events if events is not None else []
        self.offsets = offsets if offsets is not None else []
        self.path = path
        self.offset = offset
        self.complete = complete
        self.bytes_read = bytes_read
        self.skipped_lines = skipped_lines

    def __len__(self):
        return len(self.events)

    def __repr__(self):                                     # pragma: no cover
        return (f"<Reading {len(self.events)} events complete={self.complete} "
                f"offset={self.offset} skipped={self.skipped_lines}>")


def read_file(path, offset=0, max_bytes=DEFAULT_MAX_BYTES):
    """Read `path` from byte `offset` into a Reading.

    Stops on a line boundary (a trailing partial line is left unread); oversize
    lines are skipped; `complete` is False if `max_bytes` stopped the read.
    """
    path = Path(path)
    events, offsets = [], []
    pos = int(offset or 0)
    read_total, skipped = 0, 0
    complete = True
    buf = b""
    buf_at = pos                          # file offset of buf[0]
    try:
        fh = path.open("rb")
    except OSError:
        return Reading(path=path, offset=pos, complete=False)
    try:
        fh.seek(pos)
        while True:
            if read_total >= max_bytes:
                complete = False
                break
            chunk = fh.read(min(CHUNK_BYTES, max_bytes - read_total))
            if not chunk:
                break
            read_total += len(chunk)
            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    if len(buf) > MAX_LINE_BYTES:
                        # Oversize line: drop it and scan on for the next newline.
                        skipped += 1
                        buf_at += len(buf)
                        buf = b""
                    break
                line, buf = buf[:nl], buf[nl + 1:]
                line_at = buf_at
                buf_at += nl + 1
                if len(line) > MAX_LINE_BYTES:
                    skipped += 1
                    continue
                s = line.strip()
                if not s:
                    continue
                try:
                    ev = json.loads(s.decode("utf-8", "replace"))
                except ValueError:
                    continue              # skip a torn line
                if isinstance(ev, dict):
                    events.append(ev)
                    offsets.append(line_at)
    finally:
        fh.close()
    return Reading(events=events, offsets=offsets, path=path, offset=buf_at,
                   complete=complete, bytes_read=read_total,
                   skipped_lines=skipped)


def read_pane_dir(pane_dir, max_bytes=DEFAULT_MAX_BYTES):
    """Every durable event of one pane, oldest generation (rotated log) first."""
    d = Path(pane_dir)
    events, complete, skipped, used = [], True, 0, 0
    for name in LOG_NAMES:
        f = d / name
        if not f.is_file():
            continue
        r = read_file(f, 0, max_bytes=max(0, max_bytes - used))
        events += r.events
        used += r.bytes_read
        skipped += r.skipped_lines
        complete = complete and r.complete
    return Reading(events=events, path=d, complete=complete,
                   skipped_lines=skipped, bytes_read=used)


def pane_events(pane, max_bytes=DEFAULT_MAX_BYTES):
    """A pane's conversation from its durable log, falling back to the in-memory
    ring (marked incomplete when the ring is at MAX_EVENTS)."""
    d = getattr(pane, "dir", None)
    if d:
        try:
            r = read_pane_dir(d, max_bytes=max_bytes)
        except OSError:
            r = None
        if r is not None and r.events:
            return r
    ring = list(getattr(pane, "events", []) or [])
    try:
        from corral_core import sessions as _s
        capped = _s.MAX_EVENTS is not None and len(ring) >= _s.MAX_EVENTS
    except Exception:                                   # noqa: BLE001
        capped = False
    return Reading(events=ring, complete=not capped)


# ── tool calls ────────────────────────────────────────────────────────────
# ACP sends one `tool_call` then many `tool_call_update`s for the same id;
# count by id, not by row.
EDIT_KINDS = frozenset(("edit", "delete", "move"))
# Kinds that do not write. Unknown/absent kinds are deliberately absent, so
# their paths are reported as edits rather than hidden.
NON_EDIT_KINDS = frozenset(("read", "search", "fetch", "think", "execute",
                            "switch_mode", "other"))


def tool_facts(ev):
    """{'id','title','kind','status','paths'} for a `tool` event, or None."""
    if (ev or {}).get("kind") != "tool":
        return None
    d = ev.get("data") or {}
    paths = [str(l.get("path")) for l in (d.get("locations") or [])
             if isinstance(l, dict) and l.get("path")]
    return {"id": d.get("id") or "", "title": str(d.get("title") or ""),
            "kind": str(d.get("kind") or ""), "status": str(d.get("status") or ""),
            "paths": paths}


def merge_tools(facts):
    """Fold `tool_facts` into one record per tool id.

    Later non-empty fields win; rows with no id each stay a separate call.
    """
    out, anon = {}, 0
    for f in facts:
        if not f:
            continue
        key = f["id"]
        if not key:
            anon += 1
            key = f"\x00anon{anon}"
        cur = out.get(key)
        if cur is None:
            out[key] = dict(f, id=key)
            continue
        for k, v in f.items():
            if k == "id" or not v:
                continue
            if k == "paths":
                cur["paths"] = list(dict.fromkeys(cur["paths"] + v))
            else:
                cur[k] = v
    return out


def edited_paths(merged):
    """Paths edited by merged tool records: skips NON_EDIT_KINDS and failed calls."""
    out = []
    for rec in merged.values():
        if rec.get("kind") in NON_EDIT_KINDS:
            continue
        if rec.get("status") == "failed":
            continue
        for p in rec.get("paths") or []:
            if p not in out:
                out.append(p)
    return out
