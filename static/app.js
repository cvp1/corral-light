// Corral front end: renders server state from an /api/state snapshot plus one SSE stream.
// Panes re-render from their own event list, so a reload matches watching live.

const $ = s => document.querySelector(s);
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c;
                          if (x !== undefined) n.textContent = x; return n; };

function loadDetail() {
  try {
    const v = JSON.parse(localStorage.getItem('corral.detail') || '[]');
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
const S = { panes: new Map(), agents: [], focus: null, es: null, railShut: null,
            detail: new Set(loadDetail()) };
const saveDetail = () =>
  localStorage.setItem('corral.detail', JSON.stringify([...S.detail]));

/* ── theme ───────────────────────────────────────────────────────────── */
// Contrast-checked palettes (see style.css). Applied before first paint, so there
// is no flash of the wrong one.
const THEMES = [
  ['laundry', ['#e7e9e5', '#3a4660', '#d09a73'], 'Laundry', 'Cool linen & French blue', 'light'],
  ['dusk', ['#202632', '#8299c3', '#e3a173'], 'Dusk', 'Soft twilight & apricot', 'dark'],
  ['nocturne', ['#080c17', '#455db0', '#ff6f8b'], 'Nocturne', 'Indigo, violet & rose', 'dark'],
  ['umber', ['#1d1510', '#8f5031', '#f0c66a'], 'Umber', 'Espresso, copper & gold', 'dark'],
  ['slate', ['#11171d', '#4d86aa', '#82d99a'], 'Slate', 'Steel, ice & signal green', 'dark'],
  ['ink', ['#090b0f', '#587fc2', '#a78bfa'], 'Ink', 'Graphite & spectral light', 'dark'],
  ['parchment', ['#f0ece3', '#2f5892', '#c49455'], 'Parchment', 'Warm paper, navy & ochre', 'light'],
  // Calm set: low-chroma accents.
  ['sagebrush', ['#e4e9e2', '#46664f', '#c9a98a'], 'Sagebrush', 'Pale sage, moss & clay', 'light'],
  ['fog', ['#e5e7ec', '#3d4a6a', '#b8a6cc'], 'Fog', 'Morning mist & lavender', 'light'],
  ['tidepool', ['#121c1e', '#5f9fa0', '#d9c58f'], 'Tidepool', 'Deep sea-glass & sand', 'dark'],
  ['mesa', ['#1c1a1f', '#8a7aa8', '#d9a78a'], 'Mesa', 'Desert dusk, sandstone & sage', 'dark'],
  // Calm set II.
  ['everforest', ['#2d353b', '#a7c080', '#e69875'], 'Everforest', 'Forest floor, moss & ember', 'dark'],
  ['kanagawa', ['#1f1f28', '#7e9cd8', '#e6c384'], 'Kanagawa', 'Sumi ink, wave blue & lantern', 'dark'],
  ['nord', ['#2e3440', '#88c0d0', '#a3be8c'], 'Nord', 'Arctic slate & frost', 'dark'],
  ['ethereal', ['#060b1e', '#7d82d9', '#ffcead'], 'Ethereal', 'Midnight navy & peach glow', 'dark'],
  ['rosedawn', ['#faf4ed', '#286983', '#d7827e'], 'Rosé Dawn', 'Soft dawn, pine & rose', 'light'],
  ['flexoki', ['#f2f0e5', '#24837b', '#ad8301'], 'Flexoki', 'Ink on paper, teal & ochre', 'light'],
];
function applyTheme(name) {
  const meta = THEMES.find(t => t[0] === name) || THEMES.find(t => t[0] === 'ink');
  name = meta[0];
  document.documentElement.setAttribute('data-theme', name);
  document.documentElement.style.colorScheme = meta[4];
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', meta[1][0]);
  localStorage.setItem('corral.theme', name);
  document.querySelectorAll('#themes .theme-option').forEach(b => {
    const on = b.dataset.palette === name;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', String(on));
  });
  const trigger = document.querySelector('#themes .appearance');
  if (trigger) {
    trigger.setAttribute('aria-label', `Appearance: ${meta[2]}`);
    trigger.title = `${meta[2]} — ${meta[3]}`;
    const mark = trigger.querySelector('.theme-mark');
    if (mark) paintThemeMark(mark, meta[1]);
  }
}
function paintThemeMark(mark, colors) {
  mark.innerHTML = '';
  for (const color of colors) {
    const s = document.createElement('span'); s.style.background = color;
    mark.appendChild(s);
  }
}
function wireThemes() {
  const box = document.querySelector('#themes');
  if (!box) return;
  box.innerHTML = '';
  const cur = localStorage.getItem('corral.theme') || 'ink';
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.className = 'appearance';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  const currentMark = document.createElement('span'); currentMark.className = 'theme-mark';
  trigger.append(currentMark, el('span', 'appearance-chevron', '⌃'));

  const menu = document.createElement('div');
  menu.className = 'theme-menu hide'; menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', 'Color palette');
  for (const [name, colors, label, note] of THEMES) {
    const b = document.createElement('button');
    // `data-theme` belongs only on <html>: here it would apply the full theme
    // block inside this option and paint its label in the wrong colours.
    b.type = 'button'; b.className = 'theme-option'; b.dataset.palette = name;
    b.setAttribute('role', 'option'); b.setAttribute('aria-selected', String(name === cur));
    const mark = document.createElement('span'); mark.className = 'theme-mark';
    paintThemeMark(mark, colors);
    const copy = document.createElement('span'); copy.className = 'theme-copy';
    copy.append(el('span', 'theme-name', label), el('span', 'theme-note', note));
    b.append(mark, copy, el('span', 'theme-check', '✓'));
    b.onclick = () => {
      applyTheme(name); menu.classList.add('hide');
      trigger.setAttribute('aria-expanded', 'false'); trigger.focus();
    };
    menu.appendChild(b);
  }
  trigger.onclick = () => {
    const open = menu.classList.toggle('hide') === false;
    trigger.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('.theme-option.on')?.focus();
  };
  box.onkeydown = e => {
    const options = [...menu.querySelectorAll('.theme-option')];
    const at = options.indexOf(document.activeElement);
    if (!menu.classList.contains('hide') && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      const next = e.key === 'Home' ? 0 : e.key === 'End' ? options.length - 1
        : (at + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options[next].focus(); return;
    }
    if (e.key === 'Escape' && !menu.classList.contains('hide')) {
      e.preventDefault(); menu.classList.add('hide');
      trigger.setAttribute('aria-expanded', 'false'); trigger.focus();
    }
  };
  box.onfocusout = e => {
    if (!box.contains(e.relatedTarget)) {
      menu.classList.add('hide'); trigger.setAttribute('aria-expanded', 'false');
    }
  };
  box.append(trigger, menu);
  applyTheme(cur);
}
// Default theme is 'ink'; a saved preference always wins.
applyTheme(localStorage.getItem('corral.theme') || 'ink');

/* ── toast ───────────────────────────────────────────────────────────── */
let toastT;
function toast(msg, bad) {
  const t = $('#toast'); t.textContent = msg;
  t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastT); toastT = setTimeout(() => t.className = 'toast', 5000);
}

/* ── transport ───────────────────────────────────────────────────────── */
async function api(path, body) {
  const r = await fetch(path, body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  } : {});
  let d = {}; try { d = await r.json(); } catch (e) { }
  // Never reload on 401: boot() 401s until paired, so a reload would loop forever.
  // Callers decide what a 401 means; this only reports it.
  if (!r.ok) {
    const err = new Error(d.error || `${r.status} ${r.statusText}`);
    err.status = r.status;
    // The whole answer rides along: a refused rig names every reason in
    // `refused`, and the message alone would drop all but the first.
    err.body = d;
    throw err;
  }
  return d;
}

// Show the pairing screen in place on an expired session; no navigation, so no reload loop.
function relock() {
  if (S.es) { S.es.close(); S.es = null; }
  $('#app').classList.add('hide');
  pair();
}

/* ── pairing ─────────────────────────────────────────────────────────── */
let pairTimers = [];
// The #pair= code from `corral-light launch` — in the fragment, so the browser
// never sends it in a request line — read once and removed from the URL so a
// reload or a later relock() never replays it.
function presetPairCode() {
  const m = /^#pair=([^&]*)$/.exec(location.hash || '');
  if (!m) return null;
  const raw = decodeURIComponent(m[1]).trim().toUpperCase();
  try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { }
  return /^[A-Z0-9]{3}-[A-Z0-9]{3}$/.test(raw) ? raw : null;
}
async function pair() {
  pairTimers.forEach(clearInterval); pairTimers = [];
  $('#pair').classList.remove('hide');
  let code, ttl, how, d;
  // `corral-light launch` opens /#pair=<code> with a code it already approved
  // (same proof as typing `corral-light pair`: the account owns the hub). Use
  // that code once and drop it from the address bar; an unknown or expired
  // code claims as 'expired' and falls through to a fresh one below.
  const preset = presetPairCode();
  if (preset) {
    code = preset; ttl = 300; how = `corral-light pair ${code}`;
  } else {
    try { d = await api('/api/pair/new'); ({ code, ttl, how } = d); }
    catch (e) { $('#pairnote').textContent = 'Cannot reach Corral Light: ' + e.message; return; }
  }
  $('#paircode').textContent = code;
  showKeyOffer(code, d);
  // The command comes from the server: Light and the full Corral have different CLI names.
  $('#paircmd').textContent = how || `corral-light pair ${code}`;
  let left = ttl;
  const tick = setInterval(() => {
    left--;
    $('#pairttl').textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
    if (left <= 0) { pairTimers.forEach(clearInterval); pair(); }   // fresh code, no reload
  }, 1000);
  const poll = setInterval(async () => {
    let d;
    try { d = await api('/api/pair/claim?code=' + encodeURIComponent(code)); }
    catch (e) { return; }                       // transient; keep polling
    if (d.status === 'ok') {
      pairTimers.forEach(clearInterval); pairTimers = [];
      $('#pair').classList.add('hide');
      return start();                           // straight in, no navigation
    }
    if (d.status === 'expired') { pairTimers.forEach(clearInterval); pair(); }
  }, 1500);
  pairTimers.push(tick, poll);
}

/* ── pairing by key (DESIGN-6 S8) ────────────────────────────────────────
 * "Touch your key" is a second way past the same screen; the code never
 * goes away. It is offered only when all three hold: a key is enrolled for
 * the origin this page is on (the hub says so, and names the origin it holds
 * ceremonies to), the page is a secure context, and the browser has
 * WebAuthn. A key is bound to its rpId, so on 127.0.0.1 -- same hub,
 * different origin -- the page links to localhost at the same port instead
 * of showing a button whose ceremony the hub would refuse.
 *
 * This is a convenience, not a boundary: anything running as the hub's own
 * UNIX user can read session.key and forge a cookie without any key.
 */
const LOOPBACK = new Set(['127.0.0.1', '[::1]']);
let keyDeclined = false;     // NotAllowedError once -> the code, for this page load

function b64url(buf) {
  const u = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = '';
  for (const b of u) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function unb64url(str) {
  if (typeof str !== 'string' || !/^[A-Za-z0-9_-]*$/.test(str) || str.length % 4 === 1)
    throw new Error('not base64url');
  const s = atob(str.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((str.length + 3) % 4));
  const u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
}

// Where a key ceremony can happen, from this page: 'here', 'link' (to the
// hub's localhost origin, from loopback on the same port), or 'none'.
function keyPlace(keyOrigin, loc, win) {
  if (!keyOrigin) return { kind: 'none', why: 'this address has no key origin configured' };
  if (keyOrigin !== loc.origin) {
    const link = `http://localhost:${loc.port}`;
    if (LOOPBACK.has(loc.hostname) && keyOrigin === link) return { kind: 'link', link };
    return { kind: 'none', why: `keys work at ${keyOrigin}, not at this address` };
  }
  if (!win.isSecureContext) return { kind: 'none', why: 'this page is not a secure context' };
  if (typeof win.PublicKeyCredential !== 'function')
    return { kind: 'none', why: 'this browser has no WebAuthn' };
  return { kind: 'here' };
}

// What the pairing screen offers besides the code (T8.1, T8.3).
function keyOffer(d, loc, win) {
  if (!d || !d.keyAvailable) return { kind: 'none' };
  return keyPlace(d.keyOrigin, loc, win);
}

function requestOptions(o) {
  return { challenge: unb64url(o.challenge), rpId: o.rpId, timeout: o.timeout,
           userVerification: o.userVerification,
           allowCredentials: (o.allowCredentials || []).map(
             id => ({ type: 'public-key', id: unb64url(id) })) };
}

function creationOptions(o) {
  return { challenge: unb64url(o.challenge), rp: o.rp,
           user: { id: unb64url(o.user.id), name: o.user.name, displayName: o.user.displayName },
           pubKeyCredParams: o.pubKeyCredParams, timeout: o.timeout,
           excludeCredentials: (o.excludeCredentials || []).map(
             id => ({ type: 'public-key', id: unb64url(id) })),
           authenticatorSelection: o.authenticatorSelection, attestation: o.attestation };
}

function assertionBody(challenge, cred) {
  const r = cred.response;
  return { challenge, id: b64url(cred.rawId), clientDataJSON: b64url(r.clientDataJSON),
           authenticatorData: b64url(r.authenticatorData), signature: b64url(r.signature) };
}

function showKeyOffer(code, d) {
  const box = $('#pairkey');
  const offer = keyDeclined ? { kind: 'none' } : keyOffer(d, location, window);
  box.replaceChildren();
  box.classList.toggle('hide', offer.kind === 'none');
  $('#pairhow').textContent = offer.kind === 'here'
    ? 'Or pair from a shell you trust:' : 'Run this in a shell you trust:';
  if (offer.kind === 'here') {
    const b = el('button', 'btn go keybtn', 'Touch your key');
    b.type = 'button';
    b.onclick = () => pairByKey(code, b);
    box.appendChild(b);
  } else if (offer.kind === 'link') {
    const a = el('a', 'keylink', `Use your security key at ${offer.link}`);
    a.href = offer.link;
    box.appendChild(a);
  }
}

// One ceremony per click. NotAllowedError (cancelled, timed out, wrong key)
// falls back to the code ONCE: the button goes away for this page load and
// nothing calls the authenticator again on its own (T8.4).
async function pairByKey(code, btn) {
  const note = $('#pairkeynote');
  btn.disabled = true;
  note.textContent = 'Touch your key…';
  try {
    const o = await api('/api/pair/key/begin', { code });
    const cred = await navigator.credentials.get({ publicKey: requestOptions(o) });
    await api('/api/pair/key/finish', assertionBody(o.challenge, cred));
  } catch (e) {
    if (e && e.name === 'NotAllowedError') {
      keyDeclined = true;
      $('#pairkey').replaceChildren();
      $('#pairkey').classList.add('hide');
      $('#pairhow').textContent = 'Run this in a shell you trust:';
      note.textContent = 'The key was not used. Pair with the code instead.';
      return;
    }
    btn.disabled = false;
    note.textContent = `Key pairing failed: ${e.message}. The code still works.`;
    return;
  }
  note.textContent = '';
  pairTimers.forEach(clearInterval); pairTimers = [];
  $('#pair').classList.add('hide');
  return start();                             // straight in, as a claimed code does
}

/* ── Security keys (DESIGN-6 S8) ─────────────────────────────────────────
 * The list and Enroll. No Remove: removal and policy are shell-only. The
 * first key for an origin needs the one-time code from
 * `corral-light key enroll`; a later key is approved by touching an enrolled
 * one, which the hub asks for as a second ceremony.
 */
function secKeyState(d) {
  const parts = [`Policy: ${d.policy}`];
  if (d.policyError) parts.push(`policy file: ${d.policyError}`);
  if (d.keysError) parts.push(`key store: ${d.keysError}`);
  parts.push(d.verifier === 'ok' ? 'verifier: ok'
             : `verifier: ${d.verifier}${d.verifierWhy ? ` (${d.verifierWhy})` : ''}`);
  return parts.join(' · ');
}

function whenText(t) {
  return t ? new Date(t * 1000).toLocaleString() : 'never';
}

function secKeyRows(d) {
  if (!d.keys.length) return [el('p', 'hint', 'No keys enrolled.')];
  return d.keys.map(k => {
    const row = el('div', 'skrow');
    row.append(el('span', 'sklabel', k.label || 'security key'),
               el('span', 'skorigin', k.origin),
               el('span', 'skused', `last used: ${whenText(k.lastUsed)}`));
    row.title = k.id;
    return row;
  });
}

async function renderSecKeys() {
  let d;
  try { d = await api('/api/pair/key/list'); }
  catch (e) {
    $('#sk-state').textContent = '';
    $('#sk-list').replaceChildren(el('p', 'hint err', `Cannot read the keys: ${e.message}`));
    $('#sk-enroll').disabled = true;
    return;
  }
  $('#sk-state').textContent = secKeyState(d);
  $('#sk-list').replaceChildren(...secKeyRows(d));
  const first = !d.keys.some(k => k.origin === d.origin);
  $('#sk-coderow').classList.toggle('hide', !first);
  const place = keyPlace(d.origin, location, window);
  const btn = $('#sk-enroll');
  btn.disabled = place.kind !== 'here';
  if (place.kind === 'link') {
    const a = el('a', 'keylink', `Enroll at ${place.link}`);
    a.href = place.link;
    $('#sk-msg').replaceChildren(a);
  } else if (place.kind === 'none') {
    $('#sk-msg').textContent = `Enroll is unavailable: ${place.why}.`;
  }
}

async function enrollKey() {
  const msg = $('#sk-msg'), btn = $('#sk-enroll');
  btn.disabled = true;
  try {
    msg.textContent = 'Touch the new key…';
    const o = await api('/api/pair/key/enroll/begin', { code: $('#sk-code').value.trim() });
    const made = await navigator.credentials.create({ publicKey: creationOptions(o) });
    const r = await api('/api/pair/key/enroll/finish', {
      challenge: o.challenge, label: $('#sk-label').value,
      clientDataJSON: b64url(made.response.clientDataJSON),
      attestationObject: b64url(made.response.attestationObject) });
    if (r.approve) {
      msg.textContent = 'Now touch a key that is already enrolled, to approve the new one…';
      const a = await navigator.credentials.get({ publicKey: requestOptions(r.approve) });
      await api('/api/pair/key/enroll/approve', assertionBody(r.approve.challenge, a));
    }
    $('#sk-code').value = '';
    $('#sk-label').value = '';
    msg.textContent = 'Enrolled.';
  } catch (e) {
    msg.textContent = e && e.name === 'NotAllowedError'
      ? 'The key was not used; nothing was enrolled.' : `Not enrolled: ${e.message}`;
  }
  btn.disabled = false;
  await renderSecKeys();
}

async function openSecKeys() {
  $('#sk-msg').textContent = '';
  await renderSecKeys();
  $('#seckeydlg').showModal();
}

function wireSecKeys() {
  const b = $('#seckeysbtn');
  if (b) b.onclick = () => openSecKeys();
  const e = $('#sk-enroll');
  if (e) e.onclick = () => enrollKey();
}

/* ── rendering: a pane ───────────────────────────────────────────────── */
// A host:<name> shell lane renders as a terminal: monospace, prompt-prefixed, no bubbles.
const isTerm = p => (p.agent || '').startsWith('host:');

/* ── markdown ────────────────────────────────────────────────────────────
 * DOM built only from createElement/textContent, never innerHTML; links only
 * http(s). Must render partial input (an unclosed fence) since it runs every tick. */
function mdInline(s) {
  const frag = document.createDocumentFragment();
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) frag.append(s.slice(last, m.index));
    if (m[1]) frag.append(el('code', null, m[1].slice(1, -1)));
    else if (m[2]) frag.append(el('strong', null, m[2].slice(2, -2)));
    else if (m[3]) frag.append(el('em', null, m[3].slice(1, -1)));
    else if (m[4]) {
      const a = el('a', null, m[4].slice(1, m[4].indexOf(']')));
      a.href = m[5]; a.target = '_blank'; a.rel = 'noopener noreferrer';
      frag.append(a);
    }
    last = re.lastIndex;
  }
  if (last < s.length) frag.append(s.slice(last));
  return frag;
}
function md(text) {
  const box = el('div', 'msg md');
  const lines = String(text).split('\n');
  let i = 0, para = [], list = null;
  const closeList = () => { list = null; };
  const flushPara = () => {
    if (!para.length) return;
    const p = el('p'); p.append(mdInline(para.join('\n'))); box.append(p);
    para = [];
  };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^```/.test(ln)) {                      // fence; unclosed = to the end
      flushPara(); closeList();
      const code = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      i++;                                       // past the closing fence
      const pre = el('pre'); pre.append(el('code', null, code.join('\n')));
      box.append(pre); continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(ln);
    if (h) { flushPara(); closeList();
             const d = el('div', 'mdh mdh' + h[1].length); d.append(mdInline(h[2]));
             box.append(d); i++; continue; }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(ln)) {
      flushPara(); closeList(); box.append(el('hr')); i++; continue;
    }
    const li = /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/.exec(ln);
    if (li) {
      flushPara();
      const ordered = /^\s*\d/.test(ln);
      const tag = ordered ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); box.append(list); }
      const item = el('li'); item.append(mdInline(li[1])); list.append(item);
      i++; continue;
    }
    if (/^\s*\|.*\|\s*$/.test(ln)) {             // pipe table
      flushPara(); closeList();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) {   // skip the ruler row
          rows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|'));
        }
        i++;
      }
      const tb = el('table');
      rows.forEach((cells, r) => {
        const tr = el('tr');
        for (const c of cells) {
          const td = el(r === 0 ? 'th' : 'td'); td.append(mdInline(c.trim()));
          tr.append(td);
        }
        tb.append(tr);
      });
      const wrap = el('div', 'mdtbl'); wrap.append(tb); box.append(wrap);
      continue;
    }
    if (!ln.trim()) { flushPara(); closeList(); i++; continue; }
    closeList(); para.push(ln); i++;
  }
  flushPara();
  return box;
}

// The latest still-pending permission's full data, resolved from the
// transcript (p.pending carries only requestIds). Feeds the composer's
// number keys and Esc — the native-Claude dialog keys.
function pendingPerm(p) {
  for (let i = p.events.length - 1; i >= 0; i--) {
    const e = p.events[i];
    if (e.kind === 'permission' &&
        (p.pending || []).includes((e.data || {}).requestId)) return e.data;
  }
  return null;
}
// The options offered for a request: oversize keeps only refusals, matching permCard,
// so key N matches button N. A function declaration so a selftest can lift it by name.
function permOptions(d) {
  return (d.options || []).filter(o => !d.oversize || String(o.kind || '').startsWith('reject'));
}

function renderLog(p) {
  // Tool calls, plans and lifecycle noise collapse to one expandable line per run;
  // permissions and real errors are never collapsed.
  const log = el('div', 'log');
  const term = isTerm(p);
  const detailed = S.detail.has(p.id);
  let textBuf = null, thoughtBuf = null, pendingSteps = [], stepIx = new Map();
  // Expanded ⎿ rows persist across per-tick rebuilds; pruned below to rendered tool ids.
  const xopen = TEXPAND.get(p.id) || new Set();
  const xseen = new Set();

  // `/clear` hides everything up to its own marker; p.events keeps it all. The ACP
  // adapter drops the SDK's reset, so sessions.py writes the marker itself.
  let clearIx = -1;
  for (let i = p.events.length - 1; i >= 0; i--) {
    if (p.events[i].kind === 'cleared') { clearIx = i; break; }
  }
  const visible = clearIx >= 0 ? p.events.slice(clearIx + 1) : p.events;

  // Older events live only on disk: offer to load them 200 at a time. Skipped after
  // a clear, since everything earlier is pre-clear.
  const first = visible[0];
  if (clearIx < 0 && first && first.seq > 1) {
    const more = el('button', 'fbtn more',
                    `⋯ load earlier (${first.seq - 1} before this)`);
    more.onclick = async () => {
      more.disabled = true; more.textContent = 'loading…';
      try {
        const d = await api(`/api/session/history?pane=${p.id}` +
                            `&before=${first.seq}&n=200`);
        const evs = d.events || [];
        if (!evs.length) { more.textContent = 'nothing earlier on disk'; return; }
        p.events = evs.concat(p.events);
        render();
      } catch (e) { toast(e.message, true); more.disabled = false;
                    more.textContent = '⋯ load earlier'; }
    };
    log.appendChild(more);
  }

  // ACP streams tool_call_updates for the same toolCallId: merge into one row per call,
  // copying only fields an update carries (latest-wins would null the title).
  const pushStep = rec => {
    const k = rec.id;
    if (k && stepIx.has(k)) {
      const cur = pendingSteps[stepIx.get(k)];
      // Empty arrays carry nothing too: contentless updates send content:[].
      for (const [f, v] of Object.entries(rec))
        if (v != null && v !== '' && !(Array.isArray(v) && !v.length)) cur[f] = v;
      return;
    }
    if (k) stepIx.set(k, pendingSteps.length);
    pendingSteps.push(rec);
  };
  // Tool rows: ⏺ title with status as dot colour, then up to three ⎿ preview lines;
  // the row expands on click.
  const stepNode = r => {
    if (r.kind === 'plan' && Array.isArray(r.entries)) return planNode(r);
    const t = el('div', 'tool');
    const head = el('div', 'trow');
    head.append(
      el('span', 'tdot ' + (r.status === 'completed' ? 'ok' :
                            r.status === 'failed' ? 'bad' : 'run'), '⏺'),
      el('b', null, r.title || r.kind || 'tool'));
    t.append(head);
    const texts = [];
    for (const c of r.content || []) {
      const inner = (c && c.content) || c;
      if (inner && typeof inner.text === 'string' && inner.text) texts.push(inner.text);
    }
    const locs = (r.locations || []).map(l => l && l.path).filter(Boolean);
    const full = (texts.join('\n').trim() || locs.join('\n')).replace(/\s+$/, '');
    if (full) {
      const lines = full.split('\n');
      const res = el('div', 'tres');
      const elbow = el('span', 'tel', '⎿ ');
      const body = el('span');
      const paint = () => {
        const open = r.id && xopen.has(r.id);
        body.textContent = open || lines.length <= 3 ? full
          : lines.slice(0, 3).join('\n') + `\n… +${lines.length - 3} lines`;
      };
      paint();
      res.append(elbow, body);
      if (lines.length > 3 && r.id) {
        res.classList.add('x');
        res.title = 'click to expand';
        res.onclick = () => {
          xopen.has(r.id) ? xopen.delete(r.id) : xopen.add(r.id);
          paint();
        };
      }
      t.append(res);
    }
    return t;
  };
  // The agent's todo list, one glyph per entry as the native TUI draws it.
  const planNode = r => {
    const t = el('div', 'tool planbox');
    const head = el('div', 'trow');
    head.append(el('span', 'tdot run', '⏺'), el('b', null, 'Plan'));
    t.append(head);
    for (const en of r.entries) {
      const st = en.status || 'pending';
      const row = el('div', 'plent ' + st);
      row.append(el('span', 'pglyph',
                    st === 'completed' ? '☑' : st === 'in_progress' ? '◉' : '☐'),
                 el('span', null, en.content || ''));
      t.append(row);
    }
    return t;
  };
  const stepName = r => String(r.title || r.kind || 'tool').split(' ')[0];

  const flushThought = () => {
    if (thoughtBuf && thoughtBuf.trim()) {
      const d = el('div', 'thought');
      d.append(el('span', 'tmark', '∴ '), thoughtBuf.trim());
      log.appendChild(d);
    }
    thoughtBuf = null;
  };
  const flushText = () => {
    flushThought();
    if (textBuf && textBuf.trim()) {
      log.appendChild(term ? el('div', 'tout', textBuf) : md(textBuf));
    }
    textBuf = null;
  };
  const flushSteps = () => {
    if (!pendingSteps.length) return;
    const steps = pendingSteps; pendingSteps = []; stepIx = new Map();
    if (detailed) {
      for (const r of steps) log.appendChild(stepNode(r));
      return;
    }
    const names = [...new Set(steps.map(stepName))];
    const line = el('div', 'steps');
    line.append(el('span', 'cx', '▸'),
                `${steps.length} step${steps.length > 1 ? 's' : ''} · ` +
                names.slice(0, 3).join(', ') + (names.length > 3 ? '…' : ''));
    let open = false;
    const holder = el('div', 'detail');
    line.onclick = () => {
      open = !open;
      line.firstChild.textContent = open ? '▾' : '▸';
      holder.innerHTML = '';
      if (open) for (const r of steps) holder.appendChild(stepNode(r));
    };
    log.append(line, holder);
  };
  const flush = () => { flushText(); flushSteps(); };

  // Bound how many events become DOM per render (p.events keeps them all); a click
  // raises the cap for that pane. Thoughts don't count against it while hidden.
  const renderable = detailed ? visible : visible.filter(e => e.kind !== 'thought');
  const cap = LOG_CAP.get(p.id) || DEFAULT_LOG_CAP;
  const hidden = Math.max(0, renderable.length - cap);
  const events = hidden ? renderable.slice(-cap) : renderable;
  if (hidden) {
    const more = el('div', 'sys', `▲ ${hidden} earlier event${hidden > 1 ? 's' : ''} not shown — click to show more`);
    more.style.cursor = 'pointer';
    more.onclick = () => { LOG_CAP.set(p.id, cap + 1000); render(); };
    log.appendChild(more);
  }

  // Resolve each permission's outcome from the transcript. Pair by position, not by
  // requestId alone: agents reuse ids, so bind each card to the first outcome after
  // it; whatever is still open at the end is the one the agent is blocked on.
  const permOutcomes = new Map();   // permission event seq -> its outcome event
  const permOpen = new Map();       // requestId -> seq of its unanswered card
  for (const e of visible) {
    const rid = (e.data || {}).requestId;
    if (e.kind === 'permission') {
      permOpen.set(rid, e.seq);
    } else if (e.kind === 'permission_answered' || e.kind === 'permission_expired') {
      const open = permOpen.get(rid);
      if (open !== undefined) { permOutcomes.set(open, e); permOpen.delete(rid); }
    }
  }

  for (const e of events) {
    const d = e.data || {};
    switch (e.kind) {
      case 'text': flushSteps(); textBuf = (textBuf || '') + (d.text || ''); break;
      // Thoughts render only in detail mode; text is flushed first to keep stream order.
      case 'thought':
        if (detailed) { flushSteps(); flushText();
                        thoughtBuf = (thoughtBuf || '') + (d.text || ''); }
        break;
      case 'user':
        flush();
        if (term) {
          const c = el('div', 'tcmd');
          c.append(el('span', 'pr', '❯'), ' ', d.text || '');
          log.appendChild(c);
        } else {
          const u = el('div', 'msg user', d.text || '');
          // A turn sent by a script (consult, the CLI) is marked; `via` is the
          // caller's label, not proof of origin.
          if (d.via) {
            u.classList.add('via');
            u.prepend(el('span', 'viatag', 'via ' + d.via));
            u.title = `sent by ${d.via}, not typed here`;
          }
          log.appendChild(u);
        }
        break;
      // A message from another pane's agent: its own block, never the human bubble,
      // since it is untrusted. textContent only, so markup renders literally.
      case 'peer':
        flush();
        {
          const b = el('div', 'msg peer');
          b.appendChild(el('div', 'peerfrom',
            `from @${d.from_seat || d.from_pane || '?'}` + (d.hop > 1 ? ` · hop ${d.hop}` : '')));
          b.appendChild(el('div', 'peerbody', d.text || ''));
          // The hub, not the sender, flagged a claim of the human's approval
          // in the body: say plainly that it is the other agent's word.
          if (d.approval_claim) {
            b.appendChild(el('div', 'peerclaim',
              `⚠ unverified: claims your approval ("${d.approval_claim}"). Another agent cannot give it.`));
          }
          // The sender label is the supported path, not proof: any process of the
          // same user can send as that pane.
          b.title = `Sent by the agent in @${d.from_seat || d.from_pane || '?'} through `
                  + `Corral's seat tool. Not proof of origin: any process running `
                  + `as this user could send as that pane.`;
          log.appendChild(b);
        }
        break;
      case 'peer_result':
        if (d.delivered === false) {
          flush();
          log.appendChild(el('div', 'sys', `that message was not run — ${d.reason || 'unknown'}`));
        }
        break;
      case 'peer_queue':
        // A reply held for a pane waiting on its sender, recorded on both panes;
        // on the receiving side `delivered` prints nothing (the `peer` block does).
        if (!(d.side === 'to' && d.status === 'delivered')) {
          flush();
          const who = d.side === 'from' ? `to @${d.to_seat || '?'}`
                                        : `from @${d.from_seat || d.from_pane || '?'}`;
          const what = d.status === 'queued'
            ? (d.side === 'from' ? `queued until @${d.to_seat || '?'}'s turn ends`
                                 : 'queued until this turn ends')
            : `${d.status || '?'}` + (d.reason ? ` — ${d.reason}` : '');
          log.appendChild(el('div', 'sys', `message ${who}: ${what}`));
        }
        break;
      // ask_human: the agent's question for its human. Its own block, labelled
      // as the agent asking -- never the human bubble, never a system line.
      case 'question':
        flush();
        {
          const hub = d.source === 'hop-limit';
          const b = el('div', 'msg question' + (hub ? ' hub' : ''));
          b.appendChild(el('div', 'qwho', hub ? 'Corral paused this loop — not the agent'
            : d.replaces ? 'the agent asks you, replacing its earlier question'
                         : 'the agent asks you'));
          b.appendChild(el('div', 'qtext', d.text || ''));
          log.appendChild(b);
        }
        break;
      // A message this pane sent was refused at the pane-to-pane limit. When
      // it raised the hub's question, that block says it; otherwise (the
      // agent's own question was open) this line is the record.
      case 'peer_paused':
        if (!d.raised) {
          flush();
          log.appendChild(el('div', 'sys',
            `message to @${d.to_seat || '?'} refused at the pane-to-pane limit`));
        }
        break;
      case 'peer_chain_reset':
        flush();
        log.appendChild(el('div', 'sys', 'a human answered the loop pause — the message limit restarts'));
        break;
      case 'question_cleared':
        flush();
        log.appendChild(el('div', 'sys', d.reason === 'answered'
          ? 'question answered' : `question closed — ${d.reason || 'unknown'}`));
        break;
      case 'tool':
        flushText();
        if (d.id) xseen.add(d.id);
        pushStep({ id: d.id, title: d.title, kind: d.kind, status: d.status,
                   content: d.content, locations: d.locations });
        break;
      case 'plan':
        flushText();
        // One row, updated in place: each plan event replaces the list. The \u0000
        // prefix keeps a real toolCallId named "plan" from merging into it.
        pushStep({ id: '\u0000plan', kind: 'plan', entries: d.entries || [],
                   title: `plan · ${(d.entries || []).length} steps`, status: '' });
        break;
      // Never collapsed — the two things that must not be missed.
      case 'permission': flush();
        // Live only if this is the still-open card for that id AND the id is pending.
        log.appendChild(permCard(p, d, permOutcomes.get(e.seq),
                                 permOpen.get(d.requestId) === e.seq &&
                                 p.pending.includes(d.requestId)));
        break;
      case 'dead': {
        flush();
        const line = log.appendChild(el('div', 'sys err', `agent stopped — ${d.reason || 'unknown'}`));
        // Only on the death the pane is still in: an old one in the history
        // must not offer a button for a login that already came back.
        if (d.cause === 'auth' && p.agent === 'claude' && p.state === 'dead' &&
            p.deadCause === 'auth') line.appendChild(signInButton('banner', p.id));
        break;
      }
      // Quiet unless you asked for detail.
      case 'permission_answered':
        if (detailed) { flush(); log.appendChild(el('div', 'sys', `you chose \u201c${d.optionId}\u201d`)); }
        break;
      case 'closed':
        if (detailed) { flush(); log.appendChild(el('div', 'sys', 'closed')); }
        break;
      case 'cancelled': flush(); log.appendChild(el('div', 'sys', 'cancelled')); break;
      case 'turn_end': flush(); break;
    }
  }
  flush();
  // Prune held-open state to tool ids still in the rendered slice — the Set
  // stays bounded by the display cap, never a growing history.
  for (const k of [...xopen]) if (!xseen.has(k)) xopen.delete(k);
  TEXPAND.set(p.id, xopen);
  if (p.state === 'busy') {
    // Native spinner shape; the seconds advance in place (workingTick), so a
    // quiet pane needs no rebuild to keep counting.
    let t0 = null;
    for (let i = visible.length - 1; i >= 0; i--) {
      if (visible[i].kind === 'user' || visible[i].kind === 'peer') { t0 = visible[i].at; break; }
    }
    const w = el('div', 'sys working');
    if (t0) w.dataset.t0 = t0;
    // The hint is also the control: click it to stop, same as Esc or \u25a0 Stop.
    const hint = el('span', 'wstop', '(esc to interrupt)');
    hint.title = 'stop this turn';
    hint.onclick = () => api('/api/session/cancel', { pane: p.id })
      .catch(err => toast(err.message, true));
    w.append(el('span', 'wstar', '\u2733'), el('span', 'wtext', workingLabel(t0)), hint);
    log.appendChild(w);
  }
  return log;
}

