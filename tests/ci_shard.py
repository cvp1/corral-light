"""Run one CI shard of the main suite (test_corral_light.py and everything it
imports), split by test class on measured duration.

  python3 tests/ci_shard.py run I N      run shard I of N (1-based)
  python3 tests/ci_shard.py plan N       every shard's classes and seconds
  python3 tests/ci_shard.py refresh < ci.log
                                         rewrite ci_durations.json from the
                                         verbose unittest lines of a CI log

Longest class first onto the least-loaded shard, ties by name, so every shard
computes the same split. Before running, a shard checks that the N shards
together hold every test exactly once; a class the split dropped would
otherwise never run in CI and nothing would say so.
"""
import datetime
import json
import os
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DURATIONS = Path(__file__).with_name("ci_durations.json")
DEFAULT = 5.0   # seconds for a class with no recorded time


def tests_by_class():
    """{key: (attribute name in test_corral_light, [test ids])}; key is the
    defining module.Class, as a CI log names it."""
    sys.path.insert(0, str(ROOT))
    import test_corral_light as mod
    names = {}
    for attr, v in vars(mod).items():
        if isinstance(v, type) and issubclass(v, unittest.TestCase):
            names.setdefault(v, attr)

    def flat(s):
        for t in s:
            yield from flat(t) if isinstance(t, unittest.TestSuite) else [t]
    out = {}
    for t in flat(unittest.defaultTestLoader.loadTestsFromModule(mod)):
        cls = type(t)
        key = f"{cls.__module__.replace('__main__', 'test_corral_light')}.{cls.__qualname__}"
        out.setdefault(key, (names[cls], []))[1].append(t.id())
    return out


def split(keys, n):
    secs = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    shards = [[0.0, []] for _ in range(n)]
    for k in sorted(set(keys), key=lambda x: (-secs.get(x, DEFAULT), x)):
        load = min(range(n), key=lambda i: (shards[i][0], i))
        shards[load][0] += secs.get(k, DEFAULT)
        shards[load][1].append(k)
    return shards


def refresh(log):
    per, prev = Counter(), None
    for line in log.splitlines():
        m = re.search(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6})\d*Z\s+\S+ \(([\w.]+)\.\w+\) \.\.\. ", line)
        if not m:
            continue
        t = datetime.datetime.fromisoformat(m[1])
        if prev:
            per[m[2].replace("__main__", "test_corral_light")] += (t - prev).total_seconds()
        prev = t
    if not per:
        sys.exit("no verbose unittest lines with timestamps on stdin")
    DURATIONS.write_text(json.dumps({k: round(v, 1) for k, v in sorted(per.items())}, indent=0) + "\n")
    print(f"{len(per)} class durations written to {DURATIONS.name}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "refresh":
        return refresh(sys.stdin.read())
    if cmd not in ("run", "plan") or len(argv) != (4 if cmd == "run" else 3):
        sys.exit(__doc__)
    n = int(argv[-1])
    classes = tests_by_class()
    shards = split(classes, n)
    if cmd == "plan":
        for i, (secs, keys) in enumerate(shards, 1):
            print(f"shard {i}: ~{secs:.0f}s, {len(keys)} classes")
        return
    i = int(argv[2])
    if not 1 <= i <= n:
        sys.exit(f"shard {i} of {n}?")
    every = sorted(t for _, ids in classes.values() for t in ids)
    covered = sorted(t for _, keys in shards for k in keys for t in classes[k][1])
    if covered != every or not all(keys for _, keys in shards):
        sys.exit(f"the split does not hold every test once ({len(covered)} of {len(every)})")
    picked = [f"test_corral_light.{classes[k][0]}" for k in shards[i - 1][1]]
    print(f"shard {i}/{n}: {len(picked)} classes, "
          f"{sum(len(classes[k][1]) for k in shards[i - 1][1])} of {len(every)} tests", flush=True)
    os.chdir(ROOT)
    unittest.main(module=None, argv=["ci_shard", "-v", *picked])


if __name__ == "__main__":
    main(sys.argv)
