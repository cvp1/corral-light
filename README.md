# Corral Light — the multi-model workspace, without the fleet

One window where conversations with several agents run side by side, and
anything needing you is visible without hunting for it. That is all it is.

    corral-light serve                 # http://127.0.0.1:8098
    corral-light pair ABC-DEF          # authorize the browser showing that code
    corral-light doctor                # which lanes can start here, and why not
    corral-light diagnose [lane]       # run one lane end to end and show everything

## What this is

The **Live** surface of `corral/`, forked for dogma-2 on 2026-08-31 and made
standalone. Craig: *"a version of Corral I can run on Dogma that will only do
essentially the functions in the Live tab — a lightweight multi-model
interface."*

Everything ranch-server's Corral grew because it sits on the fleet is gone:
Today, Mail, Fleet, Delegates, FinOps, the scheduler, the run registry, the
attention queue, push notifications, tmux adoption, the AI-OS slash commands,
ssh-shell lanes, delegate-box lanes. Not disabled — **gone**. A light build
carrying dead branches is the heavy build with a smaller menu, and a route that
answers "unavailable" is still a surface to maintain.

The Library is the one that came back, and it came back **reimagined rather
than ported** — as ⌘K + attach, not as a room. See below.

**The definition, so it stops moving:** Light is *conversations, with your
content reachable from the composer*. That is the product. Every room upstream
has is a candidate for "just this one more", and each would be defensible on
its own; the line is here.

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
- **⌘K over everything** — see below.
- Markdown rendering, find-in-conversation, copy-on-select, minimize, pin,
  reorder, rename, archive/reopen, seven measured themes, PWA install.

## ⌘K — your content, reachable from the composer

The full Corral has a **Library**: a room you navigate to, browse, and read a
rendered page in. Light does not port it, because that shape is navigation
scaffolding for a building with seven floors and Light has one. Inverted
instead — content is not a place you go, it is something you *attach*:

    ⌘K  →  type  →  ↵ attach to this conversation
                    ⇧↵ open a new pane where that file lives

One ranked list over **open panes, archived conversations, and your notes**.
In a one-room app the palette *is* the navigation, so it searches everything;
local matches are instant, content folds in when the index answers (~2 ms
against 683 pages here).

**What "attach" inserts depends on the lane, and the difference is the point:**

| lane | inserts | why |
|---|---|---|
| has tools (Claude, Codex, Grok, Antigravity) | the file **path** | the agent opens it *itself*, through its own permission gate — visible in the transcript, refusable in the rail. Corral does not smuggle file contents past the gate |
| no tools (Ollama) | a bounded **quoted excerpt** | a path handed to an agent with no filesystem is a dead end that looks like a working feature |

Either way it lands in the composer and **nothing is sent** — you read it, add
your question, and press send. Attaching authorizes nothing (P17).

### What it indexes

Whatever `~/.config/corral-light/content.json` names; `~/notes` by default if
that file is absent and the directory exists:

```json
[{"key": "vault", "label": "notes", "root": "~/notes"},
 {"key": "work",  "label": "work",  "root": "~/Documents/work"}]
```

Never a hardcoded corpus list — a config shipping five directories nobody has
is a feature broken on arrival. `.md`/`.markdown`/`.txt`, dot-dirs and
`node_modules` pruned, symlinks that escape a root not followed, bounded at
20k files per root and 200 KB per file. The index (`content.db`, SQLite FTS5)
is **derived and disposable** — delete it and it rebuilds; nothing in it is a
system of record. No FTS5 on your sqlite? It degrades to a substring scan and
*says so in the palette* rather than quietly getting worse.

Inspect it without the browser: `python3 content.py status | search <q> | refresh`.

### The security dividend

Upstream needs `mdview.py` — 217 lines of escape-everything, emit-only-tags-we-
spell-out — precisely *because* it renders vault pages, and vault notes carry
pasted third-party content on an authed control surface (P20). Light never
renders your content in the browser at all, so that renderer stays deleted and
the surface it defends goes with it. The FTS snippet is the only file-derived
string that reaches the page, and it is set as `textContent`. A test enforces
this: if a markdown renderer ever comes back, the escaping argument has to come
back with it.

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

