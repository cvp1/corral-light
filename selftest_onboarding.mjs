/* The first ten minutes: an empty room that says what to do, and a shortcut
 * list that cannot lie.
 *
 *   An empty roster renders the empty state; one pane removes it.
 *   The `?` overlay lists EVERY binding the key handler's table holds, `?`
 *   toggles it, and Esc inside it closes the overlay only -- it must never
 *   reach a pending permission card and refuse it.
 *
 * The key handler dispatches FROM `KEYS` and the overlay is built from `KEYS`,
 * so this drives both against the same array.
 *
 * Run: node selftest_onboarding.mjs   (exit 0 = pass)
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

/* ── the smallest DOM these touch ───────────────────────────────────────── */
class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.className = ''; this.id = '';
    this.type = ''; this.title = ''; this.open = false; this.onclick = null;
    this._text = '';
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach(x => this.classList._s.add(x)),
      remove: (...c) => c.forEach(x => this.classList._s.delete(x)),
      toggle: (c, on) => on ? this.classList._s.add(c) : this.classList._s.delete(c),
      contains: c => this.classList._s.has(c),
    };
  }
  get tagName() { return (this.tag || '').toUpperCase(); }
  set textContent(v) { this._text = v; this.children = []; }
  get textContent() {
    return this._text + this.children.map(c => c.textContent).join('');
  }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.children.push(c)); }
  replaceChildren(...cs) { this.children = [...cs]; this._text = ''; }
  setAttribute(k, v) { this[k] = v; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { if (this.onclick) this.onclick(); }
  set innerHTML(v) { throw new Error('app.js assigned innerHTML here'); }
  *walk() { yield this; for (const c of this.children) if (c.walk) yield* c.walk(); }
}

const NODES = {};
const node = id => (NODES[id] = Object.assign(new Node('div'), { id }));
['#keysdlg', '#keys-body', '#new', '#palette', '#keysbtn'].forEach(node);
const $ = sel => NODES[sel] || null;
const el = (tag, cls, text) => {
  const n = new Node(tag);
  if (cls) { n.className = cls; cls.split(' ').forEach(c => n.classList.add(c)); }
  if (text !== undefined && text !== null) n._text = text;
  return n;
};
globalThis.document = { createElement: t => new Node(t), addEventListener() {} };

/* ── the table, the overlay and the dispatcher, from the real source ────── */
const KEYS_SRC = /^const KEYS = \[[\s\S]*?^\];$/m.exec(src);
if (!KEYS_SRC) throw new Error('no `const KEYS = [...]` in app.js');

const openPalette = () => { NODES['#palette'].open = true; };
const M = new Function('$', 'el', 'openPalette', `
  ${KEYS_SRC[0]}
  ${fn('isTypingTarget')}
  ${fn('toggleKeys')}
  return { KEYS, toggleKeys, isTypingTarget };
`)($, el, openPalette);

/* Every entry is describable — an overlay row with no words is a row that
 * teaches nothing. */
for (const k of M.KEYS) {
  check(typeof k.combo === 'string' && k.combo.length,
        `a KEYS entry has no combo: ${JSON.stringify(k)}`);
  check(typeof k.what === 'string' && k.what.length > 3,
        `the binding "${k.combo}" has no description`);
}

/* The overlay lists EVERY entry, and the table is what it reads. */
M.toggleKeys(true);
check(NODES['#keysdlg'].open, '? did not open the overlay');
const rows = NODES['#keys-body'].children;
check(rows.length === M.KEYS.length,
      `the overlay shows ${rows.length} rows for ${M.KEYS.length} bindings — `
    + `a list that omits a binding is a list nobody can trust`);
const shown = rows.map(r => r.textContent);
for (const k of M.KEYS) {
  const hit = shown.find(t => t.includes(k.combo) && t.includes(k.what));
  check(!!hit, `the overlay does not list "${k.combo} — ${k.what}"`);
  if (k.alt)
    check(shown.some(t => t.includes(k.alt)),
          `the overlay omits the ${k.combo} alternative ${k.alt} — a Linux `
        + `reader is told only about a Mac key`);
  if (k.where)
    check(shown.some(t => t.includes(k.where)),
          `the overlay does not say WHERE "${k.combo}" applies — a global-`
        + `looking key that only works in the composer is a bug report`);
}

/* `?` toggles. */
M.toggleKeys();
check(!NODES['#keysdlg'].open, 'a second ? did not close the overlay');

/* Dispatch comes from the table, not from a parallel if-chain. */
const wire = src.slice(src.indexOf("document.addEventListener('keydown'"),
                       src.indexOf('const KEYS = ['));
check(/for \(const k of KEYS\)/.test(wire),
      'the global key handler no longer walks KEYS — the overlay and the '
    + 'bindings can drift again');
