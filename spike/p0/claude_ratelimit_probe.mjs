// Phase 0: which rate_limit_event messages arrive on a one-turn query, and what
// the experimental structured usage call returns. Prints numbers and enums only.
import { query } from "../node_modules/@agentclientprotocol/claude-agent-acp/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs";
const t0 = Date.now();
const q = query({ prompt: "Reply with the single word OK.", options: {
  model: "claude-haiku-4-5-20251001", maxTurns: 1, persistSession: false,
  cwd: new URL("./probe-cwd/", import.meta.url).pathname,
  allowedTools: [], settingSources: [] } });
const seen = [];
let usageDone = false;
for await (const m of q) {
  const dt = Date.now() - t0;
  if (m.type === "rate_limit_event") console.log("RLE", dt, JSON.stringify(m.rate_limit_info));
  else if (m.type === "assistant" && !seen.includes("first_assistant")) { seen.push("first_assistant"); console.log("first assistant", dt); }
  else if (m.type === "result") { console.log("result", dt, "cost", m.total_cost_usd);
    if (!usageDone) { usageDone = true;
      try { const u = await q.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET();
        console.log("USAGE subscription_type", u.subscription_type, "available", u.rate_limits_available);
        console.log("USAGE rate_limits", JSON.stringify(u.rate_limits));
      } catch (e) { console.log("usage call failed:", String(e).slice(0, 200)); } } }
  else if (m.type === "system") console.log("system", m.subtype, dt);
}
console.log("done", Date.now() - t0);
