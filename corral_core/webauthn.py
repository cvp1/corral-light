"""WebAuthn ES256 verification for pairing by touching a key: assertions and
registrations (with the small strict CBOR reader registrations need).

Pure, stdlib-only; the P-256 signature check shells out to `openssl` with a
fixed argv, a timeout and a private temp dir. A workflow guard, not a security
boundary: a process running as the hub's user can still forge a cookie.
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile

MAX_CLIENT_DATA = 2048      # bytes of clientDataJSON; a real one is ~200
MIN_AUTH_DATA = 37          # rpIdHash(32) + flags(1) + signCount(4)
OPENSSL_TIMEOUT_S = 5
SYSTEM_OPENSSL = "/usr/bin/openssl"   # root-owned where it exists; PATH is the fallback

# SubjectPublicKeyInfo DER for an uncompressed P-256 point, up to the point
# itself: SEQUENCE { SEQUENCE { id-ecPublicKey, prime256v1 }, BIT STRING (66) }.
SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")

FLAG_UP = 0x01              # user present
FLAG_UV = 0x04              # user verified

# COSE_Key labels and the only values accepted.
COSE_KTY, COSE_ALG, COSE_CRV, COSE_X, COSE_Y = 1, 3, -1, -2, -3
KTY_EC2, ALG_ES256, CRV_P256 = 2, -7, 1

# P-256 domain parameters, for the on-curve check.
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b

# Distinct refusal reasons, one per check.
WRONG_TYPE = "clientData type is not webauthn.get"
WRONG_CHALLENGE = "challenge does not match"
BAD_ORIGIN = "origin not allowed"
CROSS_ORIGIN = "assertion was made cross-origin"
RPID_MISMATCH = "rpIdHash does not match this hub's rpId"
NO_UP = "user presence (UP) not set"
NO_UV = "user verification (UV) not set"
SIGNCOUNT_REGRESSED = "signCount did not advance (possible cloned key)"
UNKNOWN_CREDENTIAL = "unknown credential"
BAD_SIGNATURE = "signature does not verify"
MALFORMED_DER = "signature is not well-formed DER"
SHORT_AUTH_DATA = "authData shorter than 37 bytes"
NOT_JSON = "clientData is not JSON"
OVER_CAP = "clientData over the size cap"
UNAVAILABLE = "unavailable"   # no usable openssl; never passes


class Refused(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def b64url(raw):
    """Unpadded base64url, the encoding WebAuthn puts in clientDataJSON."""
    return base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    """Strict: the base64url alphabet only, unpadded. Raises Refused."""
    if not isinstance(text, str) or not text or \
            text.strip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"):
        raise Refused("not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise Refused("not base64url")


def parse_client_data(raw):
    """clientDataJSON bytes -> dict with string type/challenge/origin."""
    if not isinstance(raw, (bytes, bytearray)):
        raise Refused(NOT_JSON)
    if len(raw) > MAX_CLIENT_DATA:
        raise Refused(OVER_CAP)
    try:
        d = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused(NOT_JSON)
    if not isinstance(d, dict):
        raise Refused(NOT_JSON)
    for k in ("type", "challenge", "origin"):
        if not isinstance(d.get(k), str):
            raise Refused(f"clientData has no {k}")
    return d


def parse_auth_data(raw):
    """authenticatorData -> its fixed-position fields. Extensions and attested
    credential data (registration only) are left in `rest`, unparsed."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < MIN_AUTH_DATA:
        raise Refused(SHORT_AUTH_DATA)
    raw = bytes(raw)
    flags = raw[32]
    return {"rpIdHash": raw[:32], "flags": flags,
            "up": bool(flags & FLAG_UP), "uv": bool(flags & FLAG_UV),
            "at": bool(flags & 0x40), "ed": bool(flags & 0x80),
            "signCount": int.from_bytes(raw[33:37], "big"), "rest": raw[37:]}


def cose_to_spki(cose):
    """A COSE_Key map (already decoded) -> SubjectPublicKeyInfo DER.
    ES256 on P-256 only, and the point must be on the curve."""
    if not isinstance(cose, dict) or cose.get(COSE_KTY) != KTY_EC2:
        raise Refused("COSE key is not EC2")
    if cose.get(COSE_ALG) != ALG_ES256:
        raise Refused("COSE alg is not ES256 (-7)")
    if cose.get(COSE_CRV) != CRV_P256:
        raise Refused("COSE curve is not P-256")
    x, y = cose.get(COSE_X), cose.get(COSE_Y)
    if not (isinstance(x, (bytes, bytearray)) and isinstance(y, (bytes, bytearray))
            and len(x) == 32 and len(y) == 32):
        raise Refused("COSE x/y are not 32 bytes each")
    xi, yi = int.from_bytes(x, "big"), int.from_bytes(y, "big")
    if xi >= _P or yi >= _P or (yi * yi - (xi * xi * xi - 3 * xi + _B)) % _P:
        raise Refused("COSE point is not on P-256")
    return SPKI_PREFIX + b"\x04" + bytes(x) + bytes(y)