function workingLabel(t0) {
  const secs = t0 ? Math.max(0, Math.round((Date.now() - new Date(t0)) / 1000)) : null;
  return ' working\u2026' + (secs != null ? ` ${secs}s` : '') + '  ';
}

/* Once a second: advance every visible spinner's elapsed time without touching
 * the rest of the log. */
function workingTick() {
  for (const w of document.querySelectorAll('.pane > .log .working[data-t0]')) {
    const t = w.querySelector('.wtext');
    if (t) t.textContent = workingLabel(w.dataset.t0);
  }
}

// The permission card: shows exactly what is being approved. `live` is passed in
// rather than derived from p.pending, because requestIds are reused and a stale
// card would carry a digest the server refuses.
function permCard(p, d, outcome, live) {
  const answered = !live;
  const c = el('div', 'perm');
  c.appendChild(el('div', 'h', `Wants to: ${d.title || d.kind || 'act'}`));

  // Render every diff and the whole rawInput: an approval proves only what was visible.
  if (d.oversize) {
    c.appendChild(el('div', 'why',
      `This request is ${Math.round((d.bytes || 0) / 1024)} KB — too large to ` +
      `display in full, so it was not kept. Approving what cannot be shown ` +
      `is not consent, so only refusal is offered here. Answer it in the ` +
      `agent's own surface if you need to allow it.`));
  } else {
    // Every content entry: the digest covers content + locations + rawInput, so
    // non-diff entries render as literal JSON rather than being hidden.
    const diffs = [];
    for (const item of d.content || []) {
      if (item && item.type === 'diff') {
        diffs.push(item);
        c.appendChild(el('div', 'why', item.path || ''));
        const pre = el('pre');
        for (const line of String(item.oldText || '').split('\n')) {
          if (line) pre.appendChild(el('span', 'del', '- ' + line + '\n'));
        }
        for (const line of String(item.newText || '').split('\n')) {
          if (line) pre.appendChild(el('span', 'add', '+ ' + line + '\n'));
        }
        const extra = Object.keys(item).filter(
          k => !['type', 'path', 'oldText', 'newText'].includes(k));
        if (extra.length) {
          const more = el('pre');
          more.textContent = JSON.stringify(
            Object.fromEntries(extra.map(k => [k, item[k]])), null, 1);
          pre.appendChild(more);
        }
        c.appendChild(pre);
      } else {
        const pre = el('pre');
        pre.textContent = typeof item === 'string'
          ? item : JSON.stringify(item, null, 1);
        c.appendChild(pre);
      }
    }
    const raw = el('pre');
    raw.textContent = JSON.stringify(d.rawInput || {}, null, 1);
    if (!diffs.length || (d.rawInput && Object.keys(d.rawInput).length)) {
      c.appendChild(raw);
    }
    for (const loc of d.locations || []) {
      if (!loc) continue;
      const keys = Object.keys(loc).filter(k => k !== 'path');
      c.appendChild(el('div', 'why', loc.path || ''));
      if (keys.length) {                  // line numbers, ranges, anything else
        const pre = el('pre');
        pre.textContent = JSON.stringify(loc, null, 1);
        c.appendChild(pre);
      }
    }
  }
  if (d.digest) {
    const g = el('div', 'why dig', `sha256 ${d.digest.slice(0, 16)}…`);
    g.title = `The approval is recorded against these exact bytes: ${d.digest}`;
    c.appendChild(g);
  }

  if (answered) {
    // Distinguish a decision from an expiry or a dropped request.
    if (outcome && outcome.kind === 'permission_expired') {
      const reason = (outcome.data || {}).reason || 'expired';
      c.appendChild(el('div', 'why expired', `expired, unanswered — ${reason}`));
    } else if (outcome && outcome.kind === 'permission_answered') {
      c.appendChild(el('div', 'why', `you chose “${outcome.data.optionId}”`));
    } else {
      c.appendChild(el('div', 'why', 'answered'));
    }
    return c;
  }

  const opts = el('div', 'opts');
  // permOptions drops granting options for an undisplayable payload; the number
  // keys share it, so key N and button N match.
  permOptions(d).forEach((o, i) => {
    const kind = String(o.kind || '');
    const b = el('button', 'pbtn ' + (kind.startsWith('allow') ? 'allow' :
                                      kind.startsWith('reject') ? 'reject' : ''));
    b.append(el('span', 'pnum', String(i + 1)), o.name || o.optionId);
    b.onclick = async () => {
      try {
        await api('/api/session/permission',
                  { pane: p.id, requestId: d.requestId, optionId: o.optionId,
                    digest: d.digest });
      } catch (e) { toast(e.message, true); }
    };
    opts.appendChild(b);
  });
  c.appendChild(opts);
  return c;
}

// Panes are built once and updated in place: rebuilding the composer on every SSE
// event would destroy the caret, text and focus while typing. Header and transcript
// rebuild freely; the composer does not.
const PANES = new Map();      // paneId -> {root, head, log, comp, kind}
const TEXPAND = new Map();    // paneId -> Set(toolCallId) with ⎿ held open
const LOG_CAP = new Map();    // paneId -> how many recent events render to DOM
const DEFAULT_LOG_CAP = 300;

function buildPane(p) {
  const root = el('div', 'pane');
  root.dataset.pane = p.id;
  const head = el('div', 'ph');
  const ask = el('div', 'askslot');       // ask_human's banner, when open
  const log = el('div', 'log');
  const comp = el('div', 'compslot');
  root.append(head, ask, log, comp);
  log.onscroll = () => {
    // Only whether the log is pinned to the bottom needs tracking.
    const rec = PANES.get(p.id);
    if (rec) rec.pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    // Near the top of a capped transcript, raise the cap (scroll-up loads more);
    // updatePane re-anchors by height delta so the view does not jump.
    const cap = LOG_CAP.get(p.id) || DEFAULT_LOG_CAP;
    if (rec && log.scrollTop < 80 && p.events.length > cap) {
      LOG_CAP.set(p.id, cap + 1000);
      updatePane(rec, p);
    }
  };
  return { root, head, ask, log, comp, kind: null, pinned: true };
}

function composerKind(p) {
  // A dead pane with a session to load takes messages too: sending resumes it.
  if (p.state === 'dead') return p.resumable ? 'live' : 'none';
  return p.state === 'detached' ? 'detached' : 'live';
}

async function resumePane(p) {
  try { await api('/api/session/resume', { pane: p.id }); await refresh(); }
  catch (e) { toast(e.message, true); }
}

/* Everything renderLog's output depends on, as one string. A render whose
 * signature matches the last paint leaves the log's DOM alone: with several
 * panes on the wall, one pane's stream must not rebuild the others. The busy
 * spinner's seconds are not in it; workingTick updates them in place. */
function logSignature(p) {
  const evs = p.events || [];
  return [evs.length, evs.length ? evs[0].seq : 0, evs.length ? evs[evs.length - 1].seq : 0,
          S.detail.has(p.id) ? 1 : 0, LOG_CAP.get(p.id) || DEFAULT_LOG_CAP,
          p.state, (p.pending || []).join(','), p.deadCause || '', p.agent,
          isTerm(p) ? 1 : 0].join('|');
}

