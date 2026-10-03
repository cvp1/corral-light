/* Own branches in the browser (docs/worktree-review-plan.md §2.5, T-UI-*).
 *
 * Driven against the real functions in static/app.js, extracted by name, with
 * a mini-DOM where a function builds nodes. Covers the New dialog's row, the
 * header pill, the unified-diff parser, the review dialog's buttons and
 * request bodies, the 409 refresh, the rail card and the `r` key.
 *
 * Run: node selftest_review.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');

function fn(name) {
  const start = src.search(new RegExp(`(^|\\n)(async )?function ${name}\\(`));
  if (start < 0) throw new Error(`no function ${name} in app.js`);
  const from = src.indexOf('function ' + name, start);
  const head = src.slice(start, from).includes('async') ? 'async ' : '';
  let depth = 0;
  for (let j = src.indexOf('{', src.indexOf(')', from)); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return head + src.slice(from, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}
/* Build one function with its helpers and injected globals in scope. */
function load(name, helpers = [], globals = {}) {
  const names = Object.keys(globals);
  const body = [...helpers, name].map(fn).join('\n') + `\nreturn ${name};`;
  return new Function(...names, body)(...names.map(k => globals[k]));
}

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };
const eq = (a, b, why) => check(JSON.stringify(a) === JSON.stringify(b),
                                `${why}: got ${JSON.stringify(a)}, wanted ${JSON.stringify(b)}`);

/* A mini-DOM: enough for el() builders, and countable. */
function mk(tag, cls, text) {
  const n = { tag, className: cls || '', textContent: text ?? '', title: '', children: [],
              dataset: {}, style: {}, hidden: false, href: '',
              append(...xs) { this.children.push(...xs); },
              appendChild(x) { this.children.push(x); return x; },
              setAttribute(k, v) { this[k] = v; } };
  return n;
}
const count = n => 1 + n.children.reduce((a, c) => a + count(c), 0);
const texts = n => [n.textContent, ...n.children.flatMap(texts)].join('\n');

/* ── T-UI-1..3: the New dialog's own-branch row ─────────────────────────── */
const wtRowModel = load('wtRowModel', ['shortSha', 'homeTilde']);
const repo = { inside: true, top: '/home/USER/aios', branch: 'main', head: '3f2a1c9d00aa',
               detached: false, refusals: [], warnings: [] };

eq(wtRowModel({ inside: false, refusals: [] }, null, false).show, false,
   'T-UI-1 a folder outside any repo hides the row');
eq(wtRowModel(null, null, false).show, false, 'T-UI-1 no probe hides the row');
eq(wtRowModel(repo, 'own branches are not enabled for the gemini lane yet', false).show, false,
   'T-UI-1 a lane that cannot take an own branch hides the row');
let m = wtRowModel(repo, null, false);
check(m.show && m.checkbox && m.checked === false, 'T-UI-1 a repo on an enabled lane offers the checkbox');
eq(m.cut, 'cut from main @ 3f2a1c9', 'the row says what the branch is cut from');
m = wtRowModel({ ...repo, refusals: ['this folder is on tmpfs', 'submodules are not supported'] },
               null, true);
check(m.show && !m.checkbox, 'T-UI-1 a refused repo shows no checkbox');
check(/tmpfs/.test(m.refusal) && /submodules/.test(m.refusal),
      'T-UI-1 every refusal reason shows in place of the checkbox');
m = wtRowModel({ ...repo, dirty: true,
                 warnings: ['your uncommitted changes in /home/USER/aios stay there'] }, null, false);
check(m.checkbox && m.warnings.length === 1 && m.warnings[0].includes('~/aios'),
      'a warning shows beside the checkbox, with the home folder as ~');
m = wtRowModel({ ...repo, detached: true, branch: null }, null, false);
check(/detached/.test(m.cut), 'a detached HEAD says so');

/* T-UI-2: remembered per repo top. */
const store = new Map();
const localStorage = { getItem: k => store.has(k) ? store.get(k) : null,
                       setItem: (k, v) => store.set(k, String(v)) };
