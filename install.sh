#!/usr/bin/env bash
# Corral Light + AI-OS Seed — the Linux installer.
#
#   curl -fsSL https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
#   wget -qO-  https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
#
# One command. It asks two questions at the start (which assistants you have
# an account for, and whether Claude may keep memory between conversations),
# then works on its own. When it finishes, Corral Light is running as a user
# service, your browser is open on it (already paired), AI-OS Seed is
# installed and verified in ~/aios, and each assistant you chose has been
# offered its own sign-in. Run it again any time: every step checks before it
# changes anything, so a second run repairs or updates and never duplicates.
#
# What it pins (so two machines installed a month apart get the same thing):
#   Corral Light     git ref  $CORRAL_LIGHT_REF  (default: master; set a commit for an exact repeat —
#                    the commit actually installed is written to the receipt)
#   AI-OS Seed       git tag  $AIOS_SEED_REF     (default: v0.4.9-alpha)
#   Node.js          v24.21.0 LTS, SHA-256 checked, private copy (never touches a system Node)
#   Claude + ChatGPT adapters   spike/package-lock.json in the Corral Light checkout (npm ci)
#   Grok CLI         @xai-official/grok 1.0.46 from npm, into a private prefix
#   Antigravity      the release pinned in install_antigravity_acp.py (SHA-256 checked)
#   Claude Code      2.1.285 through Anthropic's own installer, which verifies the binary's
#                    SHA-256; Claude Code then keeps itself current (Anthropic's policy, not ours)
#
# Options:
#   --lanes a,b,c     which assistants to set up: claude, codex, grok, gemini
#                     (default: ask, Enter = claude; all four with --yes). gemini is a 1.5 GB download.
#   --workspace DIR   where AI-OS Seed lives (default: ~/aios)
#   --port N          hub port (default: 8098)
#   --yes             no questions: all four assistants, memory on; sign-ins still run
#   --skip-logins     do not start any sign-in (you can run them later)
#   --no-service      do not install the systemd user service; start the hub
#                     for this session only
#   --no-schedule     (testing) skip Seed's scheduler, memory mesh and hooks
#   --uninstall       stop the service and remove what this script installed
#   --help
#
# Everything it prints also goes to ~/.local/share/corral-light/install.log.
# It never runs as root, never touches a system Python or Node, and the only
# step that asks for your password is installing missing distro packages.

set -Eeuo pipefail

INSTALLER_VERSION="1.1.4"

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
CLAUDE_VERSION="2.1.285"

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
CODEX_HOME_DIR="$HOME/.config/corral-light/codex-home"
PORT="${CORRAL_LIGHT_PORT:-8098}"

# ── options ─────────────────────────────────────────────────────────────────
LANES=""
YES=0
SKIP_LOGINS=0
NO_SERVICE=0
NO_SCHEDULE=0
UNINSTALL=0

usage() { cat <<'USAGE'
Corral Light + AI-OS Seed — the Linux installer.

  curl -fsSL https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
  wget -qO-  https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash

One command. It asks two questions at the start (which assistants you have
an account for, and whether Claude may keep memory between conversations),
then works on its own. When it finishes, Corral Light is running as a user
service, your browser is open on it (already paired), AI-OS Seed is
installed and verified in ~/aios, and each assistant you chose has been
offered its own sign-in. Run it again any time: every step checks before it
changes anything, so a second run repairs or updates and never duplicates.

What it pins (so two machines installed a month apart get the same thing):
  Corral Light     git ref  $CORRAL_LIGHT_REF  (default: master; set a commit for an exact repeat —
                   the commit actually installed is written to the receipt)
  AI-OS Seed       git tag  $AIOS_SEED_REF     (default: v0.4.9-alpha)
  Node.js          v24.21.0 LTS, SHA-256 checked, private copy (never touches a system Node)
  Claude + ChatGPT adapters   spike/package-lock.json in the Corral Light checkout (npm ci)
  Grok CLI         @xai-official/grok 1.0.46 from npm, into a private prefix
  Antigravity      the release pinned in install_antigravity_acp.py (SHA-256 checked)
  Claude Code      2.1.285 through Anthropic's own installer, which verifies the binary's
                   SHA-256; Claude Code then keeps itself current (Anthropic's policy, not ours)

Options:
  --lanes a,b,c     which assistants to set up: claude, codex, grok, gemini
                    (default: ask, Enter = claude; all four with --yes). gemini is a 1.5 GB download.
  --workspace DIR   where AI-OS Seed lives (default: ~/aios)
  --port N          hub port (default: 8098)
  --yes             no questions: all four assistants, memory on; sign-ins still run
  --skip-logins     do not start any sign-in (you can run them later)
  --no-service      do not install the systemd user service; start the hub
                    for this session only
  --no-schedule     (testing) skip Seed's scheduler, memory mesh and hooks
  --uninstall       stop the service and remove what this script installed
  --help

Everything it prints also goes to ~/.local/share/corral-light/install.log.
It never runs as root, never touches a system Python or Node, and the only
step that asks for your password is installing missing distro packages.
USAGE
}
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "option $1 needs a value (try --help)" >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --lanes) need_value "$@"; LANES="$2"; shift 2 ;;
    --lanes=*) LANES="${1#*=}"; shift ;;
    --workspace) need_value "$@"; AIOS="$2"; shift 2 ;;
    --workspace=*) AIOS="${1#*=}"; shift ;;
    --port) need_value "$@"; PORT="$2"; shift 2 ;;
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
case "$PORT" in ''|*[!0-9]*) echo "--port must be a number (got '$PORT')" >&2; exit 2 ;; esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then echo "--port must be between 1024 and 65535" >&2; exit 2; fi
for l in ${LANES//,/ }; do
  case "$l" in claude|codex|grok|gemini) ;; *) echo "Unknown assistant '$l' in --lanes (choose from: claude, codex, grok, gemini)" >&2; exit 2 ;; esac
