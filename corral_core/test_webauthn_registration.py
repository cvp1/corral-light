"""DESIGN-6 S6b: the registration verifier and its CBOR reader
(corral_core/webauthn.py).

The CBOR encoder below is written separately from the decoder on purpose: a
test that encodes with the code under test proves only that it agrees with
itself. Until the live key-pairing check captures a real authenticator's
registration, the golden one is built here around a throwaway openssl key.
"""
import hashlib
import json
import unittest
from pathlib import Path

from corral_core import webauthn as w
from corral_core.test_webauthn import CHALLENGE, Key, ORIGIN, RP_ID, _binaries

HERE = Path(__file__).resolve().parent
REAL_VECTOR = HERE / "testdata" / "webauthn-registration.json"
CRED_ID = b"\x22" * 16
AAGUID = bytes(range(16))


# ── an independent, minimal CBOR encoder ────────────────────────────────────

def _head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    for ai, width in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * width):
            return bytes([major << 5 | ai]) + n.to_bytes(width, "big")
    raise ValueError(n)


def enc(v):
    if v is False:
        return b"\xf4"
    if v is True:
        return b"\xf5"
    if v is None:
        return b"\xf6"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, bytes):
        return _head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode("utf-8")
        return _head(3, len(b)) + b
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(enc(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(enc(k) + enc(x) for k, x in v.items())
    raise TypeError(type(v))


def client_data(**over):
    d = {"type": "webauthn.create", "challenge": w.b64url(CHALLENGE), "origin": ORIGIN,
         "crossOrigin": False}
    d.update(over)
    return json.dumps({k: v for k, v in d.items() if v is not None}).encode()


def cose_key(spki, over=None):
    point = spki[-64:]
    k = {1: 2, 3: -7, -1: 1, -2: point[:32], -3: point[32:]}
    k.update(over or {})
    return {key: v for key, v in k.items() if v is not None}


def auth_data(spki, flags=w.FLAG_UP | w.FLAG_UV | w.FLAG_AT, count=0, rp_id=RP_ID,
              cred=CRED_ID, cred_len=None, cose=None, tail=b""):
    attested = AAGUID + (len(cred) if cred_len is None else cred_len).to_bytes(2, "big") \
        + cred + enc(cose if cose is not None else cose_key(spki)) + tail
    return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) \
        + count.to_bytes(4, "big") + attested


def attestation(ad, fmt="none", att_stmt=None, extra=None):
    m = {"fmt": fmt, "attStmt": {} if att_stmt is None else att_stmt, "authData": ad}
    m.update(extra or {})
    return enc(m)


class Golden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bins = _binaries()
        if not bins:
            raise unittest.SkipTest("no openssl on this machine")
        cls.key = Key(bins[0])

    @classmethod
    def tearDownClass(cls):
        cls.key.close()

    def register(self, cd=None, att=None, origins=(ORIGIN,), challenge=CHALLENGE,
                 rp_id=RP_ID, **ad_kw):
        cd = client_data() if cd is None else cd
        att = attestation(auth_data(self.key.spki, **ad_kw)) if att is None else att
        return w.verify_registration(client_data_json=cd, attestation_object=att,
                                     challenge=challenge, origins=origins, rp_id=rp_id)


class T6b1_TheRegistrationVerifies(Golden):
    def test_golden_registration_returns_the_credential(self):
        ok, reason, cred = self.register(count=3)
        self.assertEqual((ok, reason), (True, "ok"))
        self.assertEqual(cred, {"id": w.b64url(CRED_ID), "spki": self.key.spki,
                                "signCount": 3, "aaguid": AAGUID.hex()})

    def test_the_enrolled_key_verifies_a_later_assertion(self):
        # Round trip: what enrollment stores is what pairing checks against.
        _, _, cred = self.register()
        ad = hashlib.sha256(RP_ID.encode()).digest() + bytes([w.FLAG_UP | w.FLAG_UV]) \
            + (1).to_bytes(4, "big")
        cd = json.dumps({"type": "webauthn.get", "challenge": w.b64url(b"\x07" * 32),
                         "origin": ORIGIN}).encode()
        sig = self.key.sign(ad + hashlib.sha256(cd).digest())
        self.assertEqual(w.verify_assertion(
            client_data_json=cd, auth_data=ad, signature=sig, credential_id=CRED_ID,
            challenge=b"\x07" * 32, origins=[ORIGIN], rp_id=RP_ID,
            credentials={cred["id"]: {"spki": cred["spki"], "signCount": cred["signCount"]}}),
            (True, "ok"))

    def test_extensions_map_after_the_key_is_allowed_with_ed(self):
        ok, reason, _ = self.register(flags=w.FLAG_UP | w.FLAG_UV | w.FLAG_AT | w.FLAG_ED,
                                      tail=enc({"credProtect": 2, "hmac-secret": True}))
        self.assertEqual((ok, reason), (True, "ok"))

    def test_real_key_vector(self):
        if not REAL_VECTOR.exists():
            self.skipTest("missing the real-key registration vector "
                          "corral_core/testdata/webauthn-registration.json (captured in live L-C)")
        v = json.loads(REAL_VECTOR.read_text(encoding="utf-8"))
        d = w.b64url_decode
        ok, reason, cred = w.verify_registration(
            client_data_json=d(v["clientDataJSON"]), attestation_object=d(v["attestationObject"]),
            challenge=d(v["challenge"]), origins=[v["origin"]], rp_id=v["rpId"])
        self.assertEqual((ok, reason), (True, "ok"))
        self.assertEqual(cred["id"], v["credentialId"])


class T6b2_EachRefusalHasItsOwnReason(Golden):
    def cases(self):
        k = self.key.spki
        ad = auth_data(k)
        flags_no = w.FLAG_UP | w.FLAG_UV | w.FLAG_AT
        point = k[-64:]
        off_curve = cose_key(k, {-3: point[32:63] + bytes([point[63] ^ 1])})
        return [
            ("assertion type", dict(cd=client_data(type="webauthn.get")), w.WRONG_TYPE_CREATE),
            ("challenge", dict(cd=client_data(challenge=w.b64url(b"\x01" * 32))), w.WRONG_CHALLENGE),
            ("origin 127.0.0.1", dict(cd=client_data(origin="http://127.0.0.1:18098")), w.BAD_ORIGIN),
            ("origin other port", dict(cd=client_data(origin="http://localhost:8098")), w.BAD_ORIGIN),
            ("origin https swap", dict(cd=client_data(origin="https://localhost:18098")), w.BAD_ORIGIN),
            ("cross-origin", dict(cd=client_data(crossOrigin=True)), w.CROSS_ORIGIN),
            ("rpId", dict(rp_id="example.invalid"), w.RPID_MISMATCH),
            ("UP clear", dict(flags=flags_no & ~w.FLAG_UP), w.NO_UP),
            ("UV clear", dict(flags=flags_no & ~w.FLAG_UV), w.NO_UV),
            ("AT clear", dict(att=attestation(ad[:32] + bytes([w.FLAG_UP | w.FLAG_UV]) + ad[33:37])), w.NO_AT),
            ("cred id length 0", dict(cred=b"", cred_len=0), w.BAD_CREDENTIAL_ID),
            ("cred id length 1024", dict(cred=b"\x01" * 1024), w.BAD_CREDENTIAL_ID),
            ("cred id length past the end", dict(cred_len=1000), w.TRUNCATED_ATTESTED),
            ("alg -8", dict(cose=cose_key(k, {3: -8})), "COSE alg is not ES256 (-7)"),
            ("RSA alg -257", dict(cose={1: 3, 3: -257, -1: b"\x01" * 256, -2: b"\x01\x00\x01"}),
             "COSE key is not EC2"),
            ("curve P-384", dict(cose=cose_key(k, {-1: 2})), "COSE curve is not P-256"),
            ("point off the curve", dict(cose=off_curve), "COSE point is not on P-256"),
            ("fmt packed", dict(att=attestation(ad, fmt="packed")), w.WRONG_FMT),
            ("attStmt not empty", dict(att=attestation(ad, att_stmt={"alg": -7})), w.ATTSTMT_NOT_EMPTY),
            ("no authData", dict(att=enc({"fmt": "none", "attStmt": {}})), w.NOT_ATTESTATION),
            ("extra top key", dict(att=attestation(ad, extra={"epAtt": True})), w.NOT_ATTESTATION),
            ("authData as text", dict(att=enc({"fmt": "none", "attStmt": {}, "authData": "x"})),
             w.NOT_ATTESTATION),
            ("bytes after the key without ED", dict(tail=b"\x00"), w.TRAILING_AUTH_DATA),
            ("ED set, extensions not a map",
             dict(flags=flags_no | w.FLAG_ED, tail=enc([1])), "extensions are not a map"),
            ("ED set, no extensions", dict(flags=flags_no | w.FLAG_ED), w.CBOR_TRUNCATED),
        ]

    def test_each_case_is_refused_with_its_reason_and_no_credential(self):
        for name, kw, want in self.cases():
            with self.subTest(name):
                ok, reason, cred = self.register(**kw)
                self.assertFalse(ok)
                self.assertEqual(reason, want)
                self.assertIsNone(cred)

    def test_core_reasons_are_distinct(self):
        reasons = [want for _, _, want in self.cases()]
        named = [w.WRONG_TYPE_CREATE, w.WRONG_CHALLENGE, w.BAD_ORIGIN, w.CROSS_ORIGIN,
                 w.RPID_MISMATCH, w.NO_UP, w.NO_UV, w.NO_AT, w.BAD_CREDENTIAL_ID,
                 w.TRUNCATED_ATTESTED, w.WRONG_FMT, w.ATTSTMT_NOT_EMPTY, w.NOT_ATTESTATION,
                 w.TRAILING_AUTH_DATA]
        self.assertEqual(len(set(named)), len(named))
        for r in named:
            self.assertIn(r, reasons)


class T6b3_NoKeyOnTheCallersSaySo(Golden):
    def test_a_naked_spki_is_refused(self):
        ok, reason, cred = self.register(att=self.key.spki)
        self.assertFalse(ok)
        self.assertIsNone(cred)

    def test_a_bare_cose_key_is_refused(self):
        ok, reason, cred = self.register(att=enc(cose_key(self.key.spki)))
        self.assertEqual((ok, reason, cred), (False, w.NOT_ATTESTATION, None))

    def test_not_bytes_is_refused(self):
        ok, reason, cred = self.register(att="not bytes")
        self.assertEqual((ok, reason, cred), (False, w.NOT_ATTESTATION, None))


class T6b4_CborReader(unittest.TestCase):
    def refused(self, raw, reason):
        with self.assertRaises(w.Refused) as cm:
            w.cbor_decode(raw)
        self.assertEqual(cm.exception.reason, reason, raw.hex() if isinstance(raw, bytes) else raw)

    def test_round_trips_every_supported_type(self):
        v = {1: 2, -7: [0, 23, 24, 255, 256, 65535, 65536, -1, -25, -257],
             "t": "ü", "b": b"\x00\xff", "f": False, "tt": True, "n": None}
        self.assertEqual(w.cbor_decode(enc(v)), v)

    def test_depth_cap(self):
        def nest(n):
            return enc(0) if n == 0 else _head(4, 1) + nest(n - 1)
        want = 0
        for _ in range(w.MAX_CBOR_DEPTH - 1):
            want = [want]
        self.assertEqual(w.cbor_decode(nest(w.MAX_CBOR_DEPTH - 1)), want)
        self.refused(nest(w.MAX_CBOR_DEPTH), w.CBOR_TOO_DEEP)

    def test_length_past_the_end(self):
        self.refused(b"\x5a\x00\x01\x00\x00" + b"\x00" * 8, w.CBOR_TRUNCATED)
        self.refused(b"\x82\x01", w.CBOR_TRUNCATED)
        self.refused(b"\xb9\xff\xff", w.CBOR_TRUNCATED)      # huge map claim, no allocation
        self.refused(b"\x19\x01", w.CBOR_TRUNCATED)
        self.refused(b"", w.CBOR_TRUNCATED)

    def test_indefinite_lengths(self):
        for raw in (b"\x5f\x41\x00\xff", b"\x7f\x61\x61\xff", b"\x9f\x01\xff", b"\xbf\x01\x02\xff"):
            self.refused(raw, w.CBOR_INDEFINITE)

    def test_trailing_bytes(self):
        self.refused(enc({1: 2}) + b"\x00", w.CBOR_TRAILING)

    def test_duplicate_keys(self):
        self.refused(b"\xa2\x01\x02\x01\x03", w.CBOR_DUPLICATE_KEY)
        self.refused(b"\xa2\x61a\x01\x61a\x02", w.CBOR_DUPLICATE_KEY)

    def test_bad_keys(self):
        self.refused(b"\xa1\x80\x01", w.CBOR_BAD_KEY)          # array key
        self.refused(b"\xa1\xf5\x01", w.CBOR_BAD_KEY)          # true as a key
        self.refused(b"\xa1\xf6\x01", w.CBOR_BAD_KEY)          # null as a key
        self.refused(b"\xa1\x41\x00\x01", w.CBOR_BAD_KEY)      # bytes key

    def test_over_the_size_cap(self):
        big = enc(b"\x00" * w.MAX_CBOR_BYTES)
        self.refused(big, w.CBOR_OVER_CAP)

    def test_unsupported_types(self):
        for raw in (b"\xc0\x60", b"\xd8\x18\x41\x00",          # tags
                    b"\xf9\x3c\x00", b"\xfa\x00\x00\x00\x00",   # floats
                    b"\xf7", b"\xf8\x20", b"\xe0",              # undefined, simple
                    b"\x1c", b"\x3d"):                          # reserved additional info
            self.refused(raw, w.CBOR_UNSUPPORTED)

    def test_non_minimal_encodings(self):
        for raw in (b"\x18\x05", b"\x19\x00\x10", b"\x1a\x00\x00\x00\x10",
                    b"\x1b\x00\x00\x00\x00\x00\x00\x00\x10", b"\x58\x01\x00", b"\x98\x00"):
            self.refused(raw, w.CBOR_NOT_MINIMAL)
        self.assertEqual(w.cbor_decode(b"\x18\x18"), 24)
        self.assertEqual(w.cbor_decode(b"\x19\x01\x00"), 256)

    def test_text_must_be_utf8(self):
        self.refused(b"\x62\xff\xfe", w.CBOR_BAD_TEXT)

    def test_prefix_leaves_the_rest_to_the_caller(self):
        v, end = w.cbor_decode_prefix(enc({1: 2}) + b"\xab\xcd")
        self.assertEqual((v, end), ({1: 2}, 3))

    def test_not_bytes(self):
        with self.assertRaises(w.Refused):
            w.cbor_decode("a1")


if __name__ == "__main__":
    unittest.main()
