#!/usr/bin/python3
"""webpush.py: Web Push with no payload, signed by the system openssl.

Run: python3 -m unittest test_webpush
"""
import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

import webpush as push

HAS_OPENSSL = bool(shutil.which("openssl"))
APPLE = "https://web.push.apple.com/QGuQyavXutnMH-test"
FCM = "https://fcm.googleapis.com/fcm/send/abc:def"


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="push-test-"))
        self.addCleanup(shutil.rmtree, self.state, True)


class EndpointsAreOnlyPushServices(unittest.TestCase):
    """The hub never posts to a URL a client chose."""

    def test_the_browsers_push_services_pass(self):
        for url in (APPLE, FCM, "https://updates.push.services.mozilla.com/wpush/v2/x",
                    "https://wns2-par02p.notify.windows.com/w/?token=x"):
            self.assertEqual(push.endpoint_ok(url), (True, "ok"), url)

    def test_everything_else_is_refused(self):
        for url in ("http://web.push.apple.com/x",            # not https
                    "https://evil.example.com/x",
                    "https://web.push.apple.com.evil.com/x",  # lookalike suffix
                    "https://evilpush.apple.com.example/x",
                    "https://evilpush.apple.com/x",           # suffix without the dot
                    "https://xnotify.windows.com/x",
                    "https://user:pw@fcm.googleapis.com/x",   # userinfo
                    "https://fcm.googleapis.com:8443/x",      # odd port
                    "https://127.0.0.1/x", "", None, 42,
                    "https://fcm.googleapis.com/" + "a" * 1100):
            self.assertFalse(push.endpoint_ok(url)[0], url)


class TheSignature(Tmp):
    """ES256 by openssl, as JOSE wants it, and verifiable by openssl."""

    def test_der_to_raw_pads_and_strips(self):
        r, s_ = b"\x00" + b"\x80" + b"\x01" * 31, b"\x05"      # 33 bytes (sign pad), 1 byte
        der = b"\x30" + bytes([4 + len(r) + len(s_)]) + b"\x02" + bytes([len(r)]) + r \
            + b"\x02" + bytes([len(s_)]) + s_
        raw = push.der_to_raw(der)
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[:32], b"\x80" + b"\x01" * 31)
        self.assertEqual(raw[32:], b"\x00" * 31 + b"\x05")

    def test_bad_der_is_refused(self):
        for bad in (b"", b"\x31\x06\x02\x01\x01\x02\x01\x01", b"\x30\x06\x03\x01\x01\x02\x01\x01",
                    b"\x30\x26\x02\x21" + b"\x01" * 33 + b"\x02\x01\x01"):
            with self.assertRaises(push.PushError):
                push.der_to_raw(bad)

    @unittest.skipUnless(HAS_OPENSSL, "needs openssl")
    def test_the_key_is_private_and_the_jwt_verifies(self):
        key = push.ensure_key(self.state)
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        self.assertEqual(push.ensure_key(self.state), key)            # made once
        pub = unb64(push.public_key(self.state))
        self.assertEqual((len(pub), pub[0]), (65, 4))
        h = push.vapid_auth(APPLE + "?x=1", key, "https://hub.example.ts.net", now=1_700_000_000)
        self.assertTrue(h.startswith("vapid t="))
        jwt, k = h[len("vapid t="):].split(", k=")
        self.assertEqual(unb64(k), pub)
        head, body, sig = jwt.split(".")
        self.assertEqual(json.loads(unb64(head)), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(unb64(body)),
                         {"aud": "https://web.push.apple.com",
                          "exp": 1_700_000_000 + push.JWT_TTL_S,
                          "sub": "https://hub.example.ts.net"})
        raw = unb64(sig)
        self.assertEqual(len(raw), 64)

        def i2der(b):
            b = b.lstrip(b"\0") or b"\0"
            return b"\x02" + bytes([len(b) + (b[0] >> 7)]) + (b"\0" if b[0] & 0x80 else b"") + b
        der = i2der(raw[:32]) + i2der(raw[32:])
        (self.state / "sig").write_bytes(b"\x30" + bytes([len(der)]) + der)
        (self.state / "msg").write_bytes(f"{head}.{body}".encode())
        subprocess.run(["openssl", "ec", "-in", str(key), "-pubout", "-out",
                        str(self.state / "pub.pem")], check=True, capture_output=True)
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.state / "pub.pem"),
                            "-signature", str(self.state / "sig"), str(self.state / "msg")],
                           capture_output=True, text=True)
        self.assertIn("Verified OK", r.stdout)

    def test_no_openssl_fails_closed(self):
        real = push.shutil.which
        push.shutil.which = lambda name: None
        try:
            with self.assertRaises(push.PushError):
                push.ensure_key(self.state)
        finally:
            push.shutil.which = real
        self.assertFalse((self.state / push.KEY_FILE).exists())


