#!/usr/bin/python3
"""transcripts — full-text search across every pane's log, and a MECHANICAL
digest of what the agents did in a window.

Ported from full Corral's transcripts.py (DESIGN-4 F2) for Corral Light on
2026-09-29, resilience review §3: Light's ⌘K searched notes, not its own
conversations, so "what did Codex say about the cookie yesterday" was a grep.
Port changes, and only these: the shared `corral_core.transcript` reader, the
index under CORRAL_LIGHT_STATE (never the full Corral's state), a Python
whose sqlite lacks FTS5 degrades to a typed error instead of a crash, and the
digest drops its full-Corral Docket tail (close.py is fleet state Light does
not have). Everything below the next heading is the original's reasoning.

DESIGN-4 F2. Two halves, one store:

    search()   every pane's transcript -- live, detached AND archived -- from
               the palette and the CLI. A closed conversation's words are on
               disk and were unfindable; that is a deletion nobody asked for.
    digest()   what happened in the last N hours, computed FROM EVENTS.
               No model is in the loop, and that is deliberate: the 2026-08-23
               three-model panel killed LLM deltas for close records ("hiding
               the exact bytes the operator needs"). `close.py digest` is arithmetic
               over records and this is arithmetic over events. If the operator wants
               prose from a digest, he quotes it into a pane -- composition,
               not dispatch.

THE INDEX IS DERIVED AND DISPOSABLE
    Same posture as `library.py`: SQLite FTS5, mtime/offset-diffed, throttled,
    its own DB at $CORRAL_LIGHT_STATE/transcripts.db. Delete it and the next refresh
    rebuilds it. A corrupt DB self-heals by rebuilding rather than locking
    (P5) -- said once on stderr, not on every query.

    A SEPARATE database from the Library's on purpose: that one is read-only
    by construction over DOCUMENTS the operator and the fleet wrote; this one is over
    Corral's own logs, which rotate, get closed, and are appended to by live
    processes. One file, two lifecycles, is how a rebuild of one loses the
    other.

WHAT IS INDEXED, AND THE ONE COLUMN DESIGN-4 DID NOT NAME
    Searchable bodies: `user`, `text`, `tool` (title + the head of its result),
    `note`, `dead`. NOT searchable: `thought` (monologue, and the operator hides it by
    default) and `permission*` -- a consent payload is not search material.

    But the digest has to be able to SAY "3 asked · 2 answered · 1 expired",
    and every number it prints must be a count over rows this index holds
    rather than a second read of the log that could disagree with search().
    So permission events are stored with an EMPTY body: countable, never
    findable. `thought` is not stored at all -- nothing counts it.

    `meta UNINDEXED` is the one column DESIGN-4's schema sketch did not list.
    It carries a tool call's `locations[].path` list, which is where "edited 5
    files" comes from. Same argument: the digest's numbers come out of the
    index or they are a different measurement wearing the same face.

BOUNDS (P8), every one a named constant
    ROW_MAX_CHARS per row, PANE_MAX_ROWS newest rows per pane (older rows are
    dropped from the INDEX, never from disk, and search() names the pane in
    `partial` so a bounded answer never passes as a complete one),
    DIGEST_MAX_PANES, DIGEST_MAX_HOURS, REFRESH_S.

CLI (P16):
    python3 transcripts.py search "route refused" --limit 20
    python3 transcripts.py digest --hours 24
    python3 transcripts.py refresh --force
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from corral_core import transcript                  # noqa: E402

# Light's own state dir, bound the same way sessions.STATE is. NOT the full
# Corral's: two products must never read each other's panes.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))

REFRESH_S = 60                  # at most one scan a minute (library.py's rule)
ROW_MAX_CHARS = 8000            # per indexed row; head kept, tail marked …
PANE_MAX_ROWS = 20000           # newest rows per pane held in the INDEX
TOOL_BODY_CHARS = 2000          # of a tool call's result text
DIGEST_MAX_PANES = 40
DIGEST_MAX_HOURS = 24 * 7       # same window close.py clamps to
DIGEST_ASK_CHARS = 120
DIGEST_MAX_FILES = 8
SEARCH_MAX_LIMIT = 200
SNIPPET_TOKENS = 14

# Bodies that are searchable.
# `peer` (DESIGN-5 S7): a message another pane's agent sent is findable by what
# it said, like a human's ask -- and counted apart from one (digest).
BODY_KINDS = ("user", "peer", "text", "tool", "note", "dead")
# Stored with an EMPTY body: countable by the digest, never findable by text.
FACT_KINDS = ("permission", "permission_answered", "permission_expired")
KEEP_KINDS = frozenset(BODY_KINDS + FACT_KINDS)

_lock = threading.RLock()
_last_refresh = 0.0
_rebuilt_said = False


def _db_path(state_dir=None):
    return (Path(state_dir) if state_dir else STATE) / "transcripts.db"


def _panes_dir(state_dir=None):
    return (Path(state_dir) if state_dir else STATE) / "panes"


# Bumped when what a ROW MEANS changes -- not when the SQL schema changes.
# v2 (2026-09-14): a turn's streamed text chunks are one row, and a tool row's
# meta is a JSON record (id, kind, status, paths) instead of a bare path list.
# Rows written by v1 are shaped right and MEAN something else, which is the
# kind of drift nothing would ever notice; the index is derived and
# disposable, so the honest migration is to throw it away and re-read the
# logs (P5).
# v4 (DESIGN-5 S7): `peer` rows exist. A v3 index would silently miss every
# peer message, so it is rebuilt rather than trusted.
INDEX_VERSION = 4


def _connect(state_dir=None):
    p = _db_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE IF NOT EXISTS index_meta(k TEXT PRIMARY KEY, v TEXT)")
    row = c.execute("SELECT v FROM index_meta WHERE k='version'").fetchone()
    have = int(row[0]) if row and str(row[0]).isdigit() else 0
    if have != INDEX_VERSION:
        for sql in ("DROP TABLE IF EXISTS ev_fts", "DROP TABLE IF EXISTS panes"):
            try:
                c.execute(sql)
            except sqlite3.DatabaseError:
                pass
        c.execute("INSERT OR REPLACE INTO index_meta VALUES('version',?)",
                  (str(INDEX_VERSION),))
        c.commit()
        global _last_refresh
        _last_refresh = 0.0
    # tail_rowid/tail_off: the row holding a text run that was still growing
    # when the last pass ended, and the byte offset it starts at. The run is
    # indexed IMMEDIATELY (a live pane's newest answer has to be findable) and
    # REPLACED on the next pass, whole, by rowid -- which is O(1), unlike
    # finding it again through an UNINDEXED column.
    c.execute("""CREATE TABLE IF NOT EXISTS panes(
        id TEXT PRIMARY KEY, title TEXT, agent TEXT, cwd TEXT, created TEXT,
        closed INT, gen TEXT, offset INT, partial INT,
        tail_rowid INT, tail_off INT, metaless INT)""")
    c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS ev_fts USING fts5(
        doc UNINDEXED, pane UNINDEXED, seq UNINDEXED, at UNINDEXED,
        kind UNINDEXED, meta UNINDEXED, body)""")
    return c


