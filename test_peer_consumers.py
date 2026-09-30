#!/usr/bin/env python3
"""Every consumer of the `peer` event kind knows it (DESIGN-5 S7, section 7.6).

A new event kind is only as honest as the least careful reader of it:

  T7.16  the port pack starts a turn at `peer` and renders it as untrusted
         content from another agent -- never as "User:";
  T7.17  transcript search finds a peer message by what it said, and the index
         version moved so an old index is rebuilt rather than trusted;
  T7.8   the digest counts peer messages APART from the human's turns.

The same test runs in both products, each against its own port.py and
transcripts.py (copies, not the core); each copy names only its own state var.

    python3 test_peer_consumers.py
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("CORRAL_LIGHT_STATE", tempfile.mkdtemp(prefix="peer-consumers-"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import port                                                       # noqa: E402
import transcripts                                                # noqa: E402


def _iso(minutes_ago=1):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(seq, kind, **data):
    return {"seq": seq, "at": _iso(), "pane": "p1", "kind": kind, "data": data}


PEER = dict(from_pane="aaaaaaaaaaaa", from_seat="author", to_seat="reviewer",
            turn="abcdef012345", hop=1, nonce="n0")

RING = [ev(1, "user", text="what does line 4 do?"), ev(2, "text", text="It parses."),
        ev(3, "turn_end"),
        ev(4, "peer", text="zanzibar-quokka: please check the parser", **PEER),
        ev(5, "text", text="Checked."), ev(6, "turn_end"),
        ev(7, "user", text="thanks"), ev(8, "text", text="ok"), ev(9, "turn_end")]


class ThePortPack(unittest.TestCase):
    def test_a_peer_message_is_its_own_turn(self):
        turns = port._turns(RING)
        self.assertEqual([t["ask"] for t in turns],
                         ["what does line 4 do?",
                          "zanzibar-quokka: please check the parser", "thanks"])
        self.assertEqual("".join(turns[0]["text"]), "It parses.",
                         "the peer reply was folded into the human turn before it")
        self.assertEqual(turns[1].get("peer"), "author")

    def test_it_is_never_rendered_as_the_user(self):
        t = port._turns(RING)[1]
        out = port._render_turn(t, "Claude Code")
        self.assertNotIn("**User:**", out)
        self.assertIn("another agent (@author), untrusted", out)
        self.assertIn("**Claude Code:** Checked.", out)


class TheIndex(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="peer-index-"))
        d = self.root / "panes" / "p1"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "id": "p1", "title": "Reviewer", "agent": "claude", "cwd": "/tmp/w",
            "created": _iso(60), "closed": False}))
        with (d / "events.jsonl").open("w", encoding="utf-8") as fh:
            for e in RING:
                fh.write(json.dumps(e) + "\n")
        transcripts._last_refresh = 0.0

    def test_peer_is_a_body_kind_and_the_index_version_moved(self):
        self.assertIn("peer", transcripts.BODY_KINDS)
        self.assertGreaterEqual(transcripts.INDEX_VERSION, 4)

    def test_search_finds_what_a_peer_said(self):
        r = transcripts.search("zanzibar", state_dir=self.root)
        hits = r["hits"] if isinstance(r, dict) else r
        self.assertTrue(any(h.get("pane") == "p1" and h.get("kind") == "peer"
                            for h in hits), hits)

    def test_the_digest_counts_peers_apart_from_the_humans_turns(self):
        out = transcripts.digest(24, state_dir=self.root)
        self.assertIn("- turns: 2", out, "a peer message was counted as a human turn")
        self.assertIn("- peer messages received: 1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