/* The same for the header: title, state, pills and the buttons' modes. */
function headSignature(p) {
  const cfg = p.config || {}, u = p.usage || {};
  return [p.title, p.label, p.state, displayState(p), p.idleS >= 30 ? fmtAge(p.idleS) : '',
          p.seat, p.seatWithheld, p.posture, p.postureEnforced,
          (cfg.model || {}).value, (cfg.effort || {}).value, u.size, u.used,
          S.detail.has(p.id) ? 1 : 0, FIND.pane === p.id ? 1 : 0,
          p.portedFrom ? JSON.stringify(p.portedFrom) : '', p.cwd,
          JSON.stringify(wtPillModel(p.worktree)), JSON.stringify(paneMeta(p))].join('|');
}

function updatePane(rec, p) {
  // Hold the log still while a selection lives in it: a rebuild would delete the
  // selection, and re-anchoring by offset could land on different text.
  const live = liveLogSelection();
  const hold = SEL.down === p.id || (live && live.log === rec.log);
  const working = ['busy', 'uncertain', 'needs-you'].includes(p.state);
  rec.root.className = 'pane' + ((p.pending.length || p.question) ? ' attn' : '') +
                       (p.state === 'dead' ? ' dead' : '') +
                       (working ? ' working' : '') +
                       (hold ? ' selhold' : '') +
                       (isTerm(p) ? ' term' : '');
  const hsig = headSignature(p);
  if (hsig !== rec.headSig) {
    rec.headSig = hsig;
    rec.head.replaceChildren(...paneHead(p).childNodes);
  }
  const qb = questionBanner(p);
  if (rec.ask) rec.ask.replaceChildren(...(qb ? [qb] : []));

  // Rebuild the log only when what it shows changed; a held selection defers
  // the rebuild (the stale signature stays, so the next render catches up).
  if (!hold && logSignature(p) !== rec.logSig) {
    const wasPinned = rec.pinned;
    const oldHeight = rec.log.scrollHeight, oldTop = rec.log.scrollTop;
    rec.log.replaceChildren(...renderLog(p).childNodes);
    // If a capped transcript fits without overflow, raise the cap until it scrolls
    // or everything shows. Bounded so a pathological pane cannot loop.
    for (let guard = 0; guard < 5 &&
         rec.log.scrollHeight <= rec.log.clientHeight &&
         p.events.length > (LOG_CAP.get(p.id) || DEFAULT_LOG_CAP); guard++) {
      LOG_CAP.set(p.id, (LOG_CAP.get(p.id) || DEFAULT_LOG_CAP) + 1000);
      rec.log.replaceChildren(...renderLog(p).childNodes);
    }
    rec.logSig = logSignature(p);         // after the cap may have moved
    if (wasPinned) {
      rec.log.scrollTop = rec.log.scrollHeight;
    } else {
      // Scrolled up: each rebuild shrinks scrollHeight and the browser clamps
      // scrollTop, so re-anchor by the height delta instead.
      rec.log.scrollTop = Math.max(0, oldTop + (rec.log.scrollHeight - oldHeight));
    }
    // A rebuild abandons the find highlights' ranges; re-anchor on fresh DOM.
    if (FIND.pane === p.id) applyFind(p.id);
  }

  const kind = composerKind(p);
  if (kind !== rec.kind) {            // only a SHAPE change rebuilds it
    rec.kind = kind;
    rec.comp.replaceChildren(...(kind === 'none' ? [] : [composer(p, kind)]));
  }
  // A Stop left half-way (cancelled, or offering Force stop) re-arms once the
  // turn is really over, so the next turn starts with the polite press.
  if (!working) {
    const sb = rec.comp.querySelector('button.stop');
    if (sb && sb._reset) sb._reset();
  }
  return rec.root;
}

/* ── seats ───────────────────────────────────────────────────────────────
 * A name the operator gives a pane so other panes can address it; bound only
 * here, behind the pairing cookie. Edited in a dialog because the header
 * rebuilds on every event. */
function seatPill(p) {
  let b;
  if (p.seat) {
    b = el('button', 'pill seat', '@' + p.seat);
    b.title = `other panes can address this one as @${p.seat} — click to change or remove`;
  } else if (p.seatWithheld) {
    // Shown for what it is: the meta still says this name, but an open pane
    // created earlier already answers to it, so this one is not reachable.
    b = el('button', 'pill seat withheld', '@' + p.seatWithheld + ' withheld');
    b.title = `another open pane already answers to @${p.seatWithheld} and was `
            + `there first, so this one is not addressable by it — click to rebind`;
  } else {
    b = el('button', 'x seatadd', '＠');
    b.title = 'give this pane a seat — a name other panes can address it by';
  }
  b.type = 'button';
  b.onclick = () => openSeat(p);
  return b;
}

function openSeat(p) {
  const dlg = $('#seatdlg');
  if (!dlg) return toast('this page has no seat dialog', true);
  dlg._pane = p.id;
  $('#seat-for').textContent = p.title || p.label;
  $('#seat-name').value = p.seat || p.seatWithheld || '';
  $('#seat-unbind').disabled = !(p.seat || p.seatWithheld);
  dlg.showModal();
}

function wireSeat() {
  const dlg = $('#seatdlg');
  if (!dlg) return;
  // Enter binds: left to the browser it would submit via the first button (Cancel).
  // reportValidity() shows the grammar's message on a bad name.
  $('#seat-name').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if ($('#seat-name').reportValidity()) dlg.close('ok');
  });
  dlg.addEventListener('close', async () => {
    const how = dlg.returnValue;
    if (how !== 'ok' && how !== 'unbind') return;
    // Unbind posts "" -- the server's word for "no seat", not a name.
    const seat = how === 'unbind' ? '' : $('#seat-name').value.trim();
    try {
      const r = await api('/api/session/seat', { pane: dlg._pane, seat });
      toast(r.seat ? `this pane is now @${r.seat}` : 'seat removed');
      await refresh();
    } catch (e) { toast(e.message, true); }
  });
}

/* The pill for a lane whose posture Corral cannot set: harness rail (our adapter
 * asks before every write), chat only (no tools), or the vendor's own policy.
 * The vendor is the label's first word. */