check(/const WT_KEY = 'corral\.ownBranch';/.test(src), 'the remembered checkboxes live under one key');
const WT_KEY = 'corral.ownBranch';
const wtRemember = load('wtRemember', ['wtStore'], { localStorage, WT_KEY });
const wtRemembered = load('wtRemembered', ['wtStore'], { localStorage, WT_KEY });
wtRemember('/r/one', true);
check(wtRemembered('/r/one') && !wtRemembered('/r/two'), 'T-UI-2 the checkbox is remembered per repo top');
wtRemember('/r/one', false);
check(!wtRemembered('/r/one'), 'T-UI-2 unchecking is remembered too');
store.set('corral.ownBranch', '{not json');
check(wtRemembered('/r/one') === false, 'T-UI-2 a corrupt store reads as unchecked');
m = wtRowModel(repo, null, true);
check(m.checked === true, 'T-UI-2 a remembered repo starts checked');

/* T-UI-3: `worktree` is sent only when checked and visible, for the folder probed. */
const wtSubmit = load('wtSubmit');
const vis = { ...wtRowModel(repo, null, false), cwd: '~/aios', agent: 'claude' };
eq(wtSubmit(vis, true, '~/aios', 'claude'), { worktree: true }, 'T-UI-3 checked and visible sends it');
eq(wtSubmit(vis, false, '~/aios', 'claude'), {}, 'T-UI-3 unchecked sends nothing');
eq(wtSubmit({ show: false, cwd: '~/aios', agent: 'claude' }, true, '~/aios', 'claude'), {},
   'T-UI-3 a hidden row sends nothing, whatever the box says');
const refusedRow = { ...wtRowModel({ ...repo, refusals: ['x'] }, null, false), cwd: '~/aios', agent: 'claude' };
eq(wtSubmit(refusedRow, true, '~/aios', 'claude'), {}, 'T-UI-3 a refused row sends nothing');
check(wtSubmit(vis, true, '~/other', 'claude').error, 'T-UI-3 a folder changed after the probe is refused, not guessed');
check(wtSubmit(vis, true, '~/aios', 'codex').error, 'T-UI-3 a lane changed after the probe is refused');

/* ── T-UI-4..6: the header ──────────────────────────────────────────────── */
const wtPillModel = load('wtPillModel', ['shortSha']);
const wt = { branch: 'corral/fix-login', path: '/home/USER/.local/state/corral-light/worktrees/aios-1a2b3c',
             repo: '/home/USER/aios', subdir: '', base: 'main', baseSha: '3f2a1c9d00aa', phase: 'active',
             blocked: null, summary: { files: 4, added: 120, deleted: 8, digest: 'aa' } };
eq(wtPillModel(null), null, 'an ordinary pane has no pill');
eq(wtPillModel(wt).text, '⎇ fix-login · 4 files +120 −8', 'T-UI-4 pill text');
eq(wtPillModel({ ...wt, summary: { files: 1, added: 1, deleted: 0 } }).text, '⎇ fix-login · 1 file +1 −0',
   'T-UI-4 one file is singular');
eq(wtPillModel({ ...wt, summary: { files: 0, added: 0, deleted: 0 } }).text, '⎇ fix-login · no changes',
   'T-UI-4 an untouched branch says so');
eq(wtPillModel({ ...wt, summary: null }).text, '⎇ fix-login', 'T-UI-4 no summary yet shows the branch alone');
check(/discarded/.test(wtPillModel({ ...wt, phase: 'trashed' }).text), 'T-UI-4 a discarded branch says so');
const gone = wtPillModel({ ...wt, phase: 'trashed', blocked: 'this branch was discarded; restore it' });
check(/\bgone\b/.test(gone.cls) && !/\bwarn\b/.test(gone.cls), 'discarded is an end state, not a problem');
check(/\bwarn\b/.test(wtPillModel({ ...wt, blocked: 'worktree is missing' }).cls),
      'T-UI-4 a blocked branch is styled as a problem');
check(wtPillModel(wt).title.includes(wt.path), 'the pill tooltip has the worktree path');

