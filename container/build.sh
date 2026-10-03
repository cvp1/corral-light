#!/bin/sh
# Build the Corral Light image from release-manifest.json — the only way in.
#
#   container/build.sh [tag]          default tag: corral-light:dev
#   NO_CACHE=1 container/build.sh     cold build (T-IMG-1 reproducibility)
#   CORRAL_BUILD_DIR=/big/disk/corral-build container/build.sh
#                                     keep the build cache OUTSIDE Docker's VM
#                                     disk (a host with a small system disk)
#
# Passes the pinned base digest and apt snapshot from the manifest, and
# normalises timestamps (SOURCE_DATE_EPOCH = the HEAD commit time) so two cold
# builds of one commit can produce one digest.
#
# With CORRAL_BUILD_DIR set, layer cache is exported to and imported from
# $CORRAL_BUILD_DIR/cache/corral-light, and afterwards Docker's own build
# cache is trimmed to CORRAL_KEEP_CACHE (default 2GB). Docker Desktop's VM
# disk grows but only shrinks lazily, so an untrimmed cache is what fills a
# small system disk (measured on mac-host, 2026-10-01: 21 GB of cache).
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

CACHE=""
if [ -n "${CORRAL_BUILD_DIR:-}" ]; then
  CACHE="$CORRAL_BUILD_DIR/cache/corral-light"
  mkdir -p "$CACHE"
  [ -f "$CACHE/index.json" ] && set -- "$@" --cache-from "type=local,src=$CACHE"
  set -- "$@" --cache-to "type=local,dest=$CACHE.new,mode=max"
fi

echo "build: $TAG from $BASE (apt $SNAP, epoch $EPOCH)${CACHE:+, cache $CACHE}" >&2
docker buildx build "$@" "$ROOT"

if [ -n "$CACHE" ]; then
  # Swap in the new export only after a successful build: a failed build
  # must not leave the next one without its cache.
  rm -rf "$CACHE.old"
  mv "$CACHE" "$CACHE.old" && mv "$CACHE.new" "$CACHE" && rm -rf "$CACHE.old"
  docker builder prune -f --keep-storage "${CORRAL_KEEP_CACHE:-2GB}" >/dev/null
  echo "build: cache in $CACHE; Docker build cache trimmed to ${CORRAL_KEEP_CACHE:-2GB}" >&2
fi
