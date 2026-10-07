import sqlite3, glob, os, collections, statistics, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse, show
def one(m,f,d=None):
    l=[v for ff,w,v in m if ff==f]; return l[0] if l else d
def ts(b):
    p=parse(b); return one(p,1,0)+one(p,2,0)/1e9
def leaves(b, path=''):
    out=[]
    for f,w,v in parse(b):
        p=f'{path}.{f}' if path else str(f)
        if w==2:
            try: sub=parse(v) if v else None
            except Exception: sub=None
            if sub and not (len(v)==36 or len(v)==38): out+=leaves(v,p)
            else: out.append((p,v))
    return out
uniq=collections.defaultdict(lambda:[0,0]); dur=[]; t23=collections.Counter(); t23u=[]; f4rel=collections.Counter()
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    tid,cid=con.execute('select trajectory_id,cascade_id from trajectory_meta').fetchone()
    vals=collections.defaultdict(list); n=0
    for gi,d in con.execute('select idx,data from gen_metadata'):
        n+=1; top=parse(d)
        f4=one(top,4).decode(); f4rel['f4==cascade' if f4==cid else 'f4==traj' if f4==tid else 'f4 other']+=1
        for p,v in leaves(d):
            if p.startswith('1.20') or p.startswith('1.15.9'): continue
            vals[p].append(v)
    if n>5:
        for p,l in vals.items(): uniq[p][0]+=len(set(l)); uniq[p][1]+=len(l)
    steps=[(t,parse(m) if m else []) for i,t,m in con.execute('select idx,step_type,metadata from steps')]
    for t,m in steps:
        if t==23 and one(m,9):
            ud={f:v for f,w,v in parse(one(m,9))}; t23u.append(ud)
            mm=one(m,24); t23[ (parse(mm) and one(parse(mm),8,b'').decode()) if mm else None]+=1
    for gi,d in con.execute('select idx,data from gen_metadata'):
        inner=parse(one(parse(d),1)); g0=ts(one(parse(one(inner,9)),4))
        dd=sum(ts(one(inner,k)) for k in (11,12) if one(inner,k))
        u=one(inner,4)
        for t,m in steps:
            if one(m,9)==u and one(m,8):
                dur.append(abs(ts(one(m,8))-g0-dd)); break
print('string/bytes leaf paths in gen: distinct/total (DBs with >5 records):')
for p,(a,b) in sorted(uniq.items()): print(' ',p,a,'/',b)
print('f4 relation',dict(f4rel))
print('|(step.8 - gen 1.9.4) - (1.11+1.12)| median',round(statistics.median(dur),3),'p90',round(sorted(dur)[int(.9*len(dur))],3))
print('type-23 usage steps model',dict(t23)); print('type-23 sample usage',t23u[:3])