function posturePill(p) {
  const vendor = String(p.label || p.agent || 'the agent')
    .split(/[\s(—-]/)[0] || p.label;
  let text, why;
  if (p.rail) {
    text = 'harness rail';
    why = `Corral cannot set a permission MODE on ${p.label}, but this lane `
        + `runs Corral's own adapter: it asks before every write or command, `
        + `with the exact bytes, and fails closed if nobody answers.`;
  } else if (p.tools === false) {
    text = 'chat only';
    why = `${p.label} has no tools, so it raises no permission requests. `
        + `An empty rail here is the lane's nature, not a rail that stopped `
        + `working.`;
  } else {
    text = `${vendor} policy`;
    why = `Corral cannot set the permission policy for ${p.label}. `
        + `Whatever that agent does by default is what you get.`;
  }
  const q = el('span', 'pill unknown', text);
  q.title = why;
  return q;
}

/* The pane header: one line of identity and controls, one quiet line of
 * facts. The eye needs the title and the state word at a glance; everything
 * else is plain text that reads only when looked for, not a row of badges.
 * Four controls stay visible (find, the ⋯ menu, minimize, close); every other
 * pane action lives in openPaneMenu, which the roster row opens too. */
function paneHead(p) {
  const h = el('div', 'ph');
  const row = el('div', 'ph1');
  const dsp = displayState(p);
  row.appendChild(el('span', 'dot d-' + dsp));
  const nm = el('span', 'nm', p.title || p.label);
  nm.title = p.title || p.label;
  row.appendChild(nm);
  // Display state, with the raw enum on the tooltip.
  const st = el('span', 'st d-' + dsp, DISPLAY_LABEL[dsp] || dsp);
  st.title = `${p.state}${p.idleS >= 30 ? ` · quiet ${fmtAge(p.idleS)}` : ''}`;
  row.appendChild(st);
  const ctl = el('span', 'ctl');
  // Literal smart-case find, every match highlighted; Enter/Shift+Enter walk them.
  const fnd = el('button', 'x find' + (FIND.pane === p.id ? ' fon' : ''), '⌕');
  fnd.type = 'button'; fnd.title = 'find in this conversation';
  fnd.onclick = () => toggleFind(p);
  const more = el('button', 'x more', '⋯');
  more.type = 'button'; more.title = 'more — every step, rename, pin, seat, pause…';
  more.setAttribute('aria-haspopup', 'menu');
  more.onclick = e => openPaneMenu(p, e.currentTarget);
  const min = el('button', 'x', '–'); min.type = 'button';
  min.title = 'minimize (keeps running)';
  min.onclick = () => setMin(p, true);
  const x = el('button', 'x close', '✕'); x.type = 'button'; x.title = 'close';
  x.onclick = async () => { try { await api('/api/session/close', { pane: p.id }); await refresh(); }
                            catch (e) { toast(e.message, true); } };
  ctl.append(fnd, more, min, x);
  row.appendChild(ctl);
  h.appendChild(row);

  // The facts, in the order they are asked for: who, where, on what, how.
  const facts = el('div', 'ph2');
  const fact = node => { node.classList.add('f'); facts.appendChild(node); return node; };
  const sp = seatPill(p);                     // the unseated ＠ lives in the menu instead
  if (p.seat || p.seatWithheld) fact(sp);
  const pm = paneMeta(p);
  const meta = el('span', 'meta', pm.text);
  if (pm.title) meta.title = pm.title;
  fact(meta);
  const wp = wtPillModel(p.worktree);        // own branch: opens review
  if (wp) {
    const b = el('button', wp.cls, wp.text);
    b.type = 'button'; b.title = wp.title;
    b.onclick = () => openReview(p);
    fact(b);
  }
  for (const cid of ['model', 'effort']) {
    const cfg = (p.config || {})[cid];
    if (!cfg || !cfg.value) continue;
    const b = el('button', 'cfg', (cid === 'effort' ? '⚡ ' : '') + cfg.value);
    b.type = 'button';
    b.title = `${cfg.name || cid} — click to change`;
    b.onclick = async () => {
      const opts = cfg.options || [];
      if (!opts.length) return;
      const cur = opts.findIndex(o => o.value === cfg.value);
      const next = opts[(cur + 1) % opts.length];          // cycle; the list is short
      try { await api('/api/session/config', { pane: p.id, configId: cid, value: next.value }); }
      catch (e) { toast(e.message, true); }
    };
    fact(b);
  }
  // Only claim a posture Corral actually imposed.
  if (p.postureEnforced === false) {
    fact(posturePill(p));
  } else {
    const q = el('span', p.posture, p.posture);
    q.title = { auto: 'auto — a classifier handles routine prompts',
                edits: 'edits — file edits auto-accepted, the rest asks',
                strict: 'strict — prompts on dangerous operations' }[p.posture] || p.posture;
    fact(q);
  }
  // ACP usage_update is unstable and may be missing or zero: show nothing rather
  // than a misleading "0% ctx".
  const u = p.usage || {};
  if (u.size > 0 && Number.isFinite(u.used)) {
    const pct = Math.round(100 * u.used / u.size);
    const warn = pct >= 75;                    // matches CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
    const c = el('span', 'ctx' + (warn ? ' warn' : ''), `${pct}% ctx`);
    c.title = `${u.used.toLocaleString()} / ${u.size.toLocaleString()} tokens in context`;
    fact(c);
  }
  if (p.portedFrom) {
    const pf = p.portedFrom;
    const tag = el('span', 'unknown', `⇄ from ${pf.agent}`);
    tag.title = `transcript carried from pane ${pf.pane} on ${pf.host} — the model ` +
                `read it, it does not remember it` + (pf.delivered === false ? ' (NOT delivered)' : '');
    fact(tag);
  }
  h.appendChild(facts);
  return h;
}

/* ── the pane menu ───────────────────────────────────────────────────────
 * Every pane action that is not find, minimize or close, in one place. The
 * header's ⋯ drops it; a roster row opens the same menu on right-click or
 * its hover button. Built from fresh state when opened, so nothing in it
 * needs a slot in headSignature. `at` is the button it hangs from, or a
 * {x, y} point for a right-click. */
function closePaneMenu() {
  for (const n of document.querySelectorAll('.pmenu, .pmenu-veil')) n.remove();
}

function openPaneMenu(p, at) {
  closePaneMenu();
  p = S.panes.get(p.id) || p;
  const veil = el('div', 'pmenu-veil');
  const m = el('div', 'pmenu');
  m.setAttribute('role', 'menu');
  const head = el('div', 'mh', p.title || p.label);
  head.title = p.title || p.label;
  m.appendChild(head);
  const item = (label, run, o = {}) => {
    const b = el('button', 'mi' + (o.danger ? ' danger' : ''));
    b.type = 'button'; b.setAttribute('role', 'menuitem');
    b.appendChild(el('span', 'chk', o.on ? '✓' : ''));
    b.appendChild(el('span', 'ml', label));
    if (o.key) b.appendChild(el('kbd', 'mk', o.key));
    if (o.title) b.title = o.title;
    b.onclick = async () => {
      closePaneMenu();
      try { await run(); } catch (e) { toast(e.message, true); }
    };
    m.appendChild(b);
    return b;
  };
  const sep = () => m.appendChild(el('div', 'msep'));

  const dead = p.state === 'dead', detached = p.state === 'detached';
  const on = S.detail.has(p.id);
  item(on ? 'Showing every step' : 'Show every step', () => {
    on ? S.detail.delete(p.id) : S.detail.add(p.id);
    saveDetail(); render();
  }, { on, title: on ? 'click to collapse tool calls' : 'expand every tool call and thought' });
  if (p.worktree && p.worktree.phase === 'active') {
    item('Review changes…', () => openReview(p), { key: 'r' });
  }
  if (!isTerm(p)) item('Carry to another lane…', () => openPort(p));
  sep();
  item('Rename…', () => { S.renaming = p.id; render(); });
  item(p.pinned ? 'Unpin' : 'Pin to the top', async () => {
    await api('/api/session/pin', { pane: p.id, pinned: !p.pinned }); await refresh();
  });
  item(p.seat ? `Seat · @${p.seat}…` : p.seatWithheld ? `Seat · @${p.seatWithheld} withheld…`
                                                      : 'Give it a seat…',
       () => openSeat(p),
       { title: 'a name other panes can address this one by' });
  sep();
  if (!dead && !detached) {
    item('Pause', async () => { await api('/api/session/pause', { pane: p.id }); await refresh(); },
         { title: 'stop the agent, keep the conversation' });
  }
  if ((dead && p.resumable) || detached) {
    item('Resume', () => resumePane(p), { title: 'restart the agent on this conversation' });
  }
  item(p.minimized ? 'Restore' : 'Minimize', () => setMin(p, !p.minimized));
  if (dead) item('Dismiss', () => forgetPane(p), { danger: true, title: 'remove it from the list' });
  item('Close', async () => { await api('/api/session/close', { pane: p.id }); await refresh(); },
       { danger: true, title: 'end the agent and file the conversation' });

  veil.onclick = closePaneMenu;
  veil.oncontextmenu = e => { e.preventDefault(); closePaneMenu(); };
  m.onkeydown = e => {
    const items = [...m.querySelectorAll('.mi')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); closePaneMenu(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
  };
  document.body.append(veil, m);
  // Place it, then keep it on screen.
  const pad = 8, vw = window.innerWidth, vh = window.innerHeight;
  let x, y;
  if (at && at.getBoundingClientRect) {
    const r = at.getBoundingClientRect();
    x = r.right - m.offsetWidth; y = r.bottom + 4;
  } else {
    x = at.x; y = at.y;
  }
  x = Math.max(pad, Math.min(x, vw - m.offsetWidth - pad));
  y = Math.max(pad, Math.min(y, vh - m.offsetHeight - pad));
  m.style.left = x + 'px'; m.style.top = y + 'px';
  m.querySelector('.mi')?.focus();
}

/* ── own branches ────────────────────────────────────────────────────────
 * A pane on its own git worktree and branch (docs/worktree-review-plan.md
 * §2.5): the New dialog's row, the header pill, the review dialog and the
 * rail card. Everything here builds nodes with el()/textContent; nothing in
 * this block parses HTML (T-UI-8). */
const WT_KEY = 'corral.ownBranch';         // repo top -> the checkbox, last time
const WT_SEEN_KEY = 'corral.wtSeen';       // pane id -> summary digest last reviewed
function shortSha(s) { return String(s || '').slice(0, 7); }
function homeTilde(s) {                   // anywhere in the text: warnings embed paths
  return String(s || '').replace(/(^|[\s'"(])\/(?:home|Users)\/[^/\s'"]+/g, '$1~');
}
function shq(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }

function wtStore(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch { return {}; }
}
function wtRemembered(top) { return wtStore(WT_KEY)[top] === true; }
function wtRemember(top, on) {
  const m = wtStore(WT_KEY);
  if (on) m[top] = true; else delete m[top];
  localStorage.setItem(WT_KEY, JSON.stringify(m));
}
function wtSeen() { return wtStore(WT_SEEN_KEY); }
function wtMarkSeen(id, summary) {
  if (!summary || !summary.digest) return;
  const m = wtSeen();
  m[id] = summary.digest;
  localStorage.setItem(WT_SEEN_KEY, JSON.stringify(m));
}

/* What the New dialog's own-branch row shows. Hidden outside a repo and for a
 * lane that cannot take an own branch; a refused repo shows its reasons where
 * the checkbox would be. */
function wtRowModel(probe, laneRefusal, remembered) {
  if (!probe || !probe.inside || laneRefusal) return { show: false };
  const refusals = probe.refusals || [];
  if (refusals.length) return { show: true, checkbox: false, refusal: refusals.join(' · '),
                                warnings: [] };
  const branch = String(probe.branch || '').replace(/^refs\/heads\//, '');
  const from = probe.detached ? `a detached HEAD @ ${shortSha(probe.head)}`
                              : `${branch || 'HEAD'} @ ${shortSha(probe.head)}`;
  return { show: true, checkbox: true, checked: !!remembered, top: probe.top,
           cut: `cut from ${from}`, warnings: (probe.warnings || []).map(homeTilde) };
}

/* The body's `worktree` key: sent only when the box is checked AND visible,
 * and only for the folder and lane the probe answered about. */
function wtSubmit(model, checked, cwd, agent) {
  if (!checked || !model || !model.show || !model.checkbox) return {};
  if (model.cwd !== cwd || model.agent !== agent)
    return { error: 'the folder or lane changed after it was checked for an own branch; ' +
                    'open New again' };
  return { worktree: true };
}

const WTP = { t: null, seq: 0 };
function wtProbeSoon(dlg, ms) {
  clearTimeout(WTP.t);
  WTP.t = setTimeout(() => wtProbe(dlg), ms);
}
async function wtProbe(dlg) {
  const cwd = $('#f-cwd').value.trim();
  const agent = dlg._chosenAgent ? dlg._chosenAgent() : '';
  const seq = ++WTP.seq;
  let d = null;
  if (cwd) {
    try { d = await api('/api/session/worktree/probe?cwd=' + encodeURIComponent(cwd)); }
    catch { d = null; }
  }
  if (seq !== WTP.seq) return;              // a newer probe was asked for
  const lanes = (d && d.laneRefusals) || {};
  const pr = d && d.probe;
  const m = wtRowModel(pr, agent in lanes ? lanes[agent] : 'unknown lane',
                       !!(pr && pr.top && wtRemembered(pr.top)));
  m.cwd = cwd; m.agent = agent;
  dlg._wt = m;
  paintWtRow(m);
}
function paintWtRow(m) {
  const row = $('#wtrow');
  if (!row) return;
  row.classList.toggle('hide', !m.show);
  $('#wt-check').classList.toggle('hide', !m.checkbox);
  $('#f-wt').checked = !!m.checked;
  $('#wt-cut').textContent = m.cut || '';
  const hint = $('#wthint');
  hint.textContent = m.refusal ? 'Own branch unavailable: ' + m.refusal
                               : (m.warnings || []).join(' ');
  hint.classList.toggle('err', !!m.refusal);
}

/* The header pill: `⎇ fix-login · 4 files +120 −8`. */
function wtPillModel(w) {
  if (!w) return null;
  const name = String(w.branch || '').replace(/^corral\//, '') || '(branch)';
  const s = w.summary;
  let text = '⎇ ' + name;
  if (w.phase === 'trashed') text += ' · discarded';
  else if (w.phase !== 'active') text += ' · ' + (w.phase || 'unknown');
  else if (s) text += s.files ? ` · ${s.files} file${s.files === 1 ? '' : 's'} +${s.added} −${s.deleted}`
                              : ' · no changes';
  const odd = w.phase !== 'active' && w.phase !== 'trashed';
  const gone = w.phase === 'trashed';    // discarded on purpose: an end state, not a fault
  const cls = 'pill wt' + ((w.blocked && !gone) || odd ? ' warn' : '') + (gone ? ' gone' : '');
  const title = [`own branch ${w.branch || ''}, cut from ${w.base || 'its base'} @ ${shortSha(w.baseSha)}`,
                 `worktree: ${w.path || '?'}`, w.blocked || '',
                 w.phase === 'active' ? 'click, or press r on the focused pane, to review' : '']
    .filter(Boolean).join('\n');
  return { text, cls, title };
}

/* `.meta`: the repo the user chose, not the worktree it runs in (tooltip). */
function paneMeta(p) {
  const w = p.worktree;
  if (!w || !w.repo) return { text: `${p.label} · ` + homeTilde(p.cwd), title: '' };
  const sub = w.subdir ? '/' + w.subdir : '';
  return { text: `${p.label} · ` + homeTilde(w.repo + sub),
           title: `running on its own branch in ${w.path}${sub}` };
}
function paneDir(p) {
  const w = p.worktree;
  return String((w && w.repo) || p.cwd || '').split('/').pop();
}

/* A worktree pane whose turn it is, with changes the user has not reviewed. */
function wtRailCards(panes, seen) {
  return panes.filter(p => {
    const w = p.worktree, s = w && w.summary;
    return !!(w && w.phase === 'active' && s && s.files > 0 &&
              displayState(p) === 'your-turn' && s.digest !== seen[p.id]);
  });
}

/* `r` opens review: no modifier, not in a text field, not over a dialog. */
function reviewKey(e, target, dialogOpen) {
  return e.key === 'r' && !e.metaKey && !e.ctrlKey && !e.altKey &&
         !isTypingTarget(e.target) && !!target && !dialogOpen;
}
function anyDialogOpen() { return !!document.querySelector('dialog[open]'); }
function reviewTarget() {
  const p = S.panes.get(S.focus);
  return p && p.worktree && p.worktree.phase === 'active' ? p : null;
}

/* One file's unified patch as hunks of numbered lines. Pure (Node selftest). */
function parseUnified(patch) {
  const out = { head: [], hunks: [], binary: false, rename: null, mode: null };
  if (patch == null) return out;
  const rows = String(patch).split('\n');
  if (rows.length && rows[rows.length - 1] === '') rows.pop();
  let h = null, o = 0, n = 0, from = null;
  for (const raw of rows) {
    const cr = raw.endsWith('\r');
    const line = cr ? raw.slice(0, -1) : raw;
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/.exec(line);
    if (m) {
      o = +m[1]; n = +m[2];
      h = { header: line, oldStart: o, newStart: n, context: m[3].trim(), lines: [] };
      out.hunks.push(h);
      continue;
    }
    if (!h) {                               // the file header, before any hunk
      out.head.push(line);
      let x;
      if (/^Binary files .* differ$/.test(line) || line === 'GIT binary patch') out.binary = true;
      else if ((x = /^rename from[ ](.*)$/.exec(line))) from = x[1];
      else if ((x = /^rename to[ ](.*)$/.exec(line))) out.rename = { from, to: x[1] };
      else if ((x = /^old mode[ ](\d+)$/.exec(line))) out.mode = { old: x[1], new: null };
      else if ((x = /^new mode[ ](\d+)$/.exec(line)) && out.mode) out.mode.new = x[1];
      continue;
    }
    const c = line[0];
    if (c === '+') h.lines.push({ t: 'add', text: line.slice(1), old: null, new: n++, cr });
    else if (c === '-') h.lines.push({ t: 'del', text: line.slice(1), old: o++, new: null, cr });
    else if (c === '\\') h.lines.push({ t: 'nonl', text: line.slice(2), old: null, new: null, cr: false });
    else h.lines.push({ t: 'ctx', text: line.slice(1), old: o++, new: n++, cr });
  }
  return out;
}

/* Which review buttons work, and why not when they do not. */
function reviewActions(snap, o) {
  const no = why => ({ ok: false, why });
  const yes = { ok: true, why: '' };
  if (!snap) {
    const w = no('waiting for the snapshot');
    return { commit: w, publish: w, discard: w, copy: w, refresh: yes };
  }
  if (o.busy) {
    const w = no('another action is running');
    return { commit: w, publish: w, discard: w, copy: w, refresh: w };
  }
  const uncommitted = snap.tree !== snap.head_tree;
  const ahead = snap.head !== snap.base_sha;
  const commit = !uncommitted ? no('nothing to commit: the reviewed files match the last commit')
               : !String(o.message || '').trim() ? no('write a commit message first')
               : { ok: true, why: '', note: 'Commits exactly the files shown, with git commit-tree: ' +
                   "this repo's commit hooks, such as pre-commit and commit-msg, do not run. " +
                   'Its pre-push hooks still run when you push.' };
  const remote = (snap.remotes || [])[o.remote || 0];
  const urls = remote ? remote.pushUrls || [] : [];
  // Left out for size, yet still untracked work: the hub refuses to push past it.
  const big = (snap.too_big || []).map(b => b.path);
  const bigN = big.length + (snap.too_big_omitted || 0);   // the review may have trimmed the list
  const them = bigN === 1 ? 'it' : 'them';
  const publish = uncommitted ? no('commit first: Push sends commits, not uncommitted files')
                : bigN ? no(`${big.length ? big.slice(0, 3).join(', ') : `${bigN} files`}` +
                                  `${bigN > Math.min(big.length, 3) && big.length ? ' and more' : ''} ` +
                                  `${bigN === 1 ? 'is' : 'are'} untracked and too large to ` +
                                  `commit; move ${them} out or add ${them} to .gitignore first`)
                : !ahead ? no('nothing to push: this branch has no commits past its base')
                : !remote ? no('this repo has no remote to push to')
                : urls.length !== 1 ? no(`${remote.name} has ${urls.length} push URLs; publishing needs exactly one`)
                : yes;
  const copy = ahead ? yes : no('nothing to merge yet: commit first');
  return { commit, publish, discard: yes, copy, refresh: yes };
}

/* Request bodies, from the snapshot on screen: the server refuses if the
 * files moved since (409 `changed`), so what is posted is what was shown. */
function commitBody(pane, snap, message) {
  return { pane, tree: snap.tree, head: snap.head, index_id: snap.index_id, message };
}
function publishBody(pane, snap, remote, pr) {
  return { pane, oid: snap.head, tree: snap.tree, remote: remote.name,
           push_url: remote.pushUrls[0], pr: pr || null };
}
function discardBody(pane, snap) { return { pane, tree: snap.tree }; }
function mergeCommand(w) { return `git -C ${shq(w.repo)} merge --no-ff ${shq(w.branch)}`; }

/* Run one action: busy while it runs; a 409 re-freezes the snapshot and keeps
 * the server's reason on screen; anything else is shown as it came. */
async function reviewRun(R, ops) {
  R.busy = true; ops.paint();
  try {
    const r = await ops.call();
    R.reason = '';
    await ops.after(r);
  } catch (e) {
    const why = (e.body && e.body.error) || e.message;
    if (e.status === 409) await ops.reload();
    R.reason = e.status === 409 ? `${why} (refreshed: the review now shows the current files)` : why;
  } finally {
    R.busy = false; ops.paint();
  }
}

function reviewBanners(snap) {
  const out = [];
  const n = (snap.ignored || {}).count || 0;
  if (n) out.push(`${n} ignored file${n === 1 ? ' is' : 's are'} not in this review; ` +
                  `Discard keeps ${n === 1 ? 'it' : 'them'} in trash.`);
  const big = snap.too_big || [];
  const bigN = big.length + (snap.too_big_omitted || 0);
  if (bigN) out.push('Left out for size, untracked and over 512 KiB: ' +
    (big.length ? big.slice(0, 5).map(b => b.path).join(', ') : `${bigN} file${bigN === 1 ? '' : 's'}`) +
    (big.length && bigN > 5 ? ` and ${bigN - Math.min(big.length, 5)} more` : '') +
    '. Commit leaves them out too.');
  const diff = snap.diff || {};
  const bin = (diff.files || []).filter(f => f.binary).length;
  if (bin) out.push(`${bin} binary file${bin === 1 ? '' : 's'}, often build output such as ` +
                    '__pycache__; listed first.');
  if (diff.truncated) out.push('The diff hit its size limit, so some files show no lines. ' +
                               'Their changes are still in what you commit.');
  const sd = snap.staged_differs || [];
  if (sd.length) out.push('Staged differently from the file on disk: ' +
    sd.slice(0, 5).join(', ') + (sd.length > 5 ? ` and ${sd.length - 5} more` : '') +
    '. Commit takes the files as they are on disk and keeps the staged version as a recovery ref.');
  if (diff.files_omitted) out.push(`${diff.files_omitted} more changed file` +
    `${diff.files_omitted === 1 ? ' is' : 's are'} not listed: the review hit its size limit, and ` +
    'they are still in what you commit.');
  return out;
}

function fileRow(f, onclick) {
  const b = el('button', 'rrow');
  b.type = 'button';
  b.dataset.path = f.path;
  b.append(el('span', 'rst s-' + f.status, f.status), el('span', 'rpath', f.path),
           el('span', 'rnum', f.binary ? 'bin' : f.add == null ? '' : `+${f.add} −${f.del}`));
  b.title = f.old_path ? `${f.old_path} → ${f.path}` : f.path;
  b.onclick = onclick;
  return b;
}

/* One file's section. Binary, over-cap and cut-off files are one placeholder
 * line, never a node per line (T-UI-14). */
function diffNodes(f) {
  const box = el('section', 'rfile');
  box.dataset.path = f.path;
  const h = el('div', 'rfh');
  h.appendChild(el('span', 'rst s-' + f.status, f.status));
  h.appendChild(el('span', 'rpath', f.old_path ? `${f.old_path} → ${f.path}` : f.path));
  if (!f.binary && f.add != null) h.appendChild(el('span', 'rnum', `+${f.add} −${f.del}`));
  box.appendChild(h);
  const note = f.binary ? 'binary file, not shown'
             : f.too_big ? 'too large to show: over 512 KiB'
             : f.patch == null ? 'not shown: the review hit its size limit' : null;
  if (note) { box.appendChild(el('div', 'rnote', note)); return box; }
  const d = parseUnified(f.patch);
  if (d.mode) box.appendChild(el('div', 'rnote', `mode ${d.mode.old} → ${d.mode.new}`));
  if (d.binary) { box.appendChild(el('div', 'rnote', 'binary file, not shown')); return box; }
  if (!d.hunks.length && !d.mode) box.appendChild(el('div', 'rnote', 'no line changes'));
  const sign = { add: '+', del: '-', ctx: ' ', nonl: '\\ ' };
  for (const hk of d.hunks) {
    box.appendChild(el('div', 'rhunk', hk.header));
    for (const l of hk.lines) {
      const row = el('div', 'rl ' + l.t);
      const t = el('span', 'lt', sign[l.t] + l.text);
      if (l.cr) {
        const cr = el('span', 'cr', '␍');
        cr.title = 'this line ends in CRLF (a Windows line ending)';
        t.appendChild(cr);
      }
      row.append(el('span', 'ln', l.old == null ? '' : String(l.old)),
                 el('span', 'ln', l.new == null ? '' : String(l.new)), t);
      box.appendChild(row);
    }
  }
  return box;
}

/* The review dialog. R.snap is what is on screen; every action posts from it. */
const R = { pane: null, snap: null, busy: false, loading: false, reason: '', done: null,
            painted: null };

// Dismiss a dead pane. An own-branch pane asks first (F7): the hub answers
// 409 own_branch, and the user picks review (to Discard) or keep-and-dismiss.
async function forgetPane(p) {
  try { await api('/api/session/forget', { pane: p.id }); await refresh(); return; }
  catch (err) {
    if (!(err.status === 409 && err.body && err.body.reason === 'own_branch')) {
      toast(err.message, true); return;
    }
    // Review opens only on an active branch; any other phase goes straight to keep-and-dismiss.
    if (p.worktree && p.worktree.phase === 'active' &&
        confirm(err.message + '\n\nOpen review to discard the branch?')) { openReview(p); return; }
    if (!confirm('Dismiss the pane and keep the branch? Reopen it from the archive to get back to it.')) return;
    try { await api('/api/session/forget', { pane: p.id, keep_branch: true }); await refresh(); }
    catch (e2) { toast(e2.message, true); }
  }
}

async function openReview(p) {
  const dlg = $('#revdlg');
  if (!dlg || !p || !p.worktree) return;
  if (p.worktree.phase !== 'active')
    return toast(p.worktree.blocked || 'this branch is not active, so there is nothing to review', true);
  Object.assign(R, { pane: p.id, snap: null, busy: false, reason: '', done: null, painted: null });
  $('#rev-msg').value = '';
  $('#rev-filter').value = '';
  $('#rev-pr').checked = false;
  $('#rev-prtitle').value = p.title || '';
  if (!dlg.open) dlg.showModal();
  await loadReview();
}

async function loadReview() {
  R.loading = true; paintReview();
  try {
    R.snap = await api('/api/session/worktree/snapshot', { pane: R.pane });
    wtMarkSeen(R.pane, R.snap.summary);
    scheduleRender();                      // the rail card goes once reviewed
  } catch (e) {
    R.snap = null;
    R.reason = (e.body && e.body.error) || e.message;
  } finally {
    R.loading = false; paintReview();
  }
}

function applyReviewFilter() {
  const q = $('#rev-filter').value.trim().toLowerCase();
  for (const box of [$('#rev-list'), $('#rev-diff')])
    for (const c of box.children)
      c.classList.toggle('hide', !!q && !String(c.dataset.path || '').toLowerCase().includes(q));
}

function paintReview() {
  const dlg = $('#revdlg');
  if (!dlg) return;
  const p = S.panes.get(R.pane);
  const w = (p && p.worktree) || {};
  const snap = R.snap;
  $('#rev-title').textContent = 'Review ⎇ ' + String(w.branch || '').replace(/^corral\//, '');
  const files = snap ? [...((snap.diff || {}).files || [])]
    .sort((a, b) => (b.binary ? 1 : 0) - (a.binary ? 1 : 0)) : [];
  $('#rev-sub').textContent = R.loading ? 'freezing a snapshot…'
    : snap ? `${files.length} file${files.length === 1 ? '' : 's'} against ` +
             `${w.base || 'the base'} @ ${shortSha(snap.base_sha)} · ` +
             (snap.tree !== snap.head_tree ? 'not committed yet'
              : (snap.too_big || []).length ? 'committed, except files left out for size'
              : 'all committed')
    : '';
  const rr = $('#rev-reason');
  rr.textContent = R.reason;
  rr.classList.toggle('hide', !R.reason);
  const done = $('#rev-done');
  done.replaceChildren();
  if (R.done) {
    done.appendChild(el('span', null, R.done.text));
    if (R.done.url && /^https:\/\//.test(R.done.url)) {
      const a = el('a', null, R.done.url);
      a.href = R.done.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      done.append(el('span', null, ' · '), a);
    }
  }
  $('#rev-banners').replaceChildren(...(snap ? reviewBanners(snap) : [])
    .map(t => el('p', 'revbanner', t)));
  if (R.painted !== snap) {                 // rebuild the diff only for a new snapshot
    R.painted = snap;
    const diff = $('#rev-diff');
    $('#rev-list').replaceChildren(...files.map(f => fileRow(f, () => {
      const box = [...diff.children].find(c => c.dataset.path === f.path);
      if (box) box.scrollIntoView({ block: 'start' });
    })));
    diff.replaceChildren(...files.map(diffNodes));
    if (snap && !files.length) diff.appendChild(el('div', 'rnote', 'no changes against the base'));
    const sel = $('#rev-remote');
    sel.replaceChildren(...((snap && snap.remotes) || []).map((r, i) => {
      const o = el('option', null, r.name); o.value = String(i); return o;
    }));
    applyReviewFilter();
  }
  const remotes = (snap && snap.remotes) || [];
  const remote = remotes[+$('#rev-remote').value || 0];
  $('#rev-remoterow').classList.toggle('hide', !remotes.length);
  $('#rev-url').textContent = !remote ? 'this repo has no remote to push to'
    : remote.pushUrls.length === 1 ? remote.pushUrls[0] : `${remote.pushUrls.length} push URLs`;
  const gh = remote && remote.pushUrls.length === 1 ? remote.githubRepo : null;
  $('#rev-prrow').classList.toggle('hide', !gh);
  $('#rev-prrepo').textContent = gh || '';
  const prOn = !!gh && $('#rev-pr').checked;
  $('#rev-prtitlerow').classList.toggle('hide', !prOn);
  $('#rev-publish').textContent = prOn ? 'Push & open PR' : 'Push';
  const ra = reviewActions(snap, { message: $('#rev-msg').value, busy: R.busy || R.loading,
                                   remote: +$('#rev-remote').value || 0 });
  for (const [id, k] of [['#rev-commit', 'commit'], ['#rev-publish', 'publish'],
                         ['#rev-discard', 'discard'], ['#rev-copy', 'copy'],
                         ['#rev-refresh', 'refresh']]) {
    $(id).disabled = !ra[k].ok;
    $(id).title = ra[k].ok ? ra[k].note || '' : ra[k].why;
  }
  $('#rev-why').textContent = snap && !R.busy
    ? [!ra.commit.ok && `Commit: ${ra.commit.why}.`, !ra.publish.ok && `Push: ${ra.publish.why}.`]
        .filter(Boolean).join(' ')
    : '';
}

function wireReview() {
  const dlg = $('#revdlg');
  if (!dlg) return;
  const run = (call, after) => reviewRun(R, { call, after, reload: loadReview, paint: paintReview });
  // Enter in a field must not submit the form (its first submit button is Close).
  for (const id of ['#rev-msg', '#rev-filter', '#rev-prtitle']) {
    $(id).addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (id === '#rev-msg' && !$('#rev-commit').disabled) $('#rev-commit').click();
    });
  }
  $('#rev-msg').oninput = paintReview;
  $('#rev-filter').oninput = applyReviewFilter;
  $('#rev-remote').onchange = paintReview;
  $('#rev-pr').onchange = paintReview;
  $('#rev-refresh').onclick = () => { R.reason = ''; R.done = null; loadReview(); };
  $('#rev-commit').onclick = () => run(
    () => api('/api/session/worktree/commit',
              commitBody(R.pane, R.snap, $('#rev-msg').value.trim())),
    async r => {
      R.done = { text: r.noop ? 'nothing to commit: the files match the last commit'
                              : `committed ${shortSha(r.commit)}` };
      $('#rev-msg').value = '';
      await loadReview();
    });
  $('#rev-publish').onclick = () => {
    const remote = ((R.snap && R.snap.remotes) || [])[+$('#rev-remote').value || 0];
    if (!remote) return;
    const pr = remote.githubRepo && $('#rev-pr').checked
      ? { repo: remote.githubRepo, title: $('#rev-prtitle').value.trim(), body: '' } : null;
    run(() => api('/api/session/worktree/publish', publishBody(R.pane, R.snap, remote, pr)),
        async r => {
          R.done = { text: `pushed ${shortSha(r.pushed)} to ${remote.name}`,
                     url: r.pr_url || r.compare_url || null };
          await loadReview();
        });
  };
  $('#rev-copy').onclick = e => {
    const p = S.panes.get(R.pane);
    if (p && p.worktree) copyText(mergeCommand(p.worktree), e.clientX, e.clientY);
  };
  $('#rev-discard').onclick = () => {
    if (!confirm('Discard this branch? Its agent stops. The branch is kept as a recovery ' +
                 'ref and its files move to trash; nothing is deleted.')) return;
    run(() => api('/api/session/worktree/discard', discardBody(R.pane, R.snap)),
        async r => {
          dlg.close();
          toast(`branch discarded: kept as ${r.recovery_ref}`);
          await refresh();
        });
  };
}
/* ── end own branches ─────────────────────────────────────────────────── */

/* ── copy & find ─────────────────────────────────────────────────────────
 * Release-to-copy, double-click word copy, selections that survive live output
 * (the hold in updatePane), and highlighted find. */
const SEL = { down: null };            // paneId a drag started in, until mouseup

// The element-or-null a node's enclosing pane log, for scoping copy-on-select
// to transcripts (never the composer, the rail, or the library article).
function logOf(n) {
  const e = n && (n.nodeType === 1 ? n : n.parentElement);
  return e && e.closest ? e.closest('.pane > .log') : null;
}

function liveLogSelection() {
  const s = window.getSelection();
  if (!s || s.isCollapsed || !s.rangeCount) return null;
  const log = logOf(s.getRangeAt(0).commonAncestorContainer);
  return log ? { sel: s, log } : null;
}

async function copyText(text, x, y) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; }
  catch { try { ok = document.execCommand('copy'); } catch { /* stays false */ } }
  if (ok) copyFlash(x, y); else toast('copy failed — clipboard unavailable', true);
}

// A small "copied" that drifts up from the cursor and fades; toasts are for problems.
function copyFlash(x, y) {
  const f = el('div', 'copyflash', 'copied');
  f.style.left = x + 'px'; f.style.top = y + 'px';
  document.body.appendChild(f);
  requestAnimationFrame(() => f.classList.add('go'));
  setTimeout(() => f.remove(), 900);
}

function wireCopySelect() {
  document.addEventListener('mousedown', e => {
    const log = logOf(e.target);
    SEL.down = log ? log.parentElement.dataset.pane : null;
  });
  document.addEventListener('mouseup', e => {
    SEL.down = null;
    // Let the browser finish building the selection first — on a double-click
    // the word is not selected yet when mouseup fires.
    setTimeout(() => {
      const live = liveLogSelection();
      if (!live) {                     // drag ended empty — release any hold
        if (document.querySelector('.pane.selhold')) render();
        return;
      }
      const text = live.sel.toString();
      if (text.trim()) copyText(text, e.clientX, e.clientY);
    }, 0);
  });
  document.addEventListener('selectionchange', () => {
    // The held pane catches up the moment the selection stops existing.
    if (document.querySelector('.pane.selhold') && !liveLogSelection() && !SEL.down)
      render();
  });
}

/* Find-in-pane. One find at a time — CSS.highlights is a page-global
 * registry, and two panes fighting over 'find' would highlight lies. */
const FIND = { pane: null };
const FIND_CAP = 2000;                 // bound the ranges

// Smart case: any capital in the query makes it case-sensitive.
function smartCase(q) { return q !== q.toLowerCase(); }

// Every start offset of literal needle q in hay. Non-overlapping, so "aa" in
// "aaa" is one match, same as a terminal search walks it.
function findOffsets(hay, q, cs) {
  if (!q) return [];
  const h = cs ? hay : hay.toLowerCase(), n = cs ? q : q.toLowerCase();
  const out = [];
  for (let i = h.indexOf(n); i !== -1; i = h.indexOf(n, i + n.length)) out.push(i);
  return out;
}

function clearFindPaint() {
  if (window.CSS && CSS.highlights) {
    CSS.highlights.delete('find'); CSS.highlights.delete('findcur');
  }
}

function toggleFind(p) {
  const rec = PANES.get(p.id); if (!rec) return;
  if (FIND.pane === p.id) return closeFind();
  if (FIND.pane) closeFind();
  FIND.pane = p.id;
  if (!rec.findbar) {
    // Built once and kept: a rebuild under the SSE stream would eat the query.
    const bar = el('div', 'findbar');
    const inp = el('input'); inp.type = 'search'; inp.placeholder = 'find in transcript…';
    const ct = el('span', 'fct');
    const prev = el('button', 'x', '↑'); prev.title = 'previous match (Shift+Enter)';
    const next = el('button', 'x', '↓'); next.title = 'next match (Enter)';
    const shut = el('button', 'x', '✕'); shut.title = 'close (Esc)';
    prev.onclick = () => findStep(p.id, -1);
    next.onclick = () => findStep(p.id, 1);
    shut.onclick = closeFind;
    inp.oninput = () => { rec.findQ = inp.value; rec.findCur = 0; applyFind(p.id); };
    inp.onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); findStep(p.id, e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { e.preventDefault(); closeFind(); }
    };
    bar.append(inp, ct, prev, next, shut);
    rec.findbar = bar;
  }
  rec.root.insertBefore(rec.findbar, rec.log);
  rec.findbar.classList.remove('hide');
  rec.findbar.querySelector('input').focus();
  applyFind(p.id);
  render();                            // the ⌕ in the head lights up
}

function closeFind() {
  const rec = FIND.pane && PANES.get(FIND.pane);
  FIND.pane = null;
  clearFindPaint();
  if (rec && rec.findbar) rec.findbar.classList.add('hide');
  render();
}

function applyFind(id) {
  const rec = PANES.get(id); if (!rec || FIND.pane !== id) return;
  const q = rec.findQ || '';
  const ct = rec.findbar.querySelector('.fct');
  rec.findRanges = [];
  clearFindPaint();
  if (!q) { ct.textContent = ''; return; }
  const cs = smartCase(q);
  const walker = document.createTreeWalker(rec.log, NodeFilter.SHOW_TEXT);
  let n, capped = false;
  outer: while ((n = walker.nextNode())) {
    for (const i of findOffsets(n.data, q, cs)) {
      const r = document.createRange();
      r.setStart(n, i); r.setEnd(n, i + q.length);
      rec.findRanges.push(r);
      if (rec.findRanges.length >= FIND_CAP) { capped = true; break outer; }
    }
  }
  rec.findCur = Math.min(rec.findCur || 0, Math.max(0, rec.findRanges.length - 1));
  paintFind(rec, ct, capped);
}

function paintFind(rec, ct, capped) {
  const rs = rec.findRanges;
  ct.textContent = rs.length
    ? `${rec.findCur + 1}/${rs.length}${capped ? '+' : ''}` : 'no matches';
  // No Custom Highlight API (old engine): count and jump still work — the
  // search degrades, it does not vanish.
  if (!(window.CSS && CSS.highlights)) return;
  CSS.highlights.set('find', new Highlight(...rs));
  if (rs.length) CSS.highlights.set('findcur', new Highlight(rs[rec.findCur]));
  else CSS.highlights.delete('findcur');
}

function findStep(id, delta) {
  const rec = PANES.get(id);
  if (!rec || !rec.findRanges || !rec.findRanges.length) return;
  rec.findCur = (rec.findCur + delta + rec.findRanges.length) % rec.findRanges.length;
  paintFind(rec, rec.findbar.querySelector('.fct'));
  const r = rec.findRanges[rec.findCur];
  (r.startContainer.parentElement || rec.log)
    .scrollIntoView({ block: 'center' });   // scrolling up unpins
}

// A shell composer: one line, ↑/↓ history, and Ctrl-C on an empty input cancels
// the running command. No slash-completer: "/tmp" is a path, not a skill.
const TERMHIST = new Map();   // pane id -> {cmds, ix, draft}; survives rebuilds
function termComposer(p) {
  const c = el('div', 'composer term');
  const h = TERMHIST.get(p.id) ||
    { cmds: p.events.filter(e => e.kind === 'user')
                    .map(e => (e.data || {}).text || '')
                    // Multi-line commands stay out of ↑/↓ history.
                    .filter(t => t && !t.includes('\n')),
      ix: null, draft: '' };
  TERMHIST.set(p.id, h);
  c.appendChild(el('span', 'pr', '❯'));
  const inp = el('input');
  inp.type = 'text'; inp.autocomplete = 'off'; inp.spellcheck = false;
  inp.placeholder = `runs on ${p.agent.slice(5)} as you`;
  let sending = false;
  // A multi-line paste is held here, since <input> would join its lines with
  // spaces. Enter runs it as one command; Esc discards. Never run on paste.
  let block = null;
  const holdBlock = (text) => {
    block = text;
    const lines = text.split('\n');
    inp.value = `${lines[0]}   … +${lines.length - 1} more pasted lines — Enter runs all, Esc discards`;
    inp.readOnly = true;
  };
  const dropBlock = () => { block = null; inp.readOnly = false; inp.value = ''; };
  const send = async () => {
    if (sending) return;
    const t = block !== null ? block : inp.value.trim(); if (!t) return;
    sending = true;
    try {
      // Clear only once the server accepted it, so a failure leaves it for retry.
      await api('/api/session/send', { pane: p.id, text: t });
      if (!t.includes('\n') && h.cmds[h.cmds.length - 1] !== t) h.cmds.push(t);
      h.ix = null; h.draft = '';
      if (block !== null) dropBlock(); else inp.value = '';
    } catch (e) { toast(e.message, true); }
    finally { sending = false; }
  };
  inp.onpaste = e => {
    const raw = String((e.clipboardData || window.clipboardData).getData('text') || '');
    const text = raw.replace(/\r\n?/g, '\n').trim();
    if (!text.includes('\n')) return;            // one line: the default paste is right
    e.preventDefault();
    if (inp.value.trim() && block === null) {
      toast('clear the prompt line before pasting a multi-line command', true);
      return;
    }
    holdBlock(text);
  };
  const recall = v => {
    inp.value = v;
    requestAnimationFrame(() => inp.setSelectionRange(v.length, v.length));
  };
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); send(); return; }
    if (block !== null) {
      // The held block is all-or-nothing: Enter above, Esc here, nothing else.
      if (e.key === 'Escape') dropBlock();
      e.preventDefault();
      return;
    }
    if (e.key === 'ArrowUp') {
      if (!h.cmds.length) return;
      e.preventDefault();
      if (h.ix === null) { h.draft = inp.value; h.ix = h.cmds.length; }
      h.ix = Math.max(0, h.ix - 1);
      recall(h.cmds[h.ix]);
      return;
    }
    if (e.key === 'ArrowDown') {
      if (h.ix === null) return;
      e.preventDefault();
      h.ix += 1;
      if (h.ix >= h.cmds.length) { h.ix = null; recall(h.draft); }
      else recall(h.cmds[h.ix]);
      return;
    }
    if (e.key === 'c' && e.ctrlKey && !inp.value &&
        !String(window.getSelection() || '')) {
      e.preventDefault();
      api('/api/session/cancel', { pane: p.id })
        .catch(err => toast(err.message, true));
    }
  };
  c.appendChild(inp);
  return c;
}

