#!/usr/bin/python3
"""content — a searchable index of the markdown you point it at.

WHY THIS IS NOT THE FULL CORRAL'S `library.py`
    Upstream's Library is a ROOM: navigate to it, browse a corpus, read a
    rendered page, then click "open agent here". That shape exists because
    Corral has seven rooms and content needed somewhere to live. Light has one
    room, so the room half — routing, page rendering, wikilink resolution,
    backlinks, pinned and recent places — is navigation scaffolding for a
    building with one floor.

    So Light keeps the half that is actually load-bearing (the index) and
    changes what it is FOR: not a place to go, but content reachable from the
    composer. ⌘K, search, attach.

    The consequence worth naming: **nothing here is ever rendered in the
    browser.** Upstream needs `mdview.py` — 217 lines of escape-everything,
    emit-only-tags-we-spell-out — precisely because vault notes carry pasted
    third-party content and Corral is an authed control surface (P20). By
    never rendering a page, Light does not need that renderer, and the attack
    surface it defends goes with it. This module returns titles, paths and
    FTS-generated snippets; the snippet is the only file-derived text that
    reaches the page, and the client inserts it as TEXT, never as markup.

WHAT IT INDEXES
    Whatever `~/.config/corral-light/content.json` names, or `~/notes` if that
    file is absent and that directory exists. Never a hardcoded fleet corpus
    list: this has to be useful on a box that has one directory of notes and
    nothing else, and a config that ships pointing at five directories nobody
    has is a feature that is broken on arrival.

    Config: [{"key": "vault", "label": "notes", "root": "~/notes"}, ...]

BOUNDS (P8)
    Files per root, bytes per file, results per query, and terms per query are
    all capped. The index is DERIVED and disposable — delete content.db and it
    rebuilds; nothing here is a system of record.
"""
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
CONFIG = Path(os.environ.get("CORRAL_CONTENT_CONFIG",
                             Path.home() / ".config/corral-light/content.json"))
DB = STATE / "content.db"

MAX_FILE = 200_000          # bytes read per file into the index
MAX_FILES_PER_ROOT = 20_000  # bound the walk
MAX_ROOTS = 12
REFRESH_S = 60              # at most one filesystem diff a minute
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git",
             ".obsidian", ".trash", "site-packages"}
SUFFIXES = (".md", ".markdown", ".txt")

_lock = threading.Lock()
_last_refresh = 0.0
_fts = None                 # None = not probed yet; bool once known


def roots():
    """[{key, label, root: Path}] — configured, or the ~/notes default.

    Unreadable or malformed config is an EMPTY list plus an error, never a
    silent fallback to the default: a typo in the config should not look
    identical to having no config, or the operator spends the afternoon
    wondering why their new root never appears.
    """
    if CONFIG.is_file():
        try:
            raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return [], f"{CONFIG} is unreadable: {e}"
        if not isinstance(raw, list):
            return [], f"{CONFIG} must be a list of {{key, label, root}} objects"
        out = []
        for entry in raw[:MAX_ROOTS]:
            if not isinstance(entry, dict) or not entry.get("root"):
                continue
            p = Path(str(entry["root"])).expanduser()
            key = str(entry.get("key") or p.name or "root")
            out.append({"key": key, "label": str(entry.get("label") or key),
                        "root": p})
        return out, ""
    default = Path.home() / "notes"
    if default.is_dir():
        return [{"key": "notes", "label": "notes", "root": default}], ""
    return [], (f"no content roots — create {CONFIG} with "
                f'[{{"key": "notes", "label": "notes", "root": "~/notes"}}]')


def _iter_files(root):
    """Yield indexable files under root, pruning dot/skip dirs and symlinks
    that escape it. Bounded (P8)."""
    if not root.is_dir():
        return
    try:
        base = root.resolve()
    except OSError:
        return
    stack, seen = [root], 0
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name.startswith(".") or p.name in SKIP_DIRS:
                continue
            if p.is_dir() and not p.is_symlink():
                stack.append(p)
            elif p.is_file() and p.name.endswith(SUFFIXES):
                try:
                    if not p.resolve().is_relative_to(base):
                        continue                 # a symlink pointing outside
                except OSError:
                    continue
                seen += 1
                if seen > MAX_FILES_PER_ROOT:
                    return
                yield p