const paneMeta = load('paneMeta', ['homeTilde']);
const wp = { label: 'Claude Code', cwd: wt.path, worktree: wt };
eq(paneMeta(wp).text, 'Claude Code · ~/aios', 'T-UI-6 .meta shows the repo path');
eq(paneMeta({ ...wp, worktree: { ...wt, subdir: 'pkg' }, cwd: wt.path + '/pkg' }).text,
   'Claude Code · ~/aios/pkg', 'T-UI-6 with the subdir the agent runs in');
check(paneMeta(wp).title.includes(wt.path), 'T-UI-6 the worktree path is on the tooltip');
eq(paneMeta({ label: 'Codex', cwd: '/home/USER/x' }).text, 'Codex · ~/x', 'an ordinary pane is unchanged');
check(/paneMeta\(p\)/.test(fn('paneHead')) && /wtPillModel\(p\.worktree\)/.test(fn('paneHead')),
      'paneHead renders the meta and the pill from these');

/* T-UI-5: headSignature changes exactly when the pill's fields change. */
const headSignature = load('headSignature', ['wtPillModel', 'shortSha', 'paneMeta', 'homeTilde'],
  { displayState: () => 'your-turn', fmtAge: () => '', S: { detail: new Set() }, FIND: { pane: null } });
const base = { id: 'p', title: 't', label: 'Claude Code', state: 'ready', cwd: wt.path, worktree: wt };
const sig = p => headSignature(p);
const with_ = s => ({ ...base, worktree: { ...wt, summary: { ...wt.summary, ...s } } });
check(sig(base) === sig(with_({ digest: 'bb' })), 'T-UI-5 a digest-only change does not rebuild the header');
check(sig(with_({ untracked: [{ path: 'x' }] })) === sig(base), 'T-UI-5 fields the pill does not show are ignored');
for (const [k, v] of [['files', 5], ['added', 121], ['deleted', 9]])
  check(sig(with_({ [k]: v })) !== sig(base), `T-UI-5 ${k} changes the header`);
check(sig({ ...base, worktree: { ...wt, phase: 'trashed' } }) !== sig(base), 'T-UI-5 phase changes the header');
check(sig({ ...base, worktree: { ...wt, blocked: 'x' } }) !== sig(base), 'T-UI-5 blocked changes the header');
check(sig({ ...base, worktree: { ...wt, repo: '/home/USER/b' } }) !== sig(base), 'T-UI-5 the repo path changes the header');

/* ── T-UI-7: parseUnified ───────────────────────────────────────────────── */
const parseUnified = load('parseUnified');
let d = parseUnified('diff --git a/n.txt b/n.txt\nnew file mode 100644\nindex 0000000..e69de29\n'
  + '--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n');
eq(d.hunks.length, 1, 'add: one hunk');
eq(d.hunks[0].lines.map(l => [l.t, l.old, l.new, l.text]),
   [['add', null, 1, 'one'], ['add', null, 2, 'two']], 'add: new-side line numbers from 1');
check(d.head.includes('new file mode 100644'), 'add: the header keeps the mode line');

d = parseUnified('diff --git a/g.txt b/g.txt\ndeleted file mode 100644\n--- a/g.txt\n+++ /dev/null\n'
  + '@@ -1,2 +0,0 @@\n-x\n-y\n');
eq(d.hunks[0].lines.map(l => [l.t, l.old, l.new]), [['del', 1, null], ['del', 2, null]], 'delete');

d = parseUnified('diff --git a/a.txt b/b.txt\nsimilarity index 90%\nrename from a.txt\nrename to b.txt\n'
  + '--- a/a.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-a\n+b\n');
eq(d.rename, { from: 'a.txt', to: 'b.txt' }, 'rename: from and to');
eq(d.hunks[0].lines.map(l => [l.t, l.old, l.new]), [['del', 1, null], ['add', null, 1]],
   'rename: a hunk header without counts');

d = parseUnified('diff --git a/s.sh b/s.sh\nold mode 100644\nnew mode 100755\n');
eq(d.mode, { old: '100644', new: '100755' }, 'mode change');
eq(d.hunks.length, 0, 'a mode-only change has no hunks');

d = parseUnified('diff --git a/b.bin b/b.bin\nindex 1..2 100644\nBinary files a/b.bin and b/b.bin differ\n');
check(d.binary && !d.hunks.length, 'binary');