class NoFts5(RuntimeError):
    """This Python's sqlite was built without FTS5."""


def _db(state_dir=None):
    """Open the index, rebuilding it from scratch if it is unreadable.

    Self-heal over lock (P5): a half-written FTS5 file is derived data, so the
    cheap correct move is to throw it away. Said ONCE on stderr -- a line on
    every query is how a real fault becomes background noise (P7).
    """
    global _rebuilt_said
    try:
        return _connect(state_dir)
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower():
            raise NoFts5("this Python's sqlite has no FTS5 — transcript "
                         "search is unavailable on it") from None
    except sqlite3.DatabaseError:
        pass
    try:
        _db_path(state_dir).unlink()
    except OSError:
        pass
    if not _rebuilt_said:
        _rebuilt_said = True
        print("corral-light: transcripts.db was unreadable — rebuilt from the logs",
              file=sys.stderr, flush=True)
    global _last_refresh
    _last_refresh = 0.0
    return _connect(state_dir)


# ── indexing ──────────────────────────────────────────────────────────────
def _clip(s):
    s = "" if s is None else str(s)
    return s if len(s) <= ROW_MAX_CHARS else s[:ROW_MAX_CHARS] + " …"


def _row_for(ev):
    """(body, meta) for an event we keep, or None for one we do not."""
    kind = ev.get("kind")
    if kind not in KEEP_KINDS:
        return None
    d = ev.get("data") or {}
    if kind in FACT_KINDS:
        return "", ""
    if kind == "dead":
        return _clip(d.get("reason") or ""), ""
    if kind == "tool":
        texts = []
        for c in d.get("content") or []:
            inner = (c or {}).get("content") if isinstance(c, dict) else None
            inner = inner if isinstance(inner, dict) else c
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                texts.append(inner["text"])
        f = transcript.tool_facts(ev) or {}
        head = " ".join(str(x) for x in (d.get("title"), d.get("kind")) if x)
        body = (head + "\n" + "\n".join(texts)[:TOOL_BODY_CHARS]).strip()
        # The IDENTITY of the call travels with the row, not just its paths.
        # The digest counted rows and called them calls; ACP sends one call
        # and a stream of updates for the same id, so its headline number was
        # inflated ~5x against what the eye counts in the pane (Grok 3). A
        # count the operator cannot reproduce is worse than no count.
        meta = json.dumps({"id": f.get("id") or "", "kind": f.get("kind") or "",
                           "status": f.get("status") or "",
                           "paths": [_clip(x) for x in (f.get("paths") or [])[:8]]})
        return _clip(body), meta
    return _clip(d.get("text") or ""), ""


