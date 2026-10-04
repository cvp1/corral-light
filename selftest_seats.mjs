/* A seat is visible on the pane and findable from ⌘K.
 *
 * The header shows `@reviewer`; a withheld seat is shown AS withheld, never
 * as if it were an address; an unseated pane offers a way to name it. ⌘K
 * finds a pane by its seat, with or without the @.
 *
 * Driven against the real functions in static/app.js, with the palette's
 * renderer replaced by a capture -- the matching is what is under test.
 *
 * Run: node selftest_seats.mjs   (exit 0 = pass)
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

const el = (tag, cls, text) => ({ tag, className: cls || '', textContent: text ?? '',
                                  title: '', type: '', onclick: null });

/* ── the header pill ─────────────────────────────────────────────────────── */
let opened = null;
const seatPill = new Function('el', 'openSeat', `${fn('seatPill')} return seatPill;`)(
  el, p => { opened = p; });

let b = seatPill({ id: 'p1', seat: 'reviewer' });
check(b.textContent === '@reviewer', `a seated pane's pill reads "${b.textContent}"`);
check(/\bseat\b/.test(b.className) && !/withheld/.test(b.className),
      `seated pill class: ${b.className}`);
b.onclick(); check(opened && opened.id === 'p1', 'clicking the pill does not open the seat dialog');

b = seatPill({ id: 'p2', seat: null, seatWithheld: 'reviewer' });
check(b.textContent === '@reviewer withheld',
      `a withheld seat reads "${b.textContent}" — it must never look like an address`);
check(/withheld/.test(b.className), 'the withheld pill is not styled as withheld');
check(/there first/.test(b.title), 'the withheld pill does not say why');

b = seatPill({ id: 'p3' });
check(b.textContent === '＠' && /seatadd/.test(b.className),
      'an unseated pane offers no way to name it');

/* The header actually carries it. */
check(/seatPill\(p\)/.test(fn('paneHead')),
      'paneHead no longer renders the seat pill');

/* ── ⌘K ──────────────────────────────────────────────────────────────────── */
const S = { panes: new Map([
  ['a1', { title: 'Code review', label: 'Claude Code', seat: 'reviewer', cwd: '/x/repo',
           agent: 'claude', state: 'ready' }],
  ['a2', { title: 'Draft', label: 'Claude Code', seat: null, cwd: '/x/other',
           agent: 'claude', state: 'ready' }],
]), archived: [] };
let rendered = [];
const PAL = { sel: 0, rows: [], t: null, seq: 0 };
const run = new Function(
  'S', 'PAL', 'renderPalette', 'api', '$', 'attachTarget', 'ROOMS_STATIC',
  'PAL_ACTIONS', 'paletteTranscripts', 'CORPUS_LABEL', 'setTimeout', 'clearTimeout', `
  ${(/^const SEARCH_PREFIX = .*$/m.exec(src) || [''])[0]}
  ${src.includes('function paletteNeedle(') ? fn('paletteNeedle') : ''}
  ${fn('paletteResults')}
  return paletteResults;`)(
  S, PAL, rows => { rendered = rows; }, async () => ({ hits: [] }), () => ({ value: '' }),
  () => null, [], [], () => {}, {}, () => 0, () => {});

for (const q of ['revi', '@revi', 'REVIEWER']) {
  run(q);
  const panes = rendered.filter(r => r.kind === 'pane');
  check(panes.length === 1 && panes[0].paneId === 'a1',
        `⌘K "${q}" found ${JSON.stringify(panes.map(r => r.paneId))}, wanted the @reviewer pane`);
  check(panes[0] && panes[0].label.includes('@reviewer'),
        `⌘K "${q}": the row does not show the seat — ${panes[0] && panes[0].label}`);
}
run('draft');
check(rendered.filter(r => r.kind === 'pane').map(r => r.paneId).join() === 'a2',
      'matching by title broke');

/* ── the dialog exists in the SHIPPED page, with the server's grammar ────── */
for (const id of ['seatdlg', 'seat-name', 'seat-for', 'seat-unbind'])
  check(new RegExp(`id="${id}"`).test(html), `index.html has no #${id}`);
const pat = /id="seat-name"[\s\S]*?pattern="([^"]+)"/.exec(html);
check(pat && pat[1].replace(/\\\\/g, '\\') === '[a-z][a-z0-9\\-]{0,31}',
      `the input's pattern is not the server's grammar: ${pat && pat[1]}`);
check(/value="unbind"[^>]*formnovalidate|formnovalidate[^>]*value="unbind"/.test(html),
      'Remove would be blocked by the pattern check on an old, now-invalid name');
check(/wireSeat\(\);/.test(src), 'nothing wires the seat dialog');
/* Enter must BIND: the form's default button is its first submit button,
 * Cancel, so without this handler Enter in the name box cancels. */
const ws = fn('wireSeat');
check(/e\.key !== 'Enter'/.test(ws) && /reportValidity\(\)\) dlg\.close\('ok'\)/.test(ws),
      'Enter in the seat name box no longer binds (it would hit Cancel, the default button)');
check(/if \(ev\.kind === 'seat'\)/.test(src),
      'another tab binding a seat never reaches this one (no reducer case)');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('OK — seats: the pill, withheld shown as withheld, ⌘K by seat, the dialog');