// Built once per pane. Never rebuilt while you are typing in it — a
// composer rebuilt under an agent's event stream eats the draft mid-word.
function composer(p, kind) {
  if (kind === 'live' && isTerm(p)) return termComposer(p);
  const c = el('div', 'composer');
  if (kind === 'detached') {
    const note = el('div', 'sys', 'Saved. The agent is not running — send a message or resume to pick it up.');
    note.style.flex = '1'; note.style.textAlign = 'left';
    const b = el('button', 'send', 'Resume');
    b.onclick = async () => {
      b.textContent = 'resuming…'; b.disabled = true;
      try { await api('/api/session/resume', { pane: p.id }); await refresh(); }
      catch (e) { toast(e.message, true); b.textContent = 'Resume'; b.disabled = false; }
    };
    c.append(note, b);
    return c;
  }
  const ta = el('textarea'); ta.placeholder = 'Message…  (/ for skills)'; ta.rows = 1;
  // Clear only after the server accepts, so a failed send keeps the text.
  // `sending` also ignores a rapid double Enter.
  let sending = false;
  const send = async () => {
    if (sending) return;
    const t = ta.value.trim(); if (!t) return;
    // Fresh state: the composer outlives the snapshot it was built from.
    if (isLoginCommand(S.panes.get(p.id) || p, t)) {   // a sign-in, not a message
      ta.value = '';
      await startLogin('composer', p.id);
      return;
    }
    hide();
    sending = true;
    try {
      await api('/api/session/send', { pane: p.id, text: t });
      ta.value = ''; ta.style.height = 'auto';
    } catch (e) {
      toast(e.message, true);
    } finally {
      sending = false;
    }
  };

  // Slash-command completion from the commands the agent advertises over ACP.
  const ac = el('div', 'ac hide');
  let hits = [], sel = 0;
  const hide = () => { ac.className = 'ac hide'; hits = []; };
  const paint = () => {
    ac.replaceChildren();
    hits.forEach((cmd, i) => {
      const row = el('div', 'acrow' + (i === sel ? ' on' : ''));
      row.appendChild(el('span', 'acn', '/' + cmd.name));
      if (cmd.description) row.appendChild(el('span', 'acd', cmd.description));
      row.onmousedown = e => { e.preventDefault(); accept(i); };
      ac.appendChild(row);
    });
    ac.className = 'ac';
  };
  const accept = i => {
    const cmd = hits[i]; if (!cmd) return;
    ta.value = '/' + cmd.name + ' ';
    hide(); ta.focus();
  };
  const refit = () => { ta.style.height = 'auto';
                        ta.style.height = Math.min(ta.scrollHeight, 130) + 'px'; };
  ta.oninput = () => {
    refit();
    // Only while typing the NAME. Once there is a space the argument is being
    // written and a popup over it is in the way.
    const m = /^\/([\w-]*)$/.exec(ta.value);
    if (!m) return hide();
    const q = m[1].toLowerCase();
    const all = (S.panes.get(p.id) || {}).commands || [];
    hits = all.filter(x => x.name.toLowerCase().includes(q))
              .sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) -
                              (b.name.toLowerCase().startsWith(q) ? 0 : 1))
              .slice(0, 8);
    sel = 0;
    hits.length ? paint() : hide();
  };
  ta.onkeydown = e => {
    if (hits.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = (sel + 1) % hits.length; return paint(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + hits.length - 1) % hits.length; return paint(); }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) { e.preventDefault(); return accept(sel); }
      if (e.key === 'Escape') { e.preventDefault(); return hide(); }
    }
    // Native dialog keys, on an empty composer only: with exactly one permission
    // pending, 1..9 answers by number and Esc refuses; otherwise Esc on a busy
    // pane interrupts. Ignores repeats and chords; reads fresh state from S.panes.
    if (ta.value === '' && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const cur = S.panes.get(p.id) || p;
      const d = (cur.pending || []).length === 1 ? pendingPerm(cur) : null;
      const answer = o => {
        api('/api/session/permission',
            { pane: p.id, requestId: d.requestId, optionId: o.optionId,
              digest: d.digest })
          .catch(err => toast(err.message, true));
      };
      if (d && /^[1-9]$/.test(e.key)) {
        const o = permOptions(d)[+e.key - 1];
        if (o) { e.preventDefault(); return answer(o); }
      }
      if (e.key === 'Escape') {
        if (d) {
          const rej = permOptions(d).find(o => String(o.kind || '').startsWith('reject'));
          if (rej) { e.preventDefault(); return answer(rej); }
        }
        if (cur.state === 'busy') {
          e.preventDefault();
          return api('/api/session/cancel', { pane: p.id })
            .catch(err => toast(err.message, true));
        }
      }
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      // Broadcast to every pane that can take a prompt; explicit chord only.
      e.preventDefault();
      const t = ta.value.trim(); if (!t || sending) return;
      const ids = composablePanes().map(x => x.id);
      if (!ids.includes(p.id)) ids.unshift(p.id);
      if (ids.length < 2) return toast('only this pane can take a prompt right now — open another lane to fan out', true);
      sending = true;
      api('/api/session/send', { panes: ids, text: t })
        .then(r => {
          const bad = Object.entries(r.results || {}).filter(([, err]) => err);
          if (r.sent) { ta.value = ''; ta.style.height = 'auto'; }
          toast(bad.length
            ? `sent to ${r.sent} of ${ids.length} — ` + bad.map(([id, err]) =>
                `${(S.panes.get(id) || {}).title || id}: ${err}`).join('; ')
            : `sent to ${r.sent} panes`, !!bad.length);
        })
        .catch(err => toast(err.message, true))
        .finally(() => { sending = false; });
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };
  ta.onblur = hide;
  const b = el('button', 'send', 'Send'); b.onclick = send;
  // The Stop button. Esc on an empty composer was the only way to interrupt a
  // turn: invisible, and impossible on a phone. Shown only while the pane is
  // working (CSS keys off the pane's `working` class, because the composer is
  // built once and outlives every state change). First press is the polite
  // one, session/cancel, which also drops whatever was queued behind the turn.
  // An agent that ignores it for STOP_FORCE_MS gets a second offer: force stop
  // = pause, which ends the process and keeps the conversation to resume.
  function stopButton(p) {
    const STOP_FORCE_MS = 6000;
    const b = el('button', 'stop', '■ Stop');
    b.type = 'button';
    b.title = 'stop the running turn and anything queued behind it (Esc)';
    let forceAt = 0, timer = null;
    const reset = () => {
      forceAt = 0; clearTimeout(timer); timer = null;
      b.textContent = '■ Stop'; b.classList.remove('force'); b.disabled = false;
    };
    b.onclick = async () => {
      const cur = S.panes.get(p.id) || p;
      if (forceAt && Date.now() >= forceAt) {
        b.disabled = true; b.textContent = 'stopping…';
        try { await api('/api/session/pause', { pane: p.id }); await refresh(); }
        catch (e) { toast(e.message, true); }
        return reset();
      }
      b.disabled = true; b.textContent = 'stopping…';
      try { await api('/api/session/cancel', { pane: p.id }); }
      catch (e) { toast(e.message, true); return reset(); }
      b.disabled = false;
      forceAt = Date.now() + STOP_FORCE_MS;
      clearTimeout(timer);
      timer = setTimeout(() => {
        const now = S.panes.get(p.id) || cur;
        if (now.state === 'busy' || now.state === 'uncertain') {
          b.textContent = '■ Force stop'; b.classList.add('force');
          b.title = 'the agent ignored the interrupt — end its process; the '
                  + 'conversation stays and resumes when you type';
        } else reset();
      }, STOP_FORCE_MS);
    };
    b._reset = reset;
    return b;
  }
  c.append(ac, ta, b, stopButton(p));
  return c;
}

// Formats an age in seconds as s/m/h.
function fmtAge(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

/* ── the display projection ──────────────────────────────────────────────
 * Mirrors corral_core/sessions.py display_state(): the browser reduces events
 * locally, so a server-computed value would lag. Both sides are pinned to
 * corral_core/display_cases.json by tests. */
const IDLE_DISPLAY_S = 1800;
// How often to re-check whether any pane has aged into `idle`; renders only on change.
const DISPLAY_TICK_MS = 60000;

/* Seconds since this pane last emitted, on this browser's own clock (hub idleS at
 * snapshot plus local elapsed), so hub/browser clock skew cannot age panes early. */
function paneAge(p, nowMs) {
  const base = p.idleS || 0;
  if (!p._idleAt) return base;
  return base + Math.max(0, (nowMs - p._idleAt) / 1000);
}

function displayState(p, nowMs) {
  const now = nowMs === undefined ? Date.now() : nowMs;
  const state = p.state || 'starting';
  const pending = ((p.pending || []).length) > 0;
  const held = !!p.gateHold;
  // An agent's open ask_human question: needs-you whatever else is true,
  // including a pane paused by a hub restart (the question is still open).
  const asked = !!p.question;
  if (pending || held || asked || state === 'needs-you') return 'needs-you';
  if (state === 'dead') return 'dead';
  if (state === 'starting' || state === 'busy' || state === 'uncertain') return 'working';
  // A detached pane never becomes ready on its own; a human must resume it.
  if (state === 'detached') return 'paused';
  // A turn another pane's agent or a rig started is not the human's turn.
  if (state === 'ready' && AGENT_ORIGIN_VIAS.includes(p.turnVia)) return 'idle';
  if (state === 'ready') return paneAge(p, now) < IDLE_DISPLAY_S ? 'your-turn' : 'idle';
  return 'idle';                       // anything unrecognised
}

const DISPLAY_LABEL = {
  'needs-you': 'needs you', 'working': 'working',
  'your-turn': 'your turn', 'idle': 'idle', 'paused': 'paused', 'dead': 'dead',
};

/* Turn origins that are not the human's (mirrors the core's AGENT_ORIGIN_VIAS);
 * a `ready` pane whose last turn came this way is `idle`. */
const AGENT_ORIGIN_VIAS = ['peer', 'rig'];
const ASK_PREVIEW_CHARS = 80;           // the roster line; the banner shows it all

/* ask_human: the agent's open question as the pane's banner, labelled as the
 * agent's words. textContent only. null when none is open. */
function questionBanner(p) {
  const q = p.question;
  if (!q || !q.text) return null;
  // `source: hop-limit` is the HUB's own words (a loop stopped at the
  // pane-to-pane message limit), never the agent's -- labelled as such.
  const hub = q.source === 'hop-limit';
  const b = el('div', 'qbanner' + (hub ? ' hub' : ''));
  const who = p.seat ? '@' + p.seat : 'the agent';
  b.appendChild(el('div', 'qwho', hub
    ? 'Corral paused this loop — send any message to let it continue'
    : `${who} asks you — answer by sending a message`));
  b.appendChild(el('div', 'qtext', q.text));
  b.title = hub
    ? 'Raised by Corral, not by the agent: a message between panes was refused '
      + 'at the limit. It stays until you send this pane a message.'
    : 'Raised by this pane\'s agent with ask_human. It stays until you '
      + 'send this pane a message, or the pane closes or stops.';
  return b;
}

/* The roster's one-line preview of the same question. */
function askLine(p) {
  const q = p.question;
  if (!q || !q.text) return null;
  const flat = String(q.text).replace(/\s+/g, ' ').trim();
  const short = flat.length > ASK_PREVIEW_CHARS
    ? flat.slice(0, ASK_PREVIEW_CHARS - 1) + '…' : flat;
  const line = el('div', 'ask', (q.source === 'hop-limit' ? 'paused: ' : 'asks: ') + short);
  line.title = q.text;
  return line;
}

/* The projection of every pane as one string, so the tick can tell "nothing
 * changed" from "a pane aged into idle" without touching the DOM. */
let displaySig = '';
function displaySignature(panes, nowMs) {
  return panes.map(p => p.id + ':' + displayState(p, nowMs)).join('|');
}

function displayTick() {
  const panes = [...S.panes.values()];
  if (displaySignature(panes) !== displaySig) render();
}

/* Renders coalesce to one per animation frame. A streamed answer arrives as
 * many events a second across several panes; painting the wall once per event
 * was what froze it. Hidden tabs get no frames, so a slow timer stands in and
 * keeps the title's counts current. */
const HIDDEN_RENDER_MS = 1000;
let renderQueued = false;
/* ?perf in the address: time every render and print a summary to the console
 * every PERF_REPORT_MS (docs/PERF-REVIEW-2026-10-04.md item 5: measure frame
 * cost before changing how the roster or a log is rebuilt). Bounded: the
 * samples array is cleared at each report. Also readable as window.corralPerf. */
const PERF = new URLSearchParams(location.search).has('perf');
const PERF_REPORT_MS = 5000;
const perfStat = { samples: [], over16: 0, frames: 0, worst: 0 };
if (PERF) {
  window.corralPerf = perfStat;
  setInterval(() => {
    const xs = perfStat.samples.sort((a, b) => a - b);
    if (!xs.length) return;
    const pct = q => xs[Math.min(xs.length - 1, Math.floor(xs.length * q))].toFixed(1);
    console.log(`[corral perf] renders ${xs.length} in ${PERF_REPORT_MS / 1000}s · ` +
                `p50 ${pct(0.5)} ms · p95 ${pct(0.95)} ms · max ${xs[xs.length - 1].toFixed(1)} ms · ` +
                `over 16 ms: ${perfStat.over16} (all time) · panes ${S.panes.size}`);
    perfStat.samples = [];
  }, PERF_REPORT_MS);
}
function timedRender() {
  const t = performance.now();
  render();
  const ms = performance.now() - t;
  perfStat.frames++;
  perfStat.worst = Math.max(perfStat.worst, ms);
  if (ms > 16) perfStat.over16++;
  if (perfStat.samples.length < 10000) perfStat.samples.push(ms);
}
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  const run = () => { renderQueued = false; if (PERF) timedRender(); else render(); };
  if (document.visibilityState === 'hidden') setTimeout(run, HIDDEN_RENDER_MS);
  else requestAnimationFrame(run);
}

/* The tab title: needs-you count, else your-turn count, else plain. */
function setTitle(panes) {
  let need = 0, turn = 0;
  for (const p of panes) {
    const d = displayState(p);
    if (d === 'needs-you') need++;
    else if (d === 'your-turn') turn++;
  }
  document.title = need ? `${need} need you · Corral`
                 : turn ? `${turn} your turn · Corral`
                 : 'Corral';
}

