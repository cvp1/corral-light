/* Module views in the browser (docs/finops-module-plan.md §4.5, §8.1): every
 * block type renders from a fixture, as TEXT; over-bound and dropped counts
 * are said; an unknown block is one line; markup-looking strings stay strings;
 * only https://host links become links; kind and level map to fixed classes;
 * the ⌘K row, the dialog's face and its Refresh (including a 429).
 *
 * Driven against the real functions in static/app.js with a capturing DOM:
 * innerHTML, outerHTML, insertAdjacentHTML, setAttribute and style all throw,
 * and every created tag is recorded, so a pass means nothing was parsed as
 * markup and no module string reached an attribute or a style. The shim's <a>
 * resolves `href` the way a browser does (through URL), so the href property
 * is what is checked. There is no real-browser harness in this repository; a
 * browser run of the same checks is still owed (plan §8.1).
 *
 * Run: node selftest_modules.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('./static/style.css', import.meta.url), 'utf8');

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

/* ── a capturing DOM ─────────────────────────────────────────────────── */
const TAGS = [];
const CLASSES = new Set();
function node(tag) {
  TAGS.push(tag);
  let cls = '';
  let href = '';
  const n = {
    tag, kids: [], textContent: '', rel: '', target: '', min: 0, max: 0, value: 0,
    disabled: false,
    appendChild(k) { this.kids.push(k); return k; },
    append(...ks) { this.kids.push(...ks); },
    replaceChildren(...ks) { this.kids = ks; },
    setAttribute() { throw new Error('setAttribute'); },
    insertAdjacentHTML() { throw new Error('insertAdjacentHTML'); },
  };
  Object.defineProperty(n, 'className', {
    get() { return cls; },
    set(v) { cls = v; for (const c of String(v).split(/\s+/)) if (c) CLASSES.add(c); } });
  for (const k of ['innerHTML', 'outerHTML', 'style']) {
    Object.defineProperty(n, k, { get() { throw new Error(k); }, set() { throw new Error(k); } });
  }
  if (tag === 'a') {
    // A browser's href property: the attribute resolved against the page.
    Object.defineProperty(n, 'href', {
      get() { return href; },
      set(v) { try { href = new URL(String(v), 'https://page.invalid/').href; } catch { href = String(v); } } });
  }
  return n;
}
const document = { createElement: node };
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c;
                          if (x !== undefined) n.textContent = x; return n; };
const all = (n, out = []) => { out.push(n); for (const k of n.kids || []) all(k, out); return out; };
const text = n => all(n).map(x => x.textContent).filter(Boolean);
const byClass = (n, c) => all(n).filter(x => x.className.split(/\s+/).includes(c));
const byTag = (n, t) => all(n).filter(x => x.tag === t);

const PURE = ['modText', 'modKindClass', 'modLevelClass', 'modClampPct', 'modSafeLink',
              'modDroppedLines', 'modBlock', 'renderModuleView', 'renderModuleSnapshot',
              'moduleFace', 'moduleWhen'];
const M = new Function('el', 'document',
  `${PURE.map(fn).join('\n')} return { ${PURE.join(', ')} };`)(el, document);

/* ── every block type renders from a fixture ─────────────────────────── */
const EVIL = ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', 'javascript:alert(1)'];
const snap = {
  schema: 'corral-light.module/1', ok: true, generated_at: '2026-10-07T18:00:00Z', error: null,
  progress: { phase: 'backfill', done_pct: 42, note: 'reading history' },
  view: [
    { type: 'tiles', items: [
      { label: 'Committed', value: '$120 / mo', kind: 'declared', note: 'from config',
        fresh_at: '2026-10-07T17:59:00Z' },
      { label: 'Codex weekly', value: '40% used', kind: 'vendor', level: 'ok' },
      { label: EVIL[0], value: EVIL[1], kind: 'evil" onclick="x', level: 'red; background:url(x)',
        note: EVIL[2] }],
      dropped: { items: 3 } },
    { type: 'meter', label: 'Codex 5 h window', pct: 37.4, kind: 'vendor', note: 'resets in 3 h' },
    { type: 'table', title: 'By lane', columns: ['Lane', 'Plan', 'Tokens'],
      rows: [['Claude', 'Max', '41.2 M'], [EVIL[0], EVIL[1], EVIL[2]]], dropped: { rows: 12 } },
    { type: 'note', text: 'Grok: credentials found; usage not reported on disk.' },
    { type: 'link', label: 'How these figures are made', url: 'https://example.com/how' },
    { type: 'unsupported', was: 'chart' },
    { type: 'iframe', src: 'https://example.com' },
  ],
  truncated: { blocks: 7 },
};
const out = M.renderModuleSnapshot(snap);
const words = text(out);
const has = s => words.some(w => w.includes(s));
check(byClass(out, 'modtile').length === 3, 'tiles: not one tile per item');
check(has('$120 / mo') && has('Codex weekly') && has('as of 2026-10-07T17:59:00Z'),
      'tiles: label, value or fresh_at missing');