def _rows(pane_id, events, offsets=None, hold_tail=False):
    """(rows, rewind_offset) — index rows for `events`.

    ADJACENT TEXT EVENTS ARE ONE ROW. ACP streams an answer as many
    `agent_message_chunk`s; indexing each as its own FTS row means a phrase
    that spans two chunks matches neither ("route " + "refused" renders as
    "route refused" and `route refused` found nothing -- Astra 8). FTS needs
    both terms in ONE row, so a turn's chunks are joined the way the reader
    sees them. The row carries the FIRST chunk's seq, which is the seq the UI
    already scrolls to.

    `rewind` is the byte offset of a run that is still OPEN -- the last event
    read was text, so the next refresh may extend it. The run is indexed now
    (a live pane's newest answer must be findable immediately) and the caller
    re-reads from `rewind` next pass, replacing that one row so the finished
    run is one row, whole, exactly once. A closed pane's log cannot grow, so
    `hold_tail` is False for it and nothing is re-read.
    """
    rows, rewind = [], None
    run = None                      # [seq, at, [texts], offset]

    def flush():
        nonlocal run
        if run is None:
            return
        rows.append((f"{pane_id}:{run[0]}", pane_id, str(run[0]), run[1],
                     "text", "", _clip("".join(run[2]))))
        run = None

    for i, ev in enumerate(events):
        kind = ev.get("kind")
        if kind not in KEEP_KINDS:
            continue                # thought &c never reach the index at all
        seq = ev.get("seq") or 0
        at = ev.get("at") or ""
        off = offsets[i] if offsets and i < len(offsets) else None
        if kind == "text":
            body = str((ev.get("data") or {}).get("text") or "")
            if run is None:
                run = [seq, at, [body], off]
            else:
                run[2].append(body)
            continue
        flush()
        made = _row_for(ev)
        if made is None:
            continue
        body, meta = made
        rows.append((f"{pane_id}:{seq}", pane_id, str(seq), at,
                     kind, meta, body))
    if run is not None:
        if hold_tail and run[3] is not None:
            rewind = run[3]         # …and re-read from here next pass
        flush()
    return rows, rewind


def _insert_tail(c, rows):
    """Insert `rows`, returning the rowid of the LAST one -- the open run."""
    _insert(c, rows[:-1])
    cur = c.execute("INSERT INTO ev_fts VALUES(?,?,?,?,?,?,?)", rows[-1])
    return cur.lastrowid


