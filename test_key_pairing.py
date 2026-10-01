"""DESIGN-6 S7: pairing by touching a key -- the store, the challenges, the
enrollment ceremony and the policy (auth.py, key_cli.py, hub routes).

The authenticator below is a throwaway openssl P-256 key wrapped to answer
create() and get() the way a security key does, built from the S6/S6b test
helpers. Every refusal also asserts that the forbidden side effect did not
happen: no cookie, no key written, no counter moved.
"""
import contextlib
import hashlib
import http.client
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

# Scratch state BEFORE any Corral import, and unconditionally. sessions.STATE
# binds at import, and `import hub` builds a Manager whose restore() reaps
# every pane's recorded adapter group as an "orphan". Against the real state
# that kills the live hub's agents -- it did, twice, on 2026-10-01 (the pane
# running this work died "agent exited rc=0"). setdefault is not enough: a
# shell that exported the real CORRAL_LIGHT_STATE (private-hub recipes do)
# would keep it.
_LIVE_STATE = (Path.home() / ".local/share/corral-light").resolve()
os.environ["CORRAL_LIGHT_STATE"] = tempfile.mkdtemp(prefix="light-key-pairing-")
if Path(os.environ["CORRAL_LIGHT_STATE"]).resolve() == _LIVE_STATE:
    raise SystemExit("refusing: test_key_pairing would run against the live hub's state")

import auth                                                       # noqa: E402
import key_cli                                                    # noqa: E402
from corral_core import webauthn as w
from corral_core.test_webauthn import Key, _binaries
from corral_core.test_webauthn_registration import attestation, auth_data as reg_auth_data

ORIGIN = "http://localhost:18098"
RP = "localhost"
COOKIE = "user.1.session-under-test"
OPENSSL = (_binaries() or [None])[0]


class Authn:
    """A security key: one credential, a counter, a signer."""

    def __init__(self, cred_id, count=0):
        self.key = Key(OPENSSL)
        self.cred = cred_id
        self.count = count

    def create(self, opts, origin=ORIGIN, rp_id=None, label=None):
        cd = json.dumps({"type": "webauthn.create", "challenge": opts["challenge"],
                         "origin": origin, "crossOrigin": False}).encode()
        ad = reg_auth_data(self.key.spki, rp_id=rp_id or opts["rp"]["id"],
                           cred=self.cred, count=self.count)
        body = {"challenge": opts["challenge"], "clientDataJSON": w.b64url(cd),
                "attestationObject": w.b64url(attestation(ad))}
        if label:
            body["label"] = label
        return body

    def get(self, challenge, origin=ORIGIN, rp_id=RP, sign_challenge=None):
        """An assertion. `sign_challenge` puts another challenge in the
        signed clientData while `challenge` is what the request names."""
        self.count += 1
        cd = json.dumps({"type": "webauthn.get", "challenge": sign_challenge or challenge,
                         "origin": origin, "crossOrigin": False}).encode()
        ad = hashlib.sha256(rp_id.encode()).digest() + bytes([w.FLAG_UP | w.FLAG_UV]) \
            + self.count.to_bytes(4, "big")
        sig = self.key.sign(ad + hashlib.sha256(cd).digest())
        return {"challenge": challenge, "id": w.b64url(self.cred),
                "clientDataJSON": w.b64url(cd), "authenticatorData": w.b64url(ad),
                "signature": w.b64url(sig)}

    def close(self):
        self.key.close()


@unittest.skipIf(OPENSSL is None, "no openssl on this machine")
class Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="test-keypair-"))
        self.state = d
        self.patches = [mock.patch.multiple(
            auth, STATE=d, LOCKFILE=d / "pair.lock", PAIRFILE=d / "pairing.json",
            KEYFILE=d / "session.key")]
        # S9: record security banners instead of showing them on this desktop.
        # Each banner also snapshots the ledger's last event at the moment it
        # fires: a banner must follow its ledger line, never precede it.
        self.notices, self.ledger_at_notice = [], []

        def record(t, b, now=None):
            self.notices.append((t, b, now))
            lines = auth.ledger_lines()
            self.ledger_at_notice.append(lines[-1]["event"] if lines else None)
            return True, "shown"
        self.patches.append(mock.patch.object(auth, "NOTIFY", record))
        for p in self.patches:
            p.start()
        self.a = Authn(b"\xaa" * 16)
        self.b = Authn(b"\xbb" * 16)
        self.c = Authn(b"\xcc" * 16)
        self.stderr = io.StringIO()
        self.err_patch = contextlib.redirect_stderr(self.stderr)
        self.err_patch.__enter__()

    def tearDown(self):
        self.err_patch.__exit__(None, None, None)
        for p in self.patches:
            p.stop()
        for k in (self.a, self.b, self.c):
            k.close()

    # ── ceremonies, end to end through auth ──
    def enroll_first(self, key, origin=ORIGIN, rp_id=RP, now=None):
        code, _ = auth.mint_enroll_code(now=now)
        opts = auth.enroll_begin(COOKIE, code, origin, rp_id, now=now)
        return auth.enroll_finish(COOKIE, key.create(opts, origin), origin, rp_id, now=now)

    def begin(self, now=None, code=None):
        if code is None:
            code, _ = auth.new_code(now=now)
        return code, auth.key_begin(code, ORIGIN, RP, now=now)

    def pair_by_key(self, key, now=None):
        _, opts = self.begin(now=now)
        return auth.key_finish(key.get(opts["challenge"]), ORIGIN, RP, now=now)

    def keys(self):
        return auth.load_keys()[0]

    def events(self, name):
        return [e for e in auth.ledger_lines() if e["event"] == name]


