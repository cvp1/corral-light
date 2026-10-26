/* The wall's progress strip and agents panel are derived from a pane's own
 * events (app.js agentView): the flow from the main session's `progress`
 * calls, each subagent from its Agent call, its steps and tool calls from the
 * calls stamped with that Agent call as parent.
 *
 * Run: node selftest_agents.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./static/app.js', import.meta.url), 'utf8');

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
  const m = new RegExp(`^const ${name} = [\\s\\S]*?;$`, 'm').exec(src);
  if (!m) throw new Error(`no const ${name} in app.js`);
  return m[0];
}

const A = new Function(`
  ${['FLOW_TOOL', 'FLOW_STEP_TOOL', 'FLOW_PHASES', 'FLOW_TIERS', 'FLOW_MAX_TASKS', 'TIER_MODEL']
      .map(constant).join('\n')}
  ${['agentNorm', 'agentTier', 'flowBlank', 'flowIsNew', 'flowMerge', 'toolText', 'agentView',
     'flowLabel', 'flowRatio', 'stripKey'].map(fn).join('\n')}
  return { agentView, flowLabel, flowRatio, stripKey, agentTier };
`)();

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

let seq = 0;
const at = s => new Date(Date.UTC(2026, 9, 25, 12, 0, s)).toISOString().replace('.000', '');
const tool = (s, d) => ({ seq: ++seq, at: at(s), kind: 'tool', data: d });
const end = (s, stopReason = 'end_turn') => ({ seq: ++seq, at: at(s), kind: 'turn_end', data: { stopReason } });
const progress = (s, id, input, parent) =>
  tool(s, { id, name: 'mcp__savvy-progress__progress', status: 'completed', input, ...(parent ? { parent } : {}) });
const agent = (s, id, input, status = 'pending', text) =>
  tool(s, { id, name: 'Agent', status, input,
            content: text ? [{ type: 'content', content: { type: 'text', text } }] : [] });
const step = (s, id, parent, input) =>
  tool(s, { id, name: 'mcp__savvy-progress__step', parent, status: 'completed', input });
const child = (s, id, parent, title) => tool(s, { id, name: 'Bash', parent, title, status: 'completed' });

/* ── a flow: plan, two workers, one accepted ───────────────────────────── */
seq = 0;
const evs = [
  { seq: ++seq, at: at(0), kind: 'cleared', data: {} },
  progress(1, 'p1', { title: 'Agents strip', total: 3, phase: 'delegate',
                      tasks: [{ title: 'Hub fields', tier: 'medium', after: [] },
                              { title: 'Wall strip', tier: 'careful', after: [1] },
                              { title: 'Tests', tier: 'light', after: [1, 2] }] }),
  agent(2, 'a1', { description: 'Hub fields', subagent_type: 'savvy-medium' }),
  agent(2, 'a2', { description: 'Wall strip', subagent_type: 'savvy-flow:savvy-careful', background: true }),
  agent(2, 'a2', { description: 'Wall strip', subagent_type: 'savvy-flow:savvy-careful', background: true },
        'completed', 'Async agent launched successfully.'),
  step(3, 's1', 'a1', { done: 0, total: 4 }),
  child(4, 'c1', 'a1', 'grep tool_event'),
  step(5, 's2', 'a1', { done: 2, note: 'patching' }),
  child(6, 'c2', 'a2', 'node --check'),
  // A worker trying to finish the flow is ignored.
  progress(7, 'p2', { finished: true, done: 3 }, 'a2'),
  agent(8, 'a1', { description: 'Hub fields', subagent_type: 'savvy-medium' }, 'completed', 'Done: two files'),
  progress(9, 'p3', { done: 1, phase: 'review' }),
];
let v = A.agentView(evs);
check(v.flow && v.flow.title === 'Agents strip', 'the flow takes its title from progress');
check(v.flow.done === 1 && v.flow.total === 3 && v.flow.phase === 'review', `merged counters: ${JSON.stringify(v.flow)}`);
check(!v.flow.finished, "a worker's progress call does not finish the flow");
check(A.flowLabel(v.flow) === 'Review 1/3', `label: ${A.flowLabel(v.flow)}`);
check(v.agents.length === 2, `two runs: ${v.agents.length}`);
const [a1, a2] = v.agents;
check(a1.status === 'done' && a1.tier === 'medium', `a foreground run completes with its call: ${a1.status}`);
check(a1.step.done === 2 && a1.step.total === 4 && a1.step.note === 'patching', `steps merge: ${JSON.stringify(a1.step)}`);
check(a1.tools === 1, `step calls are not tool calls: ${a1.tools}`);
check(a2.background && a2.status === 'running', `a launched background run is running: ${a2.status}`);
check(a2.tier === 'careful' && a2.tierModel === 'Opus · high', 'a plugin-prefixed savvy type has a tier');
check(a2.lastTool === 'node --check', 'the last child call is the note while running');
check(v.flow.running === 1, `running workers: ${v.flow.running}`);
check(v.planned.length === 1 && v.planned[0].n === 3 && v.planned[0].title === 'Tests',
      `only the unstarted task is planned: ${JSON.stringify(v.planned)}`);