done
case "$HOME" in *[[:space:]]*) echo "This installer cannot handle a home directory with spaces in its path ($HOME)." >&2; exit 2 ;; esac

# When this script arrives through `curl | bash`, stdin IS the script, and
# bash reads it as it goes. So: nothing above the last line may touch stdin
# (an `exec </dev/null` here would end the script at this line), and no child
# may read it either — main runs with stdin from /dev/null, at the very end,
# and questions go to the terminal directly.

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
die()  {
  printf '\n%s✗ %s%s\n' "$R" "$1" "$N" >&2
  [ -n "${2:-}" ] && printf '  %s\n' "$2" >&2
  [ -n "$CURRENT" ] && printf '  (while: %s)  Log: %s\n' "$CURRENT" "$LOG" >&2
  exit 1
}

# A terminal we can actually read from, or none.
TTY_IN=/dev/null
if [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then TTY_IN=/dev/tty; fi
# A prompt is written to the terminal directly (stdout is the log pipe); the
# answer is then echoed to stdout so the log keeps it.
say_tty() { if [ "$TTY_IN" = /dev/tty ]; then printf "$@" > /dev/tty; else printf "$@"; fi; }
ask_yn() {   # ask_yn "question" default(Y|N)  → 0 yes, 1 no. No terminal and no --yes: "no".
  local q="$1" def="${2:-Y}" ans
  if [ "$YES" = 1 ]; then [ "$def" = Y ]; return; fi
  if [ "$TTY_IN" = /dev/null ]; then printf '  %s — no terminal to ask on, taking "no" (--yes says yes to everything)\n' "$q"; return 1; fi
  if [ "$def" = Y ]; then say_tty '  %s [Y/n] ' "$q"; else say_tty '  %s [y/N] ' "$q"; fi
  read -r ans < "$TTY_IN" || ans=""
  ans="$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')"
  printf '  %s → %s\n' "$q" "${ans:-(Enter)}"
  case "$ans" in
    "") [ "$def" = Y ] ;;
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}
press_enter() {   # 0 = go on, 1 = skip
  [ "$YES" = 1 ] && return 0
  [ "$TTY_IN" = /dev/null ] && return 1
  say_tty '  %s(press Enter to continue, or type s to skip)%s ' "$D" "$N"
  local ans; read -r ans < "$TTY_IN" || ans=""
  printf '  → %s\n' "${ans:-(Enter)}"
  [ "$ans" != "s" ] && [ "$ans" != "S" ]
}

has_lane() { case ",$LANES," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
have() { command -v "$1" >/dev/null 2>&1; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
hub_alive() {
  local body
  body="$(curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)" || return 1
  printf '%s' "$body" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("service") == "corral-light" else 1)' 2>/dev/null
}

# Sign-in state, per assistant. Only "has a credential file"; whether the
# subscription behind it works is the assistant's own business.
claude_logged_in() {
  local bin="$1" out
  out="$("$bin" auth status 2>/dev/null)" || return 1
  printf '%s' "$out" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("loggedIn") is True else 1)' 2>/dev/null
}
claude_bin() { if [ -x "$BIN/claude" ]; then echo "$BIN/claude"; elif have claude; then command -v claude; fi; }
signed_in() {
  case "$1" in
    claude) local c; c="$(claude_bin)"; [ -n "$c" ] && claude_logged_in "$c" ;;
    grok)   [ -s "$HOME/.grok/auth.json" ] ;;
    codex)  [ -s "$CODEX_HOME_DIR/auth.json" ] ;;
    gemini) [ -s "$HOME/.gemini/antigravity-acp/acp_token.json" ] ;;
    *) return 1 ;;
  esac
}

on_error() {
  local rc=$?
  printf '\n%s✗ Step failed: %s%s\n' "$R" "${CURRENT:-start}" "$N" >&2
  printf '  The full log is at %s\n' "$LOG" >&2
  printf '  Run the same command again: it continues where it left off. If it stops at the same place,\n' >&2
  printf '  the lines just above this one say what went wrong; send them with the log when asking for help.\n' >&2
  exit "$rc"
}
trap on_error ERR
on_int() { printf '\n\n  Interrupted. Nothing is half-written that a re-run cannot finish: run the same command again to continue.\n' >&2; exit 130; }
trap on_int INT

