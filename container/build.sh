#!/bin/sh
# Build the Corral Light image from release-manifest.json — the only way in.
#
#   container/build.sh [tag]          default tag: corral-light:dev
#   NO_CACHE=1 container/build.sh     cold build (T-IMG-1 reproducibility)
#
# Passes the pinned base digest and apt snapshot from the manifest, and
# normalises timestamps (SOURCE_DATE_EPOCH = the HEAD commit time) so two cold
# builds of one commit can produce one digest.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
TAG="${1:-corral-light:dev}"
M="$HERE/release-manifest.json"

read_m() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$M" "$1"; }

REF="$(read_m base.ref)"
DIGEST="$(read_m base.digest)"
SNAP="$(read_m apt_snapshot)"
case "$DIGEST" in sha256:*) ;; *) echo "build: base digest not pinned; run container/pin.py --write" >&2; exit 1;; esac
BASE="${REF%%:*}@${DIGEST}"
EPOCH="$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || echo 0)"

set -- --platform linux/amd64 -f "$HERE/Dockerfile" \
  --build-arg "BASE=$BASE" --build-arg "APT_SNAPSHOT=$SNAP" \
  --build-arg "SOURCE_DATE_EPOCH=$EPOCH" \
  --output "type=image,name=$TAG,rewrite-timestamp=true,unpack=false" \
  --provenance=false --sbom=false
[ "${NO_CACHE:-}" = 1 ] && set -- "$@" --no-cache
echo "build: $TAG from $BASE (apt $SNAP, epoch $EPOCH)" >&2
exec docker buildx build "$@" "$ROOT"
