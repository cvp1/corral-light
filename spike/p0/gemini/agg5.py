import sqlite3, glob, os, collections, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse, varint
def one(m,f,d=None):
    l=[v for ff,w,v in m if ff==f]; return l[0] if l else d
rel=collections.Counter(); f71=set(); lens=collections.Counter()
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    steps={i:(t,parse(m) if m else []) for i,t,m in con.execute('select idx,step_type,metadata from steps')}
    for gi,d in con.execute('select idx,data from gen_metadata order by idx'):
        top=parse(d); b=one(top,2); i=0; L=[]
        while i<len(b): v,i=varint(b,i); L.append(v)
        lens[len(L)]+=1
        inner=parse(one(top,1)); u=one(inner,4)
        hit=[s for s in L if s in steps and one(steps[s][1],9)==u]
        rel['usage step in field2 list' if hit else 'not in list']+=1
        if hit: rel[f'pos={L.index(hit[0])} of {len(L)}' if len(L)<4 else 'pos=last' if L[-1]==hit[0] else 'pos=other']+=1
        rel['types:'+','.join(str(steps[s][0]) for s in L if s in steps)[:30]]+=0
        f71.add(one(parse(one(inner,7)),1))
print(dict(rel.most_common(12))); print('list lengths',dict(lens)); print('distinct 1.7.1 values',len(f71))
