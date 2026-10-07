import sqlite3, glob, os, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse
def one(m,f,d=None):
    l=[v for ff,w,v in m if ff==f]; return l[0] if l else d
a=b=c=0; cg=0; n=0
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True); prev=None
    for (d,) in con.execute('select data from gen_metadata order by idx'):
        u={f:v for f,w,v in parse(one(parse(one(parse(d),1)),4))}; n+=1
        if u.get(5,0)>u.get(2,0): cg+=1
        if prev:
            a+= u.get(2,0)>=prev.get(2,0); b+=1
            c+= (u.get(2,0)+u.get(5,0)) >= prev.get(2,0)+prev.get(5,0)+0.5*(prev.get(3,0))*0  # same as before
        prev=u
print('field2 alone non-decreasing',a,'/',b,'| 2+5 non-decreasing',c,'/',b,'| records where cached(5) > field2',cg,'/',n)
