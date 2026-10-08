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
        self.assertEqual(modules.FETCH_VENDORS["gcp"],
                         ("oauth2.googleapis.com", "bigquery.googleapis.com"))
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


@unittest.skipUnless(HAVE_BWRAP, "needs bubblewrap")
class Runs(Base):
    def setUp(self):
        super().setUp()
        self.install(self.make_repo(mutate=with_fetcher(("anthropic", "gcp"))))
        module_fetch.key_add("k1", KEY)
        module_fetch.grant("probe", "k1", "anthropic", {"location": "US"})

    def mode(self, **m):
        wd = module_fetch.work_dir("probe", "k1")
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "mode.json").write_text(json.dumps(m))

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
        self.assertNotIn(KEY, json.dumps(st))

    def test_the_key_in_a_result_is_refused(self):
        for m in ("leak", "leak_b64", "leak_u", "leak_hex"):
            with self.subTest(m=m):
                self.mode(mode=m)
                st = module_fetch.run_fetch("probe", "k1")
                self.assertEqual(st["state"], "failing")
                self.assertIn("contains the key", st["error"])
                self.assertIsNone(self.result())

    def test_a_key_sent_as_a_proxy_target_is_kept_nowhere(self):
        self.mode(mode="connect_key")
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        self.assertEqual(st["hosts"], ["1 other host(s) REFUSED"])
        for path in self.state.rglob("*"):
            if path.is_file() and path.name != "k1":
                self.assertNotIn(KEY[:40].encode(), path.read_bytes(), path)

    def test_a_swapped_key_file_is_not_what_runs(self):
        """The run binds a private copy of the bytes it checked."""
        self.mode(mode="ok")
        real = module_fetch.read_key
        swapped = []

        def read_then_swap(name):
            data = real(name)
            p = module_fetch.keys_dir() / name
            p.unlink()
            p.write_text("SWAPPED-IN-SECRET-0123456789")
            os.chmod(p, 0o600)
            swapped.append(1)
            return data
        with mock.patch.object(module_fetch, "read_key", side_effect=read_then_swap):
            st = module_fetch.run_fetch("probe", "k1")
        self.assertTrue(swapped)
        self.assertEqual(st["state"], "ok", st.get("error"))
        self.assertFalse(list(module_fetch.modules.STATE.glob("module-egress/*")),
                         "the run's key copy was not removed")

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

    def test_errors_are_scrubbed_and_back_off(self):
        self.mode(mode="fail")
        t0 = time.time()
        st = module_fetch.run_fetch("probe", "k1", now=t0)
        self.assertEqual(st["state"], "failing")
        for bad in (KEY, "abcdefghijklmnop", "https://"):
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

    def test_isolation(self):
        data = modules.data_dir("probe")
        data.mkdir(parents=True, exist_ok=True)
        (data / "ledger").write_text("collector data")
        cfg = modules.config_dir("probe")
        (cfg / "config.toml").write_text("ok\n")
        module_fetch.key_add("k2", "OTHER-KEY-0123456789")
        feed = modules.feed_dir()
        feed.mkdir(parents=True, exist_ok=True)
        (feed / "host.json").write_text("{}")
        reads = [str(data / "ledger"), str(cfg / "config.toml"),
                 str(module_fetch.keys_dir() / "k2"), str(feed / "host.json"),
                 str(self.state / "session.key"), str(self.home / ".ssh" / "id_test"),
                 module_fetch.KEY_IN_SANDBOX]
        self.mode(mode="isolation", read=reads,
                  connect=["evil.api.anthropic.com", "example.com", "console.anthropic.com",
                           "bigquery.googleapis.com"])
        st = module_fetch.run_fetch("probe", "k1")
        self.assertEqual(st["state"], "ok", st.get("error"))
        out = self.result()["result"]
        for r in reads[:-1]:
            self.assertTrue(out[r].startswith("refused"), r)
        self.assertEqual(out["key_via_env"], "OPENED")
        if sys.platform != "darwin":                  # macOS has no mounts: no fixed path
            self.assertEqual(out[module_fetch.KEY_IN_SANDBOX], "OPENED")
        self.assertTrue(out["direct"].startswith("refused"))
        for h, line in out["proxy"].items():
            self.assertIn("403", line, h)              # the gcp host is not this grant's
        self.assertIn("HTTPS_PROXY", out["env"])
        self.assertIn("CORRAL_FETCH_PARAM_LOCATION", out["env"])
        self.assertFalse([k for k in out["env"] if k.startswith("CORRAL_READ_")])
        self.assertIn("4 other host(s) REFUSED", st["hosts"])
        self.assertFalse([h for h in st["hosts"] if "evil" in h], st["hosts"])

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
