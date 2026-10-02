#!/usr/bin/env python3
"""Seat names: the grammar and the collision rule (earliest-created wins).

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corral_core import sessions as S                            # noqa: E402


class TheGrammar(unittest.TestCase):
    def test_good_names(self):
        for name in ("a", "reviewer", "author-2", "x" * 32, "r2-d2"):
            self.assertEqual(S.check_seat(name), name)

    def test_unbinding_is_none_or_empty(self):
        for name in (None, "", "   "):
            self.assertIsNone(S.check_seat(name))

    def test_surrounding_whitespace_is_not_part_of_a_name(self):
        self.assertEqual(S.check_seat("  reviewer "), "reviewer")

    def test_bad_names_are_refused_with_the_rule(self):
        """Invalid names, including uppercase, are refused rather than folded."""
        for bad in ("Reviewer", "2fast", "x" * 33, "révieweur", "has space",
                    "-lead", "under_score", "a.b", "@reviewer"):
            with self.assertRaises(ValueError, msg=bad) as e:
                S.check_seat(bad)
            self.assertIn("lowercase letter", str(e.exception), bad)

    def test_a_non_string_is_refused(self):
        for bad in (7, ["reviewer"], {"seat": "x"}):
            with self.assertRaises(ValueError):
                S.check_seat(bad)


def meta(pid, seat=None, created="2026-09-29T10:00:00Z", closed=False):
    m = {"id": pid, "created": created, "closed": closed}
    if seat is not None:
        m["seat"] = seat
    return m


class TheCollisionRule(unittest.TestCase):
    def test_the_earlier_created_keeps_the_seat(self):
        out = S.withheld_seats([
            meta("later", "reviewer", "2026-09-29T12:00:00Z"),
            meta("first", "reviewer", "2026-09-29T09:00:00Z"),
        ])
        self.assertEqual(out, {"later": ("reviewer", "first")})

    def test_three_way_withholds_two(self):
        out = S.withheld_seats([meta("a", "x", "1"), meta("b", "x", "2"),
                                meta("c", "x", "3")])
        self.assertEqual(out, {"b": ("x", "a"), "c": ("x", "a")})

    def test_a_closed_meta_holds_nothing(self):
        """Closing a pane frees its name."""
        out = S.withheld_seats([meta("old", "x", "1", closed=True),
                                meta("new", "x", "2")])
        self.assertEqual(out, {})

    def test_no_seat_and_distinct_seats_collide_with_nothing(self):
        self.assertEqual(S.withheld_seats([meta("a"), meta("b"),
                                           meta("c", "x"), meta("d", "y")]), {})

    def test_a_tie_on_created_is_broken_by_id_so_the_answer_is_stable(self):
        """Equal timestamps break by id, independent of listing order."""
        a = S.withheld_seats([meta("bbb", "x", "same"), meta("aaa", "x", "same")])
        b = S.withheld_seats([meta("aaa", "x", "same"), meta("bbb", "x", "same")])
        self.assertEqual(a, b)
        self.assertEqual(a, {"bbb": ("x", "aaa")})


class SeatIsPersisted(unittest.TestCase):
    def test_seat_is_a_meta_key(self):
        self.assertIn("seat", S.PaneBase.META_KEYS)

    def test_the_default_is_unaddressable_and_not_withheld(self):
        self.assertIsNone(S.PaneBase.seat)
        self.assertFalse(S.PaneBase.seat_withheld)
        self.assertNotIn("seat_withheld", S.PaneBase.META_KEYS,
                         "withheld is DERIVED from the files at restore; "
                         "persisting it would freeze a view (P9)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
