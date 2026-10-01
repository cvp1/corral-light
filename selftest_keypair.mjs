/* Pairing by key, in the browser (DESIGN-6 S8, T8.1-T8.4).
 *
 * Driven against the real functions in static/app.js -- keyPlace(),
 * keyOffer(), showKeyOffer(), pairByKey(), the base64url pair, the option
 * converters, renderSecKeys() and enrollKey() -- with a capturing DOM whose
 * innerHTML setter throws, a recording api(), and a fake
 * navigator.credentials. What is under test is what the page offers, what it
 * POSTs, and how often it calls the authenticator.
 *
 * Run: node selftest_keypair.mjs   (exit 0 = pass)
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
// The real declarations, not copies: a changed initial value must show here.
function decl(re) {
  const m = src.match(re);
  if (!m) throw new Error(`no declaration ${re} in app.js`);
  return m[0];
}

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

const text = n => [n.textContent, ...(n.kids || []).map(text)].join('');
const mk = (tag, cls, txt) => {
  const classes = new Set((cls || '').split(' ').filter(Boolean));
  const n = { tag, textContent: txt ?? '', kids: [], value: '', disabled: false, type: '',
              title: '', href: '', onclick: null,
              classList: { add: c => classes.add(c), remove: c => classes.delete(c),
                           contains: c => classes.has(c),
                           toggle: (c, on) => (on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c) },
              appendChild(k) { this.kids.push(k); return k; },
              append(...ks) { this.kids.push(...ks); },
              replaceChildren(...ks) { this.kids = ks; this.textContent = ''; } };
  Object.defineProperty(n, 'className', { get: () => [...classes].join(' ') });
  Object.defineProperty(n, 'innerHTML', { set() { throw new Error('innerHTML'); } });
  return n;
};
const find = (n, pred) => pred(n) ? n : (n.kids || []).map(k => find(k, pred)).find(Boolean);

const NAMES = ['b64url', 'unb64url', 'keyPlace', 'keyOffer', 'requestOptions', 'creationOptions',
               'assertionBody', 'showKeyOffer', 'pairByKey', 'secKeyState', 'whenText',
               'secKeyRows', 'renderSecKeys', 'enrollKey'];

/* A page: the real functions over a fake DOM, location, window, navigator. */
function page({ loc, win, answers = {}, get, create } = {}) {
  const ids = ['pair', 'pairkey', 'pairkeynote', 'pairhow', 'paircmd', 'pairnote',
               'sk-state', 'sk-list', 'sk-coderow', 'sk-code', 'sk-label', 'sk-enroll', 'sk-msg'];
  const dom = Object.fromEntries(ids.map(i => [i, mk('div', i === 'pairkey' ? 'pairkey hide' : '')]));
  dom.paircmd.textContent = 'corral-light pair ABC-DEF';
  const $ = s => { const n = dom[s.replace(/^#/, '')]; if (!n) throw new Error('no ' + s); return n; };
  const posts = [], calls = { get: 0, create: 0, start: 0, getArgs: [], createArgs: [] };
  const api = async (path, body) => {
    posts.push({ path, body });
    const a = answers[path];
    if (a instanceof Error) throw a;
    return typeof a === 'function' ? a(body) : (a ?? { ok: true });
  };
  const navigator = { credentials: {
    get: async o => { calls.get++; calls.getArgs.push(o); return get ? get(o) : assertion(); },
    create: async o => { calls.create++; calls.createArgs.push(o); return create ? create(o) : made(); } } };
  const deps = { $, el: mk, api, navigator, start: () => { calls.start++; },
                 clearInterval: () => {}, btoa, atob,
                 location: loc || LOCALHOST, window: win || CAPABLE };
  const names = Object.keys(deps);
  const lib = new Function(...names, `
    let pairTimers = [1, 2];
    ${decl(/const LOOPBACK = [^;]+;/)}
    ${decl(/let keyDeclined = [^;]+;/)}
    ${NAMES.map(n => (src.includes('async function ' + n + '(') ? 'async ' : '') + fn(n)).join('\n')}
    return { ${NAMES.join(', ')}, timers: () => pairTimers, declined: () => keyDeclined };`)(
    ...names.map(k => deps[k]));
  return { lib, dom, posts, calls };
}

const LOCALHOST = { origin: 'http://localhost:8098', hostname: 'localhost', port: '8098' };
const LOOP = { origin: 'http://127.0.0.1:8098', hostname: '127.0.0.1', port: '8098' };
const CAPABLE = { isSecureContext: true, PublicKeyCredential: function () {} };
const bytes = (...b) => new Uint8Array(b).buffer;
const assertion = () => ({ rawId: bytes(1, 2, 3),
  response: { clientDataJSON: bytes(4), authenticatorData: bytes(5, 6), signature: bytes(7) } });
const made = () => ({ rawId: bytes(9),
  response: { clientDataJSON: bytes(10), attestationObject: bytes(11, 12) } });
const notAllowed = () => Object.assign(new Error('The operation either timed out or was not allowed.'),
                                       { name: 'NotAllowedError' });
const KO = 'http://localhost:8098';
const BEGIN = { challenge: 'AAEC', rpId: 'localhost', timeout: 120000,
                userVerification: 'required', allowCredentials: ['AQID'] };

/* T8.1: the button needs all three -- a key for THIS origin, a secure
   context, WebAuthn -- and each one alone takes it away. */
for (const avail of [false, true]) for (const secure of [false, true]) for (const pkc of [false, true]) {
  const win = { isSecureContext: secure, PublicKeyCredential: pkc ? function () {} : undefined };
  const p = page({ win });
  const d = { keyAvailable: avail, keyOrigin: KO };
  const want = avail && secure && pkc;
  check((p.lib.keyOffer(d, LOCALHOST, win).kind === 'here') === want,
        `T8.1 keyOffer avail=${avail} secure=${secure} pkc=${pkc}`);
  p.lib.showKeyOffer('ABC-DEF', d);
  const btn = find(p.dom.pairkey, n => n.tag === 'button');
  check(!!btn === want, `T8.1 a button is rendered only with all three (${avail},${secure},${pkc})`);
  check(p.dom.pairkey.classList.contains('hide') === !want, 'T8.1 the slot hides when nothing is offered');
  check(p.dom.paircmd.textContent === 'corral-light pair ABC-DEF', 'T8.1 the code stays');
  if (want) {
    check(btn.textContent === 'Touch your key', 'T8.1 the button says Touch your key');
    check(/^Or pair from a shell/.test(p.dom.pairhow.textContent), 'T8.1 the code reads as "or pair from a shell"');
  } else {
    check(/^Run this in a shell/.test(p.dom.pairhow.textContent), 'T8.1 no button: the code is the way');
  }
}
{ // a key enrolled for ANOTHER origin is not a key for this one
  const p = page();
  check(p.lib.keyOffer({ keyAvailable: true, keyOrigin: 'https://hub.example.ts.net' },
                       LOCALHOST, CAPABLE).kind === 'none', 'T8.1 a key for another origin offers nothing');
  check(p.lib.keyOffer({ keyAvailable: true, keyOrigin: null }, LOCALHOST, CAPABLE).kind === 'none',
        'T8.1 no configured key origin offers nothing');
  check(p.lib.keyOffer(undefined, LOCALHOST, CAPABLE).kind === 'none', 'T8.1 no answer offers nothing');
}

/* T8.2: base64url round-trips for 0-64 bytes, alphabet and padding as the
   hub's webauthn.b64url writes them. */
{
  const { lib } = page();
  for (let n = 0; n <= 64; n++) {
    const u = new Uint8Array(n);
    for (let i = 0; i < n; i++) u[i] = [0xfb, 0xff, 0x3e, 0x3f, 0x00][i % 5] ^ (i * 37 & 0xff);
    const s = lib.b64url(u);
    check(/^[A-Za-z0-9_-]*$/.test(s), `T8.2 alphabet at ${n} bytes: ${s}`);
    check(s === Buffer.from(u).toString('base64url'), `T8.2 matches the reference encoding at ${n} bytes`);
    const back = lib.unb64url(s);
    check(back.length === n && back.every((b, i) => b === u[i]), `T8.2 round-trip at ${n} bytes`);
    check(lib.b64url(u.buffer) === s, `T8.2 an ArrayBuffer encodes the same at ${n} bytes`);
  }
  for (const junk of ['A', 'AAAAA', 'AA==', 'A+B/', 'AA AA', null, 7]) {
    let threw = false;
    try { lib.unb64url(junk); } catch { threw = true; }
    check(threw, `T8.2 refuses ${JSON.stringify(junk)}`);
  }
}

/* T8.3: on 127.0.0.1 the page links to localhost at the same port, and
   shows no button. A different port is not the same hub's key origin. */
{
  const p = page({ loc: LOOP });
  const d = { keyAvailable: true, keyOrigin: KO };
  const o = p.lib.keyOffer(d, LOOP, CAPABLE);
  check(o.kind === 'link' && o.link === 'http://localhost:8098', 'T8.3 127.0.0.1 gets the localhost link');
  p.lib.showKeyOffer('ABC-DEF', d);
  check(!find(p.dom.pairkey, n => n.tag === 'button'), 'T8.3 no button on 127.0.0.1');
  const a = find(p.dom.pairkey, n => n.tag === 'a');
  check(a && a.href === 'http://localhost:8098', 'T8.3 the link goes to localhost:8098');
  check(!p.dom.pairkey.classList.contains('hide'), 'T8.3 the link is visible');
  check(/^Run this in a shell/.test(p.dom.pairhow.textContent), 'T8.3 the code is still the way here');
  const other = { origin: 'http://127.0.0.1:9000', hostname: '127.0.0.1', port: '9000' };
  check(p.lib.keyOffer(d, other, CAPABLE).kind === 'none', 'T8.3 another port is not offered a link');
  check(p.lib.keyOffer({ keyAvailable: false, keyOrigin: KO }, LOOP, CAPABLE).kind === 'none',
        'T8.3 no key enrolled: no link either');
  // Only loopback is the same machine. From a LAN name, "localhost" is the
  // VISITOR's own computer, so a link there would point at the wrong hub.
  const lan = { origin: 'http://hub.lan:8098', hostname: 'hub.lan', port: '8098' };
  check(p.lib.keyOffer(d, lan, CAPABLE).kind === 'none', 'T8.3 a LAN name is not offered a localhost link');
}

/* The ceremony itself: begin with the live code, get() with the options
   decoded, finish with the assertion encoded, then straight in. */
{
  const p = page({ answers: { '/api/pair/key/begin': BEGIN, '/api/pair/key/finish': { status: 'ok' } } });
  p.lib.showKeyOffer('ABC-DEF', { keyAvailable: true, keyOrigin: KO });
  const btn = find(p.dom.pairkey, n => n.tag === 'button');
  await btn.onclick();
  check(p.posts[0].path === '/api/pair/key/begin' && p.posts[0].body.code === 'ABC-DEF',
        'begin carries the code this page shows');
  const pk = p.calls.getArgs[0].publicKey;
  check(pk.rpId === 'localhost' && pk.userVerification === 'required' && pk.timeout === 120000,
        'get() is held to the hub\'s rpId and UV');
  check(pk.challenge instanceof Uint8Array && [...pk.challenge].join() === '0,1,2', 'the challenge is decoded');
  check(pk.allowCredentials.length === 1 && pk.allowCredentials[0].type === 'public-key'
        && [...pk.allowCredentials[0].id].join() === '1,2,3', 'allowCredentials are decoded ids');
  const fin = p.posts[1];
  check(fin.path === '/api/pair/key/finish', 'finish follows');
  check(JSON.stringify(fin.body) === JSON.stringify({ challenge: 'AAEC', id: 'AQID', clientDataJSON: 'BA',
        authenticatorData: 'BQY', signature: 'Bw' }), `finish carries the assertion: ${JSON.stringify(fin.body)}`);
  check(p.calls.start === 1 && p.lib.timers().length === 0, 'paired: straight in, timers stopped');
  check(p.dom.pair.classList.contains('hide'), 'paired: the pairing screen hides');
}

/* T8.4: NotAllowedError falls back to the code ONCE -- one authenticator
   call, no finish, the button gone for this page load, never re-offered. */
{
  const p = page({ answers: { '/api/pair/key/begin': BEGIN }, get: () => { throw notAllowed(); } });
  const d = { keyAvailable: true, keyOrigin: KO };
  p.lib.showKeyOffer('ABC-DEF', d);
  await find(p.dom.pairkey, n => n.tag === 'button').onclick();
  check(p.calls.get === 1, `T8.4 exactly one authenticator call (got ${p.calls.get})`);
  check(!p.posts.some(x => x.path === '/api/pair/key/finish'), 'T8.4 nothing is finished');
  check(p.calls.start === 0, 'T8.4 not paired');
  check(p.lib.declined(), 'T8.4 the decline is remembered');
  check(p.dom.pairkey.classList.contains('hide') && !find(p.dom.pairkey, n => n.tag === 'button'),
        'T8.4 the button is gone');
  check(/code instead/.test(p.dom.pairkeynote.textContent), 'T8.4 the page says to use the code');
  check(p.lib.timers().length === 2, 'T8.4 the code\'s own polling keeps running');
  p.lib.showKeyOffer('GHJ-KLM', d);             // the code expired; a fresh one renders
  check(!find(p.dom.pairkey, n => n.tag === 'button'), 'T8.4 a fresh code does not bring the button back');
  check(p.calls.get === 1, 'T8.4 and nothing calls the authenticator on its own');
}
{ // any other failure: say why, keep the button, keep the code
  const refused = Object.assign(new Error('signature does not verify'), { status: 403 });
  const p = page({ answers: { '/api/pair/key/begin': BEGIN, '/api/pair/key/finish': refused } });
  p.lib.showKeyOffer('ABC-DEF', { keyAvailable: true, keyOrigin: KO });
  const btn = find(p.dom.pairkey, n => n.tag === 'button');
  await btn.onclick();
  check(!p.lib.declined() && !btn.disabled, 'a refusal is not a decline: the button stays usable');
  check(/signature does not verify.*code still works/.test(p.dom.pairkeynote.textContent),
        'a refusal names the reason and the code');
  check(p.calls.start === 0, 'a refusal does not pair');
}

/* Security keys: the list as text, Enroll for a first and a later key. */
const LIST = { origin: KO, policy: 'key-or-code', policyError: null, keysError: null,
               verifier: 'ok', verifierWhy: null,
               keys: [{ id: 'AQID', label: 'blue key', origin: KO, enrolledAt: 1, lastUsed: null }] };
{
  const p = page({ answers: { '/api/pair/key/list': LIST } });
  await p.lib.renderSecKeys();
  const t = text(p.dom['sk-list']);
  check(/blue key/.test(t) && t.includes(KO) && /last used: never/.test(t), `the list shows label, origin, lastUsed: ${t}`);
  check(/Policy: key-or-code/.test(p.dom['sk-state'].textContent) && /verifier: ok/.test(p.dom['sk-state'].textContent),
        'the state line shows policy and verifier');
  check(p.dom['sk-coderow'].classList.contains('hide'), 'a key for this origin exists: no code asked');
  check(!p.dom['sk-enroll'].disabled, 'Enroll is available on the key origin');
}
{
  const down = { ...LIST, verifier: 'unavailable', verifierWhy: 'no openssl', keys: [] };
  const p = page({ answers: { '/api/pair/key/list': down } });
  await p.lib.renderSecKeys();
  check(/verifier: unavailable \(no openssl\)/.test(p.dom['sk-state'].textContent), 'the verifier state says why');
  check(!p.dom['sk-coderow'].classList.contains('hide'), 'no key yet: the code is asked for');
  check(/No keys enrolled/.test(text(p.dom['sk-list'])), 'an empty list says so');
}
{
  const p = page({ loc: LOOP, answers: { '/api/pair/key/list': LIST } });
  await p.lib.renderSecKeys();
  check(p.dom['sk-enroll'].disabled, 'on 127.0.0.1 Enroll is disabled');
  const a = find(p.dom['sk-msg'], n => n.tag === 'a');
  check(a && a.href === 'http://localhost:8098', 'and links to localhost');
}
const CREATE = { challenge: 'AAEC', rp: { id: 'localhost', name: 'Corral Light' },
                 user: { id: 'AQID', name: 'corral-light', displayName: 'Corral Light' },
                 pubKeyCredParams: [{ type: 'public-key', alg: -7 }], excludeCredentials: ['CQ'],
                 authenticatorSelection: { userVerification: 'required' }, attestation: 'none',
                 timeout: 120000, first: true };
{ // a first key: the shell code, one create(), no approval
  const p = page({ answers: { '/api/pair/key/list': { ...LIST, keys: [] },
                              '/api/pair/key/enroll/begin': CREATE,
                              '/api/pair/key/enroll/finish': { enrolled: 'CQ', policy: 'key-or-code' } } });
  p.dom['sk-code'].value = ' ABCD-EFGH ';
  p.dom['sk-label'].value = 'blue key';
  await p.lib.enrollKey();
  check(p.posts[0].path === '/api/pair/key/enroll/begin' && p.posts[0].body.code === 'ABCD-EFGH',
        'enroll begins with the trimmed shell code');
  const pk = p.calls.createArgs[0].publicKey;
  check([...pk.user.id].join() === '1,2,3' && [...pk.challenge].join() === '0,1,2', 'create() gets decoded ids');
  check(pk.excludeCredentials[0].type === 'public-key' && [...pk.excludeCredentials[0].id].join() === '9',
        'excludeCredentials is sent, decoded');
  check(pk.attestation === 'none' && pk.rp.id === 'localhost', 'create() is held to the hub\'s rp');
  check(!('first' in pk), 'the hub\'s bookkeeping is not passed to the authenticator');
  const fin = p.posts[1];
  check(fin.path === '/api/pair/key/enroll/finish' && fin.body.attestationObject === 'Cww'
        && fin.body.clientDataJSON === 'Cg' && fin.body.label === 'blue key' && fin.body.challenge === 'AAEC',
        `finish carries the registration: ${JSON.stringify(fin.body)}`);
  check(p.calls.get === 0 && !p.posts.some(x => x.path.endsWith('/approve')), 'a first key needs no second touch');
  check(p.dom['sk-msg'].textContent === 'Enrolled.' && p.dom['sk-code'].value === '', 'enrolled, code field cleared');
}
{ // a later key: the hub answers with a transaction; an enrolled key signs it
  const approve = { ...BEGIN, challenge: 'BBBB' };
  const p = page({ answers: { '/api/pair/key/list': LIST,
                              '/api/pair/key/enroll/begin': { ...CREATE, first: false },
                              '/api/pair/key/enroll/finish': { approve },
                              '/api/pair/key/enroll/approve': { enrolled: 'CQ', policy: 'key-or-code' } } });
  await p.lib.enrollKey();
  check(p.calls.create === 1 && p.calls.get === 1, 'two touches: create, then get');
  const ap = p.posts.find(x => x.path === '/api/pair/key/enroll/approve');
  check(ap && ap.body.challenge === 'BBBB' && ap.body.id === 'AQID' && ap.body.signature === 'Bw',
        'the approval signs the transaction\'s own challenge');
  check(!('attestationObject' in ap.body), 'the approval carries no key of its own');
}
{ // NotAllowedError on create: nothing is finished, nothing retried
  const p = page({ answers: { '/api/pair/key/list': LIST, '/api/pair/key/enroll/begin': CREATE },
                   create: () => { throw notAllowed(); } });
  await p.lib.enrollKey();
  check(p.calls.create === 1 && !p.posts.some(x => x.path.endsWith('/finish')), 'a declined enroll finishes nothing');
  check(/nothing was enrolled/.test(p.dom['sk-msg'].textContent), 'and says so');
  check(!p.dom['sk-enroll'].disabled, 'Enroll is usable again');
}

/* The shipped page: every id the code reaches exists, and there is no Remove. */
for (const id of ['pairkey', 'pairkeynote', 'pairhow', 'paircmd', 'seckeysbtn', 'seckeydlg',
                  'sk-state', 'sk-list', 'sk-coderow', 'sk-code', 'sk-label', 'sk-enroll', 'sk-msg'])
  check(html.includes(`id="${id}"`), `index.html has #${id}`);
const dlg = html.slice(html.indexOf('<dialog id="seckeydlg"'), html.indexOf('</dialog>', html.indexOf('<dialog id="seckeydlg"')));
check(dlg.length > 0 && !/<button[^>]*>\s*Remove/i.test(dlg), 'Security keys has no Remove button');
check(/wireSecKeys\(\);/.test(fn('start')), 'start() wires the Security keys button');
check(/showKeyOffer\(code, d\)/.test(fn('pair')), 'pair() renders the key offer with every fresh code');

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('selftest_keypair: ok');
