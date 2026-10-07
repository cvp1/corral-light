import sqlite3, glob, os, collections, sys, datetime
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse
def one(m,f,d=None):
    l=[v for ff,w,v in m if ff==f]; return l[0] if l else d
combo=collections.Counter(); t23ts=[]; t23db=collections.Counter()
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    steps=[(t,parse(m) if m else []) for i,t,m in con.execute('select idx,step_type,metadata from steps')]
    byu={one(m,9):m for t,m in steps if one(m,9)}
    for (d,) in con.execute('select data from gen_metadata'):
        inner=parse(one(parse(d),1)); m=byu.get(one(inner,4))
        sm=one(m,24) if m else None
        combo[(one(inner,19).decode(), one(parse(sm),8,b'').decode() if sm else None)]+=1
    for t,m in steps:
        if t==23 and one(m,9): t23db[os.path.basename(db)[:8]]+=1
print(dict(combo)); print('type23 usage steps per db',dict(t23db))
