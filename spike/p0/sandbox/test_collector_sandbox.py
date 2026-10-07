"""Phase 0: prove the collector sandbox profile on this host, with real bwrap.

Run: cd spike && python3 -m unittest test_collector_sandbox -v

Never reads or prints a real credential: the real session.key and login
paths are only tested with os.path.exists inside the sandbox.
"""
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import collector_sandbox as cs  # noqa: E402
import review_sandbox  # noqa: E402

HOME = str(Path.home())
REAL_STATE = os.path.join(HOME, ".local/share/corral-light")
REAL_KEY = os.path.join(REAL_STATE, "session.key")
HUB_PORT = int(os.environ.get("CORRAL_LIGHT_PORT", "8098"))   # hub.py's default
HAVE_BWRAP = cs.available()[0]


def _hub_listening():
    try:
        with socket.create_connection(("127.0.0.1", HUB_PORT), timeout=1):
            return True
    except OSError:
        return False


@unittest.skipUnless(HAVE_BWRAP, "bwrap not installed")
class CollectorSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixtures live under $HOME (not /tmp, which the sandbox replaces) and
        # mirror Light's layout: <state>/session.key beside the module dirs.
        cls.root = tempfile.mkdtemp(prefix="collector-sbx-", dir=os.path.join(HOME, ".cache"))
        cls.state = os.path.join(cls.root, "state")
        cls.data = os.path.join(cls.state, "module-data", "finops")
        cls.feed = os.path.join(cls.state, "module-feed", "v1")
        cls.usage = os.path.join(cls.root, "claude-projects")
        for d in (cls.data, cls.feed, cls.usage):
            os.makedirs(d)
        cls.fake_key = os.path.join(cls.state, "session.key")
        Path(cls.fake_key).write_text("dummy-not-a-secret\n")
        Path(cls.usage, "t.jsonl").write_text('{"usage": {"input_tokens": 3}}\n')
        Path(cls.feed, "panes.json").write_text("[]\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_py(self, code, reads=None, timeout=30):
        argv = cs.build_argv([sys.executable, "-I", "-c", code],
                             [self.usage] if reads is None else reads,
                             self.feed, self.data)
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout)

    # (1) session.key: fake (sibling of the data dir) and real, both absent
    def test_session_key_absent(self):
        self.assertTrue(os.path.exists(self.fake_key))           # outside: present
        out = self.run_py(
            "import os,json;print(json.dumps([os.path.exists(p) for p in "
            f"[{self.fake_key!r},{self.state!r}+'/module-data',{REAL_KEY!r},{REAL_STATE!r}]]))")
        self.assertEqual(out, [False, True, False, False])
        # module-data exists only as the parent of the bound data dir; the
        # data dir's siblings do not.
        out = self.run_py(f"import os,json;print(json.dumps(sorted(os.listdir({self.state!r}))))")
        self.assertEqual(out, ["module-data", "module-feed"])

    # (2) no network: not the hub on loopback, not the internet
    def test_no_network(self):
        code = ("import socket,json\nres={}\n"
                f"for h,p in [('127.0.0.1',{HUB_PORT}),('1.1.1.1',443),('::1',{HUB_PORT})]:\n"
                "    try:\n"
                "        socket.create_connection((h,p),timeout=3).close(); res[h]='CONNECTED'\n"
                "    except OSError as e: res[h]=type(e).__name__+':'+str(e.errno)\n"
                "import os\nres['ifaces']=sorted(n for _,n in socket.if_nameindex())\n"
                "print(json.dumps(res))")
        out = self.run_py(code)
        for h in ("127.0.0.1", "1.1.1.1", "::1"):
            self.assertNotEqual(out[h], "CONNECTED", h)
        self.assertEqual(out["ifaces"], ["lo"])
        if not _hub_listening():
            self.skipTest(f"hub not listening on {HUB_PORT} outside; loopback check is vacuous")

    # (3) read a declared path, read the feed, write the data dir
    def test_read_declared_and_write_data(self):
        out = self.run_py(
            "import os,json\n"
            f"a=open({self.usage!r}+'/t.jsonl').read()\n"
            f"b=open({self.feed!r}+'/panes.json').read()\n"
            f"open({self.data!r}+'/snap.json','w').write('ok')\n"
            "print(json.dumps([a,b,os.getcwd(),os.environ['HOME']]))")
        self.assertIn("input_tokens", out[0])
        self.assertEqual(out[1], "[]\n")
        self.assertEqual(out[2:], [self.data, self.data])
        self.assertEqual(Path(self.data, "snap.json").read_text(), "ok")

    # (4) declared read paths and the feed are read-only
    def test_cannot_write_read_paths(self):
        code = ("import json,errno\nres=[]\n"
                f"for p in [{self.usage!r}+'/new',{self.usage!r}+'/t.jsonl',{self.feed!r}+'/x','/usr/x','/etc/x','/x',{HOME!r}+'/x',{self.state!r}+'/x']:\n"
                "    try:\n        open(p,'a').write('x'); res.append('WROTE')\n"
                "    except OSError as e: res.append(errno.errorcode[e.errno])\n"
                "print(json.dumps(res))")
        out = self.run_py(code)
        self.assertNotIn("WROTE", out)
        self.assertFalse(os.path.exists(os.path.join(self.usage, "new")))
        self.assertEqual(Path(self.usage, "t.jsonl").read_text(), '{"usage": {"input_tokens": 3}}\n')

    # (5) home's secrets and other logins do not exist
    def test_home_secrets_absent(self):
        names = [".ssh", ".claude", ".codex", ".grok", ".gemini", ".config", ".gnupg",
                 ".local/share/corral-light", ".local/share/corral", "aios/keyvault",
                 ".git-credentials", ".netrc"]
        names += list(review_sandbox.SECRET_DIRS) + list(review_sandbox.SECRET_FILES)
        paths = sorted({os.path.join(HOME, n) for n in names})
        paths += [str(f) for fs in review_sandbox.lane_logins().values() for f in fs]
        out = self.run_py(
            "import os,json;print(json.dumps({p:os.path.lexists(p) for p in "
            f"{paths!r}}}))")
        self.assertEqual([p for p, v in out.items() if v], [])
        # Home itself holds only the path down to the bound fixtures.
        out = self.run_py(f"import os,json;print(json.dumps(os.listdir({HOME!r})))")
        self.assertEqual(out, [".cache"])
        out = self.run_py(f"import os,json;print(json.dumps(os.listdir({HOME!r}+'/.cache')))")
        self.assertEqual(out, [os.path.basename(self.root)])

    def test_isolation_extras(self):
        out = self.run_py(
            "import os,json;print(json.dumps({'pids':sorted(int(p) for p in os.listdir('/proc') if p.isdigit()),"
            "'run':os.path.exists('/run'),'tmp':os.listdir('/tmp'),'env':sorted(os.environ),"
            "'shadow':os.path.exists('/etc/shadow'),'uid':os.getuid()}))")
        self.assertLessEqual(len(out["pids"]), 3)     # bwrap init + python only
        self.assertFalse(out["run"])
        self.assertEqual(out["tmp"], [])
        self.assertEqual(out["env"], ["HOME", "LANG", "PATH", "PWD"])
        self.assertFalse(out["shadow"])

    def test_refuses_bad_paths(self):
        with self.assertRaises(cs.SandboxError):
            cs.build_argv(["true"], ["relative/path"], self.feed, self.data)
        with self.assertRaises(cs.SandboxError):
            cs.build_argv(["true"], [self.state], self.feed, self.data)   # covers data dir
        with self.assertRaises(cs.SandboxError):
            cs.build_argv(["true"], ["/"], self.feed, self.data)

    # (6) python runs; startup cost
    def test_python_startup(self):
        def median(argv, n=10):
            ts = []
            for _ in range(n):
                t = time.perf_counter()
                subprocess.run(argv, check=True, capture_output=True)
                ts.append(time.perf_counter() - t)
            return statistics.median(ts) * 1000
        outside = median([sys.executable, "-I", "-c", "pass"])
        inside = median(cs.build_argv([sys.executable, "-I", "-c", "pass"], [self.usage],
                                      self.feed, self.data))
        print(f"\nSTARTUP python3 -c pass median of 10: outside {outside:.1f} ms, "
              f"inside {inside:.1f} ms, overhead {inside - outside:.1f} ms", file=sys.stderr)
        self.assertLess(inside, 2000)