class T71_SameAssertionTwice(Base):
    def test_one_pairing_then_refused(self):
        self.enroll_first(self.a)
        _, opts = self.begin()
        body = self.a.get(opts["challenge"])
        self.assertEqual(auth.key_finish(body, ORIGIN, RP), w.b64url(self.a.cred))
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_finish(body, ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.NO_CHALLENGE)
        self.assertEqual(len(self.events("key-pair")), 1)
        self.assertEqual(self.keys()[0]["signCount"], 1)

    def test_finishing_spends_the_pairing_code_too(self):
        self.enroll_first(self.a)
        code, opts = self.begin()
        auth.key_finish(self.a.get(opts["challenge"]), ORIGIN, RP)
        self.assertEqual(auth.approve(code), (False, "unknown or expired code"))


class T711_ConcurrentFinishes(Base):
    def test_two_finishes_behind_a_barrier_mint_once(self):
        self.enroll_first(self.a)
        _, opts = self.begin()
        body = self.a.get(opts["challenge"])
        barrier, results = threading.Barrier(2), []
        real_save = auth._save_keys
        writes = []

        def counted(keys):
            writes.append(1)
            return real_save(keys)

        def run():
            barrier.wait()
            try:
                results.append(auth.key_finish(body, ORIGIN, RP))
            except auth.KeyRefused as e:
                results.append(e.reason)

        with mock.patch.object(auth, "_save_keys", counted):
            ts = [threading.Thread(target=run) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
        self.assertEqual(sorted(results), sorted([w.b64url(self.a.cred), auth.NO_CHALLENGE]))
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(self.events("key-pair")), 1)


class T72_Expiry(Base):
    def test_a_challenge_dies_at_its_ttl(self):
        self.enroll_first(self.a, now=1000)
        _, opts = self.begin(now=1000)
        body = self.a.get(opts["challenge"])
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_finish(body, ORIGIN, RP, now=1000 + auth.CHALLENGE_TTL + 1)
        self.assertEqual(e.exception.reason, auth.NO_CHALLENGE)
        self.assertEqual(self.events("key-pair"), [])
        self.assertEqual(self.keys()[0]["signCount"], 0)

    def test_control_just_inside_the_ttl_finishes(self):
        self.enroll_first(self.a, now=1000)
        _, opts = self.begin(now=1000)
        auth.key_finish(self.a.get(opts["challenge"]), ORIGIN, RP,
                        now=1000 + auth.CHALLENGE_TTL - 1)
        self.assertEqual(len(self.events("key-pair")), 1)


class T73_Cap(Base):
    def test_full_refuses_a_new_begin_and_an_old_one_still_finishes(self):
        self.enroll_first(self.a, now=1000)
        code, _ = auth.new_code(now=1000)
        first = None
        for i in range(auth.MAX_CHALLENGES):
            t = 1000 + (0 if i < auth.MAX_MINTS else auth.MINT_WINDOW + 1)
            opts = auth.key_begin(code, ORIGIN, RP, now=t)
            first = first or opts
        with self.assertRaises(auth.TooMany) as e:
            auth.key_begin(code, ORIGIN, RP, now=1000 + auth.MINT_WINDOW + 2)
        self.assertIn("in flight", str(e.exception))
        self.assertEqual(len(auth._load()["challenges"]), auth.MAX_CHALLENGES)  # none evicted
        auth.key_finish(self.a.get(first["challenge"]), ORIGIN, RP,
                        now=1000 + auth.MINT_WINDOW + 3)
        self.assertEqual(len(self.events("key-pair")), 1)


class T74_RateLimits(Base):
    def test_begin_is_mint_limited(self):
        self.enroll_first(self.a, now=1000)
        code, _ = auth.new_code(now=1000)
        for _ in range(auth.MAX_MINTS):
            auth.key_begin(code, ORIGIN, RP, now=1000)
        with self.assertRaises(auth.TooMany):
            auth.key_begin(code, ORIGIN, RP, now=1000)
        auth.key_begin(code, ORIGIN, RP, now=1000 + auth.MINT_WINDOW + 1)   # window passes

    def test_wrong_codes_at_begin_are_misses(self):
        self.enroll_first(self.a, now=1000)
        for _ in range(auth.MAX_CLAIMS):
            with self.assertRaises(auth.KeyRefused):
                auth.key_begin("ZZZ-ZZZ", ORIGIN, RP, now=1000)
        with self.assertRaises(auth.TooMany):
            auth.key_begin("ZZZ-ZZZ", ORIGIN, RP, now=1000)

    def test_finish_counts_misses_and_then_refuses_even_a_good_one(self):
        self.enroll_first(self.a, now=1000)
        _, opts = self.begin(now=1000)
        for _ in range(auth.MAX_CLAIMS):
            with self.assertRaises(auth.KeyRefused):
                auth.key_finish({"challenge": "nope"}, ORIGIN, RP, now=1000)
        with self.assertRaises(auth.TooMany):
            auth.key_finish(self.a.get(opts["challenge"]), ORIGIN, RP, now=1000)
        self.assertEqual(self.events("key-pair"), [])

    def test_control_one_under_the_cap_a_good_finish_pairs(self):
        self.enroll_first(self.a, now=1000)
        _, opts = self.begin(now=1000)
        for _ in range(auth.MAX_CLAIMS - 1):
            with self.assertRaises(auth.KeyRefused):
                auth.key_finish({"challenge": "nope"}, ORIGIN, RP, now=1000)
        auth.key_finish(self.a.get(opts["challenge"]), ORIGIN, RP, now=1000)


