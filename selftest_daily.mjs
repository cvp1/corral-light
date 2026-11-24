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

// T-DD-10 New matches the pane you are using, not the agent's own default.
{
  const nd = load('newDefaults');
  const mine = { id: 'm', agent: 'claude', model: null, wantModel: 'opus', wantEffort: 'medium',
                 cwd: '/u/ai-os', posture: 'auto', title: 'ai-os', state: 'ready', created: '2' };
  eq(nd(mine, [mine]), { agent: 'claude', model: 'opus', effort: 'medium', cwd: '/u/ai-os',
                         posture: 'auto', from: 'ai-os' },
     'T-DD-10 the pane in use seeds lane, model, effort, folder and posture');
  const older = { id: 'o', agent: 'codex', model: 'gpt-6.2-sol', state: 'ready', created: '1', title: 'old' };
  const newer = { ...mine, id: 'n', model: 'claude-opus-5-5', effort: 'high', created: '3', title: 'new' };
  eq(nd(null, [newer, older]).from, 'new', 'T-DD-10 with no pane in use, the newest open one');
  eq(nd(null, [newer]).model, 'claude-opus-5-5', 'T-DD-10 the live model wins over the asked-for one');
  const dead = { ...mine, id: 'd', state: 'dead', created: '9' };
  const remote = { ...mine, id: 'r', remote: { host: 'peer-b' }, created: '8' };
  eq(nd(dead, [older, dead, remote]).agent, 'codex', 'T-DD-10 never a dead or remote pane');
  eq(nd(null, []), null, 'T-DD-10 nothing open, nothing copied');
  check(/newDefaults\(keyPane\(\), \[\.\.\.S\.panes\.values\(\)\]\)/.test(src) &&
        /fillCfg\(copied \? nd : null\)/.test(src),
        'T-DD-10 the New dialog seeds its pickers from that pane');
  const ce = load('catEntry');
  eq(ce({ reasoning_effort: { options: [{ value: 'high' }] } }, 'effort'), { options: [{ value: 'high' }] },
     'T-DD-10 Codex effort (reasoning_effort) is a choosable effort');
  eq(ce({ model: { value: 'x' } }, 'effort'), null, 'T-DD-10 no effort under any id, none offered');
  check(/catEntry\(cat, cid\)/.test(src) && /catEntry\(cat, 'effort'\)/.test(src),
        'T-DD-10 New and carry both read effort through the aliases');
  check(/id="newfrom"/.test(html) && html.indexOf('id="newfrom"') < html.indexOf('id="f-cfg"'),
        'T-DD-10 the dialog says which pane it copied, outside the fold');
}

