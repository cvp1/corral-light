/* Rigs… in the browser (DESIGN-5 S12, ported to Light in DESIGN-6 S1): what
 * the dialog shows for each seat, what it shows for a refused rig, that
 * Remove takes two clicks, and that the two doors to it exist -- the `Rigs…`
 * item in New and the ⌘K row.
 *
 * Driven against the real functions in static/app.js with a capturing DOM
 * whose innerHTML setter throws; the server's lines are the rendering
 * (corral_core/rigs.render), so what is under test is that every seat gets
 * exactly one row, as TEXT, classed by its outcome, and that a refusal shows
 * every reason.
 *
 * Run: node selftest_rigs.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');

function fn(name) {
  const start = src.search(new RegExp(`(^|\\n)(async )?function ${name}\\(`));
  if (start < 0) throw new Error(`no function ${name} in app.js`);
  let depth = 0;
  const from = src.indexOf('function ' + name, start);
  for (let j = src.indexOf('{', from); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(from, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

// T1.6: any markup path throws, so a row that passes was built from text.
const mk = (tag, cls, text) => {
  const n = { tag, className: cls || '', textContent: text ?? '', kids: [],
              disabled: false, type: '', onclick: null, dataset: {},
              appendChild(k) { this.kids.push(k); return k; },
              replaceChildren(...ks) { this.kids = ks; } };
  Object.defineProperty(n, 'innerHTML', { set() { throw new Error('innerHTML'); } });
  return n;
};
const RIG_PROBLEM = new Set(['failed', 'withheld', 'not-restored']);

/* ── T1.1 one row per seat, as text ──────────────────────────────────────── */
const rows = new Function('el', 'RIG_PROBLEM', `${fn('rigOutcomeRows')} return rigOutcomeRows;`)(
  mk, RIG_PROBLEM);
const OUT = ['resumed', 'rebuilt', 'started-fresh', 'fresh-primed', 'withheld',
             'not-restored', 'failed'];
const r = { outcomes: OUT.map((o, i) => ({ seat: 's' + i, outcome: o })),
            lines: OUT.map((o, i) => `@s${i}  ${o}  claude — <b>not markup</b>`) };
const got = rows(r);
check(got.length === OUT.length, `${got.length} rows for ${OUT.length} seats`);
got.forEach((row, i) => {
  check(row.className.includes('o-' + OUT[i]), `row ${i} class ${row.className}`);
  check(row.kids[0].textContent === OUT[i], `row ${i} pill reads ${row.kids[0].textContent}`);
  check(row.kids[1].textContent === r.lines[i], `row ${i} is not the server's line verbatim`);
  check(row.className.includes(' bad') === RIG_PROBLEM.has(OUT[i]),
        `row ${i} (${OUT[i]}) problem styling: ${row.className}`);
});

/* ── T1.2 a refusal shows every reason; api() keeps the body ─────────────── */
const refused = new Function('el', `${fn('rigRefusedRows')} return rigRefusedRows;`)(mk);
const e = new Error('rig refused'); e.body = { refused: ['@a: unknown agent', '@b: named twice'] };
const rr = refused(e);
check(rr.length === 3 && /nothing was started/.test(rr[0].textContent),
      'a refused rig does not say nothing was started');
check(rr[1].textContent.includes('@a: unknown agent') && rr[2].textContent.includes('@b: named twice'),
      'a refused rig does not show every reason');
check(refused(new Error('network down'))[0].textContent === 'network down',
      'an error with no reasons loses its message');

const BODY400 = { error: 'rig refused', refused: ['@a: live now', '@b: unknown lane'] };
const api = new Function('fetch', `async ${fn('api')} return api;`)(
  async () => ({ ok: false, status: 400, statusText: 'Bad Request',
                 async json() { return BODY400; } }));
let thrown = null;
try { await api('/api/session/rigs/up', { name: 'pair' }); } catch (x) { thrown = x; }
check(thrown && thrown.status === 400 && thrown.message === 'rig refused',
      `api() on a 400 threw ${thrown && thrown.message}`);
