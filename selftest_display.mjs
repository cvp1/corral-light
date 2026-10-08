/* The browser's display projection matches the core's (one shared case
 * table, ./corral_core/display_cases.json, also asserted against the Python),
 * and the tab title surfaces it.
 *
 * Run: node selftest_display.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const CASES = JSON.parse(readFileSync(
  new URL('./corral_core/display_cases.json', import.meta.url), 'utf8'));

function fn(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`no function ${name} in app.js`);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}

/* A const declaration, from its name to the line that closes it. Taken from
 * the file rather than restated here: a constant this test defines itself
 * would pass while app.js used a different number. */
function constant(name) {
  const m = new RegExp(`^const ${name} = [\\s\\S]*?;$`, 'm').exec(src);
  if (!m) throw new Error(`no const ${name} in app.js`);
  return m[0];
}

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

/* ── the projection itself ─────────────────────────────────────────────── */

const titles = [];
globalThis.document = { get title() { return titles[titles.length - 1]; },
                        set title(v) { titles.push(v); } };

/* `S` and `render` are what displayTick reaches for; a counting stub stands in
 * for render so the tick's "only when something changed" is observable. */
const S = { panes: new Map() };
let renders = 0;
const D = new Function('S', 'renderHook', `
  ${constant('IDLE_DISPLAY_S')}
  ${constant('DISPLAY_TICK_MS')}
  ${fn('paneAge')}
  ${constant('AGENT_ORIGIN_VIAS')}
  ${fn('displayState')}
  ${constant('DISPLAY_LABEL')}
  let displaySig = '';
  ${fn('displaySignature')}
  ${fn('setTitle')}
  function render() {
    const panes = [...S.panes.values()];
    setTitle(panes);
    displaySig = displaySignature(panes);
    renderHook();
  }
  ${fn('displayTick')}
  return { displayState, setTitle, DISPLAY_LABEL, IDLE_DISPLAY_S,
           DISPLAY_TICK_MS, paneAge, displayTick, render };
`)(S, () => { renders++; });
const { displayState, setTitle, DISPLAY_LABEL, IDLE_DISPLAY_S } = D;

check(IDLE_DISPLAY_S === 1800,
      `app.js IDLE_DISPLAY_S is ${IDLE_DISPLAY_S}, the core says 1800 — the `
    + `two thresholds must be the same number or the roster and the TUI `
    + `disagree about the same pane`);

/* The wire names differ from the python attribute names (`idleS` vs `idle_s`,
 * `gateHold` vs `_gate_hold`) because one is a JSON payload and the other is
 * an object. This is the ONLY place that mapping lives. */
const onWire = (pane) => ({
  state: pane.state,
  pending: pane.pending || [],
  gateHold: !!pane.gate_hold,
  idleS: pane.idle_s || 0,
  question: pane.question || null,
  turnVia: pane.turn_via === undefined ? null : pane.turn_via,
});

/* Projection parity: every case in the table the python answers, the
 * browser answers identically; including a raw `needs-you` with empty pending
 * and no gate hold, and `detached` distinct from a quiet `ready`. */
const reached = new Set();
for (const c of CASES.cases) {
  const got = displayState(onWire(c.pane));
  reached.add(got);
  check(got === c.want,
        `${c.why}: ${JSON.stringify(c.pane)} -> ${got}, `
      + `wanted ${c.want} (the python mirror of this case passes; app.js does not)`);
}
check(CASES.cases.length >= 15, 'the shared case table shrank');
check(displayState({ state: 'detached', idleS: 99999 })
        !== displayState({ state: 'ready', idleS: 99999 }),
      'a detached pane and a quiet ready pane read the same — a human must '
    + 'resume one of them, and nothing about the other needs anybody');

/* Every state the projection can return must have a word for it — a missing
 * label renders the raw slug and looks like a bug. */
const ALL = ['needs-you', 'working', 'your-turn', 'idle', 'paused', 'dead'];
for (const st of ALL)
  check(reached.has(st), `no case in the shared table produces "${st}" in JS`);
for (const s of ALL)
  check(typeof DISPLAY_LABEL[s] === 'string' && DISPLAY_LABEL[s].length > 0,
        `DISPLAY_LABEL has no word for the display state "${s}"`);

/* ── the tab title ─────────────────────────────────────────────────────── */

const pane = (state, extra = {}) =>
  ({ state, pending: [], idleS: 0, ...extra });

setTitle([pane('ready'), pane('ready'), pane('ready'),
          pane('needs-you'), pane('ready', { pending: ['r1'] })]);
check(document.title === '2 need you · Corral',
      `2 needs-you and 3 your-turn gave "${document.title}"`);

setTitle([pane('ready'), pane('ready'), pane('ready')]);
check(document.title === '3 your turn · Corral',
      `0 needs-you and 3 your-turn gave "${document.title}"`);

setTitle([pane('busy'), pane('ready', { idleS: 99999 }), pane('detached'),
          pane('dead')]);
check(document.title === 'Corral',
      `nothing waiting must say just "Corral", got "${document.title}"`);

setTitle([]);
check(document.title === 'Corral',
      `an empty wall gave "${document.title}"`);

/* What needs you OUTRANKS what is merely waiting: a single blocked agent must
 * not be buried under a count of finished ones. */
setTitle([pane('needs-you'), pane('ready'), pane('ready'), pane('ready')]);
check(document.title === '1 need you · Corral',
      `one blocked pane among three answered ones gave "${document.title}"`);

/* ask_human: an open question counts as needing you, and a turn another
 * pane's agent started does NOT count as your turn. */
setTitle([pane('ready', { question: { text: 're-scope?' } }),
          pane('ready', { turnVia: 'peer' }), pane('ready', { turnVia: 'rig' })]);
