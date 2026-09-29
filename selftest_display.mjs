/* Does the browser's display projection say the same thing the core does,
 * and does the tab title say it where the eye lands first?
 *
 * WHY THIS FILE EXISTS
 *   `display_state()` lives in corral_core/sessions.py. The browser cannot
 *   call it: it reduces events locally between polls, so at the moment a
 *   permission card arrives the server's `display` key is one poll stale --
 *   exactly when it matters most. So app.js MIRRORS the rule, and a mirror
 *   nobody pins is a fork waiting to happen.
 *
 *   The pin is ONE case table, ./corral_core/display_cases.json,
 *   asserted here against the JavaScript and in corral_core/
 *   test_display_state.py against the Python. Add a case there and both
 *   languages have to answer it. Full Corral runs its own copy of this file
 *   against its own app.js, reading the same table across the sibling.
 *
 *   `gateHold` never arrives on this product -- there is no runbook gate here
 *   -- and the table exercises it anyway: one rule, two skins, no branch that
 *   only one of them has ever run.
 *
 *   The title is the other half. `document.title` was never set, so Corral as
 *   one tab among twenty said nothing about whether an agent was blocked on a
 *   human -- the one fact the whole wall exists to surface.
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

const { displayState, setTitle, DISPLAY_LABEL, IDLE_DISPLAY_S } = new Function(`
  ${constant('IDLE_DISPLAY_S')}
  ${fn('displayState')}
  ${constant('DISPLAY_LABEL')}
  ${fn('setTitle')}
  return { displayState, setTitle, DISPLAY_LABEL, IDLE_DISPLAY_S };
`)();

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
});

for (const c of CASES.cases) {
  const got = displayState(onWire(c.pane), c.unread);
  check(got === c.want,
        `${c.why}: ${JSON.stringify(c.pane)} unread=${c.unread} -> ${got}, `
      + `wanted ${c.want} (the python mirror of this case passes; app.js does not)`);
}
check(CASES.cases.length >= 15, 'the shared case table shrank');

/* Every state the projection can return must have a word for it — a missing
 * label renders the raw slug and looks like a bug. */
for (const s of ['needs-you', 'working', 'your-turn', 'idle', 'dead'])
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

/* ── the same tick a permission arrives ────────────────────────────────── */
/* The reducer mutates the pane in place and then calls render(), and render()
 * is where setTitle lives. So: mutate the way the reducer does, re-render,
 * and the title must have moved already — not on the next 2-second poll. */
const live = [pane('busy'), pane('ready'), pane('ready')];
setTitle(live);
check(document.title === '2 your turn · Corral',
      `before the card: "${document.title}"`);
live[0].pending.push('req-1');            // ev.kind === 'permission'
live[0].state = 'needs-you';              // ...both lines, exactly as at 2615
setTitle(live);
check(document.title === '1 need you · Corral',
      `the card landed and the title still said "${document.title}" — a `
    + `waiting agent that the tab does not announce is the whole defect`);

/* And the wiring that makes the tick closed, rather than a comment claiming
 * it is: render() must call setTitle, and the event reducer must call
 * render(). Either one missing and every case above is theatre. */
check(/setTitle\(panes\)/.test(fn('render')),
      'render() does not call setTitle(panes) — the title would only move on '
    + 'a full refresh, if at all');
const reducer = src.slice(src.indexOf("if (ev.kind === 'permission')"));
check(/^[\s\S]{0,4000}?\brender\(\);/.test(reducer),
      'the event reducer no longer ends in render() — a permission event '
    + 'would not repaint the title in the tick it arrived');

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
check(/sub\.title = p\.state;/.test(src),
      'the roster row dropped the raw-state tooltip — the projection collapses '
    + 'six enum values into five words, and the record has to stay reachable');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log(`OK — display projection: ${CASES.cases.length} shared cases, `
          + `title, and the same-tick repaint`);