check(thrown && thrown.body && thrown.body.refused && thrown.body.refused.length === 2,
      'api() on a 400 dropped the body, so a refusal can show only one reason');
check(thrown && refused(thrown).length === 3,
      'a real api() refusal does not reach the dialog as one row per reason');

/* ── T1.3 rigUp: the answer lands in #rig-out; the button always returns ── */
function upHarness(apiImpl) {
  const nodes = { '#rig-out': mk('div') };
  const seen = { posted: [], refreshed: 0, nodes };
  seen.rigUp = new Function('$', 'el', 'api', 'refresh', 'rigOutcomeRows', 'rigRefusedRows',
    `async ${fn('rigUp')} return rigUp;`)(
    s => nodes[s], mk,
    async (path, body) => { seen.posted.push([path, body]); return apiImpl(path, body); },
    () => { seen.refreshed++; }, rows, refused);
  return seen;
}
const ok = upHarness(async () => r);
const btn = mk('button');
await ok.rigUp('team', btn);
check(ok.posted.length === 1 && ok.posted[0][0] === '/api/session/rigs/up'
      && ok.posted[0][1].name === 'team', `posted ${JSON.stringify(ok.posted)}`);
check(ok.nodes['#rig-out'].kids.length === OUT.length + 1,
      'rig-out does not hold a header plus one row per seat');
check(ok.refreshed === 1, `refreshed ${ok.refreshed} times after a good up`);
check(!btn.disabled, 'the Up button stayed disabled after success');

const no = upHarness(async () => { throw thrown; });
const btn2 = mk('button');
await no.rigUp('pair', btn2);
check(!btn2.disabled, 'the Up button stayed disabled after a throw');
check(no.refreshed === 0, 'a refused up still refreshed, as if something started');
check(no.nodes['#rig-out'].kids.length === 3
      && /nothing was started/.test(no.nodes['#rig-out'].kids[0].textContent),
      'a refused up does not show the refusal and every reason');

/* ── T1.4 + T1.6 the list: Remove takes two clicks; names are text ───────── */
const listNodes = { '#rig-list': mk('div'), '#rig-error': mk('p') };
const listPosts = [];
let renders = 0;
const listApi = async (path, body) => {
  if (body) { listPosts.push([path, body]); return { ok: true }; }
  renders++;
  return { rigs: [{ name: '<b>x</b>', seats: ['author', 'reviewer'] },
                  { name: 'broken', error: 'bad toml' }] };
};
// S2 helpers, real, shared by T1.4 and T2.2.
const hintFns = new Function(`${fn('rigSaveHint')} ${fn('rigLiveSeats')} ${fn('rigLiveText')}
  return { rigSaveHint, rigLiveSeats, rigLiveText };`)();
function listHarness(nodes, apiImpl, panes, rigUpImpl) {
  const ctx = { RIG_ROWS: [] };
  const f = new Function('$', 'el', 'api', 'rigUp', 'S', 'rigLiveSeats', 'rigLiveText', 'ctx',
    `let RIG_ROWS; const out = async function () {
       await (async ${fn('renderRigList')})(); ctx.RIG_ROWS = RIG_ROWS; };
     return out;`)(
    s => nodes[s], mk, apiImpl, rigUpImpl || (() => {}), { panes: new Map(panes.map(p => [p.id, p])) },
    hintFns.rigLiveSeats, hintFns.rigLiveText, ctx);
  return { run: f, ctx };
}
const renderRigList = listHarness(listNodes, listApi, []).run;
await renderRigList();
const listed = listNodes['#rig-list'].kids;
check(listed.length === 2, `${listed.length} list rows for 2 rigs`);
check(listed[0].kids[0].textContent === '<b>x</b>', 'a rig name was not rendered as text');
check(listed[0].kids[1].textContent === '@author @reviewer', `seats read ${listed[0].kids[1].textContent}`);
check(listed[1].kids[3].disabled === true, 'an unreadable rig can still be brought up');
check(listed[1].kids[1].textContent === 'unreadable: bad toml', 'an unreadable rig hides why');
const rm = listed[0].kids[4];
await rm.onclick();
check(listPosts.length === 0, 'the first Remove click POSTed');
check(rm.textContent === 'Remove — sure?', 'the first Remove click did not ask');
await rm.onclick();
check(listPosts.length === 1 && listPosts[0][0] === '/api/session/rigs/rm'
      && listPosts[0][1].name === '<b>x</b>', `second click posted ${JSON.stringify(listPosts)}`);

