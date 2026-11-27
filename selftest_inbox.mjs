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

const RR_HELPERS = ['paneDir', 'ctxTag', 'ctxPct', 'canReplyFromCard', 'cardTarget', 'replyMoved',
                    'railDrafts', 'replyBox', 'sendFromCard'];
const RR_S = { panes: new Map() };
const questionCard = load('questionCard', RR_HELPERS, {
  el: mk, answerInPane: () => {}, S: RR_S, RAIL_REPLY_MAX: 4000 });
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
  const focusPane = load('focusPane', ['shelfOpen'], { setDrawer: () => {},
    S, isNarrow: () => true, render: () => renders++, localStorage,
    requestAnimationFrame: () => {}, document: { querySelector: () => null } });
  focusPane('b');
  eq([S.focus, S.solo, S.railShut], ['b', 'b', null],
     'T-INB-6 opening on a phone sets the solo view, not the hand-fold');
  eq(touched, [], 'T-INB-6 solo never writes corral.railShut');
  check(renders === 1, 'T-INB-6 one render');
  const wide = { focus: null, solo: null };
  load('focusPane', ['shelfOpen'], { setDrawer: () => {}, S: wide, isNarrow: () => false, render: () => {},
    requestAnimationFrame: () => {}, document: { querySelector: () => null } })('a');
  eq(wide.solo, null, 'T-INB-6 a wide screen never solos');
}
check(src.includes("const back = el('button', 'fbtn', narrow ? '‹ Needs you' : '‹ All panes');") &&
      /back\.onclick = \(\) => \{ S\.solo = null; S\.wide = null; render\(\); \};/.test(src),
      'T-INB-6 the solo view has a Back control that only clears the solo/focus view');

