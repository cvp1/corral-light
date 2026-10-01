/* Sign in, in the browser (DESIGN-6 S4, T4.9 and the four doors).
 *
 * Driven against the real functions in static/app.js -- composer(),
 * isLoginCommand(), startLogin(), signInButton(), loginLine() -- with a
 * capturing DOM whose innerHTML setter throws and an api() that records
 * every POST. What is under test is what gets POSTed: `/login` in a Claude
 * pane that died of its login starts the sign-in and sends nothing; the same
 * text anywhere else is sent as typed.
 *
 * Run: node selftest_login.mjs   (exit 0 = pass)
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

const mk = (tag, cls, text) => {
  const n = { tag, className: cls || '', textContent: text ?? '', kids: [], value: '',
              disabled: false, type: '', title: '', onclick: null, dataset: {}, style: {},
              placeholder: '', rows: 1, scrollHeight: 20,
              appendChild(k) { this.kids.push(k); return k; },
              append(...ks) { this.kids.push(...ks); },
              replaceChildren(...ks) { this.kids = ks; } };
  Object.defineProperty(n, 'innerHTML', { set() { throw new Error('innerHTML'); } });
  return n;
};

/* A browser: the real login functions over a recording api(). */
function browser(panes, loginState) {
  const posts = [], toasts = [];
  const S = { panes: new Map(panes.map(p => [p.id, p])), claudeLogin: loginState || null };
  const api = async (path, body) => {
    posts.push({ path, body });
    if (path === '/api/claude/login') return { ok: true, state: 'running', why: 'sign in in the window that opened' };
    return { ok: true };
  };
  const deps = { el: mk, S, api, toast: (m, b) => toasts.push([m, !!b]),
                 refresh: async () => {}, isTerm: p => (p.agent || '').startsWith('host:'),
                 termComposer: () => mk('div', 'term'), composablePanes: () => [],
                 pendingPerm: () => null, permOptions: () => [] };
  const names = Object.keys(deps);
  const lib = new Function(...names, `
    ${fn('isLoginCommand')} async ${fn('startLogin')} ${fn('signInButton')} ${fn('loginLine')}
    ${fn('composer')}
    return { isLoginCommand, startLogin, signInButton, loginLine, composer };`)(
    ...names.map(k => deps[k]));
  return { ...lib, posts, toasts, S };
}

async function typeAndSend(b, pane, text) {
  const c = b.composer(pane, 'live');
  const ta = c.kids.find(k => k.tag === 'textarea');
  const send = c.kids.find(k => k.tag === 'button');
  ta.value = text;
  await send.onclick();
  return ta;
}

const authDead = { id: 'p1', agent: 'claude', state: 'dead', deadCause: 'auth', commands: [] };
const liveClaude = { id: 'p2', agent: 'claude', state: 'ready', deadCause: null, commands: [] };
const codexDead = { id: 'p3', agent: 'codex', state: 'dead', deadCause: 'auth', commands: [] };
const claudeOtherDeath = { id: 'p4', agent: 'claude', state: 'dead', deadCause: null, commands: [] };

/* ── T4.9 the composer: a sign-in only where /login cannot mean anything else ── */
{
  const b = browser([authDead]);
  const ta = await typeAndSend(b, authDead, '  /LOGIN ');
  check(b.posts.length === 1 && b.posts[0].path === '/api/claude/login',
        `/login in an auth-dead Claude pane POSTed ${JSON.stringify(b.posts)}`);
  check(b.posts[0].body.from === 'composer' && b.posts[0].body.pane === 'p1',
        `the sign-in does not name its door and pane: ${JSON.stringify(b.posts[0].body)}`);
  check(!b.posts.some(x => x.path === '/api/session/send'), '/login was also SENT as a message');
  check(ta.value === '', 'the composer kept /login after starting the sign-in');
}
for (const [pane, why] of [[liveClaude, 'a live Claude pane'], [codexDead, 'a codex pane'],
                           [claudeOtherDeath, 'a Claude pane dead of something else']]) {
  const b = browser([pane]);
  await typeAndSend(b, pane, '/login');
  check(b.posts.length === 1 && b.posts[0].path === '/api/session/send' &&
        b.posts[0].body.text === '/login', `/login in ${why} was not sent as typed: ${JSON.stringify(b.posts)}`);
}
{
  const b = browser([authDead]);
  await typeAndSend(b, authDead, '/login please');
  check(b.posts.length === 1 && b.posts[0].path === '/api/session/send' &&
        b.posts[0].body.text === '/login please', '`/login please` was not sent as typed');
}
{
  // The composer was built while the pane was live; it died of its login
  // since. The FRESH state decides, not the snapshot the composer closed over.
  const b = browser([authDead]);
  await typeAndSend(b, { ...liveClaude, id: 'p1' }, '/login');
  check(b.posts.length === 1 && b.posts[0].path === '/api/claude/login',
        `a composer built before the death sent /login: ${JSON.stringify(b.posts)}`);
}

/* ── the button: one door, disabled while a window is open, text only ────── */
{
  const b = browser([], { state: 'running' });
  const btn = b.signInButton('rail');
  check(btn.disabled && btn.textContent === 'Signing in…', 'Sign in is clickable while a window is open');
  const b2 = browser([], { state: 'check-status' });
  const btn2 = b2.signInButton('banner', 'p1');
  check(!btn2.disabled && btn2.textContent === 'Sign in', 'Sign in is not offered after an unclear sign-in');
  await btn2.onclick({ stopPropagation() {} });
  check(b2.posts.length === 1 && b2.posts[0].body.from === 'banner' && b2.posts[0].body.pane === 'p1',
        `the banner button POSTed ${JSON.stringify(b2.posts)}`);
}
{
  const b = browser([]);
  check(b.loginLine(null) === '' && b.loginLine({ state: 'idle' }) === '', 'idle says something');
  check(b.loginLine({ state: 'gave-up', why: 'w' }) === 'stopped watching the sign-in: w',
        'gave-up line: ' + b.loginLine({ state: 'gave-up', why: 'w' }));
}

/* ── a refusal reads its reason, from the body, as an error ────────────────── */
{
  const refusedApi = async () => { const e = new Error('403 Forbidden');
    e.body = { ok: false, state: 'refused', why: 'starts only from that machine — run `claude auth login`' };
    throw e; };
  const toasts = [];
  const startLogin = new Function('api', 'toast', 'refresh',
    `async ${fn('startLogin')} return startLogin;`)(refusedApi, (m, b) => toasts.push([m, !!b]), async () => {});
  const r = await startLogin('rail');
  check(r === null && toasts.length === 1 && toasts[0][1] && /claude auth login/.test(toasts[0][0]),
        `a refusal did not toast its reason as an error: ${JSON.stringify(toasts)}`);
}

/* ── the four doors exist ──────────────────────────────────────────────────── */
check(/id="signinrow"/.test(html), 'the New dialog has no #signinrow for the picker door');
check(/signInButton\('rail'\)/.test(src), 'the rail login card has no Sign in');
check((src.match(/signInButton\('banner', p\.id\)/g) || []).length === 2,
      'the dead-pane banner and the rail dead card do not both offer Sign in');
check(/signInButton\('picker'\)/.test(src), 'the picker refusal has no Sign in');

if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log('selftest_login: ok');