// T-DD-11 Next up: what waits behind the turn, with Edit and ✕.
{
  const nm = load('nextUpModel', [], { NEXT_UP_CHARS: 160 });
  eq(nm({ queued: [] }), null, 'T-DD-11 nothing queued, no strip');
  const m = nm({ queued: [{ turn: 't1', text: 'fix\n  the   flake', peer: false },
                          { turn: 't2', text: 'x'.repeat(300), peer: false },
                          { turn: 't3', text: 'hi from b', peer: true }] });
  eq(m.title, 'Next up · 3', 'T-DD-11 the strip counts what waits');
  eq(m.items[0], { turn: 't1', n: 1, full: 'fix\n  the   flake', peer: false, text: 'fix the flake',
                   canWithdraw: true }, 'T-DD-11 one line per message, full text kept for hover');
  check(m.items[1].text.length === 160 && m.items[1].text.endsWith('…'), 'T-DD-11 long text is cut');
  check(!m.items[2].canWithdraw, 'T-DD-11 another pane\'s message has no buttons');
  check(nm({ remote: { host: 'peer-b' }, queued: [{ turn: 't', text: 'a' }] }).items[0].canWithdraw,
        'T-DD-11 a pane on another machine can withdraw too (its hub\'s withdraw command)');

  // Edit puts the text back in the box; ✕ only removes; a started one says too late.
  const calls = [];
  const mkWq = (api) => load('withdrawQueued', [], {
    api, scheduleRender: () => {}, refresh: async () => calls.push(['refresh']),
    toast: (msg, bad) => calls.push(['toast', msg, !!bad]),
    insertIntoComposer: (id, text) => calls.push(['box', id, text]) });
  const p = { id: 'p1', queued: [{ turn: 't1', text: 'a' }, { turn: 't2', text: 'b' }] };
  let posted;
  const ok = mkWq(async (path, body) => { posted = [path, body]; return { ok: true, text: 'a' }; });
  check(await ok(p, 't1', true) === true, 'T-DD-11 Edit withdraws');
  eq(posted, ['/api/session/withdraw', { pane: 'p1', turn: 't1' }], 'T-DD-11 the route and its body');
  eq(p.queued.map(x => x.turn), ['t2'], 'T-DD-11 the row goes at once');
  eq(calls.find(c => c[0] === 'box'), ['box', 'p1', 'a\n'], 'T-DD-11 Edit puts it back in the box');
  calls.length = 0;
  await ok(p, 't2', false);
  check(!calls.some(c => c[0] === 'box'), 'T-DD-11 ✕ does not touch the box');
  calls.length = 0;
  const late = mkWq(async () => { const e = new Error('409'); e.status = 409; throw e; });
  check(await late({ id: 'p1', queued: [{ turn: 't9' }] }, 't9', true) === false, 'T-DD-11 too late fails');
  check(calls.some(c => c[0] === 'toast' && c[2] && /already started/.test(c[1])) &&
        !calls.some(c => c[0] === 'box'), 'T-DD-11 too late says so and puts nothing in the box');

  check(/root\.append\(head, ask, log, strip, next, comp\)/.test(src), 'T-DD-11 Next up sits above the composer');
  check(/if \(ev\.kind === 'queue'\) p\.queued = d\.items \|\| \[\];/.test(src), 'T-DD-11 the list updates live');
  check(/case 'withdrawn':/.test(src) && /u\.dataset\.turn = d\.turn/.test(src),
        'T-DD-11 a withdrawn bubble says it was not sent');
  check(/const t = el\('span', 'nut', it\.text\)/.test(src), 'T-DD-11 queued text is set as text');
  check(/\.nextup\{/.test(css) && /\.msg\.user\.withdrawn\{/.test(css), 'T-DD-11 styled');
}


// T-DD-12 the quiet shelf: idle and paused fold to chips; the live work keeps its cells.
{
  const NOW = 1e12;
  const DS = { paneAge: (p) => p.idleS || 0, IDLE_DISPLAY_S: 1800, AGENT_ORIGIN_VIAS: ['peer', 'rig', 'challenge'] };
  const split = load('shelfSplit', ['quietPane', 'displayState', 'paneAge'], DS);
  const P = (id, x = {}) => Object.assign({ id, state: 'ready', pending: [], question: null, idleS: 0 }, x);
  const work = P('w', { state: 'busy' }), ask = P('a', { question: { text: '?' } });
  const turn = P('t', { idleS: 60 }), idle = P('i', { idleS: 7200 }), paused = P('p', { state: 'detached' });
  const dead = P('d', { state: 'dead' }), pinned = P('n', { idleS: 7200, pinned: true });
  const all = [work, idle, ask, paused, turn, dead, pinned];
  const ids = xs => xs.map(p => p.id);
  let r = split(all, { on: true, keep: new Set() }, NOW);
  eq(ids(r.shelf), ['i', 'p'], 'T-DD-12 idle and paused go to the shelf, in roster order');
  eq(ids(r.cells), ['w', 'a', 't', 'd', 'n'], 'T-DD-12 working, needs-you, your-turn, dead and pinned keep their cells, in order');
  r = split(all, { on: true, keep: new Set(['i']) }, NOW);
  eq(ids(r.shelf), ['p'], 'T-DD-12 a kept pane (you are in it, opened it, or have a draft) stays');
  r = split(all, { on: false, keep: new Set() }, NOW);
  eq([ids(r.shelf), r.cells.length, r.held], [[], 7, 2], 'T-DD-12 off: everything in the grid, the quiet ones counted');
  r = split([idle, paused], { on: true, keep: new Set() }, NOW);
  eq([r.shelf.length, r.cells.length, r.held], [0, 2, 0], 'T-DD-12 an all-quiet wall keeps its cells');
  r = split([work, P('m', { idleS: 7200, minimized: true })], { on: true, keep: new Set() }, NOW);
  eq([ids(r.cells), r.shelf.length], [['w'], 0], 'T-DD-12 a minimized pane stays in the minimized bar, not the shelf');
  r = split([work, P('x', { idleS: 60, turnVia: 'peer' })], { on: true, keep: new Set() }, NOW);
  eq(ids(r.shelf), ['x'], 'T-DD-12 a turn another agent started is idle, so it folds');

  const S = { panes: new Map(), unshelved: new Set(['i', 'w', 'gone']), active: 't', focus: 'p' };
  const keep = load('shelfKeep', ['quietPane', 'displayState', 'paneAge'],
    { ...DS, S, draftsLoad: () => ({ n: 'half a thought', d: '   ' }) });
  const k = keep(all, NOW);
  eq([...S.unshelved].sort(), ['i'], 'T-DD-12 opened panes are forgotten once they work again or are gone');
  eq([...k].sort(), ['i', 'n', 'p', 't'], 'T-DD-12 kept: opened, active, focused, and a real draft (not blanks)');

  const focusSrc = fn('focusPane');
  check(/shelfOpen\(id\)/.test(focusSrc), 'T-DD-12 opening a pane from anywhere takes it off the shelf');
  const r2 = fn('render');
  check(/narrow \? \{ cells: panes\.filter\(p => !p\.minimized\), shelf: \[\], held: 0 \}/.test(r2),
        'T-DD-12 never on a phone');
  check(/bars\[bars\.length - 1\]/.test(r2), 'T-DD-12 panes follow the last bar, so a second bar cannot split the grid');
  check(/\.shelfbar::before\{content:'Quiet'\}/.test(css) && /\.grid\.one:has\(> \.minbar \+ \.minbar\)/.test(css),
        'T-DD-12 styled, and a one-pane grid makes room for both bars');
}


// T-DD-13 the question once: the banner shrinks while the transcript's copy is on screen.
{
  const shown = load('questionShown');
  const E = (vis, h, root, on = true) => ({ isIntersecting: on, rootBounds: { height: root },
    boundingClientRect: { height: h }, intersectionRect: { height: vis } });
  check(shown(E(80, 100, 400)), 'T-DD-13 most of a short question on screen counts');
  check(!shown(E(30, 100, 400)), 'T-DD-13 a sliver does not');
  check(shown(E(220, 900, 400)), 'T-DD-13 a tall question filling half the log counts');
  check(!shown(E(0, 100, 400, false)), 'T-DD-13 off screen is off screen');
  check(!shown({ isIntersecting: true, rootBounds: null, boundingClientRect: { height: 1 }, intersectionRect: { height: 1 } }),
        'T-DD-13 no root measurement is not proof it was seen');
  check(/b\.dataset\.qat = d\.at \|\| ''/.test(src), 'T-DD-13 the transcript bubble names its question');
  check(/questionInView\(rec, p\);/.test(fn('updatePane')), 'T-DD-13 every update re-finds the bubble');
  check(/'Question awaiting your reply ↓'/.test(fn('questionBanner')), 'T-DD-13 the banner carries its one-line jump');
  check(/\.askslot\.inview \.qbanner \.qwho,\.askslot\.inview \.qbanner \.qtext\{display:none\}/.test(css),
        'T-DD-13 in view: the full text hides, the jump shows');
}


// T-DD-14 header weight: posture is quiet; rust is kept for real warnings.
{
  check(!/\.ph2 \.f\.auto\{color:var\(--rust\)\}/.test(css), 'T-DD-14 auto posture is no longer rust');
  check(/\.ph2 \.f\.strict,\.ph2 \.f\.edits\{color:var\(--faint\)\}/.test(css), 'T-DD-14 strict and edits are quiet');
  check(/\.ph2 \.f\.ctx\.warn\{color:var\(--rust\)\}/.test(css), 'T-DD-14 a context warning is still rust');
  check(/case 'turn_start': break;/.test(src), 'T-DD-14 turn_start never draws in the transcript');
}


// T-DD-15 pasted images: refused at paste time where the hub would refuse.
{
  const why = load('shotRefusal', [], { SHOT_MAX_BYTES: 3_750_000, SHOTS_PER_MESSAGE: 4,
    SHOT_TYPES: ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] });
  const png = { type: 'image/png', size: 1000 };
  eq(why({ acceptsImages: true }, png, 0), '', 'T-DD-15 a PNG on a lane that takes images');
  eq(why({}, png, 0), '', 'T-DD-15 an older hub (no acceptsImages) lets the hub decide');
  check(/does not take images/.test(why({ acceptsImages: false, label: 'Grok' }, png, 0)), 'T-DD-15 a lane without images says so');
  eq(why({ remote: { host: 'b' }, acceptsImages: true }, png, 0), '', 'T-DD-15 a remote pane takes images (stored on its own hub)');
  check(/only PNG/.test(why({}, { type: 'image/svg+xml', size: 10 }, 0)), 'T-DD-15 SVG is refused (it is script)');
  check(/3\.75 MB/.test(why({}, { type: 'image/png', size: 4e6 }, 0)), 'T-DD-15 too big');
  check(/at most 4/.test(why({}, png, 4)), 'T-DD-15 at most four');
  const comp = fn('composer');
  check(/if \(!items\.some\(i => i\.type === 'text\/plain'\)\) e\.preventDefault\(\);/.test(comp),
        'T-DD-15 an image with text on the clipboard still pastes the text');
  check(/images: shots\.map\(sh => sh\.sha\)/.test(comp), 'T-DD-15 the message names its images');
  check(/if \(!t && !shots\.length\) return;/.test(comp), 'T-DD-15 an image alone can be sent');
  check(/c\.append\(att, ac, ta, dictateButton\(ta\), b, stopButton\(p\)\)/.test(comp), 'T-DD-15 the row sits above the box');
  check(/\(d\.images \|\| \[\]\)\.length\) u\.appendChild\(shotStrip\(p\.id, d\.images\)\)/.test(fn('renderLog')),
        'T-DD-15 the bubble shows what was sent, from the hub');
}