# ── logging: everything also goes to the log file ───────────────────────────
mkdir -p "$STATE"
: >> "$LOG"
printf '\n===== corral-light installer %s — %s — %s =====\n' "$INSTALLER_VERSION" "$(date -Is)" "$*" >> "$LOG"

# ── uninstall ───────────────────────────────────────────────────────────────
uninstall() {
  STEPS=5
  printf '%sThis removes what the installer added. Sign-ins (~/.claude, ~/.grok, …) and your own\n' "$B"
  printf 'files stay unless you say otherwise. Each removal names its target first.%s\n' "$N"
  step "Service"
  if [ "$NO_SERVICE" = 0 ] && systemctl --user list-unit-files corral-light.service 2>/dev/null | grep >/dev/null '^corral-light.service'; then
    printf '  stopping and removing: corral-light.service, corral-light-watch.timer, %s\n' "$DROPIN"
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
  printf '  removing: %s %s %s\n' "$NODE_DIR" "$TOOLS_PREFIX" "$BIN/grok"
  rm -rf "$NODE_DIR" "$TOOLS_PREFIX"; rm -f "$BIN/grok"
  ok "removed"
  step "Clones"
  if ask_yn "Remove the source clones $CL and $SEED (and the corral-light command)?" N; then
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
}

main() {
  # Runs as one side of `main | tee`, a subshell that inherits the options
  # and traps the outer shell had when it forked — so arm them here, not there.
  set -Eeuo pipefail
  trap on_error ERR
  trap on_int INT
  # Both modes: never as root, Linux only.
  [ "$(uname -s)" = Linux ] || die "This installer is for Linux." "macOS and Windows are not covered by this first release."
  [ "$(id -u)" != 0 ] || die "Do not run this as root." "Assistants sign in and work as you. Run it as your normal user; it asks for sudo only if a package is missing."
  if [ "$UNINSTALL" = 1 ]; then uninstall; return 0; fi

  printf '%sCorral Light installer%s %s\n' "$B" "$N" "$D$INSTALLER_VERSION$N"
  printf 'Workspace: %s   Log: %s\n' "$AIOS" "$LOG"

  # ── 1. this machine ───────────────────────────────────────────────────────
  step "Checking this machine"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) NODE_ARCH=x64; NODE_SHA="$NODE_SHA256_X64" ;;
    aarch64|arm64) NODE_ARCH=arm64; NODE_SHA="$NODE_SHA256_ARM64" ;;
    *) die "Unsupported CPU: $ARCH" "Supported: x86_64 and arm64." ;;
  esac
  ok "Linux $ARCH"
  libc="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
  case "$libc" in glibc\ *) ;; *) die "This Linux does not use glibc (Alpine/musl?)." "The prebuilt Node.js and assistant binaries need glibc 2.28+. Use a glibc-based distro for this release." ;; esac
  glibc="${libc#glibc }"
  if [ -n "$glibc" ] && ! printf '2.28\n%s\n' "$glibc" | sort -C -V; then
    die "glibc $glibc is too old; 2.28 or newer is needed (Ubuntu 20.04+, Debian 10+, Fedora 29+)."
  fi
  ok "glibc ${glibc:-present}"
  if [ "$NO_SERVICE" = 0 ] && ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "no systemd user session here (a container, or no login session) — the hub will run for this session only"
    NO_SERVICE=1
  fi
  HEADLESS=0; [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && HEADLESS=1
  if [ "$HEADLESS" = 0 ]; then ok "a desktop is available (the browser can open)"; else warn "no desktop display — sign-ins use device codes and the browser address is printed for you"; fi

  # ── the two questions: before sudo, before any download ───────────────
  if [ -z "$LANES" ]; then
    if [ "$YES" = 1 ]; then
      LANES="claude,codex,grok,gemini"
    elif [ "$TTY_IN" = /dev/null ]; then
      LANES="claude"
      warn "no terminal to ask which assistants you have — setting up Claude only (use --lanes or --yes)"
    else
      say_tty '\n  %sWhich assistants do you have an account for?%s\n' "$B" "$N"
      say_tty '    1  Claude    (claude.ai — Pro or Max)\n'
      say_tty '    2  ChatGPT   (Plus, Pro or Team)\n'
      say_tty '    3  Grok      (SuperGrok or X Premium)\n'
      say_tty '    4  Gemini    (a Google account; this one is a 1.5 GB download)\n'
      tries=0
      while :; do
        say_tty '  Type the numbers, like  1 3  (or  1 2 3 4  for all) — or just press Enter for Claude only: '
        read -r picks < "$TTY_IN" || picks=""
        case "$picks" in
          "") LANES="claude"; break ;;
          *[!1-4\ ]*) tries=$((tries+1)); say_tty '  Just the numbers 1 to 4, please (or Enter).\n'
               [ "$tries" -ge 3 ] && { LANES="claude"; say_tty '  Taking Claude only.\n'; break; } ;;
          *) LANES=""
             case "$picks" in *1*) LANES="$LANES,claude" ;; esac
             case "$picks" in *2*) LANES="$LANES,codex" ;; esac
             case "$picks" in *3*) LANES="$LANES,grok" ;; esac
             case "$picks" in *4*) LANES="$LANES,gemini" ;; esac
             LANES="${LANES#,}"; [ -n "$LANES" ] && break ;;
        esac
      done
    fi
  fi
  ok "assistants: $LANES"
  WIRE_HOOKS=0
  if [ "$NO_SCHEDULE" = 0 ] && has_lane claude; then
    say_tty '\n  %sShould Claude remember your past conversations?%s\n' "$B" "$N"
    say_tty '  (It adds a few lines to Claude'"'"'s settings file; they are printed when written and can be removed.)\n'
    if ask_yn "Remember past conversations?" Y; then WIRE_HOOKS=1; fi
  fi


  # ── 2. distro packages ────────────────────────────────────────────────────
  step "Base tools (git, python3, PyYAML, curl, tar, xz, cron)"
  missing=()
  have git     || missing+=(git)
  have curl    || missing+=(curl)
  have tar     || missing+=(tar)
  have xz      || missing+=(xz)
  have python3 || missing+=(python3)
  have crontab || missing+=(cron)
  if have python3 && ! python3 -c 'import yaml' 2>/dev/null; then missing+=(pyyaml); fi
  if ! have python3; then missing+=(pyyaml); fi
  if [ "$HEADLESS" = 0 ] && ! have xdg-open; then missing+=(xdg-utils); fi
  pkg_names() {  # generic names → this distro's packages
    local mgr="$1"; shift
    local out=()
    for m in "$@"; do
      case "$mgr:$m" in
        apt:pyyaml) out+=(python3-yaml) ;;   dnf:pyyaml) out+=(python3-pyyaml) ;;
        pacman:pyyaml) out+=(python-yaml) ;; zypper:pyyaml) out+=(python3-PyYAML) ;;
        apt:xz) out+=(xz-utils) ;;           *:xz) out+=(xz) ;;
        apt:cron) out+=(cron) ;;             *:cron) out+=(cronie) ;;
        *) out+=("$m") ;;
      esac
    done
    printf '%s\n' "${out[@]}"
  }
  if [ ${#missing[@]} -gt 0 ]; then
    if have apt-get; then MGR=apt; elif have dnf; then MGR=dnf; elif have pacman; then MGR=pacman; elif have zypper; then MGR=zypper; else MGR=""; fi
    [ -n "$MGR" ] || die "Missing: ${missing[*]} — and no known package manager (apt, dnf, pacman, zypper)." "Install them with your distro's tool, then run this again."
    mapfile -t pkgs < <(pkg_names "$MGR" "${missing[@]}")
    case "$MGR" in
      apt)    cmd="sudo apt-get install -y ${pkgs[*]}"; pre="sudo apt-get update -qq" ;;
      dnf)    cmd="sudo dnf install -y ${pkgs[*]}"; pre="" ;;
      pacman) cmd="sudo pacman -S --needed --noconfirm ${pkgs[*]}"; pre="" ;;
      zypper) cmd="sudo zypper install -y ${pkgs[*]}"; pre="" ;;
    esac
    printf '  Missing: %s\n  Will run: %s\n' "${missing[*]}" "$cmd"
    have sudo || die "sudo is not available, and these packages are missing: ${pkgs[*]}" "Ask an administrator to run: ${cmd#sudo }"
    printf '  %sThis asks for your password, for the system package manager only. Nothing shows while you type it.%s\n' "$D" "$N"
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
  PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "python3 is $PYV; 3.9 or newer is required."
  python3 -c 'import yaml' 2>/dev/null || die "PyYAML is not importable by $(command -v python3)." "Install your distro's python3 YAML package, then run again."
  ok "python3 $PYV with PyYAML"
  # cron must be running for Seed's scheduled jobs. Enabling it is a one-time admin action.
  if [ "$NO_SCHEDULE" = 0 ]; then
    cron_unit=""
    for u in cronie cron crond; do
      if systemctl list-unit-files "$u.service" 2>/dev/null | grep >/dev/null "^$u.service"; then cron_unit="$u"; break; fi
    done
    if [ -n "$cron_unit" ] && ! systemctl is-active --quiet "$cron_unit"; then
      if have sudo; then
        printf '  cron (%s) is installed but not running; starting it so scheduled jobs run (asks for your password).\n' "$cron_unit"
        sudo systemctl enable --now "$cron_unit" < "$TTY_IN" || warn "could not start $cron_unit — Seed's scheduled jobs will not run until it is"
      else
        warn "cron ($cron_unit) is not running and sudo is unavailable — scheduled jobs will not run until it is"
      fi
    elif [ -z "$cron_unit" ] && ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
      warn "no cron daemon found running — Seed's scheduled jobs need one (cron or cronie)"
    fi
  fi
  if ! curl -fsSI --max-time 15 https://github.com >/dev/null 2>&1; then
    die "No internet connection (cannot reach github.com)." "Connect, then run the installer again."
  fi
  ok "internet reachable"

  need_mb=1200; has_lane gemini && need_mb=3600
  free_mb="$(df -Pm "$HOME" | awk 'NR==2 {print $4}')"
  if [ -n "$free_mb" ] && [ "$free_mb" -lt "$need_mb" ]; then
    die "Not enough free space in $HOME: ${free_mb} MB free, about ${need_mb} MB needed." "Free some space (or leave Gemini out), then run again."
  fi
  ok "disk: ${free_mb} MB free"

  # ── 3. the two checkouts ──────────────────────────────────────────────────
  step "Fetching Corral Light and AI-OS Seed"
  mkdir -p "$TOOLS" "$BIN"
  CL_CHANGED=0
  sync_repo() {  # sync_repo <dir> <url> <ref> <label>  → sets SYNC_CHANGED=1 when HEAD moved
    local dir="$1" url="$2" ref="$3" label="$4" before="" after=""
    SYNC_CHANGED=0
    if [ -d "$dir/.git" ]; then
      before="$(git -C "$dir" rev-parse HEAD)"
      if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
        warn "$label at $dir has local changes — left exactly as it is (not updated)"
        return 0
      fi
      git -C "$dir" fetch -q --tags origin
      local want="$ref"
      git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref" && want="origin/$ref"
      if ! git -C "$dir" merge-base --is-ancestor HEAD "$want" 2>/dev/null; then
        warn "$label at $dir has commits of its own that $ref does not contain — left exactly as it is (not updated)"
        return 0
      fi
      if [ "$want" = "origin/$ref" ]; then git -C "$dir" checkout -q -B "$ref" "origin/$ref"
      else git -C "$dir" checkout -q --detach "$ref"; fi
      after="$(git -C "$dir" rev-parse HEAD)"
      if [ "$before" = "$after" ]; then skip "$label already at $ref ($(git -C "$dir" rev-parse --short HEAD))"
      else SYNC_CHANGED=1; ok "$label updated to $ref ($(git -C "$dir" rev-parse --short HEAD))"; fi
    elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
      die "$dir exists and is not a git checkout." "Move it aside, then run the installer again."
    else
      git clone -q "$url" "$dir"
      if git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref"; then
        git -C "$dir" checkout -q -B "$ref" "origin/$ref"
      else
        git -C "$dir" checkout -q --detach "$ref"
      fi
      SYNC_CHANGED=1
      ok "$label cloned at $ref ($(git -C "$dir" rev-parse --short HEAD))"
    fi
  }
  sync_repo "$CL" "$CORRAL_LIGHT_REPO" "$CORRAL_LIGHT_REF" "Corral Light"; CL_CHANGED=$SYNC_CHANGED
  sync_repo "$SEED" "$AIOS_SEED_REPO" "$AIOS_SEED_REF" "AI-OS Seed"
  [ -f "$CL/hub.py" ] || die "$CL does not look like Corral Light (no hub.py)."
  [ -f "$SEED/install.py" ] || die "$SEED does not look like AI-OS Seed (no install.py)."
  cat > "$BIN/corral-light" <<EOF
