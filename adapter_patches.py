#!/usr/bin/env python3
"""adapter_patches — small pinned patches to the npm adapters in spike/.

Each patch lists the package versions it was checked against and is an
exact text replacement that must match once. `apply` is idempotent; `check` reports
without writing. install.sh applies after `npm ci`; `lanes update` applies
to a staged tree before its probe and refuses a Claude adapter that still
drops early rate-limit notices with no patch for its version.

The one patch today (docs/finops-module-plan.md §4.6, Claude adapter):
claude-agent-acp forwards the SDK's `rate_limit_event` only once the turn
has a usage figure, and the notice always arrives before the first reply
(docs/finops-phase0.md), so Light never saw one. Patched, it is forwarded
at once as a `usage_update` with `used` (and `size`) omitted when unknown.
Offered upstream; drop the patch when a release carries the fix.

    python3 adapter_patches.py check [SPIKE_DIR]
    python3 adapter_patches.py apply [SPIKE_DIR]
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE = HERE / "spike"

MARKER = "corral-light patch: rate-limit-before-usage"

_RLE_FIND = '''                    case "rate_limit_event": {
                        if (lastAssistantTotalUsage !== null) {
                            await sendUpdate({
                                sessionId: params.sessionId,
                                update: attachUsageModel({
                                    sessionUpdate: "usage_update",
                                    used: lastAssistantTotalUsage,
                                    size: session.contextWindowSize,
                                    _meta: { "_claude/rateLimit": message.rate_limit_info },
                                }),
                            });
                        }
                        break;
                    }
'''

_RLE_REPLACE = '''                    case "rate_limit_event": {
                        // ''' + MARKER + '''
                        await sendUpdate({
                            sessionId: params.sessionId,
                            update: attachUsageModel({
                                sessionUpdate: "usage_update",
                                ...(lastAssistantTotalUsage !== null
                                    ? { used: lastAssistantTotalUsage, size: session.contextWindowSize }
                                    : {}),
                                _meta: { "_claude/rateLimit": message.rate_limit_info },
                            }),
                        });
                        break;
                    }
'''

# The unpatched guard, in any version: its presence means notices are dropped.
RLE_GUARD = ('case "rate_limit_event": {\n'
             '                        if (lastAssistantTotalUsage !== null) {')

PATCHES = (
    {"id": "rate-limit-before-usage",
     "package": "@agentclientprotocol/claude-agent-acp",
     # The guarded block is byte-identical in each listed release.
     "versions": ("0.85.1", "0.88.0"),
     "file": "dist/acp-agent.js", "marker": MARKER,
     "find": _RLE_FIND, "replace": _RLE_REPLACE},
)


def _pkg_dir(spike, package):
    return Path(spike) / "node_modules" / package


def _version(spike, package):
    try:
        return json.loads((_pkg_dir(spike, package) / "package.json")
                          .read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _status(spike, patch):
    """(status, text): patched | unpatched | absent | other-version | drift."""
    version = _version(spike, patch["package"])
    if version is None:
        return "absent", None
    if version not in patch["versions"]:
        return "other-version", None
    try:
        text = (_pkg_dir(spike, patch["package"]) / patch["file"]).read_text(encoding="utf-8")
    except OSError:
        return "drift", None
    if patch["marker"] in text and text.count(patch["replace"]) == 1:
        return "patched", text
    if text.count(patch["find"]) == 1:
        return "unpatched", text
    return "drift", text


def check(spike=SPIKE):
    """[{id, package, version, installed, status}] without writing anything."""
    return [{"id": p["id"], "package": p["package"], "version": ", ".join(p["versions"]),
             "installed": _version(spike, p["package"]), "status": _status(spike, p)[0]}
            for p in PATCHES]


def _write_atomic(path, text):
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".patch-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply(spike=SPIKE):
    """Apply every patch whose pinned version is installed. Returns check()
    rows after writing; a `drift` row means the pinned version's text did not
    match and nothing was written for it."""
    for p in PATCHES:
        status, text = _status(spike, p)
        if status == "unpatched":
            _write_atomic(_pkg_dir(spike, p["package"]) / p["file"],
                          text.replace(p["find"], p["replace"], 1))
    return check(spike)


def drops_early_rate_limits(spike=SPIKE):
    """True when the installed Claude adapter still has the unpatched guard
    (any version): the feed would never see a Claude quota notice."""
    f = _pkg_dir(spike, PATCHES[0]["package"]) / PATCHES[0]["file"]
    try:
        return RLE_GUARD in f.read_text(encoding="utf-8")
    except OSError:
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("check", "apply"):
        print("usage: adapter_patches.py check|apply [SPIKE_DIR]", file=sys.stderr, flush=True)
        return 2
    spike = Path(argv[1]) if len(argv) > 1 else SPIKE
    rows = apply(spike) if argv[0] == "apply" else check(spike)
    bad = False
    for r in rows:
        print(f"{r['id']}: {r['status']} ({r['package']} pinned {r['version']}, "
              f"installed {r['installed']})", flush=True)
        bad = bad or r["status"] in ("drift",) or \
            (argv[0] == "apply" and r["status"] == "unpatched")
    if drops_early_rate_limits(spike):
        print("claude-agent-acp still drops rate-limit notices that arrive before "
              "the first usage; quota capture will be empty", flush=True)
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