// T-DD-16 Ask a panel: the browser's way into panel.py.
{
  const req = load('panelRequest', [], { PANEL_MAX_ARMS: 4 });
  const p = { id: 'pa', cwd: '/work/repo' };
  let r = req(p, { topic: ' x ', charge: ' Which cache? ', rounds: '2', lanes: ['codex', 'grok'] });
  eq(r.errors, [], 'T-DD-16 a filled form has no errors');
  eq(r.body, { charge: 'Which cache?', lanes: ['codex', 'grok'], cwd: '/work/repo', topic: 'x', rounds: 2,
               author: 'pa', callerPane: 'pa' }, 'T-DD-16 the request: the pane is the author and the caller');
  r = req(p, { charge: '', lanes: [] });
  eq(r.errors.length, 2, 'T-DD-16 an empty question and no arms are both said');
  check(/at most 4/.test(req(p, { charge: 'q', lanes: ['a', 'b', 'c', 'd', 'e'] }).errors.join()), 'T-DD-16 at most four arms');
  eq(req(p, { charge: 'q', lanes: ['a'], rounds: '7' }).body.rounds, 1, 'T-DD-16 rounds is 1 unless 2 was picked');
  const html = readFileSync(new URL('./static/index.html', import.meta.url), 'utf8');
  check(/<dialog id="pandlg">/.test(html) && /id="pan-charge"/.test(html), 'T-DD-16 the dialog exists');
  check(/item\('Ask a panel…', \(\) => openPanel\(p\)/.test(src), 'T-DD-16 the pane menu opens it');
  check(/!isTerm\(p\) && !p\.remote\) item\('Ask a panel…'/.test(src), 'T-DD-16 not on a terminal or a remote pane');
}


// T-DD-17 Changes in this folder: a plain pane's working tree since HEAD.
{
  const sum = load('changesSummary');
  check(/not in a git repository/.test(sum({ repo: false })), 'T-DD-17 not a repo says so');
  check(/Nothing changed since the last commit on main/.test(sum({ repo: true, branch: 'main', counts: { changed: 0 } })),
        'T-DD-17 a clean tree says so');
  check(/3 files changed since the last commit on main · first 2 listed · some output was cut/.test(
        sum({ repo: true, branch: 'main', counts: { changed: 3, shown: 2 }, truncated: true })), 'T-DD-17 counts and cuts are said');
  const cls = load('patchLineClass');
  eq(['+++ b/x', '--- a/x', '@@ -1 +1 @@', '+new', '-old', ' same'].map(cls),
     ['pfile', 'pfile', 'phunk', 'padd', 'pdel', 'pctx'], 'T-DD-17 each patch line is classed, never parsed as markup');
  check(/!isTerm\(p\) && !p\.worktree\) item\('Changes in this folder…'/.test(src),
        'T-DD-17 offered on a plain pane, here or on another hub (own branches have Review)');
  check(/pre\.appendChild\(el\('span', patchLineClass\(line\), line/.test(fn('changesNode')), 'T-DD-17 patch text goes in as text');
}


// T-DD-18 @ mentions: the word at the caret, replaced by the picked path.
{
  const at = load('mentionAt');
  eq(at('see @hu', 7), { start: 4, end: 7, q: 'hu' }, 'T-DD-18 @word after a space');
  eq(at('@', 1), { start: 0, end: 1, q: '' }, 'T-DD-18 a bare @ at the start lists files');
  eq(at('mail me@host', 12), null, 'T-DD-18 an e-mail address is not a mention');
  eq(at('see @hub now', 12), null, 'T-DD-18 a finished mention, caret elsewhere, is not reopened');
  eq(at('see @hub now', 8), { start: 4, end: 8, q: 'hub' }, 'T-DD-18 the caret decides, not the end of the text');
  const ins = load('mentionInsert');
  eq(ins('see @hu and', { start: 4, end: 7 }, 'hub.py'), { value: 'see @hub.py  and', caret: 12 },
     'T-DD-18 the path replaces the word, caret after it');
  const comp = fn('composer');
  check(/mentionInsert\(ta\.value, mentionAt\(ta\.value, ta\.selectionStart\) \|\| at, cmd\.name\)/.test(comp),
        'T-DD-18 accepting re-reads the word at the caret (typing may have gone on)');
  check(/if \(mine !== askSeq\) return;/.test(comp), 'T-DD-18 a slow answer for an older keystroke is dropped');
  check(/!isTerm\(p\) \? mentionAt/.test(comp), 'T-DD-18 not on a terminal; a remote pane asks its own hub');
}

// T-DD-19 the composer's 🎙 (dictate.py): the device decides the tool.
{
  const plat = (ua, touch = 0) => load('dictatePlatform', [], {
    navigator: { userAgent: ua, maxTouchPoints: touch } })();
  eq(plat('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140'), 'mac', 'T-DD-19 a Mac');
  eq(plat('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605', 5), 'touch', 'T-DD-19 an iPad asking for the desktop site');
  eq(plat('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)'), 'touch', 'T-DD-19 an iPhone');
  eq(plat('Mozilla/5.0 (Linux; Android 15; SM-X210) Chrome/140 Safari/537.36'), 'touch', 'T-DD-19 the tablet (Android before Linux)');
  eq(plat('Mozilla/5.0 (X11; Linux x86_64) Chrome/140'), 'linux', 'T-DD-19 a Linux desktop');
  const btn = fn('dictateButton');
  check(/b\.onmousedown = e => e\.preventDefault\(\);/.test(btn), 'T-DD-19 the click never takes focus from the composer');
  check(/if \(DICTATE\.local\)/.test(btn) && /shortcuts:\/\/run-shortcut\?name=/.test(btn)
        && /DICTATE\.linuxScheme \+ ':\/\/toggle'/.test(btn), 'T-DD-19 hub, Shortcuts URL, Linux handler');
}

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('OK — daily driver: code-block Copy, the context control, drafts and history, focus mode and Alt+N, the phone bar, device alerts, carry model pick, the now-line, task-first New, New follows your pane, Next up, the quiet shelf, the question once, header weight, pasted images, Ask a panel, folder changes, @ mentions');