#!/bin/bash
exec "$CL/corral-light" "\$@"
EOF
  chmod +x "$BIN/corral-light"
  ok "command: $BIN/corral-light"
  line='export PATH="$HOME/.local/bin:$PATH"'
  added=0
  for rc in "$HOME/.profile" "$HOME/.bashrc"; do
    if ! grep -qsF "$line" "$rc"; then printf '\n# added by the Corral Light installer\n%s\n' "$line" >> "$rc"; added=1; fi
  done
  case ":$PATH:" in *":$BIN:"*) ;; *) export PATH="$BIN:$PATH" ;; esac
  if [ "$added" = 1 ]; then ok "added ~/.local/bin to PATH in ~/.profile and ~/.bashrc (new terminals pick it up)"; else skip "~/.local/bin already on PATH in ~/.profile and ~/.bashrc"; fi

  # ── 4. private Node.js ────────────────────────────────────────────────────
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
  export NPM_CONFIG_UPDATE_NOTIFIER=false

  # ── 5. adapters ───────────────────────────────────────────────────────────
  step "Claude and ChatGPT adapters (npm ci, from the lock file)"
  [ -f "$CL/spike/package-lock.json" ] || die "$CL/spike/package-lock.json is missing" "The checkout is incomplete; the adapters are installed from that lock file so every install gets the same versions."
  (cd "$CL/spike" && npm ci --no-audit --no-fund --loglevel=error)
  [ -x "$CL/spike/node_modules/.bin/claude-agent-acp" ] || die "claude-agent-acp did not install" "see $LOG"
  [ -x "$CL/spike/node_modules/.bin/codex-acp" ] || die "codex-acp did not install" "see $LOG"
  [ -x "$CL/spike/node_modules/.bin/codex" ] || die "the bundled codex CLI did not install" "see $LOG"
  ok "adapters installed"

  # ── 6. assistants' own programs ───────────────────────────────────────────
  step "Assistant programs"
  if has_lane claude; then
    if [ -n "$(claude_bin)" ]; then
      skip "Claude Code already installed ($(claude_bin))"
    else
      printf '  Installing Claude Code %s with Anthropic'"'"'s installer (%s)…\n' "$CLAUDE_VERSION" "$CLAUDE_INSTALL_URL"
      curl -fsSL --retry 3 "$CLAUDE_INSTALL_URL" | bash -s "$CLAUDE_VERSION"
      [ -n "$(claude_bin)" ] || die "Claude Code did not install" "see $LOG"
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
      printf '  Downloading the Antigravity runtime (about 1.5 GB, checksum verified; this is the slow part)…\n'
      python3 "$CL/install_antigravity_acp.py" --install
      ok "Antigravity (Gemini) runtime installed"
    fi
  fi

  # ── 7. AI-OS Seed in the workspace ────────────────────────────────────────
  # Each phase checks its own state, so a run that stopped halfway finishes
  # on the next run instead of hiding behind the receipt.
  step "AI-OS Seed in $AIOS"
  if [ ! -f "$AIOS/.cc-seed/receipt.json" ]; then
    # Never two installs on one machine: the scheduler owns one managed block.
    detect="$(python3 "$SEED/install.py" --detect 2>&1)" || die "Seed's prior-install check failed:" "$detect"
    case "$detect" in
      *"no prior AI-OS Seed footprint"*|*"prior-install signal"*) ;;
      *) die "Seed's prior-install check gave an answer this installer does not recognise:" "$detect" ;;
    esac
    other="$(printf '%s\n' "$detect" | python3 -c '