function render() {
  const panes = [...S.panes.values()];
  setTitle(panes);
  displaySig = displaySignature(panes);
  markSeen();                  // whatever this render shows, a human can see
  rigRefreshHints();           // an open Rigs dialog tracks seats and states

  // roster
  const r = $('#roster');
  // Hold the roster still while the rename box is open, so the name isn't eaten.
  if (!(S.renaming && r.querySelector('.ren'))) {
  r.innerHTML = '';
  // Pinned panes and the focused pane always show; the rest roll up under
  // "Other", open by default.
  const paneRow = p => {
    const disp = displayState(p);
    // Both classes: `rit needs-you` is what the eye reads, and the raw state
    // class stays so every rule style.css already had keeps working.
    const it = el('div', 'rit d-' + disp + ' ' + p.state +
                        (p.minimized ? ' min' : '') +
                        (S.focus === p.id ? ' on' : ''));
    it.appendChild(el('span', 'dot'));
    const t = el('div', 'txt');

    if (S.renaming === p.id) {
      const inp = el('input', 'ren'); inp.value = p.title || p.label;
      inp.onkeydown = async e => {
        if (e.key === 'Escape') { S.renaming = null; render(); }
        if (e.key === 'Enter') {
          const v = inp.value.trim(); S.renaming = null;
          if (v) { try { await api('/api/session/rename', { pane: p.id, title: v }); }
                   catch (err) { toast(err.message, true); } }
          await refresh();
        }
      };
      inp.onblur = () => { S.renaming = null; render(); };
      t.appendChild(inp);
      it.appendChild(t);
      requestAnimationFrame(() => { inp.focus(); inp.select(); });
      return it;
    }

    t.appendChild(el('div', 't', p.title || p.label));
    // Time since the pane last emitted separates "working" from "wedged".
    const quiet = (p.state === 'busy' || p.state === 'uncertain') && p.idleS >= 30
      ? ` · quiet ${fmtAge(p.idleS)}` : '';
    // Tag the agent for every lane but the default, so a directory name is never
    // mistaken for an agent badge.
    const agentTag = p.agent !== 'claude' ? p.label + ' · ' : '';
    const sub = el('div', 's',
      (DISPLAY_LABEL[disp] || disp) + quiet + ' · ' + agentTag +
      paneDir(p));
    // The raw state stays one hover away.
    sub.title = p.state;
    t.appendChild(sub);
    const ask = askLine(p);            // ask_human: the question, on the row
    if (ask) t.appendChild(ask);
    it.appendChild(t);

    // A minimized pane blocked on a permission still shows its count.
    if (p.pending.length) it.appendChild(el('span', 'badge', String(p.pending.length)));

    if (p.pinned) {
      const pm = el('span', 'pinmark', '★'); pm.title = 'pinned';
      it.appendChild(pm);
    }
    // One hover button, and the right-click, open the pane's menu: every
    // action the row used to carry lives there, beside the header's.
    const more = el('button', 'more', '⋯');
    more.type = 'button'; more.title = 'more — rename, pin, pause, resume…';
    more.onclick = e => { e.stopPropagation(); openPaneMenu(p, e.currentTarget); };
    it.appendChild(more);
    it.oncontextmenu = e => {
      e.preventDefault(); openPaneMenu(p, { x: e.clientX, y: e.clientY });
    };

    // Drag to reorder.
    it.draggable = true;
    it.dataset.pid = p.id;
    it.ondragstart = e => { S.drag = p.id; it.classList.add('dragging');
                            e.dataTransfer.effectAllowed = 'move'; };
    it.ondragend = () => { it.classList.remove('dragging'); S.drag = null; };
    it.ondragover = e => { e.preventDefault(); it.classList.add('over'); };
    it.ondragleave = () => it.classList.remove('over');
    it.ondrop = async e => {
      e.preventDefault(); it.classList.remove('over');
      if (!S.drag || S.drag === p.id) return;
      const ids = [...S.panes.keys()];
      ids.splice(ids.indexOf(S.drag), 1);
      ids.splice(ids.indexOf(p.id), 0, S.drag);
      try { await api('/api/session/order', { ids }); await refresh(); }
      catch (err) { toast(err.message, true); }
    };

    it.onclick = () => {
      if (p.minimized) return setMin(p, false);
      focusPane(p.id);
    };
    it.ondblclick = e => { e.stopPropagation(); S.renaming = p.id; render(); };
    return it;
  };

  // With nothing pinned the list is flat: a group header over every row says nothing.
  if (!panes.some(p => p.pinned)) {
    for (const p of panes) r.appendChild(paneRow(p));
  } else {
    const pinned = panes.filter(p => p.pinned || S.focus === p.id);
    const others = panes.filter(p => !p.pinned && S.focus !== p.id);
    for (const p of pinned) r.appendChild(paneRow(p));
    if (others.length) {
      // "Other", not "Recent": the roster already has a "Recent" group.
      const lab = el('div', 'lab clicky',
                     `Other · ${others.length} ${S.hideOther ? '▸' : '▾'}`);
      lab.onclick = () => { S.hideOther = !S.hideOther; render(); };
      r.appendChild(lab);
      if (!S.hideOther) for (const p of others) r.appendChild(paneRow(p));
    }
  }
  if (!panes.length) r.appendChild(el('div', 'calm', 'None yet.'));
  // Say what the restore cap left off the roster.
  if (S.notRestored) {
    const n = el('div', 'calm',
      `${S.notRestored} saved conversation${S.notRestored > 1 ? 's' : ''} not ` +
      `restored — ${12} is the cap. Their transcripts are still on disk.`);
    n.title = 'Close or archive a pane and restart to bring one back.';
    r.appendChild(n);
  }

  // Archive: closed conversations, reopenable. Collapsed by default.
  const arc = S.archived || [];
  if (arc.length) {
    const lab = el('div', 'lab clicky',
                   `Archive · ${arc.length} ${S.showArc ? '▾' : '▸'}`);
    lab.onclick = () => { S.showArc = !S.showArc; render(); };
    r.appendChild(lab);
    if (S.showArc) {
      for (const a of arc.slice(0, 25)) {
        const it = el('div', 'rit arc');
        it.appendChild(el('span', 'dot'));
        const t = el('div', 'txt');
        t.appendChild(el('div', 't', a.title));
        t.appendChild(el('div', 's', `${a.agent} · ${a.cwd.split('/').pop()}`));
        it.appendChild(t);
        it.title = 'reopen this conversation';
        it.onclick = async () => {
          try { await api('/api/session/reopen', { pane: a.id }); await refresh(); }
          catch (e) { toast(e.message, true); }
        };
        r.appendChild(it);
      }
    }
  }

  // Scheduled jobs, plus failed or missed one-shots kept until dismissed.
  const jobs = S.schedule || [];
  if (jobs.length) {
    r.appendChild(el('div', 'lab', `Scheduled · ${jobs.length}`));
    for (const j of jobs) {
      const it = el('div', 'rit arc' + (j.failed ? ' dead' : ''));
      it.appendChild(el('span', 'dot'));
      const t = el('div', 'txt');
      t.appendChild(el('div', 't', j.title || j.prompt.slice(0, 60)));
      const when = new Date(j.at).toLocaleString();
      t.appendChild(el('div', 's', j.failed ? `failed: ${j.last_error}` :
        `${j.action} · ${when}${j.repeat ? ' · ' + j.repeat : ''} · ${j.agent}`));
      it.appendChild(t);
      const x = el('button', 'a', '✕'); x.title = j.failed ? 'dismiss' : 'unschedule';
      x.onclick = async e => {
        e.stopPropagation();
        try { await api('/api/session/schedule/remove', { id: j.id }); await refresh(); }
        catch (err) { toast(err.message, true); }
      };
      const acts = el('div', 'acts'); acts.appendChild(x); it.appendChild(acts);
      r.appendChild(it);
    }
  }
  }

  // grid
  const g = $('#grid');
  const shown = panes.filter(p => !p.minimized);
  const mins = panes.filter(p => p.minimized);
  g.className = 'grid' + (shown.length === 1 ? ' one' : '');

  // Clear only the disposable children (minbar, empty state); panes reconcile by identity.
  for (const c of [...g.children]) {
    if (!c.dataset.pane) c.remove();
  }

  if (mins.length) {
    const bar = el('div', 'minbar');
    for (const p of mins) {
      // A <button> so keyboard focus can reach it.
      const cdisp = displayState(p);
      const c = el('button', 'minchip d-' + cdisp + ' ' + p.state);
      c.type = 'button';
      c.appendChild(el('span', 'd'));
      c.appendChild(el('span', 'mt', p.title || p.label));
      if (p.pending.length) c.appendChild(el('span', 'badge', String(p.pending.length)));
      // Full title and state in the name: the label ellipsizes and colour alone
      // is not enough. Display state first, raw state in parentheses.
      const full = `${p.title || p.label} — ${DISPLAY_LABEL[cdisp] || cdisp}`
                 + ` (${p.state})`
                 + (p.background ? ', in the background until it needs you' : '')
                 + ', click to restore';
      c.title = full;
      c.setAttribute('aria-label', full);
      c.onclick = () => setMin(p, false);
      bar.appendChild(c);
    }
    g.insertBefore(bar, g.firstChild);          // above the panes, which stay put
  }

  // Reconcile panes by id: drop gone panes, update the rest in place, and move a
  // node only when its position changed (moving blurs focus). Minimized panes
  // keep their record so the draft, find query and scroll survive.
  const exists = new Set(panes.map(p => p.id));
  const want = new Set(shown.map(p => p.id));
  for (const [id, rec] of [...PANES]) {
    if (!exists.has(id)) { rec.root.remove(); PANES.delete(id); }
    else if (!want.has(id)) { rec.root.remove(); }
  }
  // A retired pane must not leave its find highlights registered page-wide.
  if (FIND.pane && !PANES.has(FIND.pane)) { FIND.pane = null; clearFindPaint(); }
  let prev = mins.length ? g.querySelector(':scope > .minbar') : null;
  for (const p of shown) {
    let rec = PANES.get(p.id);
    if (!rec) { PANES.set(p.id, rec = buildPane(p)); }
    updatePane(rec, p);
    // Full-size state as a class on the persistent root; CSS renders the tag.
    rec.root.classList.toggle('solo', shown.length === 1);
    const slot = prev ? prev.nextSibling : g.firstChild;
    if (rec.root !== slot) g.insertBefore(rec.root, slot);
    prev = rec.root;
  }

  if (!shown.length) {
    const e = el('div', 'empty');
    if (!panes.length) {
      // First-run screen: what to press, how to get around, and where transcripts
      // live. createElement only: `dataDir` comes off the wire.
      e.appendChild(el('h2', null, 'Nothing running.'));
      const start = el('button', 'btn go emptygo', '＋ New conversation');
      start.type = 'button';
      start.onclick = () => $('#new').click();
      e.appendChild(start);
      const l2 = el('div', 'emptyline');
      l2.appendChild(el('kbd', null, '⌘K'));
      l2.appendChild(el('span', null, ' search and jump to any conversation · '));
      l2.appendChild(el('kbd', null, '?'));
      l2.appendChild(el('span', null, ' every shortcut'));
      e.appendChild(l2);
      const where = S.dataDir;
      const l3 = el('div', 'emptyline dim');
      l3.appendChild(el('span', null, where
        ? 'Transcripts stay on this machine, in '
        : 'Transcripts stay on this machine. Nothing is uploaded.'));
      if (where) {
        l3.appendChild(el('code', null, where));
        l3.appendChild(el('span', null, '. Nothing is uploaded.'));
      }
      e.appendChild(l3);
    } else {
      e.appendChild(el('h2', null, 'All minimized.'));
      e.appendChild(el('div', null, 'They are still running. Click one above to bring it back.'));
    }
    g.appendChild(e);
  }

  // THE rail. Built directly from the panes; permissions are answered here with
  // their exact bytes, so a minimized pane can still be unblocked.
  const n = $('#needs'); n.innerHTML = '';
  let items = 0;
  // The Claude login, ahead of the pane it would kill: expired renders as a dead
  // card, expiring as a plain one, each with the remedy. Hidden when fine.
  const ca = S.claudeAuth;
  const claudeLane = (S.agents || []).some(a => a.key === 'claude');
  if (ca && (ca.ok === false || ca.warn || (ca.ok === null && claudeLane))) {
    const c = el('div', 'ncard' + (ca.ok === false ? ' dead' : ''));
    c.appendChild(el('div', 't', ca.ok === false ? 'Claude login expired'
                                 : ca.ok === null ? 'Claude login unknown'
                                 : 'Claude login expiring'));
    c.appendChild(el('div', 'm', ca.why || ''));
    const said = loginLine(S.claudeLogin);
    if (said) c.appendChild(el('div', 'fnote', said));
    const acts = el('div', 'facts');
    acts.appendChild(signInButton('rail'));
    c.appendChild(acts);
    n.appendChild(c); items++;
  }
  for (const p of panes) {
    for (const rid of p.pending) {
      // Newest card for this id, scanning back from the tail (no copy of the ring).
      let ev = null;
      for (let i = p.events.length - 1; i >= 0; i--) {
        const e = p.events[i];
        if (e.kind === 'permission' && e.data.requestId === rid) { ev = e; break; }
      }
      const c = el('div', 'ncard');
      c.appendChild(el('div', 't', p.title || p.label));
      c.appendChild(el('div', 'm', `${p.label} · ${paneDir(p)}` +
                                   (p.minimized ? ' · minimized' : '')));
      if (ev) {
        c.appendChild(permCard(p, ev.data, null, true));
      } else {
        // The payload aged out of the ring: never offer buttons for a request
        // whose contents can no longer be shown.
        c.appendChild(el('div', 'fnote',
          'this request is older than the kept transcript — open the pane'));
      }
      const go = el('button', 'fbtn',
                    p.minimized ? 'Restore the pane' : 'Open the pane');
      go.onclick = async () => {
        if (p.minimized) await setMin(p, false);
        focusPane(p.id);
      };
      const acts = el('div', 'facts'); acts.appendChild(go);
      c.appendChild(acts);
      n.appendChild(c); items++;
    }
  }
  // A pane that died on its own stays until dismissed; closed panes never appear.
  for (const p of panes) {
    if (p.state !== 'dead') continue;
    const c = el('div', 'ncard dead');
    c.appendChild(el('div', 't', `${p.title || p.label} — agent stopped`));
    c.appendChild(el('div', 'm', p.error || 'click to dismiss'));
    c.onclick = async () => {
      await forgetPane(p);
    };
    if (p.resumable || p.deadCause === 'auth') {
      const acts = el('div', 'facts');
      // A login death: Sign in first; the pane resumes by itself after it.
      if (p.deadCause === 'auth' && p.agent === 'claude') acts.appendChild(signInButton('banner', p.id));
      if (p.resumable) {
        const rb = el('button', 'fbtn', 'Resume');
        rb.onclick = async e => { e.stopPropagation(); await resumePane(p); };
        acts.appendChild(rb);
      }
      c.appendChild(acts);
    }
    n.appendChild(c); items++;
  }
  // An `uncertain` pane is alive, mid-turn, and silent for minutes: surfaced with
  // no action, since whether it is wedged is the operator's call.
  for (const p of panes) {
    if (p.state !== 'uncertain') continue;
    const c = el('div', 'ncard');
    c.appendChild(el('div', 't', `${p.title || p.label} — quiet ${fmtAge(p.idleS)}`));
    c.appendChild(el('div', 'm',
      'alive and mid-turn, but nothing has come out of it. Still attached — ' +
      'pause or stop it if it looks wedged.'));
    const acts = el('div', 'facts');
    const go = el('button', 'fbtn', 'Open the pane');
    go.onclick = () => focusPane(p.id);
    acts.appendChild(go);
    c.appendChild(acts);
    n.appendChild(c); items++;
  }
  // Own branches with changes not yet reviewed. Never blocking: `blocked`
  // below counts pending permissions only.
  let quiet = 0;
  for (const p of wtRailCards(panes, wtSeen())) {
    const c = el('div', 'ncard wt');
    c.appendChild(el('div', 't', `${p.title || p.label} — ready to review`));
    c.appendChild(el('div', 'm', wtPillModel(p.worktree).text));
    const acts = el('div', 'facts');
    const go = el('button', 'fbtn', 'Review');
    go.onclick = () => openReview(p);
    const later = el('button', 'fbtn', 'Not now');
    later.title = 'hide this card until the branch changes again';
    later.onclick = () => { wtMarkSeen(p.id, p.worktree.summary); render(); };
    acts.append(go, later);
    c.appendChild(acts);
    n.appendChild(c); items++; quiet++;
  }
  if (!items) n.appendChild(el('div', 'calm', 'Nothing. Quiet is the steady state.'));
  const mobilePane = $('#mobile-pane');
  if (mobilePane) {
    const selected = S.focus;
    mobilePane.textContent = '';
    if (!panes.length) {
      const option = document.createElement('option');
      option.textContent = 'No conversations';
      option.disabled = true;
      option.selected = true;
      mobilePane.appendChild(option);
    } else {
      for (const p of panes) {
        const option = document.createElement('option');
        option.value = p.id;
        option.textContent = p.title || p.label;
        mobilePane.appendChild(option);
      }
      if (panes.some(p => p.id === selected)) mobilePane.value = selected;
    }
  }
  railFold(items, panes.reduce((a, p) => a + p.pending.length, 0), quiet);
}

// Minimize/restore, shared by the roster row, pane header, minbar chip and rail.
async function setMin(p, flag) {
  try {
    await api('/api/session/minimize', { pane: p.id, minimized: flag });
    p.minimized = flag; p.background = false; render();
  } catch (e) { toast(e.message, true); }
}

/* ── the needs-you rail folds ─────────────────────────────────────────────
 * Empty folds itself. Folded is a strip that still shows the count (hot when an
 * agent is blocked), never display:none. A hand-fold is sticky; an auto-fold
 * follows the contents. */
/* Whether the rail shows: a hand-fold wins; otherwise open when it holds
 * something. On a narrow screen the rail covers the pane, so review cards
 * (`quiet`, never blocking) count but do not pop it open there. */
function railOpens(items, quiet, narrow, shut) {
  if (shut !== null) return !shut;
  return (narrow ? items - quiet : items) > 0;
}
function railFold(items, blocked, quiet = 0) {
  const narrow = !!(window.matchMedia && window.matchMedia('(max-width: 820px)').matches);
  const open = railOpens(items, quiet, narrow, S.railShut);
  $('#app').classList.toggle('railshut', !open);
  $('#rrail').classList.toggle('shut', !open);
  $('#railhead').textContent = `Needs you${items ? ' · ' + items : ''} ▾`;
  // Always a digit, including 0, so an empty rail is distinguishable when collapsed.
  const tab = $('#railtabnum');
  tab.textContent = String(items);
  tab.className = 'railtabnum' + (blocked ? ' hot' : '') + (items ? '' : ' zero');
  $('#railtab').title = blocked
    ? `${blocked} agent${blocked > 1 ? 's are' : ' is'} blocked waiting on you`
    : items ? `${items} waiting` : 'nothing waiting';
}

function wireRail() {
  // Folding by hand is remembered; folding because it is empty is not.
  const saved = localStorage.getItem('corral.railShut');
  S.railShut = saved === null ? null : saved === '1';
  const set = v => {
    S.railShut = v;
    if (v === null) localStorage.removeItem('corral.railShut');
    else localStorage.setItem('corral.railShut', v ? '1' : '0');
    render();
  };
  $('#railhead').onclick = () => set(true);
  $('#railtab').onclick = () => set(false);
}

/* ── data ────────────────────────────────────────────────────────────── */
let refreshSeq = 0;
/* ── seen ────────────────────────────────────────────────────────────────
 * Report seqs a human actually had on screen (page visible and focused), so the
 * hub can skip notifying them. Throttled to one batch per SEEN_MS. */
const SEEN = new Map();
const SEEN_MS = 1000;
let seenTimer = null;
function looking() { return document.visibilityState === 'visible' && document.hasFocus(); }
function markSeen() {
  if (!looking() || seenTimer) return;
  seenTimer = setTimeout(() => {
    seenTimer = null;
    if (!looking()) return;
    // One POST for every pane that moved, not one per pane per tick.
    const seen = {};
    let any = false;
    for (const p of S.panes.values()) {
      const seq = p.seq || 0;
      if (seq > (SEEN.get(p.id) || 0)) {
        SEEN.set(p.id, seq);
        seen[p.id] = seq;
        any = true;
      }
    }
    if (any) api('/api/session/seen', { seen }).catch(() => {});
  }, SEEN_MS);
}

async function refresh() {
  // Ask only for events past what we already hold, so a snapshot cannot clobber
  // newer locally received events.
  const since = {};
  for (const [id, p] of S.panes) since[id] = p.seq || 0;
  // Only the most recently started call applies its result: concurrent /api/state
  // responses can resolve out of order.
  const seq = ++refreshSeq;
  let d;
  try {
    // full=1: the browser reads the host-wide fields and every pane's commands
    // and config; without it a cursor gets a poller's light delta (hub state()).
    d = await api('/api/state?full=1&since=' + encodeURIComponent(JSON.stringify(since)));
  } catch (e) { if (e.status === 401) return relock(); throw e; }
  if (seq !== refreshSeq) return;      // a newer refresh() has since been issued
  // A field the response leaves out keeps its previous value (never emptied).
  if ('agents' in d) S.agents = d.agents || [];
  if ('claudeAuth' in d) S.claudeAuth = d.claudeAuth || null;
  S.claudeLogin = d.claudeLogin || null;
  S.agentGroups = d.agentGroups || S.agentGroups || {};
  S.catalog = d.catalog || S.catalog || {};
  S.defaultCwd = d.defaultCwd || S.defaultCwd || '';
  S.dataDir = d.dataDir || S.dataDir || '';
  S.cwdSuggestions = d.cwdSuggestions || S.cwdSuggestions || [];
  if ('archived' in d) S.archived = d.archived || [];
  S.notRestored = d.notRestored || 0;
  if ('schedule' in d) S.schedule = d.schedule || [];
  const next = new Map();
  const rxAt = Date.now();
  for (const np of (d.panes || [])) {
    np._idleAt = rxAt;                 // idleS is as of NOW (paneAge)
    const prev = S.panes.get(np.id);
    if (!prev) { next.set(np.id, np); continue; }
    const events = prev.events || [];
    for (const ev of (np.events || [])) {
      if (!events.length || ev.seq > events[events.length - 1].seq) events.push(ev);
    }
    if (events.length > 4000) events.splice(0, events.length - 4000);
    Object.assign(prev, np);
    prev.events = events;                       // keep ours; np.events was a delta
    next.set(prev.id, prev);
  }
  S.panes = next;
  render();
}