/* ── T1.5 the two doors ──────────────────────────────────────────────────── */
const ndStart = html.indexOf('<dialog id="newdlg">');
const newdlg = html.slice(ndStart, html.indexOf('</dialog>', ndStart));
check(/id="rigsbtn"[^>]*>Rigs…</.test(newdlg), 'no Rigs… item inside the New dialog');
check(html.includes('<dialog id="rigdlg">'), 'no rig dialog in index.html');
for (const id of ['rig-list', 'rig-name', 'rig-replace', 'rig-save', 'rig-error', 'rig-out'])
  check(html.includes(`id="${id}"`), `index.html lacks #${id}`);

function palette(query) {
  let shown = null;
  const run = new Function('PAL', 'attachTarget', 'S', 'renderPalette', 'setTimeout',
    'clearTimeout', 'api', `${fn('paletteResults')} return paletteResults;`)(
    { seq: 0 }, () => null, { panes: new Map(), archived: [] },
    rows => { shown = rows; }, () => 0, () => {}, async () => ({ hits: [] }));
  run(query);
  return shown || [];
}
for (const q of ['', 'rig', 'rigs', 'RIGS'])
  check(palette(q).some(x => x.kind === 'rigs'), `⌘K "${q}" has no Rigs row`);
// The control: a needle that matches nothing must not conjure the row.
check(!palette('zzqx').some(x => x.kind === 'rigs'), '⌘K shows Rigs for a needle it does not match');

let opened = 0, closed = 0, newClicked = 0;
const activate = new Function('$', 'openRigs', 'api', 'refresh', 'toast',
  `async ${fn('activatePalette')} return activatePalette;`)(
  s => s === '#palette' ? { close() { closed++; } } : { click() { newClicked++; } },
  () => { opened++; }, async () => { throw new Error('no POST expected'); }, () => {}, () => {});
await activate({ kind: 'rigs' });
check(opened === 1 && closed === 1, `the palette row opened ${opened}, closed ${closed}`);
check(newClicked === 0, 'the Rigs row fell through to New conversation');

/* ── T2.1 the Save hint counts what Save would write ─────────────────────── */
// A fixture snapshot: the same pane fields the server sends (sessions.snapshot).
const SNAP = [
  { id: 'p1', seat: 'author', seatWithheld: null, state: 'idle' },
  { id: 'p2', seat: 'reviewer', seatWithheld: null, state: 'dead' },   // saved: on the roster
  { id: 'p3', seat: null, seatWithheld: null, state: 'busy' },
  { id: 'p4', seat: null, seatWithheld: 'author', state: 'idle' },     // withheld: not saved
];
const h1 = hintFns.rigSaveHint(SNAP);
check(h1.text === 'Saves 2 seated panes (@author @reviewer); 2 unseated are not saved',
      `hint reads ${JSON.stringify(h1.text)}`);
check(h1.disabled === false, 'Save disabled with two seated panes');
const h0 = hintFns.rigSaveHint(SNAP.filter(p => !p.seat));
check(h0.disabled === true, 'zero seated panes leaves Save enabled');
check(/no pane has a seat/.test(h0.text), `zero-seated reason reads ${JSON.stringify(h0.text)}`);
check(hintFns.rigSaveHint([SNAP[0]]).text === 'Saves 1 seated pane (@author)',
      `one seated pane reads ${JSON.stringify(hintFns.rigSaveHint([SNAP[0]]).text)}`);

