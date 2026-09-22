"""Tests for the Arena feed store and HTTP server."""

import contextlib
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import uvicorn

from agentarena.arena.feed import FeedStore
from agentarena.arena.server import create_app


@contextlib.contextmanager
def serve(app):
    """Run a FastAPI app under uvicorn in a background thread; yield base URL."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("server failed to start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


class TestFeedStore(unittest.TestCase):
    def test_add_and_sort(self):
        store = FeedStore()
        store.add({"side": "bravo", "phase": "battle", "text": "b", "ts": 2.0})
        store.add({"side": "alpha", "phase": "fortify", "text": "a", "ts": 1.0})
        entries = store.entries()
        self.assertEqual([e["ts"] for e in entries], [1.0, 2.0])

    def test_filters(self):
        store = FeedStore()
        for i, side in enumerate(["alpha", "bravo", "alpha"]):
            store.add({"side": side, "phase": "battle", "text": str(i), "ts": float(i)})
        self.assertEqual(len(store.entries(side="alpha")), 2)
        self.assertEqual(len(store.entries(since=0.5)), 2)
        self.assertEqual(len(store.entries(limit=1)), 1)

    def test_reads_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alpha.jsonl"
            path.write_text(
                json.dumps({"side": "alpha", "phase": "battle", "text": "hi", "ts": 1.0}) + "\n",
                encoding="utf-8",
            )
            store = FeedStore([path])
            self.assertEqual(len(store.entries()), 1)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestFeedServer(unittest.TestCase):
    def setUp(self):
        self.store = FeedStore()
        self.store.add({"side": "alpha", "phase": "battle", "text": "attacking", "ts": 1.0})
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(self.store), host="127.0.0.1", port=self.port, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        if not self.server.started:
            self.fail("feed server failed to start")

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=5.0)

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_feed_endpoint(self):
        status, body = self._get("/feed")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["text"], "attacking")

    def test_feed_filter_by_side(self):
        status, body = self._get("/feed?side=bravo")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), [])

    def test_health(self):
        _, body = self._get("/health")
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_index_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("AgentArena", body)


class TestFeedStatus(unittest.TestCase):
    def test_status_with_provider(self):
        payload = {
            "match_status": "ongoing",
            "winner": None,
            "attempts": {"alpha": 1, "bravo": 2},
            "limits": {"per_window": 10, "window_seconds": 60},
            "window": {"alpha": {"used": 1, "limit": 10}, "bravo": {"used": 2, "limit": 10}},
            "match": {"state": "battling", "elapsed_seconds": 12.3},
        }
        app = create_app(FeedStore(), status_provider=lambda: payload)
        with serve(app) as base:
            status, body = _get(base, "/status")
            self.assertEqual(status, 200)
            self.assertEqual(body["match_status"], "ongoing")
            self.assertEqual(body["window"]["bravo"]["used"], 2)
            self.assertEqual(body["match"]["state"], "battling")

    def test_status_fallback_without_provider(self):
        app = create_app(FeedStore())
        with serve(app) as base:
            status, body = _get(base, "/status")
            self.assertEqual(status, 200)
            self.assertEqual(body["match_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