function connect() {
  if (S.es) S.es.close();
  const es = new EventSource('/api/stream');
  S.es = es;
  es.onopen = () => {
    $('#conn').textContent = 'live'; $('#conn').className = 'conn on';
    // Fill anything emitted while the stream was down. Without this a
    // reconnect silently loses every event from the gap.
    refresh().catch(() => {});
  };
  es.onerror = () => {
    $('#conn').textContent = 'reconnecting…'; $('#conn').className = 'conn off';
    // A 401 on reconnect closes EventSource for good: check once whether we are
    // still paired rather than showing "reconnecting…" forever.
    if (es.readyState === EventSource.CLOSED) {
      api('/api/state').catch(e => { if (e.status === 401) relock(); });
    }
  };
  // The hub re-verifies the cookie behind an open stream and says so when it
  // expires. Named SSE events never reach onmessage — listen for it by name.
  es.addEventListener('expired', () => relock());
  es.onmessage = m => {
    let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
    // Layout events are shared UI state, not transcript events. They carry no
    // pane sequence, so apply them before the normal sequence deduplication.
    if (ev.kind === 'layout') {
      const layoutPane = S.panes.get(ev.pane);
      if (!layoutPane) { refresh(); return; }
      Object.assign(layoutPane, ev.data || {});
      const ordered = [...S.panes.values()].sort((a, b) => {
        const ak = [a.pinned ? 0 : 1, a.order == null ? 10000 : a.order,
                    a.created || ''];
        const bk = [b.pinned ? 0 : 1, b.order == null ? 10000 : b.order,
                    b.created || ''];
        for (let i = 0; i < ak.length; i++) {
          if (ak[i] < bk[i]) return -1;
          if (ak[i] > bk[i]) return 1;
        }
        return 0;
      });
      S.panes = new Map(ordered.map(p => [p.id, p]));
      scheduleRender();
      return;
    }
    // The server dropped our backlog because this browser fell behind: refetch,
    // since anything after a drop is untrustworthy.
    if (ev.kind === 'resync') { refresh().catch(() => {}); return; }
    const p = S.panes.get(ev.pane);
    if (!p) { refresh(); return; }        // a pane we don't know yet
    const last = p.events.length ? p.events[p.events.length - 1].seq : (p.seq || 0);
    if (ev.seq <= last) return;                 // already have it
    // A gap means events went missing: refetch rather than derive state from a
    // partial story. Debounced so a burst does not fire one refresh per event.
    if (last && ev.seq > last + 1) {
      if (!S.refreshing) { S.refreshing = true; refresh().catch(() => {}).finally(() => { S.refreshing = false; }); }
      return;
    }
    p.events.push(ev);
    // It just spoke: its age restarts on this browser's clock (paneAge).
    p.idleS = 0; p._idleAt = Date.now();
    // Cap live growth without dropping history just loaded from disk; bounded.
    const cap = Math.min(20000, Math.max(4000, p.events.length - 1));
    if (p.events.length > cap) p.events.splice(0, p.events.length - cap);
    p.seq = ev.seq || p.seq || 0;
    const d = ev.data || {};
    if (ev.kind === 'permission') { p.pending.push(d.requestId); p.state = 'needs-you'; }
    if (ev.kind === 'permission_answered') {
      p.pending = p.pending.filter(x => x !== d.requestId);
      p.state = p.pending.length ? 'needs-you' : 'busy';
    }
    // Mirror the server: an expired request is no longer pending.
    if (ev.kind === 'permission_expired') {
      p.pending = p.pending.filter(x => x !== d.requestId);
      refresh().catch(() => {});          // server owns what the state is now
    }
    if (ev.kind === 'paused') { p.state = 'detached'; p.pending = []; }
    // /clear ended the old conversation server-side (fresh agent session):
    // nothing it was asking is waiting any more.
    if (ev.kind === 'cleared') { p.pending = []; p.state = 'starting'; }
    if (ev.kind === 'user') { p.state = 'busy'; p.turnVia = d.via || null; }
    // A peer message starts a turn just as a human's does.
    if (ev.kind === 'peer') { p.state = 'busy'; p.turnVia = 'peer'; }
    // ask_human: the agent raised (or replaced) its question; a human turn,
    // a close or the agent's death closed it. The server owns both.
    if (ev.kind === 'question') p.question = { text: d.text, at: d.at, turn: d.turn,
                                              source: d.source || null };
    if (ev.kind === 'question_cleared') p.question = null;
    if (ev.kind === 'peer_result' && d.delivered === false) refresh().catch(() => {});
    if (ev.kind === 'turn_end') p.state = p.pending.length ? 'needs-you' : 'ready';
    if (ev.kind === 'dead') { p.state = 'dead'; p.error = d.reason; p.deadCause = d.cause || null; }
    if (ev.kind === 'closed') { p.state = 'dead'; p.error = null; refresh(); }
    if (ev.kind === 'ready') p.state = 'ready';
    // Server-observed state edges (busy→uncertain, detected dead).
    if (ev.kind === 'state' && d.state) p.state = d.state;
    if (ev.kind === 'resumed') { p.state = 'ready'; refresh(); }
    if (ev.kind === 'renamed') p.title = d.title;
    if (ev.kind === 'seat') { p.seat = d.seat || null; p.seatWithheld = null; }
    if (ev.kind === 'config' || ev.kind === 'ready') {
      if (d.model) p.model = d.model;
      if (d.effort) p.effort = d.effort;
      if (d.config) p.config = d.config;
    }
    // The commands event carries only a count; pull the list from state.
    if (ev.kind === 'commands') refresh();
    if (ev.kind === 'note') toast(d.text, true);
    // Own branch: a turn's summary, and the actions that change the entry.
    if (ev.kind === 'worktree' && p.worktree) {
      if (d.summary) p.worktree.summary = d.summary;
      if (d.commit || d.published || d.discarded) refresh().catch(() => {});
    }
    scheduleRender();
  };
}

/* ── Rigs ────────────────────────────────────────────────────────────────
 * The server does preflight, per-seat work and renders each outcome; this dialog
 * lists, saves, removes and brings rigs up, showing those lines as text. */
const RIG_PROBLEM = new Set(['failed', 'withheld', 'not-restored']);

function rigOutcomeRows(r) {
  const rows = [];
  (r.outcomes || []).forEach((o, i) => {
    const row = el('div', 'rigrow o-' + o.outcome + (RIG_PROBLEM.has(o.outcome) ? ' bad' : ''));
    row.appendChild(el('span', 'pill', o.outcome));
    row.appendChild(el('span', 't', (r.lines || [])[i] || ''));
    rows.push(row);
  });
  return rows;
}

function rigRefusedRows(e) {
  const reasons = (e && e.body && e.body.refused) || [];
  const rows = [el('div', 'rigrow bad', reasons.length
    ? 'Refused — nothing was started:' : ((e && e.message) || 'failed'))];
  for (const why of reasons) rows.push(el('div', 'rigrow bad', '· ' + why));
  return rows;
}

/* Sign in runs the vendor's own login (`claude auth login`) in a window on the
 * hub's screen; the hub never sees a URL, code or token. `/login` in the composer
 * is a sign-in only on a Claude pane that died of its login. */
function isLoginCommand(p, text) {
  return String(text || '').trim().toLowerCase() === '/login' && !!p &&
         p.agent === 'claude' && p.state === 'dead' && p.deadCause === 'auth';
}

async function startLogin(from, paneId) {
  try {
    const r = await api('/api/claude/login', paneId ? { from, pane: paneId } : { from });
    toast(r.ok ? 'sign-in window opened on the hub machine — sign in there' : r.why, !r.ok);
    await refresh();
    return r;
  } catch (e) {
    toast((e.body && e.body.why) || e.message, true);
    return null;
  }
}

function signInButton(from, paneId) {
  const running = (S.claudeLogin || {}).state === 'running';
  const b = el('button', 'fbtn', running ? 'Signing in…' : 'Sign in');
  b.type = 'button';
  b.disabled = running;
  b.title = 'opens the Claude login in a window on the hub machine; you sign in there';
  b.onclick = async e => { e.stopPropagation(); await startLogin(from, paneId); };
  return b;
}

/* One line about the last sign-in, or '' when there is nothing to say. */
function loginLine(cl) {
  if (!cl || !cl.state || cl.state === 'idle') return '';
  const what = { 'running': 'sign-in window open', 'signed-in': 'signed in',
                 'check-status': 'sign-in unclear', 'closed': 'sign-in window closed',
                 'gave-up': 'stopped watching the sign-in' }[cl.state] || cl.state;
  return cl.why ? `${what}: ${cl.why}` : what;
}

/* Forecasts what Save would write and what Up would refuse, mirroring the server's
 * rigs.save / rigs.preflight. Advisory only: the server's preflight decides. */
function rigSaveHint(panes) {
  const seated = panes.filter(p => p.seat).map(p => '@' + p.seat);
  const unseated = panes.length - seated.length;
  if (!seated.length) return { disabled: true,
    text: 'Nothing to save: no pane has a seat. Give a pane a seat first: click its @ pill.' };
  const n = seated.length;
  return { disabled: false,
    text: `Saves ${n} seated pane${n === 1 ? '' : 's'} (${seated.join(' ')})`
      + (unseated ? `; ${unseated} unseated ${unseated === 1 ? 'is' : 'are'} not saved` : '') };
}

function rigLiveSeats(seats, panes) {
  return (seats || []).filter(seat => panes.some(p =>
    p.seat === seat && p.state !== 'dead' && p.state !== 'detached'));
}

function rigLiveText(live) {
  if (!live.length) return '';
  return `Up will refuse: ${live.map(s => '@' + s).join(' ')} ${live.length === 1 ? 'is' : 'are'} live`;
}

// The list's rows, so a pane changing state re-marks them without a rebuild
// (a rebuild would reset a half-armed Remove or an Up in flight).
let RIG_ROWS = [];

function rigRefreshHints() {
  const dlg = $('#rigdlg');
  if (!dlg || !dlg.open) return;
  const panes = [...S.panes.values()];
  const h = rigSaveHint(panes);
  $('#rig-savehint').textContent = h.text;
  $('#rig-save').disabled = h.disabled;
  for (const { seats, warn } of RIG_ROWS) warn.textContent = rigLiveText(rigLiveSeats(seats, panes));
}

async function renderRigList() {
  const list = $('#rig-list');
  let d;
  try { d = await api('/api/session/rigs'); }
  catch (e) { list.replaceChildren(el('div', 'hint err', e.message)); return; }
  const panes = [...S.panes.values()];
  RIG_ROWS = [];
  const rows = (d.rigs || []).map(r => {
    const row = el('div', 'rigrow');
    row.appendChild(el('span', 't', r.name));
    row.appendChild(el('span', 'hint', r.error ? 'unreadable: ' + r.error
      : (r.seats || []).map(s => '@' + s).join(' ')));
    const warn = el('span', 'rigwarn', r.error ? '' : rigLiveText(rigLiveSeats(r.seats, panes)));
    row.appendChild(warn);
    if (!r.error) RIG_ROWS.push({ seats: r.seats || [], warn });
    // Up stays enabled with a live seat: the server says no, with every reason.
    const up = el('button', 'btn go', 'Up');
    up.type = 'button'; up.disabled = !!r.error;
    up.onclick = () => rigUp(r.name, up);
    const rm = el('button', 'btn', 'Remove');
    rm.type = 'button';
    // Two clicks: a removed rig is a file gone, and there is no undo.
    rm.onclick = async () => {
      if (!rm.dataset.armed) { rm.dataset.armed = '1'; rm.textContent = 'Remove — sure?'; return; }
      try { await api('/api/session/rigs/rm', { name: r.name }); }
      catch (e) { $('#rig-error').textContent = e.message; }
      renderRigList();
    };
    row.appendChild(up); row.appendChild(rm);
    return row;
  });
  list.replaceChildren(...(rows.length ? rows
    : [el('div', 'hint', 'No rigs saved yet. Seat some panes, then save them here.')]));
}

