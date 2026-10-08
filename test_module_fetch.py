"""Module fetchers: keys, grants, the exact-host proxy and the fetch sandbox
(docs/finops-module-plan.md §6.7, §8.1 "Exact hosts" and "Fetchers").
Fixture: testkit/modules/probe/fetcher.py, driven by a mode file."""
import json
import os
import stat
import sys
import time
import unittest
from unittest import mock

import module_fetch
import module_sandbox
import modules
import review_egress
from test_modules import HAVE_BWRAP, Base, good_manifest, quiet

KEY = "sk-ant-admin01-FIXTUREKEY-0123456789abcdefghijklmnopqrstuvwxyz"


def with_fetcher(vendors=("anthropic",), **over):
    def mutate(src):
        m = good_manifest()
        m["fetcher"] = dict({"script": "fetcher.py", "vendors": list(vendors),
                             "every_s": 3600, "timeout_s": 10}, **over)
        (src / "module.json").write_text(json.dumps(m))
    return mutate


class ExactHosts(unittest.TestCase):
    def test_exact_only(self):
        hosts = ("api.anthropic.com",)
        self.assertTrue(review_egress.exact_allowed("api.anthropic.com", hosts))
        self.assertTrue(review_egress.exact_allowed("API.Anthropic.com.", hosts))
        for h in ("evil.api.anthropic.com", "anthropic.com", "api.anthropic.com.evil.net",
                  "xapi.anthropic.com", "", None, 7):
            self.assertFalse(review_egress.exact_allowed(h, hosts), h)

    def test_sign_in_hosts_are_refused_even_when_listed(self):
        for h in ("auth.openai.com", "console.anthropic.com", "claude.ai", "auth.x.ai",
                  "x.auth.openai.com"):
            self.assertFalse(review_egress.exact_allowed(h, (h,)), h)

    def test_the_lane_rule_is_unchanged(self):
        self.assertTrue(review_egress.host_allowed("api.anthropic.com", "claude"))
        self.assertTrue(review_egress.host_allowed("eu.api.anthropic.com", "claude"))
        self.assertFalse(review_egress.host_allowed("console.anthropic.com", "claude"))

    def test_vendor_hosts_are_fixed_and_exact(self):
        self.assertEqual(modules.FETCH_VENDORS["anthropic"], ("api.anthropic.com",))
        self.assertEqual(modules.FETCH_VENDORS["gcp"], ("bigquery.googleapis.com",))
        for hosts in modules.FETCH_VENDORS.values():
            for h in hosts:
                self.assertFalse(review_egress.denied_everywhere(h), h)


class TheFetcherManifest(unittest.TestCase):
    def m(self, **f):
        g = good_manifest()
        g["fetcher"] = dict({"script": "fetcher.py", "vendors": ["anthropic"]}, **f)
        return modules.validate_manifest(g)

    def test_defaults_and_bounds(self):
        f = self.m()["fetcher"]
        self.assertEqual((f["every_s"], f["timeout_s"], f["vendors"]), (21600, 60, ["anthropic"]))
        for bad in ({"every_s": 60}, {"every_s": 8 * 86400}, {"timeout_s": 600},
                    {"vendors": []}, {"vendors": ["evil"]}, {"vendors": "anthropic"},
                    {"network": ["evil.com"]}, {"hosts": ["evil.com"]}):
            with self.assertRaises(modules.ModuleError, msg=bad):
                self.m(**bad)

    def test_install_shows_the_hosts(self):
        text = modules.describe(self.m(vendors=["openai", "gcp"]), True, "s", "c" * 40, "d")
        self.assertIn("Fetcher", text)
        self.assertIn("api.openai.com", text)
        self.assertIn("bigquery.googleapis.com", text)
        self.assertNotIn("Fetcher", modules.describe(modules.validate_manifest(good_manifest()),
                                                     True, "s", "c" * 40, "d"))

    def test_the_collector_path_never_runs_a_fetcher(self):
        with self.assertRaises(modules.ModuleError):
            modules.build_run("probe", "fetcher", run_dir="/nonexistent")


