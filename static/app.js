// Corral — the front end. Vanilla ES modules, no build step.
//
// State lives on the server; this renders it. Two inputs: a snapshot from
// /api/state on load, and a single SSE stream carrying every pane's events.
// Panes are re-rendered from their own event list, so a reload is identical to
// having watched it live — which is the whole reason events are persisted.

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
// Measured palettes; taste is Craig's call, not something to guess at again
// (four passes went that way). Every one states its numbers in style.css and
// is asserted by the selftest. Applied before first paint, so no flash of the
// wrong one.
const THEMES = [
  ['laundry', ['#e7e9e5', '#3a4660', '#d09a73'], 'Laundry', 'Cool linen & French blue', 'light'],
  ['dusk', ['#202632', '#8299c3', '#e3a173'], 'Dusk', 'Soft twilight & apricot', 'dark'],
  ['nocturne', ['#080c17', '#455db0', '#ff6f8b'], 'Nocturne', 'Indigo, violet & rose', 'dark'],
  ['umber', ['#1d1510', '#8f5031', '#f0c66a'], 'Umber', 'Espresso, copper & gold', 'dark'],
  ['slate', ['#11171d', '#4d86aa', '#82d99a'], 'Slate', 'Steel, ice & signal green', 'dark'],
  ['ink', ['#090b0f', '#587fc2', '#a78bfa'], 'Ink', 'Graphite & spectral light', 'dark'],
  ['parchment', ['#f0ece3', '#2f5892', '#c49455'], 'Parchment', 'Warm paper, navy & ochre', 'light'],
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
    // `data-theme` belongs ONLY on <html>: putting it here would activate the
    // stylesheet's full [data-theme] block inside this option, so a dark
    // option in a light menu would paint its label with dark-theme text.
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
// Shipped default is 'ink', not 'laundry' -- Craig asked 2026-08-22 for
// something closer to a dark Notion-style look; Ink was already one of the
// four measured/contrast-checked palettes above, so this points the default
// at it rather than inventing a new one. A saved localStorage preference
// (any theme) always wins -- this only changes what a browser that has never
// chosen sees. Full rationale + scope boundary: vault 06 Logs/Decisions/.
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
  // A 401 must NEVER trigger location.reload(). boot() begins by calling
  // /api/state, which 401s precisely when you are not paired yet — reloading
  // there reloads into the same 401 forever and the pairing screen never gets
  // to render. Shipped that way 2026-08-01; Craig: "it appears to be looping."
  // Callers decide what a 401 means; this only reports it.
  if (!r.ok) {
    const err = new Error(d.error || `${r.status} ${r.statusText}`);
    err.status = r.status;
    throw err;
  }
  return d;
}

// One place decides what an expired session looks like: show the pairing
// screen in situ. No navigation, so no loop is reachable from here either.
function relock() {
  if (S.es) { S.es.close(); S.es = null; }
  $('#app').classList.add('hide');
  pair();
}

/* ── pairing ─────────────────────────────────────────────────────────── */
let pairTimers = [];
async function pair() {
  pairTimers.forEach(clearInterval); pairTimers = [];
  $('#pair').classList.remove('hide');
  let code, ttl, how;
  try { ({ code, ttl, how } = await api('/api/pair/new')); }
  catch (e) { $('#pairnote').textContent = 'Cannot reach Corral Light: ' + e.message; return; }
  $('#paircode').textContent = code;
  // The command comes from the SERVER, not from a template here. Light and the
  // full Corral have different CLI names, and a pairing screen that prints the
  // other product's command is an instruction that cannot work — measured
  // 2026-08-31, this screen said `corral pair` on a corral-light hub.
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

/* ── rendering: a pane ───────────────────────────────────────────────── */
// A host:<name> shell lane is a terminal, not a conversation (Craig,
// 2026-08-24: "more of a terminal design and less of a chat design") — its
// transcript and composer render monospace, prompt-prefixed, bubble-free.
//
// The fork claimed this mode was "absent rather than dormant". It was not: the
// branches survived intact and `term` was pinned to a literal `false`, which is
// dormant wearing absent's clothes — the comment was load-bearing and wrong.
// Restoring the lane (2026-09-01) needed one expression, not a rendering mode.
const isTerm = p => (p.agent || '').startsWith('host:');

/* ── markdown, the mdview manner ─────────────────────────────────────────
 * Agent replies arrive as markdown and used to render as raw text — every
 * **bold**, fence and table shipped as punctuation. Same trust posture as
 * mdview.py (the Library's renderer): DOM built only from createElement/
 * textContent, no innerHTML of content ever, links only http(s) and only as
 * links, images degrade to links. Partial input (a still-streaming fence)
 * must render sanely, because this runs on every SSE tick. */
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
// The options actually offered for a request — oversize keeps only refusals,
// exactly permCard's own rule, so key N always matches button N. A function
// declaration, not a const: selftest_permcard.mjs lifts it out by name.
function permOptions(d) {
  return (d.options || []).filter(o => !d.oversize || String(o.kind || '').startsWith('reject'));
}

function renderLog(p) {
  // A transcript should read like a conversation. Tool calls, plans and
  // lifecycle noise are collapsed into one quiet line per run -- Craig: "hide
  // all the tool calls and info that I don't necessarily need to see."
  // Nothing is DISCARDED: the line expands, and the eye in the header reveals
  // everything permanently. Permissions and real errors are never collapsed;
  // those are the two things he must not miss.
  const log = el('div', 'log');
  const term = isTerm(p);
  const detailed = S.detail.has(p.id);
  let textBuf = null, thoughtBuf = null, pendingSteps = [], stepIx = new Map();
  // ⎿ rows held open live OUTSIDE this per-tick rebuild (panel 2026-08-30:
  // click-to-expand kept its flag in the throwaway closure, so the next SSE
  // tick instantly re-collapsed it). Bounded: pruned below to the tool ids
  // this pass actually rendered, so it can never outgrow the capped slice.
  const xopen = TEXPAND.get(p.id) || new Set();
  const xseen = new Set();

  // `/clear` folds everything up to and including its own marker out of view
  // (sessions.py Pane.send — the SDK's own `conversation_reset` for this text
  // never reaches us, the vendored ACP adapter drops it on the wire, so
  // Corral marks the fold itself). Nothing on disk or in p.events is
  // discarded; this is purely which events become DOM this pass.
  let clearIx = -1;
  for (let i = p.events.length - 1; i >= 0; i--) {
    if (p.events[i].kind === 'cleared') { clearIx = i; break; }
  }
  const visible = clearIx >= 0 ? p.events.slice(clearIx + 1) : p.events;

  // Transcript paging (Phase 5b): the ring holds the tail; older events live
  // only in events.jsonl. If this pane's history starts past seq 1, offer
  // the disk read — 200 events a click, prepended in place. Skipped right
  // after a clear: everything a disk fetch would surface is pre-clear, i.e.
  // exactly what the fold just hid on purpose.
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

  // ACP sends one `tool_call` and then a stream of `tool_call_update`s for
  // the SAME toolCallId. Rendering each as its own step inflated the count
  // about 5x on every pane measured (2026-08-01: 415 "steps" for 79 real
  // tool calls under Claude, 24 for 5 under Grok) — so the collapsed line,
  // whose whole job is to tell you how much you are not looking at, was
  // overstating it fivefold. One row per actual call; latest status wins.
  // Merge only the fields an update actually carries. A plain latest-wins
  // Object.assign loses the title: Claude's stream is
  // ("Terminal", pending) -> ("cd … && git log …", null) -> (null, completed),
  // so the last message nulls the one field worth reading and every step
  // renders as the word "tool".
  const pushStep = rec => {
    const k = rec.id;
    if (k && stepIx.has(k)) {
      const cur = pendingSteps[stepIx.get(k)];
      // An empty array is also "this update carries nothing": sessions.py
      // emits content:[] on every contentless tool_call_update, and letting
      // it through blanked the accumulated preview mid-stream (Gemini
      // adversarial review 2026-08-31, confirmed both sides in source).
      for (const [f, v] of Object.entries(rec))
        if (v != null && v !== '' && !(Array.isArray(v) && !v.length)) cur[f] = v;
      return;
    }
    if (k) stepIx.set(k, pendingSteps.length);
    pendingSteps.push(rec);
  };
  // Native-Claude tool rows: ⏺ title, then ⎿ result-preview lines from the
  // content/locations sessions.py already captures (and this UI used to drop).
  // Three lines by default; the row expands on click. Status is the dot's
  // colour, the way the real TUI paints it, not a word.
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
  // The agent's todo list, rendered as the native TUI draws it — one glyph
  // per entry, the in-progress one bright — instead of the old "plan · N
  // steps" string that hid the plan itself.
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

  // Transcript soft-cap (DESIGN-2 Phase 5 harden): the DOM, not the data, is
  // what was unbounded -- every visible pane's WHOLE log was rebuilt from
  // p.events on every SSE tick, which is the thing the README named as "the
  // first thing to feel slow at ten busy panes." p.events itself keeps every
  // event up to the existing MAX_EVENTS ring (4,000); only how much of it
  // gets turned into DOM nodes each render is bounded here, and a click
  // raises the cap for that one pane on demand -- nothing is discarded.
  // Thought events never spend the display cap while the eye is off (panel
  // 2026-08-30): they render nothing then, so letting them count let a long
  // hidden monologue evict the user prompt and permission cards from view.
  // The server now coalesces them too; this also covers replayed old logs.
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

  // A permission's OUTCOME (answered vs. expired) lives in a later event, not
  // in `p.pending` — that array only says what's ACTIONABLE right now, so a
  // request that was answered five minutes ago and one that just timed out
  // unanswered both read as "not pending" and rendered as the identical
  // word "answered". Resolve from the transcript itself, over the full
  // visible ring (not just the capped slice below), so history replay gets
  // the same answer live SSE handling already computes for `p.pending`.
  //
  // Paired by POSITION, not by requestId alone. A requestId is the agent's own
  // JSON-RPC id, unique only among the requests it has IN FLIGHT — it is free
  // to reuse one the moment the previous is answered, and Grok reuses `0` for
  // every permission it ever asks (measured on pane 495d803d, 2026-08-31: two
  // cards 18,000 events apart, both requestId "0"). So one transcript holds
  // many cards under one id. Keyed by id alone, every old card inherited the
  // newest outcome — and, far worse, the LIVENESS test below (`is this id
  // pending?`) said yes to all of them, so a card from an hour ago re-armed
  // its buttons carrying an hour-old digest. Clicking it posted that stale
  // digest and the server refused it, correctly and unanswerably. That is the
  // freeze Craig hit. Walk forward instead and bind each permission to the
  // first outcome that follows IT; whatever is still open at the end is the
  // one — and the only one — the agent is actually blocked on.
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
      // Thinking, the native way: gray italic, and only behind the same
      // "every step" eye that reveals tool calls — quiet by default.
      // Text is flushed first so a thought can never render ahead of the
      // reply text that preceded it in the stream (panel 2026-08-30).
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
          log.appendChild(el('div', 'msg user', d.text || ''));
        }
        break;
      case 'tool':
        flushText();
        if (d.id) xseen.add(d.id);
        pushStep({ id: d.id, title: d.title, kind: d.kind, status: d.status,
                   content: d.content, locations: d.locations });
        break;
      case 'plan':
        flushText();
        // One row, updated in place: every plan event replaces the whole
        // list (ACP semantics), so they merge by the same id — namespaced
        // with \u0000 so a real toolCallId literally named "plan" (nothing
        // reserves that string) cannot merge into it (panel 2026-08-30).
        pushStep({ id: '\u0000plan', kind: 'plan', entries: d.entries || [],
                   title: `plan · ${(d.entries || []).length} steps`, status: '' });
        break;
      // Never collapsed — the two things that must not be missed.
      case 'permission': flush();
        // Live only if this is the still-open card for that id AND the id
        // is actionable right now. Both halves are load-bearing; see above.
        log.appendChild(permCard(p, d, permOutcomes.get(e.seq),
                                 permOpen.get(d.requestId) === e.seq &&
                                 p.pending.includes(d.requestId)));
        break;
      case 'dead': flush();
        log.appendChild(el('div', 'sys err', `agent stopped — ${d.reason || 'unknown'}`)); break;
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
    // The native spinner's shape: glyph + elapsed + the interrupt hint.
    // Elapsed is computed at render, so it advances with the SSE ticks a
    // busy pane produces anyway \u2014 no timer of its own.
    let t0 = null;
    for (let i = visible.length - 1; i >= 0; i--) {
      if (visible[i].kind === 'user') { t0 = visible[i].at; break; }
    }
    const secs = t0 ? Math.max(0, Math.round((Date.now() - new Date(t0)) / 1000)) : null;
    const w = el('div', 'sys working');
    w.append(el('span', 'wstar', '\u2733'),
             ' working\u2026' + (secs != null ? ` ${secs}s` : '') + '  (esc to interrupt)');
    log.appendChild(w);
  }
  return log;
}