check(has('3 more items not shown'), 'tiles: dropped items not said');
const meter = byTag(out, 'meter')[0];
check(meter && meter.value === 37.4 && meter.max === 100, 'meter: no <meter> at the pct');
check(has('37%'), 'meter: percent not shown as text');
check(byTag(out, 'th').length === 3 && byTag(out, 'td').length === 6, 'table: wrong th/td count');
check(has('12 more rows not shown'), 'table: dropped rows not said');
check(has('Grok: credentials found; usage not reported on disk.'), 'note: missing');
check(byTag(out, 'a').length === 1, 'link: the https link did not become exactly one <a>');
check(byClass(out, 'modunsupported').length === 2, 'unsupported and unknown types: not one line each');
check(byClass(out, 'modunsupported').every(n => n.kids.length === 0 && /^unsupported block/.test(n.textContent)),
      'an unsupported block is more than one line');
check(has('7 more blocks not shown'), 'snapshot truncation not said');
check(has('backfill · 42% · reading history'), 'progress not shown');
check(has('generated 2026-10-07T18:00:00Z'), 'generated_at not shown');

/* ── markup-looking text is text ─────────────────────────────────────── */
for (const t of ['script', 'img', 'iframe', 'style', 'object', 'embed']) {
  check(!TAGS.includes(t), `a <${t}> element was created`);
}
for (const e of EVIL) {
  check(all(out).some(n => n.textContent === e), `"${e}" did not render verbatim as text`);
}
check(byTag(out, 'a').every(a => a.textContent !== EVIL[2]), 'javascript: text became a link');

/* ── kind and level: fixed classes only ──────────────────────────────── */
const ALLOWED = new Set(['modview', 'modline', 'bad', 'warn', 'ok', 'modblock', 'modtiles',
  'modtile', 'modlabel', 'modvalue', 'modkind', 'modnote', 'modmeter', 'modbar', 'modpct',
  'modtable', 'modtitle', 'modlink', 'modunsupported', 'modbadge',
  'mk-billed', 'mk-vendor', 'mk-declared', 'mk-list', 'mk-estimate', 'mk-unknown',
  'ml-ok', 'ml-info', 'ml-warn', 'ml-bad']);
for (const c of CLASSES) check(ALLOWED.has(c), `class "${c}" is not a fixed class`);
const tiles = byClass(out, 'modtile');
check(tiles[0].className === 'modtile mk-declared ml-info', `declared tile: ${tiles[0].className}`);
check(tiles[1].className === 'modtile mk-vendor ml-ok', `vendor tile: ${tiles[1].className}`);
check(tiles[2].className === 'modtile mk-unknown ml-info', `evil tile: ${tiles[2].className}`);
check(byClass(tiles[2], 'modkind')[0].textContent === 'unknown', 'an unknown kind is not shown as unknown');
for (const k of ['billed', 'vendor', 'declared', 'list', 'estimate', 'unknown'])
  check(M.modKindClass(k)[0] === 'mk-' + k, `kind ${k}`);
for (const l of ['ok', 'info', 'warn', 'bad']) check(M.modLevelClass(l) === 'ml-' + l, `level ${l}`);
for (const x of ['constructor', '__proto__', 'toString', 'BILLED', '', null, undefined, 3, {}]) {
  check(M.modKindClass(x)[0] === 'mk-unknown', `kind ${String(x)} not unknown`);
  check(M.modLevelClass(x) === 'ml-info', `level ${String(x)} not info`);
}
for (const c of ALLOWED) {
  if (/^m[kl]-/.test(c)) check(css.includes('.' + c), `style.css has no rule for .${c}`);
}