def der_signature_ok(sig):
    """Strict DER shape of ECDSA-Sig-Value: SEQUENCE { INTEGER r, INTEGER s },
    short-form lengths, minimal positive integers of at most 33 bytes, and
    nothing after."""
    if not isinstance(sig, (bytes, bytearray)):
        return False
    sig = bytes(sig)
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2 or sig[1] >= 0x80:
        return False
    i = 2
    for _ in range(2):
        if i + 2 > len(sig) or sig[i] != 0x02:
            return False
        n = sig[i + 1]
        if n == 0 or n > 33 or i + 2 + n > len(sig):
            return False
        v = sig[i + 2:i + 2 + n]
        if v[0] & 0x80:                                  # negative
            return False
        if n > 1 and v[0] == 0 and not v[1] & 0x80:      # non-minimal
            return False
        i += 2 + n
    return i == len(sig)


def openssl_bin():
    """The root-owned system binary first; PATH only as a fallback."""
    if os.path.isfile(SYSTEM_OPENSSL) and os.access(SYSTEM_OPENSSL, os.X_OK):
        return SYSTEM_OPENSSL
    return shutil.which("openssl")


def verify_signature(spki, message, signature, openssl=None):
    """ECDSA-SHA256 over `message` by the key in `spki` (DER). -> (ok, reason)."""
    binary = openssl or openssl_bin()
    if not binary:
        return False, UNAVAILABLE
    d = tempfile.mkdtemp(prefix="corral-webauthn-")     # 0700 by construction
    try:
        os.chmod(d, 0o700)
        paths = {}
        for name, data in (("key.der", spki), ("sig.der", signature), ("msg.bin", message)):
            paths[name] = os.path.join(d, name)
            with open(paths[name], "wb") as f:
                f.write(bytes(data))
        argv = [binary, "dgst", "-sha256", "-verify", paths["key.der"], "-keyform", "DER",
                "-signature", paths["sig.der"], paths["msg.bin"]]
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=OPENSSL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False, UNAVAILABLE
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if r.returncode == 0 and b"Verified OK" in r.stdout:
        return True, "ok"
    return False, BAD_SIGNATURE


def verify_assertion(*, client_data_json, auth_data, signature, credential_id,
                     challenge, origins, rp_id, credentials, openssl=None):
    """One `navigator.credentials.get()` result, judged. -> (ok, reason).

    `challenge` is the raw bytes this hub issued; `origins` the exact origins
    allowed (a single string is one origin, never a substring list);
    `credentials` maps base64url credential id -> {"spki": DER bytes or
    base64url, "signCount": int}. The caller updates the stored signCount
    from parse_auth_data(auth_data) after an ok -- under its own lock.
    """
    try:
        cd = parse_client_data(client_data_json)
        if cd["type"] != "webauthn.get":
            raise Refused(WRONG_TYPE)
        if not isinstance(challenge, (bytes, bytearray)) or not challenge or \
                not hmac.compare_digest(cd["challenge"].encode("ascii", "replace"),
                                        b64url(challenge).encode("ascii")):
            raise Refused(WRONG_CHALLENGE)
        allowed = (origins,) if isinstance(origins, str) else tuple(origins or ())
        if cd["origin"] not in allowed:
            raise Refused(BAD_ORIGIN)
        if cd.get("crossOrigin") is True:
            raise Refused(CROSS_ORIGIN)
        ad = parse_auth_data(auth_data)
        if not hmac.compare_digest(ad["rpIdHash"], hashlib.sha256(rp_id.encode("utf-8")).digest()):
            raise Refused(RPID_MISMATCH)
        if not ad["up"]:
            raise Refused(NO_UP)
        if not ad["uv"]:
            raise Refused(NO_UV)
        rec = (credentials or {}).get(b64url(credential_id or b""))
        if not isinstance(rec, dict) or not rec.get("spki"):
            raise Refused(UNKNOWN_CREDENTIAL)
        stored = int(rec.get("signCount") or 0)
        if stored > 0 and ad["signCount"] <= stored:
            raise Refused(SIGNCOUNT_REGRESSED)
        if not der_signature_ok(signature):
            raise Refused(MALFORMED_DER)
        spki = rec["spki"]
        if isinstance(spki, str):
            spki = b64url_decode(spki)
        message = bytes(auth_data) + hashlib.sha256(bytes(client_data_json)).digest()
        return verify_signature(spki, message, signature, openssl)
    except Refused as e:
        return False, e.reason


