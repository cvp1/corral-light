# syntax=docker/dockerfile:1.7
#
# Corral Light — one public linux/amd64 image (docs/container-install-proposal.md v3).
#
# Build with container/build.sh, never bare `docker build`: build.sh reads
# release-manifest.json and passes the pinned base digest and apt snapshot.
# Every downloaded artifact is verified by container/fetch.py against the
# manifest; a mismatch stops the build (T-IMG-2).
#
# PUBLIC IMAGE HYGIENE: nothing host-, user- or machine-specific is baked in.
# UID/GID, home path, hostname, mounts and the interpreter parity map are all
# RUNTIME config (compose env), applied by entrypoint.sh. T-IMG-4 scans for it.

ARG BASE
ARG APT_SNAPSHOT

# ── fetch: download + verify, nothing from here reaches the final image but
#    the verified outputs ──────────────────────────────────────────────────
FROM --platform=linux/amd64 ${BASE} AS fetch
ARG APT_SNAPSHOT
RUN test -n "${APT_SNAPSHOT}" || (echo "APT_SNAPSHOT unset: use container/build.sh" >&2; exit 1)
RUN printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm main\n' "${APT_SNAPSHOT}" \
      > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/* \
 && apt-get -o Acquire::Retries=3 update -qq \
 && apt-get install -y -qq --no-install-recommends git ca-certificates xz-utils \
 && rm -rf /var/lib/apt/lists/*
COPY container/release-manifest.json container/fetch.py /src/
RUN python3 /src/fetch.py --manifest /src/release-manifest.json --out /dl \
 && mkdir -p /dl/node \
 && tar -xJf /dl/node.tar.xz -C /dl/node --strip-components=1 \
 && rm /dl/node.tar.xz

# ── deps: the two npm adapters, from the lockfile (sha512 integrity = pin).
#    Brings the Linux x64 Claude Code and Codex binaries as optional deps. ──
FROM --platform=linux/amd64 ${BASE} AS deps
COPY --from=fetch /dl/node /opt/node
ENV PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin
COPY spike/package.json spike/package-lock.json /opt/corral-light/spike/
RUN cd /opt/corral-light/spike \
 && npm ci --no-audit --no-fund --ignore-scripts=false \
 && test -x node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude \
 && test -x node_modules/.bin/codex \
 && rm -rf /root/.npm

# ── src: the app source with every timestamp set to SOURCE_DATE_EPOCH.
#    A plain `COPY .` carries host mtimes into the layer, and host directory
#    mtimes change for incidental reasons (an editor temp file, a symlink),
#    so two builds of one commit differed (T-IMG-1, measured 2026-10-01).
#    BuildKit's rewrite-timestamp only clamps times NEWER than the epoch. ──
FROM --platform=linux/amd64 ${BASE} AS src
ARG SOURCE_DATE_EPOCH
COPY . /src/
RUN test -n "${SOURCE_DATE_EPOCH}" \
 && find /src -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +

# ── final ────────────────────────────────────────────────────────────────
FROM --platform=linux/amd64 ${BASE}
ARG APT_SNAPSHOT
LABEL org.opencontainers.image.source="https://github.com/cvp1/corral-light" \
      org.opencontainers.image.description="Corral Light: hub, Seed and four agent lanes" \
      org.opencontainers.image.licenses="MIT"

RUN printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm main\n' "${APT_SNAPSHOT}" \
      > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/* \
 && apt-get -o Acquire::Retries=3 update -qq \
 && apt-get install -y -qq --no-install-recommends \
      git openssh-client ca-certificates tini procps less ripgrep \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/apt /var/log/dpkg.log \
           /var/log/alternatives.log /var/cache/ldconfig/aux-cache
# (the last two differ on every cold build — T-IMG-1, measured 2026-10-01)

COPY --from=fetch /dl/node /opt/node
COPY --from=fetch /dl/grok /opt/corral/bin/grok
COPY --from=fetch /dl/antigravity /opt/corral/antigravity-acp
COPY --from=fetch /dl/aios-seed /opt/aios-seed
# Seed is stdlib-only: no pip, and pip's install is not byte-reproducible.
RUN python3 -m venv --without-pip /opt/aios-seed/.venv

COPY --from=deps /opt/corral-light/spike/node_modules /opt/corral-light/spike/node_modules
# App source last: it changes most often. .dockerignore keeps docs, reviews,
# tests, host service files and .git out.
COPY --from=src /src/ /opt/corral-light/
# No compileall: .pyc output differed between two cold builds (T-IMG-1), and
# PYTHONDONTWRITEBYTECODE keeps runtime from writing any either.

# Container-owned PATH: container binaries only, never a host bin dir (T-ID-3).
# claude/codex are the version-matched binaries the adapters run, so a login
# writes state for the same CLI the lane uses.
RUN set -e; B=/opt/corral/bin; NM=/opt/corral-light/spike/node_modules; \
    ln -s $NM/@anthropic-ai/claude-agent-sdk-linux-x64/claude $B/claude; \
    ln -s $NM/.bin/codex $B/codex; \
    printf '#!/bin/sh\nexec /opt/corral-light/corral-light "$@"\n' > $B/corral-light; \
    chmod 755 $B/corral-light /opt/corral-light/container/entrypoint.sh

ENV PATH=/opt/corral/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
    CORRAL_CONTAINER=1 \
    CORRAL_LIGHT_BIND=0.0.0.0 \
    CORRAL_LIGHT_STATE=/var/lib/corral/state \
    CORRAL_GROK_BIN=/opt/corral/bin/grok \
    CORRAL_ANTIGRAVITY_ACP_BINARY=/opt/corral/antigravity-acp/agy_acp_server.par \
    CORRAL_MANIFEST=/opt/corral-light/container/release-manifest.json \
    AIOS_SEED_HOME=/opt/aios-seed \
    AIOS_SEED_PYTHON=/opt/aios-seed/.venv/bin/python3 \
    CHOKIDAR_USEPOLLING=1 \
    WATCHPACK_POLLING=true \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8

EXPOSE 8098
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/corral-light/container/entrypoint.sh"]
CMD ["hub"]
