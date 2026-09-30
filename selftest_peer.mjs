/* A message from another pane renders as what it is (DESIGN-5 S7).
 *
 *   T7.6   a `peer` event renders its OWN block: class `peer`, `from @author`
 *          visible, the body as text (a `<script>` stays characters), and a
 *          tooltip that says the sender label is not proof of origin (7.9);
 *          a `user` event still renders as the human bubble.
 *   T7.18  the event reducer sets `busy` on `peer` exactly as on `user` --
 *          otherwise the roster and tab title say "your turn" for the whole
 *          of a peer-driven turn while the server says busy.
 *
 * renderLog as a whole pulls in the entire transcript machinery; this runs
 * the REAL `case` blocks and the REAL reducer slice, cut from app.js by their
 * labels, against the smallest environment they touch.
 *
 * Run: node selftest_peer.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

/* ── a DOM that cannot parse ─────────────────────────────────────────────── */
class N {
  constructor(tag) { this.tag = tag; this.children = []; this.className = '';
                     this.title = ''; this._t = ''; this.dataset = {};
                     this.classList = { add: c => { this.className += ' ' + c; } }; }
  // Text is a CHILD, as in a real DOM: prepend() must land before it.
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

/* The case bodies, sliced by their labels in renderLog's switch. */
function caseBlock(label, next) {
  const a = src.indexOf(`      case '${label}':`);
  const b = src.indexOf(`      case '${next}':`, a + 1);
  if (a < 0 || b < 0) throw new Error(`case '${label}' is not where this test expects it`);
  return src.slice(a, b);
}
const PEER_CASES = caseBlock('peer', 'tool');         // peer + peer_result
const USER_CASE = caseBlock('user', 'peer');

function render(kind, d) {
  const log = new N('div');
  const run = new Function('el', 'log', 'd', 'e', 'term', 'flush', 'flushText', `
    switch (${JSON.stringify(kind)}) {
    ${USER_CASE}
    ${PEER_CASES}
    }`);
  run(el, log, d, { seq: 7 }, false, () => {}, () => {});
  return log;
}

/* ── T7.6 ─────────────────────────────────────────────────────────────── */
const hostile = '<script>alert(1)</script> and <b>bold</b>';
const log = render('peer', { from_seat: 'author', from_pane: 'aaaaaaaaaaaa',
                             to_seat: 'reviewer', hop: 1, text: hostile });
const block = log.children[0];
check(block && /\bmsg\b/.test(block.className) && /\bpeer\b/.test(block.className),
      `the peer block's class is "${block && block.className}"`);
check(!/\buser\b/.test(block.className), 'a peer message wears the human bubble');
check(block.textContent.includes('from @author'), 'the sender is not shown');
check(block.textContent.includes(hostile), 'the body was altered or parsed');
check([...block.walk()].every(n => n.tag !== 'script' && n.tag !== 'b'),
      'markup in the body became elements');
check(/Not proof of origin/.test(block.title),
      'the peer block does not carry the honest threat statement (section 7.9)');

const chained = render('peer', { from_seat: 'author', hop: 3, text: 'x' }).children[0];
check(chained.textContent.includes('hop 3'), 'a chained message does not show its hop');

const u = render('user', { text: 'typed by the human' }).children[0];
check(/\bmsg user\b/.test(u.className) && u.textContent === 'typed by the human',
      `the human bubble changed: "${u.className}" "${u.textContent}"`);

const via = render('user', { text: 'from a script', via: 'consult' }).children[0];
check(via.textContent.startsWith('via consult') && /\bvia\b/.test(via.className),
      'a script-sent turn does not say so (S5)');

const failed = render('peer_result', { turn: 't', delivered: false, reason: 'card-pending' });
check(failed.children[0] && failed.children[0].textContent.includes('card-pending'),
      'a withdrawn peer turn is silent');
check(render('peer_result', { turn: 't', delivered: true }).children.length === 0,
      'a successful peer_result printed a line');

// DESIGN-5 S11b: the reply queue's record, on both sides, as text.
const qTo = render('peer_queue', { status: 'queued', side: 'to', from_seat: 'reviewer' });
check(qTo.children[0] && qTo.children[0].textContent === 'message from @reviewer: queued until this turn ends',
      `the waiter's queued line reads "${qTo.children[0] && qTo.children[0].textContent}"`);
const qFrom = render('peer_queue', { status: 'expired', side: 'from', to_seat: 'author' });
check(qFrom.children[0] && qFrom.children[0].textContent === 'message to @author: expired',
      "the sender's expiry line is wrong");
const qDrop = render('peer_queue', { status: 'dropped', side: 'to', from_seat: 'r',
                                     reason: '<b>paused</b>' });
check(qDrop.children[0].textContent.endsWith('dropped — <b>paused</b>')
      && [...qDrop.walk()].every(n => n.tag !== 'b'), 'a drop reason was parsed as markup');
check(render('peer_queue', { status: 'delivered', side: 'to' }).children.length === 0,
      "the receiving side repeated a delivery its peer block already shows");
check(render('peer_queue', { status: 'delivered', side: 'from', to_seat: 'a' }).children[0]
      .textContent === 'message to @a: delivered', "the sender never learns it was delivered");

/* ── T7.18 the reducer ────────────────────────────────────────────────── */
const a = src.indexOf("    if (ev.kind === 'permission') { p.pending.push(d.requestId);");
const b = src.indexOf("    if (ev.kind === 'renamed')", a);
if (a < 0 || b < 0) throw new Error('the reducer is not where this test expects it');
const reduce = new Function('ev', 'p', 'd', 'refresh', 'toast', 'render',
                            src.slice(a, b));
const refreshes = [];
const pane = { state: 'ready', pending: [] };
reduce({ kind: 'peer' }, pane, { text: 'x' }, () => { refreshes.push(1); return Promise.resolve(); },
       () => {}, () => {});
check(pane.state === 'busy', `a peer event left the pane "${pane.state}", not busy`);
const human = { state: 'ready', pending: [] };
reduce({ kind: 'user' }, human, { text: 'x' }, () => Promise.resolve(), () => {}, () => {});
check(human.state === 'busy', 'the user path changed');
reduce({ kind: 'peer_result' }, pane, { delivered: false, reason: 'card-pending' },
       () => { refreshes.push(1); return Promise.resolve(); }, () => {}, () => {});
check(refreshes.length === 1, 'a withdrawn peer turn does not refetch the true state');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('OK — peer: its own block, text only, honest tooltip; busy on peer');