import os, re, sys
mine = os.path.realpath(sys.argv[1])
for line in sys.stdin:
    m = re.search(r"install root (\S+)", line) or re.search(r"dir: (\S+) — an AI-OS Seed INSTALL", line)
    if m and os.path.realpath(m.group(1).rstrip(",")) != mine:
        print(line.rstrip())
' "$AIOS")"
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
  else
    skip "already installed (receipt present) — checking each piece"
  fi
  gated() { python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if sys.argv[2] in (r.get("gated_writes") or {}) else 1)' "$AIOS/.cc-seed/receipt.json" "$1" 2>/dev/null; }

  printf '  Verifying…\n'
  python3 "$AIOS/_lib/selftest.py" >/dev/null
  python3 "$AIOS/session-brief/session_brief.py" selftest >/dev/null
  python3 "$AIOS/observability/log_run.py" --job hello_fleet -- python3 "$AIOS/demo/hello_fleet.py"
  python3 "$AIOS/observability/log_run.py" --job repo_hygiene -- python3 "$AIOS/observability/repo_hygiene.py" --root "$AIOS" --findings-exit0
  python3 "$AIOS/observability/report.py" --job hello_fleet | tail -n 2
  ok "selftests pass; the demo job ran through the real run logger"

  if ! grep -qs "cc-seed:start" "$AIOS/CLAUDE.md" 2>/dev/null; then
    mkdir -p "$AIOS/.cc-seed/staged"
    # Unquoted heredoc on purpose: paths are filled in; the backticks are escaped.
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
  else
    skip "CLAUDE.md already has the seed region"
  fi

  if [ "$NO_SCHEDULE" = 0 ]; then
    if gated mesh-bootstrap; then
      skip "memory mesh already bootstrapped"
    else
      python3 "$SEED/install.py" --target "$AIOS" --approve mesh-bootstrap
      ok "memory mesh started (event log at ~/memory-events, fold every 5 minutes)"
    fi
    if gated memory-hooks; then
      if [ "$WIRE_HOOKS" = 1 ]; then skip "memory hooks already wired"
      else skip "memory hooks are already wired from an earlier run and were left in place — to remove: python3 $SEED/install.py --target $AIOS --revoke memory-hooks"; fi
    elif [ "$WIRE_HOOKS" = 1 ]; then
      CI=true python3 "$SEED/install.py" --target "$AIOS" --approve memory-hooks --apply
      if python3 "$SEED/install.py" --target "$AIOS" --contract; then
        ok "memory hooks wired, and the memory contract holds"
      else
        warn "memory hooks wired, but the memory contract reported a problem (above)"
      fi
    else
      warn "memory hooks not wired (you said no) — later: python3 $SEED/install.py --target $AIOS --approve memory-hooks"
    fi
    bash "$AIOS/scheduler/sync.sh"
    ok "scheduler synced to cron (crontab -l shows the cc-seed block)"
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || die "Seed's post-install audit flagged a difference (above)."
    ok "post-install audit clean"
  else
    warn "--no-schedule: scheduler, memory mesh and hooks skipped"
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || warn "audit FLAGGED — expected under --no-schedule (the scheduler was not synced)"
  fi
  python3 "$AIOS/observability/freshness.py" --all | grep -E '^\[(OK|MISSING|DRIFT|STALE)' | head -n 6 || true

  # ── 8. service ────────────────────────────────────────────────────────────
  step "Running Corral Light"
  SERVICE_PATH="$NODE_DIR/bin:/usr/local/bin:/usr/bin:/bin:$BIN"
  if ! hub_alive; then
    # Nothing of ours answers; if the port is held anyway, say so now instead
    # of waiting 60 s for a hub that can never bind it.
    if ! python3 -c "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', $PORT)); s.close()" 2>/dev/null; then
      die "Port $PORT is in use by another program." "Run the installer again with --port 8099 (or any free port)."
    fi
  fi
  if [ "$NO_SERVICE" = 0 ]; then
    if [ ! -f "$UNIT_DIR/corral-light.service" ]; then
      "$CL/corral-light" install-service --port "$PORT" >/dev/null
      ok "wrote $UNIT_DIR/corral-light.service"
    else
      skip "service file exists — left as it is"
    fi
    mkdir -p "$(dirname "$DROPIN")"
    new_dropin="$(cat <<EOF
