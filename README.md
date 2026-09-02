# Corral Light

**The window for AIOS.**

A local workspace for the AI coding assistants you already run, side by side, with one permission rail. The floor underneath — schedule, vault, run log, memory — is [AI-OS Seed](https://github.com/cvp1/ai-os-seed). Two repos, one folder.

## Install

You already have Claude Code. Python 3.9+.

One folder: `~/aios`. Seed lives in it. This app looks at it. A second folder is a second brain.

1. Install Seed into `~/aios` (or `--into` a workspace you already have — that folder then *is* `~/aios` for this purpose). See the Seed README. Do not install Seed into this repo.
2. Clone this repo, then:
   ```
   ./corral-light doctor
   ./corral-light serve
   ```
   Open http://127.0.0.1:8098, then in another terminal `./corral-light pair <code>` with the code on screen.
3. New Claude conversation. Working directory = `~/aios`.
4. Done when `/status` answers.

The server runs in the foreground. Data lives at `~/.local/share/corral-light` — not in `~/aios`, and not in this clone. `doctor` lists the assistants that are ready and explains what is missing for the others.

## Supported assistants

Corral Light connects to software installed and signed in on your computer.

| Assistant | What you need | Notes |
| --- | --- | --- |
| Claude Code | Claude Code and its Agent Client Protocol adapter | Supports model and effort selection. |
| ChatGPT (Codex) | The Codex adapter and a Codex login | Uses a separate configuration directory. |
| Grok | The Grok command-line tool and `grok login` | The Grok tool manages its own sign-in. |
| Antigravity (Gemini) | Run `python3 install_antigravity_acp.py --install` | The included installer currently supports Linux x86-64. |
| Ollama | Ollama and at least one downloaded model | Chat only; it cannot edit files or run commands. |

The availability check is intentionally honest: an assistant is marked unavailable when a required program, login, or platform is missing. If an assistant passes that check but fails to answer, run:

```
./corral-light diagnose [assistant]
```

## Search and attach files

Press `⌘K` to search open conversations, archived conversations, notes, and other configured text files.

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

## Security

The server listens only on your computer by default (`127.0.0.1`). To use it from another computer, create an encrypted SSH tunnel:

```
ssh -N -L 8098:127.0.0.1:8098 user@example.com
```

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

The default address is local-only by design. If you change `CORRAL_LIGHT_BIND` to expose the server on a network, protect access with your network controls and pairing code.

## Run in the background

The repository includes service definitions for running Corral Light as your signed-in user:

- **Linux:** `corral-light.service` for systemd user services
- **macOS:** the included launchd plist

Update executable paths, the working directory, and log paths for your installation. Do not run the service as root; assistants need the permissions and sign-ins of the user who starts them.

## Troubleshooting

Use the command that matches the problem:

```
./corral-light doctor        # Check installation and sign-in requirements
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
- `acp.py` — Agent Client Protocol integration
- `content.py` — file indexing and search
- `static/` — browser interface
- `test_corral_light.py` — automated tests

This repo is the window. It does not import Seed’s `_lib`, fleet, scheduler, or vault, and it does not add hub routes for `/status`. Contributions should keep the project lightweight, local by default, and explicit about what an assistant can access or do.
