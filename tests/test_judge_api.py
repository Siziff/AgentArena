"""Integration test: run the real Judge FastAPI app under uvicorn in a
background thread and talk to it over HTTP with the JudgeClient (stdlib).
"""

import json
import socket
import threading
import time
import unittest
import urllib.request

import uvicorn

from agentarena.core.types import Side
from agentarena.judge.app import create_app
from agentarena.judge.client import HttpJudgeClient
from agentarena.judge.core import JudgeCore

ALPHA_T = "a" * 128
BRAVO_T = "b" * 128


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class JudgeServer:
    """Context manager running a uvicorn server for a JudgeCore in a thread."""

    def __init__(self, core: JudgeCore):
        self.core = core
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(core), host="127.0.0.1", port=self.port, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "JudgeServer":
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("judge server failed to start")
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5.0)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))


def make_core(rate=100) -> JudgeCore:
    core = JudgeCore(rate_limit_per_minute=rate, window_seconds=60)
    core.register_secret(Side.ALPHA, ALPHA_T)
    core.register_secret(Side.BRAVO, BRAVO_T)
    return core


class TestJudgeApi(unittest.TestCase):
    def test_health(self):
        with JudgeServer(make_core()) as srv:
            self.assertEqual(srv.get("/health"), {"status": "ok"})

    def test_submit_correct_then_finished(self):
        with JudgeServer(make_core()) as srv:
            alpha = HttpJudgeClient(srv.base_url, Side.ALPHA)
            out = alpha.submit(BRAVO_T)
            self.assertEqual(out["http_status"], 200)
            self.assertEqual(out["verdict"], "correct")
            self.assertEqual(out["winner"], "alpha")

            status = srv.get("/status")
            self.assertEqual(status["match_status"], "finished")
            self.assertEqual(status["winner"], "alpha")

            # further submissions are refused with 409
            bravo = HttpJudgeClient(srv.base_url, Side.BRAVO)
            out2 = bravo.submit(ALPHA_T)
            self.assertEqual(out2["http_status"], 409)
            self.assertEqual(out2["error"], "match_finished")

    def test_submit_incorrect_and_invalid(self):
        with JudgeServer(make_core()) as srv:
            alpha = HttpJudgeClient(srv.base_url, Side.ALPHA)
            bad = alpha.submit("c" * 128)
            self.assertEqual(bad["http_status"], 200)
            self.assertEqual(bad["verdict"], "incorrect")

            invalid = alpha.submit("short")
            self.assertEqual(invalid["http_status"], 400)
            self.assertEqual(invalid["error"], "invalid_request")

    def test_rate_limited(self):
        with JudgeServer(make_core(rate=2)) as srv:
            alpha = HttpJudgeClient(srv.base_url, Side.ALPHA)
            self.assertEqual(alpha.submit("c" * 128)["http_status"], 200)
            self.assertEqual(alpha.submit("d" * 128)["http_status"], 200)
            limited = alpha.submit("e" * 128)
            self.assertEqual(limited["http_status"], 429)
            self.assertEqual(limited["error"], "rate_limited")
            self.assertGreater(limited["retry_after_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
