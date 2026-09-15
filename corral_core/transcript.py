#!/usr/bin/python3
"""transcript — ONE reader of "the conversation", for every consumer of it.

WHY THIS EXISTS (2026-09-14 bug bash, both arms, same answer)
    A pane's words were read four different ways: the bounded in-memory ring
    (`compose`, `last_answer`), `history()`'s backwards pager, `port._read_log`
    (export), and `transcripts._scan` (the search index). They disagreed, and
    every disagreement was a defect a user could see:

      * the handoff pack labelled turn 3 "## The original ask", because the
        real first ask had left the 4,000-event ring (Astra 4 / Grok 4);
      * the search index dropped everything past the first 32 MiB of a
        ROTATED log, so words that were on disk became unfindable, silently
        (Astra 7) -- and a single line longer than that chunk stalled the
        pane's read offset at 0 forever;
      * export read whole files into memory before applying any cap.

    Grok: "do not add a fourth transcript reader." Astra: "give conversation
    interpretation one owner." This is that owner. Feature-specific inclusion
    rules (what a pack carries, what is searchable) stay with the features;
    what is IN the conversation is decided once, here.

BOUNDED, AND IT SAYS WHEN IT BOUND (P8, P4)
    Every read has a byte ceiling and every result carries `complete`. A
    reader that silently returns less than the file holds is how "no matches"
    came to mean two different things. `complete=False` is the caller's cue to
    say so rather than to imply the rest is not there.

NOTHING HERE MAY IMPORT FROM `corral/` -- see the package docstring.
"""
import json
from pathlib import Path

# One read() syscall's worth. Small enough that a huge log streams instead of
# arriving as one allocation; large enough that a normal log is one or two.
CHUNK_BYTES = 4 * 1024 * 1024
# A single JSONL line longer than this is not a transcript event; it is a
# corruption or an attack on the reader. It is SKIPPED and counted, never
# waited on: the old code returned "no complete line yet" and left the pane's
# byte offset where it was, which is a permanent stall dressed as patience.
MAX_LINE_BYTES = 8 * 1024 * 1024
# Per file, per pass. 512 MiB is eight rotations' worth of the 64 MiB cap
# `_rotate_log` enforces -- so in practice this never binds, which is the
# point: the old 32 MiB ceiling bound on a log that rotates at 64 MiB and
# discarded the rest.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

LOG_NAMES = ("events.jsonl.1", "events.jsonl")   # oldest generation first


class Reading:
    """Events, where the read stopped, and whether it saw everything.

    `offsets[i]` is the byte offset the i-th event's LINE starts at, in the
    file named by `path`. A caller that wants to re-read from an event (the
    index does, to coalesce a turn's streamed chunks across refreshes) rewinds
    to that offset instead of guessing.
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
    """(Reading) from `path`, starting at byte `offset`.

    Stops on a LINE BOUNDARY so the next pass resumes cleanly; a trailing
    partial line is left unread and `offset` points at its first byte. A line
    that exceeds MAX_LINE_BYTES is skipped and counted rather than stalling
    the offset. `complete` is False when the byte ceiling stopped the read
    before the end of the file.
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
                        # No newline in a line already longer than any event
                        # can be. Drop what we hold and keep scanning for the
                        # next boundary rather than re-reading it forever.
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
                    continue              # a torn line is skipped, not fatal
                if isinstance(ev, dict):
                    events.append(ev)
                    offsets.append(line_at)
    finally:
        fh.close()
    return Reading(events=events, offsets=offsets, path=path, offset=buf_at,
                   complete=complete, bytes_read=read_total,
                   skipped_lines=skipped)


def read_pane_dir(pane_dir, max_bytes=DEFAULT_MAX_BYTES):
    """Every durable event of one pane, oldest generation first.

    `events.jsonl.1` is read WHOLE. It holds the turns rotation moved out from
    under the live file, and a reader that takes only its head is a deletion
    nobody asked for -- which is exactly what the search index was doing.
    """
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
    """The conversation as the DURABLE log has it, falling back to the ring.

    The in-memory `pane.events` ring is a display cache bounded at MAX_EVENTS;
    reading it and calling the first entry "the original ask" is the bug this
    module was built for. The log is the record. A pane with no directory (a
    test double, a pane that never persisted) still answers from the ring --
    and says `complete=False` when that ring is at its bound, because then it
    demonstrably is not the whole conversation.
    """
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


# ── one definition of a tool call ─────────────────────────────────────────
# ACP sends one `tool_call` and then a stream of `tool_call_update`s for the
# SAME toolCallId. Anything that counts rows instead of ids reports a number
# nobody can reproduce by eye: the digest said "tools: 5 calls" for two calls
# (Grok 3), and the UI has deduplicated by id since 2026-08-01. One helper,
# used by the pack (`port._turns`) and by the index the digest counts.
EDIT_KINDS = frozenset(("edit", "delete", "move"))
# ACP's kinds for calls that DO NOT write. An unknown/absent kind is not on
# this list on purpose: an adapter that sends no kind at all leaves us unable
# to tell, and dropping its paths would trade a false "edited" for a false
# "nothing happened" -- the direction that hides work (P4 cuts the other way
# here: the honest default is to report the file, not to swallow it).
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
    """Fold a stream of `tool_facts` into one record per tool id.

    Later updates win on every field they actually carry -- an update with no
    title must not blank the title the call arrived with (the same rule
    app.js's `pushStep` follows). Anonymous rows (no id) each stay their own
    call rather than collapsing into one.
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
    """The files a set of merged tool records actually EDITED.

    Not "every path any tool named": a `read` names its file too, and the
    digest counted those as edits (Astra 11 -- three updates of one read-only
    tool printed "edited 1 file: untouched.py"). A failed call did not edit
    anything either. `EDIT_KINDS` names the writes; `NON_EDIT_KINDS` names the
    reads; anything else is a kind neither list knows, and is reported.
    """
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
