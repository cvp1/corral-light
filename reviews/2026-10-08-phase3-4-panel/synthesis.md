# Phases 3 and 4: quick code review (2026-10-08)

Arms, cold, one read-only folder holding both repositories at what was
live (Light `5b931e7`, FinOps `ff5ab56`), via `corral-light consult ask`.
Charges: `charge-p3-notices.md`, `charge-p4-billing.md`.

| Phase | Arm | Model | Verdict | File |
|---|---|---|---|---|
| 3, notices | Codex | gpt-6-astra (read-only mode) | FIX-FIRST | p3-notices-astra.md |
| 3, notices | Grok | grok-4.6 | FIX-FIRST | p3-notices-grok.md |
| 3, notices | Gemini | gemini-pro-agent | FIX-FIRST | p3-notices-gemini.md |
| 4, billing | Codex | gpt-6-astra (read-only mode) | FIX-FIRST | p4-billing-astra.md |
| 4, billing | Grok | grok-4.6 | FIX-FIRST | p4-billing-grok.md |
| 4, billing | Gemini | gemini-pro-agent | FIX-FIRST | p4-billing-gemini.md |

Every finding below was checked by the author against the code before it
was accepted or rejected. Each accepted one has a test that fails without
its fix.

## Phase 3, accepted

1. **A current `allowed_warning` with no percent raised nothing (Astra,
   Grok, Gemini).** Checked: `_level_for` returned `info` with no percent,
   and the status only lifted `ok`. Grok added the mixed case: a fresh
   `rejected` over a stale percent took the stale branch. Fix: a status the
   report kept (it clears a stale one) sets the level after the percent.
2. **An open tab never dropped expired notices (Gemini).** Checked: the
   page fetched `moduleNotices` only with the full state, after an action
   or a reconnect. Fix: `/api/modules` carries the notices and the page
   asks once a minute while visible.
3. **Read-time expiry trusted the installed manifest (Astra).** Checked:
   `notices()` re-read `module.json` from the generation, which can change
   after the digest check. Fix: each good run records the verified copy's
   opt-in and period in its status; reads use only those.
4. **The page capped before Not now (Gemini).** Checked: `slice(0, 8)` ran
   first, so hiding a card did not let the next in. Fix: the hub sends up
   to 24 (three per module); the page applies Not now, then draws eight
   and counts the rest.
5. **Stored notices were not checked again at read (Grok).** Fix: id and
   title are validated again in `notices()`.
6. **Two accounts gave identical cards (Grok).** Fix: labels that collide
   carry a short piece of the fingerprint, tile and card alike.
7. **A notice could lack its tile past four windows (Gemini).** Fix:
   windows that raise a notice sort first and always get a tile.
8. **Two weak tests (Astra, Grok).** The card test used a stand-in for
   `el()`, and "never in the hot count" grepped source text. Both now check
   behaviour: `el()` writes `textContent` only, `blockedCount` stays 0
   beside notices, and the poll is pinned.

## Phase 3, rejected

- **"A notice id embeds a credential path" (Gemini, high).** Checked: the
  account part is `claude:<fingerprint>`, a 64-character hash from the
  feed's quota keys; no path reaches it.
- **"The per-module cap drops a fourth `bad` for another module's
  `info`" (Gemini).** By design (§4.7): three per module keeps one module
  from filling the rail; the dropped ones are counted.

## Phase 4, accepted

1. **Refused proxy targets were stored verbatim (Astra, high).** Checked:
   `on_host` kept the requested host text, so a fetcher could CONNECT to
   its key and have it printed by `module fetch`. Fix: only names from the
   grant's own list are kept; refusals are a count.
2. **`\uXXXX` escapes passed the key check (Astra, Grok, high).** Checked:
   needles ran on raw stdout, then `json.loads` rebuilt the key in the
   stored object. Fix: the parsed object is checked again in canonical
   form; needles gain URL-safe base64 and hex, and always include the key
   itself whatever its length. Keys under 16 characters are refused.
3. **Revoke raced a fetch in flight (Astra).** Fix: grant and revoke take
   the module lock, so a result cannot land after a revoke.
4. **The key file could be swapped between check and bind (Grok).** Fix:
   the key is read through an `O_NOFOLLOW` descriptor, checked on that
   descriptor (hard links refused), and the run binds a private copy.
5. **A malformed or empty response could erase complete days (Astra,
   Grok).** Fix: OpenAI results without an amount fail the fetch; an empty
   fetch keeps earlier days in its range and says so.
6. **Float amounts lost cents (Astra).** Fix: JSON is parsed with
   `parse_float=Decimal`.
7. **Update recovery reported success it did not have (Astra, Gemini).**
   Fix: recovery reinstalls clean adapters, then patches, and says plainly
   when that fails too.
8. **Smaller ones.** Billed months now follow UTC days (Grok); the xAI
   team id must be `[A-Za-z0-9_-]` and is quoted (Grok); Google's query
   treats a NULL cost as 0 (Gemini); a truncated key PEM is a ValueError
   (Gemini); the key folder is chmod-ed through a descriptor (Gemini).

## Phase 4, rejected

- **"Needles miss a service account's `private_key` alone" (Gemini,
  high).** Checked: `needles()` already adds `private_key`,
  `private_key_id`, their PEM lines and encodings;
  `test_needles_cover_raw_lines_json_and_base64` pins it.
- **"`range_start` is overwritten, burying older days" (Gemini).**
  Checked: the range in `billed_fetch` is informational; days outside a
  fetch's range stay in `billed_day` and are summed by day.

## Built afterwards

Astra's closing sentence: move credential handling into a trusted core
client so module code never holds a reusable secret. Built the same day
(plan §6.7.2): the hub's fetch proxy adds each credential and the module
never holds one. What follows was the reasoning before that. The key checks catch
plain and common encodings only; module code that holds a key can always
encode it some other way. The plan accepts that a fetcher, being pinned
and verified code the operator chose to grant a key to, holds the key
inside the sandbox, with egress only to that key's own vendor. A core-side
billing client is the stronger design and is listed for a later phase.