/* ── links: only https with a host and no userinfo ───────────────────── */
const linkOf = url => {
  const n = M.modBlock({ type: 'link', label: 'L', url });
  return { a: byTag(n, 'a')[0], n };
};
{
  const { a } = linkOf('https://example.com');
  const u = a && new URL(a.href);
  check(a && u.protocol === 'https:' && u.host === 'example.com', 'https://example.com is not an https link');
  check(a && a.rel === 'noopener noreferrer' && a.target === '_blank', 'link lacks noopener noreferrer / _blank');
}
// `https:alert(1)` parses (WHATWG URL) to https://alert(1)/ with a host, so
// the literal "https://" is required: it is shown as text, never a link.
check(new URL('https:alert(1)').host === 'alert(1)', 'URL parsing of https:alert(1) changed; revisit');
for (const u of ['javascript:alert(1)', 'https:alert(1)', 'http://x', 'https://u:p@x',
                 'https://u@x', 'https://@x', 'https:/x', 'https:\\\\x', ' https://x',
                 'https://ex ample.com', 'https://x\n.com', 'HTTP://x', 'data:text/html,hi',
                 '//example.com', '/relative', '', null, 42, 'https://']) {
  const { a, n } = linkOf(u);
  check(!a, `${JSON.stringify(u)} became a link`);
  if (typeof u === 'string' && u) {
    check(text(n).some(t => t.includes(u)), `${JSON.stringify(u)} not shown as plain text`);
  }
}
check(M.modSafeLink('HTTPS://Example.COM/a') === 'https://example.com/a', 'scheme case not accepted');

/* ── bounds: the client cuts again and says so ───────────────────────── */
const many = M.renderModuleView(Array.from({ length: 60 }, (_, i) => ({ type: 'note', text: 'n' + i })));
check(byClass(many, 'modblock').length === 50, 'more than 50 blocks rendered');
check(text(many).includes('10 more blocks not shown'), '60 blocks: the 10 dropped not said');
const big = M.modBlock({ type: 'tiles', items: Array.from({ length: 30 }, () => ({ label: 'x' })),
                         dropped: { items: 2 } });
check(byClass(big, 'modtile').length === 24, 'more than 24 tiles rendered');
check(text(big).includes('8 more items not shown'), 'tiles: client and server drops not summed');
const wide = M.modBlock({ type: 'table', columns: Array.from({ length: 15 }, (_, i) => 'c' + i),
                          rows: Array.from({ length: 205 }, () => ['a']) });
check(byTag(wide, 'th').length === 12, 'more than 12 columns rendered');
check(byTag(wide, 'tr').length === 201, 'more than 200 rows rendered');
check(text(wide).includes('5 more rows not shown') && text(wide).includes('3 more columns not shown'),
      'table: client cut not said');
const long = M.modBlock({ type: 'note', text: 'y'.repeat(900) });
check(text(long)[0].length === 501, 'a note is not capped at 500 characters');
for (const [p, want] of [[150, 100], [-5, 0], [NaN, 0], [Infinity, 0], ['50', 0], [null, 0], [12.5, 12.5]])
  check(M.modClampPct(p) === want, `pct ${p} -> ${M.modClampPct(p)}, want ${want}`);
check(M.modText({ toString() { return '<b>' } }, 10) === '', 'an object is rendered as text');
check(M.renderModuleView('not a list').kids.length === 0, 'a non-list view renders blocks');
check(M.modBlock(null).textContent === 'unsupported block', 'a null block is not one line');

/* ── errors, empty, face, when ───────────────────────────────────────── */
const failed = M.renderModuleSnapshot({ ok: false, error: 'collector exited 1', view: [] });
check(byClass(failed, 'bad').some(n => n.textContent === 'error: collector exited 1'), 'error not shown');
check(text(M.renderModuleSnapshot(null)).some(t => /No snapshot yet/.test(t)), 'no snapshot not said');
const face = m => M.moduleFace(m).map(n => n.textContent);
check(face({ enabled: true, sandboxed: false, state: 'ok' }).includes('unsandboxed'), 'unsandboxed badge missing');
check(!face({ enabled: true, sandboxed: true, state: 'ok' }).includes('unsandboxed'), 'sandboxed shows unsandboxed');
check(face({ enabled: false, sandboxed: true, state: 'disabled' }).includes('disabled'), 'disabled not shown');
check(face({ enabled: true, sandboxed: true, state: 'failing', error: 'timed out' }).includes('failing: timed out'),
      'failing: <error> not shown');