def _grok_binary():
    link = os.path.join(HOME, ".grok/bin/grok")
    if os.path.lexists(link):
        return os.path.realpath(link)
    w = shutil.which("grok")
    return os.path.realpath(w) if w else None


@unittest.skipUnless(HAVE_BWRAP, "bwrap not installed")
class GrokSingleFileBinding(unittest.TestCase):
    """Can the core run `grok` with only its resolved binary file bound?
    Runs `--version` only; nothing in ~/.grok besides the binary is touched."""

    def test_single_file_bind(self):
        binary = _grok_binary()
        if not binary or not os.path.isfile(binary):
            self.skipTest("grok not installed")
        scratch = tempfile.mkdtemp(prefix="grok-sbx-", dir=os.path.join(HOME, ".cache"))
        feed = os.path.join(scratch, "feed")
        home = os.path.join(scratch, "home")
        os.makedirs(feed)
        os.makedirs(home)
        try:
            grok_dir = os.path.dirname(binary)
            probe = cs.build_argv(
                [sys.executable, "-I", "-c",
                 f"import os,json;print(json.dumps(sorted(os.listdir({grok_dir!r}))))"],
                [binary], feed, home)
            r = subprocess.run(probe, capture_output=True, text=True, timeout=30)
            self.assertEqual(json.loads(r.stdout), [os.path.basename(binary)])
            argv = cs.build_argv([binary, "--version"], [binary], feed, home)
            r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            print(f"\nGROK {binary} single-file bind: exit {r.returncode}, "
                  f"stdout {r.stdout.strip()[:80]!r}, stderr {r.stderr.strip()[-160:]!r}",
                  file=sys.stderr)
            self.assertEqual(r.returncode, 0)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
