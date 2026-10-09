# Delegates module, Phase 0 results (partial, 2026-10-09)

Source: cc-handoff task
`2026-10-09T182353Z_delegates-module-phase-0-anonymized-delegates-stat-3072`,
answered by ranch-server's auto-triage in 44 s. **Read from code, not
measured**: the triage run had no shell, so the JSON sample is synthetic,
built from `status.py`'s own row construction, and anonymized by
construction. Measured items 0(b) to 0(d) are not done yet.

## 0(a) The ranch's delegates tooling

**Today's output is empty.** `delegates/charters/` has no live charters,
and `collect()` globs only `charters/*.md` (archived charters are not
read). A fixture must come from `delegates/selftest_status.py`, whose
states are `live-1`, `overdue-1`, `expired-1`, `revoked-1` and
`unapproved-1`, or from the synthetic sample.

**Charters are signed with OpenSSH, not gpg.** Plan rev 1 §1 said gpg; it
took that from a stale docstring in full Corral's `delegates.py`. The
signature is a detached PEM `SSH SIGNATURE` at `<name>.md.sig`, made with
`sk-ssh-ed25519` (a hardware key), hash `sha512`, and believed to be in
the namespace `cc-handoff` (read from base64 by eye; confirm with
`ssh-keygen -Y check-novalidate`). Verification is `ssh-keygen -Y
find-principals` then `-Y verify` against `cc-handoff/allowed_signers`.
**Consequence:** the module can verify ranch charters locally with the
same mechanism as its own, if the namespace is a per-charter-folder
setting rather than fixed. Plan Q4 (gpg) is moot.

**Row shape** (per delegate): `name`, `charter`, `state`, `signed`,
`sig_ok`, `sig_detail`, `approval_signed`, `expires` (raw string or null),
`expires_unparseable`, `hours_left` (null, or negative once expired),
`data_class`, `capabilities`, `budget_usd`, `spend.box` (`amount_usd`,
`bundle_id`, `as_of`, `note`), `spend.credentials[]` (`type`,
`vault_file`, `amount_usd`, `note`), `box` (`tier`, `ip`, `domain`,
`manifest_key`, or null), `active`, `audit_events`, `last_event`. Board
level: `unattributed_audit`, `gcp_visible`, `generated_at`. Rows are sorted
worst first. Types of `capabilities` and `budget_usd` are unconfirmed.

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
`proposed`, `>` or `|` means not approved. There is no `status:` key.
Companion files (`<name>.manifest.md`, `<name>.policy_v1.json`) sit
beside each charter, and the name regex `^[a-z][a-z0-9-]{0,60}$` excludes
them.

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
(rule 2), which is what the panel asked for (synthesis: "a box listed
with no currently valid grant is the alarm"). It does not cover an
unapproved charter whose box is live: rule 3 returns `LIVE` whenever the
signature is good, whatever `approval` says. Rev 2 should not copy that.

**Cost and network:** two `ssh-keygen` runs per signed charter, plus one
`gcp/list.py --json` with a 15 s timeout. `status.py` and `spend.py` make
no network calls. `gcp/list.py` is described as local-only and not yet
confirmed. A missing `ACTIVE-BOXES.md` exits 2. A failed `gcp/list.py`
sets `gcp_visible: false` and writes `WARN:` to stderr, so a converter
must read stdout only.

## Still to do

- 0(a): a measured run on ranch-server (`time status.py --json`, the real
  value types, the namespace check). This needs a human or an authorized
  agent with a shell, not auto-triage.
- 0(b) to 0(d), on this host: `ssh-keygen -Y verify` inside the collector
  sandbox (including an `sk-ssh-ed25519` signature, which needs no
  hardware to verify); a config-folder write being seen on the next run;
  the aggregate notice.
