import sqlite3, glob, os, collections, sys
sys.path.insert(0, os.path.dirname(__file__)); from pbshape import parse, varint
def one(m,f,d=None):
    l=[v for ff,w,v in m if ff==f]; return l[0] if l else d
top_f=collections.Counter(); inner_f=collections.Counter(); f2len=collections.Counter(); f71=collections.Counter(); seq=[]; sizes=[]
for db in sorted(glob.glob(os.path.expanduser('~/.gemini/antigravity-acp/conversations/*.db'))):
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    for gi,d,sz in con.execute('select idx,data,size from gen_metadata order by idx'):
        top=parse(d); top_f.update(f for f,w,v in top); sizes.append((len(d),sz))
        inner=parse(one(top,1)); inner_f.update(set(f for f,w,v in inner))
        b=one(top,2); f2len[len(b)]+=1
        try:
            v,_=varint(b,0); seq.append((gi,v))
        except Exception: pass
        f71[(len(one(parse(one(inner,7)),1,b'')), one(parse(one(inner,7)),1,b'').isalpha())]+=1
print('top fields',dict(top_f)); print('inner fields (records containing)',dict(sorted(inner_f.items())))
print('field2 lengths',dict(f2len)); print('field2 as varint vs idx sample',seq[:8], 'equal idx+?', collections.Counter(v-g for g,v in seq).most_common(3))
print('1.7.1 (len,isalpha)',dict(f71)); print('size col == len(data)', sum(a==b for a,b in sizes), '/', len(sizes))
