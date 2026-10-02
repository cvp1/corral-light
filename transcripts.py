#!/usr/bin/python3
"""transcripts — full-text search across every pane's log, and a mechanical
digest of what the agents did in a window.

The index is a derived, disposable SQLite FTS5 DB at
$CORRAL_LIGHT_STATE/transcripts.db, diffed by mtime/offset and rebuilt if
unreadable. Permission events are stored with an empty body (countable, never
findable); `thought` is not stored. The digest counts index rows; no model.

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

# Bound the same way sessions.STATE is.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))

REFRESH_S = 60                  # at most one scan a minute
ROW_MAX_CHARS = 8000            # per indexed row; head kept, tail marked …
PANE_MAX_ROWS = 20000           # newest rows per pane held in the INDEX
TOOL_BODY_CHARS = 2000          # of a tool call's result text
DIGEST_MAX_PANES = 40
DIGEST_MAX_HOURS = 24 * 7
DIGEST_ASK_CHARS = 120
DIGEST_MAX_FILES = 8
SEARCH_MAX_LIMIT = 200
SNIPPET_TOKENS = 14

# Bodies that are searchable (`peer` = another pane's agent's message).
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


# Bump when what a row means changes; a mismatched index is dropped and rebuilt.
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
    # tail_rowid/tail_off: the still-growing text run indexed last pass and its
    # byte offset; replaced whole by rowid on the next pass.
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
    """Open the index, rebuilding it from scratch if it is unreadable
    (reported once on stderr)."""
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
        # Carry the call id so the digest can fold ACP update rows into one call.
        meta = json.dumps({"id": f.get("id") or "", "kind": f.get("kind") or "",
                           "status": f.get("status") or "",
                           "paths": [_clip(x) for x in (f.get("paths") or [])[:8]]})
        return _clip(body), meta
    return _clip(d.get("text") or ""), ""


def _rows(pane_id, events, offsets=None, hold_tail=False):
    """(rows, rewind_offset) — index rows for `events`.

    Adjacent text chunks join into one row (so phrases spanning chunks match),
    keyed by the first chunk's seq. With `hold_tail`, `rewind` is the offset of
    a trailing open text run that the next pass re-reads and replaces.
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
            continue
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
            rewind = run[3]         # re-read from here next pass
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
    """Hold at most PANE_MAX_ROWS newest rows for one pane; returns whether
    anything was dropped (search() reports it as partial)."""
    n = c.execute("SELECT COUNT(*) FROM ev_fts WHERE pane=?",
                  (pane_id,)).fetchone()[0]
    if n <= PANE_MAX_ROWS:
        return False
    c.execute("DELETE FROM ev_fts WHERE rowid IN ("
              " SELECT rowid FROM ev_fts WHERE pane=? ORDER BY rowid LIMIT ?)",
              (pane_id, n - PANE_MAX_ROWS))
    return True


#: A pane directory with no `meta.json` at all (distinct from an unreadable one).
_META_MISSING = object()


def _meta_of(d):
    """The pane's meta dict; `_META_MISSING` if there is no meta.json (the
    log is still indexed under its id); `{}` if meta.json is unreadable or
    invalid (the pane is skipped)."""
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
    """One pass over every pane directory (calls counted for tests)."""
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
            # Index under its id with agent "?"; hits carry `metaless`.
            if not cur.is_file():
                skipped.append(pane_id)  # no meta and no log
                continue
            m = {"id": pane_id, "title": pane_id, "agent": "?", "closed": True}
        elif not m.get("id"):
            skipped.append(pane_id)      # unreadable meta
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
            # Full (re)index. Read the rotated `.1` log first so rowid order
            # stays chronological (the trim drops oldest).
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
                    # Drop the open run's row; it is re-read whole below.
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
        # Only a pane that gained rows can cross the cap; _trim is a table scan.
        if gained and _trim(c, pane_id):
            partial = True
        c.execute(
            "INSERT OR REPLACE INTO panes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pane_id, m.get("title") or pane_id, m.get("agent") or "?",
             m.get("cwd") or "", m.get("created") or "",
             1 if m.get("closed") else 0, gen_now, off,
             1 if partial else 0, tail_rowid, off, 1 if metaless else 0))
        panes += 1
    # Evict panes whose directories are gone.
    live = {d.name for d in root.iterdir() if d.is_dir()}
    for gone in set(have) - live:
        c.execute("DELETE FROM ev_fts WHERE pane=?", (gone,))
        c.execute("DELETE FROM panes WHERE id=?", (gone,))
    # Persist skipped dirs so a throttled search() can still report them.
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
    """User text is never FTS syntax: every term is a quoted prefix token."""
    terms = re.findall(r"[\w'-]+", q or "")[:8]
    return " ".join('"' + t.replace('"', "") + '"*' for t in terms if t)


def search(q, limit=30, since=None, pane=None, agent=None, state_dir=None):
    """{"hits": [...], "partial": [...], "skipped": [...]}.

    `partial` (panes trimmed at PANE_MAX_ROWS) and `skipped` (pane dirs with
    unreadable meta) are always returned, whether or not anything matched.
    """
    try:
        refresh(state_dir=state_dir)
    except NoFts5 as e:
        return {"hits": [], "partial": [], "skipped": [], "error": str(e)}
    limit = max(1, min(int(limit or 30), SEARCH_MAX_LIMIT))
    match = _fts_quote(q)
    c = _db(state_dir)
    if _last_refresh == 0.0:
        # _db() just rebuilt an unreadable index; repopulate before querying.
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
            # An empty query matches nothing.
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
        # Degrade to a typed error, never a 500.
        return {"hits": [], "partial": [], "skipped": [],
                "error": "index unreadable"}
    finally:
        c.close()
    hits = []
    for pid, seq, at, kind, snip, title, ag, closed, metaless in rows:
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
    """Mechanical what-the-agents-did, as markdown, counted from index rows.

    `live` is the set of pane ids with a process attached (known only to the
    hub); other non-closed panes read `detached`.
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
            # Collected, then folded by call id via transcript.merge_tools.
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
            # Counted apart from the human's turns.
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


# ── CLI ───────────────────────────────────────────────────────────────────
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
