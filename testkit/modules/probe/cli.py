"""Fixture CLI: prints its argv and environment names; `doctor` says ok;
`--sleep N` sleeps quietly (a long interactive run)."""
import os
import sys
import time

if sys.argv[1:2] == ["--sleep"]:
    time.sleep(float(sys.argv[2]))
elif sys.argv[1:] == ["doctor"]:
    print("probe doctor: ok")
else:
    print("probe cli:", " ".join(sys.argv[1:]))
    print("env:", ",".join(sorted(os.environ)))
    cfg = os.environ.get("CORRAL_MODULE_CONFIG")
    if "--write-config" in sys.argv:
        with open(cfg, "w") as f:
            f.write("ok\n")
        print("wrote config")
