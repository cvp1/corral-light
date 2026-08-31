# Corral Light — the multi-model workspace, without the fleet

One window where conversations with several agents run side by side, and
anything needing you is visible without hunting for it. That is all it is.

    corral-light serve                 # http://127.0.0.1:8098
    corral-light pair ABC-DEF          # authorize the browser showing that code
    corral-light doctor                # which lanes can start here, and why not

## What this is

The **Live** surface of `corral/`, forked for dogma-2 on 2026-08-31 and made
standalone. Craig: *"a version of Corral I can run on Dogma that will only do
essentially the functions in the Live tab — a lightweight multi-model
interface."*

Everything ranch-server's Corral grew because it sits on the fleet is gone:
Today, Mail, Fleet, Delegates, FinOps, the Library, the scheduler, the run
registry, the attention queue, push notifications, tmux adoption, the AI-OS
slash commands, ssh-shell lanes, delegate-box lanes. Not disabled — **gone**. A
light build carrying dead branches is the heavy build with a smaller menu, and
a route that answers "unavailable" is still a surface to maintain.

**A fork, deliberately, not a shared tree with a flag.** Fleet doctrine is that
capability moves by install seed, never by synced trees (`FLEET.md`), and the
two products have genuinely different owners: ranch-server's Corral answers to
the fleet, this one answers to one machine. The cost is real — a fix to the
pane renderer has to be carried across by hand — and it is bought back by
`test_corral_light.py`, which fails the moment this build starts importing its
way back to heavy.

## What it keeps, because this is the part that matters

- **Panes.** One agent process per conversation, its own cwd, its own model and
  effort, its own transcript on disk. Reload the browser, restart the server,
  reboot the machine: the conversations come back (`detached` — attached again
  on the first message or a click).
- **The permission rail.** An agent asking to write a file or run a command is
  a real process blocked on you. The card carries the **exact bytes** and a
  sha256 of them; a payload too large to display can only be *refused*, never
  approved, and that gate is enforced on the server, not in the browser
  (P17 — an approval proves only what the human could see).
- **Honest lanes.** The picker greys out what cannot start and says why, at pick
  time rather than by handing you a pane that dies on its first prompt.
- **Honest posture.** Only the Claude lane runs under a `CLAUDE_CONFIG_DIR`
  Corral owns, so only that lane's permission pill is a claim Corral can back.
  Every other pane says `agent-set` instead of displaying a safety property
  nobody established.
- Markdown rendering, find-in-conversation, copy-on-select, minimize, pin,
  reorder, rename, archive/reopen, seven measured themes, PWA install.

## Lanes

| lane | how it starts | notes |
|---|---|---|
| Claude Code | `spike/node_modules/.bin/claude-agent-acp` | model + effort selectable; the one lane whose permission posture Corral imposes |
| ChatGPT (Codex) | `codex_launcher.py` → `@agentclientprotocol/codex-acp` | owns a dedicated `CODEX_HOME` (`~/.config/corral-light/codex-home`) so it can never inherit another config's model; refuses up front when not logged in, naming the device-auth command |
| Grok | `grok_launcher.py` → `grok agent stdio` | the vendor CLI owns auth; the credential never enters argv, env, or logs |
| Antigravity (Gemini) | `antigravity_acp_launcher.py` | Google's own native ACP server; OAuth stays in the vendor's local state. Install/verify with `python3 install_antigravity_acp.py --install` / `--check` |
| Local (Ollama) | `ollama_acp.py` | **the sovereign lane** — no key, no vendor, works with the WAN down. **Chat only: no tools, so no permission requests.** The label says so, because an empty rail and a broken rail look identical |

Third-party providers (DeepSeek, OpenRouter) are absent for the same reason
they are absent upstream: a pane is an agent with tools in a working tree, not
a data-classed API call, so PRINCIPLES 11 has nothing to grade it against.

### Adding a lane

