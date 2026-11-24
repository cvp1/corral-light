#!/usr/bin/python3
"""Talk (talk.py): the 🎙 drawer's hub side. A fake credential broker stands in
for the real one, so no token and no Home Assistant are involved. Collected by
test_corral_light.py."""
import base64
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

import talk  # noqa: E402

# A broker stand-in: answers like HA by --url path and logs its argv.
FAKE_BROKER = r'''
import json, sys
a = sys.argv[1:]
log, mode = a[0], a[1]
a = a[2:]
url = a[a.index("--url") + 1]
body = open(a[a.index("--data-file") + 1], "rb").read() if "--data-file" in a else b""
open(log, "a").write(json.dumps(a + ["BODY", body[:200].decode("latin-1")]) + "\n")
if mode == "fail":
    sys.stderr.write("secret: fetch failed: HTTP 401 Unauthorized\n"); sys.exit(1)
if "/api/stt/" in url:
    print(json.dumps({"result": "success", "text": "what is the battery at"}))
elif url.endswith("/api/conversation/process"):
    req = json.loads(body)
    print(json.dumps({"conversation_id": "c1", "response": {"response_type": "action_done",
          "speech": {"plain": {"speech": "Heard: " + req["text"] + " via " + req["agent_id"]}}}}))
elif url.endswith("/api/tts_get_url"):
    print(json.dumps({"path": "/api/tts_proxy/abc.wav"}))
'''


class _Proxy(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        self.wfile.write(b"RIFFfake")

    def log_message(self, *a):
        pass


class TalkTurns(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tmpdir(self, "corral-light-talk-"))
        self.broker = self.dir / "broker.py"
        self.broker.write_text(FAKE_BROKER)
        self.log = self.dir / "argv.log"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def cfg(self, mode="ok", **over):
        c = {"url": f"http://127.0.0.1:{self.srv.server_address[1]}",
             "agent_id": "conversation.sasha_local", "stt": "stt.whisper",
             "tts": "tts.piper", "title": "Sasha (local)",
             "fetch": [sys.executable, str(self.broker), str(self.log), mode]}
        c.update(over)
        f = self.dir / "talk.json"
        f.write_text(json.dumps(c))
        return talk.load(f)

    def argvs(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def test_absent_config_is_off(self):
        self.assertIsNone(talk.load(self.dir / "nope.json"))
        self.assertEqual(talk.public(None), {"enabled": False})

    def test_public_view_never_names_the_agent_or_broker(self):
        pub = json.dumps(talk.public(self.cfg()))
        for secretish in ("conversation.", "127.0.0.1", "broker", "stt.", "tts."):
            self.assertNotIn(secretish, pub)

    def test_typed_turn_goes_to_the_configured_agent_only(self):
        r = talk.turn(self.cfg(), {"text": "hello", "agent_id": "conversation.cloud"})
        self.assertEqual(r["reply"], "Heard: hello via conversation.sasha_local")
        self.assertEqual(r["conversationId"], "c1")
        self.assertIsNone(r["heard"])
        self.assertNotIn("audio", r)

    def test_spoken_turn_transcribes_with_the_speech_header_then_speaks(self):
        pcm = b"\x00\x01" * 16000
        r = talk.turn(self.cfg(), {"audio": base64.b64encode(pcm).decode(), "speak": True})
        self.assertEqual(r["heard"], "what is the battery at")
        self.assertIn("what is the battery at", r["reply"])
        self.assertEqual(r["audio"]["mime"], "audio/wav")
        self.assertEqual(base64.b64decode(r["audio"]["b64"]), b"RIFFfake")
        stt = self.argvs()[0]
        hdr = stt[stt.index("--header") + 1]
        self.assertTrue(hdr.startswith("X-Speech-Content: format=wav; codec=pcm; sample_rate=16000"))

    def test_conversation_id_is_carried_and_checked(self):
        cfg = self.cfg()
        talk.turn(cfg, {"text": "again", "conversationId": "c1"})
        self.assertEqual(json.loads(self.argvs()[-1][-1])["conversation_id"], "c1")
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"text": "x", "conversationId": "../../etc"})

    def test_limits(self):
        cfg = self.cfg()
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"text": "x" * (talk.TEXT_MAX + 1)})
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"text": "   "})
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"audio": base64.b64encode(b"\x00" * (talk.PCM_MAX + 2)).decode()})
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"audio": "not base64!!"})

    def test_chat_only_when_no_stt(self):
        cfg = self.cfg(stt=None)
        self.assertFalse(talk.public(cfg)["talk"])
        with self.assertRaises(talk.TalkError):
            talk.turn(cfg, {"audio": base64.b64encode(b"\x00\x00" * 100).decode()})

    def test_broker_failure_is_a_readable_error(self):
        with self.assertRaisesRegex(talk.TalkError, "401"):
            talk.turn(self.cfg("fail"), {"text": "hi"})

    def test_bad_config_is_loud(self):
        for bad in ({"url": "file:///etc"}, {"agent_id": "nope"}, {"fetch": "bin/secret"},
                    {"url": "http://h:8123/api"}, {"language": "en; rm"}):
            with self.subTest(bad=bad), self.assertRaises(talk.TalkError):
                self.cfg(**bad)


if __name__ == "__main__":
    unittest.main()
