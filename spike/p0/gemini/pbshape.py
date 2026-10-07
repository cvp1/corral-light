import sqlite3, sys, re, struct
SAFE = re.compile(r'^(gemini|claude|gpt|models/|MODEL_)[\w.\-/]{0,60}$')
def varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1; r |= (c & 0x7f) << s; s += 7
        if not c & 0x80: return r, i
def parse(b):
    i, out = 0, []
    while i < len(b):
        k, i = varint(b, i); f, w = k >> 3, k & 7
        if f == 0: raise ValueError
        if w == 0: v, i = varint(b, i)
        elif w == 1: v = struct.unpack('<d', b[i:i+8])[0]; i += 8
        elif w == 5: v = struct.unpack('<f', b[i:i+4])[0]; i += 4
        elif w == 2:
            n, i = varint(b, i); v = b[i:i+n]; i += n
            if i > len(b): raise ValueError
        else: raise ValueError
        out.append((f, w, v))
    return out
def show(b, path='', depth=0, lines=None):
    for f, w, v in parse(b):
        p = f'{path}.{f}' if path else str(f)
        if w == 2:
            sub = None
            if len(v) > 0:
                try: sub = parse(v)
                except Exception: sub = None
            try: s = v.decode('utf-8'); txt = s.isprintable()
            except Exception: txt = False
            if txt and SAFE.match(s): lines.append(f'{p} str "{s}"')
            elif sub is not None and not (txt and len(v) < 40 and not any(c < 32 for c in v[:1])) and depth < 6:
                lines.append(f'{p} msg len={len(v)}'); show(v, p, depth+1, lines)
            else:
                lines.append(f'{p} {"str" if txt else "bytes"} len={len(v)}')
        else: lines.append(f'{p} w{w} {v}')
    return lines
if __name__ == '__main__':
    db, table = sys.argv[1], sys.argv[2]
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    col = 'data' if table != 'steps' else 'metadata'
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    for idx, d in con.execute(f'select idx, {col} from {table} order by idx limit ?', (lim,)) if table!='trajectory_metadata_blob' else con.execute('select id,data from trajectory_metadata_blob'):
        print(f'--- {table} idx={idx} bytes={len(d) if d else 0}')
        if d: print('\n'.join(show(d, lines=[])))