`sessions.AGENTS` is the one place. A lane needs `label`, `argv`,
`posture_via_config_dir`, and — this is the part that is easy to skip —
`requires`, naming every file it needs on disk. Four of the five lanes launch
through an interpreter, so `argv[0]` is `python3` and exists everywhere;
without `requires`, availability is judged by a check that cannot fail.
`test_corral_light.py` enforces this.

## Install (dogma-2, macOS)

```bash
git clone <this repo>/corral-light ~/Github/CC/corral-light
cd ~/Github/CC/corral-light/spike && npm install     # the two node ACP adapters
cd .. && ./corral-light doctor                        # see what is live
cp com.cvande.corral-light.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.cvande.corral-light.plist
open http://127.0.0.1:8098/
./corral-light pair <the code the page shows>
```

Only `python3` (3.9+, the system one is fine) and — for the Claude and Codex
lanes only — `node`. No venv, no pip, no `_lib`, no CC workspace: the
structural tests prove it stays that way.

## Configuration

| variable | default | |
|---|---|---|
| `CORRAL_LIGHT_BIND` | `127.0.0.1` | **not** `0.0.0.0`, unlike ranch's Corral (see below) |
| `CORRAL_LIGHT_PORT` | `8098` | the full Corral uses 8099 |
| `CORRAL_LIGHT_STATE` | `~/.local/share/corral-light` | panes, transcripts, the session key |
| `CORRAL_OLLAMA_URL` | `http://127.0.0.1:11434` | point elsewhere to borrow another node's models |
| `CORRAL_CLAUDE_ADAPTER` | `spike/node_modules/.bin/claude-agent-acp` | |
| `CORRAL_NODE_BIN` | `~/.hermes/node/bin` *if it exists* | ignored when absent, so a stock Mac's PATH node wins |

**Why loopback by default.** Ranch's Corral binds `0.0.0.0` on the argument
that the ranch LAN is first-class local and the boundary is the pairing gate,
not the network. Light runs on a machine that *moves between networks*, and a
coffee-shop LAN did not earn that ruling. Reach it from another ranch machine
with `ssh -N -L 8098:127.0.0.1:8098 dogma-2` — which also makes it a secure
context, so the PWA install prompt actually appears. Setting
`CORRAL_LIGHT_BIND=0.0.0.0` is a decision to make deliberately, not a default
to inherit.

Light and full Corral use different ports, different state dirs, different
cookie names, and different CLI names, so both can run on one host without
either corrupting the other's panes or minting a cookie the other accepts.

## Verified live, 2026-08-31

Not "the tests pass" — run end to end against real agents on ranch-server
before this shipped (P13):

- **Ollama lane** — pane opened against the .21 node, catalog seeded with its 19
  real models, prompt sent, answer streamed back and rendered as markdown in the
  browser over SSE.
- **Claude lane** — pane opened under `strict`, asked to write a file; the
  permission request arrived in the rail carrying its title, digest, byte
  count and three real options; answering `allow_once` over the API delivered
  it and `proof.txt` appeared on disk with the right contents.
- **Restart** — hub killed and restarted; closed panes came back as archive
  rows, transcripts intact.
- **Browser** — pairing screen → paired → app renders, one JS bundle, no
  console errors beyond the expected pre-pairing 401.

## Tests

```bash
python3 -m unittest test_corral_light -v
```

Half of them are structural, and those are the ones that earn their keep: no
module reaches into the CC workspace, no fleet module is imported or present,
`hub.py` serves only Live routes, and **every `/api/...` path the front end
calls is one the server actually serves**. The front end here is a *trim* of a
4,514-line file — a leftover `api('/api/attention')` would not be a syntax
error, it would be a silent 404 on every render, which is exactly the class of
bug a trim produces.

## Where the reasoning lives

The comments in `sessions.py`, `acp.py` and `static/app.js` carry the measured
history behind decisions that look arbitrary — why a turn is never ended by a
clock, why a permission's payload lives with the pending request instead of in
the bounded event ring, why the composer is built once per pane. That history
is upstream's, kept because deleting the reason is how a fix gets undone by
someone who only sees the code. Upstream: `../corral/` (`DESIGN.md`,
`reviews/`, and its README's lane table).