def _insert(c, rows):
    if rows:
        c.executemany("INSERT INTO ev_fts VALUES(?,?,?,?,?,?,?)", rows)
    return len(rows)


def _trim(c, pane_id):
    """Hold at most PANE_MAX_ROWS newest rows for one pane. Returns whether
    anything was dropped -- which search() reports, because a bounded answer
    that does not say it is bounded reads exactly like a complete one."""
    n = c.execute("SELECT COUNT(*) FROM ev_fts WHERE pane=?",
                  (pane_id,)).fetchone()[0]
    if n <= PANE_MAX_ROWS:
        return False
    c.execute("DELETE FROM ev_fts WHERE rowid IN ("
              " SELECT rowid FROM ev_fts WHERE pane=? ORDER BY rowid LIMIT ?)",
              (pane_id, n - PANE_MAX_ROWS))
    return True


#: A pane directory with no `meta.json` AT ALL. Not the same thing as a
#: meta.json that cannot be trusted, and the difference decides whether the
#: conversation is findable (2026-09-15, P2: 28 such dirs on the Linux server).
_META_MISSING = object()


def _meta_of(d):
    """The pane's meta, or a marker saying WHICH kind of nothing it is.

    Three outcomes, deliberately distinct:

      * a dict with an `id` — an ordinary pane.
      * `_META_MISSING` — no `meta.json` on disk. These predate `save_meta`
        or are crash leftovers, and their `events.jsonl` is still a real
        conversation the operator had. Indexing them under their id is strictly
        better than a search that answers "no matches" about words that are
        demonstrably on his disk (P1: a clean answer you cannot substantiate
        is worse than an ugly one).
      * `{}` — `meta.json` exists and cannot be trusted: broken JSON, not a
        dict, no `id`, or unreadable. THAT is what `skipped` now means, and
        only that: a directory the index genuinely could not read.
    """
    try:
        raw = (d / "meta.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return _META_MISSING
    except OSError:
        return {}                       # permissions, a directory, a bad link
    try:
        m = json.loads(raw)
    except ValueError:
        return {}
    return m if isinstance(m, dict) and m.get("id") else {}


def _scan(c, state_dir=None):
    """One pass over every pane directory. Counted, so the throttle can be
    measured rather than asserted about."""
    _scan.calls += 1
    root = _panes_dir(state_dir)
    panes, rows, skipped = 0, 0, []
    if not root.is_dir():
        return {"panes": 0, "rows": 0, "skipped": skipped}
    have = {r[0]: r for r in c.execute(
        "SELECT id, gen, offset, partial, tail_rowid FROM panes")}
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        pane_id = d.name
        m = _meta_of(d)
        cur = d / "events.jsonl"
        metaless = m is _META_MISSING
        if metaless:
            # INDEXED, never dropped and never deleted (the operator, 2026-09-15).
            # A log with no meta still answers "what did I say about X"; the
            # only things lost are the title and the agent, so it gets its id
            # for a title and "?" for the agent, and every hit carries
            # `metaless` so the surface can say which it is rather than
            # implying a title it does not have.
            if not cur.is_file():
                skipped.append(pane_id)  # no meta AND no log: not a pane dir
                continue
            m = {"id": pane_id, "title": pane_id, "agent": "?", "closed": True}
        elif not m.get("id"):
            skipped.append(pane_id)      # a meta that cannot be read; say so
            continue
        try:
            st = cur.stat()
        except OSError:
            skipped.append(pane_id)
            continue
        prev = have.get(pane_id)
        gen_now = f"{st.st_ino}:{st.st_size}"
        prev_gen = (prev[1] if prev else "") or ""
        prev_off = int(prev[2] or 0) if prev else 0
        partial = bool(prev[3]) if prev else False
        prev_ino, _, prev_size = prev_gen.partition(":")
        rotated = (prev is not None and
                   (prev_ino != str(st.st_ino) or st.st_size < prev_off))
        closed = bool(m.get("closed"))
        prev_tail = (prev[4] if prev else None)
        gained, tail_rowid = 0, None
        if prev is None or rotated:
            # Full (re)index. Rotation moved events.jsonl -> .1 under us, so
            # the old generation still holds turns nobody should lose; read it
            # FIRST so rowid order stays chronological (the trim drops oldest).
            #
            # WHOLE, not a head. The old read stopped at 32 MiB and threw the
            # rest of `.1` away, while the core rotates at 64 MiB: a marker in
            # a 36 MB log was searchable before rotation and gone after, with
            # the bytes still on disk (Astra 7). transcript.read_file is
            # chunked and bounded per PASS, not per prefix, and a line too
            # long to be an event is skipped instead of stalling the offset at
            # 0 forever.
            c.execute("DELETE FROM ev_fts WHERE pane=?", (pane_id,))
            partial = False
            old = d / "events.jsonl.1"
            if old.is_file():
                r = transcript.read_file(old, 0)
                made, _ = _rows(pane_id, r.events, r.offsets, hold_tail=False)
                gained += _insert(c, made)
            r = transcript.read_file(cur, 0)
            made, rewind = _rows(pane_id, r.events, r.offsets,
                                 hold_tail=not closed)
            if made and rewind is not None:
                tail_rowid = _insert_tail(c, made)
                gained += len(made)
            else:
                gained += _insert(c, made)
            off = rewind if rewind is not None else r.offset
        else:
            if st.st_size == prev_off:
                off = prev_off
                tail_rowid = prev_tail
            else:
                if prev_tail is not None:
                    # The open run is about to be re-read whole; its partial
                    # row goes now, so the turn is never in the index twice.
                    c.execute("DELETE FROM ev_fts WHERE rowid=?", (prev_tail,))
                r = transcript.read_file(cur, prev_off)
                made, rewind = _rows(pane_id, r.events, r.offsets,
                                     hold_tail=not closed)
                if made and rewind is not None:
                    tail_rowid = _insert_tail(c, made)
                    gained += len(made)
                else:
                    gained += _insert(c, made)
                off = rewind if rewind is not None else r.offset
        rows += gained
        # ONLY a pane that gained rows can have crossed the cap. `_trim`'s
        # COUNT(*) filters on an UNINDEXED FTS column, so it scans the table
        # once per pane -- and it ran for every pane on every refresh: Astra
        # measured 28.6 SECONDS to refresh 300 panes with ZERO new events.
        # The bound is unchanged; what is gone is asking a quiet pane whether
        # it grew (P7: a no-op pass does no work and says nothing).
        if gained and _trim(c, pane_id):
            partial = True
        c.execute(
            "INSERT OR REPLACE INTO panes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pane_id, m.get("title") or pane_id, m.get("agent") or "?",
             m.get("cwd") or "", m.get("created") or "",
             1 if m.get("closed") else 0, gen_now, off,
             1 if partial else 0, tail_rowid, off, 1 if metaless else 0))
        panes += 1
    # A pane directory the operator deleted must leave the index too (P23: eviction
    # is accretion's other half), or search keeps answering out of a
    # conversation that no longer exists.
    live = {d.name for d in root.iterdir() if d.is_dir()}
    for gone in set(have) - live:
        c.execute("DELETE FROM ev_fts WHERE pane=?", (gone,))
        c.execute("DELETE FROM panes WHERE id=?", (gone,))
    # PERSISTED, because search() answers from the index and the refresh that
    # found these may have been the throttled one an hour ago. A pane dir the
    # index cannot read is a conversation that is not in ANY answer, and until
    # 2026-09-14 only a CLI `refresh` ever printed it -- 28 such dirs on this
    # host, unfindable and unnamed, with nothing to say so (Grok 2).
    c.execute("INSERT OR REPLACE INTO index_meta VALUES('skipped',?)",
              (json.dumps(skipped[:200]),))
    return {"panes": panes, "rows": rows, "skipped": skipped}


