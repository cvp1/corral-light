# Delegates module, Phase 0 results (2026-10-09)

Two passes. The first (18:31Z) came from cc-handoff task
`2026-10-09T182353Z_delegates-module-phase-0-anonymized-delegates-stat-3072`,
answered by ranch-server's auto-triage in 44 s: **read from code, not
measured**, because the triage run had no shell. The second (2026-10-10,
00:58Z to 01:20Z) was **measured**: an operator-authorised agent ran the
read-only commands over ssh on ranch-server (user `cvande`, `~/Github/CC`)
and on camano, outside the mailbox. The typed `delegates-status` verb that
would do this through the mailbox is staged in camano's cc-handoff clone
and waits for a signed commit; the direct run is recorded here so the plan
need not wait for it.

## 0(a) The ranch's delegates tooling

**Measured run.** `time python3 delegates/status.py --json` in
`~/Github/CC`:

| Item | Value |
|---|---|
| exit | 0 |
| wall | 0.13 s |
| stdout | 115 bytes |
| stderr | none (the three stderr lines were `time`'s own) |

Output, verbatim:

```json
{
 "delegates": [],
 "unattributed_audit": [],
 "gcp_visible": true,
 "generated_at": "2026-10-09T17:58:39-0700"
}
```

**Today's board is empty**, as the code reading predicted. `charters/`
holds only `archive/<name>/` folders (cal-proof-1, chat-1, gce-chat-1,
shell-proof-1, each with a `.md.sig`; llm-proof-1 unsigned), and
`collect()` globs `charters/*.md` only. A fixture must come from the
selftest or from `archive/`.

**Fixture, captured.** `delegates/selftest_status.py`'s fixture arm was
run with its output kept: `docs/fixtures/ranch-status-fixture.json`
(16 rows, 12.5 KB). Fixture names only; no real delegate appears in it.
With `DELEGATES_SKIP_VERIFY=refuse`, every signed fixture that is not
`OVERDUE-REVOKE`, `REVOKED`, `EXPIRED` or `DRAFTED` reads `BAD-SIG`, so the
fixture covers the state order but **not** a passing `LIVE`, `READY` or
`UNAPPROVED`; those need a real signature, which the selftest deliberately
cannot fake. States in the capture: `OVERDUE-REVOKE`, `BAD-SIG` (×12),
`REVOKED`, `EXPIRED`, `DRAFTED`.

**Value types, measured** (the plan's "unconfirmed" items):

| Field | Type |
|---|---|
| `name`, `charter`, `state`, `sig_detail`, `data_class` | str |
| `signed`, `sig_ok`, `approval_signed`, `expires_unparseable`, `active` | bool |
| `expires` | str (`2026-01-01T00:00:00Z`), or null |
| `hours_left` | float (`-6769.0`, `168.0`), or null |
| `capabilities` | the raw front-matter value: null in the fixture; a string such as `[llm.chat]` for a real charter (`status.py:349` does not parse it) |
| `budget_usd` | **str** (`"5"`), not a number (`status.py:350`) |
| `spend.box` | dict: `amount_usd` (float or null), `bundle_id`, `as_of`, `note` |
| `spend.credentials` | list of dicts |
| `box` | null, or dict: `tier`, `ip`, `domain` (`lightsail`/`gcp`), `manifest_key` |
| `audit_events` | int |
| `last_event` | str (`revoke 2026-08-16…`), or null |
| board: `unattributed_audit` | list of str (file names) |
| board: `gcp_visible` | bool |
| board: `generated_at` | str, `%Y-%m-%dT%H:%M:%S%z`, `-0700` with no colon |

**What `status.py --json` is.** One row **per charter**, with the box
joined in from `ACTIVE-BOXES.md` or `gcp/list.py`. It is **not a box
inventory**: a box whose charter is deleted leaves the output. That is why
the plan's converter never claims `complete` (plan §4.3). `gcp/list.py`
reads `boxes/<name>.json` manifests
with `status == "live"`; its `--reconcile` mode, which does talk to GCP, is
a separate entry point the converter does not use.

**Namespace confirmed.** The four archived `.sig` files, header parsed:

| File | Namespace | Hash | Key type |
|---|---|---|---|
| cal-proof-1, chat-1, gce-chat-1, shell-proof-1 | `cc-handoff` | `sha512` | `sk-ssh-ed25519@openssh.com` |

`cc-handoff/allowed_signers` holds six `craig@fleet` lines: two
`ssh-ed25519`, one `sk-ssh-ed25519@openssh.com`, three
`sk-ecdsa-sha2-nistp256@openssh.com`. ranch-server's `ssh-keygen` is
OpenSSH 9.6p1 (Ubuntu).

**Charters are signed with OpenSSH, not gpg.** Plan rev 1 §1 said gpg; it
took that from a stale docstring in full Corral's `delegates.py`.
Verification is `ssh-keygen -Y find-principals` then `-Y verify` against
`cc-handoff/allowed_signers`. **Consequence:** the module can verify ranch
charters locally with the same mechanism as its own, with the namespace a
per-charter-folder setting. Plan Q4 (gpg) is moot.

**Time formats.** `generated_at` is `%Y-%m-%dT%H:%M:%S%z`, giving `-0700`
with no colon (the host is in America/Phoenix). `expires` has appeared as
both `YYYY-MM-DDTHH:MM:SSZ` and a bare `YYYY-MM-DD`, and the bare date means
the end of that UTC day. The plan's rule "no time zone → BAD-CHARTER"
would wrongly reject every ranch charter that uses a bare date.

**Charter keys in use:** `name`, `mission`, `created`, `expires`,
`data-class`, `capabilities` (`[a, b]`), `payload`, `tools`, `budget-usd`,
`credential-N-type` and `credential-N-vault-file` (N up to 4),
`payload-files`, `ssh-key-file`, `approval`. Keys use hyphens, and values
can be bracketed lists. `approval:` of `""`, `unsigned`, `pending`,
`proposed`, `>` or `|` means not approved (the archived chat-1 charter
notes that `lightsail/gate.py` refuses the block markers as approval
values). There is no `status:` key. Companion files (`<name>.manifest.md`,
`<name>.policy_v1.json`) sit beside each charter, and the name regex
`^[a-z][a-z0-9-]{0,60}$` excludes them.

**State derivation** (`status.py:88`, `:256`, in order, worst first:
`OVERDUE-REVOKE`, `BAD-SIG`, `LIVE`, `REVOKED`, `EXPIRED`, `UNAPPROVED`,
`READY`, `DRAFTED`):

1. Box live and past `expires` → `OVERDUE-REVOKE`, which beats
   everything.
2. Box live, in date, signature missing or bad → `BAD-SIG`.
3. Box live, signature good → `LIVE`.
4. No box, and the last lifecycle audit event is a revoke → `REVOKED`.
5. No box, past `expires` → `EXPIRED`.
6. No box, `.sig` present and failing → `BAD-SIG`.
7. Signature good, `approval` unsigned → `UNAPPROVED`.
8. Signed and approved → `READY`.
9. No `.sig` → `DRAFTED`.

An unparseable `expires` is never treated as expired. "Live" means a
row with `tier == "delegate"` in `lightsail/ACTIVE-BOXES.md` or in
`gcp/list.py --json`, keyed `<name>-node` first, then `<name>`. **Revoked
comes from audit events, not from the charter**: a revoke audit file
later than the latest provision, enroll or deliver event.

The ranch already treats a live box with no good signature as an alarm
(rule 2), which is what the panel asked for. It does not cover an
unapproved charter whose box is live: rule 3 returns `LIVE` whenever the
signature is good, whatever `approval` says. The plan does not copy that.

**Cost and network:** two `ssh-keygen` runs per signed charter, plus one
`gcp/list.py --json` with a 15 s timeout. `status.py` and `spend.py` make
no network calls. A missing `ACTIVE-BOXES.md` exits 2. A failed
`gcp/list.py` sets `gcp_visible: false` and writes `WARN:` to stderr, so a
converter must read stdout only.

## 0(b) Signature verification inside the collector sandbox, on this host

Measured on camano (OpenSSH 10.5p1), using the real `module_sandbox.build_argv`
from this clone with a temp config folder bound read-only and a temp data
folder as `HOME`. Fixture: the archived `chat-1.md` and its `.sig`
(sha256 `ee1c72fa…3802`, identical on both hosts) and the one
`sk-ssh-ed25519@openssh.com` line from `allowed_signers`, written as
`craig@fleet namespaces="cc-handoff" <key>`. The charter copies were deleted
after the run.

| Case | Result |
|---|---|
| valid, `-n cc-handoff -I craig@fleet` | exit 0, `Good "cc-handoff" signature for craig@fleet with ED25519-SK key` |
| wrong namespace (`corral-light-delegate-charter`) | exit 255, `namespace does not match` |
| one body byte changed | exit 255, `incorrect signature` |
| one front-matter byte changed (`expires` year) | exit 255, `incorrect signature` |
| wrong principal (`nobody@fleet`) | exit 255, `Could not verify signature.` |
| `allowed_signers` line **without** `namespaces=` | **exit 0, verifies** |
| `$HOME` inside the sandbox | the data dir; `~/.ssh` does not exist |

Findings for the plan:

- A hardware-key (`sk-`) signature verifies with no hardware present, as
  expected, inside the real sandbox. Phase 0b is done.
- **`namespaces=` is not enforced by `ssh-keygen` when absent.** A signers
  line with no option accepts the signature for any namespace. The module
  must write the option in `setup` and refuse a line without it
  (plan §4.1). The plan's test "`allowed_signers` without `namespaces=`"
  now has an expected result: `bad-sig: cannot verify`, decided by the
  module, not by `ssh-keygen`.
- `-I` with a principal that has no line gives a bare `Could not verify
  signature.` with no reason; the module should say "principal not in
  allowed_signers" itself.

## 0(c), 0(d)

Not run. Both need a collector and a notice to exist: a file renamed into
the inbox being seen on the next run, and the alarm notice returning after
"Not now" when the alarm set changes. They are now part of Phase 1's
done-when (plan §11). The core's own behaviour they rest on is in
`test_modules.py` (89 tests) and `static/app.js:3249` (dismissal key).

## Still to do

- Land the `delegates-status` verb (staged in camano's cc-handoff clone:
  `recipes/ranch-server/delegates-status` and its `ACL.json` line) with a
  signed commit, so future measurements go through the mailbox.
- A fixture with a **passing** signature for `LIVE`, `READY` and
  `UNAPPROVED`: either an archived charter signed by the operator, placed
  in a fixture tree with `allowed_signers`, or a new throwaway key the
  module repository owns.