// The permission card. This is the load-bearing surface: it must show the
// EXACT thing being approved, because an approval proves only what the human
// could see (PRINCIPLES 17). ACP gives us rawInput and a structured diff.
// `live` says whether THIS card is the one the agent is blocked on. It is
// passed in, never derived from `p.pending` here: requestId is reused across a
// pane's transcript (see renderLog), so "is that id pending?" is true of every
// stale card sharing the id, and each of them carries a digest the server will
// refuse. The rail passes true because it renders only the open card by
// construction.
function permCard(p, d, outcome, live) {
  const answered = !live;
  const c = el('div', 'perm');
  c.appendChild(el('div', 'h', `Wants to: ${d.title || d.kind || 'act'}`));

  // EVERY diff, and the whole rawInput. Both used to be clipped — only the
  // first diff was rendered and rawInput was sliced at 4,000 characters — so
  // a multi-file edit or a long command could be approved with its meaningful
  // part never on screen. An approval proves only what was visible.
  if (d.oversize) {
    c.appendChild(el('div', 'why',
      `This request is ${Math.round((d.bytes || 0) / 1024)} KB — too large to ` +
      `display in full, so it was not kept. Approving what cannot be shown ` +
      `is not consent, so only refusal is offered here. Answer it in the ` +
      `agent's own surface if you need to allow it.`));
  } else {
    // EVERY content entry, not only the diff-shaped ones. The digest is taken
    // over content + locations + rawInput, so anything rendered selectively is
    // signed-for but unseen — the exact failure this card was rewritten to
    // stop, one layer further in. Diffs get the readable view; everything else
    // gets its literal JSON, because "unknown type" is not permission to hide.
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
    // Distinguish "you decided this" from "nothing decided this" — the two
    // used to render as the identical word "answered", so reading the log
    // back could not tell an approval from a timeout/pause/crash that just
    // dropped the request.
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
  // Nothing that grants may be offered for a payload we could not display —
  // permOptions applies that filter, and the composer's number keys share it,
  // so key N and button N always name the same option.
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

// PANES ARE BUILT ONCE AND UPDATED IN PLACE — this is not an optimization.
//
// The first cut rebuilt the whole grid on every SSE event: `g.innerHTML = ''`
// then a fresh <textarea> per pane. With one idle agent that is invisible.
// With several running, events arrive continuously, so the textarea you were
// typing into was destroyed and replaced several times a second — losing the
// caret, the text, and the focus. Craig, 2026-08-01: "active terminals keep
// stealing the focus from each other making it impossible to type."
//
// Nothing was stealing focus. Focus was being DELETED, and the browser fell
// back to <body>. So the rule here: the composer element is created once per
// pane and never touched again while the pane keeps the same shape. The
// header and the transcript are cheap and rebuild freely; the one element
// holding human state does not.
const PANES = new Map();      // paneId -> {root, head, log, comp, kind}
const TEXPAND = new Map();    // paneId -> Set(toolCallId) with ⎿ held open
const LOG_CAP = new Map();    // paneId -> how many recent events render to DOM
const DEFAULT_LOG_CAP = 300;

function buildPane(p) {
  const root = el('div', 'pane');
  root.dataset.pane = p.id;
  const head = el('div', 'ph');
  const log = el('div', 'log');
  const comp = el('div', 'compslot');
  root.append(head, log, comp);
  log.onscroll = () => {
    // "Am I pinned to the bottom?" is the only scroll fact worth keeping, and
    // with a persistent log element the browser preserves the rest for free.
    const rec = PANES.get(p.id);
    if (rec) rec.pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    // Scrolling to the top of a capped transcript used to just... stop, with
    // nothing to reveal it but a small "N earlier events not shown" sys line
    // easy to miss mid-scroll -- Craig read that as scrolling being broken,
    // not as a control. Reveal automatically instead, same as any normal chat
    // UI's infinite-scroll-up. updatePane's existing height-delta re-anchor
    // (the df78d6f fix) keeps the view pinned to the same content once the
    // cap grows, so this doesn't jump him anywhere.
    const cap = LOG_CAP.get(p.id) || DEFAULT_LOG_CAP;
    if (rec && log.scrollTop < 80 && p.events.length > cap) {
      LOG_CAP.set(p.id, cap + 1000);
      updatePane(rec, p);
    }
  };
  return { root, head, log, comp, kind: null, pinned: true };
}

function composerKind(p) {
  return p.state === 'dead' ? 'none' : p.state === 'detached' ? 'detached' : 'live';
}

function updatePane(rec, p) {
  // A selection in this log is human state exactly like the composer's text:
  // rebuilding the DOM under it deletes it mid-drag. Herdr rule — a selection
  // survives live output. So the log holds still while one lives in it (the
  // chip says so), and catches up in one render the moment it clears. Holding
  // rather than re-anchoring is deliberate: text ABOVE a selection mutates in
  // place here (the cap banner counts up, a steps line grows), so restoring by
  // offset can silently re-anchor onto different text — a copy that lies.
  const live = liveLogSelection();
  const hold = SEL.down === p.id || (live && live.log === rec.log);
  rec.root.className = 'pane' + (p.pending.length ? ' attn' : '') +
                       (p.state === 'dead' ? ' dead' : '') +
                       (hold ? ' selhold' : '') +
                       (isTerm(p) ? ' term' : '');
  rec.head.replaceChildren(...paneHead(p).childNodes);

  if (!hold) {
    const wasPinned = rec.pinned;
    const oldHeight = rec.log.scrollHeight, oldTop = rec.log.scrollTop;
    rec.log.replaceChildren(...renderLog(p).childNodes);
    // A capped transcript that happens to fit the viewport with room to
    // spare has NOTHING to scroll -- the only sign more exists was a small
    // "N earlier events not shown" sys line, easy to miss, with no scrollbar
    // to hint at it either. That read as "scrolling is broken," not "there's
    // a control here" (Craig, 2026-08-23: clicked it once he found it, and
    // it worked fine -- the hiding itself was the confusion). Keep raising
    // the cap until either everything shows or there's real overflow to
    // scroll through. Bounded so a pathological pane (huge per-event render,
    // tiny viewport) can't loop forever.
    for (let guard = 0; guard < 5 &&
         rec.log.scrollHeight <= rec.log.clientHeight &&
         p.events.length > (LOG_CAP.get(p.id) || DEFAULT_LOG_CAP); guard++) {
      LOG_CAP.set(p.id, (LOG_CAP.get(p.id) || DEFAULT_LOG_CAP) + 1000);
      rec.log.replaceChildren(...renderLog(p).childNodes);
    }
    if (wasPinned) {
      rec.log.scrollTop = rec.log.scrollHeight;
    } else {
      // Not pinned means Craig scrolled up to read something. Every tick
      // still rebuilds this subtree from scratch (a collapsed "N steps" line
      // re-collapses -- its `open` flag lives in renderLog()'s throwaway
      // closure, not on the pane), which shrinks scrollHeight and the browser
      // clamps scrollTop to the new max: the view got yanked to the bottom
      // out from under him mid-read, on EVERY tick, with no way to hold a
      // position (Craig, 2026-08-23: "scrolling still does not work in
      // GPT"). Re-anchor by the height delta instead of trusting the
      // clamped value.
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
  return rec.root;
}

function paneHead(p) {
  const h = el('div', 'ph');
  h.appendChild(el('span', 'nm', p.title || p.label));
  // Only claim a posture Corral actually imposed. `oc acp` runs under its own
  // policy, so a Grok pane wearing a `strict` pill was the UI asserting a
  // safety property nothing had established.
  if (p.postureEnforced === false) {
    const q = el('span', 'pill unknown', 'agent-set');
    q.title = `Corral cannot set the permission policy for ${p.label}. ` +
              `Whatever that agent does by default is what you get.`;
    h.appendChild(q);
  } else {
    h.appendChild(el('span', 'pill ' + p.posture, p.posture));
  }
  for (const cid of ['model', 'effort']) {
    const cfg = (p.config || {})[cid];
    if (!cfg || !cfg.value) continue;
    const b = el('button', 'pill cfg', (cid === 'effort' ? '⚡ ' : '') + cfg.value);
    b.title = `${cfg.name || cid} — click to change`;
    b.onclick = async () => {
      const opts = cfg.options || [];
      if (!opts.length) return;
      const cur = opts.findIndex(o => o.value === cfg.value);
      const next = opts[(cur + 1) % opts.length];          // cycle; the list is short
      try { await api('/api/session/config', { pane: p.id, configId: cid, value: next.value }); }
      catch (e) { toast(e.message, true); }
    };
    h.appendChild(b);
  }
  // Context usage: ACP's usage_update is EXPERIMENTAL/UNSTABLE (spec marks it
  // so) — size/used can arrive as 0/undefined before the first turn, or drift
  // if a future adapter version renames the fields. Absence is silent, not an
  // error: no pill rather than a misleading "0% ctx" claim.
  const u = p.usage || {};
  if (u.size > 0 && Number.isFinite(u.used)) {
    const pct = Math.round(100 * u.used / u.size);
    const warn = pct >= 75;                    // matches CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
    const pill = el('span', 'pill ctx' + (warn ? ' warn' : ''), `${pct}% ctx`);
    pill.title = `${u.used.toLocaleString()} / ${u.size.toLocaleString()} tokens in context`;
    h.appendChild(pill);
  }
  h.appendChild(el('span', 'meta', `${p.label} · ` + p.cwd.replace(/^\/(home|Users)\/[^/]+/, '~')));
  // The on-state used to be a colour change on a 12px glyph, and it persists
  // in localStorage per pane forever. So a pane could sit in full-detail mode
  // indefinitely and read as a rendering bug — it did, 2026-08-01, on a Grok
  // pane. A latched mode has to SAY it is latched.
  const on = S.detail.has(p.id);
  const eye = el('button', 'eye' + (on ? ' on' : ''), on ? '☰ every step' : '☰');
  eye.title = on ? 'showing every step — click to collapse tool calls'
                 : 'show every step';
  eye.onclick = () => {
    S.detail.has(p.id) ? S.detail.delete(p.id) : S.detail.add(p.id);
    saveDetail(); render();
  };
  h.appendChild(eye);
  // Herdr's copy-mode search, on the web pane: literal smart-case find with
  // every match highlighted and Enter/Shift+Enter walking them.
  const fnd = el('button', 'x' + (FIND.pane === p.id ? ' fon' : ''), '⌕');
  fnd.title = 'find in this conversation';
  fnd.onclick = () => toggleFind(p);
  h.appendChild(fnd);
  const min = el('button', 'x', '–'); min.title = 'minimize (keeps running)';
  min.onclick = () => setMin(p, true);
  h.appendChild(min);
  const x = el('button', 'x', '✕'); x.title = 'close';
  x.onclick = async () => { try { await api('/api/session/close', { pane: p.id }); await refresh(); }
                            catch (e) { toast(e.message, true); } };
  h.appendChild(x);
  return h;
}

/* ── copy & find, the herdr manner ───────────────────────────────────────
 * Craig lives in herdr the rest of the day, and its clipboard habits are the
 * ones his hands know: releasing a drag copies it, a double-clicked word
 * copies itself, a selection survives live output, and / search highlights
 * every match. This section ports those to the pane transcripts. The
 * survival half lives in updatePane (the hold); this is the rest. */
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

// Quiet, herdr-quiet: a small "copied" that drifts up from the cursor and
// fades. The toast is for problems; success should barely register.
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
const FIND_CAP = 2000;                 // bound the ranges (PRINCIPLES 8)

// Herdr's rule, literal smart-case: any capital in the query makes it exact;
// an all-lowercase query matches case-insensitively.
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
    // Built once and kept, exactly like the composer: this is a live text
    // input under an SSE stream, and a rebuild would eat the query mid-word.
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
    .scrollIntoView({ block: 'center' });   // scrolling up unpins — herdr's
}                                            // "stay put while you read history"

// A shell composer, not a chat one: one line, shell history on ↑/↓, and Ctrl-C
// on an EMPTY input (so it does not steal copy from a selection you just made
// or from text you typed) cancels the in-flight command — ssh_acp kills the
// shell and reconnects clean on the next command, exactly its designed degrade.
// No slash-completer here: "/tmp" is a path, not a skill, and the completer
// was stealing Enter/Tab from any command that started with "/".
const TERMHIST = new Map();   // pane id -> {cmds, ix, draft}; survives rebuilds
function termComposer(p) {
  const c = el('div', 'composer term');
  const h = TERMHIST.get(p.id) ||
    { cmds: p.events.filter(e => e.kind === 'user')
                    .map(e => (e.data || {}).text || '').filter(Boolean),
      ix: null, draft: '' };
  TERMHIST.set(p.id, h);
  c.appendChild(el('span', 'pr', '❯'));
  const inp = el('input');
  inp.type = 'text'; inp.autocomplete = 'off'; inp.spellcheck = false;
  inp.placeholder = `runs on ${p.agent.slice(5)} as you`;
  let sending = false;
  const send = async () => {
    if (sending) return;
    const t = inp.value.trim(); if (!t) return;
    sending = true;
    try {
      // Same eager-clear hazard as the chat composer: clear only once the
      // server accepted it, so a failure leaves the command typed for retry.
      await api('/api/session/send', { pane: p.id, text: t });
      if (h.cmds[h.cmds.length - 1] !== t) h.cmds.push(t);
      h.ix = null; h.draft = '';
      inp.value = '';
    } catch (e) { toast(e.message, true); }
    finally { sending = false; }
  };
  const recall = v => {
    inp.value = v;
    requestAnimationFrame(() => inp.setSelectionRange(v.length, v.length));
  };
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); send(); return; }
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
  // Clearing eagerly, before the request even landed, meant a queue-full,
  // expired-auth, or network error silently ATE what Craig typed -- the
  // textarea read empty and the toast was the only trace anything had been
  // typed at all. Clear only once the server has actually accepted it; on
  // failure the text stays put for a retry. `sending` replaces the clear's
  // old accidental job of ignoring a rapid double Enter.
  let sending = false;
  const send = async () => {
    if (sending) return;
    const t = ta.value.trim(); if (!t) return;
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

  // Slash-command completion. The agent advertises its own commands over ACP
  // right after session/new (70 of them under Claude), so nothing here is a
  // hardcoded list -- a skill Craig adds shows up in the next pane he opens.
  // Craig: "they don't autocomplete... You have to know the skill."
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
    // The native dialog keys, on an EMPTY composer only (typed text always
    // wins): with a permission pending, 1..9 answers by number — the card's
    // buttons carry the same numbers — and Esc picks the refusal. With no
    // permission up, Esc on a busy pane interrupts the turn, exactly the
    // TUI's esc. Fresh state from S.panes: `p` here is the snapshot the
    // composer was built from, and the composer outlives it by design.
    //
    // Consent guards (2026-08-30 panel, 3/3 arms on the identity hazard):
    // digits act ONLY when exactly one request is pending — every card
    // numbers its buttons from 1, so with two cards key 1 is ambiguous and
    // must do nothing (PRINCIPLES 17: key N and button N the same option,
    // always). And only a fresh, unmodified press: a held key (e.repeat) or
    // a chorded one is not a deliberate answer to a displayed card.
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
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };
  ta.onblur = hide;
  const b = el('button', 'send', 'Send'); b.onclick = send;
  c.append(ac, ta, b);
  return c;
}

// The only clock formatting Light still needs. The full Corral also carries
// fmtWhen (a FUTURE time — scheduled jobs) and fmtAgo (a past ISO timestamp —
// heartbeats, last-run rows); neither has a caller once the scheduler and the
// fleet rooms are gone, and a dead formatter is a thing to keep in sync with
// nothing.
function fmtAge(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

/* ── rendering: shell ────────────────────────────────────────────────── */
function render() {
  const panes = [...S.panes.values()];

  // roster
  const r = $('#roster');
  // Same hazard as the composer: the rename box is a live text input, and
  // rebuilding the roster under an agent's event stream would eat the name
  // mid-word. Renaming is brief and modal, so the roster simply holds still
  // until it ends.
  if (!(S.renaming && r.querySelector('.ren'))) {
  r.innerHTML = '';
  // Pinned and the pane you are looking at always show; everything else
  // rolls up under "Other" (Craig, 2026-08-23) — same collapsible shape as
  // Archive below, but open by default: unlike Archive's closed history,
  // these are live conversations, so the default is visible, not hidden.
  const paneRow = p => {
    const it = el('div', 'rit ' + p.state + (p.minimized ? ' min' : '') +
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
    // `busy` was a pulsing dot with nothing behind it — the same animation at
    // three seconds and at forty minutes. Saying how long since the pane last
    // said anything is the difference between "working" and "wedged", which is
    // the question a roster of ten agents exists to answer.
    const quiet = (p.state === 'busy' || p.state === 'uncertain') && p.idleS >= 30
      ? ` · quiet ${fmtAge(p.idleS)}` : '';
    // The directory tail alone reads as an agent badge when it happens to BE
    // one -- Craig's daily-driver repo is named "CC", so every non-Claude
    // pane opened there showed "· CC" here and looked like a Claude Code
    // conversation. Tag the agent explicitly for every lane but the default.
    const agentTag = p.agent !== 'claude' ? p.label + ' · ' : '';
    t.appendChild(el('div', 's',
      p.state + quiet + ' · ' + agentTag + p.cwd.split('/').pop()));
    it.appendChild(t);

    // A minimized pane blocked on a permission must still SHOW that it is —
    // otherwise minimizing becomes a way to make an agent wait forever while
    // the UI looks calm.
    if (p.pending.length) it.appendChild(el('span', 'badge', String(p.pending.length)));

    const acts = el('div', 'acts');
    if (p.state === 'dead') {
      const f = el('button', 'a', '✕'); f.title = 'dismiss — remove from the list';
      f.onclick = async e => {
        e.stopPropagation();
        try { await api('/api/session/forget', { pane: p.id }); await refresh(); }
        catch (err) { toast(err.message, true); }
      };
      acts.appendChild(f);
    }
    const pin = el('button', 'a' + (p.pinned ? ' on' : ''), p.pinned ? '★' : '☆');
    pin.title = p.pinned ? 'unpin' : 'pin to the top';
    pin.onclick = async e => {
      e.stopPropagation();
      try { await api('/api/session/pin', { pane: p.id, pinned: !p.pinned }); await refresh(); }
      catch (err) { toast(err.message, true); }
    };
    const ren = el('button', 'a', '✎'); ren.title = 'rename';
    ren.onclick = e => { e.stopPropagation(); S.renaming = p.id; render(); };
    // Pause is the missing middle. Close ends the conversation and files it;
    // pause just stops the process and keeps everything, which is what you
    // actually want for work you mean to come back to.
    if (p.state !== 'detached' && p.state !== 'dead') {
      const ps = el('button', 'a', '⏸'); ps.title = 'pause — stop the agent, keep the conversation';
      ps.onclick = async e => {
        e.stopPropagation();
        try { await api('/api/session/pause', { pane: p.id }); await refresh(); }
        catch (err) { toast(err.message, true); }
      };
      acts.appendChild(ps);
    }
    const mm = el('button', 'a', p.minimized ? '▣' : '–');
    mm.title = p.minimized ? 'restore' : 'minimize (keeps running)';
    mm.onclick = e => { e.stopPropagation(); setMin(p, !p.minimized); };
    acts.append(pin, ren, mm);
    it.appendChild(acts);

    // Drag to order. Craig's original complaint was that a terminal cannot
    // arrange running work; creation order is not an arrangement.
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

  const pinned = panes.filter(p => p.pinned || S.focus === p.id);
  const others = panes.filter(p => !p.pinned && S.focus !== p.id);
  for (const p of pinned) r.appendChild(paneRow(p));
  if (others.length) {
    // "Other", not "Recent" — this same roster already has a "Recent"
    // group in the same list.
    const lab = el('div', 'lab clicky',
                   `Other · ${others.length} ${S.hideOther ? '▸' : '▾'}`);
    lab.onclick = () => { S.hideOther = !S.hideOther; render(); };
    r.appendChild(lab);
    if (!S.hideOther) for (const p of others) r.appendChild(paneRow(p));
  }
  if (!panes.length) r.appendChild(el('div', 'calm', 'None yet.'));
  // Say what the cap kept off the screen. A pane that is on disk and absent
  // from the roster, with nothing said about it, is indistinguishable from a
  // pane the product threw away.
  if (S.notRestored) {
    const n = el('div', 'calm',
      `${S.notRestored} saved conversation${S.notRestored > 1 ? 's' : ''} not ` +
      `restored — ${12} is the cap. Their transcripts are still on disk.`);
    n.title = 'Close or archive a pane and restart to bring one back.';
    r.appendChild(n);
  }

  // Archive. Closing used to be the end of a conversation as far as the
  // product was concerned, while its transcript sat on disk untouched — a
  // deletion nobody asked for. Collapsed by default: it is history, not work.
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

  // Light's roster stops here. The full Corral also lists scheduled jobs,
  // adopted tmux sessions, and pinned/recent Library places below this
  // point — three sources that do not exist on this build.
  }

  // grid
  const g = $('#grid');
  const shown = panes.filter(p => !p.minimized);
  const mins = panes.filter(p => p.minimized);
  g.className = 'grid' + (shown.length === 1 ? ' one' : '');

  // The minbar and the empty-state are disposable; the panes are not (see
  // PANES above). So clear only the disposable children and reconcile the
  // rest by identity.
  for (const c of [...g.children]) {
    if (!c.dataset.pane) c.remove();
  }

  if (mins.length) {
    const bar = el('div', 'minbar');
    for (const p of mins) {
      // A semantic <button>: a click-only div never takes focus, so
      // keyboard and :focus-visible can't reach it (panel, 2026-08-24).
      const c = el('button', 'minchip ' + p.state);
      c.type = 'button';
      c.appendChild(el('span', 'd'));
      c.appendChild(el('span', 'mt', p.title || p.label));
      if (p.pending.length) c.appendChild(el('span', 'badge', String(p.pending.length)));
      // Full identity in the name: the visible label ellipsizes, and the
      // state must not live in the dot's color alone.
      const full = `${p.title || p.label} — ${p.state}, click to restore`;
      c.title = full;
      c.setAttribute('aria-label', full);
      c.onclick = () => setMin(p, false);
      bar.appendChild(c);
    }
    g.insertBefore(bar, g.firstChild);          // above the panes, which stay put
  }

  // Reconcile panes by id. Retire what is gone, create what is new, update
  // the rest IN PLACE, and move a node only when its position actually
  // changed — moving a DOM node blurs whatever is focused inside it, which
  // is the bug this whole structure exists to avoid.
  // A minimized pane is HIDDEN, not gone. Deleting its PANES record here was
  // the same mistake buildPane's persistent-DOM rule exists to prevent, one
  // layer up: minimize destroyed the composer (unsent draft text), the find
  // query, and the scroll position, then rebuilt a blank one on restore --
  // contradicting the "composer is created once" design a few lines above.
  // Only a pane that has left `panes` entirely (closed, forgotten, archived)
  // should lose its record; minimizing must not silently do the same thing
  // closing does.
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
    // Full-size is a STATE the pane must wear, not something inferred from
    // an empty grid (Craig, 2026-08-30: "show clearly when a pane is at its
    // full size"). Class on the persistent root — CSS renders the tag, so
    // reconciliation can never orphan it.
    rec.root.classList.toggle('solo', shown.length === 1);
    const slot = prev ? prev.nextSibling : g.firstChild;
    if (rec.root !== slot) g.insertBefore(rec.root, slot);
    prev = rec.root;
  }

  if (!shown.length) {
    const e = el('div', 'empty');
    if (!panes.length) {
      e.appendChild(el('h2', null, 'Nothing running.'));
      e.appendChild(el('div', null, 'Start a conversation and it appears here.'));
    } else {
      e.appendChild(el('h2', null, 'All minimized.'));
      e.appendChild(el('div', null, 'They are still running. Click one above to bring it back.'));
    }
    g.appendChild(e);
  }

  // THE rail. In the full Corral this is a view of /api/attention — a
  // server-side queue that merges permissions with fleet mailbox tasks,
  // scheduled-run failures, estate health and the Docket. None of those
  // sources exist here, so Light does NOT keep a hollow queue with one
  // member: the rail is composed directly from the panes, which is the one
  // authority it has.
  //
  // What survives the cut is the property that matters: a permission is
  // answered HERE, with its exact bytes, not by scrolling to the pane. The
  // rail used to render a summary and scroll you to the conversation — and
  // if that pane was minimized there was no node to scroll to, so the click
  // did nothing at all and the agent stayed blocked.
  const n = $('#needs'); n.innerHTML = '';
  let items = 0;
  for (const p of panes) {
    for (const rid of p.pending) {
      const ev = [...p.events].reverse()
        .find(e => e.kind === 'permission' && e.data.requestId === rid);
      const c = el('div', 'ncard');
      c.appendChild(el('div', 't', p.title || p.label));
      c.appendChild(el('div', 'm', `${p.label} · ${p.cwd.split('/').pop()}` +
                                   (p.minimized ? ' · minimized' : '')));
      if (ev) {
        c.appendChild(permCard(p, ev.data, null, true));
      } else {
        // The payload aged out of the bounded ring. Never offer buttons for a
        // request whose contents can no longer be shown — an approval proves
        // only what the human could see (P17).
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
  // A pane that died on its own is news and stays until dismissed; a pane you
  // closed is finished business and never appears here (the manager drops it
  // from the roster on close, so this loop cannot see one).
  for (const p of panes) {
    if (p.state !== 'dead') continue;
    const c = el('div', 'ncard dead');
    c.appendChild(el('div', 't', `${p.title || p.label} — agent stopped`));
    c.appendChild(el('div', 'm', p.error || 'click to dismiss'));
    c.onclick = async () => {
      try { await api('/api/session/forget', { pane: p.id }); await refresh(); }
      catch (e) { toast(e.message, true); }
    };
    n.appendChild(c); items++;
  }
  // An `uncertain` pane is alive, mid-turn, and has emitted nothing for
  // minutes. It is not blocked on anything, so it carries no action — but it
  // is exactly the state the operator would otherwise never notice, because
  // the roster's pulsing dot looks identical at three seconds and at forty
  // minutes. It says so and offers the pane; deciding it is wedged is Craig's.
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
  if (!items) n.appendChild(el('div', 'calm', 'Nothing. Quiet is the steady state.'));
  railFold(items, panes.reduce((a, p) => a + p.pending.length, 0));
}

// Every minimize/restore click goes through here — the roster row, the pane
// header's own button, the minbar chip, and the rail's "restore" action all
// call it. Dropped during the trim that cut this file from the full Corral's
// app.js (loadAttention/loadFleet/askResolve sat right next to it and the cut
// boundary took setMin with them); every CALLER survived the trim, so every
// click threw a silent ReferenceError in the console instead of doing
// anything — `node --check` catches a syntax error, not a missing runtime
// reference, so this shipped unnoticed until Craig actually clicked minimize.
async function setMin(p, flag) {
  try {
    await api('/api/session/minimize', { pane: p.id, minimized: flag });
    p.minimized = flag; render();
  } catch (e) { toast(e.message, true); }
}

/* ── the needs-you rail folds ─────────────────────────────────────────────
 * Craig, 2026-08-01: "needs to be collapsible and collapse when nothing is in
 * it. More screenspace for reading is always appreciated."
 *
 * Two rules, and the second is the one that matters:
 *   1. Empty folds itself. A 300px column reserved for "Nothing." is 300px
 *      of transcript he is not reading.
 *   2. Folded is a STRIP, never `display:none`. The strip carries the count,
 *      and wears the attention colour when an agent is actually blocked. This
 *      is the same rule minimize follows for panes — hiding the surface must
 *      never hide the state, or folding the rail becomes a way to leave an
 *      agent waiting forever while the window looks calm. It is also exactly
 *      how the <1100px media query broke this once: it deleted the rail
 *      outright and every permission went with it.
 *
 * A hand-fold is sticky, because a control that reopens itself is not a
 * control. An auto-fold is not: it follows the contents.
 */
function railFold(items, blocked) {
  const open = S.railShut === null ? items > 0 : !S.railShut;
  $('#app').classList.toggle('railshut', !open);
  $('#rrail').classList.toggle('shut', !open);
  $('#railhead').textContent = `Needs you${items ? ' · ' + items : ''} ▾`;
  // Always a digit, including 0 (Craig, DESIGN-2: the rail IS the silence
  // metric). An empty badge and a genuine zero were visually identical when
  // collapsed — measured 2026-08-22, rival-reviewed unanimously as hiding
  // the win condition behind a click.
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

// Resolve is a dialog, not a button, ON PURPOSE. A rail where one tap closes a
// card gets that tap used on every card — measured, 2026-08-01. The evidence
// box is the friction, and the server refuses anything under 12 characters
// whatever this form does.
/* ── data ────────────────────────────────────────────────────────────── */
let refreshSeq = 0;
async function refresh() {
  // Ask only for events past what we already hold. Replacing the map wholesale
  // was the other half of the reload bug: a snapshot taken while a turn was
  // streaming clobbered locally-received events with a staler server view.
  const since = {};
  for (const [id, p] of S.panes) since[id] = p.seq || 0;
  // refresh() is called from a dozen unrelated places -- a user action and an
  // SSE-driven refresh can fire back to back, and two concurrent /api/state
  // requests are not guaranteed to RESOLVE in the order they were sent. An
  // older response landing after a newer one used to win outright, silently
  // reverting any pane the newer response had already discovered. Only the
  // most recently STARTED call is allowed to apply its result, whichever
  // order the network hands the responses back.
  const seq = ++refreshSeq;
  let d;
  try {
    d = await api('/api/state?since=' + encodeURIComponent(JSON.stringify(since)));
  } catch (e) { if (e.status === 401) return relock(); throw e; }
  if (seq !== refreshSeq) return;      // a newer refresh() has since been issued
  S.agents = d.agents || [];
  S.agentGroups = d.agentGroups || S.agentGroups || {};
  S.catalog = d.catalog || S.catalog || {};
  S.defaultCwd = d.defaultCwd || S.defaultCwd || '';
  S.cwdSuggestions = d.cwdSuggestions || S.cwdSuggestions || [];
  S.archived = d.archived || [];
  S.notRestored = d.notRestored || 0;
  const next = new Map();
  for (const np of (d.panes || [])) {
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
  };
  es.onmessage = m => {
    let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
    // The server emits this when it had to throw away our backlog because this
    // browser fell behind. Everything after a drop is untrustworthy — a lost
    // `permission` followed by a delivered `turn_end` reads as `ready` for a
    // pane that is actually blocked — so refetch rather than carry on.
    if (ev.kind === 'resync') { refresh().catch(() => {}); return; }
    const p = S.panes.get(ev.pane);
    if (!p) { refresh(); return; }        // a pane we don't know yet
    const last = p.events.length ? p.events[p.events.length - 1].seq : (p.seq || 0);
    if (ev.seq <= last) return;                 // already have it
    // A GAP means events went missing between the server and here. The old
    // check only rejected duplicates, so a gap was accepted silently and the
    // pane's state was then derived from a partial story. Debounced: a burst
    // of events arriving mid-gap used to fire one concurrent refresh() PER
    // event (each still sees the same stale `last`), flooding the server.
    if (last && ev.seq > last + 1) {
      if (!S.refreshing) { S.refreshing = true; refresh().catch(() => {}).finally(() => { S.refreshing = false; }); }
      return;
    }
    p.events.push(ev);
    // The cap is a ceiling on ONE turn's live growth, not on history "load
    // earlier" (below) just fetched from disk -- the old fixed 4000 deleted
    // exactly the events a click just loaded, the instant the next live
    // event arrived. Float the floor to whatever's already loaded, bounded
    // so a long run of loads still can't grow the ring without limit.
    const cap = Math.min(20000, Math.max(4000, p.events.length - 1));
    if (p.events.length > cap) p.events.splice(0, p.events.length - cap);
    p.seq = ev.seq || p.seq || 0;
    const d = ev.data || {};
    if (ev.kind === 'permission') { p.pending.push(d.requestId); p.state = 'needs-you'; }
    if (ev.kind === 'permission_answered') {
      p.pending = p.pending.filter(x => x !== d.requestId);
      p.state = p.pending.length ? 'needs-you' : 'busy';
    }
    // The server clears `pending` when a request times out or the agent dies.
    // The browser did not, so the rail kept offering buttons for a request
    // nothing was waiting on any more, and the pane stayed `needs-you`
    // forever. A fix that only landed on one side of the wire is half a fix.
    if (ev.kind === 'permission_expired') {
      p.pending = p.pending.filter(x => x !== d.requestId);
      refresh().catch(() => {});          // server owns what the state is now
    }
    if (ev.kind === 'paused') { p.state = 'detached'; p.pending = []; }
    if (ev.kind === 'user') p.state = 'busy';
    if (ev.kind === 'turn_end') p.state = p.pending.length ? 'needs-you' : 'ready';
    if (ev.kind === 'dead') { p.state = 'dead'; p.error = d.reason; }
    if (ev.kind === 'closed') { p.state = 'dead'; p.error = null; refresh(); }
    if (ev.kind === 'ready') p.state = 'ready';
    // snapshot()'s observed edges (busy→uncertain, poll()-detected dead):
    // the server-side mutation now broadcasts, and this is the receiving end.
    if (ev.kind === 'state' && d.state) p.state = d.state;
    if (ev.kind === 'resumed') { p.state = 'ready'; refresh(); }
    if (ev.kind === 'renamed') p.title = d.title;
    if (ev.kind === 'config' || ev.kind === 'ready') {
      if (d.model) p.model = d.model;
      if (d.effort) p.effort = d.effort;
      if (d.config) p.config = d.config;
    }
    // The command list arrives once, unprompted, just after session/new. The
    // event carries only a count -- 70 names and descriptions replayed on
    // every reload is transcript bloat -- so pull the list itself from state.
    if (ev.kind === 'commands') refresh();
    if (ev.kind === 'note') toast(d.text, true);
    render();
  };
}

/* ── new-conversation dialog ─────────────────────────────────────────── */
// Descriptions are the agent's own, from session/new's configOptions — not my
// paraphrase. The first version claimed auto meant "nothing will ask", which
// was simply wrong: auto runs a classifier and still escalates what it will
// not approve.
const HINTS = {
  strict: 'Prompts on dangerous operations. Most approvals land in your rail.',
  edits: 'File edits apply without asking; commands and network still ask.',
  auto: 'A classifier approves or denies routine prompts, and escalates what it will not approve. Fewer interruptions, not none.'
};
function wireDialog() {
  const dlg = $('#newdlg');
  $('#new').onclick = () => {
    // Agents that belong to a GROUP collapse to one entry (Craig, 2026-08-31:
    // "consolidate the SSH connections under one main SSH tab and then break it
    // out into each individual session if we choose SSH"). One row per box meant
    // the handful of lanes that are genuinely different kinds of thing were
    // outnumbered by machines. `group:<id>` is a UI-only sentinel — the real
    // lane key is resolved from the host picker before anything is submitted.
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
    // Which member of the selected group, when one is selected. Members keep
    // their own availability + reason here, so a destroyed box still shows up
    // greyed with why rather than vanishing.
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
    // Land on the FIRST group's first live member (today: Agents → Claude), so
    // reorganising the menu costs nothing on the overwhelmingly common path —
    // open the dialog, press Start. A grouping that adds a click to the default
    // case would be a worse dialog wearing a tidier one's clothes.
    const firstOpt = sel.options?.find?.(o => !o.disabled)
                  || [...(sel.options || [])].find(o => !o.disabled);
    if (firstOpt) sel.value = firstOpt.value;
    $('#f-cwd').value = S.lastCwd || S.defaultCwd || '~';
    // Real directories on the host, so choosing one does not require already
    // knowing its absolute path. The input stays free text — this only means
    // the common cases are one click away instead of typed from memory.
    const dl = $('#cwdlist'); dl.innerHTML = '';
    for (const d of S.cwdSuggestions || []) {
      const o = document.createElement('option'); o.value = d;
      dl.appendChild(o);
    }
    // Model/effort options come from the server's remembered catalog for the
    // SELECTED agent, not from a live pane. Scraping a live pane meant that
    // with nothing running -- i.e. every fresh start -- the pickers offered
    // only "Default" and the first conversation could not choose a model.
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
          // A disabled dropdown next to a working one reads as broken, not
          // explained -- Craig's "issue with the Grok model picker" report,
          // root-caused by the 2026-08-23 bugbash panel (all 3 agreed):
          // Grok really does run one fixed model, Corral really does know
          // which one, and a picker offering nothing to pick was the wrong
          // affordance for that fact. Name it instead of graying it out.
          label.style.display = 'none';
          hint.textContent = `${entry.name || cid}: ${entry.value} — set by ` +
            `the agent, not choosable here.`;
        } else if (!opts.length) {
          // Honest empty state: we have never seen this agent's list at all
          // (distinct from the case above, which HAS seen it and knows there
          // is truly nothing to pick).
          label.style.display = '';
          hint.textContent = '';
          const o = el('option', null, 'Default (agent decides)'); o.value = '';
          node.appendChild(o); node.disabled = true;
        } else {
          label.style.display = '';
          hint.textContent = '';
          node.disabled = false;
          // A real options list with no explicit "leave it to the agent"
          // entry left the browser defaulting the <select> to its FIRST
          // option -- and the dialog always sends `.value` on close, so
          // just opening the dialog and clicking Start (never touching this
          // control) silently submitted an explicit want_model/want_effort
          // Craig never chose. Harmless when the adapter's own list already
          // self-describes a default choice (Claude's "Default
          // (recommended)" does, value "default" round-trips correctly);
          // real for any adapter that only lists concrete choices (Codex's
          // does). Found 2026-08-23 bugbash panel (GPT-5.6-sol).
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
    // Do not offer a posture Corral cannot impose. Only agents launched
    // through a CLAUDE_CONFIG_DIR obey it; for the rest the picker was a
    // control that did nothing and then displayed its imaginary result.
    const fillPosture = () => {
      const a = S.agents.find(x => x.key === chosenAgent()) || {};
      const sel = $('#f-posture'), hint = $('#posturehint');
      if (a.postureEnforced === false) {
        sel.disabled = true;
        hint.textContent = `${a.label} manages its own permissions — Corral ` +
                           `cannot set this, and will not pretend it did.`;
      } else {
        sel.disabled = false;
        sel.value = localStorage.getItem('corral.posture') || 'auto';
        hint.textContent = HINTS[sel.value];
      }
    };
    // Order matters: fillHost resolves which lane the other two describe.
    $('#f-agent').onchange = () => { fillHost(); fillCfg(); fillPosture(); };
    // Switching host inside a group changes the lane, so the model/posture
    // panels have to follow it — a stale "Default (agent decides)" left over
    // from the previous host is the same lying control this dialog keeps
    // getting fixed for.
    $('#f-host').onchange = () => { fillCfg(); fillPosture(); };
    fillHost();
    fillCfg();
    fillPosture();
    dlg.showModal();
  };
  $('#f-posture').onchange = e => { $('#posturehint').textContent = HINTS[e.target.value]; };
  // The full Corral's dialog has a third mode here: "At a time…", which posts
  // the same form to /api/schedule/add and starts the conversation later.
  // Light has no scheduler process, so the control is absent rather than
  // present-and-broken — a Start button that silently means Now is the worst
  // of the three options.
  dlg.addEventListener('close', async () => {
    if (dlg.returnValue !== 'ok') return;
    const cwd = $('#f-cwd').value.trim();
    S.lastCwd = cwd;
    localStorage.setItem('corral.posture', $('#f-posture').value);
    const common = { agent: dlg._chosenAgent(), cwd,
                     posture: $('#f-posture').value,
                     model: $('#f-model').value, effort: $('#f-effort').value };
    try {
      const d = await api('/api/session/new', common);
      S.panes.set(d.pane.id, d.pane);
      // Starting a conversation IS focusing it — otherwise the pane you just
      // opened is not the one ⌘K would attach a note to.
      S.focus = d.pane.id;
      render();
      if (d.pane.state === 'dead') toast('agent failed to start: ' + (d.pane.error || ''), true);
    } catch (e) { toast(e.message, true); }
  });
}

/* ── ⌘K — the one way to get anywhere ──────────────────────────────────────
 * Light has ONE room, so the palette is not a shortcut past a nav bar the way
 * it is in the full Corral — it IS the navigation. That means it has to search
 * everything in one ranked list: the panes you have open, the conversations
 * you closed, and your own notes.
 *
 * Local sources match with zero latency because they are already in memory;
 * content is debounced and folds in when it lands, so typing never waits on
 * the index. A dropped or slow /api/search degrades the palette to its local
 * matches instead of emptying it.
 *
 * WHAT ACTIVATING A CONTENT HIT DOES — and why it isn't "open the page":
 * upstream's Library renders the note in a room. Light never renders your
 * content in the browser at all (see content.py's docstring: that is what
 * lets the whole escape-everything renderer stay deleted). So a hit ATTACHES
 * instead — the server decides whether that means a path reference or a
 * quoted excerpt, based on whether the target lane can read a file itself.
 *   Enter       attach to the focused pane's composer
 *   ⇧Enter      open a NEW pane in that file's directory, then attach
 * Nothing is sent either way. It lands in the box; you read it and press send.
 */
const PAL = { sel: 0, rows: [], t: null, seq: 0, status: null };

function openPalette() {
  const q = $('#pal-q');
  q.value = '';
  paletteResults('');
  $('#palette').showModal();
  requestAnimationFrame(() => q.focus());
  // Fetched once per open, not per keystroke: it is only needed to explain an
  // EMPTY result, and explaining that badly ("no results") is the whole
  // difference between "nothing matches" and "you never pointed me at
  // anything".
  api('/api/content/status').then(s => { PAL.status = s; }).catch(() => {});
}

/* Which pane an attach lands in.
 *
 * S.focus alone is wrong, and measured wrong: it is only set by clicking a
 * roster row, so with exactly one conversation open — the overwhelmingly
 * common case, and the whole shape of a one-room app — every content hit
 * offered "open a new pane" while the pane you were plainly looking at sat
 * right there. An attach target that ignores the only pane on screen is a
 * feature explaining itself to a user who can see the answer.
 *
 * A minimized or dead pane is never the target: attaching into a composer
 * that is not on screen is a message you cannot see and did not send.
 */
function attachTarget() {
  const focused = S.panes.get(S.focus);
  if (focused && !focused.minimized && focused.state !== 'dead') return focused;
  const shown = [...S.panes.values()]
    .filter(p => !p.minimized && p.state !== 'dead');
  return shown.length === 1 ? shown[0] : null;
}

function paletteResults(query) {
  const needle = query.trim().toLowerCase();
  const rows = [];
  const focused = attachTarget();

  for (const [id, p] of S.panes || []) {
    const label = p.title || p.label;
    if (!needle || label.toLowerCase().includes(needle)
        || (p.cwd || '').toLowerCase().includes(needle)) {
      rows.push({ kind: 'pane', label, paneId: id,
                  sub: p.state + ' · ' + ((p.cwd || '').split('/').pop() || '') });
    }
  }
  for (const a of S.archived || []) {
    const label = a.title || a.id;
    if (needle && !label.toLowerCase().includes(needle)) continue;
    rows.push({ kind: 'archived', label, paneId: a.id, sub: 'archived' });
  }
  if (!needle || 'new conversation'.includes(needle)) {
    rows.push({ kind: 'action', label: 'New conversation', sub: 'action' });
  }

  renderPalette(rows.slice(0, 30), needle);
  if (needle.length < 2) return;

  // Debounced, and guarded by a sequence number: keystrokes outrun the
  // network, and an older response landing after a newer one would repaint
  // the list with results for a query that is no longer in the box.
  const seq = ++PAL.seq;
  clearTimeout(PAL.t);
  PAL.t = setTimeout(async () => {
    let d;
    try { d = await api('/api/search?q=' + encodeURIComponent(needle)); }
    catch (e) { return; }                  // degrade to local matches only
    if (seq !== PAL.seq) return;
    const hits = (d.hits || []).map(h => ({
      kind: 'content', label: h.title, id: h.id,
      sub: h.corpus, snippet: h.snippet,
      // The pane this would attach to, decided when the row is BUILT so the
      // row can say what it will do. `focused` may be undefined — the row
      // then offers to open a pane, which is the honest fallback.
      pane: focused }));
    renderPalette([...rows, ...hits].slice(0, 40), needle, d.error);
  }, 160);
}

function renderPalette(rows, needle, contentError) {
  PAL.rows = rows; PAL.sel = 0;
  const res = $('#pal-res');
  res.innerHTML = '';
  if (contentError) res.appendChild(el('div', 'palnote', contentError));
  if (!rows.length) {
    // An empty palette has two very different causes and they need different
    // sentences. A bare "Nothing matches." on a box with no configured roots
    // is a lie by omission — it says your query failed when the truth is the
    // index is empty.
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
    // The snippet is FILE-DERIVED TEXT on an authed control surface, so it is
    // set as textContent by el() and never parsed as markup (P20). This is
    // the only content-derived string the page renders at all.
    if (r.snippet) t.appendChild(el('span', 'palsnip', r.snippet));
    row.appendChild(t);
    if (r.kind === 'content') {
      row.appendChild(el('span', 'palhint',
        r.pane ? '↵ attach · ⇧↵ new pane' : '↵ new pane here'));
    }
    row.onmousedown = e => e.preventDefault();      // keep focus in the input
    row.onclick = e => activatePalette(r, e.shiftKey);
    res.appendChild(row);
  });
}

async function activatePalette(row, newPane) {
  $('#palette').close();
  if (row.kind === 'action') return $('#new').click();
  if (row.kind === 'pane') return focusPane(row.paneId);
  if (row.kind === 'archived') {
    try {
      await api('/api/session/reopen', { pane: row.paneId });
      await refresh();
      focusPane(row.paneId);
    } catch (e) { toast(e.message, true); }
    return;
  }
  if (row.kind !== 'content') return;
  await attachContent(row.id, newPane || !row.pane ? null : row.pane.id);
}

/* Attach a note to a pane's composer — or to a new pane opened where it
 * lives. The SERVER decides what the inserted text is (a path for a lane with
 * tools, a quoted excerpt for one without); this only has to put it in the
 * right box and leave the cursor after it. */
async function attachContent(id, paneId) {
  let d;
  try { d = await api('/api/content/attach', { id, pane: paneId || '' }); }
  catch (e) { return toast(e.message, true); }
  if (!paneId) {
    // No pane to attach to (none focused, or ⇧↵). Open one where the file
    // lives — the directory is the context an agent with tools actually
    // needs, and it is the same create a click on New makes.
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
      // The new pane is a different LANE from the one the text was computed
      // for, so ask again rather than pasting an excerpt into a pane that can
      // read the file perfectly well (or a bare path into one that cannot).
      d = await api('/api/content/attach', { id, pane: paneId });
    } catch (e) { return toast(e.message, true); }
  }
  focusPane(paneId);
  requestAnimationFrame(() => {
    const box = document.querySelector(`[data-pane="${paneId}"] .composer textarea`)
             || document.querySelector(`[data-pane="${paneId}"] .composer input`);
    if (!box) return toast('attached, but that pane has no composer', true);
    box.value = d.text + (box.value || '');
    box.focus();
    // Cursor AFTER the inserted text: you are about to type the question, and
    // landing at position 0 means typing in front of your own attachment.
    const at = d.text.length;
    box.setSelectionRange(at, at);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    toast(d.mode === 'excerpt'
      ? `quoted "${d.title}" — this lane has no tools, so the text came along`
      : `referenced "${d.title}" — the agent will read it through its own gate`);
  });
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
  q.oninput = () => paletteResults(q.value);
  q.onkeydown = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); movePaletteSel(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); movePaletteSel(-1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (PAL.rows[PAL.sel]) activatePalette(PAL.rows[PAL.sel], e.shiftKey);
    }
  };
  // Global, and deliberately NOT swallowed inside a composer: ⌘K is how you
  // reach a note while writing the message that needs it, which is the whole
  // point of attach. Escape is the dialog's own.
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      $('#palette').open ? $('#palette').close() : openPalette();
    }
  });
}