class Keys(Base):
    def test_add_list_remove(self):
        p = module_fetch.key_add("anthropic-admin", KEY + "\n\n")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(module_fetch.keys_dir()).st_mode), 0o700)
        self.assertEqual(p.read_text(), KEY + "\n")
        self.assertEqual([r[:2] for r in module_fetch.key_list()], [("anthropic-admin", True)])
        with self.assertRaises(modules.ModuleError):
            module_fetch.key_add("anthropic-admin", "other")
        module_fetch.key_remove("anthropic-admin")
        self.assertEqual(module_fetch.key_list(), [])

    def test_names_and_sizes(self):
        for bad in ("../x", "A", "", "-x", "a/b", ".hidden"):
            with self.assertRaises(modules.ModuleError, msg=bad):
                module_fetch.key_add(bad, KEY)
        for bad in ("", "   ", "shortsecret", "x" * (module_fetch.MAX_KEY_BYTES + 1)):
            with self.assertRaises(modules.ModuleError):
                module_fetch.key_add("k", bad)

    def test_unsafe_key_files_are_refused(self):
        module_fetch.key_add("good", KEY)
        d = module_fetch.keys_dir()
        outside = self.tmp / "outside"
        outside.write_text(KEY)
        os.chmod(outside, 0o600)
        os.symlink(outside, d / "linked")
        (d / "shared").write_text(KEY)
        os.chmod(d / "shared", 0o640)
        (d / "sub").mkdir()
        cases = {"linked": "symlink", "shared": "readable by others", "sub": "not a regular",
                 "missing": "no key"}
        for name, why in cases.items():
            with self.assertRaises(modules.ModuleError, msg=name) as cm:
                module_fetch.key_path(name)
            self.assertIn(why, str(cm.exception))
        os.link(d / "good", self.tmp / "second-name")
        with self.assertRaises(modules.ModuleError) as cm:
            module_fetch.read_key("good")
        self.assertIn("hard-linked", str(cm.exception))
        os.unlink(self.tmp / "second-name")
        self.assertTrue(module_fetch.read_key("good").startswith(KEY.encode()))
        os.chmod(d, 0o755)
        with self.assertRaises(modules.ModuleError) as cm:
            module_fetch.key_path("good")
        self.assertIn("0700", str(cm.exception))


class Grants(Base):
    def setUp(self):
        super().setUp()
        self.install(self.make_repo(mutate=with_fetcher(("anthropic", "openai"))))
        module_fetch.key_add("k1", KEY)

    def test_grant_and_revoke(self):
        self.assertEqual(module_fetch.grant("probe", "k1", "anthropic"), ("api.anthropic.com",))
        self.assertEqual(modules.load_pins()["probe"]["grants"]["k1"]["vendor"], "anthropic")
        with self.assertRaises(modules.ModuleError):
            module_fetch.key_remove("k1")              # granted: revoke first
        module_fetch.revoke("probe", "k1")
        self.assertEqual(modules.load_pins()["probe"]["grants"], {})

    def test_refusals(self):
        for key, vendor, why in (("k1", "xai", "does not declare"), ("k1", "evil", "not one of"),
                                 ("nokey", "anthropic", "no key")):
            with self.assertRaises(modules.ModuleError, msg=vendor) as cm:
                module_fetch.grant("probe", key, vendor)
            self.assertIn(why, str(cm.exception))

    def test_params_are_bounded_and_plain(self):
        module_fetch.grant("probe", "k1", "openai", {"table": "p.d.gcp_billing_export_v1_X",
                                                    "location": "US"})
        self.assertEqual(modules.load_pins()["probe"]["grants"]["k1"]["params"]["location"], "US")
        for bad in ({"Table": "x"}, {"t": "a b"}, {"t": "x" * 201}, {"t": "$(id)"}, {"t": ""},
                    {f"p{i}": "x" for i in range(9)}):
            with self.assertRaises(modules.ModuleError, msg=bad):
                module_fetch.grant("probe", "k1", "openai", bad)

    def test_a_module_without_a_fetcher_cannot_be_granted(self):
        modules.remove("probe", out=quiet)
        self.install(self.make_repo(name="plain"))
        with self.assertRaises(modules.ModuleError):
            module_fetch.grant("probe", "k1", "anthropic")

    def test_an_update_that_changes_vendors_asks_again(self):
        src = self.tmp / "src"
        with_fetcher(("anthropic", "xai"))(src)
        import test_modules
        test_modules.git(src, "commit", "-qam", "vendors")
        with self.assertRaises(modules.ModuleError):
            modules.update("probe", confirm="yes", out=quiet)
        self.assertTrue(modules.update("probe", confirm="probe", out=quiet))


