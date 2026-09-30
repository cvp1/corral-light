#!/usr/bin/env python3
"""tomlmini (DESIGN-5 S12): the strict reader reads what Corral writes exactly
as tomllib does, and refuses everything else rather than guessing.

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from corral_core import tomlmini                                  # noqa: E402

try:
    import tomllib
except ImportError:                                               # 3.9/3.10
    tomllib = None

SAME = [
    '# c\nschema = 1\ndescription = "a \\"q\\" b"\n',
    'version = 1\n\n[[seat]]\nid = "a"\n[[seat]]\nid = "b"  # trailing\n',
    'a = "\\u00e9\\U0001F600\\t\\\\"\nb = -12\nc = 0\nd = 1_000\n',
    'x = "tab\tinside"\n',
]
REFUSED = ['[table]\n', 'a = [1,2]\n', 'a = "x"\na = "y"\n', "a = 'lit'\n",
           'a = """m"""\n', 'a = 1.5\n', 'a = true\n', 'a.b = 1\n', 'a = 01\n',
           'a = "x" y\n', 'a = "\\q"\n', 'a = "unterminated\n',
           '[[s]]\nk = 1\nk = 2\n', 's = 1\n[[s]]\n', 'a = "\x01"\n',
           'a = "\\uD800"\n', 'a = { b = 1 }\n']


class TheStrictReader(unittest.TestCase):
    def test_what_it_reads_it_reads_as_tomllib_does(self):
        for t in SAME:
            got = tomlmini.loads_strict(t)
            if tomllib:
                self.assertEqual(got, tomllib.loads(t), t)
        self.assertEqual(tomlmini.loads_strict(SAME[1]),
                         {"version": 1, "seat": [{"id": "a"}, {"id": "b"}]})

    def test_everything_else_is_refused(self):
        for t in REFUSED:
            with self.assertRaises(ValueError, msg=t):
                tomlmini.loads_strict(t)

    def test_basic_round_trips_any_string(self):
        for v in ['', 'plain', 'q"uote', 'back\\slash', 'nl\nand\rcr',
                  'bell\x07 del\x7f', 'é 😀', '"\n[[seat]]\nid = "evil"']:
            t = f"k = {tomlmini.basic(v)}\n"
            self.assertEqual(tomlmini.loads_strict(t), {"k": v}, repr(v))
            if tomllib:
                self.assertEqual(tomllib.loads(t), {"k": v}, repr(v))

    def test_the_rig_template_reads_the_same_both_ways(self):
        t = (HERE / "rig.example.toml").read_text(encoding="utf-8")
        doc = tomlmini.loads_strict(t)
        self.assertEqual(doc["version"], 1)
        self.assertEqual([s["id"] for s in doc["seat"]], ["author", "reviewer"])
        if tomllib:
            self.assertEqual(doc, tomllib.loads(t))


if __name__ == "__main__":
    unittest.main()