class T75_FirstEnrollment(Base):
    def test_no_code_is_refused_and_nothing_is_written(self):
        for code in (None, "", "WRON-GCOD"):
            with self.assertRaises(auth.KeyRefused) as e:
                auth.enroll_begin(COOKIE, code, ORIGIN, RP)
            self.assertEqual(e.exception.reason, auth.NEEDS_ENROLL_CODE)
        self.assertEqual(self.keys(), [])
        self.assertEqual(auth.policy(), ("code", None))

    def test_a_code_enrolls_and_sets_key_or_code(self):
        r = self.enroll_first(self.a)
        self.assertEqual(r, {"enrolled": w.b64url(self.a.cred), "policy": "key-or-code"})
        k = self.keys()[0]
        self.assertEqual((k["origin"], k["rpId"], k["signCount"]), (ORIGIN, RP, 0))
        self.assertEqual(w.b64url_decode(k["spki"]), self.a.key.spki)
        self.assertEqual(len(self.events("enroll")), 1)

    def test_the_code_is_single_use(self):
        code, _ = auth.mint_enroll_code()
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        auth.enroll_finish(COOKIE, self.a.create(opts), ORIGIN, RP)
        auth.remove_key(w.b64url(self.a.cred))            # back to no keys
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.NEEDS_ENROLL_CODE)

    def test_a_replaced_code_spends_a_begun_ceremony(self):
        code, _ = auth.mint_enroll_code()
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        auth.mint_enroll_code()
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_finish(COOKIE, self.a.create(opts), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.ENROLL_CODE_SPENT)
        self.assertEqual(self.keys(), [])

    def test_another_session_cannot_finish_it(self):
        code, _ = auth.mint_enroll_code()
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_finish("someone.else", self.a.create(opts), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_COOKIE)
        self.assertEqual(self.keys(), [])

    def test_a_bare_key_with_no_attestation_is_refused(self):
        code, _ = auth.mint_enroll_code()
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        body = self.a.create(opts)
        body["attestationObject"] = w.b64url(self.a.key.spki)
        with self.assertRaises(auth.KeyRefused):
            auth.enroll_finish(COOKIE, body, ORIGIN, RP)
        self.assertEqual(self.keys(), [])

    def test_create_excludes_every_key_already_on_the_rp(self):
        self.enroll_first(self.a)
        opts = auth.enroll_begin(COOKIE, None, ORIGIN, RP)
        self.assertEqual(opts["excludeCredentials"], [w.b64url(self.a.cred)])
        self.assertFalse(opts["first"])

    def test_an_explicit_key_only_is_not_weakened_by_enrollment(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        self.enroll_first(self.b, origin="https://hub.example.ts.net", rp_id="hub.example.ts.net")
        self.assertEqual(auth.policy()[0], "key-only")


class T76_LaterEnrollment(Base):
    def setUp(self):
        super().setUp()
        self.enroll_first(self.a)

    def propose(self, key):
        opts = auth.enroll_begin(COOKIE, None, ORIGIN, RP)
        return auth.enroll_finish(COOKIE, key.create(opts), ORIGIN, RP)["approve"]

    def test_without_the_second_touch_nothing_is_enrolled(self):
        txn = self.propose(self.b)
        self.assertEqual(txn["allowCredentials"], [w.b64url(self.a.cred)])
        self.assertEqual(len(self.keys()), 1)

    def test_the_approving_touch_enrolls_exactly_the_proposed_key(self):
        txn = self.propose(self.b)
        r = auth.enroll_approve(COOKIE, self.a.get(txn["challenge"]), ORIGIN, RP)
        self.assertEqual(r["enrolled"], w.b64url(self.b.cred))
        ks = {k["id"]: k for k in self.keys()}
        self.assertEqual(ks[w.b64url(self.b.cred)]["approvedBy"], w.b64url(self.a.cred))
        self.assertEqual(w.b64url_decode(ks[w.b64url(self.b.cred)]["spki"]), self.b.key.spki)
        self.assertEqual(ks[w.b64url(self.a.cred)]["signCount"], 1)

    def test_the_new_key_cannot_approve_itself(self):
        txn = self.propose(self.b)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_approve(COOKIE, self.b.get(txn["challenge"]), ORIGIN, RP)
        self.assertEqual(e.exception.reason, w.UNKNOWN_CREDENTIAL)
        self.assertEqual(len(self.keys()), 1)

    def test_substitution_approval_for_one_proposal_cannot_enroll_another(self):
        t_b, t_c = self.propose(self.b), self.propose(self.c)
        # A approved B's transaction; the request names C's.
        forged = self.a.get(t_c["challenge"], sign_challenge=t_b["challenge"])
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_approve(COOKIE, forged, ORIGIN, RP)
        self.assertEqual(e.exception.reason, w.WRONG_CHALLENGE)
        self.assertEqual([k["id"] for k in self.keys()], [w.b64url(self.a.cred)])
        auth.enroll_approve(COOKIE, self.a.get(t_b["challenge"]), ORIGIN, RP)
        self.assertNotIn(w.b64url(self.c.cred), [k["id"] for k in self.keys()])

    def test_a_tampered_transaction_key_is_refused(self):
        txn = self.propose(self.b)
        d = auth._load()
        d["challenges"][txn["challenge"]]["proposed"]["spki"] = w.b64url(self.c.key.spki)
        auth._save(d)
        with self.assertRaises(auth.KeyRefused):
            auth.enroll_approve(COOKIE, self.a.get(txn["challenge"]), ORIGIN, RP)
        self.assertEqual(len(self.keys()), 1)

    def test_a_pairing_assertion_cannot_approve_an_enrollment(self):
        _, opts = self.begin()
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_approve(COOKIE, self.a.get(opts["challenge"]), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_PURPOSE)
        self.assertEqual(len(self.keys()), 1)

    def test_an_enrollment_approval_cannot_pair(self):
        txn = self.propose(self.b)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_finish(self.a.get(txn["challenge"]), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_PURPOSE)
        self.assertEqual(self.events("key-pair"), [])

    def test_another_session_cannot_approve(self):
        txn = self.propose(self.b)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_approve("other.cookie", self.a.get(txn["challenge"]), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_COOKIE)
        self.assertEqual(len(self.keys()), 1)

    def test_the_cap_is_four(self):
        for k in (self.b, self.c, Authn(b"\xdd" * 16)):
            auth.enroll_approve(COOKIE, self.a.get(self.propose(k)["challenge"]), ORIGIN, RP)
        self.assertEqual(len(self.keys()), auth.MAX_KEYS)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_begin(COOKIE, None, ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.TOO_MANY_KEYS)


class T77_KeyOnly(Base):
    def test_codes_refused_and_break_glass_pairs_and_audits_once(self):
        self.enroll_first(self.a)
        self.assertEqual(auth.set_policy("key-only")[0], True)
        code, _ = auth.new_code()
        ok, msg = auth.approve(code)
        self.assertFalse(ok)
        self.assertIn("key-only", msg)
        self.assertEqual(auth.claim(code), (None, "pending"))
        self.assertEqual(self.events("break-glass"), [])
        ok, _ = auth.approve(code, break_glass=True)
        self.assertTrue(ok)
        tok, status = auth.claim(code)
        self.assertEqual((auth.verify(tok), status), ("craig", "ok"))
        self.assertEqual(len(self.events("break-glass")), 1)

    def test_key_only_needs_a_key(self):
        self.assertEqual(auth.set_policy("key-only")[0], False)
        self.assertEqual(auth.policy()[0], "code")

    def test_the_cli_pair_flag(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        code, _ = auth.new_code()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(key_cli.main(["pair", code]), 2)
            self.assertEqual(key_cli.main(["pair", "--break-glass", code]), 0)
        self.assertIn("break-glass", out.getvalue())
        self.assertEqual(len(self.events("break-glass")), 1)


class T78_Corrupt(Base):
    def test_corrupt_store_is_no_keys_and_loud_and_kept(self):
        self.enroll_first(self.a)
        (self.state / "keys.json").write_text("{not json")
        keys, err = auth.load_keys()
        self.assertEqual(keys, [])
        self.assertIn("corrupt", err)
        self.assertIn("corrupt", self.stderr.getvalue())
        with self.assertRaises(auth.KeyRefused) as e:
            self.begin()
        self.assertEqual(e.exception.status, 503)
        with self.assertRaises(auth.KeyRefused):
            auth.enroll_begin(COOKIE, auth.mint_enroll_code()[0], ORIGIN, RP)
        self.assertEqual((self.state / "keys.json").read_text(), "{not json")

    def test_codes_still_pair_under_key_or_code(self):
        self.enroll_first(self.a)
        (self.state / "keys.json").write_text("[]")
        code, _ = auth.new_code()
        self.assertTrue(auth.approve(code)[0])

    def test_under_key_only_only_break_glass_pairs(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        (self.state / "keys.json").write_text('{"keys": [{"id": 1}]}')
        code, _ = auth.new_code()
        with self.assertRaises(auth.KeyRefused):
            auth.key_begin(code, ORIGIN, RP)
        self.assertFalse(auth.approve(code)[0])
        self.assertTrue(auth.approve(code, break_glass=True)[0])

    def test_a_corrupt_policy_reads_as_key_only(self):
        (self.state / "key-policy.json").write_text('{"policy": "open"}')
        self.assertEqual(auth.policy()[0], "key-only")
        code, _ = auth.new_code()
        self.assertFalse(auth.approve(code)[0])
        self.assertIn("key-policy.json", self.stderr.getvalue())


class T710_FileDiscipline(Base):
    def test_store_and_policy_and_ledger_are_0600(self):
        self.enroll_first(self.a)
        for name in ("keys.json", "key-policy.json", "key-ledger.jsonl"):
            mode = stat.S_IMODE(os.stat(self.state / name).st_mode)
            self.assertEqual(mode, 0o600, name)

    def test_an_interrupted_write_keeps_the_old_store(self):
        self.enroll_first(self.a)
        before = (self.state / "keys.json").read_bytes()
        _, opts = self.begin()
        with mock.patch.object(auth.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                auth.key_finish(self.a.get(opts["challenge"]), ORIGIN, RP)
        self.assertEqual((self.state / "keys.json").read_bytes(), before)
        self.assertEqual([p.name for p in self.state.glob("*.tmp")], [])


class T712_BeginNeedsTheLiveCode(Base):
    def test_no_or_wrong_code_leaks_no_ids(self):
        self.enroll_first(self.a)
        for code in (None, "", "ZZZ-ZZZ", 7):
            with self.assertRaises(auth.KeyRefused) as e:
                auth.key_begin(code, ORIGIN, RP)
            self.assertEqual(e.exception.reason, auth.NEEDS_LIVE_CODE)
            self.assertNotIn(w.b64url(self.a.cred), e.exception.reason)
        self.assertEqual(auth._load().get("challenges"), {})

    def test_an_expired_code_is_not_live(self):
        self.enroll_first(self.a, now=1000)
        code, _ = auth.new_code(now=1000)
        with self.assertRaises(auth.KeyRefused):
            auth.key_begin(code, ORIGIN, RP, now=1000 + auth.CODE_TTL + 1)

    def test_only_keys_for_this_origin_are_offered(self):
        self.enroll_first(self.a, origin="https://hub.example.ts.net", rp_id="hub.example.ts.net")
        code, _ = auth.new_code()
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_begin(code, ORIGIN, RP)
        self.assertEqual((e.exception.reason, e.exception.status), (auth.NO_KEY_FOR_ORIGIN, 404))

    def test_policy_code_offers_no_key_pairing(self):
        self.enroll_first(self.a)
        auth.set_policy("code")
        code, _ = auth.new_code()
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_begin(code, ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.POLICY_CODE)


class T714_VerifierUnavailable(Base):
    def test_key_only_without_openssl_is_break_glass_only_and_list_says_why(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        code, _ = auth.new_code()
        with mock.patch.object(w, "openssl_bin", return_value=None):
            with self.assertRaises(auth.KeyRefused) as e:
                auth.key_begin(code, ORIGIN, RP)
            self.assertEqual(e.exception.status, 503)
            self.assertIn(w.UNAVAILABLE, e.exception.reason)
            self.assertFalse(auth.approve(code)[0])
            self.assertTrue(auth.approve(code, break_glass=True)[0])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                key_cli.main(["key", "list"])
        self.assertIn("unavailable", out.getvalue())
        self.assertIn("--break-glass", out.getvalue())

    def test_codes_still_pair_under_code_and_key_or_code(self):
        with mock.patch.object(w, "openssl_bin", return_value=None):
            for pol in ("code", "key-or-code"):
                (self.state / "key-policy.json").write_text(json.dumps({"policy": pol}))
                code, _ = auth.new_code()
                self.assertTrue(auth.approve(code)[0], pol)


class T715_Recover(Base):
    def test_lost_only_key_end_to_end(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        lost = w.b64url(self.a.cred)
        code, _ = auth.new_code()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(key_cli.main(["key", "recover", code, lost]), 0)
        tok, status = auth.claim(code)
        self.assertEqual((auth.verify(tok), status), ("craig", "ok"))
        self.assertEqual(self.keys(), [])
        self.assertEqual(auth.policy()[0], "code")
        self.assertEqual([e["event"] for e in auth.ledger_lines()][-5:],
                         ["break-glass", "rm", "policy", "enroll-code", "recover"])
        enroll = out.getvalue().split("enrollment code ")[1].split()[0]
        opts = auth.enroll_begin(COOKIE, enroll, ORIGIN, RP)
        r = auth.enroll_finish(COOKIE, self.b.create(opts), ORIGIN, RP)
        self.assertEqual(r, {"enrolled": w.b64url(self.b.cred), "policy": "key-or-code"})

    def test_a_bad_pair_code_removes_nothing(self):
        self.enroll_first(self.a)
        ok, lines = auth.recover("ZZZ-ZZZ", [w.b64url(self.a.cred)])
        self.assertFalse(ok)
        self.assertEqual(len(self.keys()), 1)
        self.assertEqual(self.events("break-glass"), [])


SERVE_ORIGIN, SERVE_RP = "https://hub.example.ts.net", "hub.example.ts.net"


class BindingsTheMutantsFound(Base):
    """Four checks the first mutation run showed no test held (2026-10-01)."""

    def test_a_challenge_finishes_only_at_the_origin_it_was_issued_to(self):
        self.enroll_first(self.a)
        self.enroll_first(self.b, origin=SERVE_ORIGIN, rp_id=SERVE_RP)
        _, opts = self.begin()                                  # issued to localhost
        body = self.b.get(opts["challenge"], origin=SERVE_ORIGIN, rp_id=SERVE_RP)
        with self.assertRaises(auth.KeyRefused) as e:
            auth.key_finish(body, SERVE_ORIGIN, SERVE_RP)      # finished through Serve
        self.assertEqual(e.exception.reason, auth.WRONG_ORIGIN)
        self.assertEqual(self.events("key-pair"), [])
        b = next(k for k in self.keys() if k["id"] == w.b64url(self.b.cred))
        self.assertEqual(b["signCount"], 0)

    def test_a_create_challenge_cannot_approve_and_an_approve_challenge_cannot_create(self):
        self.enroll_first(self.a)
        opts = auth.enroll_begin(COOKIE, None, ORIGIN, RP)      # stage: create
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_approve(COOKIE, self.a.get(opts["challenge"]), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_PURPOSE)
        txn = auth.enroll_finish(COOKIE, self.b.create(opts), ORIGIN, RP)["approve"]
        with self.assertRaises(auth.KeyRefused) as e:          # stage: approve
            auth.enroll_finish(COOKIE, self.c.create({"challenge": txn["challenge"],
                                                      "rp": {"id": RP}}), ORIGIN, RP)
        self.assertEqual(e.exception.reason, auth.WRONG_PURPOSE)
        self.assertEqual([k["id"] for k in self.keys()], [w.b64url(self.a.cred)])

    def test_the_same_authenticator_is_refused_at_finish_not_after_a_second_touch(self):
        self.enroll_first(self.a)
        opts = auth.enroll_begin(COOKIE, None, ORIGIN, RP)
        before = dict(auth._load().get("challenges", {}))
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_finish(COOKIE, self.a.create(opts), ORIGIN, RP)
        self.assertEqual((e.exception.reason, e.exception.status), (auth.ALREADY_ENROLLED, 409))
        after = auth._load().get("challenges", {})
        self.assertFalse(any(v.get("stage") == "approve" for v in after.values()),
                         "a second touch was asked for a key that is already enrolled")
        self.assertEqual(set(after), set(before))
        self.assertEqual(len(self.keys()), 1)

    def test_the_key_cap_is_rechecked_at_finish(self):
        # Three keys on another origin; the first key here begins under the cap,
        # and a fourth lands elsewhere before it finishes.
        def fake(i):
            return {"id": w.b64url(bytes([i]) * 16), "spki": w.b64url(b"\x00" * 91),
                    "signCount": 0, "rpId": SERVE_RP, "origin": SERVE_ORIGIN}
        auth._save_keys([fake(1), fake(2), fake(3)])
        code, _ = auth.mint_enroll_code()
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP)
        auth._save_keys([fake(1), fake(2), fake(3), fake(4)])
        with self.assertRaises(auth.KeyRefused) as e:
            auth.enroll_finish(COOKIE, self.a.create(opts), ORIGIN, RP)
        self.assertEqual((e.exception.reason, e.exception.status), (auth.TOO_MANY_KEYS, 409))
        self.assertNotIn(w.b64url(self.a.cred), [k["id"] for k in self.keys()])
        self.assertEqual(len(self.keys()), 4)


# ── S9: security notices ────────────────────────────────────────────────────

LATE = datetime(2026, 10, 1, 23, 0).timestamp()      # inside quiet hours


@unittest.skipIf(OPENSSL is None, "no openssl on this machine")
class T91_SecurityNotices(Base):
    def titles(self):
        return [t.split(" — ", 1)[1] for t, _, _ in self.notices]

    def test_t91_one_banner_per_event_at_23h(self):
        """Every event that changes who can get in raises exactly one banner
        at a fake 23:00, each after its ledger line; pairing by key and
        minting an enrollment code raise none."""
        code, _ = auth.mint_enroll_code(now=LATE)
        self.assertEqual(self.notices, [], "an enrollment code is ledger-only")
        opts = auth.enroll_begin(COOKIE, code, ORIGIN, RP, now=LATE)
        auth.enroll_finish(COOKIE, self.a.create(opts), ORIGIN, RP, now=LATE)
        # the first enrollment is two events: the key, and code -> key-or-code
        self.assertEqual(self.titles(), ["Security key enrolled", "Pairing policy changed"])
        self.assertIn("shell enrollment code", self.notices[0][1])
        self.assertIn("code -> key-or-code (first enrollment)", self.notices[1][1])

        opts = auth.enroll_begin(COOKIE, None, ORIGIN, RP, now=LATE)
        txn = auth.enroll_finish(COOKIE, self.b.create(opts), ORIGIN, RP, now=LATE)["approve"]
        self.assertEqual(len(self.notices), 2, "a proposed key is not yet an enrollment")
        auth.enroll_approve(COOKIE, self.a.get(txn["challenge"]), ORIGIN, RP, now=LATE)
        self.assertEqual(self.titles()[2:], ["Security key enrolled"])
        self.assertIn("approved by an enrolled key", self.notices[2][1])

        self.pair_by_key(self.a, now=LATE)
        self.assertEqual(len(self.notices), 3, "an ordinary key pairing is ledger-only")
        self.assertEqual(len(self.events("key-pair")), 1)

        auth.set_policy("key-only", now=LATE)
        code, _ = auth.new_code(now=LATE)
        self.assertTrue(auth.approve(code, now=LATE, break_glass=True)[0])
        self.assertTrue(auth.remove_key(w.b64url(self.b.cred), now=LATE)[0])
        self.assertEqual(self.titles()[3:], ["Pairing policy changed", "Break-glass pairing",
                                             "Security key removed"])
        self.assertEqual(len(self.notices), 6)
        self.assertTrue(all(n == LATE for _, _, n in self.notices), "each notice gets the clock")
        # and every banner has its ledger line
        ev = [e["event"] for e in auth.ledger_lines()]
        self.assertEqual([e for e in ev if e in auth.NOTICE_EVENTS],
                         ["enroll", "policy", "enroll", "policy", "break-glass", "rm"])
        self.assertEqual(self.ledger_at_notice,
                         ["enroll", "policy", "enroll", "policy", "break-glass", "rm"],
                         "each banner fires after its own ledger line is on disk")

    def test_recover_banners_its_parts_not_itself(self):
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        self.notices.clear()
        code, _ = auth.new_code()
        ok, _ = auth.recover(code, [w.b64url(self.a.cred)])
        self.assertTrue(ok)
        self.assertEqual(self.titles(), ["Break-glass pairing", "Security key removed",
                                         "Pairing policy changed"])
        self.assertEqual(len(self.events("recover")), 1)

    def test_a_failing_notifier_never_undoes_the_event(self):
        def boom(*a, **k):
            raise RuntimeError("notifier exploded")
        self.enroll_first(self.a)
        auth.set_policy("key-only")
        code, _ = auth.new_code()
        with mock.patch.object(auth, "NOTIFY", boom):
            ok, _ = auth.approve(code, break_glass=True)
        self.assertTrue(ok)
        self.assertEqual(auth.claim(code)[1], "ok")
        self.assertEqual(len(self.events("break-glass")), 1)
        self.assertIn("security notice failed", self.stderr.getvalue())
        with mock.patch.object(auth, "NOTIFY", lambda t, b, now=None: (False, "no notifier")):
            self.assertTrue(auth.set_policy("code")[0])
        self.assertIn("security notice not shown (no notifier)", self.stderr.getvalue())


class T91_TheBannerIsSilentAndLocal(unittest.TestCase):
    """notify.security at 23:00: shown (quiet hours do not hide it), one
    notifier process, no sound, and no network at all -- nothing can page a
    phone. notify.desktop keeps its quiet hours."""

    def run_at_23(self, platform, which):
        import notify
        calls = []
        def run(argv, **k):
            calls.append(argv)
            return mock.Mock(returncode=0, stderr="")
        def no_network(*a, **k):
            raise AssertionError("security notice opened a socket")
        with mock.patch.object(notify.subprocess, "run", run), \
             mock.patch.object(notify.sys, "platform", platform), \
             mock.patch.object(notify.shutil, "which", which), \
             mock.patch("socket.socket", no_network), \
             mock.patch("socket.create_connection", no_network):
            shown = notify.security("Corral Light — Break-glass pairing", "body",
                                    now=datetime(2026, 10, 1, 23, 0))
        return shown, calls

    def test_macos(self):
        shown, calls = self.run_at_23("darwin", lambda n: "/usr/bin/" + n)
        self.assertEqual(shown, (True, "shown"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "osascript")
        self.assertNotIn("sound", " ".join(calls[0]))

    def test_linux(self):
        shown, calls = self.run_at_23(
            "linux", lambda n: "/usr/bin/notify-send" if n == "notify-send" else None)
        self.assertEqual(shown, (True, "shown"))
        self.assertEqual(len(calls), 1)
        self.assertIn("--hint=boolean:suppress-sound:true", calls[0])

    def test_desktop_keeps_quiet_hours(self):
        import notify
        with mock.patch.object(notify.subprocess, "run") as run:
            self.assertEqual(notify.desktop("t", "b", now=datetime(2026, 10, 1, 23, 0)),
                             (False, "quiet hours (21:00–05:00)"))
        run.assert_not_called()

    def test_auth_uses_the_silent_notifier(self):
        import notify
        self.assertIs(auth.NOTIFY, notify.security)


# ── over a real socket, through hub.Handler ─────────────────────────────────

@unittest.skipIf(OPENSSL is None, "no openssl on this machine")
class Routes(Base):
    @classmethod
    def setUpClass(cls):
        import hub
        from http.server import ThreadingHTTPServer
        cls.hub = hub
        cls.origin = f"http://localhost:{hub.PORT}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, method, path, body=None, **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **headers}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        raw = r.read()
        return r.status, json.loads(raw or b"{}"), r.getheader("Set-Cookie"), raw

    def pair_over_http(self, origin, rp_id, **headers):
        st, new, _, _ = self.call("GET", "/api/pair/new", **headers)
        self.assertEqual(st, 200)
        self.assertTrue(new["keyAvailable"])
        self.assertEqual(new["keyOrigin"], origin)      # S8: what the page compares
        st, opts, _, _ = self.call("POST", "/api/pair/key/begin", {"code": new["code"]}, **headers)
        self.assertEqual((st, opts["rpId"]), (200, rp_id), opts)
        return self.call("POST", "/api/pair/key/finish",
                         self.a.get(opts["challenge"], origin=origin, rp_id=rp_id), **headers)

    def test_t79_a_key_minted_cookie_verifies(self):
        self.enroll_first(self.a, origin=self.origin)
        st, body, cookie, _ = self.pair_over_http(self.origin, "localhost")
        self.assertEqual((st, body), (200, {"status": "ok"}))
        tok = cookie.split(";")[0].split("=", 1)[1]
        self.assertEqual(auth.verify(tok), "craig")
        self.assertNotIn("Secure", cookie)
        st, _, cookie2, _ = self.call("POST", "/api/pair/key/finish",
                                      self.a.get("x" * 43, origin=self.origin))
        self.assertEqual((st, cookie2), (403, None))

    def test_t79_through_serve_the_cookie_is_serve_only(self):
        from corral_core import edge
        host = "hub.example.ts.net"
        with mock.patch.object(self.hub, "SERVE_HOST", host):
            self.enroll_first(self.a, origin=f"https://{host}", rp_id=host)
            ts = {"Tailscale-User-Login": "someone@example.com"}
            st, _, cookie, _ = self.pair_over_http(f"https://{host}", host, **ts)
        self.assertEqual(st, 200)
        self.assertIn("Secure", cookie)
        user = auth.verify(cookie.split(";")[0].split("=", 1)[1])
        self.assertEqual(user, edge.SERVE_USER)
        self.assertTrue(edge.audience_ok(user, ts, "127.0.0.1"))
        self.assertFalse(edge.audience_ok(user, {}, "127.0.0.1"))

    def test_t712_begin_without_the_code_leaks_no_ids(self):
        self.enroll_first(self.a, origin=self.origin)
        st, body, _, raw = self.call("POST", "/api/pair/key/begin", {"code": "ZZZ-ZZZ"})
        self.assertEqual((st, body), (403, {"error": auth.NEEDS_LIVE_CODE}))
        self.assertNotIn(w.b64url(self.a.cred).encode(), raw)

    def test_t713_hostile_host_headers_never_move_the_rp(self):
        self.enroll_first(self.a, origin=self.origin)
        code, _ = auth.new_code()
        hostile = {"Host": "evil.example", "X-Forwarded-Host": "evil.example",
                   "Origin": "http://evil.example"}
        st, opts, _, _ = self.call("POST", "/api/pair/key/begin", {"code": code}, **hostile)
        self.assertEqual((st, opts["rpId"]), (200, "localhost"))
        entry = auth._load()["challenges"][opts["challenge"]]
        self.assertEqual((entry["origin"], entry["rpId"]), (self.origin, "localhost"))
        st, _, cookie, _ = self.call(
            "POST", "/api/pair/key/finish",
            self.a.get(opts["challenge"], origin="http://evil.example", rp_id="evil.example"),
            **hostile)
        self.assertEqual((st, cookie), (403, None))

    def test_enroll_routes_need_the_cookie_and_same_origin(self):
        code, _ = auth.mint_enroll_code()
        st, _, _, _ = self.call("POST", "/api/pair/key/enroll/begin", {"code": code})
        self.assertEqual(st, 401)
        tok = auth.mint()
        st, _, _, _ = self.call("POST", "/api/pair/key/enroll/begin", {"code": code},
                                Cookie=f"{self.hub.COOKIE}={tok}",
                                Origin="http://evil.example", Host=f"127.0.0.1:{self.port}")
        self.assertEqual(st, 403)
        st, opts, _, _ = self.call("POST", "/api/pair/key/enroll/begin", {"code": code},
                                   Cookie=f"{self.hub.COOKIE}={tok}")
        self.assertEqual(st, 200)
        st, r, _, _ = self.call("POST", "/api/pair/key/enroll/finish",
                                self.a.create(opts, self.origin),
                                Cookie=f"{self.hub.COOKIE}={tok}")
        self.assertEqual((st, r["policy"]), (200, "key-or-code"))
        st, lst, _, raw = self.call("GET", "/api/pair/key/list",
                                    Cookie=f"{self.hub.COOKIE}={tok}")
        self.assertEqual((st, [k["id"] for k in lst["keys"]]), (200, [w.b64url(self.a.cred)]))
        self.assertEqual(lst["origin"], self.origin)
        self.assertNotIn(b"spki", raw)

    def test_s8_key_origin_is_configuration_not_the_host_header(self):
        """S8: keyOrigin is what the page compares with its own address, so a
        hostile Host or forwarded header must not move it either (T7.13's
        rule, on the route that now reports it)."""
        hostile = {"Host": "evil.example", "X-Forwarded-Host": "evil.example"}
        st, new, _, _ = self.call("GET", "/api/pair/new", **hostile)
        self.assertEqual((st, new["keyOrigin"]), (200, self.origin))
        self.assertFalse(new["keyAvailable"])
        with mock.patch.object(self.hub, "SERVE_HOST", None):
            st, new, _, _ = self.call("GET", "/api/pair/new",
                                      **{"Tailscale-User-Login": "someone@example.com"})
        self.assertEqual((st, new["keyOrigin"], new["keyAvailable"]), (200, None, False))

    def test_serve_without_a_configured_host_refuses(self):
        with mock.patch.object(self.hub, "SERVE_HOST", None):
            st, body, _, _ = self.call("POST", "/api/pair/key/begin", {"code": "x"},
                                       **{"Tailscale-User-Login": "someone@example.com"})
        self.assertEqual(st, 403)
        self.assertIn("CORRAL_LIGHT_SERVE_HOST", body["error"])


if __name__ == "__main__":
    unittest.main()