# Written by the Corral Light installer. The hub needs the private Node and
# ~/.local/bin on its PATH; a user service does not inherit your shell's.
# System directories come before ~/.local/bin on purpose.
[Service]
Environment="PATH=$SERVICE_PATH"
Environment="CORRAL_NODE_BIN=$NODE_DIR/bin"
Environment="CORRAL_LIGHT_PORT=$PORT"
EOF
)"
    DROPIN_CHANGED=0
    if [ ! -f "$DROPIN" ] || [ "$(cat "$DROPIN")" != "$new_dropin" ]; then printf '%s\n' "$new_dropin" > "$DROPIN"; DROPIN_CHANGED=1; fi
    systemctl --user daemon-reload
    systemctl --user enable corral-light.service >/dev/null 2>&1 || true
    if systemctl --user is-active --quiet corral-light.service; then
      if [ "$CL_CHANGED" = 1 ] || [ "$DROPIN_CHANGED" = 1 ]; then
        systemctl --user restart corral-light.service
        ok "service restarted on the updated code"
      else
        skip "service already running, nothing changed"
      fi
    else
      systemctl --user start corral-light.service
      ok "service enabled and started"
    fi
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep >/dev/null 'Linger=yes'; then
      if loginctl enable-linger "$USER" 2>/dev/null; then ok "service survives logout (linger enabled)"
      else warn "could not enable linger; the hub stops when you log out (sudo loginctl enable-linger $USER fixes that)"; fi
    fi
    if [ -f "$CL/corral-light-watch.timer" ] && [ ! -f "$UNIT_DIR/corral-light-watch.timer" ]; then
      sed "s|%HERE%|$CL|" "$CL/corral-light-watch.service" > "$UNIT_DIR/corral-light-watch.service"
      cp "$CL/corral-light-watch.timer" "$UNIT_DIR/"
      systemctl --user daemon-reload
      systemctl --user enable --now corral-light-watch.timer >/dev/null 2>&1 || true
      ok "watchdog timer enabled (pages you if the hub goes down; never restarts it)"
    fi
  else
    if hub_alive; then
      skip "a Corral Light hub already answers on port $PORT"
    else
      # The whole subshell is redirected and exec's the hub: no shell in between
      # keeps this script's output pipe open, so the installer can finish.
      ( cd "$CL" && exec env PATH="$SERVICE_PATH" CORRAL_LIGHT_PORT="$PORT" nohup "$CL/corral-light" serve ) >> "$STATE/hub.log" 2>&1 </dev/null &
      ok "hub started for this session (log: $STATE/hub.log)"
    fi
  fi
  for _ in $(seq 1 60); do hub_alive && break; sleep 1; done
  hub_alive || die "The hub did not answer on port $PORT within 60 s." "journalctl --user -u corral-light -n 50   shows why."
  ok "hub answering at http://127.0.0.1:$PORT/"

  # ── 9. sign-ins ───────────────────────────────────────────────────────────
  step "Signing in to each assistant"
  if [ "$SKIP_LOGINS" = 1 ]; then
    warn "--skip-logins: nothing started (the summary below says how to sign in later)"
  else
    if has_lane claude; then
      if signed_in claude; then ok "Claude: already signed in"
      else
        printf '  %sClaude:%s your browser will open to sign in with your Claude account (Pro or Max).\n' "$B" "$N"
        if press_enter; then
          if timeout --foreground -k 10 600 "$(claude_bin)" auth login < "$TTY_IN"; then ok "Claude: signed in"; else warn "Claude sign-in did not finish (ten-minute limit) — later: claude auth login"; fi
        else skip "Claude sign-in skipped"; fi
      fi
    fi
    if has_lane grok; then
      if signed_in grok; then ok "Grok: already signed in"
      else
        printf '  %sGrok:%s sign in with your X / Grok account.\n' "$B" "$N"
        if press_enter; then
          grok_login=("$BIN/grok" login); [ "$HEADLESS" = 1 ] && grok_login+=(--device-auth)
          if timeout --foreground -k 10 600 "${grok_login[@]}" < "$TTY_IN"; then ok "Grok: signed in"
          else warn "Grok sign-in did not finish (ten-minute limit) — later: grok login"; fi
        else skip "Grok sign-in skipped"; fi
      fi
    fi
    if has_lane codex; then
      if signed_in codex; then ok "ChatGPT: already signed in"
      else
        printf '  %sChatGPT:%s sign in with your ChatGPT account (Plus, Pro or Team).\n' "$B" "$N"
        if press_enter; then
          mkdir -p "$CODEX_HOME_DIR"; chmod 700 "$CODEX_HOME_DIR"
          codex_login=("$CL/spike/node_modules/.bin/codex" login); [ "$HEADLESS" = 1 ] && codex_login+=(--device-auth)
          if CODEX_HOME="$CODEX_HOME_DIR" timeout --foreground -k 10 600 "${codex_login[@]}" < "$TTY_IN"; then ok "ChatGPT: signed in"
          else warn "ChatGPT sign-in did not finish — later: CODEX_HOME=$CODEX_HOME_DIR $CL/spike/node_modules/.bin/codex login"; fi
        else skip "ChatGPT sign-in skipped"; fi
      fi
    fi
    if has_lane gemini; then
      if signed_in gemini; then ok "Gemini: already signed in"
      else ok "Gemini: signs in with your Google account the first time you open a Gemini conversation"; fi
    fi
  fi

  # ── 10. open the wall ─────────────────────────────────────────────────────
  step "Opening Corral Light"
  LAUNCH_OK=0
  if CORRAL_LIGHT_PORT="$PORT" "$CL/corral-light" launch; then LAUNCH_OK=1; else warn "could not open the browser — open http://127.0.0.1:$PORT/ yourself, or run: corral-light launch"; fi

  # ── 11. receipt + what you have ───────────────────────────────────────────
  step "Done"
  claude_ver=""; c="$(claude_bin)"; [ -n "$c" ] && claude_ver="$("$c" --version 2>/dev/null | head -n1 || true)"
  python3 - "$RECEIPT" "$INSTALLER_VERSION" "$CL" "$SEED" "$AIOS" "$NODE_VERSION" "$LANES" "$PORT" "$claude_ver" <<'PY'