check(!/e\.key\.toLowerCase\(\) === 'k'/.test(wire),
      'the handler still open-codes a binding beside the table');

const press = (init) => {
  const e = { preventDefault() {}, metaKey: false, ctrlKey: false,
              altKey: false, shiftKey: false, target: null, ...init };
  for (const k of M.KEYS) if (k.match && k.match(e)) { k.run(); return k; }
  return null;
};

check(press({ key: '?' }) !== null, 'pressing ? matched no binding');
check(NODES['#keysdlg'].open, 'pressing ? did not open the overlay');

/* Esc closes the overlay, and ONLY while the overlay is open.
 * Esc on a pane refuses a pending permission card and interrupts a running
 * turn. A global Esc that swallowed the key would turn the help screen into
 * a way to deny a tool call, so the binding is conditional on `dlg.open` and
 * that condition is what is asserted here — both directions. */
const hit = press({ key: 'Escape' });
check(hit && hit.combo === 'Esc', 'Esc inside the overlay matched no binding');
check(!NODES['#keysdlg'].open, 'Esc did not close the overlay');
check(press({ key: 'Escape' }) === null,
      'Esc is swallowed globally even with the overlay closed — that is the '
    + 'key that refuses a permission card and interrupts a turn');

/* ? must not fire while someone is typing a question mark. */
NODES['#keysdlg'].open = false;
for (const tag of ['INPUT', 'TEXTAREA', 'SELECT']) {
  const t = new Node(tag.toLowerCase());
  check(press({ key: '?', target: t }) === null,
        `? opened the overlay while typing in a <${tag.toLowerCase()}>`);
}
check(press({ key: '?', target: Object.assign(new Node('div'),
                                              { isContentEditable: true }) }) === null,
      '? opened the overlay inside a contenteditable');

/* ⌘K and Ctrl+K both reach the palette. */
NODES['#palette'].open = false;
check(press({ key: 'k', metaKey: true }) !== null && NODES['#palette'].open,
      'Cmd+K no longer opens the palette');
NODES['#palette'].open = false;
check(press({ key: 'K', ctrlKey: true }) !== null && NODES['#palette'].open,
      'Ctrl+K no longer opens the palette (uppercase K — shift or caps lock)');

/* The dialog and the button the overlay needs exist in the shipped HTML: a
 * mini-DOM invents whatever it is asked for. */
for (const id of ['keysdlg', 'keys-body', 'keysbtn'])
  check(new RegExp(`id="${id}"`).test(html),
        `index.html has no #${id} — the overlay would silently never open`);
check(/<div id="keys-body"[^>]*>\s*<\/div>/.test(html),
      'index.html hand-writes shortcut rows — they must come from KEYS, or '
    + 'the two lists drift');

/* ── the empty state ─────────────────────────────────────────────────────── */
/* Asserted on the real source: the block lives inside render(), which pulls
 * in the whole pane-reconciliation machinery. */
const renderSrc = fn('render');
const emptyFrom = renderSrc.indexOf('if (!shown.length)');
/* Bounded at the rail block that follows, so the innerHTML check does not
 * fire on the rail's code. */
const emptyBlock = renderSrc.slice(emptyFrom, renderSrc.indexOf('// THE rail.',
                                                                emptyFrom));
check(emptyFrom > 0 && emptyBlock.length > 300 && emptyBlock.length < 3000,
      `the empty-state block is not where this test expects it (${emptyBlock.length} chars)`);
check(/el\('h2', null, 'Nothing running\.'\)/.test(emptyBlock),
      'the first-run empty state lost its heading');
check(/\$\('#new'\)\.click\(\)/.test(emptyBlock),
      'the empty state has no button that starts a conversation — the one '
    + 'thing a first-run screen must offer');
check(/'⌘K'/.test(emptyBlock) && /'\?'/.test(emptyBlock),
      'the empty state no longer names ⌘K and ? — the two ways around');
check(/S\.dataDir/.test(emptyBlock),
      'the empty state does not say where transcripts live — "is this going '
    + 'to someone\'s cloud?" is the question an empty room answers badly');
check(!/innerHTML/.test(emptyBlock),
      'the empty state builds markup with innerHTML');
check(/All minimized\./.test(emptyBlock),
      'the all-minimized case was lost — it is NOT the first-run case and '
    + 'must not offer first-run advice to someone with work running');
check(/dataDir/.test(src.slice(src.indexOf('S.defaultCwd = d.defaultCwd'),
                               src.indexOf('S.defaultCwd = d.defaultCwd') + 200)),
      'dataDir is never read off the wire, so the empty state can only ever '
    + 'show the fallback sentence');

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log(`OK — onboarding: ${M.KEYS.length} bindings listed from one table, `
          + `? toggles, Esc is local, and the empty state says what to do`);
