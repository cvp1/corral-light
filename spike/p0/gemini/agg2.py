import sqlite3, glob, os, collections, statistics, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse
def get(m,f): return [v for ff,w,v in m if ff==f]
def one(m,f,d=None):
    l=get(m,f); return l[0] if l else d
def ts(b):
    p=parse(b); return one(p,1,0)+one(p,2,0)/1e9
matched=unmatched=0; extra_types=collections.Counter(); dur=[]; pairs=[]; f4_turns=[]; kv36=collections.Counter(); s12=[]
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    tid,cid=con.execute('select trajectory_id,cascade_id from trajectory_meta').fetchone()
    steps=[(i,t,parse(m) if m else [],len(p or b'')) for i,t,m,p in con.execute('select idx,step_type,metadata,step_payload from steps order by idx')]
    s12.append(len(set(one(m,12) for _,_,m,_ in steps)))
    byu={}
    for i,t,m,pl in steps:
        u=one(m,9)
        if u is not None: byu.setdefault(u,[]).append((i,t,m,pl))
    used=set(); f4=[]
    for gi,d in con.execute('select idx,data from gen_metadata order by idx'):
        top=parse(d); inner=parse(one(top,1)); u=one(inner,4,b''); f4.append(one(top,4))
        for kv in get(inner,20):
            p=parse(kv); v=one(p,2,b'')
            if len(v)==36: kv36[('==cascade' if v.decode()==cid else '==traj' if v.decode()==tid else '==f4' if v==one(top,4) else 'other')]+=1
        c=[s for s in byu.get(u,[]) if s[0] not in used]
        if not c: unmatched+=1; continue
        i,t,m,pl=c[0]; used.add(i); matched+=1
        ud={f:v for f,w,v in parse(u)}
        d=sum(ts(one(inner,k)) for k in (11,12) if one(inner,k))
        if one(m,6) and one(m,8): dur.append(abs(d-(ts(one(m,8))-ts(one(m,6)))))
        pairs.append((ud.get(10,0),ud.get(3,0),pl))
    for us in byu.values():
        for i,t,m,pl in us:
            if i not in used: extra_types[t]+=1
    f4_turns.append((len(f4),len(set(f4))))
print('gen matched to a step by identical usage msg:',matched,'unmatched',unmatched,'| usage-bearing steps without gen, by step_type',dict(extra_types))
print('|ttft+stream - step(6..8) window| median s',round(statistics.median(dur),3),'p90',round(sorted(dur)[int(.9*len(dur))],3),'n',len(dur))
x=[a for a,b,c in pairs];z=[b for a,b,c in pairs];y=[c for a,b,c in pairs]
print('corr f10 vs payload',round(statistics.correlation(x,y),3),'corr f3 vs payload',round(statistics.correlation(z,y),3))
print('gen records vs distinct field4 per db',f4_turns); print('distinct step.12 per db',s12); print('36-char kv values',dict(kv36))