class Scrubbing(unittest.TestCase):
    def test_needles_cover_raw_lines_json_and_base64(self):
        # Not a key: two made-up base64 lines between PEM armour, which is
        # assembled here so secret scanners do not read the source as a key.
        armour = "PRIVATE" + " KEY"
        pem = (f"-----BEGIN {armour}-----\nFIXTUREfixtureFIXTUREfixtureFIXTU\n"
               f"NOTAKEYnotakeyNOTAKEYnotakeyNOTAK\n-----END {armour}-----\n")
        doc = json.dumps({"type": "service_account", "private_key": pem,
                          "private_key_id": "0123456789abcdef0123",
                          "client_email": "x@y.iam.gserviceaccount.com"}).encode()
        ns = module_fetch.needles(doc)
        for want in (b"FIXTUREfixtureFIXTUREfixtureFIXTU", b"NOTAKEYnotakeyNOTAKEYnotakeyNOTAK",
                     b"0123456789abcdef0123", doc.strip()):
            self.assertIn(want, ns)
        import base64
        self.assertIn(base64.b64encode(b"0123456789abcdef0123"), ns)
        self.assertNotIn(f"-----BEGIN {armour}-----".encode(), ns)

    def test_scrub(self):
        ns = module_fetch.needles(KEY.encode())
        t = module_fetch.scrub(f"GET https://api.x/v1?k={KEY} failed\nAuthorization: Bearer "
                               f"abcdefghijklmnop\nx-api-key: {KEY}\ntoken Bearer zzzzzzzzzz", ns)
        self.assertNotIn(KEY, t)
        self.assertNotIn("abcdefghijklmnop", t)
        self.assertNotIn("https://", t)
        self.assertNotIn("zzzzzzzzzz", t)
        self.assertLessEqual(len(module_fetch.scrub("x" * 5000)), module_fetch.MAX_ERROR)


class NoSandboxNoFetch(Base):
    def test_a_host_without_the_sandbox_never_runs_a_fetcher(self):
        with mock.patch.object(module_sandbox, "available", return_value=(False, "none")):
            self.install(self.make_repo(mutate=with_fetcher()), ack_unsandboxed="unsandboxed")
            module_fetch.key_add("k1", KEY)
            module_fetch.grant("probe", "k1", "anthropic")
            st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "failing")
        self.assertIn("only in the module sandbox", st["error"])
        self.assertFalse((modules.fetch_dir("probe") / "k1.json").exists())