_scan.calls = 0


def refresh(force=False, state_dir=None):
    """Diff the logs against the index; read only what is new."""
    global _last_refresh
    with _lock:
        if not force and time.time() - _last_refresh < REFRESH_S:
            return {"refreshed": False, "panes": 0, "rows": 0, "skipped": []}
        c = _db(state_dir)
        try:
            out = _scan(c, state_dir)
            c.commit()
        except sqlite3.DatabaseError as e:      # noqa: BLE001
            c.close()
            print(f"corral-light: transcripts refresh failed ({e}) — index rebuilt",
                  file=sys.stderr, flush=True)
            try:
                _db_path(state_dir).unlink()
            except OSError:
                pass
            c = _connect(state_dir)
            out = _scan(c, state_dir)
            c.commit()
        finally:
            try:
                c.close()
            except Exception:                   # noqa: BLE001
                pass
        _last_refresh = time.time()
        out["refreshed"] = True
        return out


# ── search ────────────────────────────────────────────────────────────────
def _fts_quote(q):
    """User text is never FTS syntax: every term is a quoted prefix token.
    Lifted from library.py deliberately -- the two indexes are separate and a
    shared helper across them would couple two lifecycles for four lines."""
    terms = re.findall(r"[\w'-]+", q or "")[:8]
    return " ".join('"' + t.replace('"', "") + '"*' for t in terms if t)


