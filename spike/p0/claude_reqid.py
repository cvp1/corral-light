"""Phase 0: Claude transcript requestId multiplicity. Prints aggregates only."""
import json, glob, os, time, collections, sys
H = os.path.expanduser
roots = {"home": H("~/.claude/projects"),
         "panes": H("~/.local/share/corral-light/panes")}
files = {"home": glob.glob(roots["home"] + "/**/*.jsonl", recursive=True),
         "panes": glob.glob(roots["panes"] + "/*/config/projects/**/*.jsonl", recursive=True)}
t0 = time.time(); nbytes = 0
recs = collections.defaultdict(list)   # requestId -> [(src, file, line#, usage, model, msgid, ts)]
lines = bad = noreq = synth = 0
for src, fl in files.items():
    for f in fl:
        nbytes += os.path.getsize(f)
        with open(f, "rb") as fh:
            for i, raw in enumerate(fh):
                lines += 1
                try: d = json.loads(raw)
                except Exception: bad += 1; continue
                if d.get("type") != "assistant": continue
                m = d.get("message") or {}
                u = m.get("usage")
                if not u: continue
                if m.get("model") == "<synthetic>": synth += 1; continue
                rid = d.get("requestId")
                if not rid: noreq += 1; continue
                recs[rid].append((src, f, i, u, m.get("model"), m.get("id"), d.get("timestamp"), d.get("uuid"), [c.get("type") for c in (m.get("content") or []) if isinstance(c, dict)]))
el = time.time() - t0
print(f"files home={len(files['home'])} panes={len(files['panes'])} bytes={nbytes/1e6:.1f}MB lines={lines} bad={bad} scan={el:.2f}s")
print(f"assistant usage recs with requestId={sum(map(len,recs.values()))} distinct={len(recs)} noreq={noreq} synthetic={synth}")
mult = collections.Counter(len(v) for v in recs.values())
print("multiplicity:", sorted(mult.items())[:15])
# within-multi: do usages differ? which field differs?
same = diff = 0; difffields = collections.Counter(); crossfile = crosssrc = 0
outmono = outnot = 0; lastmax = lastnot = 0; msgids = collections.Counter()
for rid, v in recs.items():
    if len(v) < 2: continue
    files_ = {x[1] for x in v}; srcs = {x[0] for x in v}
    if len(files_) > 1: crossfile += 1
    if len(srcs) > 1: crosssrc += 1
    msgids[len({x[5] for x in v})] += 1
    us = [x[3] for x in v]
    if all(json.dumps(u, sort_keys=True) == json.dumps(us[0], sort_keys=True) for u in us): same += 1
    else:
        diff += 1
        for k in set().union(*us):
            if len({json.dumps(u.get(k), sort_keys=True) for u in us}) > 1: difffields[k] += 1
    # within one file, ordered by line: is output_tokens nondecreasing; is last == max
    byf = collections.defaultdict(list)
    for x in v: byf[x[1]].append(x)
    for f, xs in byf.items():
        xs.sort(key=lambda x: x[2]); o = [x[3].get("output_tokens", 0) for x in xs]
        if len(o) > 1:
            outmono += all(a <= b for a, b in zip(o, o[1:])); outnot += not all(a <= b for a, b in zip(o, o[1:]))
            lastmax += o[-1] == max(o); lastnot += o[-1] != max(o)
print(f"multi: identical-usage={same} differing={diff} cross-file={crossfile} cross-source(home vs pane)={crosssrc}")
print("fields differing:", difffields.most_common())
print("distinct message.id per requestId:", sorted(msgids.items()))
print(f"within-file output_tokens nondecreasing={outmono} not={outnot}; last-is-max={lastmax} not={lastnot}")
# usage keys and server_tool_use / service_tier
keys = collections.Counter(); tiers = collections.Counter(); models = collections.Counter()
for v in recs.values():
    for x in v:
        keys.update(x[3].keys()); tiers[x[3].get("service_tier")] += 1; models[x[4]] += 1
print("usage keys:", keys.most_common())
print("service_tier:", tiers.most_common()); print("models:", models.most_common(12))