# ── registration (DESIGN-6 S6b) ─────────────────────────────────────────────
#
# Enrollment reads one `navigator.credentials.create()` result. Its
# attestationObject is CBOR, the one binary format here; the reader below is
# deliberately small and strict -- it parses exactly the shapes an
# attestation-`none` registration carries and refuses everything else by name.
# What it takes out is the credential id, the public key (as SPKI), the
# signCount and the AAGUID. Attestation is NOT verified: `none` is requested,
# so a software authenticator enrolls as readily as a hardware one (see the C
# threat model in the module docstring).

MAX_CBOR_BYTES = 4096       # a whole attestationObject; a real one is ~250
MAX_CBOR_DEPTH = 6          # top map -> attStmt / COSE key / extensions sit at 2-3
MAX_CREDENTIAL_ID = 1023    # WebAuthn's own ceiling on credentialIdLength
FLAG_AT = 0x40              # attested credential data present
FLAG_ED = 0x80              # extension data present

WRONG_TYPE_CREATE = "clientData type is not webauthn.create"
NOT_ATTESTATION = "not an attestation object"
WRONG_FMT = "attestation format is not none"
ATTSTMT_NOT_EMPTY = "attStmt is not empty for format none"
NO_AT = "attested credential data (AT) not set"
BAD_CREDENTIAL_ID = "credential id length out of range"
TRUNCATED_ATTESTED = "attested credential data is truncated"
TRAILING_AUTH_DATA = "authData has bytes after the credential key"
CBOR_OVER_CAP = "CBOR over the size cap"
CBOR_TOO_DEEP = "CBOR nested too deep"
CBOR_TRUNCATED = "CBOR truncated or length past the end"
CBOR_INDEFINITE = "CBOR indefinite length not allowed"
CBOR_NOT_MINIMAL = "CBOR length or integer not minimally encoded"
CBOR_DUPLICATE_KEY = "CBOR map has a duplicate key"
CBOR_BAD_KEY = "CBOR map key is not an integer or text"
CBOR_UNSUPPORTED = "CBOR type not supported"
CBOR_BAD_TEXT = "CBOR text is not UTF-8"
CBOR_TRAILING = "CBOR has bytes after the top item"


