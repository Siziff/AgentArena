"""Test the battle-control server (setup screen + start + live arena)."""

import contextlib
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from agentarena.arena.control import MatchController, StartRequest, create_app
from agentarena.cli import _ALPHA_TREASURE, _BRAVO_TREASURE, _demo_provider_factory
from agentarena.core.types import Side


@contextlib.contextmanager
def serve(app):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
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
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read().decode()


def _get_json(base, path):
    status, text = _get(base, path)
    return status, json.loads(text)


def _post_json(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestControl(unittest.TestCase):
    def make_controller(self, root):
        return MatchController(
            provider_factory=_demo_provider_factory,
            run_root=root,
            treasures={Side.ALPHA: _ALPHA_TREASURE, Side.BRAVO: _BRAVO_TREASURE},
            max_agent_iterations=10,
        )

    def test_setup_screen_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            with serve(create_app(self.make_controller(tmp))) as base:
                _, state = _get_json(base, "/api/state")
                self.assertEqual(state["state"], "setup")
                _, html = _get(base, "/")
                self.assertIn('id="fortify"', html)
                self.assertIn('value="60"', html)          # default 1-minute fortify
                self.assertIn('id="treasures"', html)      # treasures-per-side input
                self.assertIn("Treasures per side", html)
                self.assertIn("Start battle", html)
                self.assertIn("Quick test battle", html)   # one-click system check

    def test_setup_screen_shows_configured_treasure_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = MatchController(
                provider_factory=_demo_provider_factory,
                run_root=tmp,
                treasures_per_side=3,
                max_agent_iterations=10,
            )
            with serve(create_app(controller)) as base:
                _, html = _get(base, "/")
                self.assertIn('id="treasures"', html)
                self.assertIn('value="3"', html)

    def test_treasures_per_side_provisions_multiple_treasures(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = MatchController(
                provider_factory=_demo_provider_factory,
                run_root=tmp,
                max_agent_iterations=10,  # no pre-seeded treasures -> generated
            )
            res = controller.start(StartRequest(fortify_seconds=0, treasures_per_side=2))
            self.assertTrue(res["ok"])
            try:
                deadline = time.time() + 10
                while time.time() < deadline:
                    handles = controller.runner.handles
                    if len(handles) == 2 and all(
                        len(h.treasure_paths) == 2 for h in handles.values()
                    ):
                        break
                    time.sleep(0.05)
                handles = controller.runner.handles
                self.assertEqual(len(handles), 2)
                for handle in handles.values():
                    self.assertEqual(len(handle.treasure_paths), 2)
                    for p in handle.treasure_paths:
                        self.assertTrue(p.is_file())
                # the judge knows about all four treasures (2 per side)
                status = controller.runner.judge.status()
                self.assertEqual(status["progress"]["alpha"]["total"], 2)
                self.assertEqual(status["progress"]["bravo"]["total"], 2)
            finally:
                controller.stop()
                deadline = time.time() + 15
                while controller.state == "running" and time.time() < deadline:
                    time.sleep(0.1)
            self.assertEqual(controller.state, "finished")

    def test_quick_test_battle_forces_60s_and_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            with serve(create_app(controller)) as base:
                code, body = _post_json(base, "/api/start", {"test": True, "fortify_seconds": 0})
                self.assertTrue(body["ok"])
                # test mode forces a 60s fortify regardless of the requested value
                self.assertEqual(controller.runner.match.fortify_seconds, 60)

                # wait until the match actually enters FORTIFYING (the deadline is
                # set on entry), then collapse the window so it finishes fast.
                from agentarena.core.types import MatchState

                deadline = time.time() + 10
                while (
                    time.time() < deadline
                    and controller.runner.match.state is not MatchState.FORTIFYING
                ):
                    time.sleep(0.05)
                controller.runner.match._fortify_deadline_mono = time.monotonic()
                deadline = time.time() + 20
                status = {}
                while time.time() < deadline:
                    _, status = _get_json(base, "/status")
                    if status.get("match_status") == "finished":
                        break
                    time.sleep(0.2)
                self.assertEqual(status.get("match_status"), "finished")
                self.assertEqual(status.get("winner"), "alpha")

    def test_start_runs_mock_match_to_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            with serve(create_app(self.make_controller(tmp))) as base:
                code, body = _post_json(base, "/api/start", {"fortify_seconds": 0})
                self.assertTrue(body["ok"])
                self.assertIn("match_id", body)

                _, state = _get_json(base, "/api/state")
                self.assertEqual(state["state"], "running")

                # cannot start a second match while one is running
                code, _ = _post_json(base, "/api/start", {"fortify_seconds": 0})
                self.assertEqual(code, 409)

                # wait for the (fast, scripted) match to finish
                deadline = time.time() + 20
                status = {}
                while time.time() < deadline:
                    _, status = _get_json(base, "/status")
                    if status.get("match_status") == "finished":
                        break
                    time.sleep(0.2)
                self.assertEqual(status.get("match_status"), "finished")
                self.assertEqual(status.get("winner"), "alpha")

                # commentary is flowing for both sides
                _, feed = _get_json(base, "/feed")
                self.assertTrue(any(e["side"] == "alpha" for e in feed))
                self.assertTrue(any(e["side"] == "bravo" for e in feed))

    def test_stop_aborts_running_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.start(StartRequest(fortify_seconds=60))
            self.assertEqual(controller.state, "running")
            time.sleep(0.5)
            res = controller.stop()
            self.assertTrue(res["ok"])
            deadline = time.time() + 15
            while controller.state == "running" and time.time() < deadline:
                time.sleep(0.1)
            self.assertEqual(controller.state, "finished")
            self.assertEqual(controller.runner.match.result.value, "aborted")

    def test_stop_without_match_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            self.assertFalse(controller.stop()["ok"])

    def test_restart_starts_a_fresh_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.start(StartRequest(fortify_seconds=60))
            time.sleep(0.3)
            first_id = controller.runner.match.match_id
            res = controller.restart()
            self.assertTrue(res["ok"])
            self.assertEqual(controller.state, "running")
            self.assertNotEqual(controller.runner.match.match_id, first_id)
            controller.stop()
            # let the match thread wind down before the temp dir is cleaned up
            deadline = time.time() + 15
            while controller.state == "running" and time.time() < deadline:
                time.sleep(0.1)
            self.assertEqual(controller.state, "finished")


if __name__ == "__main__":
    unittest.main()