## Install on a blank box

Verified 2026-08-31 by actually doing it: a fresh copy of this tree, an empty
`HOME`, `env -i` (no inherited environment at all), `PATH=/usr/bin:/bin`. It
came up, paired, opened a pane from the browser and answered — with nothing
installed but `python3` and an Ollama to talk to.

**The floor is `python3` 3.9+ and nothing else.** No venv, no pip, no node, no
`_lib`, no CC workspace. `hub.py` refuses to start on 3.8 naming the reason,
rather than failing later inside a request handler.

```bash
git clone https://github.com/cvp1/corral-light.git && cd corral-light
./corral-light doctor       # says which lanes work here, and why the rest don't
./corral-light serve        # http://127.0.0.1:8098 — leave it running
./corral-light pair ABC-DEF # in a second shell: the code the page is showing
```

`serve` runs in the foreground and the pairing code only exists while it is up,
so those last two are two shells, not two steps in one. (Or run it as a service
first — below — and pair once against that.)

That is the whole install. It creates `~/.local/share/corral-light` on first
run and needs no other setup.

### What a truly blank box gets you

Lanes are the box's own software, not this repo's — so on a bare machine
`doctor` correctly greys out all four vendor lanes and you have **no working
lane at all** until you add one. Cheapest first:

| to get | install on the box | notes |
|---|---|---|
| **Local (Ollama)** | `ollama`, then `ollama pull <model>` | works immediately; or set `CORRAL_OLLAMA_URL` to borrow another machine's models |
| **Claude Code** | `node`, then `cd spike && npm install` | **`doctor` cannot verify this one's login** — see the note below. Also needs Claude Code's own login there: the pane copies `~/.claude/.credentials.json` and `~/.claude.json` into its config dir, and symlinks `~/.claude/{skills,agents,commands,plugins,prompts,CLAUDE.md}` so a pane has the same capability the terminal does |
| **ChatGPT (Codex)** | the same `npm install` | then log in — run the exact command `doctor` prints (it creates `CODEX_HOME` and logs in, in one paste). The separate `CODEX_HOME` is deliberate: a shared one is how a pane ends up on whatever model another config pinned, so this is a separate login from any codex you already use |
| **Grok** | the Grok CLI, then `grok login` | the CLI owns auth; nothing here touches the credential. `doctor` checks `~/.grok/auth.json` and prints the login command |
| **Antigravity** | `python3 install_antigravity_acp.py --install` | **Linux x86-64 only** — see below |

None of that is Corral-specific state to reproduce — it is each vendor's normal
login on that machine. If the CLI works in a terminal there, the lane works.

**When a pane fails, run `corral-light diagnose` first.** `doctor` answers
"can this lane start"; that question kept saying `ok` while every conversation
died, because the failure is one step further in — the handshake succeeds and
the first *prompt* fails. `diagnose` sends a real prompt and prints the whole
delta between a pane and your terminal: the resolved argv, whether a private
config dir was used or refused, which environment variables reach the agent
(names only — values are never printed, so it is safe to paste), and on
failure **the adapter's own stderr**, which `acp.py` has always captured and
until now only ever used one line of.

Then check this:

```bash
env | grep -iE 'ANTHROPIC|OPENAI|GEMINI|XAI|GROK'
```

A vendor key exported in the shell that started the hub silently **outranks**
the login you verified — the agent runs as a different identity than the one
the picker described, and `/usage` shows token statistics instead of your
subscription page because it is in API-key mode. Since 2026-08-31 panes strip
`ANTHROPIC_*`, `OPENAI_*`, `GEMINI_*`, `GOOGLE_API*`, `XAI_*`, `GROK_*` and
`CLAUDE_CODE_OAUTH*` from the agent's environment and `doctor` says so when it
does. `CORRAL_LIGHT_ALLOW_VENDOR_ENV=1` keeps them if API-key auth is what you
want.