class Subscriptions(Tmp):
    def test_subscribe_dedupes_caps_and_is_private(self):
        push.subscribe(self.state, APPLE, "iPhone", now=1)
        push.subscribe(self.state, APPLE, "iPhone again", now=2)
        self.assertEqual([s["label"] for s in push.load_subs(self.state)], ["iPhone again"])
        for i in range(push.MAX_SUBS + 3):
            push.subscribe(self.state, f"{FCM}{i}", now=10 + i)
        subs = push.load_subs(self.state)
        self.assertEqual(len(subs), push.MAX_SUBS)
        self.assertEqual(subs[-1]["endpoint"], f"{FCM}{push.MAX_SUBS + 2}")   # oldest dropped
        mode = stat.S_IMODE(os.stat(self.state / push.SUBS_FILE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_bad_endpoint_is_refused_and_not_stored(self):
        with self.assertRaises(push.PushError):
            push.subscribe(self.state, "https://evil.example.com/x")
        self.assertEqual(push.load_subs(self.state), [])

    def test_unsubscribe_and_a_corrupt_file(self):
        push.subscribe(self.state, APPLE)
        self.assertEqual(push.unsubscribe(self.state, APPLE), 1)
        self.assertEqual(push.unsubscribe(self.state, APPLE), 0)
        (self.state / push.SUBS_FILE).write_text("{not json")
        self.assertEqual(push.load_subs(self.state), [])
        # A hand-edited file cannot smuggle in a non-push URL.
        (self.state / push.SUBS_FILE).write_text(json.dumps([{"endpoint": "https://evil.example.com"}]))
        self.assertEqual(push.load_subs(self.state), [])


class Sending(Tmp):
    """One empty POST per device; a forgotten subscription is dropped."""

    def opener(self, codes):
        self.reqs = []

        class R:
            def __init__(self, code): self.status = code
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def urlopen(req, timeout=None):
            self.reqs.append((req, timeout))
            code = codes[req.full_url]
            if code >= 400:
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
            return R(code)
        return urlopen

    def test_nobody_subscribed_sends_nothing_and_makes_no_key(self):
        r = push.send_all(self.state, "https://h", urlopen=self.opener({}))
        self.assertEqual(r, {"sent": 0, "dropped": 0, "errors": []})
        self.assertEqual(self.reqs, [])
        self.assertFalse((self.state / push.KEY_FILE).exists())
        self.assertIsNone(push.send_async(self.state, "https://h"))

    @unittest.skipUnless(HAS_OPENSSL, "needs openssl")
    def test_each_device_gets_one_empty_signed_ping(self):
        push.subscribe(self.state, APPLE)
        push.subscribe(self.state, FCM)
        r = push.send_all(self.state, "https://hub.example.ts.net",
                          urlopen=self.opener({APPLE: 201, FCM: 201}))
        self.assertEqual(r, {"sent": 2, "dropped": 0, "errors": []})
        for req, timeout in self.reqs:
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.data, b"")                       # no payload, ever
            self.assertEqual(req.get_header("Ttl"), str(push.PUSH_TTL_S))
            self.assertTrue(req.get_header("Authorization").startswith("vapid t="))
            self.assertEqual(timeout, push.SEND_TIMEOUT_S)

    @unittest.skipUnless(HAS_OPENSSL, "needs openssl")
    def test_gone_is_dropped_and_errors_are_reported(self):
        push.subscribe(self.state, APPLE)
        push.subscribe(self.state, FCM)
        r = push.send_all(self.state, "https://h", urlopen=self.opener({APPLE: 410, FCM: 500}))
        self.assertEqual((r["sent"], r["dropped"]), (0, 1))
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("HTTP 500", r["errors"][0])
        self.assertEqual([s["endpoint"] for s in push.load_subs(self.state)], [FCM])


class Notices(unittest.TestCase):
    def test_recent_is_bounded_and_newest_first(self):
        for i in range(push.RECENT_MAX + 5):
            push.remember(f"p{i}", "permission", f"t{i}", "b" * 1000, now=i)
        got = push.recent(3)
        self.assertEqual([n["pane"] for n in got],
                         [f"p{push.RECENT_MAX + 4}", f"p{push.RECENT_MAX + 3}", f"p{push.RECENT_MAX + 2}"])
        self.assertEqual(len(got[0]["body"]), 300)
        self.assertLessEqual(len(push.recent(100)), push.RECENT_MAX)


if __name__ == "__main__":
    unittest.main()
