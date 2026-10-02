"""The WebAuthn assertion verifier (corral_core/webauthn.py).

The golden vector is built here with a throwaway openssl P-256 key; no key
material is checked in.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from corral_core import webauthn as w

HERE = Path(__file__).resolve().parent
REAL_VECTOR = HERE / "testdata" / "webauthn-assertion.json"
RP_ID = "localhost"
ORIGIN = "http://localhost:18098"
CHALLENGE = bytes(range(32))
CRED_ID = b"\x11" * 16


def _run(argv, **kw):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def _binaries():
    """Every distinct openssl here: the first on PATH and the system one."""
    seen, out = set(), []
    for b in (shutil.which("openssl"), w.SYSTEM_OPENSSL):
        if b and os.path.isfile(b) and os.path.realpath(b) not in seen:
            seen.add(os.path.realpath(b))
            out.append(b)
    return out


class Key:
    """A throwaway P-256 key, its SPKI DER, and a signer."""

    def __init__(self, openssl):
        self.openssl = openssl
        self.dir = tempfile.mkdtemp(prefix="test-webauthn-")
        self.pem = os.path.join(self.dir, "key.pem")
        _run([openssl, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", self.pem])
        der = os.path.join(self.dir, "pub.der")
        _run([openssl, "ec", "-in", self.pem, "-pubout", "-outform", "DER", "-out", der])
        self.spki = Path(der).read_bytes()

    def sign(self, message):
        m, s = os.path.join(self.dir, "m"), os.path.join(self.dir, "s")
        Path(m).write_bytes(message)
        _run([self.openssl, "dgst", "-sha256", "-sign", self.pem, "-out", s, m])
        return Path(s).read_bytes()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def client_data(**over):
    d = {"type": "webauthn.get", "challenge": w.b64url(CHALLENGE), "origin": ORIGIN,
         "crossOrigin": False}
    d.update(over)
    return json.dumps({k: v for k, v in d.items() if v is not None}).encode()


def auth_data(flags=w.FLAG_UP | w.FLAG_UV, count=5, rp_id=RP_ID):
    return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")


class Golden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bins = _binaries()
        if not cls.bins:
            raise unittest.SkipTest("no openssl on this machine")
        cls.key = Key(cls.bins[0])

    @classmethod
    def tearDownClass(cls):
        cls.key.close()

    def assertion(self, cd=None, ad=None, sign_cd=None, sign_ad=None, stored=4, sig=None,
                  cred=CRED_ID, origins=(ORIGIN,), challenge=CHALLENGE, rp_id=RP_ID, **kw):
        cd = client_data() if cd is None else cd
        ad = auth_data() if ad is None else ad
        if sig is None:
            sig = self.key.sign((sign_ad or ad) + hashlib.sha256(sign_cd or cd).digest())
        creds = {w.b64url(CRED_ID): {"spki": self.key.spki, "signCount": stored}}
        return w.verify_assertion(client_data_json=cd, auth_data=ad, signature=sig,
                                  credential_id=cred, challenge=challenge, origins=origins,
                                  rp_id=rp_id, credentials=creds, **kw)


class T61_TheVectorVerifies(Golden):
    def test_golden_assertion_verifies(self):
        self.assertEqual(self.assertion(), (True, "ok"))

    def test_spki_stored_as_base64url_also_verifies(self):
        cd, ad = client_data(), auth_data()
        sig = self.key.sign(ad + hashlib.sha256(cd).digest())
        creds = {w.b64url(CRED_ID): {"spki": w.b64url(self.key.spki), "signCount": 0}}
        self.assertEqual(w.verify_assertion(
            client_data_json=cd, auth_data=ad, signature=sig, credential_id=CRED_ID,
            challenge=CHALLENGE, origins=[ORIGIN], rp_id=RP_ID, credentials=creds), (True, "ok"))

    def test_first_use_with_zero_counter_both_sides(self):
        # Many authenticators never count: stored 0 and new 0 is allowed.
        self.assertEqual(self.assertion(ad=auth_data(count=0), stored=0), (True, "ok"))

    def test_real_key_vector(self):
        if not REAL_VECTOR.exists():
            self.skipTest("missing the real-key assertion vector "
                          "corral_core/testdata/webauthn-assertion.json (captured in live L-C)")
        v = json.loads(REAL_VECTOR.read_text(encoding="utf-8"))
        d = w.b64url_decode
        ok = w.verify_assertion(
            client_data_json=d(v["clientDataJSON"]), auth_data=d(v["authenticatorData"]),
            signature=d(v["signature"]), credential_id=d(v["credentialId"]),
            challenge=d(v["challenge"]), origins=[v["origin"]], rp_id=v["rpId"],
            credentials={v["credentialId"]: {"spki": v["spki"], "signCount": 0}})
        self.assertEqual(ok, (True, "ok"))


class T62_EachRefusalHasItsOwnReason(Golden):
    def refusals(self):
        other = Key(self.bins[0])
        self.addCleanup(other.close)
        good_sig = self.key.sign(auth_data() + hashlib.sha256(client_data()).digest())
        return {
            "type": self.assertion(cd=client_data(type="webauthn.create")),
            "challenge": self.assertion(cd=client_data(challenge=w.b64url(b"x" * 32))),
            "origin 127.0.0.1": self.assertion(cd=client_data(origin="http://127.0.0.1:18098")),
            "origin other port": self.assertion(cd=client_data(origin="http://localhost:18099")),
            "origin https swap": self.assertion(cd=client_data(origin="https://localhost:18098")),
            "cross-origin": self.assertion(cd=client_data(crossOrigin=True)),
            "rpIdHash": self.assertion(ad=auth_data(rp_id="evil.example")),
            "UP": self.assertion(ad=auth_data(flags=w.FLAG_UV)),
            "UV": self.assertion(ad=auth_data(flags=w.FLAG_UP)),
            "signCount same": self.assertion(stored=5),
            "signCount back": self.assertion(stored=9),
            "unknown credential": self.assertion(cred=b"\x22" * 16),
            "other bytes": self.assertion(sig=good_sig, ad=auth_data(count=6)),
            "other key": self.assertion(sig=other.sign(auth_data() + hashlib.sha256(client_data()).digest())),
            "malformed DER": self.assertion(sig=good_sig[:-1]),
            "short authData": self.assertion(ad=auth_data()[:36]),
            "not JSON": self.assertion(cd=b"{not json"),
            "over cap": self.assertion(cd=client_data(pad="x" * w.MAX_CLIENT_DATA)),
        }

    def test_every_case_is_refused_with_the_named_reason(self):
        got = self.refusals()
        want = {
            "type": w.WRONG_TYPE, "challenge": w.WRONG_CHALLENGE,
            "origin 127.0.0.1": w.BAD_ORIGIN, "origin other port": w.BAD_ORIGIN,
            "origin https swap": w.BAD_ORIGIN, "cross-origin": w.CROSS_ORIGIN,
            "rpIdHash": w.RPID_MISMATCH, "UP": w.NO_UP, "UV": w.NO_UV,
            "signCount same": w.SIGNCOUNT_REGRESSED, "signCount back": w.SIGNCOUNT_REGRESSED,
            "unknown credential": w.UNKNOWN_CREDENTIAL, "other bytes": w.BAD_SIGNATURE,
            "other key": w.BAD_SIGNATURE, "malformed DER": w.MALFORMED_DER,
            "short authData": w.SHORT_AUTH_DATA, "not JSON": w.NOT_JSON, "over cap": w.OVER_CAP,
        }
        for case, reason in want.items():
            self.assertEqual(got[case], (False, reason), case)

    def test_the_reasons_are_distinct(self):
        reasons = [w.WRONG_TYPE, w.WRONG_CHALLENGE, w.BAD_ORIGIN, w.CROSS_ORIGIN,
                   w.RPID_MISMATCH, w.NO_UP, w.NO_UV, w.SIGNCOUNT_REGRESSED,
                   w.UNKNOWN_CREDENTIAL, w.BAD_SIGNATURE, w.MALFORMED_DER,
                   w.SHORT_AUTH_DATA, w.NOT_JSON, w.OVER_CAP, w.UNAVAILABLE]
        self.assertEqual(len(set(reasons)), len(reasons))
        self.assertNotIn("ok", reasons)

    def test_a_single_origin_string_is_not_a_substring_list(self):
        # `"http://localhost:1" in "http://localhost:18098"` is True for str.
        self.assertEqual(self.assertion(cd=client_data(origin="http://localhost:1"),
                                        origins=ORIGIN), (False, w.BAD_ORIGIN))
        self.assertEqual(self.assertion(origins=ORIGIN), (True, "ok"))

    def test_no_allowed_origins_refuses(self):
        self.assertEqual(self.assertion(origins=()), (False, w.BAD_ORIGIN))

    def test_empty_challenge_never_matches(self):
        self.assertEqual(self.assertion(cd=client_data(challenge=""), challenge=b""),
                         (False, w.WRONG_CHALLENGE))

    def test_clientdata_missing_fields(self):
        cd = json.dumps({"type": "webauthn.get", "challenge": w.b64url(CHALLENGE)}).encode()
        ok, why = self.assertion(cd=cd)
        self.assertFalse(ok)
        self.assertIn("origin", why)
        self.assertEqual(self.assertion(cd=b"[1, 2]"), (False, w.NOT_JSON))

    def test_signature_is_over_the_hash_of_these_exact_client_bytes(self):
        # Same JSON meaning, different bytes: the signature must not carry over.
        cd = client_data()
        spaced = cd.replace(b",", b", ")
        self.assertNotEqual(cd, spaced)
        self.assertEqual(self.assertion(cd=spaced, sign_cd=cd), (False, w.BAD_SIGNATURE))


class DerShape(unittest.TestCase):
    def test_strict_der(self):
        ok = bytes.fromhex("3006020101020101")
        self.assertTrue(w.der_signature_ok(ok))
        bad = {
            "trailing": ok + b"\x00",
            "junk inside the sequence": bytes.fromhex("300702010102010100"),
            "wrong tag": b"\x31" + ok[1:],
            "length lies": b"\x30\x07" + ok[2:],
            "negative r": bytes.fromhex("3006020181020101"),
            "non-minimal r": bytes.fromhex("300702020001020101"),
            "zero-length r": bytes.fromhex("30050200020101"),
            "one int": bytes.fromhex("3003020101"),
            "raw r||s": b"\x01" * 64,
            "not bytes": "3006020101020101",
        }
        for case, sig in bad.items():
            self.assertFalse(w.der_signature_ok(sig), case)

    def test_a_high_bit_integer_needs_its_zero(self):
        self.assertTrue(w.der_signature_ok(bytes.fromhex("300702020081020101")))


class ParseAuthData(unittest.TestCase):
    def test_fields(self):
        ad = w.parse_auth_data(auth_data(flags=0x45, count=258) + b"ext")
        self.assertEqual(ad["signCount"], 258)
        self.assertTrue(ad["up"] and ad["uv"] and ad["at"])
        self.assertFalse(ad["ed"])
        self.assertEqual(ad["rest"], b"ext")

    def test_exactly_37_is_enough(self):
        self.assertEqual(w.parse_auth_data(b"\x00" * 37)["signCount"], 0)
        with self.assertRaises(w.Refused):
            w.parse_auth_data(b"\x00" * 36)


class T64_CoseToSpki(unittest.TestCase):
    """Round-trips on the first openssl on PATH AND on the system one."""

    def test_round_trip_on_every_openssl(self):
        bins = _binaries()
        if not bins:
            self.skipTest("no openssl on this machine")
        self.assertTrue(any(os.path.realpath(b) == os.path.realpath(w.SYSTEM_OPENSSL)
                            for b in bins) or not os.path.exists(w.SYSTEM_OPENSSL))
        for b in bins:
            with self.subTest(openssl=b):
                k = Key(b)
                self.addCleanup(k.close)
                self.assertTrue(k.spki.startswith(w.SPKI_PREFIX + b"\x04"), b)
                pt = k.spki[len(w.SPKI_PREFIX) + 1:]
                cose = {1: 2, 3: -7, -1: 1, -2: pt[:32], -3: pt[32:]}
                spki = w.cose_to_spki(cose)
                self.assertEqual(spki, k.spki)
                # ...and every binary verifies a signature against it.
                msg = b"authData" + hashlib.sha256(b"clientData").digest()
                sig = k.sign(msg)
                for v in bins:
                    self.assertEqual(w.verify_signature(spki, msg, sig, openssl=v), (True, "ok"), v)
                    self.assertEqual(w.verify_signature(spki, msg + b"!", sig, openssl=v),
                                     (False, w.BAD_SIGNATURE), v)

    def test_refusals(self):
        k = Key(_binaries()[0]) if _binaries() else None
        if not k:
            self.skipTest("no openssl on this machine")
        self.addCleanup(k.close)
        pt = k.spki[len(w.SPKI_PREFIX) + 1:]
        good = {1: 2, 3: -7, -1: 1, -2: pt[:32], -3: pt[32:]}
        cases = {
            "OKP kty": {**good, 1: 1},
            "EdDSA alg": {**good, 3: -8},
            "RS256 alg": {**good, 3: -257},
            "P-384": {**good, -1: 2},
            "short x": {**good, -2: pt[:31]},
            "x as str": {**good, -2: "x" * 32},
            "off curve": {**good, -3: pt[32:63] + bytes([pt[63] ^ 1])},
            "not a map": [2, -7],
        }
        reasons = {}
        for case, cose in cases.items():
            with self.assertRaises(w.Refused, msg=case) as cm:
                w.cose_to_spki(cose)
            reasons[case] = cm.exception.reason
        self.assertIn("not on P-256", reasons["off curve"])
        self.assertIn("ES256", reasons["EdDSA alg"])
        self.assertIn("P-256", reasons["P-384"])


class T65_Unavailable(Golden):
    def setUp(self):
        # subprocess.run is patched process-wide below, so sign first.
        self.sig = self.key.sign(auth_data() + hashlib.sha256(client_data()).digest())

    def test_no_openssl_says_unavailable_never_ok(self):
        with mock.patch.object(w, "openssl_bin", return_value=None):
            self.assertEqual(self.assertion(sig=self.sig), (False, w.UNAVAILABLE))

    def test_a_binary_that_cannot_run_is_unavailable(self):
        self.assertEqual(self.assertion(openssl="/nonexistent/openssl"), (False, w.UNAVAILABLE))

    def test_a_hung_binary_is_unavailable(self):
        def hang(argv, **kw):
            self.assertEqual(kw.get("timeout"), w.OPENSSL_TIMEOUT_S)
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        with mock.patch.object(w.subprocess, "run", side_effect=hang):
            self.assertEqual(self.assertion(sig=self.sig), (False, w.UNAVAILABLE))

    def test_an_openssl_that_exits_0_without_saying_so_is_not_ok(self):
        r = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(w.subprocess, "run", return_value=r):
            self.assertEqual(self.assertion(sig=self.sig), (False, w.BAD_SIGNATURE))


class OpensslHandling(Golden):
    def test_system_binary_first_path_as_fallback(self):
        with mock.patch.object(w.os.path, "isfile", return_value=True), \
                mock.patch.object(w.os, "access", return_value=True):
            self.assertEqual(w.openssl_bin(), w.SYSTEM_OPENSSL)
        with mock.patch.object(w.os.path, "isfile", return_value=False), \
                mock.patch.object(w.shutil, "which", return_value="/opt/x/openssl"):
            self.assertEqual(w.openssl_bin(), "/opt/x/openssl")

    def test_fixed_argv_private_dir_removed_after(self):
        seen = {}
        real_run = subprocess.run

        def spy(argv, **kw):
            d = os.path.dirname(argv[4])
            seen["argv"], seen["dir"] = list(argv), d
            seen["mode"] = os.stat(d).st_mode & 0o777
            seen["stdin"] = kw.get("stdin")
            return real_run(argv, **kw)
        sig = self.key.sign(auth_data() + hashlib.sha256(client_data()).digest())
        with mock.patch.object(w.subprocess, "run", side_effect=spy):
            self.assertEqual(self.assertion(sig=sig, openssl=self.bins[0]), (True, "ok"))
        a, d = seen["argv"], seen["dir"]
        self.assertEqual(a, [self.bins[0], "dgst", "-sha256", "-verify", os.path.join(d, "key.der"),
                             "-keyform", "DER", "-signature", os.path.join(d, "sig.der"),
                             os.path.join(d, "msg.bin")])
        self.assertEqual(seen["mode"], 0o700)
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)
        self.assertFalse(os.path.exists(d))

    def test_dir_removed_even_when_openssl_fails(self):
        seen = {}

        sig = self.key.sign(auth_data() + hashlib.sha256(client_data()).digest())

        def boom(argv, **kw):
            seen["dir"] = os.path.dirname(argv[4])
            raise OSError("exec failed")
        with mock.patch.object(w.subprocess, "run", side_effect=boom):
            self.assertEqual(self.assertion(sig=sig), (False, w.UNAVAILABLE))
        self.assertFalse(os.path.exists(seen["dir"]))


class B64url(unittest.TestCase):
    def test_strict(self):
        self.assertEqual(w.b64url_decode(w.b64url(b"\xfb\xff")), b"\xfb\xff")
        for bad in ("", "a+b", "a/b", "ab==", "a b", None):
            with self.assertRaises(w.Refused, msg=repr(bad)):
                w.b64url_decode(bad)


if __name__ == "__main__":
    unittest.main()