The second thing to check is the private-config-dir case: a pane runs under a
`CLAUDE_CONFIG_DIR` Corral owns (that is what carries the per-pane permission
posture), seeded by copying `~/.claude/.credentials.json`. On macOS that file
need not exist — Claude Code can keep the OAuth in the Keychain — so the
private directory got created with no credential in it. Since 2026-08-31 the
seeding refuses in that case and the pane runs under your own `~/.claude`
instead; it then reports `postureEnforced: false` and wears the `agent-set`
pill, because whatever your ambient `defaultMode` is, is what you get. Losing
the per-pane posture is a real cost and it is the smaller one — a pane that
cannot run has no posture either.

**The Claude lane is probed live, not guessed at.** Every other lane is gated
on a credential file — which is a guess about where a vendor keeps its secret,
and for Claude on macOS that guess is wrong (the login can live in the
Keychain). So instead of a better guess, `doctor` and the picker run the real
handshake: spawn the adapter, `initialize`, `session/new`, read what comes
back, kill it. Cached 120 s, ~2 s cold.

That one call answers both questions that matter, which is why it exists:
**can this lane authenticate here**, and **what models and effort levels does
it offer** — because a lane's model/effort lists come from nowhere else but a
completed `session/new`. Before this, a lane that could not authenticate could
never fill its own pickers, so a fresh box showed a disabled "Default (agent
decides)" for both and there was no way to choose Opus or an effort level
until some session had already succeeded. If the handshake fails, the picker
shows **the vendor's own words** ("Authentication required"), not a paraphrase.

**Antigravity is Linux x86-64 only.** Google publishes this ACP server under
`.../releases/linux/`; the darwin and mac paths 404 (probed 2026-08-31). The
installer refuses on any other platform rather than putting a binary on disk
that cannot exec — because `--install` succeeding is what would then make
`doctor` report the lane **ok**, which is worse than the honest "not
installed" it replaced. The picker checks the platform too, so a hand-copied
or home-synced binary cannot produce a lying lane either. If a build for your
platform appears, pinning it is an operator decision: set `RELEASE`, `URL` and
`ARCHIVE_SHA256` in `install_antigravity_acp.py` to the real archive and its
verified digest.

### Run it as a service

- **Linux:** `corral-light.service` — a systemd *user* unit; its header carries
  the three install commands, plus `loginctl enable-linger` for a headless box.
- **macOS:** `com.cvande.corral-light.plist` — a launchd user agent. Edit the
  two `/Users/cvande` paths for another account.

Both are USER services on purpose. Every lane authenticates as the logged-in
user and every pane runs with that user's filesystem access, so running this as
root would hand a browser a root shell behind a pairing gate.

## Configuration

| variable | default | |
|---|---|---|
| `CORRAL_LIGHT_BIND` | `127.0.0.1` | **not** `0.0.0.0`, unlike ranch's Corral (see below) |
| `CORRAL_LIGHT_PORT` | `8098` | the full Corral uses 8099 |
| `CORRAL_LIGHT_STATE` | `~/.local/share/corral-light` | panes, transcripts, the session key |
| `CORRAL_OLLAMA_URL` | `http://127.0.0.1:11434` | point elsewhere to borrow another node's models |
| `CORRAL_CLAUDE_ADAPTER` | `spike/node_modules/.bin/claude-agent-acp` | |
| `CORRAL_CONTENT_CONFIG` | `~/.config/corral-light/content.json` | which directories ⌘K indexes |
| `CORRAL_NODE_BIN` | `~/.hermes/node/bin` *if it exists* | ignored when absent, so a stock Mac's PATH node wins |