_TITLE = re.compile(r"^#\s+(.+)$", re.M)


def _title_of(path, text):
    m = _TITLE.search(text[:4000])
    return (m.group(1).strip() if m else path.stem)[:160]


def _connect():
    """Open the index, creating it. Probes FTS5 ONCE and remembers.

    A missing FTS5 must degrade to a LIKE scan that SAYS it is one — not
    silently get slower, and not take search down. Apple's system sqlite ships
    FTS5 and so does Debian's, but "ships it here" is not evidence about the
    box this ends up on, and finding out at query time on a laptop is how a
    feature becomes 'search is broken'.
    """
    global _fts
    STATE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS pages(
        id TEXT PRIMARY KEY, corpus TEXT, path TEXT, title TEXT,
        rel TEXT, body TEXT, mtime REAL, size INTEGER)""")
    if _fts is None:
        try:
            c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
                USING fts5(id UNINDEXED, title, body)""")
            _fts = True
        except sqlite3.OperationalError:
            _fts = False
    return c


def fts_available():
    if _fts is None:
        _connect().close()
    return bool(_fts)


def refresh(force=False):
    """Diff the filesystem against the index; re-read only what changed."""
    global _last_refresh
    with _lock:
        if not force and time.time() - _last_refresh < REFRESH_S:
            return {"refreshed": False}
        configured, err = roots()
        c = _connect()
        try:
            have = {r[0]: (r[1], r[2]) for r in
                    c.execute("SELECT id, mtime, size FROM pages")}
            live, added, updated = set(), 0, 0
            for spec in configured:
                root = spec["root"]
                for p in _iter_files(root):
                    rel = str(p.relative_to(root))
                    pid = f"{spec['key']}:{rel}"
                    live.add(pid)
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    prev = have.get(pid)
                    if (prev and abs(prev[0] - st.st_mtime) < 1e-6
                            and prev[1] == st.st_size):
                        continue
                    try:
                        text = p.read_text(errors="replace")[:MAX_FILE]
                    except OSError:
                        continue
                    title = _title_of(p, text)
                    c.execute("INSERT OR REPLACE INTO pages "
                              "VALUES(?,?,?,?,?,?,?,?)",
                              (pid, spec["key"], str(p), title, rel, text,
                               st.st_mtime, st.st_size))
                    if _fts:
                        c.execute("DELETE FROM pages_fts WHERE id=?", (pid,))
                        c.execute("INSERT INTO pages_fts VALUES(?,?,?)",
                                  (pid, title, text))
                    added += 0 if prev else 1
                    updated += 1 if prev else 0
            gone = set(have) - live
            for pid in gone:
                c.execute("DELETE FROM pages WHERE id=?", (pid,))
                if _fts:
                    c.execute("DELETE FROM pages_fts WHERE id=?", (pid,))
            c.commit()
        finally:
            c.close()
        _last_refresh = time.time()
        return {"refreshed": True, "added": added, "updated": updated,
                "removed": len(gone), "roots": len(configured), "error": err}


def _fts_query(q):
    """User text is never FTS syntax: every term becomes a quoted prefix token.

    Without this, a note titled `C++ (notes)` typed into the box is a syntax
    error from sqlite rather than a search, and an apostrophe or a bare `*`
    can make the query mean something the person did not type.
    """
    terms = re.findall(r"[\w'-]+", q or "")[:8]
    return " ".join('"' + t.replace('"', "") + '"*' for t in terms if t)