/* ── the turn that waited on the background run ends ───────────────────── */
v = A.agentView([...evs, end(20)]);
check(v.agents[1].status === 'done', `a turn_end finishes a background run: ${v.agents[1].status}`);
check(v.agents[1].endedAt === Date.parse(at(7)), 'it ended at its last own activity');
/* … and a child call after that turn_end means it was still going. */
v = A.agentView([...evs, end(20), child(21, 'c3', 'a2', 'more')]);
check(v.agents[1].status === 'running', 'child activity after a turn_end reopens the run');
/* A cancelled turn stops what it ran; a dead pane stops everything. */
v = A.agentView([...evs, end(20, 'cancelled')]);
check(v.agents[1].status === 'stopped', 'a cancelled turn stops its background run');
v = A.agentView(evs, false);
check(v.agents[1].status === 'stopped', 'a dead pane has no running agents');

/* ── a new title is a new flow; finishing ──────────────────────────────── */
v = A.agentView([...evs, end(20), progress(30, 'p4', { title: 'Next thing', total: 1, tasks: [{ title: 'Only', tier: 'light' }] })]);
check(v.flow.title === 'Next thing' && v.flow.done === 0 && v.flow.total === 1, 'a new flow resets its counters');
check(v.agents.length === 0, 'runs that finished before the new flow are dropped');
v = A.agentView([...evs, progress(30, 'p5', { finished: true })]);
check(v.flow.finished && A.flowRatio(v.flow) === 1 && A.flowLabel(v.flow) === 'Done', 'finished fills the bar');
check(v.planned.length === 0, 'a finished flow plans nothing');

/* ── plain subagents, no flow ──────────────────────────────────────────── */
seq = 0;
const plain = [agent(1, 'x1', { description: 'Explore the repo', subagent_type: 'Explore' })];
v = A.agentView(plain);
check(!v.flow && v.agents.length === 1 && v.agents[0].tier === 'other', 'a non-savvy run draws no flow');
check(A.stripKey(v) !== '', 'a running subagent still shows the strip');
v = A.agentView([...plain, end(5)]);
check(v.agents[0].status === 'stopped', 'a foreground call left open by its turn was stopped');
check(A.stripKey(v) === '', 'nothing running and no flow: no strip');

/* ── safety net: savvy workers with no progress report ─────────────────── */
seq = 0;
v = A.agentView([agent(1, 'w1', { description: 'Fix', subagent_type: 'savvy-light' })]);
check(v.flow && v.flow.phase === 'delegate' && A.flowLabel(v.flow) === 'Tasks 1 running',
      `a savvy worker alone draws a flow: ${v.flow && A.flowLabel(v.flow)}`);

/* ── events from before the hub kept names draw nothing ────────────────── */
seq = 0;
v = A.agentView([tool(1, { id: 'old', title: 'Task', kind: 'think', status: 'pending' })]);
check(!v.flow && v.agents.length === 0, 'an unnamed old Task row is not guessed at');

check(A.agentTier('savvy-fable') === 'fable' && A.agentTier('fable') === 'other' &&
      A.agentTier('savvy-unknown') === 'other', 'tiers come only from savvy-* types');

if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log('selftest_agents: ok');