/* ── focus ───────────────────────────────────────────────────────────────
 * Light has one room, so "open this conversation" is only ever: remember it
 * as focused (the roster keeps it out of "Other" and highlights it) and
 * scroll it into view. The full Corral's version also had to switch rooms
 * first — several call sites there once set `location.hash = '#pane=' + id`
 * instead, a hash nothing routed, so clicking a conversation from the rail
 * silently did nothing. One function, called from everywhere, is the fix
 * that keeps working.
 */
function focusPane(id) {
  S.focus = id;
  render();
  requestAnimationFrame(() =>
    document.querySelector(`[data-pane="${id}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
}

/* ── PWA ─────────────────────────────────────────────────────────────── */
// Registered only so Brave/Chrome will offer "Install app"; it caches nothing
// (see sw.js). Requires a secure context, so on plain http this silently
// no-ops — which is why the install affordance may not appear. Reaching Light
// at http://127.0.0.1:8098 IS a secure context, so the default local bind is
// also the one where install works.
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

/* ── boot ────────────────────────────────────────────────────────────── */
async function start() {
  $('#app').classList.remove('hide');
  wireThemes();
  wireDialog();
  wireRail();
  wireCopySelect();
  wirePalette();
  // Stream FIRST, then snapshot. The reverse order left a window between the
  // snapshot and the EventSource opening in which every event was dropped and
  // never recoverable — the actual cause of "reload loses running work".
  connect();
  await refresh();
  // A backgrounded tab has its timers throttled, and Light has no polling
  // loop left to be throttled — every update arrives on the SSE stream. But a
  // tab that was asleep long enough for the browser to drop the connection
  // comes back with a stale view and an `onerror` that may not have fired
  // yet, so re-sync on the way in rather than trusting the stream survived.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh().catch(() => {});
  });
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
