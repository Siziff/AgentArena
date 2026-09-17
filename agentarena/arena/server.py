"""Arena feed HTTP server + minimal live HTML view.

Run standalone:
    uvicorn agentarena.arena.server:app --host 127.0.0.1 --port 8001

Endpoints:
- GET /feed?side=&since=&limit=  -> JSON list of commentary entries
- GET /health                   -> liveness
- GET /                         -> tiny HTML page that polls /feed
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

from .feed import FeedStore

_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AgentArena — Live Feed</title>
  <style>
    body { font-family: ui-monospace, monospace; background:#0b0f14; color:#dbe2ea; margin:0; padding:24px; }
    h1 { font-size:18px; }
    .entry { padding:6px 10px; margin:4px 0; border-radius:6px; background:#131a22; }
    .alpha { border-left:4px solid #4da3ff; }
    .bravo { border-left:4px solid #ff7a59; }
    .meta { color:#8b98a5; font-size:12px; margin-right:8px; }
  </style>
</head>
<body>
  <h1>AgentArena — Live Commentary</h1>
  <div id="feed"></div>
  <script>
    let since = 0;
    function esc(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
      }[c]));
    }
    async function poll() {
      try {
        const r = await fetch('/feed?since=' + since);
        const items = await r.json();
        for (const e of items) {
          since = Math.max(since, e.ts);
          const d = document.createElement('div');
          d.className = 'entry ' + esc(e.side);
          const t = new Date(e.ts * 1000).toLocaleTimeString();
          d.innerHTML = '<span class="meta">' + esc(t) + ' [' + esc(e.side) + '/' +
                        esc(e.phase) + ']</span>' + esc(e.text);
          document.getElementById('feed').appendChild(d);
        }
      } catch (err) { /* keep polling */ }
      setTimeout(poll, 1000);
    }
    poll();
  </script>
</body>
</html>"""


def create_app(store: FeedStore) -> FastAPI:
    app = FastAPI(title="AgentArena Feed", version="0.1.0")
    app.state.feed = store

    @app.get("/feed")
    def feed(
        request: Request,
        side: str | None = Query(default=None),
        since: float | None = Query(default=None),
        limit: int | None = Query(default=None),
    ) -> list[dict]:
        store_: FeedStore = request.app.state.feed
        return store_.entries(side=side, since=since, limit=limit)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    return app


def create_default_app() -> FastAPI:
    """App backed by JSONL files listed in AA_FEED_FILES (os.pathsep-separated)."""
    paths = [Path(p) for p in os.getenv("AA_FEED_FILES", "").split(os.pathsep) if p]
    return create_app(FeedStore(paths))


app = create_default_app()

__all__ = ["app", "create_app", "create_default_app"]