def _excerpt(body, q, width=240):
    """A plain-text window around the first matching term. Never markup.

    Used for the LIKE fallback, and as the text the client offers to quote
    into a chat-only pane. Returned as TEXT; the client inserts it with
    textContent, so an angle bracket in a note is an angle bracket.
    """
    terms = [t for t in re.findall(r"[\w'-]+", q or "") if t]
    lo = body.lower()
    at = min((lo.find(t.lower()) for t in terms if lo.find(t.lower()) >= 0),
             default=-1)
    if at < 0:
        return " ".join(body[:width].split())
    start = max(0, at - width // 3)
    return ("…" if start else "") + \
        " ".join(body[start:start + width].split()) + "…"


def search(q, limit=25):
    """Ranked hits: {id, corpus, path, title, rel, snippet}. Never raises."""
    q = (q or "").strip()
    if not q:
        return {"hits": [], "error": ""}
    try:
        refresh()
    except Exception as e:                     # noqa: BLE001 — a stale index
        return {"hits": [], "error": f"index refresh failed: {e}"[:200]}
    limit = max(1, min(int(limit or 25), 50))
    c = _connect()
    try:
        if _fts:
            match = _fts_query(q)
            if not match:
                return {"hits": [], "error": ""}
            rows = c.execute(
                "SELECT p.id, p.corpus, p.path, p.title, p.rel,"
                "       snippet(pages_fts, 2, '', '', '…', 14)"
                " FROM pages_fts f JOIN pages p ON p.id = f.id"
                " WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT ?",
                (match, limit)).fetchall()
            hits = [{"id": r[0], "corpus": r[1], "path": r[2], "title": r[3],
                     "rel": r[4], "snippet": " ".join((r[5] or "").split())}
                    for r in rows]
            return {"hits": hits, "error": ""}
        # No FTS5. A LIKE scan over titles and bodies, ordered by title match
        # first — and the caller is TOLD, so "search got worse" has a cause
        # attached rather than being a mystery about a laptop.
        like = f"%{q}%"
        rows = c.execute(
            "SELECT id, corpus, path, title, rel, body FROM pages"
            " WHERE title LIKE ? OR body LIKE ?"
            " ORDER BY (title LIKE ?) DESC, mtime DESC LIMIT ?",
            (like, like, like, limit)).fetchall()
        hits = [{"id": r[0], "corpus": r[1], "path": r[2], "title": r[3],
                 "rel": r[4], "snippet": _excerpt(r[5], q)} for r in rows]
        return {"hits": hits,
                "error": "this sqlite has no FTS5 — falling back to a plain "
                         "substring scan, so ranking is by title match and "
                         "recency only"}
    except sqlite3.Error as e:
        return {"hits": [], "error": f"search failed: {e}"[:200]}
    finally:
        c.close()


def get(place_id):
    """One indexed file: its path, title, and a bounded body. None if gone."""
    c = _connect()
    try:
        r = c.execute("SELECT id, corpus, path, title, rel, body FROM pages"
                      " WHERE id=?", (place_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    if not r:
        return None
    return {"id": r[0], "corpus": r[1], "path": r[2], "title": r[3],
            "rel": r[4], "body": r[5]}


def status():
    """What the index knows, for the empty state. Cheap; safe to call often."""
    configured, err = roots()
    try:
        r = refresh()
        err = err or (r.get("error") or "")
    except Exception as e:                     # noqa: BLE001
        return {"roots": [], "pages": 0, "fts": fts_available(),
                "error": f"index unavailable: {e}"[:200]}
    c = _connect()
    try:
        counts = dict(c.execute(
            "SELECT corpus, count(*) FROM pages GROUP BY corpus").fetchall())
        total = sum(counts.values())
    except sqlite3.Error:
        counts, total = {}, 0
    finally:
        c.close()
    return {"roots": [{"key": s["key"], "label": s["label"],
                       "root": str(s["root"]), "exists": s["root"].is_dir(),
                       "pages": counts.get(s["key"], 0)} for s in configured],
            "pages": total, "fts": bool(_fts), "error": err}


if __name__ == "__main__":            # a CLI, so the index is inspectable
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(), indent=1), flush=True)
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        print(json.dumps(search(" ".join(sys.argv[2:])), indent=1), flush=True)
    elif len(sys.argv) > 1 and sys.argv[1] == "refresh":
        print(json.dumps(refresh(force=True), indent=1), flush=True)
    else:
        print("usage: python3 content.py {status|refresh|search <query>}",
              flush=True)