d = parseUnified('diff --git a/e.txt b/e.txt\n--- a/e.txt\n+++ b/e.txt\n@@ -1 +1 @@\n-old\n'
  + '\\ No newline at end of file\n+new\n\\ No newline at end of file\n');
eq(d.hunks[0].lines.map(l => l.t), ['del', 'nonl', 'add', 'nonl'], 'no newline at end of file');
check(d.hunks[0].lines[1].old === null && d.hunks[0].lines[1].new === null,
      'the no-newline marker takes no line number');

d = parseUnified('diff --git a/w.txt b/w.txt\n--- a/w.txt\n+++ b/w.txt\n@@ -1,2 +1,2 @@\n a\r\n-b\r\n+c\r\n');
eq(d.hunks[0].lines.map(l => [l.t, l.text, l.cr]),
   [['ctx', 'a', true], ['del', 'b', true], ['add', 'c', true]], 'CRLF: the CR is flagged, not kept in the text');

const three = ['diff --git a/m.py b/m.py', '--- a/m.py', '+++ b/m.py',
  '@@ -1,3 +1,3 @@ def top():', ' a', '-b', '+B', ' c',
  '@@ -10,2 +10,3 @@ class K:', ' j', '+k', ' l',
  '@@ -20 +21,0 @@', '-z', ''].join('\n');
d = parseUnified(three);
eq(d.hunks.map(h => [h.oldStart, h.newStart, h.context]), [[1, 1, 'def top():'], [10, 10, 'class K:'], [20, 21, '']],
   '3 hunks: starts and section context');
eq(d.hunks[0].lines.map(l => [l.old, l.new]), [[1, 1], [2, null], [null, 2], [3, 3]], 'hunk 1 line numbers');
eq(d.hunks[1].lines.map(l => [l.old, l.new]), [[10, 10], [null, 11], [11, 12]], 'hunk 2 line numbers');
eq(d.hunks[2].lines.map(l => [l.t, l.old]), [['del', 20]], 'hunk 3 line numbers');
d = parseUnified('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n--- a dashed line\n++++ plus\n');
eq(d.hunks[0].lines.map(l => [l.t, l.text]), [['del', '-- a dashed line'], ['add', '+++ plus']],
   'lines that look like headers inside a hunk are content');
eq(parseUnified(null), { head: [], hunks: [], binary: false, rename: null, mode: null },
   'no patch parses to nothing');

/* ── T-UI-8: no innerHTML anywhere in the own-branch code ─────────────────── */
const a0 = src.indexOf('/* ── own branches'), a1 = src.indexOf('/* ── end own branches');
check(a0 > 0 && a1 > a0, 'app.js marks the own-branch block');
check(!/innerHTML|insertAdjacentHTML|outerHTML/.test(src.slice(a0, a1)),
      'T-UI-8 the own-branch code never parses HTML');
for (const name of ['paneHead', 'openReview', 'paintReview', 'diffNodes', 'fileRow'])
  check(!/innerHTML/.test(fn(name)), `T-UI-8 ${name} uses no innerHTML`);

/* ── T-UI-9: what Commit and Publish post ──────────────────────────────── */
const snap = { tree: 't'.repeat(40), head: 'h'.repeat(40), head_tree: 'b'.repeat(40),
               base_sha: 'h'.repeat(40), index_id: 'i'.repeat(64),
               remotes: [{ name: 'origin', pushUrls: ['git@github.com:u/aios.git'], githubRepo: 'u/aios' }] };
eq(load('commitBody')('p1', snap, 'feat: x'),
   { pane: 'p1', tree: snap.tree, head: snap.head, index_id: snap.index_id, message: 'feat: x' },
   'T-UI-9 commit posts the tree and head it rendered');
const done = { ...snap, head: 'c'.repeat(40), head_tree: snap.tree };
eq(load('publishBody')('p1', done, done.remotes[0], null),
   { pane: 'p1', oid: done.head, tree: done.tree, remote: 'origin',
     push_url: 'git@github.com:u/aios.git', pr: null },
   'T-UI-9 publish posts the OID and the remote URL it showed');