check(M.moduleWhen(null) === 'never run' && /^last run /.test(M.moduleWhen('2026-10-07T18:00:00Z')),
      'last run time not shown');

/* ── ⌘K: one row per enabled module; an old hub is quiet ─────────────── */
const PAL = { seq: 0, modules: [] };
const loadModules = new Function('PAL', 'api', 'modText', `async ${fn('loadModules')} return loadModules;`);
await loadModules(PAL, async () => ({ modules: [
  { name: 'finops', title: 'FinOps', summary: 'what the lanes cost', enabled: true },
  { name: 'off', title: 'Off', enabled: false },
  { name: 42, title: 'bad name', enabled: true }] }), M.modText)();
check(PAL.modules.length === 1 && PAL.modules[0].title === 'FinOps', 'enabled modules not listed alone');
const run = new Function('PAL', 'attachTarget', 'S', 'renderPalette', 'setTimeout', 'clearTimeout', 'api',
  `${fn('paletteResults')} return paletteResults;`);
const rowsFor = q => { let shown = [];
  run(PAL, () => null, { panes: new Map(), archived: [] }, r => { shown = r; }, () => 0, () => {},
      async () => ({ hits: [] }))(q); return shown.filter(r => r.kind === 'module'); };
check(rowsFor('').length === 1 && rowsFor('fin').length === 1 && rowsFor('cost').length === 1,
      '⌘K has no FinOps row');
check(rowsFor('zzqx').length === 0, '⌘K shows a module for a needle it does not match');
check(/row\.kind === 'module'\) return openModule\(row\.name\)/.test(src), 'the ⌘K row does not open the dialog');
const gone = { modules: [{ name: 'x', title: 'x', summary: '' }] };
let threw = false;
try { await loadModules(gone, async () => { const e = new Error('404'); e.status = 404; throw e; }, M.modText)(); }
catch { threw = true; }
check(!threw && gone.modules.length === 0, 'a hub without /api/modules is not quiet and empty');

/* ── the dialog: its ids, Refresh, a 429 ─────────────────────────────── */
for (const id of ['moddlg', 'mod-title', 'mod-face', 'mod-when', 'mod-view', 'mod-error', 'mod-refresh'])
  check(html.includes(`id="${id}"`), `index.html lacks #${id}`);
function refreshHarness(apiImpl) {
  const nodes = { '#mod-refresh': node('button'), '#mod-error': node('p'), '#mod-when': node('p') };
  const seen = { posted: [], later: [], nodes };
  seen.go = new Function('$', 'api', 'MOD', 'renderModule', 'setTimeout', 'clearTimeout',
    `async ${fn('refreshModule')} return refreshModule;`)(
    s => nodes[s], async (p, b) => { seen.posted.push([p, b]); return apiImpl(); },
    { open: 'finops', timer: null }, async () => {}, f => { seen.later.push(f); return 1; }, () => {});
  return seen;
}
const ok = refreshHarness(async () => ({ queued: true }));
await ok.go();
check(ok.posted.length === 1 && ok.posted[0][0] === '/api/module/finops/refresh' && ok.posted[0][1],
      `refresh posted ${JSON.stringify(ok.posted)}`);
check(ok.later.length === 2, 'refresh does not schedule a re-fetch');
const busy = refreshHarness(async () => { const e = new Error('a run is already queued'); e.status = 429; throw e; });
await busy.go();
check(busy.nodes['#mod-error'].textContent === 'a run is already queued', '429 not shown');
check(busy.later.length === 1, 'a 429 schedules a re-fetch');

if (bad) { console.error(`selftest_modules: ${bad} failure(s)`); process.exit(1); }
console.log('OK — module views: every block as text, links only https://host, fixed classes, bounds said, ⌘K row, Refresh and 429');
