#!/usr/bin/env bash
# Register corral-dictate:// on this Linux desktop so the Corral composer's 🎙
# toggles voxtype here (dictate.py). Per user; nothing outside ~/.local.
#   packaging/corral-dictate-handler.sh            install
#   packaging/corral-dictate-handler.sh --uninstall
set -euo pipefail
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop="$apps/corral-dictate.desktop"
if [[ "${1:-}" == "--uninstall" ]]; then
  [[ -e "$desktop" ]] && mv "$desktop" "$desktop.removed-$(date +%Y%m%d%H%M%S)"
  command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
  echo "corral-dictate:// handler removed (the old file was kept beside it)"
  exit 0
fi
command -v voxtype >/dev/null || { echo "voxtype is not installed here" >&2; exit 1; }
mkdir -p "$apps"
cat > "$desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Corral dictate
Comment=Toggle voxtype dictation from the Corral Light composer
Exec=voxtype record toggle
NoDisplay=true
MimeType=x-scheme-handler/corral-dictate;
DESK
xdg-mime default corral-dictate.desktop x-scheme-handler/corral-dictate
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
echo "corral-dictate:// now runs: $(xdg-mime query default x-scheme-handler/corral-dictate)"