/* ── T-INB-7: solo never minimizes, so fan-out membership is unchanged ──── */
{
  const S = { panes: new Map(), solo: null, focus: null };
  const all = [pane('a'), pane('b', { minimized: true }), pane('c', { agent: 'host:sh' })];
  for (const p of all) S.panes.set(p.id, p);
  const composablePanes = load('composablePanes', [], { S });
  const before = composablePanes().map(p => p.id);
  const apis = [];
  const g = { S, isNarrow: () => true, render: () => {}, requestAnimationFrame: () => {},
              setDrawer: () => {},
              document: { querySelector: () => null },
              setMin: async () => apis.push('/api/session/minimize') };
  const focusPane = load('focusPane', ['shelfOpen'], g);
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

/* ── module notices (docs/finops-module-plan.md §4.7) ───────────────────── */
{
  const LV = { info: 'info', warn: 'warn', bad: 'bad' };
  const railNotices = load('railNotices', ['noticeSlot', 'noticeKey'],
                          { NOTICE_LEVEL_CLASS: LV, NOTICE_CARDS: 8 });
  const noticeKey = load('noticeKey');
  const noticeSlot = load('noticeSlot');
  const nt = (id, o = {}) => ({ module: 'finops', moduleTitle: 'FinOps', id, level: 'warn',
                                title: 'Claude weekly 82% used', text: 'resets Tue', ...o });
  let r = railNotices({ items: [nt('a'), nt('b', { level: 'evil class' }), { id: 'x' }, null,
                                nt('c', { title: '' }), nt('d', { module: 7 })], more: 2 }, {});
  eq(r.shown.map(x => [x.id, x.level]), [['a', 'warn'], ['b', 'info']],
     'NOT-1 malformed notices are skipped; an unknown level maps to info');
  eq(r.more, 2, 'NOT-1 the hub\'s overflow count is kept');
  eq(railNotices(null, {}), { shown: [], more: 0 }, 'NOT-1 no field, no cards');
  const twelve = { items: Array.from({ length: 12 }, (_, i) => nt('n' + i)), more: 3 };
  eq([railNotices(twelve, {}).shown.length, railNotices(twelve, {}).more], [8, 7],
     'NOT-1 never more than 8 cards; the rest, and the hub\'s overflow, are counted');
  const hid = { [noticeSlot(nt('n0'))]: noticeKey(nt('n0')) };
  eq(railNotices(twelve, hid).shown.map(x => x.id).slice(-1), ['n8'],
     'NOT-2 Not now first: hiding one lets the ninth in');
  const seen = { [noticeSlot(nt('a'))]: noticeKey(nt('a')) };
  eq(railNotices({ items: [nt('a'), nt('b')] }, seen).shown.map(x => x.id), ['b'],
     'NOT-2 Not now hides that notice');
  eq(railNotices({ items: [nt('a', { level: 'bad' })] }, seen).shown.length, 1,
     'NOT-2 a notice whose level changes comes back');
  eq(railNotices({ items: [nt('a', { title: 'Claude weekly 91% used' })] }, seen).shown.length, 1,
     'NOT-2 a notice whose title changes comes back');

  const opened = [], marked = [];
  let renders = 0;
  const noticeCard = load('noticeCard', [], {
    el: mk, NOTICE_LEVEL_CLASS: LV, openModule: m => opened.push(m),
    noticeMarkSeen: x => marked.push(x.id), render: () => renders++ });
  const c = noticeCard(railNotices({ items: [nt('a', { title: '<script>alert(1)</script>',
                                                       level: 'bad' })] }, {}).shown[0]);
  eq(c.className, 'ncard nmod bad', 'NOT-3 the level reaches the page through the fixed table');
  check(texts(c).includes('FinOps — <script>alert(1)</script>'), 'NOT-3 the title is text');
  const btns = [];
  (function walk(x) { if (x.tag === 'button') btns.push(x); x.children.forEach(walk); })(c);
  eq(btns.map(b => b.textContent), ['Open', 'Not now'], 'NOT-3 Open and Not now, nothing else');
  check(!find(c, x => /perm|pbtn/.test(x.className)), 'NOT-3 no permission controls');
  btns[0].onclick(); btns[1].onclick();
  eq([opened, marked, renders], [['finops'], ['a'], 1], 'NOT-3 Open opens the module; Not now hides and repaints');
  eq(railOpens(1, 1, true, null), false, 'NOT-4 phone: a notice alone (quiet) never pops the rail');
  eq(railOpens(1, 1, false, null), true, 'NOT-4 desktop: a notice opens the rail');
  // The real el() sets text, never markup (the card tests use a stand-in).
  const elDef = src.slice(src.indexOf('const el = '), src.indexOf('return n; };', src.indexOf('const el = ')));
  check(/textContent/.test(elDef) && !/innerHTML/.test(elDef), 'NOT-3 el() writes textContent only');
  const blocked = load('blockedCount', ['wtReviewDue'], {});
  eq(blocked([{ pending: [], question: null }]), 0,
     'NOT-4 a wall with notices and nothing pending has a hot count of 0');
  check(/setInterval\([^]*loadModules[^]*NOTICE_POLL_MS/.test(fn('wireModuleDialog')),
        'NOT-5 an idle tab polls for notices');
  check(/S\.moduleNotices = d\.notices/.test(fn('loadModules')),
        'NOT-5 the poll replaces the notices it shows');
  const rail = fn('render');
  check(/railNotices\(S\.moduleNotices[\s\S]{0,120}items\+\+; quiet\+\+/.test(rail),
        'NOT-4 the rail counts each notice as quiet');
  check(!fn('blockedCount').includes('otice'), 'NOT-4 notices never enter the hot count');
  for (const lv of ['info', 'warn', 'bad'])
    check(css.includes(`.ncard.nmod.${lv}{`), `NOT-3 CSS has the ${lv} notice class`);
}

/* ── CSS: the phone rail is full width open, a strip folded ─────────────── */
check(/\.rail\.right:not\(\.shut\)\{width:100%;z-index:56/.test(css),
      'T-VIS-A1 (static) phone: the open rail is full width above the header controls');

/* ── the left rail: open or a strip ─────────────────────────────────── */
const leftShut = new Function(`const LEFT_STRIP_BELOW = 1600;\n${fn('leftShut')}\nreturn leftShut;`)();
eq(leftShut(null, 1280), true, 'T-LR-1 the 1280px laptop starts on the strip');
eq(leftShut(null, 1920), false, 'T-LR-1 a 1920px screen starts open');
eq(leftShut(false, 1280), false, 'T-LR-2 a hand-open wins on a narrow screen');
eq(leftShut(true, 1920), true, 'T-LR-2 a hand-fold wins on a wide screen');
check(/\.app,\.app\.railshut,\.app\.lshut\{grid-template-columns:minmax\(0,1fr\)\}/.test(css),
      'T-LR-3 (static) phone: the strip column does not leak onto the one-column layout');
check(/@media\(min-width:821px\)\{\s*\.app\.lshut/.test(css),
      'T-LR-3 (static) the strip styles are desktop-only');

/* ── T-WAIT-*: turns that ended on the operator (2026-11-22 panel, item 1) ── */
{
  const consts = ['AGENT_ORIGIN_VIAS', 'WAIT_MAX_AGE_S', 'WAIT_EXCERPT', 'WAIT_SKIP_VIAS']
    .map(k => new RegExp(`^const ${k} = .*;$`, 'm').exec(src)[0]).join('\n');
  const waitingTurns = new Function(`${consts}\n${fn('paneAge')}\n${fn('waitExcerpt')}\n` +
                                    `${fn('waitingTurns')}\nreturn waitingTurns;`)();
  const NOW = Date.parse('2026-11-22T15:00:00Z');
  const ago = m => new Date(NOW - m * 60000).toISOString();
  const turn = (endMin, text, x = {}) => [
    { seq: 1, at: ago(endMin + 5), kind: 'user', data: { text: 'plan it' } },
    { seq: 2, at: ago(endMin + 4), kind: 'tool', data: { title: 'Read x' } },
    { seq: 3, at: ago(endMin + 1), kind: 'text', data: { text: text.slice(0, 10) } },
    { seq: 4, at: ago(endMin + 1), kind: 'text', data: { text: text.slice(10) } },
    { seq: 5, at: ago(endMin), kind: 'turn_end', data: { stopReason: 'end_turn', ...x } },
  ];
  const ask = 'Plan, 4 steps.\n\nWant me to start on step 1?';
  const ws = waitingTurns([
    pane('young', { events: turn(5, ask) }),
    pane('old', { events: turn(45, ask) }),                       // past the 30-min decay
    pane('replied', { events: [...turn(20, ask), { seq: 6, at: ago(1), kind: 'user', data: {} }] }),
    pane('busy', { state: 'busy', events: turn(20, ask) }),
    pane('perm', { pending: ['r'], events: turn(20, ask) }),
    pane('asked', { question: { text: 'q' }, events: turn(20, ask) }),
    pane('peer', { turnVia: 'peer', events: turn(20, ask) }),
    pane('panel', { turnVia: 'panel', events: turn(20, ask) }),
    pane('stopped', { events: turn(20, ask, { stopReason: 'cancelled' }) }),
    pane('stale', { events: turn(73 * 60, ask) }),
    pane('dismissed', { events: turn(20, ask) }),
    pane('review', { events: turn(20, ask) }),
  ], { dismissed: 5 }, NOW, new Set(['review']));
  eq(ws.map(w => w.p.id), ['old', 'young'],
     'T-WAIT-1 only human turns that ended on the operator, oldest first, past the idle decay');
  eq(ws[0].excerpt, 'Want me to start on step 1?', 'T-WAIT-2 the excerpt is the end of the joined answer');
  check(Math.round(ws[0].age) === 45 * 60, 'T-WAIT-3 age is from the turn_end');
  eq(waitingTurns([pane('d', { events: turn(20, ask) })], { d: 4 }, NOW).length, 1,
     'T-WAIT-4 a dismissal is for one turn; a newer turn surfaces again');
  const waitExcerpt = new Function(`${consts}\n${fn('waitExcerpt')}\nreturn waitExcerpt;`)();
  const cut = waitExcerpt('y'.repeat(500));
  check(cut.length === 220 && cut.startsWith('…'), 'T-WAIT-5 the excerpt is bounded from the front');
  // Rendering: quiet (never blocking), with its own section count.
  const r = fn('render');
  check(/n\.appendChild\(waitCard\(w, narrow\)\); items\+\+; quiet\+\+;/.test(r),
        'T-WAIT-6 render counts each wait card as quiet');
  check(/waits\.slice\(0, WAIT_SHOWN\)/.test(r), 'T-WAIT-7 the rail shows at most WAIT_SHOWN');
  eq(blockedCount([pane('w', { events: turn(5, ask) })]), 0, 'T-WAIT-8 a wait never counts as blocked');
}

/* ── T-CNT-1: the header number counts what needs you, not quiet rows ───── */
{
  const nodes = {};
  const $ = q => (nodes[q] ||= { textContent: '', className: '', title: '',
                                  classList: { toggle() {} } });
  const railFold = load('railFold', ['railOpens'], {
    $, isNarrow: () => false, S: { railShut: null } });
  railFold(5, 1, 3);
  eq(nodes['#railhead'].textContent, 'Needs you · 2 ▾', 'T-CNT-1 header shows items minus quiet');
  eq(nodes['#railtabnum'].textContent, '2', 'T-CNT-1 the folded tab digit matches');
  railFold(3, 0, 3);
  eq(nodes['#railhead'].textContent, 'Needs you ▾', 'T-CNT-2 only quiet rows: no number');
  check(/\bsoft\b/.test(nodes['#railtabnum'].className), 'T-CNT-2 the tab is soft, not zero-grey only');
}

/* ── T-CTX-*: context use is always on screen ───────────────────────────── */
{
  const ctxPct = load('ctxPct');
  const ctxTag = load('ctxTag', ['ctxPct']);
  eq(ctxTag(pane('k', { usage: { used: 42, size: 100 } })), ' · 42% ctx', 'T-CTX-1 rail cards carry ctx');
  eq(ctxTag(pane('k')), ' · ctx unknown', 'T-CTX-2 unknown is said, not hidden');
  check(/ctxTag\(p\)/.test(fn('questionCard')), 'T-CTX-3 question cards carry ctx');
  check(/'ctx unk', 'ctx unknown'/.test(src), 'T-CTX-4 the pane header says unknown');
  check(!/ctx !== null && ctx >= CTX_WARN_PCT\) \{\s*const f = el\('span', 'flag warn'/.test(src),
        'T-CTX-5 the roster no longer hides ctx below the warn line');
  check(/'flag ctxf'/.test(src), 'T-CTX-5 the roster always draws a ctx flag');
  void ctxPct;
}


/* ── T-RR: reply from the card (build item 7) ───────────────────────────── */
{
  const cardTarget = load('cardTarget');
  eq(cardTarget(pane('a', { question: { text: 'q?', at: 'T', id: 'abc' } })), 'q:abc',
     'T-RR-1 a question binds to its id');
  eq(cardTarget(pane('a', { question: { text: 'q?', at: 'T' } })), 'q:T',
     'T-RR-1 an older question without an id binds to when it was asked');
  eq(cardTarget(pane('a'), { seq: 7 }), 't:7', 'T-RR-1 a waiting turn binds to its turn_end');
  eq(cardTarget(pane('a')), null, 'T-RR-1 nothing to answer is no target');

  const can = load('canReplyFromCard');
  check(can(pane('a', { question: { text: 'q' } })), 'T-RR-2 a local question can be answered here');
  check(can(pane('a', { remote: { host: 'peer-b' }, question: { text: 'q' } })),
        'T-RR-2 a pane from another hub answers here too (through its hub\'s reply command)');
  check(!can(pane('a', { question: { text: 'x', source: 'hop-limit' } })),
        'T-RR-2 a hop-limit stop is not a question to answer here');

  const moved = load('replyMoved');
  check(!moved(null, 'q:a'), 'T-RR-3 no draft, nothing moved');
  check(!moved({ text: 'hi', target: 'q:a' }, 'q:a'), 'T-RR-3 same card, not moved');
  check(moved({ text: 'hi', target: 'q:a' }, 'q:b'), 'T-RR-3 a replaced question is moved');
  check(!moved({ text: '  ', target: 'q:a' }, 'q:b'), 'T-RR-3 an empty draft is never flagged');

  const railSig = load('railSig');
  const k = (key, t) => Object.assign(mk('div', 'ncard', ''), { dataset: key ? { key } : {},
    querySelector: () => ({ textContent: t || '' }) });
  eq(railSig([k('q:a:1'), k(null, 'Login')]), 'q:a:1|ncard:Login', 'T-RR-4 keyed cards, then title');
  check(railSig([k('w:a:5')]) !== railSig([k('w:a:6')]), 'T-RR-4 a new turn is a different rail');

  // The card carries a reply box bound to the pane, remote or not.
  const asker2 = pane('q', { question: { text: 'Ship it?', at: 'T', id: 'qid' } });
  const qc = questionCard(asker2, false);
  const box = find(qc, n => n.tag === 'textarea');
  check(!!box && box.dataset.reply === 'q', 'T-RR-5 a question card has a reply box for its pane');
  check(!!find(qc, n => n.textContent === 'Send'), 'T-RR-5 with a Send button');
  check(!!find(qc, n => n.textContent === 'Answer in pane'), 'T-RR-5 and still Answer in pane');
  eq(qc.dataset.key, 'q:q:qid', 'T-RR-5 the card is keyed by its question');
  const rc = questionCard(pane('r', { remote: { host: 'peer-b' }, question: { text: 'Ship?', at: 'T' } }), false);
  check(!!find(rc, n => n.tag === 'textarea'), 'T-RR-5 a remote pane has a reply box too');
  RR_S.railDrafts = { q: { text: 'yes', target: 'q:old' } };
  const qm = questionCard(asker2, false);
  check(texts(qm).includes('this changed since you typed'), 'T-RR-5 a moved draft is marked');
  eq(find(qm, n => n.tag === 'textarea').value, 'yes', 'T-RR-5 and its words are kept');
  RR_S.railDrafts = {};

  const waitCard = load('waitCard', [...RR_HELPERS, 'fmtAge', 'dismissWait', 'waitDismiss', 'waitSeen'],
    { el: mk, answerInPane: () => {}, S: RR_S, RAIL_REPLY_MAX: 4000, render: () => {},
      localStorage: { getItem: () => null, setItem: () => {} } });
  const wc = waitCard({ p: pane('w'), seq: 9, age: 60, excerpt: 'Want me to start?' }, false);
  eq(wc.dataset.key, 'w:w:9', 'T-RR-6 a waiting card is keyed by its turn');
  check(!!find(wc, n => n.tag === 'textarea'), 'T-RR-6 a waiting card has a reply box');
  check(!!find(wc, n => n.textContent === 'Open'), 'T-RR-6 Open still opens the pane');
}

/* sendFromCard: the target rides along; a 409 keeps the words. */
for (const [label, run] of [
  ['ok', async () => {
    const posted = [], toasts = [];
    const S = { panes: new Map(), railDrafts: { a: { text: 'yes', target: 't:3' } } };
    const send = load('sendFromCard', ['railDrafts'], { S, isLoginCommand: () => false,
      api: async (u, b) => { posted.push([u, b]); return { ok: true }; },
      toast: (m, bad) => toasts.push([m, !!bad]), refresh: async () => {},
      document: { querySelector: () => null }, insertIntoComposer: () => {} });
    const ta = { value: ' yes ' }, b = { disabled: false, textContent: 'Send' };
    await send(pane('a'), 't:3', ta, b);
    eq(posted, [['/api/session/send', { pane: 'a', text: 'yes', expect: 't:3' }]],
       'T-RR-7 the reply carries what the card showed');
    eq([ta.value, 'a' in S.railDrafts, b.disabled], ['', false, false], 'T-RR-7 sent: box and draft cleared');
    check(toasts.some(([m, bad]) => !bad && /sent to/.test(m)), 'T-RR-7 says where it went');
  }],
  ['moved, card still there', async () => {
    const toasts = [], inserted = [];
    const S = { panes: new Map(), railDrafts: {} };
    const err = Object.assign(new Error('moved'), { status: 409, body: { reason: 'moved' } });
    const send = load('sendFromCard', ['railDrafts'], { S, isLoginCommand: () => false,
      api: async () => { throw err; }, toast: (m, bad) => toasts.push([m, !!bad]),
      refresh: async () => {}, document: { querySelector: () => ({}) },
      insertIntoComposer: (...a) => inserted.push(a) });
    const ta = { value: 'yes' };
    await send(pane('a'), 'q:x', ta, { disabled: false });
    eq(S.railDrafts.a, { text: 'yes', target: 'moved' }, 'T-RR-8 a stale reply keeps its words, marked');
    eq(ta.value, 'yes', 'T-RR-8 the box is not cleared');
    check(toasts.some(([m, bad]) => bad && /not sent/.test(m)), 'T-RR-8 and says it was not sent');
    eq(inserted.length, 0, 'T-RR-8 the card is still there, so the words stay in it');
  }],
  ['moved, card gone', async () => {
    const inserted = [];
    const S = { panes: new Map(), railDrafts: {} };
    const err = Object.assign(new Error('moved'), { status: 409, body: { reason: 'moved' } });
    const send = load('sendFromCard', ['railDrafts'], { S, isLoginCommand: () => false,
      api: async () => { throw err; }, toast: () => {}, refresh: async () => {},
      document: { querySelector: () => null }, insertIntoComposer: (...a) => inserted.push(a) });
    await send(pane('a'), 'q:x', { value: 'yes' }, { disabled: false });
    eq(inserted.map(a => a.slice(0, 2)), [['a', 'yes']], 'T-RR-9 no card left: the words go to the pane box');
    check(!('a' in S.railDrafts), 'T-RR-9 and the rail draft is dropped');
  }],
  ['login', async () => {
    let called = 0; const toasts = [];
    const send = load('sendFromCard', ['railDrafts'], { S: { panes: new Map() },
      isLoginCommand: () => true, api: async () => { called++; }, toast: (m, bad) => toasts.push(bad),
      refresh: async () => {}, document: { querySelector: () => null }, insertIntoComposer: () => {} });
    await send(pane('a'), 't:1', { value: '/login' }, { disabled: false });
    eq([called, toasts], [0, [true]], 'T-RR-10 a sign-in is never sent from a card');
  }],
]) { try { await run(); } catch (e) { check(false, `T-RR ${label}: ${e.stack}`); } }

{
  const r = fn('render');
  check(/const needs = \$\('#needs'\), n = document\.createDocumentFragment\(\)/.test(r),
        'T-RR-11 the rail is built off-screen');
  check(/typing\.dataset\.reply && needs\.dataset\.sig === sig/.test(r),
        'T-RR-11 and kept as it is while a reply box has focus and the cards are the same');
  check(/box\.setSelectionRange\(caret\[1\], caret\[2\]\)/.test(r),
        'T-RR-11 a rebuild while typing puts the caret back');
  check(/\.ncard \.rreply textarea\{/.test(css) && /pointer:coarse\)\{\.ncard \.rreply textarea\{font-size:16px;min-height:44px/.test(css),
        'T-RR-12 the box is styled, 16px and 44px on touch (no iOS zoom)');
  check(/question'\) p\.question = \{[^}]*id: d\.id/.test(src), 'T-RR-13 the live question keeps its id');
}


/* ── T-SR: a scheduled run that did not happen gets a receipt ───────────── */
{
  const misses = load('scheduleMisses', [], { MISS_SHOWN: 5 });
  const jobs = [
    { id: 'a', title: 'nightly', last: '2026-11-23T13:00:00Z', last_error: 'its pane is gone', repeat: 'daily' },
    { id: 'b', title: 'ok', last: '2026-11-23T12:00:00Z', last_error: null },
    { id: 'c', title: 'once', last: '2026-11-23T14:00:00Z', last_error: 'missed while Corral Light was down', failed: true },
  ];
  eq(misses(jobs, {}).map(j => j.id), ['c', 'a'], 'T-SR-1 failed and missed runs, newest first; a good run is not one');
  eq(misses(jobs, { a: '2026-11-23T13:00:00Z' }).map(j => j.id), ['c'], 'T-SR-2 Got it hides that run');
  eq(misses([{ ...jobs[0], last: '2026-11-24T13:00:00Z' }], { a: '2026-11-23T13:00:00Z' }).length, 1,
     'T-SR-2 the next failed run of the same job shows again');
  eq(misses(null, {}), [], 'T-SR-3 no schedule, no cards');
  const card = load('missCard', ['missDismiss', 'missSeen'], { el: mk, S: { schedule: jobs },
    render: () => {}, localStorage: { getItem: () => null, setItem: () => {} } })(jobs[0]);
  check(texts(card).includes('nightly did not run') && texts(card).includes('its pane is gone'),
        'T-SR-4 the card names the job and why');
  check(texts(card).includes('it will try again'), 'T-SR-4 a repeat says it will try again');
  const r = fn('render');
  check(/scheduleMisses\(S\.schedule, missSeen\(\)\)\) \{ n\.appendChild\(missCard\(m\)\); items\+\+; quiet\+\+; \}/.test(r),
        'T-SR-5 quiet in the rail, never blocking');
  check(/schedLine\(j\)/.test(r) && /last run failed: \$\{j\.last_error\}/.test(fn('schedLine')),
        'T-SR-6 a repeat\'s failure shows on its Scheduled row');
  const hub = readFileSync(new URL('./hub.py', import.meta.url), 'utf8');
  check(/MGR\.schedule\.on_miss = _notify_schedule_miss/.test(hub), 'T-SR-7 the hub alerts (desktop and phone) on a miss');
}

/* ── T-SP: the Scheduled jobs page: on, off, delete ─────────────────────── */
{
  const line = load('schedLine');
  const on = { id: 'a', title: 'nightly', action: 'start', at: '2026-11-28T13:00:00Z', repeat: 'daily', agent: 'claude' };
  check(/^start · .* · daily · claude$/.test(line(on)), 'T-SP-1 an armed job reads as before');
  eq(line({ ...on, paused: true }), 'off · start · daily · claude', 'T-SP-2 an off repeat names its rhythm, not a stale date');
  check(line({ ...on, paused: true, repeat: '' }).startsWith('off · start · '), 'T-SP-2 an off one-shot keeps its time');
  eq(line({ ...on, failed: true, last_error: 'boom' }), 'failed: boom', 'T-SP-3 a failed run says why');
  const sum = load('schedSummary');
  eq(sum([]), 'Nothing is scheduled.', 'T-SP-4 an empty page says so');
  eq(sum([on, { ...on, paused: true }, { ...on, failed: true }]), '1 on · 1 off · 1 did not run', 'T-SP-4 the count by state');
  const calls = [];
  const env = { el: mk, S: { schedule: [] }, SCHED_ARMED: null, renderSched: () => calls.push('render'),
    schedAct: (p, b) => calls.push([p, b]) };
  const row = load('schedRow', ['schedLine'], env);
  const r1 = row(on);
  const box = find(r1, n => n.tag === 'input');
  check(box && box.checked === true, 'T-SP-5 an armed job\'s switch is on');
  box.checked = false; box.onchange();
  eq(calls.pop(), ['/api/session/schedule/pause', { id: 'a', paused: true }], 'T-SP-5 switching off pauses, it does not delete');
  check(find(row({ ...on, paused: true }), n => n.tag === 'input').checked === false, 'T-SP-6 an off job\'s switch is off');
  check(!find(row({ ...on, failed: true, last_error: 'x' }), n => n.tag === 'input'),
        'T-SP-7 a failed record has no switch, only Delete');
  const rm = find(r1, n => n.tag === 'button');
  rm.onclick();
  eq(calls.pop(), 'render', 'T-SP-8 the first Delete click only arms');
  check(!calls.length, 'T-SP-8 nothing was removed on one click');
  check(/SCHED_ARMED = null;\s*schedAct\('\/api\/session\/schedule\/remove'/.test(fn('schedRow')),
        'T-SP-8 the second click removes');
  check(/id="scheddlg"/.test(html) && /id="schedbtn"/.test(html), 'T-SP-9 the page and its button exist');
  check(/lab\.onclick = openSched/.test(fn('render')), 'T-SP-9 the rail\'s Scheduled label opens it');
  const hub = readFileSync(new URL('./hub.py', import.meta.url), 'utf8');
  check(/"\/api\/session\/schedule\/pause"/.test(hub), 'T-SP-10 the hub has the pause route');
}

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('selftest_inbox: ok');
