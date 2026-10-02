You are one of three independent reviewers on a design-and-code panel. The other two are different models from different vendors; you will see their reviews in a second round. The author is a Claude agent working for the operator, who owns this system and makes the final call.

WHAT YOU ARE REVIEWING
A first-release Linux installer for Corral Light — a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini) — together with the AI-OS Seed workspace it sits on. The goal, in the operator's words: an installer "my mom could use", where installing is "as easy as clicking on a link and the next thing is a running corral session", "100% reliable and repeatable", Linux only for now.

Three artifacts are pasted below, in full:
  A. install.sh — the installer (bash).
  B. The new top of README.md — what a first-time user reads.
  C. launch.py — a new hub verb that opens the browser already paired, plus the 20-line browser-side change it relies on.

FIXED CONSTRAINTS (solve within them, do not argue against them):
  C1. Linux only, this release. x86-64 and arm64.
  C2. Native install, not a container. (A container design exists separately and is not in scope.)
  C3. No vendor API keys anywhere. Each assistant signs in with its own tool, as the user.
  C4. The hub must never run as root; assistants run as the user.
  C5. AI-OS Seed's gated writes (CLAUDE.md, mesh bootstrap, memory hooks) go through `install.py --approve`, never a direct write.

Everything you need is in the paste. Do not open files or run commands. If a claim looks wrong but you cannot check it from the text, say "unverifiable from the text" — but you MAY draw on your own platform knowledge (bash, systemd user units, Debian/Ubuntu/Fedora/Arch packaging, npm, Node, curl|bash patterns, each CLI's login flow) and should say when you do.

YOUR JOB — break it, then fix it
1. VERDICT, one line each with the strongest reason, BUILD / RESHAPE / KILL: (a) the overall approach, (b) install.sh, (c) the README top, (d) launch.py and the ?pair= page change.
2. THE MOM TEST. Walk the first run as a non-technical person on a fresh Ubuntu 24.04 desktop with none of these tools installed. List every point where she would be stuck, confused, or asked something she cannot answer. Be concrete: quote the line she sees.
3. BUGS AND RELIABILITY FAILURES, ranked most severe first, at most 12. For each: the exact line or construct, the input or machine state that triggers it, what the user sees, and a FIX. Look hard at: idempotence on a second run; partial failure and resume; the trap/ERR and `set -e` interaction with `||`, pipes and subshells; stdin when piped through `curl | bash`; `/dev/tty`; the tee/exec redirection; PATH for the systemd user service; the sudo/package step; the Seed `--detect` grep; the `claude auth status` check; the `--yes` and `--skip-logins` semantics; arm64; distros where `python3` is 3.8 or where `cron` is not systemd-managed; what happens if port 8098 is taken; what happens with no display.
4. SECURITY: the curl|bash pattern, what is pinned and what is not (Claude Code's installer is not pinned), the pre-approved pairing code in a URL, the PATH edits to ~/.profile and ~/.bashrc, the drop-in Environment=PATH line. For each: real risk or not, and the smallest fix.
5. WHAT IS MISSING for "100% reliable and repeatable": at most 6 items, each with how to add it.
6. TESTS: up to 6 automated checks (shell or Python) that would catch regressions in this installer without a human, and how each would run in CI.
7. ONE SENTENCE: what would most likely make this fail on the operator's mother's machine.

FORMAT: Markdown, under 1,500 words. Start with the four verdict lines. No preamble.

=== A. install.sh ===
```bash
#!/usr/bin/env bash
# Corral Light + AI-OS Seed — the Linux installer.
#
#   curl -fsSL https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
#
# One command. When it finishes, Corral Light is running as a user service,
# your browser is open on it (already paired), AI-OS Seed is installed and
# verified in ~/aios, and each assistant you chose has been offered its own
# sign-in. Run it again any time: every step checks before it changes
# anything, so a second run repairs or updates and never duplicates.
#
# What it pins (so two machines installed a month apart get the same thing):
#   Corral Light     git ref  $CORRAL_LIGHT_REF     (default: master)
#   AI-OS Seed       git tag  $AIOS_SEED_REF        (default: v0.4.9-alpha)
#   Node.js          v24.21.0 LTS, SHA-256 checked, private copy (never touches a system Node)
#   Claude + ChatGPT adapters   spike/package-lock.json in the Corral Light checkout (npm ci)
#   Grok CLI         @xai-official/grok 1.0.46 from npm, into a private prefix
#   Antigravity      the release pinned in install_antigravity_acp.py (SHA-256 checked)
#   Claude Code      Anthropic's own installer, "stable" channel (it updates itself)
#
# Options:
#   --lanes a,b,c     which assistants to set up: claude, codex, grok, gemini
#                     (default: all four). gemini is a 1.5 GB download.
#   --workspace DIR   where AI-OS Seed lives (default: ~/aios)
#   --port N          hub port (default: 8098)
#   --yes             no questions; sensible defaults; sign-ins still run
#   --skip-logins     do not start any sign-in (you can run them later)
#   --no-service      do not install the systemd user service; start the hub
#                     for this session only
#   --no-schedule     (testing) skip Seed's scheduler, memory mesh and hooks
#   --uninstall       stop the service and remove what this script installed
#   --help
#
# Everything it does is logged to ~/.local/share/corral-light/install.log.
# It never runs as root, never touches a system Python or Node, and the only
# step that asks for your password is installing missing distro packages.

set -euo pipefail

INSTALLER_VERSION="1.0.0"

# ── pins ────────────────────────────────────────────────────────────────────
CORRAL_LIGHT_REPO="${CORRAL_LIGHT_REPO:-https://github.com/cvp1/corral-light}"
CORRAL_LIGHT_REF="${CORRAL_LIGHT_REF:-master}"
AIOS_SEED_REPO="${AIOS_SEED_REPO:-https://github.com/cvp1/ai-os-seed}"
AIOS_SEED_REF="${AIOS_SEED_REF:-v0.4.9-alpha}"
NODE_VERSION="v24.21.0"
NODE_SHA256_X64="fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6"
NODE_SHA256_ARM64="6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2"
GROK_PACKAGE="@xai-official/grok"
GROK_VERSION="1.0.46"
CLAUDE_INSTALL_URL="https://claude.ai/install.sh"
CLAUDE_CHANNEL="stable"

# ── places ──────────────────────────────────────────────────────────────────
TOOLS="$HOME/tools"
CL="$TOOLS/corral-light"
SEED="$TOOLS/ai-os-seed"
AIOS="${AIOS_WORKSPACE:-$HOME/aios}"
STATE="${CORRAL_LIGHT_STATE:-$HOME/.local/share/corral-light}"
NODE_DIR="$STATE/node"
TOOLS_PREFIX="$STATE/tools"
BIN="$HOME/.local/bin"
LOG="$STATE/install.log"
RECEIPT="$STATE/install-receipt.json"
UNIT_DIR="$HOME/.config/systemd/user"
DROPIN="$UNIT_DIR/corral-light.service.d/10-installer.conf"
PORT="${CORRAL_LIGHT_PORT:-8098}"

# ── options ─────────────────────────────────────────────────────────────────
LANES="claude,codex,grok,gemini"
YES=0
SKIP_LOGINS=0
NO_SERVICE=0
NO_SCHEDULE=0
UNINSTALL=0

usage() { sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --lanes) LANES="$2"; shift 2 ;;
    --lanes=*) LANES="${1#*=}"; shift ;;
    --workspace) AIOS="$2"; shift 2 ;;
    --workspace=*) AIOS="${1#*=}"; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --yes|-y) YES=1; shift ;;
    --skip-logins) SKIP_LOGINS=1; shift ;;
    --no-service) NO_SERVICE=1; shift ;;
    --no-schedule) NO_SCHEDULE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --version) echo "corral-light installer $INSTALLER_VERSION"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done
case "$AIOS" in /*) ;; *) AIOS="$PWD/$AIOS" ;; esac

# ── output ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi
STEP=0; STEPS=11; CURRENT=""
step() { STEP=$((STEP+1)); CURRENT="$1"; printf '\n%s[%d/%d] %s%s\n' "$B" "$STEP" "$STEPS" "$1" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
skip() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '\n%s✗ %s%s\n' "$R" "$1" "$N" >&2; [ -n "${2:-}" ] && printf '  %s\n' "$2" >&2; exit 1; }

# Questions read from the terminal even when the script itself is piped in.
TTY_IN=/dev/tty
[ -r /dev/tty ] || TTY_IN=/dev/null
ask_yn() {   # ask_yn "question" default(Y|N)  → 0 yes, 1 no
  local q="$1" def="${2:-Y}" ans
  if [ "$YES" = 1 ] || [ "$TTY_IN" = /dev/null ]; then [ "$def" = Y ]; return; fi
  if [ "$def" = Y ]; then printf '  %s [Y/n] ' "$q"; else printf '  %s [y/N] ' "$q"; fi
  read -r ans < "$TTY_IN" || ans=""
  ans="$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')"
  case "$ans" in
    "") [ "$def" = Y ] ;;
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}
press_enter() {
  [ "$YES" = 1 ] && return 0
  [ "$TTY_IN" = /dev/null ] && return 0
  printf '  %s(press Enter to continue, or type s to skip)%s ' "$D" "$N"
  local ans; read -r ans < "$TTY_IN" || ans=""
  [ "$ans" != "s" ] && [ "$ans" != "S" ]
}

# ── logging: everything also goes to the log file ───────────────────────────
mkdir -p "$STATE"
: >> "$LOG"
exec > >(tee -a "$LOG") 2>&1
printf '\n===== corral-light installer %s — %s =====\n' "$INSTALLER_VERSION" "$(date -Is)" >> "$LOG"

on_error() {
  local rc=$?
  printf '\n%s✗ Step failed: %s%s\n' "$R" "${CURRENT:-start}" "$N" >&2
  printf '  The full log is at %s\n' "$LOG" >&2
  printf '  Fix what it names, then run the same command again — it continues where it left off.\n' >&2
  exit "$rc"
}
trap on_error ERR

has_lane() { case ",$LANES," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
have() { command -v "$1" >/dev/null 2>&1; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# ── uninstall ───────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = 1 ]; then
  STEPS=5
  printf '%sThis removes what the installer added. Your sign-ins (~/.claude, ~/.grok, …) and\n%s' "$B" "$N"
  printf 'your workspace content stay unless you say otherwise.%s\n' "$N"
  step "Stopping the service"
  if systemctl --user list-unit-files corral-light.service >/dev/null 2>&1; then
    systemctl --user disable --now corral-light.service 2>/dev/null || true
    systemctl --user disable --now corral-light-watch.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/corral-light.service" "$UNIT_DIR/corral-light-watch.service" "$UNIT_DIR/corral-light-watch.timer"
    rm -rf "$UNIT_DIR/corral-light.service.d"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "service removed"
  else
    skip "no service installed"
  fi
  step "AI-OS Seed"
  if [ -f "$AIOS/.cc-seed/receipt.json" ] && [ -f "$SEED/install.py" ]; then
    if ask_yn "Remove the AI-OS Seed install at $AIOS (de-schedules its jobs; your own files stay)?" N; then
      python3 "$SEED/install.py" --target "$AIOS" --uninstall || warn "Seed uninstall reported a problem — see above"
      systemctl --user disable --now memory-fold.timer memory-home-watch.timer 2>/dev/null || true
      rm -f "$UNIT_DIR"/memory-{fold,home-watch}.{service,timer}
      systemctl --user daemon-reload 2>/dev/null || true
      ok "Seed removed (~/memory-events is yours and was left alone)"
    else
      skip "Seed left in place"
    fi
  else
    skip "no Seed install at $AIOS"
  fi
  step "Private Node, Grok CLI, adapters"
  rm -rf "$NODE_DIR" "$TOOLS_PREFIX" "$BIN/grok"
  ok "removed $NODE_DIR and $TOOLS_PREFIX"
  step "Clones"
  if ask_yn "Remove the source clones $CL and $SEED?" N; then
    rm -rf "$CL" "$SEED"; rm -f "$BIN/corral-light"; ok "removed"
  else
    skip "clones left in place"
  fi
  step "Hub state"
  if ask_yn "Remove saved conversations and hub state at $STATE?" N; then
    rm -rf "$STATE"; ok "removed"
  else
    skip "state left at $STATE"
  fi
  printf '\n%sDone.%s Claude Code itself (~/.local/bin/claude) and each assistant'"'"'s sign-in were not touched.\n' "$B" "$N"
  exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════
printf '%sCorral Light installer%s %s\n' "$B" "$N" "$D$INSTALLER_VERSION$N"
printf 'Assistants: %s   Workspace: %s   Log: %s\n' "$LANES" "$AIOS" "$LOG"

# ── 1. this machine ─────────────────────────────────────────────────────────
step "Checking this machine"
[ "$(uname -s)" = Linux ] || die "This installer is for Linux." "macOS and Windows are not covered by this first release."
[ "$(id -u)" != 0 ] || die "Do not run this as root." "Assistants sign in and work as you. Run it as your normal user; it asks for sudo only if a package is missing."
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) NODE_ARCH=x64; NODE_SHA="$NODE_SHA256_X64" ;;
  aarch64|arm64) NODE_ARCH=arm64; NODE_SHA="$NODE_SHA256_ARM64" ;;
  *) die "Unsupported CPU: $ARCH" "Supported: x86_64 and arm64." ;;
esac
ok "Linux $ARCH"
for l in ${LANES//,/ }; do
  case "$l" in claude|codex|grok|gemini) ;; *) die "Unknown assistant '$l' in --lanes" "Choose from: claude, codex, grok, gemini" ;; esac
done
if [ "$NO_SERVICE" = 0 ]; then
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "no systemd user session here (container, or no login session) — the hub will run for this session only"
    NO_SERVICE=1
  fi
fi
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then ok "a desktop is available (the browser can open)"; else warn "no desktop display — sign-ins will use device codes and the browser URL will be printed"; fi
if ! curl -fsSI --max-time 15 https://github.com >/dev/null 2>&1; then
  die "No internet connection (cannot reach github.com)." "Connect, then run the installer again."
fi
ok "internet reachable"

# ── 2. distro packages ──────────────────────────────────────────────────────
step "Base tools (git, python3, PyYAML, curl, tar, xz, cron)"
missing=()
have git     || missing+=(git)
have curl    || missing+=(curl)
have tar     || missing+=(tar)
have xz      || missing+=(xz)
have python3 || missing+=(python3)
have crontab || missing+=(cron)
if have python3; then python3 -c 'import yaml' 2>/dev/null || missing+=(pyyaml); fi
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && ! have xdg-open; then missing+=(xdg-utils); fi

pkg_names() {  # translate generic names to this distro's packages
  local mgr="$1"; shift
  local out=()
  for m in "$@"; do
    case "$mgr:$m" in
      apt:pyyaml) out+=(python3-yaml) ;;   dnf:pyyaml) out+=(python3-pyyaml) ;;
      pacman:pyyaml) out+=(python-yaml) ;; zypper:pyyaml) out+=(python3-PyYAML) ;;
      apk:pyyaml) out+=(py3-yaml) ;;
      apt:xz) out+=(xz-utils) ;;           pacman:xz) out+=(xz) ;;   *:xz) out+=(xz) ;;
      apt:cron) out+=(cron) ;;             *:cron) out+=(cronie) ;;
      apk:python3) out+=(python3) ;;
      *) out+=("$m") ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}
if [ ${#missing[@]} -gt 0 ]; then
  if have apt-get; then MGR=apt; elif have dnf; then MGR=dnf; elif have pacman; then MGR=pacman; elif have zypper; then MGR=zypper; elif have apk; then MGR=apk; else MGR=""; fi
  [ -n "$MGR" ] || die "Missing: ${missing[*]} — and no known package manager." "Install them with your distro's tool, then run this again."
  mapfile -t pkgs < <(pkg_names "$MGR" "${missing[@]}")
  case "$MGR" in
    apt)    cmd="sudo apt-get install -y ${pkgs[*]}"; pre="sudo apt-get update -qq" ;;
    dnf)    cmd="sudo dnf install -y ${pkgs[*]}"; pre="" ;;
    pacman) cmd="sudo pacman -S --needed --noconfirm ${pkgs[*]}"; pre="" ;;
    zypper) cmd="sudo zypper install -y ${pkgs[*]}"; pre="" ;;
    apk)    cmd="sudo apk add ${pkgs[*]}"; pre="" ;;
  esac
  printf '  Missing: %s\n  Will run: %s\n' "${missing[*]}" "$cmd"
  have sudo || die "sudo is not available, and these packages are missing: ${pkgs[*]}" "Ask an administrator to run: ${cmd#sudo }"
  printf '  %s(your password is for the system package manager, nothing else)%s\n' "$D" "$N"
  [ -z "$pre" ] || $pre < "$TTY_IN"
  $cmd < "$TTY_IN"
  for m in "${missing[@]}"; do
    case "$m" in
      pyyaml) python3 -c 'import yaml' || die "PyYAML still not importable after install" ;;
      cron) have crontab || die "crontab still missing after install" ;;
      xdg-utils) ;;
      *) have "$m" || die "$m still missing after install" ;;
    esac
  done
  ok "installed: ${pkgs[*]}"
else
  ok "all present"
fi
# The scheduler needs cron running. Enabling it is a one-time admin action.
if [ "$NO_SCHEDULE" = 0 ]; then
  cron_unit=""
  for u in cronie cron crond; do
    if systemctl list-unit-files "$u.service" 2>/dev/null | grep -q "^$u.service"; then cron_unit="$u"; break; fi
  done
  if [ -n "$cron_unit" ] && ! systemctl is-active --quiet "$cron_unit"; then
    if have sudo; then
      printf '  cron (%s) is installed but not running; starting it so scheduled jobs run.\n' "$cron_unit"
      sudo systemctl enable --now "$cron_unit" < "$TTY_IN" || warn "could not start $cron_unit — Seed's scheduled jobs will not run until it is"
    else
      warn "cron ($cron_unit) is not running and sudo is unavailable — scheduled jobs will not run until it is"
    fi
  fi
fi
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "python3 is $PYV; 3.9 or newer is required."
ok "python3 $PYV with PyYAML"

# ── 3. the two checkouts ────────────────────────────────────────────────────
step "Fetching Corral Light and AI-OS Seed"
mkdir -p "$TOOLS" "$BIN"
sync_repo() {  # sync_repo <dir> <url> <ref> <label>
  local dir="$1" url="$2" ref="$3" label="$4"
  if [ -d "$dir/.git" ]; then
    if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
      warn "$label at $dir has local changes — left exactly as it is (not updated)"
      return 0
    fi
    git -C "$dir" fetch -q --tags origin
    if git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref"; then
      git -C "$dir" checkout -q -B "$ref" "origin/$ref"
    else
      git -C "$dir" checkout -q --detach "$ref"
    fi
    ok "$label updated to $ref ($(git -C "$dir" rev-parse --short HEAD))"
  elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    die "$dir exists and is not a git checkout." "Move it aside, then run the installer again."
  else
    git clone -q "$url" "$dir"
    if git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref"; then
      git -C "$dir" checkout -q -B "$ref" "origin/$ref"
    else
      git -C "$dir" checkout -q --detach "$ref"
    fi
    ok "$label cloned at $ref ($(git -C "$dir" rev-parse --short HEAD))"
  fi
}
sync_repo "$CL" "$CORRAL_LIGHT_REPO" "$CORRAL_LIGHT_REF" "Corral Light"
sync_repo "$SEED" "$AIOS_SEED_REPO" "$AIOS_SEED_REF" "AI-OS Seed"
[ -f "$CL/hub.py" ] || die "$CL does not look like Corral Light (no hub.py)."
[ -f "$SEED/install.py" ] || die "$SEED does not look like AI-OS Seed (no install.py)."

# the command
cat > "$BIN/corral-light" <<EOF
#!/bin/bash
exec "$CL/corral-light" "\$@"
EOF
chmod +x "$BIN/corral-light"
ok "command: $BIN/corral-light"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    line='export PATH="$HOME/.local/bin:$PATH"'
    added=0
    for rc in "$HOME/.profile" "$HOME/.bashrc"; do
      if ! grep -qsF "$line" "$rc"; then printf '\n# added by the Corral Light installer\n%s\n' "$line" >> "$rc"; added=1; fi
    done
    export PATH="$BIN:$PATH"
    if [ "$added" = 1 ]; then ok "added ~/.local/bin to PATH (in ~/.profile and ~/.bashrc; new terminals pick it up)"
    else skip "~/.local/bin is already in ~/.profile and ~/.bashrc (this shell just has not reloaded them)"; fi
    ;;
esac

# ── 4. private Node.js ──────────────────────────────────────────────────────
step "Node.js $NODE_VERSION (private copy for the adapters)"
if [ -x "$NODE_DIR/bin/node" ] && [ "$("$NODE_DIR/bin/node" --version)" = "$NODE_VERSION" ]; then
  skip "already present"
else
  tarball="node-$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
  dl="$STATE/node.download"       # beside the destination, not /tmp
  rm -rf "$dl"; mkdir -p "$dl"
  curl -fsSL --retry 3 -o "$dl/$tarball" "https://nodejs.org/dist/$NODE_VERSION/$tarball"
  got="$(sha256_of "$dl/$tarball")"
  [ "$got" = "$NODE_SHA" ] || die "Node download checksum mismatch" "expected $NODE_SHA, got $got — the download was corrupted or altered; run again."
  tar -xJf "$dl/$tarball" -C "$dl"
  rm -rf "$NODE_DIR"
  mv "$dl/node-$NODE_VERSION-linux-$NODE_ARCH" "$NODE_DIR"
  rm -rf "$dl"
  ok "installed at $NODE_DIR (checksum verified)"
fi
export PATH="$NODE_DIR/bin:$PATH"
export CORRAL_NODE_BIN="$NODE_DIR/bin"
export NPM_CONFIG_UPDATE_NOTIFIER=false   # no "new npm available" banners in an install log

# ── 5. adapters ─────────────────────────────────────────────────────────────
step "Claude and ChatGPT adapters (npm ci, from the lock file)"
if [ -f "$CL/spike/package-lock.json" ]; then
  (cd "$CL/spike" && npm ci --no-audit --no-fund --loglevel=error)
else
  (cd "$CL/spike" && npm install --no-audit --no-fund --loglevel=error)
fi
[ -x "$CL/spike/node_modules/.bin/claude-agent-acp" ] || die "claude-agent-acp did not install" "see $LOG"
[ -x "$CL/spike/node_modules/.bin/codex-acp" ] || die "codex-acp did not install" "see $LOG"
ok "adapters installed"

# ── 6. assistants' own programs ─────────────────────────────────────────────
step "Assistant programs"
if has_lane claude; then
  if [ -x "$BIN/claude" ] || have claude; then
    skip "Claude Code already installed ($(command -v claude || echo "$BIN/claude"))"
  else
    printf '  Installing Claude Code with Anthropic'"'"'s installer (%s, %s channel)…\n' "$CLAUDE_INSTALL_URL" "$CLAUDE_CHANNEL"
    curl -fsSL --retry 3 "$CLAUDE_INSTALL_URL" | bash -s "$CLAUDE_CHANNEL"
    [ -x "$BIN/claude" ] || have claude || die "Claude Code did not install" "see $LOG"
    ok "Claude Code installed"
  fi
fi
if has_lane grok; then
  if [ -x "$TOOLS_PREFIX/bin/grok" ] && [ "$("$TOOLS_PREFIX/bin/grok" --version 2>/dev/null | awk '{print $2}')" = "$GROK_VERSION" ]; then
    skip "Grok CLI $GROK_VERSION already present"
  else
    mkdir -p "$TOOLS_PREFIX"
    npm install -g --prefix "$TOOLS_PREFIX" --no-audit --no-fund --loglevel=error "$GROK_PACKAGE@$GROK_VERSION"
    [ -x "$TOOLS_PREFIX/bin/grok" ] || die "Grok CLI did not install" "see $LOG"
    ok "Grok CLI $GROK_VERSION installed"
  fi
  cat > "$BIN/grok" <<EOF
#!/bin/sh
# Grok CLI, installed by the Corral Light installer with its private Node.
export PATH="$NODE_DIR/bin:\$PATH"
exec "$TOOLS_PREFIX/bin/grok" "\$@"
EOF
  chmod +x "$BIN/grok"
fi
if has_lane codex; then
  ok "ChatGPT (Codex) uses the CLI bundled with its adapter — nothing more to install"
fi
if has_lane gemini; then
  if python3 "$CL/install_antigravity_acp.py" --check >/dev/null 2>&1; then
    skip "Antigravity (Gemini) runtime already present"
  else
    printf '  Downloading the Antigravity runtime (about 1.5 GB, checksum verified)…\n'
    python3 "$CL/install_antigravity_acp.py" --install
    ok "Antigravity (Gemini) runtime installed"
  fi
fi

# ── 7. AI-OS Seed in the workspace ──────────────────────────────────────────
step "AI-OS Seed in $AIOS"
if [ -f "$AIOS/.cc-seed/receipt.json" ]; then
  skip "already installed (receipt present) — left as it is; to update, see $SEED/README.md"
else
  # Never two installs on one machine: the scheduler owns one managed block.
  detect="$(python3 "$SEED/install.py" --detect 2>&1 || true)"
  other="$(printf '%s\n' "$detect" | grep -E 'INSTALL$|install root' | grep -v -F "$AIOS" || true)"
  if [ -n "$other" ]; then
    printf '%s\n' "$detect"
    die "Another AI-OS Seed install exists on this machine (above)." "One machine supports one install. Use --workspace to point at it, or remove it first (see AGENT-INSTALL.md Phase 0)."
  fi
  if [ -e "$AIOS" ] && [ -n "$(ls -A "$AIOS" 2>/dev/null)" ]; then
    if [ -f "$AIOS/CLAUDE.md" ] || [ -d "$AIOS/.claude" ]; then
      printf '  %s already has content; the seed will join it (--into) and touch none of your files.\n' "$AIOS"
      python3 "$SEED/install.py" --target "$AIOS" --into
    else
      die "$AIOS exists and is not empty." "Choose an empty folder with --workspace DIR, or move its contents aside."
    fi
  else
    python3 "$SEED/install.py" --target "$AIOS"
  fi
  ok "installed"

  printf '  Verifying…\n'
  python3 "$AIOS/_lib/selftest.py"
  python3 "$AIOS/session-brief/session_brief.py" selftest
  python3 "$AIOS/observability/log_run.py" --job hello_fleet -- python3 "$AIOS/demo/hello_fleet.py"
  python3 "$AIOS/observability/log_run.py" --job repo_hygiene -- python3 "$AIOS/observability/repo_hygiene.py" --root "$AIOS" --findings-exit0
  python3 "$AIOS/observability/report.py" --job hello_fleet
  python3 "$AIOS/observability/freshness.py" --all
  ok "selftests pass; the demo job ran once through the real run logger"

  # CLAUDE.md: staged, then approved — the approval is what moves the bytes.
  if ! grep -qs "cc-seed:start" "$AIOS/CLAUDE.md" 2>/dev/null; then
    mkdir -p "$AIOS/.cc-seed/staged"
    cat > "$AIOS/.cc-seed/staged/claude-md.proposed" <<EOF
# AI-OS — this workspace

This is an AI-OS workspace at \`$AIOS\`, installed from AI-OS Seed
$AIOS_SEED_REF by the Corral Light installer on $(date +%Y-%m-%d). The source
clone lives at \`$SEED\` and is never edited in place.

## What's here
- \`scheduler/\` — jobs in \`manifest.yml\`, synced to cron by \`scheduler/sync.sh\`.
- \`observability/\` — \`log_run.py\` wraps every job run into \`runs.db\`;
  \`report.py\` shows history; \`freshness.py --all\` flags anything gone quiet.
- \`keyvault/\` — secrets live here, never in transcripts or code.
- \`memory/\` + \`memory-mesh/\` — durable memory and the event log beneath it.
- \`session-brief/\`, \`friction-miner/\`, \`mcp-guard/\`, \`views/\`, \`demo/\`.
- Skills in \`.claude/skills/\`: /status, /recall, /capture, /improve, /freeze, /skill-center.

Read \`PRINCIPLES.md\` first, then each component's \`README.md\` before changing it.

## Machine notes
- Linux. Scheduled jobs run from cron; if this machine sleeps, runs that
  fall during sleep are skipped, so a stale freshness line may mean
  "asleep", not "broken". Check before fixing.
- No first job chosen yet; \`demo/hello_fleet.py\` is a placeholder.

## Asking another model
Second opinions go through \`corral-light consult\` (the window's lanes, on
this user's own subscription logins) — never a vendor API key by default.
\`corral-light\` is installed (clone at \`$CL\`, command in \`~/.local/bin\`);
\`consult\` needs the hub running (\`systemctl --user status corral-light\`)
and \`corral-light doctor\` shows which lanes are live. If a lane is down, do
not fall back to an API key: that silently changes which vendors see this
user's data. Ask first.
EOF
    python3 "$SEED/install.py" --target "$AIOS" --approve claude-md
    ok "CLAUDE.md written (staged, then approved)"
  fi

  if [ "$NO_SCHEDULE" = 0 ]; then
    python3 "$SEED/install.py" --target "$AIOS" --approve mesh-bootstrap
    ok "memory mesh started (event log at ~/memory-events, fold every 5 minutes)"
    printf '\n  %sMemory hooks.%s Without them, memory is written but never read back into a\n' "$B" "$N"
    printf '  conversation. Wiring them edits Claude Code'"'"'s settings.json; the exact change\n'
    printf '  is printed below and can be undone with: install.py --revoke memory-hooks\n'
    if ask_yn "Wire the memory hooks now?" Y; then
      CI=true python3 "$SEED/install.py" --target "$AIOS" --approve memory-hooks --apply
      python3 "$SEED/install.py" --target "$AIOS" --contract || warn "the memory contract reported a problem — see above"
      ok "memory hooks wired and proven"
    else
      warn "memory hooks not wired — run later: python3 $SEED/install.py --target $AIOS --approve memory-hooks"
    fi
    bash "$AIOS/scheduler/sync.sh"
    ok "scheduler synced to cron (crontab -l shows the cc-seed block)"
  else
    warn "--no-schedule: scheduler, memory mesh and hooks skipped"
  fi
  if [ "$NO_SCHEDULE" = 0 ]; then
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || die "Seed's post-install audit flagged a difference (above)."
    ok "post-install audit clean"
  else
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || warn "audit FLAGGED — expected under --no-schedule (the scheduler was not synced)"
  fi
fi

# ── 8. service ──────────────────────────────────────────────────────────────
step "Running Corral Light"
SERVICE_PATH="$BIN:$NODE_DIR/bin:/usr/local/bin:/usr/bin:/bin"
if [ "$NO_SERVICE" = 0 ]; then
  if [ ! -f "$UNIT_DIR/corral-light.service" ]; then
    "$CL/corral-light" install-service --port "$PORT" >/dev/null
    ok "wrote $UNIT_DIR/corral-light.service"
  else
    skip "service file exists — left as it is"
  fi
  mkdir -p "$(dirname "$DROPIN")"
  cat > "$DROPIN" <<EOF
# Written by the Corral Light installer. The hub needs the private Node and
# ~/.local/bin on its PATH; a user service does not inherit your shell's.
[Service]
Environment=PATH=$SERVICE_PATH
Environment=CORRAL_NODE_BIN=$NODE_DIR/bin
Environment=CORRAL_LIGHT_PORT=$PORT
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now corral-light.service >/dev/null 2>&1 || systemctl --user restart corral-light.service
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null && ok "service survives logout (linger enabled)" || warn "could not enable linger; the hub stops when you log out (sudo loginctl enable-linger $USER fixes that)"
  fi
  if [ -f "$CL/corral-light-watch.timer" ] && [ ! -f "$UNIT_DIR/corral-light-watch.timer" ]; then
    sed "s|%HERE%|$CL|" "$CL/corral-light-watch.service" > "$UNIT_DIR/corral-light-watch.service"
    cp "$CL/corral-light-watch.timer" "$UNIT_DIR/"
    systemctl --user daemon-reload
    systemctl --user enable --now corral-light-watch.timer >/dev/null 2>&1 || true
    ok "watchdog timer enabled (pages you if the hub goes down; never restarts it)"
  fi
else
  if curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    skip "a hub already answers on port $PORT"
  else
    ( cd "$CL" && PATH="$SERVICE_PATH" CORRAL_LIGHT_PORT="$PORT" nohup "$CL/corral-light" serve >> "$STATE/hub.log" 2>&1 < /dev/null & )
    ok "hub started for this session (log: $STATE/hub.log)"
  fi
fi
for _ in $(seq 1 60); do
  curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || die "The hub did not answer on port $PORT within 60 s." "journalctl --user -u corral-light -n 50   shows why."
ok "hub answering at http://127.0.0.1:$PORT/"

# ── 9. sign-ins ─────────────────────────────────────────────────────────────
step "Signing in to each assistant"
HEADLESS=0; [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && HEADLESS=1
if [ "$SKIP_LOGINS" = 1 ]; then
  warn "--skip-logins: nothing started. Later: claude auth login · grok login · codex login (see README)"
else
  if has_lane claude; then
    if "$BIN/claude" auth status 2>/dev/null | grep -q '"loggedIn": true' || claude auth status 2>/dev/null | grep -q '"loggedIn": true'; then
      ok "Claude: already signed in"
    else
      printf '  %sClaude:%s your browser will open to sign in with your Claude account (Pro or Max).\n' "$B" "$N"
      if press_enter; then
        ( "$BIN/claude" auth login < "$TTY_IN" || claude auth login < "$TTY_IN" ) && ok "Claude: signed in" || warn "Claude sign-in did not finish — later: claude auth login"
      else skip "Claude sign-in skipped"; fi
    fi
  fi
  if has_lane grok; then
    if [ -s "$HOME/.grok/auth.json" ]; then
      ok "Grok: already signed in"
    else
      printf '  %sGrok:%s sign in with your X / Grok account.\n' "$B" "$N"
      if press_enter; then
        if [ "$HEADLESS" = 1 ]; then "$BIN/grok" login --device-auth < "$TTY_IN"; else "$BIN/grok" login < "$TTY_IN"; fi \
          && ok "Grok: signed in" || warn "Grok sign-in did not finish — later: grok login"
      else skip "Grok sign-in skipped"; fi
    fi
  fi
  if has_lane codex; then
    CODEX_HOME_DIR="$HOME/.config/corral-light/codex-home"
    if [ -s "$CODEX_HOME_DIR/auth.json" ]; then
      ok "ChatGPT: already signed in"
    else
      printf '  %sChatGPT:%s sign in with your ChatGPT account (Plus, Pro or Team).\n' "$B" "$N"
      if press_enter; then
        mkdir -p "$CODEX_HOME_DIR"; chmod 700 "$CODEX_HOME_DIR"
        if [ "$HEADLESS" = 1 ]; then
          CODEX_HOME="$CODEX_HOME_DIR" "$CL/spike/node_modules/.bin/codex" login --device-auth < "$TTY_IN"
        else
          CODEX_HOME="$CODEX_HOME_DIR" "$CL/spike/node_modules/.bin/codex" login < "$TTY_IN"
        fi && ok "ChatGPT: signed in" || warn "ChatGPT sign-in did not finish — later: CODEX_HOME=$CODEX_HOME_DIR $CL/spike/node_modules/.bin/codex login"
      else skip "ChatGPT sign-in skipped"; fi
    fi
  fi
  if has_lane gemini; then
    ok "Gemini: signs in with your Google account the first time you open a Gemini conversation"
  fi
fi

# ── 10. open the wall ───────────────────────────────────────────────────────
step "Opening Corral Light"
LAUNCH_OUT="$(CORRAL_LIGHT_PORT="$PORT" "$CL/corral-light" launch 2>&1 || true)"
printf '  %s\n' "$LAUNCH_OUT"

# ── 11. receipt + what you have ─────────────────────────────────────────────
step "Done"
python3 - "$RECEIPT" "$INSTALLER_VERSION" "$CL" "$SEED" "$AIOS" "$NODE_VERSION" "$LANES" "$PORT" <<'PY'
import json, subprocess, sys, datetime
rc, ver, cl, seed, aios, node, lanes, port = sys.argv[1:9]
def rev(d):
    try: return subprocess.check_output(["git", "-C", d, "rev-parse", "HEAD"], text=True).strip()
    except Exception: return None
json.dump({"installer": ver, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "corral_light": {"path": cl, "commit": rev(cl)}, "ai_os_seed": {"path": seed, "commit": rev(seed)},
           "workspace": aios, "node": node, "lanes": lanes.split(","), "port": int(port)},
          open(rc, "w"), indent=1)
PY
ok "receipt: $RECEIPT"
printf '\n'
"$CL/corral-light" doctor || true
printf '\n%sYou are set up.%s\n' "$B" "$N"
printf '  The wall:        http://127.0.0.1:%s/   (any time: %scorral-light launch%s)\n' "$PORT" "$B" "$N"
printf '  Your workspace:  %s\n' "$AIOS"
printf '  First thing to try: open a terminal, run  %scd %s && claude%s  and type  %s/status%s\n' "$B" "$AIOS" "$N" "$B" "$N"
printf '  Health:          corral-light doctor   ·   Log: %s\n' "$LOG"
printf '  Run this installer again any time to update; --uninstall removes it.\n\n'
```

=== B. README.md (top, down to the Reference index) ===
# Corral Light

**The window for AIOS.**

A local workspace for the AI coding assistants you already pay for — Claude,
ChatGPT, Grok, Gemini — side by side in one browser tab, with one permission
rail: every file write and every command is shown to you before it runs. The
floor underneath — schedule, vault, run log, memory — is
[AI-OS Seed](https://github.com/cvp1/ai-os-seed). Two repos, one folder.

Nothing leaves your computer except what each assistant sends to its own
vendor, on the account you signed into. No API keys. No cloud in the middle.

---

## Install on Linux — one line

Open a terminal and paste this:

```
curl -fsSL https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
```

That is the whole install. About five minutes on a normal connection (twenty
with Gemini, which is a 1.5 GB download). When it finishes, your browser is
open on Corral Light, already signed in to it, and you can start typing.

**What you need**

| | |
|---|---|
| Computer | Linux on x86-64 or arm64 — Ubuntu, Debian, Fedora, Arch, openSUSE, Alpine. A desktop, so a browser can open. |
| Accounts | At least one of: Claude (Pro or Max), ChatGPT (Plus, Pro or Team), Grok (SuperGrok or X Premium), a Google account for Gemini. The installer asks you to sign in to each one you chose; you can skip any of them and come back later. |
| Disk | About 600 MB; 2.1 GB with Gemini. |
| Your password | Only if a basic tool is missing (`git`, `python3`, `curl`, `cron`). The installer names what it will install and runs your distro's own package manager, nothing else. |

**What the installer does, in order**

| Step | What it checks, then does |
|---|---|
| 1 | Confirms Linux, your CPU, a desktop and an internet connection. Refuses to run as root. |
| 2 | Installs any missing base tools with your package manager, after showing you the command. |
| 3 | Clones this repo to `~/tools/corral-light` and AI-OS Seed to `~/tools/ai-os-seed`, each at a pinned version. Puts the `corral-light` command on your PATH. |
| 4 | Downloads a private copy of Node.js (checksum verified) into `~/.local/share/corral-light/node`. Your system's Node, if any, is not touched. |
| 5 | Installs the Claude and ChatGPT adapters from the lock file (`npm ci`). |
| 6 | Installs Claude Code (Anthropic's own installer), the Grok CLI (pinned, into a private prefix) and the Antigravity runtime for Gemini (pinned, checksum verified). |
| 7 | Installs AI-OS Seed into `~/aios`, runs its selftests and its demo job through the real run logger, writes your `CLAUDE.md` through Seed's stage-then-approve gate, starts the memory mesh, offers to wire the memory hooks (one yes/no), and syncs the scheduler to cron. Then runs Seed's post-install audit. |
| 8 | Installs Corral Light as a **user** service (systemd), starts it, enables it to survive logout, and enables the watchdog timer. Waits until the hub answers. |
| 9 | Offers each assistant's own sign-in, one at a time. Each opens your browser (or prints a device code if there is no display). |
| 10 | Opens Corral Light in your browser, already paired (`corral-light launch`). |
| 11 | Writes a receipt and prints `corral-light doctor`. |

Every step checks before it changes anything, so **running the same line
again is safe**: it updates what moved and repairs what is missing, and never
installs twice. Everything it prints also goes to
`~/.local/share/corral-light/install.log`.

**Choosing what to install**

The one-liner installs all four assistants. To choose, download the script
and pass options:

```
curl -fsSLO https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh
bash install.sh --lanes claude,grok          # just these two
bash install.sh --workspace ~/work/aios      # Seed somewhere other than ~/aios
bash install.sh --skip-logins                # sign in later (see below)
bash install.sh --yes                        # no questions
bash install.sh --help                       # every option, and every version it pins
```

**If something goes wrong**

The installer stops at the step that failed, says what it was, and points at
the log. Fix what it names and run the same line again. After that:

```
corral-light doctor              # which assistants are ready, and why not
corral-light diagnose claude     # one full conversation, with every error shown
journalctl --user -u corral-light -n 50   # the hub's own log
```

**Updating** — run the install line again. **Removing it** — `bash install.sh --uninstall`
asks before each thing it removes; your sign-ins and your files are never
part of it.

### Your first five minutes

1. The browser tab that opened is **the wall**. Click **New**, pick an
   assistant, choose a folder (the default is `~/aios`), and type.
2. When an assistant wants to write a file or run a command, the pane pauses
   and shows you exactly what — bytes and a digest. **Approve** or **Refuse**.
   Nothing happens until you do.
3. Open a terminal, run `cd ~/aios && claude`, and type `/status`. That is
   the floor answering: every scheduled job, its last run, and anything that
   has gone quiet.
4. Closed the tab? `corral-light launch` opens it again, paired. The hub keeps
   running in the background; `systemctl --user status corral-light` shows it.

### Signing in later

Each assistant signs in with its own tool, as you, and keeps its own
credential. Corral Light never sees a password or a token.

| Assistant | Command |
|---|---|
| Claude | `claude auth login` |
| Grok | `grok login` (or `grok login --device-auth` with no display) |
| ChatGPT | `CODEX_HOME=~/.config/corral-light/codex-home ~/tools/corral-light/spike/node_modules/.bin/codex login` |
| Gemini | Open a Gemini conversation on the wall; the Google sign-in opens the first time. |

`corral-light doctor` tells you which ones are done.

### Install by hand (any Linux, or macOS)

You already have Claude Code. Python 3.9+. Node.js 20+.

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
   `doctor` names the step if the directory is missing.
   The other three lanes (Grok, Antigravity, Ollama) resolve their programs
   outside this tree and are unaffected.
   Then either `./corral-light launch` (opens the browser, paired), or open
   http://127.0.0.1:8098 and in another terminal run `./corral-light pair <code>`
   with the code on screen.
3. New Claude conversation. Working directory = `~/aios`.
4. Done when `/status` answers.

The server runs in the foreground. Data lives at `~/.local/share/corral-light` — not in `~/aios`, and not in this clone. `doctor` lists the assistants that are ready and explains what is missing for the others.

---

## Reference

=== C. launch.py ===
```python
#!/usr/bin/env python3
"""corral-light launch — open the wall in your browser, already paired.

    corral-light launch            mint a code, approve it, open the browser
    corral-light launch --print    print the URL and open nothing

Pairing normally takes two steps: the browser shows a code, and you type
`corral-light pair <code>` in a shell. Both halves prove the same thing —
that you own this UNIX account — so a command that runs as that account can
do both halves itself: mint the code, approve it, and hand it to the browser
in the URL (`/?pair=<code>`). The page claims the code once and drops it from
the address bar. The code is single-use and expires in five minutes.

What this does not change: a browser on another machine still needs
`corral-light pair`, and nothing here reads or relays a session cookie — the
browser gets its cookie from the hub, as always.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import auth  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8098"


def hub_url(env=None):
    """Where the hub is: CORRAL_LIGHT_URL, else bind/port, else the default."""
    env = os.environ if env is None else env
    explicit = (env.get("CORRAL_LIGHT_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    bind = (env.get("CORRAL_LIGHT_BIND") or "127.0.0.1").strip()
    port = (env.get("CORRAL_LIGHT_PORT") or "8098").strip()
    if bind in ("0.0.0.0", "", "::"):
        bind = "127.0.0.1"
    return f"http://{bind}:{port}"


def hub_alive(base, timeout=3.0):
    """True when /health answers. Never raises."""
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def paired_url(base):
    """A URL that pairs the browser that opens it: the code is minted and
    approved here, by the account that owns the hub."""
    code, _ttl = auth.new_code()
    ok, msg = auth.approve(code)
    if not ok:
        raise RuntimeError(msg)
    return f"{base}/?pair={code}"


def opener(platform=None, env=None):
    """The command that opens a URL on this machine, or None."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "darwin":
        return [shutil.which("open")] if shutil.which("open") else None
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return None
    for name in ("xdg-open", "gio"):
        found = shutil.which(name)
        if found:
            return [found, "open"] if name == "gio" else [found]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="corral-light launch",
        description="Open Corral Light in your browser, already paired.")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the paired URL and open nothing")
    ap.add_argument("--url", default=None,
                    help=f"hub address (default: CORRAL_LIGHT_URL or {DEFAULT_URL})")
    a = ap.parse_args(argv)
    base = (a.url or hub_url()).rstrip("/")

    if not hub_alive(base):
        print(f"Corral Light is not answering at {base}.\n"
              f"Start it with one of:\n"
              f"  systemctl --user start corral-light     # if installed as a service\n"
              f"  corral-light serve                      # in this terminal\n"
              f"then run `corral-light launch` again.", file=sys.stderr, flush=True)
        return 2

    try:
        url = paired_url(base)
    except auth.TooMany as e:
        print(f"could not mint a pairing code: {e}", file=sys.stderr, flush=True)
        return 3

    if a.show:
        print(url, flush=True)
        return 0

    cmd = opener()
    if not cmd:
        print("No browser opener found (no display, or no xdg-open).\n"
              "Open this address yourself within five minutes:\n"
              f"  {url}", flush=True)
        return 0
    try:
        # Detached: a browser that inherits this terminal would hold it open.
        subprocess.Popen(cmd + [url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        print(f"could not start {cmd[0]}: {e}\nOpen this address yourself:\n  {url}",
              flush=True)
        return 0
    print(f"opening {base}/ in your browser (paired)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

=== C (continued). static/app.js — the pairing change ===
```js
function presetPairCode() {
  let u;
  try { u = new URL(location.href); } catch (e) { return null; }
  const raw = (u.searchParams.get('pair') || '').trim().toUpperCase();
  if (!raw) return null;
  u.searchParams.delete('pair');
  try { history.replaceState(null, '', u.pathname + u.search + u.hash); } catch (e) { }
  return /^[A-Z0-9]{3}-[A-Z0-9]{3}$/.test(raw) ? raw : null;
}

async function pair() {
  pairTimers.forEach(clearInterval); pairTimers = [];
  $('#pair').classList.remove('hide');
  let code, ttl, how;
  // `corral-light launch` opens /?pair=<code> with a code it already approved
  // (same proof as typing `corral-light pair`: the account owns the hub). Use
  // that code once and drop it from the address bar; an unknown or expired
  // code claims as 'expired' and falls through to a fresh one below.
  const preset = presetPairCode();
  if (preset) {
    code = preset; ttl = 300; how = `corral-light pair ${code}`;
  } else {
    try { ({ code, ttl, how } = await api('/api/pair/new')); }
    catch (e) { $('#pairnote').textContent = 'Cannot reach Corral Light: ' + e.message; return; }
  }
  $('#paircode').textContent = code;
```