class Upstream:
    """A stand-in vendor: records what the proxy sent; `reply(method, path,
    headers, body)` -> (status, bytes)."""

    def __init__(self, reply):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        up = self
        self.seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self, method):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                up.seen.append((method, self.path, {k.lower(): v for k, v in self.headers.items()},
                                body))
                status, data = reply(method, self.path, self.headers, body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Set-Cookie", "s=1")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addr = ("127.0.0.1", self.srv.server_address[1])

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def via(proxy, method, url, headers=None, body=None):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=20)
    try:
        c.request(method, url, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


class TheFetchProxy(unittest.TestCase):
    """The key stays in the core: the proxy adds it, the module never has it
    (plan §6.7.2). No sandbox needed, so these run on every platform."""

    def setUp(self):
        import fetch_proxy
        self.fp = fetch_proxy
        self.addCleanup(fetch_proxy.UPSTREAM_OVERRIDE.clear)

    def upstream(self, host, reply):
        up = Upstream(reply)
        self.addCleanup(up.close)
        self.fp.UPSTREAM_OVERRIDE[host] = up.addr
        return up

    def proxy(self, vendor, key, hosts):
        seen = []
        p = self.fp.FetchProxy("", vendor, key, hosts,
                               on_host=lambda h, ok: seen.append((h, ok)), tcp=True)
        self.addCleanup(p.close)
        return p, seen

    def test_the_credential_is_added_and_the_modules_are_dropped(self):
        up = self.upstream("api.anthropic.com", lambda m, p, h, b: (200, b'{"data": []}'))
        p, seen = self.proxy("anthropic", KEY.encode(), ("api.anthropic.com",))
        st, hdrs, body = via(p, "GET", "https://api.anthropic.com/v1/organizations/cost_report?x=1",
                             {"x-api-key": "module-chosen", "Authorization": "Bearer evil",
                              "Cookie": "c=1", "anthropic-version": "2023-06-01",
                              "Host": "evil.example"})
        self.assertEqual((st, body), (200, b'{"data": []}'))
        method, path, h, _ = up.seen[0]
        self.assertEqual((method, path), ("GET", "/v1/organizations/cost_report?x=1"))
        self.assertEqual(h["x-api-key"], KEY)
        self.assertEqual(h["anthropic-version"], "2023-06-01")
        self.assertEqual(h["host"], "api.anthropic.com")
        self.assertNotIn("authorization", h)
        self.assertNotIn("cookie", h)
        self.assertNotIn("set-cookie", {k.lower() for k in hdrs})
        self.assertEqual(seen, [("api.anthropic.com", True)])

    def test_bearer_vendors(self):
        for vendor, host in (("openai", "api.openai.com"), ("xai", "management-api.x.ai")):
            up = self.upstream(host, lambda m, p, h, b: (200, b"{}"))
            p, _ = self.proxy(vendor, KEY.encode(), (host,))
            via(p, "POST", f"https://{host}/v1/x", {"Content-Type": "application/json"}, b'{"a":1}')
            self.assertEqual(up.seen[0][2]["authorization"], f"Bearer {KEY}")
            self.assertEqual(up.seen[0][3], b'{"a":1}')

    def test_refusals(self):
        self.upstream("api.anthropic.com", lambda m, p, h, b: (200, b"{}"))
        p, seen = self.proxy("anthropic", KEY.encode(), ("api.anthropic.com",))
        for url in ("https://example.com/", "https://evil.api.anthropic.com/",
                    "http://api.anthropic.com/", "https://api.anthropic.com:8443/",
                    "https://u:p@api.anthropic.com/", "https://console.anthropic.com/"):
            self.assertEqual(via(p, "GET", url)[0], 403, url)
        self.assertEqual(via(p, "DELETE", "https://api.anthropic.com/")[0], 405)
        self.assertFalse([h for h, ok in seen if ok])

    def test_no_connect_tunnel(self):
        import socket
        p, _ = self.proxy("anthropic", KEY.encode(), ("api.anthropic.com",))
        s = socket.create_connection(("127.0.0.1", p.port), timeout=5)
        s.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b" 405 ", s.recv(200))
        s.close()

    def test_a_response_carrying_the_key_is_refused(self):
        import base64
        for echo in (KEY.encode(), base64.b64encode(KEY.encode())):
            self.upstream("api.anthropic.com", lambda m, p, h, b, e=echo: (200, b'{"k":"' + e + b'"}'))
            p, _ = self.proxy("anthropic", KEY.encode(), ("api.anthropic.com",))
            st, _h, body = via(p, "GET", "https://api.anthropic.com/v1/x")
            self.assertEqual(st, 502)
            self.assertNotIn(KEY.encode(), body)

    def test_a_request_cap(self):
        self.upstream("api.anthropic.com", lambda m, p, h, b: (200, b"{}"))
        p, _ = self.proxy("anthropic", KEY.encode(), ("api.anthropic.com",))
        with mock.patch.object(self.fp, "MAX_REQUESTS", 2):
            codes = [via(p, "GET", "https://api.anthropic.com/v1/x")[0] for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


@unittest.skipUnless(__import__("shutil").which("openssl"), "needs openssl")
class TheGoogleToken(unittest.TestCase):
    """The core signs the service-account assertion and keeps the token."""

    def setUp(self):
        import fetch_proxy
        import subprocess
        import tempfile
        self.fp = fetch_proxy
        self.addCleanup(fetch_proxy.UPSTREAM_OVERRIDE.clear)
        d = tempfile.mkdtemp(prefix="corral-rsa-")
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        self.dir = d
        k = os.path.join(d, "k.pem")
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                        "rsa_keygen_bits:2048", "-out", k], check=True, capture_output=True)
        with open(k) as f:
            self.pem = f.read()
        self.sa = {"type": "service_account", "client_email": "fx@p.iam.gserviceaccount.com",
                   "private_key": self.pem, "private_key_id": "abcdef0123456789abcd"}

    def verify(self, msg, sig):
        import subprocess
        pub, m, s = (os.path.join(self.dir, n) for n in ("pub.pem", "m", "s"))
        subprocess.run(["openssl", "pkey", "-in", os.path.join(self.dir, "k.pem"), "-pubout",
                        "-out", pub], check=True, capture_output=True)
        for path, data in ((m, msg), (s, sig)):
            with open(path, "wb") as f:
                f.write(data)
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", s, m],
                           capture_output=True)
        return r.returncode == 0

    def test_openssl_verifies_the_signature(self):
        for msg in (b"", b"hello", os.urandom(777)):
            self.assertTrue(self.verify(msg, self.fp.rsa_sign(self.pem, msg)))
        self.assertFalse(self.verify(b"other", self.fp.rsa_sign(self.pem, b"hello")))
        for bad in ("", "-----BEGIN PRIVATE KEY-----\nMA==\n-----END PRIVATE KEY-----"):
            with self.assertRaises(ValueError):
                self.fp.rsa_sign(bad, b"x")

    def test_the_module_gets_data_never_the_token(self):
        import base64
        import urllib.parse
        tokens = []

        def token(m, path, h, body):
            form = urllib.parse.parse_qs(body.decode())
            head, claims, sig = form["assertion"][0].split(".")
            pad = lambda x: x + "=" * (-len(x) % 4)  # noqa: E731
            c = json.loads(base64.urlsafe_b64decode(pad(claims)))
            assert c["aud"] == self.fp.GOOGLE_TOKEN_URL and c["iss"] == self.sa["client_email"]
            assert self.verify(f"{head}.{claims}".encode(), base64.urlsafe_b64decode(pad(sig)))
            tokens.append("ya29.SECRET-TOKEN-0123456789")
            return 200, json.dumps({"access_token": tokens[-1], "expires_in": 3600}).encode()
        tok_up = Upstream(token)
        bq_up = Upstream(lambda m, p, h, b: (200, b'{"jobComplete": true, "rows": []}'))
        self.addCleanup(tok_up.close)
        self.addCleanup(bq_up.close)
        self.fp.UPSTREAM_OVERRIDE.update({"oauth2.googleapis.com": tok_up.addr,
                                          "bigquery.googleapis.com": bq_up.addr})
        p = self.fp.FetchProxy("", "gcp", json.dumps(self.sa).encode(),
                               ("bigquery.googleapis.com",), tcp=True)
        self.addCleanup(p.close)
        for _ in range(2):
            st, _h, body = via(p, "POST", "https://bigquery.googleapis.com/bigquery/v2/projects/"
                               "p/queries", {"Content-Type": "application/json"}, b"{}")
            self.assertEqual(st, 200)
            self.assertNotIn(b"SECRET-TOKEN", body)
        self.assertEqual(len(tokens), 1, "the token is cached for the run")
        self.assertEqual(bq_up.seen[0][2]["authorization"], "Bearer " + tokens[0])
        self.assertEqual(via(p, "POST", "https://oauth2.googleapis.com/token")[0], 403,
                         "the token host is the core's, never the module's")