eq(load('publishBody')('p1', done, done.remotes[0], { repo: 'u/aios', title: 'T', body: '' }).pr,
   { repo: 'u/aios', title: 'T', body: '' }, 'T-UI-9 the PR rides along when asked for');
eq(load('discardBody')('p1', snap), { pane: 'p1', tree: snap.tree }, 'discard posts the tree it showed');
eq(load('mergeCommand', ['shq'])({ repo: "/home/USER/it's", branch: 'corral/fix-login' }),
   "git -C '/home/USER/it'\\''s' merge --no-ff 'corral/fix-login'", 'the merge command is shell-quoted');
const wire = fn('wireReview');
check(/commitBody\(R\.pane, R\.snap,/.test(wire) && /publishBody\(R\.pane, R\.snap,/.test(wire)
      && /discardBody\(R\.pane, R\.snap\)/.test(wire),
      'T-UI-9 the buttons post from the snapshot on screen (R.snap)');

/* Buttons: enabled only when true; disabled ones say why. */
const reviewActions = load('reviewActions');
let ra = reviewActions(snap, { message: '', remote: 0, busy: false });
check(!ra.commit.ok && /message/.test(ra.commit.why), 'Commit needs a message, and says so');
ra = reviewActions(snap, { message: 'm', remote: 0, busy: false });
check(ra.commit.ok, 'Commit is enabled for uncommitted changes with a message');
check(/commit hooks/.test(ra.commit.note) && /do not run/.test(ra.commit.note) && /pre-push/.test(ra.commit.note),
      'an enabled Commit says, on hover, that commit hooks do not run (plan §6)');
check(/ra\[k\]\.ok \? ra\[k\]\.note \|\| '' : ra\[k\]\.why/.test(fn('paintReview')),
      'the tooltip shows the note when enabled and the reason when not');
check(!ra.publish.ok && /commit/i.test(ra.publish.why), 'Publish waits for a commit, and says so');
ra = reviewActions(done, { message: '', remote: 0, busy: false });
check(!ra.commit.ok && /nothing to commit/.test(ra.commit.why), 'nothing to commit says so');
check(ra.publish.ok && ra.copy.ok, 'a committed branch can be pushed and merged');
ra = reviewActions({ ...snap, head_tree: snap.tree }, { message: 'm', remote: 0, busy: false });
check(!ra.publish.ok && /no commits/.test(ra.publish.why), 'an empty branch has nothing to push');
ra = reviewActions({ ...done, remotes: [] }, { message: '', remote: 0, busy: false });
check(!ra.publish.ok && /no remote/.test(ra.publish.why), 'no remote says so');
ra = reviewActions({ ...done, remotes: [{ name: 'o', pushUrls: ['a', 'b'] }] }, { remote: 0 });
check(!ra.publish.ok && /exactly one/.test(ra.publish.why), 'two push URLs refuse');
ra = reviewActions({ ...done, too_big: [{ path: 'huge.txt', size: 700000 }] }, { message: '', remote: 0 });
check(!ra.publish.ok && /huge\.txt/.test(ra.publish.why) && /\.gitignore/.test(ra.publish.why),
      'an untracked file too large to commit blocks Push, by name, with the way out');
check(ra.copy.ok, 'the merge command still works: the commit is there');
ra = reviewActions(done, { message: 'm', remote: 0, busy: true });
check(!ra.commit.ok && !ra.publish.ok && !ra.discard.ok, 'nothing runs twice while an action is in flight');
ra = reviewActions(null, { message: 'm' });
check(!ra.commit.ok && !ra.discard.ok && ra.refresh.ok, 'with no snapshot only Refresh works');

/* ── T-UI-10: a 409 refreshes the snapshot and shows the reason ──────────── */
const reviewRun = load('reviewRun');
{
  const R = { busy: false, reason: '' };
  let reloads = 0, painted = 0;
  const conflict = Object.assign(new Error('files changed since you opened review; refresh it'),
    { status: 409, body: { reason: 'changed', error: 'files changed since you opened review; refresh it' } });
  await reviewRun(R, { call: async () => { throw conflict; },
                       reload: async () => { reloads++; R.reason = ''; },
                       paint: () => painted++,
                       after: async () => { throw new Error('ran on success'); } });
  check(reloads === 1, 'T-UI-10 a 409 reloads the snapshot');
  check(/files changed/.test(R.reason), 'T-UI-10 the reason survives the reload and is shown');
  check(R.busy === false && painted >= 2, 'the dialog repaints as busy, then idle');
  let ok = null;
  R.reason = 'old';
  await reviewRun(R, { call: async () => ({ ok: true, commit: 'c' }),
                       reload: async () => { reloads++; }, paint: () => {},
                       after: async r => { ok = r; } });
  check(ok && ok.commit === 'c' && R.reason === '', 'success clears the reason and runs the follow-up');
  await reviewRun(R, { call: async () => { throw Object.assign(new Error('boom'), { status: 500 }); },
                       reload: async () => { reloads++; }, paint: () => {}, after: async () => {} });
  check(reloads === 1 && R.reason === 'boom', 'a non-409 error is shown without a reload');
}

/* ── T-UI-11, T-UI-14: banners and the size cap ───────────────────────── */
const reviewBanners = load('reviewBanners');
let bn = reviewBanners({ ignored: { count: 12, sample: ['node_modules/'] }, too_big: [],
                         diff: { files: [], truncated: false } });
check(bn.some(b => /12 ignored files are not in this review/.test(b) && /trash/.test(b)),
      'T-UI-11 the ignored-files banner comes from the snapshot');
bn = reviewBanners({ ignored: { count: 1, sample: [] }, too_big: [{ path: 'big.bin', size: 600000 }],
                     diff: { truncated: true, files: [{ binary: true }, { binary: true }, { binary: false }] } });
check(bn.some(b => /1 ignored file is not/.test(b) && /keeps it in/.test(b)), 'one ignored file is singular');
check(bn.some(b => /big\.bin/.test(b)), 'files left out for size are named');
check(bn.some(b => /2 binary files/.test(b)), 'binary files get a banner');
check(bn.some(b => /size limit/.test(b)), 'a truncated diff says so');
eq(reviewBanners({ ignored: { count: 0 }, too_big: [], diff: { files: [] } }), [], 'nothing to say, no banners');

const diffNodes = load('diffNodes', ['parseUnified'], { el: mk });
const big = { path: 'huge.txt', status: 'M', add: 9000, del: 0, binary: false, too_big: true, patch: null };
let node = diffNodes(big);
check(count(node) < 8, `T-UI-14 an over-cap file renders a placeholder (${count(node)} nodes)`);
check(/too large|over 512/.test(texts(node)), 'T-UI-14 the placeholder says why');
node = diffNodes({ path: 'b.bin', status: 'A', binary: true, too_big: false, patch: null });
check(count(node) < 8 && /binary/.test(texts(node)), 'a binary renders a placeholder');
node = diffNodes({ path: 'x', status: 'M', add: 1, del: 0, binary: false, too_big: false, patch: null });
check(count(node) < 8 && /size limit/.test(texts(node)), 'a patch cut by the response cap says so');
const lines = Array.from({ length: 300 }, (_, i) => '+line ' + i).join('\n');
node = diffNodes({ path: 'n.txt', status: 'A', add: 300, del: 0, binary: false, too_big: false,
                   patch: `diff --git a/n.txt b/n.txt\n--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1,300 @@\n${lines}\n` });
check(texts(node).includes('line 299') && texts(node).includes('n.txt'), 'a normal file renders its lines');
const evil = '<img src=x onerror=alert(1)>';
node = diffNodes({ path: evil, status: 'M', add: 1, del: 0, binary: false, too_big: false,
                   patch: `diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+${evil}\n` });
check(texts(node).includes(evil), 'markup in a path or a line is text');

/* ── T-UI-12: the rail card ─────────────────────────────────────────────── */
const wtRailCards = load('wtRailCards', [], { displayState: p => p._disp });
const rp = (id, disp, summary, phase = 'active') =>
  ({ id, _disp: disp, pending: [], worktree: { ...wt, phase, summary } });
const s4 = { files: 4, added: 1, deleted: 0, digest: 'd1' };
eq(wtRailCards([rp('a', 'your-turn', s4)], {}).map(p => p.id), ['a'], 'T-UI-12 your turn with new changes: a card');
eq(wtRailCards([rp('a', 'your-turn', s4)], { a: 'd1' }), [], 'T-UI-12 already reviewed: no card');
eq(wtRailCards([rp('a', 'your-turn', { ...s4, digest: 'd2' })], { a: 'd1' }).length, 1,
   'T-UI-12 changed since last review: a card again');
eq(wtRailCards([rp('a', 'working', s4), rp('b', 'needs-you', s4), rp('c', 'idle', s4)], {}), [],
   'T-UI-12 only a pane whose turn it is');
eq(wtRailCards([rp('a', 'your-turn', { ...s4, files: 0 }), rp('b', 'your-turn', null),
                rp('c', 'your-turn', s4, 'trashed'), { id: 'd', _disp: 'your-turn', pending: [] }], {}), [],
   'T-UI-12 no card without changes, a summary, an active branch or a worktree');
check(/railFold\(items, panes\.reduce\(\(a, p\) => a \+ p\.pending\.length, 0\), quiet\)/.test(fn('render')),
      'T-UI-12 blocked still counts only pending permissions, never review cards');
/* On a phone the rail is an overlay: review cards count, but do not pop it open. */
const railOpens = load('railOpens');
check(railOpens(1, 0, true, null) && railOpens(1, 1, false, null), 'a card opens a folded rail on a wide screen');
check(!railOpens(1, 1, true, null), 'review cards alone do not open the rail over a phone\'s pane');
check(railOpens(2, 1, true, null), 'anything else in the rail still opens it on a phone');
check(!railOpens(3, 0, false, true) && railOpens(0, 0, true, false), 'a hand-fold or hand-open wins');
check(/railOpens\(/.test(fn('railFold')), 'railFold decides through railOpens');
check(/wtRailCards\(panes, wtSeen\(\)\)/.test(fn('render')), 'render builds the review cards');

/* ── T-UI-13: `r` outside text fields only ──────────────────────────────── */
const reviewKey = load('reviewKey', ['isTypingTarget']);
const k = (key, extra = {}) => ({ key, metaKey: false, ctrlKey: false, altKey: false,
                                   target: { tagName: 'DIV' }, ...extra });
check(reviewKey(k('r'), true, false), 'T-UI-13 r on a focused own-branch pane opens review');
check(!reviewKey(k('r', { target: { tagName: 'TEXTAREA' } }), true, false), 'T-UI-13 not in a textarea');
check(!reviewKey(k('r', { target: { tagName: 'INPUT' } }), true, false), 'T-UI-13 not in an input');
check(!reviewKey(k('r', { target: { tagName: 'DIV', isContentEditable: true } }), true, false),
      'T-UI-13 not in contenteditable');
check(!reviewKey(k('r', { ctrlKey: true }), true, false) && !reviewKey(k('r', { metaKey: true }), true, false),
      'T-UI-13 not with a modifier (Ctrl+R reloads)');
check(!reviewKey(k('r'), false, false), 'T-UI-13 not without an own-branch pane in focus');
check(!reviewKey(k('r'), true, true), 'T-UI-13 not while a dialog is open');
check(!reviewKey(k('R'), true, false) && !reviewKey(k('e'), true, false), 'only r');
check(/reviewKey\(/.test(src.slice(src.indexOf('const KEYS = ['), src.indexOf('function isTypingTarget'))),
      'T-UI-13 the KEYS table dispatches r through reviewKey');

/* ── SSE and markup ─────────────────────────────────────────────────────── */
check(/ev\.kind === 'worktree'/.test(fn('connect')) && /p\.worktree\.summary = d\.summary/.test(fn('connect')),
      'one SSE line mirrors worktree events into p.worktree.summary');
for (const id of new Set([...src.matchAll(/\$\('#((?:rev|wt)[\w-]*|f-wt)'\)/g)].map(x => x[1])))
  check(new RegExp(`id="${id}"`).test(html), `app.js reads $('#${id}') and index.html has no such element`);
check(/<dialog id="revdlg"/.test(html), 'index.html has the review dialog');

if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log('selftest_review: ok');
