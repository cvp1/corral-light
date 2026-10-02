#!/usr/bin/python3
"""Launch Google's native Antigravity ACP server with a private file mode.

The vendor server keeps its own OAuth token and conversation state below
``~/.gemini/antigravity-acp`` and speaks ACP natively end to end.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


RUNTIME = Path.home() / ".local/lib/corral/antigravity-acp"
BINARY = Path(os.environ.get("CORRAL_ANTIGRAVITY_ACP_BINARY",
                             str(RUNTIME / "agy_acp_server.par")))
HELPER = BINARY.with_name("localharness_external")
PRIVATE_UMASK = 0o077


def server_argv(binary=None, row=None):
    """Exact vendor command for the build pinned on this platform; a host with
    no pinned row gets the Linux flags."""
    if row is None:
        from install_antigravity_acp import release_for
        row = release_for()
    args = row["args"] if row else ["--uid="]
    return [str(binary or BINARY), *args]


def unavailable_reason(binary=None):
    binary = Path(binary or BINARY)
    helper = binary.with_name("localharness_external")
    missing = [str(p) for p in (binary, helper) if not p.is_file()]
    if missing:
        return "native Antigravity ACP runtime missing: " + ", ".join(missing)
    if not os.access(binary, os.X_OK):
        return f"native Antigravity ACP server is not executable: {binary}"
    return ""


def health(binary=None):
    """Perform a real ACP initialize handshake; never emits token material."""
    binary = Path(binary or BINARY)
    problem = unavailable_reason(binary)
    if problem:
        return False, problem
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {},
                   "clientInfo": {"name": "corral-health", "version": "1"}},
    }
    try:
        os.umask(PRIVATE_UMASK)
        result = subprocess.run(server_argv(binary), input=json.dumps(request) + "\n",
                                text=True, capture_output=True, timeout=25,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"native Antigravity ACP did not start: {e}"
    for line in result.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        info = message.get("result", {}).get("agentInfo", {})
        if info.get("name") == "antigravity-acp":
            return True, str(info.get("version", "unknown version"))
    detail = result.stderr.strip().splitlines()[-1:] or [f"exit {result.returncode}"]
    return False, "native Antigravity ACP handshake failed: " + detail[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", action="store_true",
                        help="perform an ACP initialize handshake and exit")
    args = parser.parse_args(argv)
    if args.health:
        ok, detail = health()
        print(("native Antigravity ACP ready: " if ok else "native Antigravity ACP unavailable: ")
              + detail, flush=True)
        return 0 if ok else 1
    problem = unavailable_reason()
    if problem:
        # Flushed: the stderr tail becomes the pane's exit reason.
        print(f"corral-light: {problem}", file=sys.stderr, flush=True)
        return 127
    # Owner-only umask so the vendor's transcripts are not group-readable.
    os.umask(PRIVATE_UMASK)
    os.execv(str(BINARY), server_argv())


if __name__ == "__main__":
    raise SystemExit(main())