**Why loopback by default.** Ranch's Corral binds `0.0.0.0` on the argument
that the ranch LAN is first-class local and the boundary is the pairing gate,
not the network. Light runs on a machine that *moves between networks*, and a
coffee-shop LAN did not earn that ruling. Reach it from another ranch machine
with `ssh -N -L 8098:127.0.0.1:8098 dogma-2` — which also makes it a secure
context, so the PWA install prompt actually appears. Setting
`CORRAL_LIGHT_BIND=0.0.0.0` is a decision to make deliberately, not a default
to inherit.

## Running both on one host

Yes — Light is built to sit next to the full Corral on the same machine, and
the separation is audited by a test rather than asserted here.

| | full Corral | Light |
|---|---|---|
| port | 8099 | **8098** |
| state | `~/.local/share/corral` | `~/.local/share/corral-light` |
| cookie | `corral` | `corral_light` |
| MCP config | `~/.config/corral/mcp.json` | `~/.config/corral-light/mcp.json` |
| codex home | `~/.config/corral/codex-home` | `~/.config/corral-light/codex-home` |
| CLI | `corral` | `corral-light` |
| unit | `corral.service` | `corral-light.service` / the plist |

Separate state dirs matter more than they look: they hold the **session key**,
so a shared one would let either hub mint a cookie the other accepts, and each
would restore the other's panes with lanes it does not have.

**What they genuinely share, and what that costs:**

- **Vendor logins.** Grok's CLI state and Antigravity's OAuth are the vendor's,
  not Corral's — one login serves both, which is the good case. The Antigravity
  *binary* is shared too (`~/.local/lib/corral/antigravity-acp`), so installing
  it from either build is enough.
- **Codex is the exception**: separate `CODEX_HOME`s means a separate
  `codex login --device-auth` for each. Deliberate — a shared codex home is
  how a pane ends up talking to whatever model the *other* config pinned.
- **Claude credentials — the one to actually watch.** Both builds copy
  `~/.claude/.credentials.json` into each pane's own config dir at pane
  creation, and that copy cannot be refreshed from the original. Two hubs means
  more independent copies of one rotating OAuth token, alongside your terminal
  Claude Code as a third. Observed live 2026-08-31: a fresh pane died with
  `OAuth session expired and could not be refreshed` twenty-eight seconds after
  the host credential's own `expiresAt`. This is inherited upstream behaviour,
  not something Light introduced, and it is not made *worse* by a second hub so
  much as made more likely to be noticed. If a Claude pane dies this way, the
  fix is to refresh the login on the host and start a new pane.
- **Env var NAMES overlap** (`CORRAL_CODEX_HOME`, `CORRAL_MCP_CONFIG`,
  `CORRAL_NODE_BIN`, `CORRAL_CLAUDE_ADAPTER`, `CORRAL_OLLAMA_URL`). The
  *defaults* differ correctly, but exporting one in a shell that launches both
  points both at the same thing. Set them in the unit file, not in `.bashrc`.

## Verified live, 2026-08-31

Not "the tests pass" — run end to end against real agents before this shipped
(P13):

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
- **⌘K + attach** — indexed the real vault (683 pages, FTS5, 2 ms queries);
  ⌘K in the browser → `ranch letter` → ↵ put the note's **path** into a Claude
  pane's composer; the same hit into an **Ollama** pane quoted a 2,798-char
  excerpt instead, and that chat-only lane — which cannot read a file — then
  answered a question about the note correctly from the quoted text alone.
  Nothing was sent in either case until the composer was submitted by hand.
  Fixed while testing: with exactly one pane open, every hit offered "open a
  new pane" because `S.focus` is only set by clicking a roster row — an attach
  target that ignores the only conversation on screen.
- **Clean room** — the tracked tree copied to an empty directory with an empty
  `HOME` under `env -i`: hub started, browser paired, "New conversation"
  defaulted its Directory to that box's own home, opened an Ollama pane and
  answered. Two bugs this caught, both invisible from the build machine: the
  new-conversation dialog hardcoded `/home/cvande/Github/CC` (a directory that
  exists on exactly one computer), and `mcp.py` still read the FULL Corral's
  `~/.config/corral/mcp.json`. Both are now covered by tests.

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