// Wired: an open dialog shows the hint and the Save button follows it.
function hintNodes(open) {
  return { '#rigdlg': { open }, '#rig-savehint': mk('p'), '#rig-save': mk('button') };
}
function refreshHints(nodes, panes, rowsIn) {
  new Function('$', 'S', 'RIG_ROWS', 'rigSaveHint', 'rigLiveSeats', 'rigLiveText',
    `${fn('rigRefreshHints')} return rigRefreshHints;`)(
    s => nodes[s], { panes: new Map(panes.map(p => [p.id, p])) }, rowsIn || [],
    hintFns.rigSaveHint, hintFns.rigLiveSeats, hintFns.rigLiveText)();
}
const hn = hintNodes(true);
refreshHints(hn, SNAP.filter(p => !p.seat));
check(hn['#rig-save'].disabled === true, 'the dialog left Save enabled with nothing seated');
check(/no pane has a seat/.test(hn['#rig-savehint'].textContent), 'the dialog hides why Save is off');
refreshHints(hn, SNAP);
check(hn['#rig-save'].disabled === false, 'Save stayed off after a pane was seated');
check(hn['#rig-savehint'].textContent === h1.text, 'the dialog hint is not the computed hint');
const closedDlg = hintNodes(false);
refreshHints(closedDlg, []);
check(closedDlg['#rig-save'].disabled === false && closedDlg['#rig-savehint'].textContent === '',
      'a closed dialog was repainted');

/* ── T2.2 a live seat is marked; Up still POSTs ──────────────────────────── */
const t2Nodes = { '#rig-list': mk('div'), '#rig-error': mk('p'), '#rig-out': mk('div') };
const t2Posts = [];
const t2Api = async (path, body) => {
  if (body) { t2Posts.push([path, body]); return { outcomes: [], lines: [] }; }
  return { rigs: [{ name: 'pair', seats: ['author', 'reviewer'] },
                  { name: 'solo', seats: ['reviewer'] },
                  { name: 'paused', seats: ['coder'] }] };
};
const t2Up = upHarness(t2Api);
const t2 = listHarness(t2Nodes, t2Api,
  [...SNAP, { id: 'p5', seat: 'coder', seatWithheld: null, state: 'detached' }],
  (name, b) => t2Up.rigUp(name, b));
await t2.run();
const [pairRow, soloRow, pausedRow] = t2Nodes['#rig-list'].kids;
check(pairRow.kids[2].textContent === 'Up will refuse: @author is live',
      `live marker reads ${JSON.stringify(pairRow.kids[2].textContent)}`);
check(soloRow.kids[2].textContent === '', 'a dead holder was marked live');
check(pausedRow.kids[2].textContent === '', 'a detached (paused) holder was marked live');
check(!pairRow.kids[3].disabled, 'the client blocked Up on a live seat');
await pairRow.kids[3].onclick();
check(t2Posts.length === 1 && t2Posts[0][0] === '/api/session/rigs/up' && t2Posts[0][1].name === 'pair',
      `Up with a live seat posted ${JSON.stringify(t2Posts)}`);
// A pane state change re-marks the open dialog without rebuilding the list.
const before = t2Nodes['#rig-list'].kids;
const awake = SNAP.map(p => p.id === 'p2' ? { ...p, state: 'busy' } : p);
refreshHints(hintNodes(true), awake, t2.ctx.RIG_ROWS);
check(soloRow.kids[2].textContent === 'Up will refuse: @reviewer is live',
      `re-mark reads ${JSON.stringify(soloRow.kids[2].textContent)}`);
check(pairRow.kids[2].textContent === 'Up will refuse: @author @reviewer are live',
      `two live seats read ${JSON.stringify(pairRow.kids[2].textContent)}`);
check(t2Nodes['#rig-list'].kids === before, 'a state change rebuilt the rig list');
check(html.includes('id="rig-savehint"'), 'index.html lacks #rig-savehint');
check(/rigRefreshHints\(\)/.test(fn('render')), 'render() does not keep an open Rigs dialog current');

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('selftest_rigs: ok');
