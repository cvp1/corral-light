import sqlite3, glob, os, re, collections, statistics, datetime, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse
UUID = re.compile(rb'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
def get(msg, f): return [v for ff, w, v in msg if ff == f]
def one(msg, f, d=None):
    l = get(msg, f); return l[0] if l else d
usage_fields = collections.Counter(); models = collections.Counter(); enum_kv = collections.Counter()
n=chk_ok=chk_bad=dup17_ok=0; match_steps=mism=0; pairs=[]; growth_up=growth_dn=0; f4_uuid=f4_unique_total=0
t_min=t_max=None; f4_eq_step=0; dbs=0; dur_ok=dur_bad=0; per_db=[]; f1_3=collections.Counter()
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    dbs+=1; con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    gens = [(i, parse(d)) for i, d in con.execute('select idx,data from gen_metadata order by idx')]
    steps = [(i, t, parse(m) if m else [], len(p or b'')) for i, t, m, p in con.execute('select idx,step_type,metadata,step_payload from steps order by idx')]
    ustep = [(i, t, m, pl) for i, t, m, pl in steps if one(m, 9) is not None]
    step_ids = set(one(m, 12) for _, _, m, _ in steps)
    f4s = []; prev_in=None; tot=[0,0,0,0]
    for k, (gi, top) in enumerate(gens):
        n+=1; inner = parse(one(top, 1)); u = parse(one(inner, 4, b''))
        f4 = one(top, 4); f4s.append(f4); f4_uuid += bool(UUID.match(f4 or b'')); f4_eq_step += f4 in step_ids
        ud = {f: v for f, w, v in u}; usage_fields.update(ud.keys())
        if ud.get(3, 0) == ud.get(9, 0) + ud.get(10, 0): chk_ok+=1
        else: chk_bad+=1
        u17 = one(inner, 17); 
        if u17 and parse(one(parse(u17), 2, b'')) == u: dup17_ok+=1
        models[one(inner, 19, b'').decode()] += 1; f1_3[one(inner, 3)] += 1
        for kv in get(inner, 20):
            kvp = parse(kv); val = one(kvp, 2, b'')
            if val.startswith(b'MODEL_'): enum_kv[val.decode()] += 1
        ts = parse(one(parse(one(inner, 9)), 4)); t = one(ts, 1)
        t_min = t if t_min is None else min(t_min, t); t_max = t if t_max is None else max(t_max, t)
        inp = ud.get(2, 0) + ud.get(5, 0)
        if prev_in is not None: growth_up += inp >= prev_in; growth_dn += inp < prev_in
        prev_in = inp
        tot[0]+=ud.get(2,0); tot[1]+=ud.get(5,0); tot[2]+=ud.get(3,0); tot[3]+=ud.get(9,0)
        if k < len(ustep):
            su = {f: v for f, w, v in parse(one(ustep[k][2], 9))}
            if su == ud: match_steps+=1
            else: mism+=1
            pairs.append((ud.get(10, 0), ustep[k][3], ud.get(3,0)))
            # durations 1.11 + 1.12 vs step 6/7..8 window
            def dur(m):
                if m is None: return 0
                p = parse(m); return one(p,1,0) + one(p,2,0)/1e9
            d = dur(one(inner, 11)) + dur(one(inner, 12))
            m = ustep[k][2]; st = parse(one(m,1)); en = parse(one(m,8) or one(m,7))
            w = (one(en,1,0)+one(en,2,0)/1e9) - (one(st,1,0)+one(st,2,0)/1e9)
            if abs(d - w) < 0.5: dur_ok+=1
            else: dur_bad+=1
    f4_unique_total += len(set(f4s)) == len(f4s)
    per_db.append((os.path.basename(db)[:8], len(gens), len(steps), len(ustep), collections.Counter(t for _, t, _, _ in steps).most_common(3), tot))
print('dbs', dbs, 'gen records', n)
print('usage subfields present', dict(usage_fields))
print('3 == 9+10:', chk_ok, 'bad', chk_bad, '| 1.17.2 == 1.4:', dup17_ok)
print('models 1.19', dict(models)); print('enum kv', dict(enum_kv)); print('1.3', dict(f1_3))
print('gen usage == nth step-with-usage usage:', match_steps, 'mismatch', mism)
print('field4 uuid-shaped', f4_uuid, 'all-unique-per-db', f4_unique_total, 'field4 equals some step field12', f4_eq_step)
print('input(2+5) non-decreasing vs prev', growth_up, 'decreasing', growth_dn)
print('ttft+stream dur matches step window (<0.5s):', dur_ok, 'no', dur_bad)
f=lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).isoformat()
print('gen ts range', f(t_min), f(t_max))
xs=[a for a,b,c in pairs]; ys=[b for a,b,c in pairs]; zs=[c for a,b,c in pairs]
print('corr(visible out tokens f10, step_payload bytes)', round(statistics.correlation(xs, ys),3), 'corr(f3,payload)', round(statistics.correlation(zs,ys),3))
r=[b/a for a,b,c in pairs if a>20]; print('payload bytes per f10 token median', round(statistics.median(r),2))
for row in per_db: print(row)