@unittest.skipUnless(HAVE_BWRAP, "needs the module sandbox")
class Runs(Base):
    def setUp(self):
        super().setUp()
        import fetch_proxy
        self.fp = fetch_proxy
        self.addCleanup(fetch_proxy.UPSTREAM_OVERRIDE.clear)
        self.install(self.make_repo(mutate=with_fetcher(("anthropic", "gcp"))))
        module_fetch.key_add("k1", KEY)
        module_fetch.grant("probe", "k1", "anthropic", {"location": "US"})

    def mode(self, **m):
        wd = module_fetch.work_dir("probe", "k1")
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "mode.json").write_text(json.dumps(m))

    def upstream(self, reply):
        up = Upstream(reply)
        self.addCleanup(up.close)
        self.fp.UPSTREAM_OVERRIDE["api.anthropic.com"] = up.addr
        return up

    def result(self):
        p = modules.fetch_dir("probe") / "k1.json"
        return json.loads(p.read_text()) if p.exists() else None

    def test_ok_is_stored_for_the_collector(self):
        self.mode(mode="ok")
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        r = self.result()
        self.assertEqual((r["schema"], r["vendor"], r["key"]),
                         (module_fetch.RESULT_SCHEMA, "anthropic", "k1"))
        self.assertEqual(r["result"], {"ok": True, "vendor": "anthropic",
                                       "hosts": "api.anthropic.com"})
        self.assertGreater(st["next_due_at"], time.time() + 3000)

    def test_the_sandboxed_fetcher_reaches_the_vendor_without_the_key(self):
        up = self.upstream(lambda m, p, h, b: (200, b'{"data": [1]}'))
        self.mode(mode="call", requests=[
            {"url": "https://api.anthropic.com/v1/organizations/cost_report",
             "headers": {"anthropic-version": "2023-06-01", "x-api-key": "guess"}}])
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        self.assertEqual(self.result()["result"]["replies"], [{"status": 200,
                                                             "text": '{"data": [1]}'}])
        self.assertEqual(up.seen[0][2]["x-api-key"], KEY)
        self.assertEqual(st["hosts"], ["api.anthropic.com allowed"])

    def test_a_vendor_that_echoes_the_key_never_reaches_the_module(self):
        self.upstream(lambda m, p, h, b: (200, b'{"echo": "' + KEY.encode() + b'"}'))
        self.mode(mode="call", requests=[{"url": "https://api.anthropic.com/v1/x"}])
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        self.assertEqual(self.result()["result"]["replies"][0]["status"], 502)
        self.assertNotIn(KEY, json.dumps(self.result()))

    def test_isolation(self):
        data = modules.data_dir("probe")
        data.mkdir(parents=True, exist_ok=True)
        (data / "ledger").write_text("collector data")
        cfg = modules.config_dir("probe")
        (cfg / "config.toml").write_text("ok\n")
        feed = modules.feed_dir()
        feed.mkdir(parents=True, exist_ok=True)
        (feed / "host.json").write_text("{}")
        reads = [str(data / "ledger"), str(cfg / "config.toml"),
                 str(module_fetch.keys_dir() / "k1"), str(feed / "host.json"),
                 str(self.state / "session.key"), str(self.home / ".ssh" / "id_test"),
                 "/run/corral/key"]
        self.mode(mode="isolation", read=reads)
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        out = self.result()["result"]
        for r in reads:
            self.assertTrue(out[r].startswith("refused"), r)
        self.assertTrue(out["direct"].startswith("refused"))
        self.assertIn(" 405 ", out["connect"])
        self.assertEqual((out["other_host"], out["sign_in_host"], out["plain_http"]),
                         (403, 403, 403))
        self.assertFalse([k for k in out["env"] if k.startswith("CORRAL_READ_")])
        self.assertNotIn("CORRAL_FETCH_KEY", out["env"])
        self.assertIn("CORRAL_FETCH_PARAM_LOCATION", out["env"])
        self.assertIn("CORRAL_FETCH_API", out["env"])
        self.assertTrue(any("REFUSED" in h for h in st["hosts"]))
        for path in self.state.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY[:40].encode(), path.read_bytes(), path)

    def test_errors_are_scrubbed_and_back_off(self):
        self.mode(mode="fail")
        t0 = time.time()
        st = module_fetch.run_fetch("probe", "k1", now=t0)
        self.assertEqual(st["state"], "failing")
        for bad in ("abcdefghijklmnop", "https://"):
            self.assertNotIn(bad, json.dumps(st))
        self.assertGreaterEqual(st["next_due_at"], t0 + module_fetch.BACKOFF_MIN_S)
        st = module_fetch.run_fetch("probe", "k1", now=t0)
        self.assertEqual(st["failures"], 2)

    def test_size_and_time_limits(self):
        self.mode(mode="big")
        self.assertIn("cap", module_fetch.run_fetch("probe", "k1")["error"])
        self.mode(mode="sleep")
        t0 = time.monotonic()
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "failing")
        self.assertLess(time.monotonic() - t0, 30)

    def test_a_swapped_key_file_is_not_what_is_sent(self):
        up = self.upstream(lambda m, p, h, b: (200, b"{}"))
        self.mode(mode="call", requests=[{"url": "https://api.anthropic.com/v1/x"}])
        real = module_fetch.read_key

        def read_then_swap(name):
            data = real(name)
            p = module_fetch.keys_dir() / name
            p.unlink()
            p.write_text("SWAPPED-IN-SECRET-0123456789")
            os.chmod(p, 0o600)
            return data
        with mock.patch.object(module_fetch, "read_key", side_effect=read_then_swap):
            st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        self.assertEqual(up.seen[0][2]["x-api-key"], KEY)

    def test_revoke_waits_for_a_fetch_in_flight(self):
        import threading
        held = threading.Event()
        done = threading.Event()

        def hold():
            with modules._Lock("probe"):
                held.set()
                time.sleep(1.0)
            done.set()
        threading.Thread(target=hold).start()
        held.wait(5)
        t0 = time.monotonic()
        module_fetch.revoke("probe", "k1")
        self.assertTrue(done.is_set())
        self.assertGreater(time.monotonic() - t0, 0.5)

    def test_the_collector_reads_results_read_only(self):
        self.mode(mode="ok")
        module_fetch.run_fetch("probe", "k1")
        fd = modules.fetch_dir("probe")
        self.set_mode("isolation", f"read {fd}/k1.json", f"write {fd}/k1.json",
                      f"write {fd}/new.json")
        st = modules.run_collector("probe")
        self.assertEqual(st["state"], "ok", st.get("error"))
        snap = json.loads(modules.snapshot_path("probe").read_text())
        rows = dict(map(tuple, snap["view"][0]["rows"]))
        self.assertEqual(rows[f"read {fd}/k1.json"], "OPENED")
        self.assertTrue(rows[f"write {fd}/k1.json"].startswith("refused"))
        self.assertTrue(rows[f"write {fd}/new.json"].startswith("refused"))

    def test_runner_fetches_when_due_and_queues_the_collector(self):
        self.mode(mode="ok")
        r = modules.Runner(grace_s=0)
        r.fetch_tick()
        self.assertEqual(module_fetch.load_status("probe")["k1"]["state"], "ok")
        self.assertIn("probe", r._queued)
        self.assertEqual(module_fetch.due("probe", modules.load_pins()["probe"]), [])

    def test_revoke_and_remove_delete_results(self):
        self.mode(mode="ok")
        module_fetch.run_fetch("probe", "k1")
        module_fetch.revoke("probe", "k1")
        self.assertIsNone(self.result())
        self.assertFalse(module_fetch.work_dir("probe", "k1").exists())
        module_fetch.grant("probe", "k1", "anthropic")
        module_fetch.run_fetch("probe", "k1")
        modules.remove("probe", out=quiet)
        self.assertFalse(modules.fetch_dir("probe").exists())

    def test_doctor_and_summary_show_grants(self):
        self.mode(mode="ok")
        module_fetch.run_fetch("probe", "k1")
        s = modules.summary("probe")
        self.assertEqual(s["fetch"]["k1"]["state"], "ok")
        import doctor
        lines = "\n".join(doctor.module_lines())
        self.assertIn("fetcher, key k1 (anthropic): ok", lines)
        self.assertNotIn(KEY, lines)


if __name__ == "__main__":
    unittest.main()
