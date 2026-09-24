"""The edge contract — run by both skins' suites, like test_acp_rail."""
import unittest

from corral_core import edge


class ViaServe(unittest.TestCase):
    def test_identity_header_is_the_proof(self):
        self.assertTrue(edge.via_serve({"Tailscale-User-Login": "a@b.c"}))
        self.assertTrue(edge.via_serve({"tailscale-user-login": "a@b.c"}))

    def test_lan_request_is_not_serve(self):
        self.assertFalse(edge.via_serve({}))
        self.assertFalse(edge.via_serve({"X-Forwarded-For": "100.1.2.3"}))
        self.assertFalse(edge.via_serve({"Tailscale-User-Login": "  "}))


class CookieHeader(unittest.TestCase):
    def test_secure_only_via_serve(self):
        lan = edge.cookie_header("corral", "t.1.s", 43200, secure=False)
        tls = edge.cookie_header("corral", "t.1.s", 43200, secure=True)
        self.assertNotIn("Secure", lan)
        self.assertIn("; Secure; ", tls)
        for h in (lan, tls):
            self.assertTrue(h.startswith("corral=t.1.s; "))
            self.assertIn("HttpOnly", h)
            self.assertIn("SameSite=Strict", h)
            self.assertIn("Path=/", h)
            self.assertTrue(h.endswith("Max-Age=43200"))


class IdentityOk(unittest.TestCase):
    ME = "craig@example.com"

    def test_unbound_hub_is_unchanged(self):
        self.assertEqual(edge.identity_ok({"Tailscale-User-Login": "eve@x"}, None),
                         (True, "unbound"))
        self.assertEqual(edge.identity_ok({}, ""), (True, "unbound"))

    def test_local_request_left_to_the_pairing_gate(self):
        self.assertEqual(edge.identity_ok({}, self.ME), (True, "local"))

    def test_bound_login_passes_case_insensitively(self):
        ok, why = edge.identity_ok({"Tailscale-User-Login": "Craig@Example.com"},
                                   self.ME)
        self.assertTrue(ok)
        self.assertEqual(why, "bound")

    def test_other_tailnet_identity_refused(self):
        ok, why = edge.identity_ok({"Tailscale-User-Login": "eve@example.com"},
                                   self.ME)
        self.assertFalse(ok)
        self.assertIn("eve@example.com", why)

    def test_proxied_without_identity_refused(self):
        # Funnel and tagged devices: Serve forwards, but stamps no login.
        ok, why = edge.identity_ok({"X-Forwarded-For": "203.0.113.9"}, self.ME)
        self.assertFalse(ok)
        self.assertIn("no tailnet identity", why)

    def test_spoof_can_only_narrow(self):
        # A LAN client that forges the identity header gains nothing it did
        # not have: the same request without the header also passes this
        # check, and the pairing cookie is still required after it.
        forged = edge.identity_ok({"Tailscale-User-Login": self.ME}, self.ME)
        plain = edge.identity_ok({}, self.ME)
        self.assertTrue(forged[0] and plain[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
