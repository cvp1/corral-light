/* Daily-driver ergonomics (T-DD-*): code-block Copy, the context control,
 * chat drafts and history, desktop focus mode and Alt+N jumps.
 *
 * Driven against the real functions in static/app.js, extracted by name.
 * Run: node selftest_daily.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
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
  const n = { tag, className: cls || '', kids: [], _text: text ?? '', type: '', title: '',
              onclick: null, setAttribute() {},
              append(...k) { this.kids.push(...k); }, appendChild(k) { this.kids.push(k); return k; } };
  Object.defineProperty(n, 'textContent', { get() { return this._text; }, set(v) { this._text = v; } });
  return n;
}

/* ── T-DD-1: code blocks ─────────────────────────────────────────────── */
const codeLang = load('codeLang');
eq(codeLang('```python'), 'python', 'T-DD-1 the fence names the language');
eq(codeLang('``` Bash title="x"'), 'bash', 'T-DD-1 only the first word, lower-cased');
eq(codeLang('```'), '', 'T-DD-1 a bare fence has no label');
eq(codeLang('```<img src=x>'), '', 'T-DD-1 markup is never a label');
{
  const codeBlock = load('codeBlock', [], { el: mk, copyExact: async () => true });
  const w = codeBlock('a <b>\nc', 'js');
  eq(w.className, 'codeblk', 'T-DD-1 a block is wrapped');
  const bar = w.kids.find(k => k.className === 'codebar');
  const pre = w.kids.find(k => k.tag === 'pre');
  check(bar && bar.kids.some(k => k.className === 'codecopy' && k.textContent === 'Copy'),
        'T-DD-1 every block has a Copy button');
  check(bar.kids.some(k => k.className === 'codelang' && k.textContent === 'js'),
        'T-DD-1 the language shows as a label');
  eq(pre.kids[0].textContent, 'a <b>\nc', 'T-DD-1 the code is text, byte for byte');
  const noLang = codeBlock('x', '');
  check(!noLang.kids[0].kids.some(k => k.className === 'codelang'), 'T-DD-1 no label without a language');
}
check(/box\.append\(codeBlock\(code\.join\('\\n'\), lang\)\)/.test(src),
      'T-DD-1 md() draws every fence through codeBlock');
check(!/function codeBlock[\s\S]{0,900}innerHTML/.test(fn('codeBlock')),
      'T-DD-1 codeBlock never parses HTML');
check(/window\.isSecureContext/.test(fn('copyExact')) && /execCommand\('copy'\)/.test(fn('copyExact')),
      'T-DD-1 copy works on plain-http LAN (textarea fallback)');