def search(q, limit=30, since=None, pane=None, agent=None, state_dir=None):
    """{"hits": [...], "partial": [...], "skipped": [...]} — never a bare list.

    `partial` names every pane whose index was trimmed at PANE_MAX_ROWS;
    `skipped` names every pane directory the last scan could not read at all —
    a broken or unreadable `meta.json`, and ONLY that since 2026-09-15. A dir
    with a log and no meta at all is indexed under its id and its hits carry
    `metaless: true`; it is a conversation, not a read error.
    BOTH ARE UNCONDITIONAL. `partial` used to be filtered to panes that also
    had a hit, so a search for a word that lives ONLY in trimmed rows returned
    `{hits: [], partial: []}` -- "no matches", which is the one answer that
    was not true (bug bash 2026-09-14: Astra 9, Grok 2; two live panes are
    already past the 20,000-row cap). An empty-and-silent result is the
    dangerous shape; incompleteness travels with the answer or it does not
    exist.
    """
    try:
        refresh(state_dir=state_dir)
    except NoFts5 as e:
        return {"hits": [], "partial": [], "skipped": [], "error": str(e)}
    limit = max(1, min(int(limit or 30), SEARCH_MAX_LIMIT))
    match = _fts_quote(q)
    c = _db(state_dir)
    if _last_refresh == 0.0:
        # _db() found the file unreadable and threw it away, and it does that
        # by zeroing the throttle. Without this the query that TRIGGERED the
        # rebuild runs against the empty replacement and answers "no matches"
        # -- the same silence as a real empty result (Astra 9, third case:
        # her repro corrupts the DB right after a refresh, when the throttle
        # would otherwise skip the scan).
        c.close()
        refresh(force=True, state_dir=state_dir)
        c = _db(state_dir)
    try:
        partial = [r[0] for r in c.execute(
            "SELECT id FROM panes WHERE partial=1 ORDER BY id")]
        row = c.execute("SELECT v FROM index_meta WHERE k='skipped'").fetchone()
        try:
            skipped = json.loads(row[0]) if row else []
        except ValueError:
            skipped = []
        if not isinstance(skipped, list):
            skipped = []
        if not match:
            # An empty query is not an error and is certainly not everything.
            return {"hits": [], "partial": partial, "skipped": skipped}
        where = ["ev_fts MATCH ?"]
        args = [match]
        if pane:
            where.append("f.pane = ?")
            args.append(str(pane))
        if since:
            where.append("f.at >= ?")
            args.append(str(since))
        if agent:
            where.append("p.agent = ?")
            args.append(str(agent))
        args.append(limit)
        rows = c.execute(
            "SELECT f.pane, f.seq, f.at, f.kind,"
            f"       snippet(ev_fts, 6, '‹', '›', '…', {SNIPPET_TOKENS}),"
            "       p.title, p.agent, p.closed, p.metaless"
            " FROM ev_fts f LEFT JOIN panes p ON p.id = f.pane"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY f.at DESC LIMIT ?", args).fetchall()
    except sqlite3.DatabaseError:
        # Degrade to "no answer", loudly typed, never a 500 up the stack.
        return {"hits": [], "partial": [], "skipped": [],
                "error": "index unreadable"}
    finally:
        c.close()
    hits = []
    for pid, seq, at, kind, snip, title, ag, closed, metaless in rows:
        # `metaless` travels WITH the hit, because the title is then the pane
        # id and the agent is "?" -- a surface that shows those without saying
        # why is inventing a conversation that looks badly named instead of
        # one whose meta.json is gone.
        hits.append({"pane": pid, "title": title or pid, "agent": ag or "?",
                     "closed": bool(closed), "seq": int(seq or 0),
                     "at": at or "", "kind": kind or "", "snippet": snip or "",
                     "metaless": bool(metaless)})
    return {"hits": hits, "partial": partial, "skipped": skipped}


# ── digest ────────────────────────────────────────────────────────────────
def _since_iso(hours):
    from datetime import datetime, timedelta, timezone
    hours = min(max(float(hours or 0), 0.0), float(DIGEST_MAX_HOURS))
    return (datetime.now(timezone.utc) - timedelta(hours=hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"), hours


def digest(hours, state_dir=None, live=None):
    """Mechanical what-the-agents-did, as markdown. No model summarizes
    anything; every number is a count over rows this index holds.

    `live` is the set of pane ids with a process attached RIGHT NOW, which
    only the running hub knows. Without it a non-closed pane reads `detached`
    -- the honest answer from disk alone, since a restored pane is detached
    until somebody resumes it.
    """
    try:
        refresh(state_dir=state_dir)
    except NoFts5 as e:
        return f"# What the agents did\n\n({e})\n"
    since, hours = _since_iso(hours)
    live = set(live or ())
    c = _db(state_dir)
    try:
        panes = {r[0]: r for r in c.execute(
            "SELECT id, title, agent, cwd, closed, partial FROM panes")}
        rows = c.execute(
            "SELECT pane, seq, at, kind, meta, body FROM ev_fts"
            " WHERE at >= ? ORDER BY at", (since,)).fetchall()
    except sqlite3.DatabaseError:
        rows, panes = [], {}
    finally:
        c.close()
    by = {}
    for pid, seq, at, kind, meta, body in rows:
        d = by.setdefault(pid, {"last": "", "turns": 0, "asks": [], "peers": 0,
                                "toolrows": [], "tools": 0,
                                "files": [], "perm": 0, "answered": 0,
                                "expired": 0, "dead": None})
        if at > d["last"]:
            d["last"] = at
        if kind == "user":
            d["turns"] += 1
            d["asks"].append((body or "").strip())
        elif kind == "tool":
            # Collected, not counted. One ACP tool call is one row plus a
            # stream of update rows for the same id, and a `read` names its
            # file exactly the way an `edit` does -- so counting rows here
            # reported five calls for two, and "edited untouched.py" for a
            # file nothing wrote (bug bash: Grok 3, Astra 11). The fold is
            # `transcript.merge_tools` / `edited_paths`, the same identity the
            # handoff pack uses.
            try:
                f = json.loads(meta) if meta else None
            except ValueError:
                f = None
            if isinstance(f, dict):
                d["toolrows"].append({"id": f.get("id") or "",
                                      "title": "", "kind": f.get("kind") or "",
                                      "status": f.get("status") or "",
                                      "paths": list(f.get("paths") or [])})
            else:
                d["toolrows"].append({"id": "", "title": "", "kind": "",
                                      "status": "", "paths": []})
        elif kind == "peer":
            # Counted APART from the human's turns: "3 turns" that were really
            # one ask and two messages from another agent would misreport who
            # drove this pane (DESIGN-5 S7, T7.8).
            d["peers"] += 1
        elif kind == "permission":
            d["perm"] += 1
        elif kind == "permission_answered":
            d["answered"] += 1
        elif kind == "permission_expired":
            d["expired"] += 1
        elif kind == "dead":
            d["dead"] = (body or "unknown").strip()
    for d in by.values():
        merged = transcript.merge_tools(d.pop("toolrows"))
        d["tools"] = len(merged)
        d["files"] = transcript.edited_paths(merged)
    order = sorted(by, key=lambda p: by[p]["last"], reverse=True)
    shown = order[:DIGEST_MAX_PANES]
    L = [f"# What the agents did — last {hours:g}h "
         f"(mechanical; transcripts.py — counted from events, no model)"]
    if not shown:
        L.append("\n(no pane activity in the window)")
    for pid in shown:
        d = by[pid]
        row = panes.get(pid)
        title = (row[1] if row else pid) or pid
        agent = (row[2] if row else "?") or "?"
        cwd = (row[3] if row else "") or ""
        closed = bool(row[4]) if row else False
        state = "archived" if closed else ("live" if pid in live else "detached")
        L.append(f"\n## {title} — {agent} · {cwd} · {state}")
        L.append(f"- turns: {d['turns']}" +
                 (f" (last {d['last']})" if d["last"] else ""))
        if d["asks"]:
            first = d["asks"][0].replace("\n", " ")[:DIGEST_ASK_CHARS]
            more = len(d["asks"]) - 1
            L.append(f'- asked: "{first}"' + (f" (+{more} more)" if more else ""))
        if d["peers"]:
            L.append(f"- peer messages received: {d['peers']}")
        if d["tools"]:
            files = d["files"]
            line = f"- tools: {d['tools']} calls"
            if files:
                line += (f" · edited {len(files)} file"
                         f"{'s' if len(files) != 1 else ''}: "
                         + ", ".join(files[:DIGEST_MAX_FILES])
                         + ("…" if len(files) > DIGEST_MAX_FILES else ""))
            L.append(line)
        if d["perm"] or d["answered"] or d["expired"]:
            L.append(f"- permissions: {d['perm']} asked · {d['answered']} "
                     f"answered · {d['expired']} expired")
        if d["dead"]:
            L.append(f'- ended: dead — "{d["dead"][:200]}"')
        if row and row[5]:
            L.append("- (index partial — older turns not counted)")
    if len(order) > len(shown):
        L.append(f"\n({len(order) - len(shown)} more pane(s) active in the "
                 f"window, not shown — the cap is {DIGEST_MAX_PANES})")
    return "\n".join(L) + "\n"


# ── CLI (P16) ─────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(prog="transcripts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="full-text search every pane's log")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--pane", default=None)
    s.add_argument("--agent", default=None)
    s.add_argument("--since", default=None, help="ISO timestamp floor")
    d = sub.add_parser("digest", help="mechanical what-the-agents-did")
    d.add_argument("--hours", type=float, default=24)
    r = sub.add_parser("refresh", help="re-read the logs into the index")
    r.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "search":
        out = search(args.query, limit=args.limit, since=args.since,
                     pane=args.pane, agent=args.agent)
        for h in out["hits"]:
            flag = " [archived]" if h["closed"] else ""
            print(f"{h['at']}  {h['title']}{flag} · {h['agent']} "
                  f"#{h['seq']} ({h['kind']})\n    {h['snippet']}", flush=True)
        if out.get("skipped"):
            print(f"\n({len(out['skipped'])} pane director"
                  f"{'y' if len(out['skipped']) == 1 else 'ies'} could not be "
                  f"read and are in NO answer: {', '.join(out['skipped'][:10])})",
                  file=sys.stderr, flush=True)
        if out["partial"]:
            print(f"\n(index partial for: {', '.join(out['partial'])} — older "
                  f"turns beyond {PANE_MAX_ROWS} rows are on disk, not indexed)",
                  file=sys.stderr, flush=True)
        if not out["hits"]:
            print("(nothing)", flush=True)
    elif args.cmd == "digest":
        sys.stdout.write(digest(args.hours))
    elif args.cmd == "refresh":
        print(json.dumps(refresh(force=args.force), indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
