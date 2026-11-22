/* Daily-driver ergonomics (T-DD-*): code-block Copy, the context control,
 * chat drafts and history, desktop focus mode and Alt+N jumps.
 *
 * Driven against the real functions in static/app.js, extracted by name.
 * Run: node selftest_daily.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('./static/style.css', import.meta.url), 'utf8');
const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');

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

/* ── T-DD-5: the phone's bottom bar, drawer and real viewport height ─── */
check(/name="viewport" content="[^"]*viewport-fit=cover/.test(html),
      'T-DD-5 viewport-fit=cover, so safe-area insets are real');
for (const id of ['mobile-panes', 'mobile-pane', 'mobile-new', 'mobile-search', 'mobile-needs'])
  check(html.includes(`id="${id}"`), `T-DD-5 the phone bar has #${id}`);
{
  const phone = css.slice(css.indexOf('The phone\'s controls live in a bottom bar'));
  check(/\.mobile-actions\{display:flex;position:fixed;left:0;right:0;bottom:0;top:auto;/.test(phone),
        'T-DD-5 the bar sits at the bottom, in thumb reach');
  check(/env\(safe-area-inset-bottom/.test(phone), 'T-DD-5 the bar clears the home indicator');
  check(/\.mobile-actions button,\.mobile-actions select\{height:44px/.test(phone),
        'T-DD-5 44px touch targets');
  check(/\.rail\.right\.shut\{display:none\}/.test(phone),
        'T-DD-5 the 34px strip is gone on a phone (the bar holds the count)');
}
// Every 100vh height is followed by its dvh twin: the keyboard must not hide the composer.
{
  const found = [...css.matchAll(/(?<![\w-])(?:min-|max-)?height:[^;{}]*\b100vh\b[^;{}]*/g)];
  const decls = found.map(m => m[0]);
  // In place: the declaration right after each one is its dvh twin.
  const twins = found.filter(m => css.startsWith(';' + m[0].replace('100vh', '100dvh'),
                                                 m.index + m[0].length));
  eq(twins.length, decls.length, 'T-DD-5 every 100vh height has a 100dvh twin');
  check(decls.length >= 8, `T-DD-5 the vh heights were found (${decls.length})`);
}
{
  const sd = fn('setDrawer');
  check(!/api\(/.test(sd) && !/localStorage/.test(sd),
        'T-DD-5 the drawer is view-only: no API call, no saved preference');
  check(/isNarrow\(\)/.test(sd), 'T-DD-5 the drawer only opens on a phone');
  check(/S\.applyLeft/.test(sd), 'T-DD-5 closing hands the rail its own folded state back');
  check(/S\.solo = id; setDrawer\(false\);/.test(fn('focusPane')),
        'T-DD-5 opening a pane from the drawer closes it');
  check(/if \(mn\) \{ mn\.textContent = tab\.textContent; mn\.className = tab\.className; \}/.test(fn('railFold')),
        'T-DD-5 the bar shows the same Needs-you count as the rail');
}

/* ── T-DD-6: alerts on this device (Web Push) ────────────────────────── */
{
  const pushSupport = load('pushSupport');
  const nav = { serviceWorker: {} };
  eq(pushSupport({ isSecureContext: false, navigator: nav, PushManager: 1, Notification: 1 }), 'none',
     'T-DD-6 plain http (the LAN wall): no bell at all');
  eq(pushSupport({ isSecureContext: true, navigator: {}, PushManager: 1, Notification: 1 }), 'none',
     'T-DD-6 no service worker: no bell');
  eq(pushSupport({ isSecureContext: true, navigator: nav }), 'install',
     'T-DD-6 iPhone Safari before Add to Home Screen: the bell explains');
  eq(pushSupport({ isSecureContext: true, navigator: nav, PushManager: 1, Notification: 1 }), 'ok',
     'T-DD-6 secure, worker and PushManager: alerts can be turned on');
  const b64urlBytes = load('b64urlBytes', [], { atob: s => Buffer.from(s, 'base64').toString('binary') });
  const key = Buffer.from(Array.from({ length: 65 }, (_, i) => (i * 37 + 4) % 256));
  const enc = key.toString('base64url');
  eq([...b64urlBytes(enc)], [...key], 'T-DD-6 the server key decodes byte for byte (base64url, no padding)');
  const linkedPane = load('linkedPane');
  const panes = new Map([['p1', {}]]);
  eq(linkedPane('?pane=p1', panes), 'p1', 'T-DD-6 a tapped notice opens its pane');
  eq(linkedPane('?pane=gone', panes), null, 'T-DD-6 a closed pane is ignored');
  eq(linkedPane('', panes), null, 'T-DD-6 no link, nothing opened');
  const on = fn('pushOn');
  check(on.indexOf('Notification.requestPermission') < on.indexOf("api('/api/push/key')"),
        'T-DD-6 permission is asked first, straight from the tap (iOS requires it)');
  check(/userVisibleOnly: true/.test(on), 'T-DD-6 every push shows something');
  const sw = readFileSync(new URL('./static/sw.js', import.meta.url), 'utf8');
  check(/fetch\('\/api\/push\/latest', \{ credentials: 'same-origin'/.test(sw),
        'T-DD-6 the worker asks the hub over its own origin, not the push service');
  check(/catch \{ return FALLBACK; \}/.test(sw) && /if \(!r\.ok\) return FALLBACK;/.test(sw),
        'T-DD-6 an unreachable hub still shows a notice (a silent push costs the subscription)');
  check(/new URL\(w\.url\)\.origin !== self\.location\.origin/.test(sw),
        'T-DD-6 a tap only ever focuses this origin');
  check(!/innerHTML/.test(sw), 'T-DD-6 the worker parses no HTML');
}

// T-DD-7 carry picks the exact model, defaulting to what the source runs.
{
  const cd = load('carryDefaults');
  const sp = { id: 's', agent: 'claude', model: 'opus', effort: 'high', state: 'ready' };
  eq(cd(sp, 'claude', [sp], null), { model: 'opus', effort: 'high', from: 'this pane' },
     'T-DD-7 same lane keeps the source pane\'s model (not the adapter default)');
  const detached = { id: 'd', agent: 'claude', model: null, wantModel: 'opus', state: 'detached' };
  eq(cd(detached, 'claude', [], null).model, 'opus',
     'T-DD-7 a detached source still carries the model it asked for');
  eq(cd(sp, 'codex', [sp], { agent: 'codex', model: 'gpt-6.2-astra', effort: 'high' }),
     { model: 'gpt-6.2-astra', effort: 'high', from: '⚡ preset' },
     'T-DD-7 another lane takes the ⚡ preset for that lane');
  const other = { id: 'o', agent: 'codex', model: 'gpt-6.2-sol', title: 'Build', state: 'ready' };
  const dead = { id: 'x', agent: 'codex', model: 'gpt-5.6-luna', state: 'dead' };
  eq(cd(sp, 'codex', [other, dead], null).model, 'gpt-6.2-sol',
     'T-DD-7 else the newest live pane on that lane, never a dead one');
  eq(cd(sp, 'grok', [sp], null), { model: '', effort: '', from: '' },
     'T-DD-7 else the agent decides, and the dialog says nothing came from anywhere');

  const opt = () => { const o = mk('option'); return o; };
  const mkSel = () => {
    const n = { kids: [], value: '', disabled: false, closest: () => ({ style: {} }),
                replaceChildren() { this.kids = []; }, appendChild(k) { this.kids.push(k); return k; } };
    return n;
  };
  const elx = (tag, cls, text) => { const o = mk(tag, cls, text); o.value = ''; return o; };
  const fill = load('fillCarryPick', [], { el: elx });
  const m = mkSel();
  fill(m, { options: [{ value: 'default', name: 'default' }, { value: 'opus', name: 'opus' },
                      { value: 'fable', name: 'fable' }], value: 'fable' }, 'opus');
  eq(m.value, 'opus', 'T-DD-7 the picker lands on the wanted model, not the catalog\'s current value');
  eq(m.kids.map(k => k.value), ['default', 'opus', 'fable'],
     'T-DD-7 a list with its own default gets no second one');
  const m2 = mkSel();
  fill(m2, { options: [{ value: 'opus', name: 'opus' }] }, 'claude-opus-5-5');
  eq([m2.value, m2.kids.map(k => k.value)], ['claude-opus-5-5', ['', 'opus', 'claude-opus-5-5']],
     'T-DD-7 a live model the catalog has not listed still shows and is sent');
  const m3 = mkSel();
  const hint = fill(m3, { value: 'grok-4.7', options: [] }, '');
  check(m3.disabled && /grok-4\.7/.test(hint), 'T-DD-7 a fixed model is named, not offered');
  check(/id="p-model"/.test(html) && /id="p-effort"/.test(html), 'T-DD-7 the carry dialog has both pickers');
  check(/api\('\/api\/session\/port', \{[^}]*model: \$\('#p-model'\)\.value/.test(src),
        'T-DD-7 Send posts the picked model');
  void opt;
}

// T-DD-8 the roster's now-line, human durations, and a quiet tag on uncertain.
{
  const fe = load('fmtElapsed');
  eq([fe(0), fe(59), fe(60), fe(1955), fe(3599), fe(3600), fe(3900), fe(-4)],
     ['0s', '59s', '1m', '32m', '59m', '1h 00m', '1h 05m', '0s'],
     'T-DD-8 32m, not 1955s');
  const wl = load('workingLabel', ['fmtElapsed']);
  const t0 = '2026-11-22T20:00:00Z';
  check(wl(t0, Date.parse(t0) + 1955e3).includes('working\u2026 32m'),
        'T-DD-8 the spinner reads 32m after 1955 seconds');

  const nl = load('nowLine', [], { NOW_SCAN: 400, NOW_CHARS: 90 });
  const ev = (kind, data) => ({ kind, data });
  const pane = (state, events) => ({ state, events });
  const turn = [ev('user', { text: 'go' }),
                ev('plan', { entries: [{ content: 'read the code', status: 'completed' },
                                       { content: 'write the test', status: 'in_progress' }] })];
  eq(nl(pane('busy', turn)), 'write the test', 'T-DD-8 the plan\'s in-progress step');
  const t2 = [...turn, ev('tool', { id: 'c1', title: 'Run node selftest_daily.mjs', status: 'in_progress' })];
  eq(nl(pane('busy', t2)), 'Run node selftest_daily.mjs', 'T-DD-8 a later tool call wins');
  const t3 = [...t2, ev('tool', { id: 'c2', title: 'Edit app.js' }), ev('tool', { id: 'c1', status: 'completed' })];
  eq(nl(pane('busy', t3)), 'Run node selftest_daily.mjs',
     'T-DD-8 a title-less update names its own call, not the one between');
  eq(nl(pane('ready', t2)), '', 'T-DD-8 an idle pane has no now-line');
  eq(nl(pane('busy', [ev('tool', { title: 'old turn' }), ev('turn_end', {}), ev('user', {})])), '',
     'T-DD-8 nothing from a finished turn');
  eq(nl(pane('uncertain', [ev('tool', { title: 'a\n  b' })])), 'a b', 'T-DD-8 one line, whitespace folded');
  check(nl(pane('busy', [ev('tool', { title: 'x'.repeat(300) })])).length === 90,
        'T-DD-8 the line is capped');
  const long = [ev('tool', { title: 'far back' }), ...Array(500).fill(ev('text', { text: '.' }))];
  eq(nl(pane('busy', long)), '', 'T-DD-8 the scan is bounded');

  check(/const doing = nowLine\(p\)/.test(src) && /el\('div', 'now', doing\)/.test(src),
        'T-DD-8 the roster row shows the now-line as text');
  check(/p\.state === 'uncertain'\) \{\s*const f = el\('span', 'flag quiet'/.test(src),
        'T-DD-8 an uncertain pane carries a quiet tag');
  check(/\.rit \.now\{/.test(css), 'T-DD-8 the now-line is styled to one line');
}

// T-DD-9 task-first New: the task leads, settings fold behind a line, the
// goal is pinned, and a task that did not send is never lost.
{
  const cs = load('cfgSummaryText');
  eq(cs({ agent: 'Claude Code', model: 'opus', effort: 'high', posture: 'auto', cwd: '~/ai-os' }),
     'Claude Code · opus · high effort · auto in ~/ai-os', 'T-DD-9 the folded line names every setting');
  eq(cs({ agent: 'Codex', role: 'reviewer' }), 'role reviewer · Codex · default model in ~',
     'T-DD-9 a role leads, an unset model says so');

  const gl = load('goalLine');
  eq(gl({}), null, 'T-DD-9 no goal, no line');
  eq(gl({ goal: { task: 'fix the flake', done: 'CI green 3x' } }),
     { text: '◎ fix the flake  ·  done when CI green 3x',
       title: 'goal: fix the flake\ndone when: CI green 3x' }, 'T-DD-9 the pinned goal');
  check(/const gl = el\('div', 'goal', g\.text\)/.test(src), 'T-DD-9 the goal is set as text');
  check(/p\.goal \? JSON\.stringify\(p\.goal\)/.test(fn('headSignature')),
        'T-DD-9 a goal change repaints the header');

  const iTask = html.indexOf('id="f-task"'), iCfg = html.indexOf('id="f-cfg"'),
        iAgent = html.indexOf('id="f-agent"'), iEnd = html.indexOf('</details>', iCfg);
  check(iTask > 0 && iTask < iCfg && iCfg < iAgent && iAgent < iEnd,
        'T-DD-9 the task comes first and the agent is inside the folded settings');
  check(!/id="f-prompt"/.test(html) && !/#f-prompt/.test(src),
        'T-DD-9 Later asks the task above, not a second box');
  check(/common\.task = task/.test(src), 'T-DD-9 Start carries the task');

  // The not-sent path: the typed task goes back into the pane, unsent.
  const calls = [];
  const sc = load('startConversation', [], {
    api: async () => ({ pane: { id: 'n1', state: 'ready' }, sent: false, error: 'boom' }),
    S: { panes: new Map() }, render: () => {}, toast: (m, bad) => calls.push(['toast', m, !!bad]),
    insertIntoComposer: (id, text) => calls.push(['box', id, text]) });
  const ok = await sc({ agent: 'claude', task: 'do it', doneWhen: 'done' });
  eq(calls.find(c => c[0] === 'box'), ['box', 'n1', 'do it\n\nDone when: done'],
     'T-DD-9 an unsent task is put back in the box');
  check(calls.some(c => c[0] === 'toast' && c[2] && /NOT sent: boom/.test(c[1])),
        'T-DD-9 and it says so, loudly');
  check(ok === true, 'T-DD-9 the pane itself opened');
}

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('OK — daily driver: code-block Copy, the context control, drafts and history, focus mode and Alt+N, the phone bar, device alerts, carry model pick, the now-line, task-first New');