check(/@media \(hover:none\)\{\.codecopy/.test(css), 'T-DD-1 touch screens always see Copy');

/* ── T-DD-2: the context control ─────────────────────────────────────── */
const isTerm = p => (p.agent || '').startsWith('host:');
const ctxActions = load('ctxActions', [], { isTerm });
const kinds = p => ctxActions(p).map(a => a.kind);
eq(kinds({ agent: 'claude', state: 'ready', commands: [{ name: 'compact' }] }), ['compact', 'carry'],
   'T-DD-2 an agent that advertises compact offers it, and Carry');
eq(kinds({ agent: 'codex', state: 'ready', commands: [{ name: 'review' }] }), ['carry'],
   'T-DD-2 no compact is assumed for a lane that does not advertise one');
eq(kinds({ agent: 'codex', state: 'ready' }), ['carry'], 'T-DD-2 no commands list: Carry only');
eq(ctxActions({ agent: 'claude', state: 'busy', commands: [{ name: 'Compact' }] })[0],
   { kind: 'compact', text: '/Compact', label: 'Compact after this turn' },
   'T-DD-2 on a busy pane compact says it queues, and sends the name as advertised');
eq(ctxActions({ agent: 'claude', state: 'ready', commands: [{ name: 'compact' }] })[0].label,
   'Compact now', 'T-DD-2 on an idle pane it is now');
eq(kinds({ agent: 'claude', state: 'dead', commands: [{ name: 'compact' }] }), [],
   'T-DD-2 a dead pane offers nothing');
eq(kinds({ agent: 'claude', state: 'detached' }), [], 'T-DD-2 a detached pane offers nothing');
eq(kinds({ agent: 'host:box1', state: 'ready' }), [], 'T-DD-2 a terminal pane offers nothing');
{
  const ctxSummary = load('ctxSummary', ['ctxPct'], { });
  const sum = ctxSummary({ usage: { used: 124000, size: 200000 } });
  check(/^62% context · 124,000 of 200,000 tokens$/.test(sum), `T-DD-2 the head reads the numbers: ${sum}`);
  eq(ctxSummary({ usage: {} }), 'context use not reported by this lane',
     'T-DD-2 unknown says unknown, never 0%');
}
const head = fn('paneHead');
check((head.match(/c\.onclick = \(\) => openCtxMenu\(p, c\)/g) || []).length === 2,
      'T-DD-2 the ctx fact is a control whether known or unknown');
check(/el\('button', 'ctx' \+/.test(head) && /el\('button', 'ctx unk'/.test(head),
      'T-DD-2 ctx is still always drawn (known and unknown)');

/* ── T-DD-3: drafts and history ──────────────────────────────────────── */
const draftsWith = load('draftsWith', [], { DRAFT_MAX: 10 });
eq(draftsWith({ a: 'old', gone: 'x' }, 'a', 'new', ['a', 'b']), { a: 'new' },
   'T-DD-3 saving replaces, and panes that are gone are pruned');
eq(draftsWith({ a: 'x', b: 'y' }, 'a', '   ', ['a', 'b']), { b: 'y' },
   'T-DD-3 an empty draft is deleted');
eq(draftsWith({}, 'a', '0123456789ABC', ['a']), { a: '0123456789' }, 'T-DD-3 a draft is capped');
eq(draftsWith(null, 'a', 'x', ['a']), { a: 'x' }, 'T-DD-3 no stored map is fine');
{
  const chatHistory = load('chatHistory', [], {
    WAIT_SKIP_VIAS: ['peer', 'rig', 'challenge', 'panel'], CHAT_HIST_MAX: 3 });
  const u = (text, via) => ({ kind: 'user', data: { text, via } });
  eq(chatHistory([u('one'), { kind: 'text', data: { text: 'reply' } }, u('two'), u('two'),
                  u('from a peer', 'peer'), u('panel q', 'panel'), u('  '), u('three', 'wall')]),
     ['one', 'two', 'three'], 'T-DD-3 your own turns, oldest first, repeats collapsed, peers/panels out');
  eq(chatHistory([u('a'), u('b'), u('c'), u('d')]), ['b', 'c', 'd'], 'T-DD-3 history is bounded');
  eq(chatHistory(undefined), [], 'T-DD-3 no events, no history');
}
const comp = fn('composer');
check(/ta\.value = draftsLoad\(\)\[p\.id\] \|\| '';/.test(comp), 'T-DD-3 the composer restores the draft');
check(/ta\.oninput = \(\) => \{[\s\S]{0,80}draftSave\(p\.id, ta\.value\)/.test(comp),
      'T-DD-3 typing saves the draft');
check(/text: t \}\);\s*ta\.value = ''; ta\.style\.height = 'auto';\s*walk\.ix = null; draftSave\(p\.id, ''\);/.test(comp),
      'T-DD-3 the draft clears only after the server accepts the send');
check(/ta\.value === '' && e\.key === 'ArrowUp'/.test(comp),
      'T-DD-3 ↑ starts a walk only from an empty box');

/* ── T-DD-4: focus mode and Alt+N ────────────────────────────────────── */
const widePane = load('widePane'), nthPane = load('nthPane'), altDigit = load('altDigit');
const ps = [{ id: 'a' }, { id: 'b', minimized: true }, { id: 'c' }];
eq(widePane(ps, 'a', false), 'a', 'T-DD-4 a wide screen can show one pane alone');
eq(widePane(ps, 'a', true), null, 'T-DD-4 a phone uses its own solo view instead');
eq(widePane(ps, 'gone', false), null, 'T-DD-4 a closed pane ends focus mode');
eq(widePane(ps, null, false), null, 'T-DD-4 off by default');
eq(nthPane(ps, 2).id, 'c', 'T-DD-4 Alt+N counts the panes on the wall, not minimized ones');
eq(nthPane(ps, 3), null, 'T-DD-4 past the end is nothing');
eq(nthPane(ps, 0), null, 'T-DD-4 zero is nothing');
eq(altDigit({ altKey: true, code: 'Digit3', key: '£' }), 3, 'T-DD-4 Option+3 on a Mac (e.key is £)');
eq(altDigit({ altKey: true, metaKey: true, code: 'Digit3' }), null, 'T-DD-4 ⌘⌥3 is not ours');
eq(altDigit({ altKey: true, shiftKey: true, code: 'Digit3' }), null, 'T-DD-4 ⇧⌥3 is not ours');
eq(altDigit({ altKey: false, code: 'Digit3' }), null, 'T-DD-4 a bare digit is not ours');
eq(altDigit({ altKey: true, code: 'Digit0' }), null, 'T-DD-4 0 is not a pane');
check(/const solo = soloPane\(panes, S\.solo, narrow\) \|\| S\.wide;/.test(src),
      'T-DD-4 focus mode draws through the solo path (client-side, nothing minimized)');
check(!/api\(/.test(fn('toggleWide')) && !/api\(/.test(fn('widePane')) && !/api\(/.test(fn('jumpToPane')),
      'T-DD-4 focus mode and jumps make no API call');
check(/S\.solo \|\| S\.wide\)\);/.test(src), 'T-DD-4 seen-on-screen counts the focused pane');

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('OK — daily driver: code-block Copy, the context control, drafts and history, focus mode and Alt+N');
