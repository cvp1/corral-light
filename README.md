# Corral Light

**The window for AIOS.**

A local workspace for the AI coding assistants you already run, side by side, with one permission rail. The floor underneath — schedule, vault, run log, memory — is [AI-OS Seed](https://github.com/cvp1/ai-os-seed). Two repos, one folder.

## Install

You already have Claude Code. Python 3.9+.

One folder: `~/aios`. Seed lives in it. This app looks at it. A second folder is a second brain.

1. Install Seed into `~/aios` (or `--into` a workspace you already have — that folder then *is* `~/aios` for this purpose). See the Seed README. Do not install Seed into this repo.
2. Clone this repo, then:
   ```
   cd spike && npm install && cd ..    # the Claude and ChatGPT adapters
   ./corral-light doctor
   ./corral-light serve
   ```
   The adapters for Claude Code and ChatGPT (Codex) are an npm package, and
   `spike/node_modules/` is gitignored — so no clone arrives with them. Skip
   this step and those two lanes report `not installed: …/spike/node_modules/.bin/claude-agent-acp`,
   which reads like a broken install rather than a step you have not run yet.
   `doctor` names the step if the directory is missing. Needs Node.js.
   The other three lanes (Grok, Antigravity, Ollama) resolve their programs
   outside this tree and are unaffected.
   Open http://127.0.0.1:8098, then in another terminal `./corral-light pair <code>` with the code on screen.
3. New Claude conversation. Working directory = `~/aios`.
4. Done when `/status` answers.

The server runs in the foreground. Data lives at `~/.local/share/corral-light` — not in `~/aios`, and not in this clone. `doctor` lists the assistants that are ready and explains what is missing for the others.

## Supported assistants

Corral Light connects to software installed and signed in on your computer.

| Assistant | What you need | Notes |
| --- | --- | --- |
| Claude Code | Claude Code, and the adapter from `cd spike && npm install` | Supports model and effort selection. |
| ChatGPT (Codex) | The same npm install, plus a Codex login | Uses a separate configuration directory. |
| Grok | The Grok command-line tool and `grok login` | The Grok tool manages its own sign-in. |
| Antigravity (Gemini) | Run `python3 install_antigravity_acp.py --install` | The included installer currently supports Linux x86-64. It also selects your Google login (`oauth-personal`) in `~/.gemini/antigravity-acp/settings.json` when no sign-in method is set; a method you chose yourself is left alone. |
| Ollama | Ollama and at least one downloaded model | Chat only; it cannot edit files or run commands. |

The availability check is intentionally honest: an assistant is marked unavailable when a required program, login, or platform is missing. If an assistant passes that check but fails to answer, run:

```
./corral-light diagnose [assistant]
```

## Search and attach files

Press `⌘K` to search open conversations, archived conversations, notes, and other configured text files. Press `?` for every keyboard shortcut — that list is generated from the same table the key handler dispatches from, so it cannot advertise a key that does nothing.

By default, Corral Light searches `~/notes` when that directory exists. Add other directories in `~/.config/corral-light/content.json`:

```json
[
  {"key": "notes", "label": "Notes", "root": "~/notes"},
  {"key": "documents", "label": "Documents", "root": "~/Documents"}
]
```

The search index includes Markdown and plain-text files. It skips hidden directories and `node_modules`, refuses symlinks that leave the configured directories, and applies limits to the number and size of indexed files. The SQLite index is temporary derived data and can be deleted; it will be rebuilt from your files.

When you attach a search result:

- Coding assistants receive the file path and must open it through their normal approval flow.
- Chat-only assistants receive a bounded quoted excerpt.

Nothing is sent until you review the composer and press send.

Manage the index from a terminal:

```
python3 content.py status
python3 content.py search "your query"
python3 content.py refresh
```

## Passing work between assistants

Five ways to move work across panes, and when each one fits: attach a note,
quote one pane's answer into another, fan one prompt out to every pane (⌘↵),
cross-feed the answers so they argue (⇄), or ask an assistant to consult
another one itself. The guide, with the panel recipe and the rules that hold
in every case: [`docs/PASSING-WORK.md`](docs/PASSING-WORK.md).

An assistant (or any script) can do the last one through the wall instead of
through a vendor API: `corral-light consult ask --lane grok --prompt "…"`
opens a pane on that lane, waits for the answer, and prints it as JSON —
on the subscription the lane is already signed in with, in a pane you can
watch and answer. `consult lanes` lists what is live; `fanout` and
`crossfeed` are the ⌘↵ and ⇄ buttons from a script. Point your assistant at
it once in its instructions file ("to ask another model, use `corral-light
consult`, never an API key by default") and second opinions stop costing a
second bill.

## Security

The server listens only on your computer by default (`127.0.0.1`). To use it from another computer, create an encrypted SSH tunnel:

```
ssh -N -L 8098:127.0.0.1:8098 user@example.com
```

Or, from your phone on a [Tailscale](https://tailscale.com) tailnet, put Tailscale Serve in front of it — real HTTPS, tailnet-only, no open port:

```
tailscale serve --bg 8098                       # https://<machine>.<tailnet>.ts.net → 127.0.0.1:8098
CORRAL_TAILSCALE_LOGIN=you@example.com ./corral-light serve
```

With `CORRAL_TAILSCALE_LOGIN` set, a request that arrives through Serve must carry that tailnet identity (Serve stamps it and strips any forged copy); proxied traffic with no identity — Funnel, a tagged device — is refused; requests on the machine itself are unchanged. The session cookie is marked `Secure` when it is minted through Serve, and an open event stream re-checks its cookie every 30 seconds and closes itself when the cookie expires. Pairing still needs a shell on the machine — that is the point. What this does not do: separate the approval authority from the assistant's own UNIX user; anything running as you can still pair itself. (`corral_core/edge.py`, contract in `corral_core/test_edge.py`.)

When an assistant asks to write a file or run a command, Corral Light pauses it and shows the exact request, byte count, and SHA-256 digest. Requests too large to display cannot be approved. The browser cannot bypass this check because the server enforces it.

Corral Light removes common provider credential variables from assistant processes by default. This prevents a shell environment from silently changing which account an assistant uses. To intentionally allow those variables through, set:

```
CORRAL_LIGHT_ALLOW_VENDOR_ENV=1
```

Diagnostic output includes command names, configuration details, environment variable names, connection results, and assistant error messages. It never prints credential values.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORRAL_LIGHT_BIND` | `127.0.0.1` | Network address for the local server. |
| `CORRAL_LIGHT_PORT` | `8098` | Web interface port. |
| `CORRAL_LIGHT_STATE` | `~/.local/share/corral-light` | Saved conversations, history, and session security data. |
| `CORRAL_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server address. |
| `CORRAL_CLAUDE_ADAPTER` | `spike/node_modules/.bin/claude-agent-acp` | Claude adapter location. |
| `CORRAL_CONTENT_CONFIG` | `~/.config/corral-light/content.json` | Directories searched by `⌘K`. |
| `CORRAL_NODE_BIN` | An available Node.js installation | Optional Node.js path override. |
| `CORRAL_LIGHT_URL` | `http://127.0.0.1:8098` | Where `corral-light consult` finds the hub. |
| `CORRAL_LIGHT_CONSULT_CFG` | `~/.config/corral-light/consult-session.json` | The paired session `consult` keeps (0600). |

The default address is local-only by design. If you change `CORRAL_LIGHT_BIND` to expose the server on a network, protect access with your network controls and pairing code.

## Run in the background

```
./corral-light install-service --print   # show the file, write nothing
./corral-light install-service           # write it, and print how to start it
```

This writes a systemd user unit (Linux) or a launchd agent (macOS) with every
path resolved from the checkout that is running — the interpreter, the working
directory, and the log path. It **does not enable and does not start
anything**: writing a file is reversible, and starting a daemon that holds a
port and spawns assistants with your filesystem access is your decision. The
exact enable command is printed for you to run. An existing file is left alone
unless you pass `--force`.

The repository also ships the two files as templates (`corral-light.service`,
`com.cvande.corral-light.plist`) if you would rather edit them by hand.

Do not run the service as root; assistants need the permissions and sign-ins
of the user who starts them. Do not point a service at a worktree —
`spike/node_modules/` is gitignored, so a worktree has neither vendor adapter
and both those lanes go dark in a way that looks like a vendor outage.

The systemd unit uses `KillMode=mixed`: on a stop or restart the hub is signalled first and writes one note in every pane that had a turn running, naming the interrupted message and anything queued behind it. Nothing is re-sent automatically. If an agent process outlives the hub anyway (launchd), the next start stops it before any conversation is resumed.

## Watching the hub

`corral-light watch` checks the hub from outside it: it reads `/health`, judges how long ago the hub last checked its panes, and checks that the hub process is alive. When something is wrong it **pages** — it writes the reason to `~/.local/share/corral-light/DEAD` and shows a desktop notification (`notify-send` on Linux, `osascript` on macOS; never between 21:00 and 05:00, never over the network). It **never restarts** the hub, because a restart ends every running conversation; that decision stays with you. It notifies once per new problem and removes `DEAD` when the hub is healthy again. Exit status: 0 healthy, 2 paged.

Linux, every ten minutes:

```
sed "s|%HERE%|$PWD|" corral-light-watch.service > ~/.config/systemd/user/corral-light-watch.service
cp corral-light-watch.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now corral-light-watch.timer
```

macOS: save as `~/Library/LaunchAgents/com.cvande.corral-light-watch.plist` (fix the two paths), then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.cvande.corral-light-watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>/path/to/corral-light/watch.py</string>
  </array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
```

The hub itself also notifies you — on a permission request or an agent that stopped — when no open, focused browser tab has shown that pane's event within 20 seconds. Same quiet hours, same no-network rule.

## From the command line

Everything the browser does with a pane, a terminal can do too, on every lane — the commands talk to the running server exactly as the browser does, so a pane opened here appears on the wall and keeps its permission rail:

```
./corral-light panes                               # list panes: state, lane, model, waiting cards
./corral-light open --lane grok --cwd ~/src/app    # prints the new pane's id
./corral-light say <pane> "review the diff"        # sends, streams the reply
./corral-light watch <pane>                        # follow a pane live
./corral-light pending <pane>                      # every waiting card, in full, with its digest
./corral-light ok <pane> [n] | no <pane> [n]       # answer a card
./corral-light cancel|pause|resume|close|forget|reopen <pane>
./corral-light rename <pane> <title> · config <pane> <id> <value>
./corral-light attach <pane> <note-id> · quote <from> [<to>]
```

`say` waits for as long as the turn takes. When the pane needs you it prints the whole request — the same bytes the approval's digest covers — and takes `ok`, `no` or `cancel` in the same terminal. It never cancels a turn on a timer; Ctrl-C detaches and the turn keeps running. From a script without a terminal, `ok` requires `--digest <first 12 characters>` of the digest it prints; `no` never does. A pane id can be shortened to any unique prefix.

### Does every lane keep its memory? (evidence)

`python3 lane_matrix.py` opens a pane on every available lane, asks it to remember a random word, pauses and resumes it (a new agent process on the same conversation), asks for the word back, then asks for one shell command under the strict posture and refuses the permission card. Measured 2026-09-29 on a Linux host against a private hub (scratch state dir):

| lane | opens | model | first turn | pause → resume | remembers after resume | permission round-trip | notes |
|---|---|---|---|---|---|---|---|
| claude | yes | opus | yes | yes | yes | asked, refused | |
| grok | yes | grok-4.6 | yes | yes | yes | **no card — ran the command without asking** | posture not enforceable on this lane — the pill read `agent-set` when measured and reads `Grok policy` since DESIGN-5 S2; the probe file was created in the scratch dir and removed |
| gemini | yes | gemini-3.7-flash-high | yes | yes | yes | asked, refused | |
| codex | yes | gpt-5.6-sol | yes | yes | yes | **no card — ran the command without asking** | measured 2026-09-29 07:05 MST with the launcher pointed at a fresh `codex login` (`CORRAL_CODEX_HOME=~/.codex`). Not a Light gap: the pane runs Codex's `agent` mode (workspace-write sandbox, `approval_policy = on-request`), where a command inside the working tree is auto-approved and only an escalation outside the sandbox raises a card. Its ACP `mode` option also offers `read-only`; Light does not map its posture onto it (`posture_via_acp_mode` is false for this lane), so the pill read `agent-set` when measured and reads `ChatGPT policy` since DESIGN-5 S2 (the vendor's own policy applies — which the old label could not say). The probe file was created in the scratch dir and removed |

Not measured: **ollama** (no local Ollama on that host; by design it keeps no context across a restart and now says so in the pane), SSH lanes (a shell has nothing to remember). Re-run the matrix after any adapter upgrade.

## Troubleshooting

Use the command that matches the problem:

```
./corral-light doctor        # Check installation and sign-in requirements
                             # (names the npm step if the adapters are absent)
./corral-light diagnose      # Test a complete assistant conversation
python3 content.py status    # Check search configuration and index status
```

If an assistant works in its normal terminal tool but not in Corral Light:

1. Run `diagnose` and read the complete error output.
2. Confirm that the same operating-system user starts the server and the assistant tool.
3. Check for exported provider credentials in the server’s environment.
4. Sign in again and create a new conversation.

If the browser cannot connect, confirm that the server is running and that the browser uses the configured port.

## Development

Run the test suite with Python’s standard library:

```
python3 -m unittest test_corral_light -v
```

The tests cover installation checks, assistant discovery, routing, saved conversations, approval handling, search, security boundaries, and browser/server API compatibility.

Key files:

- `hub.py` — web server and API
- `sessions.py` — conversation storage and assistant processes
- `corral_core/` — the code this project and its larger sibling must not fork
  (see below). `corral_core/acp.py` is the Agent Client Protocol client and
  the permission rail; `corral_core/test_acp_rail.py` is their contract, run
  by this suite and by the sibling's
- `acp.py` — an alias for `corral_core.acp`, so `import acp` keeps working
- `content.py` — file indexing and search
- `consult.py` — scripted client of the hub (`corral-light consult`): ask a lane, fan out, cross-feed, from a shell or an assistant
- `test_consult.py` — its offline tests (`python3 test_consult.py`)
- `static/` — browser interface
- `test_corral_light.py` — automated tests

This repo is the window. It does not import Seed’s `_lib`, fleet, scheduler, or vault, and it does not add hub routes for `/status`. Contributions should keep the project lightweight, local by default, and explicit about what an assistant can access or do.

### `corral_core` — one copy of the parts that must not drift

Corral Light shares a code core with a larger, private sibling built on the
same rail. The dependency points **one way only**: the sibling imports
`corral_core` from this tree, and nothing in `corral_core` may import from it.
That keeps this project standalone on a machine where the sibling does not
exist, and two tests enforce it (`TheCoreNeverImportsFullCorral`) — one reads
the parsed imports, the other actually imports the core in a clean interpreter
with only this directory on the path.

The rule exists because the alternative was tried. The two projects each kept
their own copy of the ACP client, and on one day in August each copy received
a safety fix the other never saw: the sibling got a three-model review's
fifteen permission-rail fixes, this one got the fix that stops an ambient
`ANTHROPIC_API_KEY` from silently outranking the login you just verified. Nine
days later, five of the ten rail-contract tests still failed here — including
one where the assistant could rename the request you were being asked to
approve, so your click could be recorded against a different action than the
one on your screen. Nobody was careless. There was simply no seam that made
"we fixed it" mean "the version people run is fixed".

So: anything both projects need to agree on — the permission rail above all —
lives in `corral_core`, and its tests run in both suites.
