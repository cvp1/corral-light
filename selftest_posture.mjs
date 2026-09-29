/* Does the UI say only what is TRUE about a lane's permissions?
 *
 * Three defects, one theme — a control or a label claiming a safety property
 * nothing established (DESIGN-5 S2):
 *
 *   1. The New dialog disables the Permissions <select> on a lane that cannot
 *      enforce a posture, but the close handler read `.value` anyway, so a
 *      leftover `strict` from the previously chosen lane was POSTED and stored
 *      on the pane. Blanking is not enough on its own — the key has to be
 *      absent from the body.
 *   2. Every id app.js writes into must exist in the shipped index.html. Full
 *      Corral shipped a note written into `#pt-postnote`, an element that was
 *      never there, and its mini-DOM test did not catch it because a mini-DOM
 *      INVENTS every id it is asked for. So this reads the real index.html.
 *   3. `agent-set` was one pill for three different promises: the vendor
 *      decides, OUR adapter asks and fails closed, or the lane has no tools at
 *      all. The most constrained lane and the least constrained lane wore the
 *      same badge.
 *
 * Run: node selftest_posture.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const HERE = new URL('./', import.meta.url);
const src = readFileSync(new URL('static/app.js', HERE), 'utf8');
const html = readFileSync(new URL('static/index.html', HERE), 'utf8');

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

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

/* Corral Light has no port dialog with a posture control (its port picker is
 * `#p-agent` and carries none), so the missing-#pt-postnote defect is full
 * Corral's alone. What IS shared is the pill and the New dialog, below.
 *
 * The general property behind that defect still applies here: an id app.js
 * writes into must exist in the shipped index.html, not only in a mini-DOM.
 * Checked over the New dialog's own controls. */
for (const id of new Set([...src.matchAll(/\$\('#(f-[\w-]+|posturehint)'\)/g)]
                           .map(m => m[1])))
  check(new RegExp(`id="${id}"`).test(html),
        `app.js reads $('#${id}') and index.html has no such element`);

/* ── 3. the pill says which promise this lane actually makes ────────────── */
const el = (tag, cls, text) => ({ tag, className: cls, textContent: text ?? '',
                                  title: '' });
const posturePill = new Function('el', `${fn('posturePill')} return posturePill;`)(el);

const cases = [
  { why: 'our own adapter asks and fails closed',
    pane: { label: 'Local (harness → .21)', agent: 'local', rail: true },
    text: 'harness rail', titleHas: 'fails closed' },
  { why: 'rail wins over a missing tools flag',
    pane: { label: 'Fireworks (open models)', agent: 'fireworks', rail: true },
    text: 'harness rail' },
  { why: 'the vendor decides, and the pill names the vendor',
    pane: { label: 'Grok', agent: 'grok' },
    text: 'Grok policy', titleHas: 'cannot set the permission policy' },
  { why: 'a multi-word label is cut to its first word',
    pane: { label: 'Claude Code', agent: 'claude' }, text: 'Claude policy' },
  { why: 'a parenthesised label is cut at the paren',
    pane: { label: 'Antigravity (Gemini)', agent: 'gemini' },
    text: 'Antigravity policy' },
  { why: 'no tools means nothing to ask about — not a broken rail',
    pane: { label: 'Local (Ollama) — chat only', agent: 'ollama', tools: false },
    text: 'chat only', titleHas: "lane's nature" },
  { why: 'a lane with tools but no rail is still vendor policy',
    pane: { label: 'Codex', agent: 'codex', tools: true }, text: 'Codex policy' },
];

for (const c of cases) {
  const pill = posturePill(c.pane);
  check(pill.textContent === c.text,
        `${c.why}: got "${pill.textContent}", wanted "${c.text}"`);
  if (c.titleHas)
    check(pill.title.includes(c.titleHas),
          `${c.why}: the tooltip does not say "${c.titleHas}" — ${pill.title}`);
  check(pill.title.length > 40,
        `${c.why}: a pill this terse MUST carry the full sentence on hover`);
}

/* The old single label is gone. Left in place it would keep flattening the
 * three promises for whichever branch still used it. */
check(!/'agent-set'/.test(src),
      "app.js still renders the literal 'agent-set' — three different promises "
    + 'under one word is the defect S2 removes');

/* ── 1. the submitted body carries no unearned posture ──────────────────── */
/* The close handler is not extractable as a named function (it is an inline
 * listener over the whole dialog), so this asserts the two properties that
 * make it correct, on the real source: the value is taken only when the
 * control is live, and the key is set only when there is a value. */
/* The New dialog's own close handler: app.js has four, so anchor on the one
 * that follows fillPosture and stop at its schedule branch. */
const closeFrom = src.indexOf("dlg.addEventListener('close'",
                              src.indexOf('const fillPosture'));
const closeBody = src.slice(closeFrom, src.indexOf("const when = $('#f-when').value;",
                                                   closeFrom) + 40);
check(closeFrom > 0 && closeBody.length > 200,
      'the New dialog close handler is not where this test expects it');
check(/const posture = \$\('#f-posture'\)\.disabled \? '' : \$\('#f-posture'\)\.value;/
        .test(closeBody),
      'the close handler reads #f-posture unconditionally again — a disabled '
    + 'control still has a value, and that value was the previous lane’s');
check(/if \(posture\) common\.posture = posture;/.test(closeBody),
      'the posture key is written into the body unconditionally — absent and '
    + '"" resolve the same at the hub, but only absence is honest about the '
    + 'operator never having chosen');
check(/if \(posture\) localStorage\.setItem\('corral\.posture', posture\)/
        .test(closeBody),
      'the blanked value is written back to localStorage — opening the dialog '
    + 'on a Grok lane would forget a `strict` set on a lane where it means '
    + 'something');
check(!/posture: \$\('#f-posture'\)\.value/.test(closeBody),
      'the body still inlines the raw control value');

/* And the control is BLANKED, not merely greyed — the property the key
 * omission depends on. */
const fpFrom = src.indexOf('const fillPosture');
const fillPostureBody = src.slice(fpFrom, src.indexOf("$('#f-agent').onchange", fpFrom));
check(fpFrom > 0 && fillPostureBody.length > 100 && fillPostureBody.length < 4000,
      `fillPosture is not where this test expects it (${fillPostureBody.length} chars)`);
check(/sel\.disabled = true;[\s\S]{0,800}?sel\.value = '';/.test(fillPostureBody),
      'fillPosture greys the control without blanking it — a disabled <select> '
    + 'keeps the previous lane’s value, which is what got posted');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log(`OK — posture honesty: ${cases.length} pill cases, every dialog id `
          + `exists, and the New dialog posts no unearned posture`);
