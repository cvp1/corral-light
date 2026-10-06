/* The Needs-you rail you can trust (docs/ux-10x-plan.md Part A, T-INB-*).
 *
 * Driven against the real functions in static/app.js, extracted by name, with
 * a mini-DOM where a function builds nodes. Covers question cards, the paused
 * list, the blocking count, the phone's solo view, "seen means on screen" and
 * the cross-feed dialog. T-INB-10 (display state unchanged) is
 * selftest_display.mjs, which must stay green untouched.
 *
 * Run: node selftest_inbox.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('./static/style.css', import.meta.url), 'utf8');

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
function load(name, helpers = [], globals = {}) {
  const names = Object.keys(globals);
  const body = [...helpers, name].map(fn).join('\n') + `\nreturn ${name};`;
  return new Function(...names, body)(...names.map(k => globals[k]));
}

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };
const eq = (a, b, why) => check(JSON.stringify(a) === JSON.stringify(b),
                                `${why}: got ${JSON.stringify(a)}, wanted ${JSON.stringify(b)}`);

function mk(tag, cls, text) {
  return { tag, className: cls || '', textContent: text ?? '', title: '', children: [],
           dataset: {}, style: {}, hidden: false, disabled: false, onclick: null,
           append(...xs) { this.children.push(...xs); },
           appendChild(x) { this.children.push(x); return x; },
           setAttribute(k, v) { this[k] = v; } };
}
const texts = n => [n.textContent, ...n.children.flatMap(texts)].join('\n');
const find = (n, pred) => pred(n) ? n : n.children.reduce((a, c) => a || find(c, pred), null);
const flush = () => new Promise(r => setTimeout(r, 0));

const pane = (id, x = {}) => Object.assign({ id, label: 'Claude Code', title: 'pane ' + id,
  agent: 'claude', state: 'ready', pending: [], question: null, minimized: false,
  cwd: '/work/repo', worktree: null, events: [] }, x);

/* ── T-INB-1: an open question is a blocking card with the full text ────── */
const railExtras = load('railExtras');
const blockedCount = load('blockedCount');
const long = 'Which schema should the migration target?\n' + 'x'.repeat(400);
const asker = pane('a', { question: { text: long, at: 1 } });
const permer = pane('b', { pending: ['r1', 'r2'] });
const calm = pane('c');
let ex = railExtras([asker, permer, calm]);
eq(ex.asked.map(p => p.id), ['a'], 'T-INB-1 the asking pane is a question card');
eq(blockedCount([asker, permer, calm]), 3, 'T-INB-1 blocked counts permissions and questions');
eq(blockedCount([calm]), 0, 'T-INB-1 nothing open blocks nothing');
eq(railExtras([pane('e', { question: { text: '' } })]).asked.length, 0,
   'T-INB-1 an empty question is not a card');

const questionCard = load('questionCard', ['paneDir'], {
  el: mk, answerInPane: () => {} });
let card = questionCard(asker, false);
check(texts(card).includes(long), 'T-INB-1 the card carries the full question, not a preview');
check(texts(card).includes('asks you'), 'T-INB-1 the card says the agent is asking');
check(!!find(card, n => n.textContent === 'Answer in pane'), 'T-INB-1 Answer in pane button');
const hop = questionCard(pane('h', { question: { text: 'loop stopped', source: 'hop-limit' } }), false);
check(texts(hop).includes('Corral paused this loop'), 'T-INB-1 a hop-limit stop is the hub\'s words');
check(/\bhub\b/.test(hop.className), 'T-INB-1 a hop-limit card is marked as the hub\'s');

/* ── T-INB-2: Answer in pane opens the pane and focuses its composer ────── */
{
  const calls = [];
  const ta = { focus: o => calls.push(['focus', o]) };
  const g = {
    setMin: async (p, f) => { calls.push(['setMin', p.id, f]); },
    focusPane: id => calls.push(['focusPane', id]),
    requestAnimationFrame: cb => cb(),
    document: { querySelector: q => { calls.push(['q', q]); return ta; } },
    isNarrow: () => false,
  };
  const answerInPane = load('answerInPane', ['openFromRail'], g);
  await answerInPane(pane('m', { minimized: true, question: { text: 'q' } }), false);
  eq(calls.map(c => c[0]), ['setMin', 'focusPane', 'q', 'focus'],
     'T-INB-2 wide screen: restore, focus the pane, then its composer');
  check(calls[2][1] === '[data-pane="m"] .composer textarea', 'T-INB-2 the right composer');
  calls.length = 0;
  await answerInPane(pane('m', { minimized: true, question: { text: 'q' } }), true);
  eq(calls.map(c => c[0]), ['focusPane', 'q', 'focus'],
     'T-INB-2 / T-INB-7 phone: no restore (the solo view shows it), composer focused');
}

/* ── T-INB-3: a paused pane is a quiet row with one Resume ──────────────── */
ex = railExtras([pane('p', { state: 'detached' }),
                 pane('pq', { state: 'detached', question: { text: 'still open' } }),
                 calm]);
