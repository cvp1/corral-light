"""Phase 0: Codex rollout token_count behaviour. Aggregates only."""
import json, glob, os, collections, time
H=os.path.expanduser
homes=[H("~/.config/corral-light/codex-home"), H("~/.codex")]
t0=time.time()
for home in homes:
    fs=glob.glob(home+"/sessions/**/*.jsonl", recursive=True)+glob.glob(home+"/archived_sessions/**/*.jsonl", recursive=True)
    if not fs: print(home, "no rollouts"); continue
    types=collections.Counter(); metakeys=collections.Counter(); resets=0; dup=0; ooo=0; sessions=0
    rlkeys=collections.Counter(); plan=collections.Counter(); ttl_keys=collections.Counter(); forked=0; first_nonzero=0
    sid_files=collections.Counter(); win=collections.Counter(); null_info=0; null_rl=0
    for f in fs:
        tots=[]; sessions+=1; sid=None
        for line in open(f,'rb'):
            try: d=json.loads(line)
            except: continue
            types[d.get('type')]+=1
            p=d.get('payload') or {}
            if d.get('type')=='session_meta':
                metakeys.update(p.keys()); sid=p.get('id')
                if p.get('forked_from_id') or p.get('source') not in (None,'cli','vscode','exec'): pass
                if p.get('forked_from_id'): forked+=1
            if d.get('type')=='event_msg' and p.get('type')=='token_count':
                info=p.get('info'); rl=p.get('rate_limits')
                if rl: 
                    rlkeys.update(rl.keys()); plan[rl.get('plan_type')]+=1
                    for k in ('primary','secondary'):
                        w=rl.get(k)
                        if w: win[(k,w.get('window_minutes'))]+=1
                else: null_rl+=1
                if not info: null_info+=1; continue
                ttl_keys.update((info.get('total_token_usage') or {}).keys())
                tots.append((d.get('timestamp'), (info.get('total_token_usage') or {}).get('total_tokens',0)))
        if sid: sid_files[sid]+=1
        if tots and tots[0][1]>0: first_nonzero+=1
        for (ta,a),(tb,b) in zip(tots,tots[1:]):
            if b<a: resets+=1
            if b==a: dup+=1
            if tb<ta: ooo+=1
    print(f"== {home}: files={len(fs)}")
    print(" types", types.most_common(8))
    print(" session_meta keys", sorted(metakeys))
    print(f" token_count: null info={null_info} null rate_limits={null_rl}; total decreases={resets} equal-consecutive={dup} ts-out-of-order={ooo}; sessions starting nonzero={first_nonzero}; forked={forked}")
    print(" sessions in >1 file:", sum(1 for v in sid_files.values() if v>1))
    print(" total_token_usage keys", sorted(ttl_keys)); print(" rate_limits keys", sorted(rlkeys)); print(" plan", plan.most_common()); print(" windows", win.most_common())
print("scan", round(time.time()-t0,2),"s")