async function rigUp(name, btn) {
  const out = $('#rig-out');
  if (btn) btn.disabled = true;
  out.replaceChildren(el('div', 'hint', `Bringing up ${name}… each seat may take a handshake. ` +
    'Seats start minimized; one that needs you comes back on its own.'));
  try {
    const r = await api('/api/session/rigs/up', { name });
    out.replaceChildren(el('div', 'hint', `rig ${name}:`), ...rigOutcomeRows(r));
    refresh();
  } catch (e) {
    out.replaceChildren(...rigRefusedRows(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openRigs() {
  $('#rig-error').textContent = '';
  $('#rig-out').replaceChildren();
  $('#rig-name').value = '';
  $('#rig-replace').checked = false;
  RIG_ROWS = [];
  renderRigList();
  $('#rigdlg').showModal();
  rigRefreshHints();
}

function wireRigDialog() {
  if (!$('#rigdlg')) return;
  $('#rig-save').onclick = async () => {
    const name = ($('#rig-name').value || '').trim();
    try {
      const r = await api('/api/session/rigs/save', { name, replace: $('#rig-replace').checked });
      $('#rig-error').textContent = '';
      $('#rig-out').replaceChildren(el('div', 'hint',
        `saved ${r.name}: ` + r.seats.map(s => '@' + s).join(' ')));
      renderRigList();
    } catch (e) { $('#rig-error').textContent = e.message; }
  };
  const b = $('#rigsbtn');
  if (b) b.onclick = () => { const n = $('#newdlg'); if (n && n.open) n.close(); openRigs(); };
}

/* ── new-conversation dialog ─────────────────────────────────────────── */
// Posture descriptions, matching the agent's own configOptions wording.
const HINTS = {
  strict: 'Prompts on dangerous operations. Most approvals land in your rail.',
  edits: 'File edits apply without asking; commands and network still ask.',
  auto: 'A classifier approves or denies routine prompts, and escalates what it will not approve. Fewer interruptions, not none.'
};
function wireDialog() {
  const dlg = $('#newdlg');
  // Roles are fetched each time the dialog opens; a role with a lane selects it.
  const fillRoles = async () => {
    const sel = $('#f-role'), hint = $('#rolehint');
    let d = { roles: [] };
    try { d = await api('/api/session/roles'); } catch (e) { /* no roles */ }
    S.roles = d.roles || [];
    sel.replaceChildren(el('option', null, 'None'));
    sel.firstChild.value = '';
    for (const r of S.roles) {
      const o = el('option', null, r.error ? `${r.id} (broken)` : `${r.id} — ${r.description}`);
      o.value = r.error ? '' : r.id;
      o.disabled = !!r.error;
      o.title = r.error || '';
      sel.appendChild(o);
    }
    hint.textContent = S.roles.length ? '' : `no roles yet — add them in ${d.dir || 'the roles folder'}`;
  };
  $('#f-role').onchange = () => {
    const r = (S.roles || []).find(x => x.id === $('#f-role').value);
    $('#rolehint').textContent = r ? `${r.data_class} · instructions go into the composer; nothing is sent until you press send` : '';
    if (r && r.lane && [...$('#f-agent').options].some(o => o.value === r.lane)) {
      $('#f-agent').value = r.lane;
      $('#f-agent').dispatchEvent(new Event('change'));
    }
  };
  $('#new').onclick = () => {
    // Agents in a GROUP collapse to one entry. `group:<id>` is a UI-only sentinel,
    // resolved to the real lane key from the host picker before submit.
    const groups = S.agentGroups || {};
    const sel = $('#f-agent'); sel.innerHTML = '';
    const seen = new Set();
    for (const a of S.agents) {
      if (a.group) {
        if (seen.has(a.group)) continue;      // one entry for the whole family
        seen.add(a.group);
        const g = groups[a.group] || {};
        const members = S.agents.filter(x => x.group === a.group);
        const live = members.filter(x => x.available).length;
        // A group is offerable if ANY member is: the sub-picker greys the rest
        // individually, each with its own reason.
        const o = el('option', null,
                     `${g.label || a.group} (${live})`);
        o.value = `group:${a.group}`; o.disabled = live === 0;
        sel.appendChild(o);
        continue;
      }
      const o = el('option', null, a.available ? a.label : `${a.label} — ${a.why}`);
      o.value = a.key; o.disabled = !a.available;
      sel.appendChild(o);
    }
    // The group's members, each greyed with its own reason when unavailable.
    const fillHost = () => {
      const v = $('#f-agent').value;
      const gid = v.startsWith('group:') ? v.slice(6) : null;
      const row = $('#hostrow'), node = $('#f-host'), hint = $('#hosthint');
      if (!gid) { row.className = 'hide'; hint.textContent = ''; return; }
      row.className = '';
      hint.textContent = (groups[gid] || {}).hint || '';
      node.innerHTML = '';
      for (const m of S.agents.filter(x => x.group === gid)) {
        const o = el('option', null,
                     m.available ? (m.memberLabel || m.key)
                                 : `${m.memberLabel || m.key} — ${m.why}`);
        o.value = m.key; o.disabled = !m.available;
        node.appendChild(o);
      }
      // Land on a member that can actually start, not just the first one.
      const firstLive = S.agents.find(x => x.group === gid && x.available);
      if (firstLive) node.value = firstLive.key;
    };
    // The real lane key behind whatever the two pickers currently show.
    const chosenAgent = () => {
      const v = $('#f-agent').value;
      return v.startsWith('group:') ? $('#f-host').value : v;
    };
    dlg._chosenAgent = chosenAgent;   // the close handler submits this, not #f-agent
    const si = $('#signinrow');
    if (si) {
      si.replaceChildren();
      const lapsed = S.agents.find(a => a.signIn);
      if (lapsed) {
        si.appendChild(el('span', null, `${lapsed.label}: ${lapsed.why} `));
        si.appendChild(signInButton('picker'));
      }
    }
    // Select the first enabled option, so the common path is open and press Start.
    const firstOpt = sel.options?.find?.(o => !o.disabled)
                  || [...(sel.options || [])].find(o => !o.disabled);
    if (firstOpt) sel.value = firstOpt.value;
    $('#f-cwd').value = S.lastCwd || S.defaultCwd || '~';
    // Directory suggestions; the input stays free text.
    const dl = $('#cwdlist'); dl.innerHTML = '';
    for (const d of S.cwdSuggestions || []) {
      const o = document.createElement('option'); o.value = d;
      dl.appendChild(o);
    }
    // Model/effort options come from the server's catalog for the selected agent,
    // so they work with nothing running.
    const fillCfg = () => {
      const cat = (S.catalog || {})[chosenAgent()] || {};
      for (const [sel, cid, hintSel] of
           [['#f-model', 'model', '#modelhint'], ['#f-effort', 'effort', '#efforthint']]) {
        const node = $(sel), hint = $(hintSel), label = node.closest('label');
        const want = node.value;
        node.innerHTML = '';
        const entry = cat[cid] || {};
        const opts = entry.options || [];
        if (!opts.length && entry.value) {
          // The agent runs one fixed model: name it instead of a disabled picker.
          label.style.display = 'none';
          hint.textContent = `${entry.name || cid}: ${entry.value} — set by ` +
            `the agent, not choosable here.`;
        } else if (!opts.length) {
          // Never seen this agent's list: offer only the agent's default.
          label.style.display = '';
          hint.textContent = '';
          const o = el('option', null, 'Default (agent decides)'); o.value = '';
          node.appendChild(o); node.disabled = true;
        } else {
          label.style.display = '';
          hint.textContent = '';
          node.disabled = false;
          // Add an explicit "agent decides" entry unless the list has its own
          // default; otherwise pressing Start would submit the first option.
          const hasOwnDefault = opts.some(o => /^default\b/i.test(o.name || ''));
          if (!hasOwnDefault) {
            const def = el('option', null, 'Default (agent decides)'); def.value = '';
            node.appendChild(def);
          }
          for (const o of opts) {
            const n = el('option', null, o.name || o.value); n.value = o.value;
            node.appendChild(n);
          }
          node.value = want || '';
        }
      }
    };
    // Do not offer a posture Corral cannot impose (only lanes launched through a
    // CLAUDE_CONFIG_DIR obey it).
    const fillPosture = () => {
      const a = S.agents.find(x => x.key === chosenAgent()) || {};
      const sel = $('#f-posture'), hint = $('#posturehint');
      if (a.postureEnforced === false) {
        sel.disabled = true;
        // Blank it, not just grey it: the close handler reads `.value`, and empty
        // means the lane's own default (the key is then omitted).
        sel.value = '';
        hint.textContent = `${a.label} manages its own permissions — Corral ` +
                           `cannot set this, and will not pretend it did.`;
      } else {
        sel.disabled = false;
        sel.value = localStorage.getItem('corral.posture') || 'auto';
        hint.textContent = HINTS[sel.value];
      }
    };
    // Order matters: fillHost resolves which lane the other two describe.
    $('#f-agent').onchange = () => { fillHost(); fillCfg(); fillPosture(); wtProbeSoon(dlg, 0); };
    // Switching host changes the lane, so model and posture follow it.
    $('#f-host').onchange = () => { fillCfg(); fillPosture(); wtProbeSoon(dlg, 0); };
    // Own branch: the row follows the folder (debounced) and the lane.
    dlg._wt = { show: false };
    paintWtRow(dlg._wt);
    $('#f-cwd').oninput = () => wtProbeSoon(dlg, 300);
    wtProbeSoon(dlg, 0);
    fillHost();
    fillCfg();
    fillPosture();
    fillRoles();
    $('#f-quick').checked = !!dlg._quickSet; dlg._quickSet = false;
    $('#quickhint').textContent = 'now: ' + quickLabel();
    dlg.showModal();
  };
  $('#f-posture').onchange = e => { $('#posturehint').textContent = HINTS[e.target.value]; };
  // With a Later time set, Start arms the conversation via schedule/add instead
  // of opening it.
  dlg.addEventListener('close', async () => {
    if (dlg.returnValue !== 'ok') return;
    const cwd = $('#f-cwd').value.trim();
    S.lastCwd = cwd;
    // Remember the posture only when it was a choice, not a blanked control.
    const posture = $('#f-posture').disabled ? '' : $('#f-posture').value;
    if (posture) localStorage.setItem('corral.posture', posture);
    const common = { agent: dlg._chosenAgent(), cwd,
                     model: $('#f-model').value, effort: $('#f-effort').value,
                     role: $('#f-role').value };
    // Omit posture rather than send it empty, so it is never read as a deliberate
    // "none".
    if (posture) common.posture = posture;
    // Own branch: only when checked, visible, and probed for this folder and lane.
    const wtPick = wtSubmit(dlg._wt, $('#f-wt').checked, cwd, common.agent);
    if (dlg._wt && dlg._wt.checkbox && dlg._wt.cwd === cwd) wtRemember(dlg._wt.top, $('#f-wt').checked);
    if (wtPick.error) { toast(wtPick.error, true); return; }
    if ($('#f-quick').checked) {
      const a = S.agents.find(x => x.key === common.agent) || {};
      const m = $('#f-model'), ef = $('#f-effort');
      localStorage.setItem('corral.quick', JSON.stringify({
        agent: common.agent, cwd, model: common.model, effort: common.effort,
        posture, label: a.memberLabel || a.label || common.agent,
        modelName: m.value ? m.options[m.selectedIndex]?.text : '',
        effortName: ef.value ? ef.options[ef.selectedIndex]?.text : '' }));
      $('#f-quick').checked = false;
      toast('⚡ now starts ' + quickLabel());
    }
    const when = $('#f-when').value;
    if (when && wtPick.worktree) {
      toast('an own branch cannot be scheduled yet: start it now, or untick Own branch', true);
      return;
    }
    if (when) {
      // Later: arm it, open nothing now. The prompt is stored as typed (a
      // role's instructions are inlined server-side when it is armed).
      try {
        const r = await api('/api/session/schedule/add', { ...common, when,
          repeat: $('#f-repeat').value, prompt: $('#f-prompt').value });
        toast(`scheduled for ${new Date(r.job.at).toLocaleString()}`);
        for (const n of (r.job.role_notes || [])) toast(n);
        $('#f-when').value = ''; $('#f-prompt').value = '';
        await refresh();
      } catch (e) { toast(e.message, true); }
      return;
    }
    await startConversation({ ...common, ...wtPick });
  });
  $('#new-quick').onclick = quickStart;
  // Say exactly what ⚡ will start, read at hover time so it never goes stale.
  $('#new-quick').onmouseenter = e => {
    e.currentTarget.title = `Start ${quickLabel()} now · right-click to change`;
  };
  $('#new-quick').oncontextmenu = e => {
    e.preventDefault();
    dlg._quickSet = true;
    $('#new').click();
  };
}

// Open a pane now. Shared by the dialog's Start and the ⚡ quick button.
async function startConversation(common) {
  try {
    const d = await api('/api/session/new', common);
    S.panes.set(d.pane.id, d.pane);
    // Starting a conversation focuses it, so ⌘K attaches to it.
    S.focus = d.pane.id;
    render();
    if (d.pane.state === 'dead') toast('agent failed to start: ' + (d.pane.error || ''), true);
    else if (d.preamble) insertIntoComposer(d.pane.id, d.preamble + '\n\n---\n\n',
      `role ${common.role}: its instructions are in the box — add your ask and send`);
    for (const n of (d.notes || [])) toast(n);
  } catch (e) { toast(e.message, true); }
}

// The lane the dialog would land on: walk agents in menu order and take the
// first live one. A group's menu entry sits at its first member's position and
// resolves to its first LIVE member, which this walk also yields.
function defaultAgent() {
  return (S.agents || []).find(a => a.available) || null;
}

// ⚡ starts the saved preset, or the dialog's defaults (agent's model/effort, no
// role, last posture). A preset whose lane is down says so rather than switching.
function quickPreset() {
  try { return JSON.parse(localStorage.getItem('corral.quick') || 'null'); }
  catch (e) { return null; }
}
function quickLabel() {
  const q = quickPreset();
  if (q) return [q.label, q.modelName, q.effortName && q.effortName + ' effort']
    .filter(Boolean).join(' · ') + ` in ${q.cwd || '~'}`;
  const a = defaultAgent();
  return a ? `${a.memberLabel || a.label} · agent's default model in ` +
             `${S.lastCwd || S.defaultCwd || '~'}` : 'no agent available';
}
async function quickStart() {
  const q = quickPreset();
  if (q) {
    const a = (S.agents || []).find(x => x.key === q.agent);
    if (!a || !a.available) {
      toast(`⚡ preset lane ${q.label} is not available` +
            (a && a.why ? ` — ${a.why}` : '') + '. Right-click ⚡ to change it.', true);
      return;
    }
    const common = { agent: q.agent, cwd: q.cwd || S.defaultCwd || '~',
                     model: q.model || '', effort: q.effort || '', role: '' };
    if (q.posture && a.postureEnforced !== false) common.posture = q.posture;
    return startConversation(common);
  }
  const a = defaultAgent();
  if (!a) { toast('no agent is available to start', true); return; }
  const common = { agent: a.key, cwd: S.lastCwd || S.defaultCwd || '~',
                   model: '', effort: '', role: '' };
  // Same rule as the dialog: send a posture only to a lane that obeys one.
  if (a.postureEnforced !== false)
    common.posture = localStorage.getItem('corral.posture') || 'auto';
  await startConversation(common);
}

/* ── port: carry a conversation to another lane ───────────────────────────
 * The preview is the exact pack; Send posts its sha and the hub refuses if the
 * transcript has grown since. */
async function openPort(src) {
  const dlg = $('#portdlg'), sel = $('#p-agent'), go = $('#p-go');
  sel.replaceChildren();
  for (const a of S.agents.filter(a => a.available && !a.key.startsWith('host:'))) {
    const o = el('option', null, a.label); o.value = a.key; sel.appendChild(o);
  }
  // A seat stays with this pane; the new one starts without one.
  const seatNote = $('#p-seatnote');
  if (seatNote) seatNote.textContent = src.seat
    ? `@${src.seat} stays with this pane — the new one starts without a seat.` : '';
  let pack = null;
  const load = async () => {
    go.disabled = true; pack = null;
    $('#p-text').textContent = 'composing…';
    try {
      pack = await api('/api/session/port/preview', { pane: src.id, agent: sel.value });
      $('#p-hint').textContent = `${pack.chars} chars · ${pack.turns_carried} of ` +
        `${pack.turns_total} turns · goes to ${pack.vendor} · sha ${pack.sha.slice(0, 12)}`;
      $('#p-text').textContent = pack.text;
      go.disabled = false;
    } catch (e) { $('#p-hint').textContent = e.message; $('#p-text').textContent = ''; }
  };
  sel.onchange = load;
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok' || !pack) return;
    try {
      const r = await api('/api/session/port', { pane: src.id, agent: sel.value, sha: pack.sha });
      S.panes.set(r.pane.id, r.pane); S.focus = r.pane.id; render();
      toast(r.delivered ? 'carried — the new pane read the transcript'
                        : 'the pane opened but the transcript was NOT delivered: ' + r.error, !r.delivered);
    } catch (e) { toast(e.message, true); }
  };
  dlg.showModal();
  await load();
}

/* ── ⌘K palette ──────────────────────────────────────────────────────────
 * Light's navigation: one ranked list of open panes, closed conversations and
 * notes. A note hit attaches to a composer (Enter: focused pane; ⇧Enter: new
 * pane in its directory) and is never sent. */
const PAL = { sel: 0, rows: [], t: null, seq: 0, status: null };

function openPalette() {
  const q = $('#pal-q');
  q.value = '';
  paletteResults('');
  $('#palette').showModal();
  requestAnimationFrame(() => q.focus());
  // Fetched once per open, to explain an empty result.
  api('/api/content/status').then(s => { PAL.status = s; }).catch(() => {});
}

/* Which pane an attach lands in: the focused pane, else the only usable one.
 * Never a minimized, detached, dead or shell pane. */
function attachTarget() {
  const focused = S.panes.get(S.focus);
  const usable = p => !p.minimized && p.state !== 'dead' &&
    p.state !== 'detached' && !p.agent.startsWith('host:');
  if (focused && usable(focused)) return focused;
  const shown = [...S.panes.values()]
    .filter(usable);
  return shown.length === 1 ? shown[0] : null;
}

function paletteResults(query) {
  const seq = ++PAL.seq;
  clearTimeout(PAL.t);
  const needle = query.trim().toLowerCase();
  const rows = [];
  const focused = attachTarget();

  // Action rows first, so a verb's own name reaches it before a pane title.
  if (!needle || 'new conversation'.includes(needle)) {
    rows.push({ kind: 'action', label: 'New conversation', sub: 'action' });
  }
  if (!needle || 'what the agents did digest'.includes(needle)) {
    rows.push({ kind: 'digest', label: 'What the agents did — last 24h', sub: 'digest' });
  }
  // The Rigs verb, matched by its label.
  const rigsRow = { kind: 'rigs', label: 'Rigs · save or bring up your seats', sub: 'rigs' };
  if (!needle || rigsRow.label.toLowerCase().includes(needle)) rows.push(rigsRow);

  for (const [id, p] of S.panes || []) {
    const label = p.title || p.label;
    // A seat is searchable with or without its @.
    const seat = p.seat ? '@' + p.seat : '';
    if (!needle || label.toLowerCase().includes(needle)
        || (p.cwd || '').toLowerCase().includes(needle)
        || (seat && seat.includes(needle.startsWith('@') ? needle : '@' + needle))) {
      rows.push({ kind: 'pane', label: seat ? `${label} ${seat}` : label,
                  paneId: id, ssh: p.agent.startsWith('host:'),
                  sub: p.state + ' · ' + ((p.cwd || '').split('/').pop() || '') });
    }
  }
  for (const a of S.archived || []) {
    const label = a.title || a.id;
    if (needle && !label.toLowerCase().includes(needle)) continue;
    rows.push({ kind: 'archived', label, paneId: a.id, sub: 'archived' });
  }

  renderPalette(rows.slice(0, 30), needle);
  if (needle.length < 2) return;

  // Debounced and sequence-guarded so a stale response cannot repaint the list.
  PAL.t = setTimeout(async () => {
    // Notes and pane transcripts are searched separately; either may fail alone.
    const [d, t] = await Promise.all([
      api('/api/search?q=' + encodeURIComponent(needle)).catch(() => ({ hits: [] })),
      api('/api/session/search?q=' + encodeURIComponent(needle)).catch(() => ({ hits: [] }))]);
    if (seq !== PAL.seq) return;
    const hits = (d.hits || []).map(h => ({
      kind: 'content', label: h.title, id: h.id,
      sub: h.corpus, snippet: h.snippet,
      // The attach target, decided at build time so the row can say what it does;
      // undefined means it offers to open a pane.
      pane: focused }));
    const said = (t.hits || []).slice(0, 15).map(h => ({
      kind: 'said', label: h.title, paneId: h.pane, closed: h.closed,
      sub: `${h.agent} · ${h.kind}${h.closed ? ' · archived' : ''}`,
      snippet: h.snippet }));
    const err = [d.error, t.error].filter(Boolean).join(' · ');
    renderPalette([...rows, ...said, ...hits].slice(0, 50), needle, err);
  }, 160);
}

function renderPalette(rows, needle, contentError) {
  PAL.rows = rows; PAL.sel = 0;
  const res = $('#pal-res');
  res.innerHTML = '';
  if (contentError) res.appendChild(el('div', 'palnote', contentError));
  if (!rows.length) {
    // Distinguish "nothing matches" from an empty or unconfigured index.
    const s = PAL.status;
    if (s && !(s.roots || []).length) {
      res.appendChild(el('div', 'calm', s.error
        || 'No content roots configured yet.'));
    } else if (s && !s.pages) {
      res.appendChild(el('div', 'calm',
        'The index is empty — nothing indexable under the configured roots.'));
    } else {
      res.appendChild(el('div', 'calm', 'Nothing matches.'));
    }
    return;
  }
  rows.forEach((r, i) => {
    const row = el('div', 'palrow' + (i === 0 ? ' on' : ''));
    row.appendChild(el('span', 'pill corp', r.sub));
    const t = el('div', 'palt');
    t.appendChild(el('span', 't', r.label));
    // The snippet is file-derived text: set via textContent, never parsed as markup.
    if (r.snippet) t.appendChild(el('span', 'palsnip', r.snippet));
    row.appendChild(t);
    if (r.kind === 'content') {
      row.appendChild(el('span', 'palhint',
        r.pane ? '↵ attach · ⇧↵ new pane' : '↵ new pane here'));
    } else if (r.kind === 'pane') {
      // Offer quote only when there is another pane for the words to go to.
      const t = attachTarget();
      row.appendChild(el('span', 'palhint',
        t && t.id !== r.paneId && !r.ssh
          ? '↵ focus · ⇧↵ quote its answer into ' + (t.title || t.label)
          : '↵ focus'));
    }
    row.onmousedown = e => e.preventDefault();      // keep focus in the input
    row.onclick = e => activatePalette(r, e.shiftKey);
    res.appendChild(row);
  });
}

async function activatePalette(row, newPane) {
  $('#palette').close();
  if (row.kind === 'action') return $('#new').click();
  if (row.kind === 'rigs') return openRigs();
  if (row.kind === 'said') {
    // A hit in a closed conversation reopens it (detached, as Archive does).
    if (!S.panes.has(row.paneId)) {
      try { await api('/api/session/reopen', { pane: row.paneId }); await refresh(); }
      catch (e) { return toast(e.message, true); }
    }
    return focusPane(row.paneId);
  }
  if (row.kind === 'digest') {
    // A mechanical digest counted from events; lands in a composer or the
    // clipboard, never sent.
    let d;
    try { d = await api('/api/session/digest?hours=24'); }
    catch (e) { return toast(e.message, true); }
    const t = attachTarget();
    if (t) return insertIntoComposer(t.id, d.text, 'digest of the last 24h — nothing sent');
    try { await navigator.clipboard.writeText(d.text); toast('digest copied — no pane to put it in'); }
    catch { toast('open a pane to receive the digest', true); }
    return;
  }
  if (row.kind === 'pane') {
    const target = newPane ? attachTarget() : null;   // ⇧↵ = quote, ↵ = focus
    if (target && target.id !== row.paneId) return quoteInto(row.paneId, target.id);
    return focusPane(row.paneId);
  }
  if (row.kind === 'archived') {
    try {
      await api('/api/session/reopen', { pane: row.paneId });
      await refresh();
      focusPane(row.paneId);
    } catch (e) { toast(e.message, true); }
    return;
  }
  if (row.kind !== 'content') return;
  const target = newPane ? null : attachTarget();
  await attachContent(row.id, target ? target.id : null);
}

/* Attach a note to a pane's composer, or to a new pane opened in its directory.
 * The server decides the text (a path for a lane with tools, else an excerpt). */
async function attachContent(id, paneId) {
  let d;
  try { d = await api('/api/content/attach', { id, pane: paneId || '' }); }
  catch (e) { return toast(e.message, true); }
  if (!paneId) {
    // No pane to attach to: open one in the file's directory.
    try {
      const from = attachTarget();
      const agent = (from && from.agent)
        || (S.agents.find(a => a.available) || {}).key
        || 'claude';
      const r = await api('/api/session/new',
                          { agent, cwd: d.dir,
                            posture: localStorage.getItem('corral.posture') || 'auto' });
      S.panes.set(r.pane.id, r.pane);
      paneId = r.pane.id;
      render();
      // Ask again: the new pane's lane decides path vs excerpt.
      d = await api('/api/content/attach', { id, pane: paneId });
    } catch (e) { return toast(e.message, true); }
  }
  insertIntoComposer(paneId, d.text, d.mode === 'excerpt'
      ? `quoted "${d.title}" — this lane has no tools, so the text came along`
      : `referenced "${d.title}" — the agent will read it through its own gate`);
}

/* Put text at the head of a pane's composer and leave the cursor after it.
 * Shared by note attach and pane quote: both are composer conveniences that
 * send nothing by themselves. */
function insertIntoComposer(paneId, text, msg) {
  focusPane(paneId);
  requestAnimationFrame(() => {
    const box = document.querySelector(`[data-pane="${paneId}"] .composer textarea`)
             || document.querySelector(`[data-pane="${paneId}"] .composer input`);
    if (!box) return toast('attached, but that pane has no composer', true);
    box.value = text + (box.value || '');
    box.focus();
    // Cursor after the inserted text, ready for the question.
    const at = text.length;
    box.setSelectionRange(at, at);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    toast(msg);
  });
}

/* Quote one pane's last answer into another's composer. The server decides
 * the bytes (bounded, fenced, attributed); nothing is sent until the operator
 * presses send in the target. */
async function quoteInto(fromId, toId) {
  let d;
  try { d = await api('/api/session/quote', { from: fromId, pane: toId }); }
  catch (e) { return toast(e.message, true); }
  insertIntoComposer(toId, d.text,
    `quoted ${d.label}'s answer` + (d.complete ? '' : ' — still being written')
      + (d.clipped ? ' (truncated)' : ''));
}

/* Panes that can take a composed prompt right now: live, on screen, with a
 * chat composer. The same test attachTarget applies to one pane, over all. */
function composablePanes() {
  return [...S.panes.values()].filter(p => !p.minimized && p.state !== 'dead'
    && p.state !== 'detached' && !p.agent.startsWith('host:'));
}

const CROSSFEED_DEFAULT = 'Round two. Below are the other arms\' answers to the '
  + 'same question. Attack them: where are they wrong, what did they miss, and '
  + 'does your own answer change? Say what you now reject and end with your '
  + 'revised answer.';

/* Round two of a panel: each composable pane gets every other's last answer under
 * an editable preamble, sent as its own user turn. */
async function crossfeed() {
  // Only panes that have been asked something take part; the rest are named in
  // the confirmation.
  const all = composablePanes();
  const asked = (p) => (p.events || []).some(e => e.kind === 'user');
  const panes = all.filter(asked);
  const idle = all.filter(p => !asked(p)).map(p => p.title || p.label);
  if (panes.length < 2) {
    return toast('cross-feed needs two or more panes that have been asked something'
      + (idle.length ? ` — never asked: ${idle.join(', ')}` : ''), true);
  }
  const text = window.prompt(
    `Cross-feed ${panes.length} panes: ${panes.map(p => p.title || p.label).join(', ')}.`
    + (idle.length ? `\nLeaving out, never asked: ${idle.join(', ')}.` : '')
    + `\nPreamble each arm gets above the others' answers:`,
    CROSSFEED_DEFAULT);
  if (text === null) return;
  try {
    const r = await api('/api/session/crossfeed', { panes: panes.map(p => p.id), text });
    const bad = Object.entries(r.results || {}).filter(([, err]) => err);
    toast(bad.length
      ? `cross-fed ${r.sent} of ${panes.length} — ` + bad.map(([id, err]) =>
          `${(S.panes.get(id) || {}).title || id}: ${err}`).join('; ')
      : `cross-fed ${r.sent} panes`, !!bad.length);
  } catch (e) { toast(e.message, true); }
}

function movePaletteSel(delta) {
  if (!PAL.rows.length) return;
  const rows = [...$('#pal-res').children].filter(n => n.classList.contains('palrow'));
  rows[PAL.sel]?.classList.remove('on');
  PAL.sel = (PAL.sel + delta + PAL.rows.length) % PAL.rows.length;
  rows[PAL.sel]?.classList.add('on');
  rows[PAL.sel]?.scrollIntoView({ block: 'nearest' });
}

function wirePalette() {
  const q = $('#pal-q');
  $('#search-trigger').onclick = openPalette;
  $('#crossfeed').onclick = crossfeed;
  q.oninput = () => paletteResults(q.value);
  q.onkeydown = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); movePaletteSel(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); movePaletteSel(-1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (PAL.rows[PAL.sel]) activatePalette(PAL.rows[PAL.sel], e.shiftKey);
    }
  };
  // Global, and not swallowed inside a composer, so ⌘K works while writing.
  document.addEventListener('keydown', e => {
    for (const k of KEYS) {
      if (k.match && k.match(e)) { e.preventDefault(); k.run(); return; }
    }
  });
}

/* ── the keyboard ────────────────────────────────────────────────────────
 * One table: the global handler dispatches from it and the `?` overlay lists it.
 * Entries without `match` are handled by the focused control (composer, find
 * bar) and only documented here. */
const KEYS = [
  { combo: '⌘K', alt: 'Ctrl+K', what: 'Search conversations and jump to one',
    match: e => (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k',
    run: () => { $('#palette').open ? $('#palette').close() : openPalette(); } },
  { combo: '?', what: 'Show this list',
    match: e => e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey
                && !isTypingTarget(e.target),
    run: () => toggleKeys() },
  { combo: 'Esc', what: 'Close this list, or the search',
    match: e => e.key === 'Escape' && $('#keysdlg') && $('#keysdlg').open,
    run: () => toggleKeys(false) },
  { combo: 'r', where: 'on a focused own-branch pane', what: 'Review its changes',
    match: e => reviewKey(e, reviewTarget(), anyDialogOpen()),
    run: () => openReview(reviewTarget()) },
  { combo: 'Enter', where: 'in a message box', what: 'Send' },
  { combo: 'Shift+Enter', where: 'in a message box', what: 'Newline, do not send' },
  { combo: '⌘Enter', alt: 'Ctrl+Enter', where: 'in a message box',
    what: 'Send to EVERY pane that can take a prompt' },
  { combo: '1…9', where: 'on a pane with a permission card',
    what: 'Answer the card with that option' },
  { combo: 'Esc', where: 'on a pane with a permission card',
    what: 'Refuse the card; on a busy pane, interrupt the turn' },
  { combo: '↑ / ↓', where: 'in an empty terminal box',
    what: 'Walk back through what you typed before' },
  { combo: 'Ctrl+C', where: 'in an empty terminal box',
    what: 'Interrupt the running command' },
];

/* True when focus is in a text field, so `?` can still be typed there. */
function isTypingTarget(t) {
  if (!t) return false;
  const tag = (t.tagName || '').toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
      || t.isContentEditable === true;
}

function toggleKeys(want) {
  const dlg = $('#keysdlg');
  if (!dlg) return;
  const open = want === undefined ? !dlg.open : want;
  if (!open) return dlg.close();
  const body = $('#keys-body');
  body.replaceChildren();
  for (const k of KEYS) {
    const row = el('div', 'keysrow');
    const combo = el('span', 'keyscombo', k.combo + (k.alt ? ` / ${k.alt}` : ''));
    row.appendChild(combo);
    const what = el('span', 'keyswhat', k.what);
    row.appendChild(what);
    // Where it applies; omitted means global.
    if (k.where) row.appendChild(el('span', 'keyswhere', k.where));
    body.appendChild(row);
  }
  dlg.showModal();
}

function wireKeysButton() {
  const b = $('#keysbtn');
  // A page without the button wires nothing rather than throwing.
  if (b) b.onclick = () => toggleKeys();
}

function wireMobileActions() {
  $('#mobile-new').onclick = () => $('#new').click();
  $('#mobile-search').onclick = openPalette;
  $('#mobile-pane').onchange = e => {
    if (e.target.value) focusPane(e.target.value);
  };
}

/* ── focus ───────────────────────────────────────────────────────────────
 * Open a conversation: mark it focused and scroll it into view. */
function focusPane(id) {
  S.focus = id;
  render();
  requestAnimationFrame(() =>
    document.querySelector(`[data-pane="${id}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
}

/* ── PWA ─────────────────────────────────────────────────────────────── */
// Registered only so the browser offers "Install app"; it caches nothing (see
// sw.js). Needs a secure context (https or localhost), else it no-ops.
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

/* ── boot ────────────────────────────────────────────────────────────── */
async function start() {
  $('#app').classList.remove('hide');
  wireThemes();
  wireDialog();
  wireRigDialog();
  wireRail();
  wireCopySelect();
  wirePalette();
  wireSeat();
  wireReview();
  wireKeysButton();
  wireSecKeys();
  wireMobileActions();
  // Stream first, then snapshot, so no event falls in the gap between them.
  connect();
  await refresh();
  // Re-check display state periodically so quiet panes age into `idle`.
  setInterval(displayTick, DISPLAY_TICK_MS);
  setInterval(workingTick, 1000);     // the busy spinners' seconds
  // Re-sync when the tab becomes visible: a sleeping tab may have lost the
  // stream before onerror fired.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh().catch(() => {});
    markSeen();
  });
  window.addEventListener('focus', markSeen);
}

(async function boot() {
  try {
    await api('/api/state');
  } catch (e) {
    if (e.status === 401) return pair();       // not paired yet — expected
    $('#pair').classList.remove('hide');
    $('#pairnote').textContent = 'Corral Light is unreachable: ' + e.message;
    return;
  }
  start();
})();