check(document.title === '1 need you · Corral',
      `a question and two agent-started turn ends gave "${document.title}"`);
setTitle([pane('ready', { turnVia: 'peer' }), pane('ready', { turnVia: null })]);
check(document.title === '1 your turn · Corral',
      `a peer-started turn end counted as your turn: "${document.title}"`);

/* ── the same tick a permission arrives ────────────────────────────────── */
/* The reducer mutates the pane in place and then calls render(), and render()
 * is where setTitle lives. So: mutate the way the reducer does, re-render,
 * and the title must have moved already — not on the next 2-second poll. */
const live = [pane('busy'), pane('ready'), pane('ready')];
setTitle(live);
check(document.title === '2 your turn · Corral',
      `before the card: "${document.title}"`);
live[0].pending.push('req-1');            // ev.kind === 'permission'
live[0].state = 'needs-you';              // ...both lines, as the reducer does
setTitle(live);
check(document.title === '1 need you · Corral',
      `the card landed and the title still said "${document.title}" — a `
    + `waiting agent that the tab does not announce is the whole defect`);

/* And the wiring that makes the tick closed, rather than a comment claiming
 * it is: render() must call setTitle, the event reducer must end by
 * scheduling a render, and that schedule must reach render() (within one
 * animation frame; renders coalesce so a burst of events paints once).
 * Any one missing and every case above is theatre. */
check(/setTitle\(panes\)/.test(fn('render')),
      'render() does not call setTitle(panes) — the title would only move on '
    + 'a full refresh, if at all');
const reducer = src.slice(src.indexOf("if (ev.kind === 'permission')"));
check(/^[\s\S]{0,4000}?\bscheduleRender\(\);/.test(reducer),
      'the event reducer no longer ends in scheduleRender() — a permission '
    + 'event would not repaint the title in the frame it arrived');
check(/\brender\(\);/.test(fn('scheduleRender')),
      'scheduleRender() never calls render() — the reducer\'s schedule would '
    + 'paint nothing');
check(/requestAnimationFrame\(run\)/.test(fn('scheduleRender')),
      'scheduleRender() does not coalesce on requestAnimationFrame — a '
    + 'streamed answer would paint the wall once per chunk again');

/* ── the three surfaces render the projection, not the raw enum ────────── */
/* Structural, and deliberately shallow: the real proof is the Playwright pass
 * in LIVE.md. This is the tripwire for someone deleting a call site while
 * leaving the function in place. */
for (const [site, needle] of [
  ['the roster row', /const disp = displayState\(p\);/],
  ['the minimized chip', /const cdisp = displayState\(p\);/],
  ['the pane header', /const dsp = displayState\(p\);/],
])
  check(needle.test(src), `${site} no longer reads the display projection`);
check(/it\.title = `[^`]*state: \$\{p\.state\}`;/.test(src),
      'the roster row dropped the raw-state tooltip — the projection collapses '
    + 'six enum values into five words, and the record has to stay reachable');

/* ── the idle clock ──────────────────────────────────────────────────────── */
/* A pane goes quiet and NOTHING else happens: no SSE event, no refresh. The
 * title must still stop claiming it is your turn, on the browser's own tick. */
check(D.DISPLAY_TICK_MS === 60000,
      `the display tick is ${D.DISPLAY_TICK_MS} ms — the design says a minute`);
const t0 = 1_000_000_000_000;
const quiet = { id: 'q', state: 'ready', pending: [], idleS: 1741, _idleAt: t0 };
check(displayState(quiet, t0) === 'your-turn', 'a 29-minute-quiet pane is not your-turn');
check(D.paneAge(quiet, t0 + 60_000) === 1801,
      `paneAge did not advance on the browser clock: ${D.paneAge(quiet, t0 + 60_000)}`);
check(displayState(quiet, t0 + 60_000) === 'idle',
      'one tick later (1801 s, nothing unread) the pane is still your-turn');

/* The tick renders when — and only when — a state changed. */
const realNow = Date.now;
try {
  S.panes = new Map([['q', { ...quiet }]]);
  Date.now = () => t0;
  D.render(); renders = 0;
  D.displayTick();
  check(renders === 0, 'the tick re-rendered with nothing changed — a full '
      + 'roster rebuild every minute for no reason');
  Date.now = () => t0 + 60_000;
  D.displayTick();
  check(renders === 1, 'the tick did not render when a pane aged into idle — '
      + 'the title keeps saying "your turn" until some other pane speaks');
  check(document.title === 'Corral',
        `after the tick the title still reads "${document.title}"`);
} finally { Date.now = realNow; }

/* The two stamps that make the clock right: a snapshot records WHEN its idleS
 * was true, and a live event restarts the pane's age. Without the first, age
 * never advances; without the second, a pane that just answered is aged from
 * a snapshot taken before it answered. */
check(/np\._idleAt = rxAt;/.test(fn('refresh')),
      'refresh() no longer stamps when each pane\'s idleS arrived');
check(/p\.idleS = 0; p\._idleAt = Date\.now\(\);/.test(src),
      'the event reducer no longer restarts a pane\'s age when it emits');
check(/setInterval\(displayTick, DISPLAY_TICK_MS\)/.test(src),
      'nothing schedules displayTick — the clock exists and never runs');

/* No read receipt in the projection: the hub has no source for one, and this
 * browser keeps none either. */
check(!/displayState\(p, unread\)|function displayState\(p, unread/.test(src),
      'displayState takes an `unread` again — a guess with the face of a fact');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log(`OK — display projection: ${CASES.cases.length} shared cases, `
          + `title, the same-tick repaint, and the idle clock`);