def _cbor_head(buf, i):
    """-> (major, value-or-length, next index). Definite, minimal only."""
    if i >= len(buf):
        raise Refused(CBOR_TRUNCATED)
    b = buf[i]
    major, ai = b >> 5, b & 0x1F
    i += 1
    if ai < 24:
        return major, ai, i
    if ai == 31:
        raise Refused(CBOR_INDEFINITE)
    if ai > 27:
        raise Refused(CBOR_UNSUPPORTED)
    n = 1 << (ai - 24)                       # 1, 2, 4 or 8 following bytes
    if i + n > len(buf):
        raise Refused(CBOR_TRUNCATED)
    v = int.from_bytes(buf[i:i + n], "big")
    # Minimal: each width must be needed (CTAP2 canonical CBOR).
    floor = 24 if n == 1 else 1 << (8 * (n // 2))
    if v < floor:
        raise Refused(CBOR_NOT_MINIMAL)
    return major, v, i + n


def _cbor_item(buf, i, depth):
    """One CBOR data item at buf[i] -> (value, next index)."""
    if depth > MAX_CBOR_DEPTH:
        raise Refused(CBOR_TOO_DEEP)
    first = buf[i] if i < len(buf) else None
    if first is not None and (first >> 5 == 6 or
                              (first >> 5 == 7 and first not in (0xF4, 0xF5, 0xF6))):
        raise Refused(CBOR_UNSUPPORTED)      # tags, floats, other simple values
    major, v, i = _cbor_head(buf, i)
    if major == 0:
        return v, i
    if major == 1:
        return -1 - v, i
    if major in (2, 3):
        if i + v > len(buf):
            raise Refused(CBOR_TRUNCATED)
        raw = bytes(buf[i:i + v])
        if major == 2:
            return raw, i + v
        try:
            return raw.decode("utf-8"), i + v
        except UnicodeDecodeError:
            raise Refused(CBOR_BAD_TEXT)
    if major == 4:
        if v > len(buf) - i:                 # every element takes >= 1 byte
            raise Refused(CBOR_TRUNCATED)
        out = []
        for _ in range(v):
            item, i = _cbor_item(buf, i, depth + 1)
            out.append(item)
        return out, i
    if major == 5:
        if v * 2 > len(buf) - i:
            raise Refused(CBOR_TRUNCATED)
        out = {}
        for _ in range(v):
            key, i = _cbor_item(buf, i, depth + 1)
            if isinstance(key, bool) or not isinstance(key, (int, str)):
                raise Refused(CBOR_BAD_KEY)
            if key in out:
                raise Refused(CBOR_DUPLICATE_KEY)
            out[key], i = _cbor_item(buf, i, depth + 1)
        return out, i
    return {0xF4: False, 0xF5: True, 0xF6: None}[first], i   # major 7, screened above


def cbor_decode_prefix(buf, start=0):
    """Decode ONE item from buf[start:] -> (value, end index). Bytes after it
    are left for the caller -- the COSE key inside authData is followed by
    extensions, or by nothing."""
    if not isinstance(buf, (bytes, bytearray)):
        raise Refused(NOT_ATTESTATION)
    if len(buf) > MAX_CBOR_BYTES:
        raise Refused(CBOR_OVER_CAP)
    return _cbor_item(bytes(buf), start, 1)


def cbor_decode(buf):
    """Decode exactly one item with nothing after it."""
    value, end = cbor_decode_prefix(buf)
    if end != len(buf):
        raise Refused(CBOR_TRAILING)
    return value


def parse_attested_auth_data(raw):
    """authData from a registration -> parse_auth_data fields plus aaguid,
    credentialId and the decoded COSE key. AT must be set; with ED clear the
    COSE key must end the buffer, with ED set exactly one extensions map may
    follow it."""
    ad = parse_auth_data(raw)
    if not ad["at"]:
        raise Refused(NO_AT)
    rest = ad["rest"]
    if len(rest) < 18:
        raise Refused(TRUNCATED_ATTESTED)
    aaguid, n = rest[:16], int.from_bytes(rest[16:18], "big")
    if n < 1 or n > MAX_CREDENTIAL_ID:
        raise Refused(BAD_CREDENTIAL_ID)
    if 18 + n >= len(rest):
        raise Refused(TRUNCATED_ATTESTED)
    cred_id = rest[18:18 + n]
    cose, end = cbor_decode_prefix(rest, 18 + n)
    if ad["ed"]:
        ext, end = cbor_decode_prefix(rest, end)
        if not isinstance(ext, dict):
            raise Refused("extensions are not a map")
    if end != len(rest):
        raise Refused(TRAILING_AUTH_DATA)
    ad.update(aaguid=bytes(aaguid), credentialId=bytes(cred_id), cose=cose)
    return ad


def verify_registration(*, client_data_json, attestation_object, challenge,
                        origins, rp_id):
    """One `navigator.credentials.create()` result, judged.
    -> (ok, reason, credential) where credential is None unless ok, else
    {"id": base64url, "spki": DER bytes, "signCount": int, "aaguid": hex}.

    Checked like an assertion (type, challenge, exact origin, not
    cross-origin, rpIdHash, UP, UV) plus the registration's own framing:
    the attestationObject must be exactly {fmt: "none", attStmt: {},
    authData: bytes}, AT set, a credential id of 1..1023 bytes, an ES256
    P-256 key on the curve, and nothing unexplained after it. A bare public
    key, or anything that is not that map, is refused -- enrollment never
    takes a key on the caller's say-so.
    """
    try:
        cd = parse_client_data(client_data_json)
        if cd["type"] != "webauthn.create":
            raise Refused(WRONG_TYPE_CREATE)
        if not isinstance(challenge, (bytes, bytearray)) or not challenge or \
                not hmac.compare_digest(cd["challenge"].encode("ascii", "replace"),
                                        b64url(challenge).encode("ascii")):
            raise Refused(WRONG_CHALLENGE)
        allowed = (origins,) if isinstance(origins, str) else tuple(origins or ())
        if cd["origin"] not in allowed:
            raise Refused(BAD_ORIGIN)
        if cd.get("crossOrigin") is True:
            raise Refused(CROSS_ORIGIN)
        att = cbor_decode(attestation_object)
        if not isinstance(att, dict) or set(att) != {"fmt", "attStmt", "authData"} \
                or not isinstance(att["authData"], bytes):
            raise Refused(NOT_ATTESTATION)
        if att["fmt"] != "none":
            raise Refused(WRONG_FMT)
        if att["attStmt"] != {}:
            raise Refused(ATTSTMT_NOT_EMPTY)
        ad = parse_attested_auth_data(att["authData"])
        if not hmac.compare_digest(ad["rpIdHash"], hashlib.sha256(rp_id.encode("utf-8")).digest()):
            raise Refused(RPID_MISMATCH)
        if not ad["up"]:
            raise Refused(NO_UP)
        if not ad["uv"]:
            raise Refused(NO_UV)
        spki = cose_to_spki(ad["cose"])
        return True, "ok", {"id": b64url(ad["credentialId"]), "spki": spki,
                            "signCount": ad["signCount"], "aaguid": ad["aaguid"].hex()}
    except Refused as e:
        return False, e.reason, None
