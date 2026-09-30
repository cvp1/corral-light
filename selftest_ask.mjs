/* ask_human renders as what it is: the agent asking its human.
 *
 *   the banner   an open question renders a banner with the asker and the
 *                full text, as TEXT (markup stays characters); no question,
 *                no banner
 *   the roster   the row carries a one-line `asks: …` preview, cut at
 *                ASK_PREVIEW_CHARS with the full text on the tooltip
 *   transcript   a `question` event is its own block -- never the human
 *                bubble, never a system line; `question_cleared` says why
 *   reducer      `question` opens it, `question_cleared` closes it, `user`
 *                records its via (null for a typed turn), `peer` records
 *                `peer` -- so the display mirror sees what the core sees
 *   wiring       updatePane fills the banner slot, the roster row appends
 *                the preview (a deleted call site with the function left in
 *                place would pass every check above)
 *
 * The real functions and case blocks are cut from app.js and run against a
 * DOM that cannot parse (the selftest_peer.mjs pattern).
 *
 * Run: node selftest_ask.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

/* ── a DOM that cannot parse ─────────────────────────────────────────────── */
class N {
  constructor(tag) { this.tag = tag; this.children = []; this.className = '';
                     this.title = ''; this.dataset = {};
                     this.classList = { add: c => { this.className += ' ' + c; } }; }
  set textContent(v) { this.children = [String(v)]; }
  get textContent() { return this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  prepend(c) { this.children.unshift(c); }
  set innerHTML(v) { throw new Error('assigned innerHTML'); }
  *walk() { yield this; for (const c of this.children) if (c instanceof N) yield* c.walk(); }
}
const el = (tag, cls, text) => {
  const n = new N(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
};

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
function constant(name) {
  const m = new RegExp(`^const ${name} = [\\s\\S]*?;`, 'm').exec(src);
  if (!m) throw new Error(`no const ${name} in app.js`);
  return m[0];
}

const Q = new Function('el', `
  ${constant('ASK_PREVIEW_CHARS')}
  ${fn('questionBanner')}
  ${fn('askLine')}
  return { questionBanner, askLine, ASK_PREVIEW_CHARS };
`)(el);

/* ── the banner ───────────────────────────────────────────────────────── */
const hostile = 'Re-scope? <script>alert(1)</script> or <b>keep going</b>';
const b = Q.questionBanner({ seat: 'author', question: { text: hostile } });
check(b && /\bqbanner\b/.test(b.className), `no banner: ${b && b.className}`);
check(b.textContent.includes('@author asks you'), `the asker is not named: "${b.textContent}"`);
check(b.textContent.includes(hostile), 'the question text was altered or parsed');
check([...b.walk()].every(n => n.tag !== 'script' && n.tag !== 'b'),
      'markup in the question became elements');
check(!/\buser\b/.test(b.className), 'the banner wears the human bubble');
const anon = Q.questionBanner({ seat: null, question: { text: 'ok?' } });
check(anon.textContent.includes('the agent asks you'), 'an unseated pane has no asker');
check(Q.questionBanner({ question: null }) === null, 'a banner with no question');
check(Q.questionBanner({}) === null, 'a banner on a pane that never asked');

/* ── the roster line ──────────────────────────────────────────────────── */
const long = 'word '.repeat(60).trim();
const line = Q.askLine({ question: { text: long } });
check(line && /\bask\b/.test(line.className), 'no roster line');
check(line.textContent.startsWith('asks: '), `roster line reads "${line.textContent}"`);
check(line.textContent.length <= 'asks: '.length + Q.ASK_PREVIEW_CHARS,
      `the roster line is ${line.textContent.length} chars`);
check(line.textContent.endsWith('…'), 'a cut preview does not say it was cut');
check(line.title === long, 'the full question is not on the tooltip');
const multi = Q.askLine({ question: { text: 'line one\n\nline two' } });
check(multi.textContent === 'asks: line one line two', `newlines survive: "${multi.textContent}"`);
check(Q.askLine({ question: null }) === null, 'a roster line with no question');

/* ── the transcript block ─────────────────────────────────────────────── */
function caseBlock(label, next) {
  const a = src.indexOf(`      case '${label}':`);
  const z = src.indexOf(`      case '${next}':`, a + 1);
  if (a < 0 || z < 0) throw new Error(`case '${label}' is not where this test expects it`);
  return src.slice(a, z);
}
const Q_CASES = caseBlock('question', 'tool');
function render(kind, d) {
  const log = new N('div');
  new Function('el', 'log', 'd', 'flush', `switch (${JSON.stringify(kind)}) { ${Q_CASES} }`)(
    el, log, d, () => {});
  return log;
}
const blk = render('question', { text: hostile, turn: 't1' }).children[0];
check(blk && /\bmsg question\b/.test(blk.className), `transcript block: ${blk && blk.className}`);
check(!/\buser\b/.test(blk.className) && !/\bsys\b/.test(blk.className),
      'the question renders as the human or as a system line (P20)');
check(blk.textContent.includes('the agent asks you') && blk.textContent.includes(hostile),
      `the question block reads "${blk.textContent}"`);
check([...blk.walk()].every(n => n.tag !== 'script'), 'markup parsed in the transcript');
const rep = render('question', { text: 'b', replaces: '2026-09-30T00:00:00Z' }).children[0];
check(rep.textContent.includes('replacing its earlier question'), 'a replacing question does not say so');
check(render('question_cleared', { reason: 'answered' }).children[0].textContent === 'question answered',
      'an answered question is not closed in the transcript');
check(render('question_cleared', { reason: 'dead' }).children[0].textContent.includes('dead'),
      'a question closed by death does not say why');

/* ── the reducer ──────────────────────────────────────────────────────── */
const a = src.indexOf("    if (ev.kind === 'permission') { p.pending.push(d.requestId);");
const z = src.indexOf("    if (ev.kind === 'renamed')", a);
if (a < 0 || z < 0) throw new Error('the reducer is not where this test expects it');
const reduce = new Function('ev', 'p', 'd', 'refresh', 'toast', 'render', src.slice(a, z));
const nop = () => Promise.resolve();
const p = { state: 'busy', pending: [], question: null, turnVia: null };
reduce({ kind: 'question' }, p, { text: 'which?', at: 'T', turn: 't1' }, nop, nop, nop);
check(p.question && p.question.text === 'which?', 'a question event did not open it');
reduce({ kind: 'user' }, p, { text: 'this one', via: 'rig' }, nop, nop, nop);
check(p.turnVia === 'rig', `user via rig recorded as ${p.turnVia}`);
reduce({ kind: 'user' }, p, { text: 'typed' }, nop, nop, nop);
check(p.turnVia === null, `a typed turn recorded via ${p.turnVia}`);
reduce({ kind: 'question_cleared' }, p, { reason: 'answered' }, nop, nop, nop);
check(p.question === null, 'question_cleared did not close it');
reduce({ kind: 'peer' }, p, { text: 'x' }, nop, nop, nop);
check(p.turnVia === 'peer' && p.state === 'busy', 'a peer turn is not recorded as peer');

/* ── the wiring ───────────────────────────────────────────────────────── */
check(/const qb = questionBanner\(p\);[\s\S]{0,120}rec\.ask\.replaceChildren/.test(fn('updatePane')),
      'updatePane no longer fills the question banner slot');
check(/root\.append\(head, ask, log, comp\)/.test(fn('buildPane')),
      'buildPane no longer places the banner slot between header and transcript');
check(/const ask = askLine\(p\);\s*\/\/[^\n]*\n\s*if \(ask\) t\.appendChild\(ask\);/.test(src),
      'the roster row no longer shows the question preview');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('OK — ask_human: banner, roster preview, transcript block, reducer');
