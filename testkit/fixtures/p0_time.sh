#!/bin/bash
# usage: p0_time.sh <repo> <label>   — makes a worktree, dirties it, times summary and snapshot
set -u; REPO=$1; L=$2; W=$HOME/wt-spike/root/time-$L
git -C $REPO worktree add -q -b corral/time-$L -- $W HEAD 2>/dev/null || true
cd $W; base=$(git rev-parse HEAD)
# dirty: modify 20 tracked files, add 5 untracked, one 10 MiB untracked
git ls-files | head -20 | while read f; do echo "# edit" >> "$f"; done
for i in 1 2 3 4 5; do echo new$i > new$i.txt; done; head -c 10485760 /dev/urandom > big-untracked.bin
objs() { find "$(git rev-parse --git-common-dir)/objects" -type f | wc -l; }
idx=$(git rev-parse --git-path index); before_idx=$(sha256sum "$idx"|cut -c1-16); o0=$(objs)
t() { local s=$(date +%s%N); "$@" >/dev/null; echo $(( ($(date +%s%N)-s)/1000000 )); }
for run in 1 2 3; do
 s1=$(t env GIT_OPTIONAL_LOCKS=0 git diff --numstat -z --no-renames --no-textconv --no-ext-diff $base)
 s2=$(t git ls-files --others --exclude-standard -z)
 echo "$L summary run$run: diff ${s1}ms + ls-files ${s2}ms"
done
o1=$(objs); after_idx=$(sha256sum "$idx"|cut -c1-16); echo "$L summary wrote objects: $((o1-o0)); real index unchanged: $([ $before_idx = $after_idx ] && echo yes || echo NO)"
tmp=$W.snapidx; for run in 1 2; do cp "$idx" $tmp; s=$(date +%s%N); GIT_INDEX_FILE=$tmp git add -A -- . ':!big-untracked.bin'; tree=$(GIT_INDEX_FILE=$tmp git write-tree); echo "$L snapshot run$run: $(( ($(date +%s%N)-s)/1000000 ))ms tree=${tree:0:12}"; done; rm -f $tmp
echo "$L files tracked: $(git ls-files | wc -l)"