eq(ex.paused.map(p => p.id), ['p'], 'T-INB-3 a paused pane joins the quiet list');
eq(ex.asked.map(p => p.id), ['pq'], 'T-INB-3 a paused pane with a question shows once, as the question');
eq(blockedCount([pane('p', { state: 'detached' })]), 0, 'T-INB-3 paused never counts as blocked');
{
  const resumed = [], focused = [];
  const pausedRow = load('pausedRow', [], {
    el: mk, focusPane: id => focused.push(id), resumePane: async p => resumed.push(p.id) });
  const row = pausedRow(pane('p', { state: 'detached', title: 'nightly docs' }));
  check(texts(row).includes('nightly docs'), 'T-INB-3 the row names the pane');
  const rb = find(row, n => n.textContent === 'Resume');
  await rb.onclick(); await flush();
  eq(resumed, ['p'], 'T-INB-3 Resume resumes that pane only');
  check(rb.disabled, 'T-INB-3 Resume disables itself while resuming');
  find(row, n => n.className === 'pt').onclick();
  eq(focused, ['p'], 'T-INB-3 the name opens the pane');
}
// The render loop counts paused rows as quiet items, not blocked ones.
{
  const r = fn('render');
  check(/for \(const p of paused\) \{ n\.appendChild\(pausedRow\(p\)\); items\+\+; quiet\+\+; \}/
          .test(r), 'T-INB-3 render counts each paused row in items and quiet');
  check(/railFold\(items, blockedCount\(panes\), quiet, !!solo\)/.test(r),
        'T-INB-3 render passes the blocking count, which leaves paused out');
}