import json, subprocess, sys, datetime
rc, ver, cl, seed, aios, node, lanes, port, claude_ver = sys.argv[1:10]
def rev(d):
    try: return subprocess.check_output(["git", "-C", d, "rev-parse", "HEAD"], text=True).strip()
    except Exception: return None
json.dump({"installer": ver, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "corral_light": {"path": cl, "commit": rev(cl)}, "ai_os_seed": {"path": seed, "commit": rev(seed)},
           "workspace": aios, "node": node, "lanes": lanes.split(","), "port": int(port),
           "claude_code": claude_ver or None},
          open(rc, "w"), indent=1)
PY
  ok "receipt: $RECEIPT"
  printf '\n'
  todo=()
  for l in ${LANES//,/ }; do
    case "$l" in
      claude) if signed_in claude; then ok "Claude: signed in"; else todo+=("Claude:   claude auth login"); fi ;;
      grok)   if signed_in grok; then ok "Grok: signed in"; else todo+=("Grok:     grok login"); fi ;;
      codex)  if signed_in codex; then ok "ChatGPT: signed in"; else todo+=("ChatGPT:  CODEX_HOME=$CODEX_HOME_DIR $CL/spike/node_modules/.bin/codex login"); fi ;;
      gemini) if signed_in gemini; then ok "Gemini: signed in"; else todo+=("Gemini:   open a Gemini conversation on the wall; the Google sign-in opens"); fi ;;
    esac
  done
  if [ ${#todo[@]} -eq 0 ] && [ "$LAUNCH_OK" = 1 ]; then
    printf '\n%sReady.%s Corral Light is running, every assistant you chose has a sign-in, and the browser was opened on it.\n' "$B" "$N"
  else
    printf '\n%sInstalled.%s Corral Light is running. Still to do:\n' "$B" "$N"
    for t in "${todo[@]}"; do printf '    %s\n' "$t"; done
    [ "$LAUNCH_OK" = 1 ] || printf '    Open the wall:  corral-light launch   (or http://127.0.0.1:%s/ in your browser)\n' "$PORT"
  fi
  printf '\n  The wall:        http://127.0.0.1:%s/   (any time: %scorral-light launch%s)\n' "$PORT" "$B" "$N"
  printf '  Your workspace:  %s\n' "$AIOS"
  if has_lane claude; then printf '  Try next:        open a NEW terminal, run  %scd %s && claude%s  and type  %s/status%s\n' "$B" "$AIOS" "$N" "$B" "$N"; fi
  printf '  Health:          corral-light doctor   ·   Log: %s\n' "$LOG"
  printf '  Update:          run the same install line again (a copy is at %s/install.sh)\n' "$CL"
  printf '  Remove:          bash %s/install.sh --uninstall\n\n' "$CL"
}

# Outer shell: no errexit and no ERR trap here, or a failure inside main would
# be reported twice (main's own trap already named the step). main re-arms both.
trap - ERR
set +e
main "$@" </dev/null 2>&1 | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
exit "$rc"
