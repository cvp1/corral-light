import { query } from "../node_modules/@agentclientprotocol/claude-agent-acp/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs";
let release; const hold = new Promise(r => release = r);
async function* input() {
  yield { type: "user", message: { role: "user", content: "Reply with the single word OK." }, parent_tool_use_id: null, session_id: "" };
  await hold;
}
const t0 = Date.now();
const q = query({ prompt: input(), options: { model: "claude-haiku-4-5-20251001", persistSession: false,
  cwd: new URL("./probe-cwd/", import.meta.url).pathname, allowedTools: [], settingSources: [] } });
for await (const m of q) {
  if (m.type === "rate_limit_event") console.log("RLE", Date.now()-t0, JSON.stringify(m.rate_limit_info));
  if (m.type === "result") {
    const t1 = Date.now();
    try { const u = await q.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET();
      console.log("USAGE ms", Date.now()-t1, "sub", u.subscription_type, "available", u.rate_limits_available);
      console.log("USAGE rate_limits", JSON.stringify(u.rate_limits));
    } catch (e) { console.log("usage call failed:", String(e).slice(0,200)); }
    release(); q.interrupt?.().catch(()=>{}); break;
  }
}
console.log("done", Date.now()-t0); process.exit(0);