/* ── T-INB-4: the calm line needs no cards and no paused panes ──────────── */
{
  const r = fn('render');
  const at = r.indexOf("'Nothing. Quiet is the steady state.'");
  check(at > r.indexOf('pausedRow(p)'), 'T-INB-4 the calm check follows the paused list');
  check(/if \(!items\) n\.appendChild\(el\('div', 'calm'/.test(r),
        'T-INB-4 the calm line keys on items, which paused rows increment');
}

/* ── T-INB-5: on a phone a question opens the rail; paused alone does not ── */
const railOpens = load('railOpens');
eq(railOpens(1, 0, true, null), true, 'T-INB-5 phone: a question (blocking) opens the rail');
eq(railOpens(2, 2, true, null), false, 'T-INB-5 phone: paused only (quiet) stays shut');
eq(railOpens(2, 2, false, null), true, 'T-INB-5 desktop: paused rows still open the rail');
eq(railOpens(3, 0, true, true), false, 'T-INB-5 a hand-fold still wins');

/* ── T-INB-6: the solo view folds the rail for that view only ───────────── */
const soloPane = load('soloPane');
const ps = [pane('a'), pane('b')];
eq(soloPane(ps, 'a', true), 'a', 'T-INB-6 phone: the opened pane shows alone');
eq(soloPane(ps, 'a', false), null, 'T-INB-6 wide screen: no solo view');
eq(soloPane(ps, 'gone', true), null, 'T-INB-6 a closed pane ends the solo view');
eq(railOpens(5, 0, true, null, true), false, 'T-INB-6 solo folds the rail even with cards');
eq(railOpens(5, 0, true, false, true), false, 'T-INB-6 solo folds it even over a hand-open');
eq(railOpens(5, 0, false, null, true), true, 'T-INB-6 solo means nothing on a wide screen');
{
  const S = { focus: null, solo: null, railShut: null };
  const touched = [];
  const localStorage = new Proxy({}, { get: (_, k) => { touched.push(k); return () => {}; } });
  let renders = 0;
  const focusPane = load('focusPane', [], {
    S, isNarrow: () => true, render: () => renders++, localStorage,
    requestAnimationFrame: () => {}, document: { querySelector: () => null } });
  focusPane('b');
  eq([S.focus, S.solo, S.railShut], ['b', 'b', null],
     'T-INB-6 opening on a phone sets the solo view, not the hand-fold');
  eq(touched, [], 'T-INB-6 solo never writes corral.railShut');
  check(renders === 1, 'T-INB-6 one render');
  const wide = { focus: null, solo: null };
  load('focusPane', [], { S: wide, isNarrow: () => false, render: () => {},
    requestAnimationFrame: () => {}, document: { querySelector: () => null } })('a');
  eq(wide.solo, null, 'T-INB-6 a wide screen never solos');
}
check(src.includes("const back = el('button', 'fbtn', '‹ Needs you');") &&
      /back\.onclick = \(\) => \{ S\.solo = null; render\(\); \};/.test(src),
      'T-INB-6 the solo view has a Back control that only clears the solo view');

/* ── T-INB-7: solo never minimizes, so fan-out membership is unchanged ──── */
{
  const S = { panes: new Map(), solo: null, focus: null };
  const all = [pane('a'), pane('b', { minimized: true }), pane('c', { agent: 'host:sh' })];
  for (const p of all) S.panes.set(p.id, p);
  const composablePanes = load('composablePanes', [], { S });
  const before = composablePanes().map(p => p.id);
  const apis = [];
  const g = { S, isNarrow: () => true, render: () => {}, requestAnimationFrame: () => {},
              document: { querySelector: () => null },
              setMin: async () => apis.push('/api/session/minimize') };
  const focusPane = load('focusPane', [], g);
  const openFromRail = load('openFromRail', [], { ...g, focusPane });
  await openFromRail(S.panes.get('b'), true);
  await openFromRail(S.panes.get('a'), true);
  eq(apis, [], 'T-INB-7 opening from the rail on a phone calls no minimize');
  eq(composablePanes().map(p => p.id), before, 'T-INB-7 composablePanes() is unchanged');
  check(!fn('focusPane').includes('/api/session/minimize') &&
        !fn('soloPane').includes('api('), 'T-INB-7 the solo path has no API call');
}

/* ── T-INB-8: seen means on screen ───────────────────────────────────────── */
const seenPanes = load('seenPanes');
const boxes = { on: { top: 100, bottom: 500, height: 400 },
                part: { top: 900, bottom: 1300, height: 400 },
                below: { top: 1200, bottom: 1600, height: 400 },
                above: { top: -500, bottom: -10, height: 490 },
                flat: { top: 10, bottom: 10, height: 0 } };
const rectOf = id => boxes[id] || null;
const sp = ['on', 'part', 'below', 'above', 'flat', 'undrawn'].map(id => pane(id));
eq(seenPanes(sp, rectOf, 1000, false, null), ['on', 'part'],
   'T-INB-8 only panes overlapping the viewport are seen');
eq(seenPanes([pane('on', { minimized: true })], rectOf, 1000, false, null), [],
   'T-INB-8 a minimized pane is not seen');
eq(seenPanes([pane('on', { minimized: true })], rectOf, 1000, false, 'on'), ['on'],
   'T-INB-8 a minimized pane shown alone on a phone is seen');
eq(seenPanes(sp, rectOf, 1000, true, null), [],
   'T-INB-8 under the phone\'s full-width rail nothing is seen');
{
  const m = fn('markSeen');
  check(m.includes('if (!vis.has(p.id)) continue;'), 'T-INB-8 markSeen skips panes not on screen');
  check(m.includes('SEEN_MS'), 'T-INB-8 the debounce is kept');
}
check(/\$\('#grid'\)\.addEventListener\('scroll', markSeen/.test(src),
      'T-INB-8 scrolling the wall re-checks what is seen');

/* ── T-INB-9: cross-feed is an in-page dialog ───────────────────────────── */
{
  const crossfeedPlan = load('crossfeedPlan');
  const asked = { events: [{ kind: 'user' }] };
  const A = pane('a', { title: 'arm A', ...asked }), B = pane('b', { title: 'arm B', ...asked });
  const C = pane('c', { title: 'fresh' });
  const M = pane('m', { title: 'tucked', minimized: true, ...asked });
  const P = pane('p', { title: 'asleep', state: 'detached', ...asked });
  const H = pane('h', { title: 'shell', agent: 'host:sh' });
  const plan = crossfeedPlan([A, B, C, M, P, H], [A, B, C]);
  eq(plan.panes.map(p => p.id), ['a', 'b'], 'T-INB-9 the asked, composable panes take part');
  eq(plan.out, ['fresh (never asked)', 'tucked (minimized)', 'asleep (paused)'],
     'T-INB-9 the left-out panes are named with why; terminals are not panes to feed');

  const run = async answer => {
    const posts = [], toasts = [];
    const crossfeed = load('crossfeed', ['crossfeedPlan'], {
      S: { panes: new Map([A, B, C].map(p => [p.id, p])) },
      composablePanes: () => [A, B, C],
      askPreamble: async (panes, out) => { posts.push(['asked', panes.length, out.length]); return answer; },
      api: async (url, body) => { posts.push([url, body]); return { sent: 2, results: {} }; },
      toast: (m, b) => toasts.push([m, !!b]),
      window: { prompt: () => { throw new Error('window.prompt must not be used'); } },
    });
    await crossfeed();
    return { posts, toasts };
  };
  let r = await run(null);
  eq(r.posts, [['asked', 2, 1]], 'T-INB-9 Cancel sends nothing');
  r = await run('Round two, edited.');
  eq(r.posts[1], ['/api/session/crossfeed', { panes: ['a', 'b'], text: 'Round two, edited.' }],
     'T-INB-9 Send posts the edited preamble to the taking-part panes');
  check(!fn('crossfeed').includes('window.prompt'), 'T-INB-9 no window.prompt left');
  for (const id of ['xfdlg', 'xf-title', 'xf-who', 'xf-out', 'xf-text', 'xf-go'])
    check(html.includes(`id="${id}"`), `T-INB-9 the dialog has #${id}`);
  const ask = fn('askPreamble');
  check(ask.includes("d.returnValue === 'ok' ? ta.value : null"),
        'T-INB-9 Escape or Cancel resolves null');
}

/* ── CSS: the phone rail is full width open, a strip folded ─────────────── */
check(/\.rail\.right:not\(\.shut\)\{width:100%;z-index:56/.test(css),
      'T-VIS-A1 (static) phone: the open rail is full width above the header controls');

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('selftest_inbox: ok');
