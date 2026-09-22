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
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentArena — Live Match</title>
  <style>
    :root { --bg:#0b0f14; --panel:#11161d; --panel2:#0e1319; --line:#1f2731;
            --text:#dbe2ea; --muted:#8b98a5; --alpha:#4da3ff; --bravo:#ff7a59; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text);
           font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
    .app { max-width:1200px; margin:0 auto; padding:16px; height:100vh;
           display:flex; flex-direction:column; gap:12px; }
    header { display:flex; align-items:center; gap:14px; padding:12px 16px;
             background:var(--panel); border:1px solid var(--line); border-radius:10px; }
    header .logo { font-size:18px; font-weight:700; }
    header .meta { color:var(--muted); font-size:13px; }
    .badge { padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700;
             border:1px solid var(--line); background:var(--panel2); color:var(--muted); }
    header .clock { margin-left:auto; font-variant-numeric:tabular-nums; color:var(--muted); }
    main { flex:1; display:grid; grid-template-columns:1fr 64px 1fr; gap:12px; min-height:0; }
    .pane { display:flex; flex-direction:column; background:var(--panel);
            border:1px solid var(--line); border-radius:10px; overflow:hidden; min-height:0; }
    .pane.alpha { border-top:3px solid var(--alpha); }
    .pane.bravo { border-top:3px solid var(--bravo); }
    .pane .head { padding:10px 14px; border-bottom:1px solid var(--line);
                  display:flex; align-items:baseline; gap:10px; background:var(--panel2); }
    .pane .name { font-weight:700; } .pane.alpha .name{color:var(--alpha);} .pane.bravo .name{color:var(--bravo);}
    .pane .role { color:var(--muted); font-size:12px; }
    .chat { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px; }
    .msg { max-width:82%; padding:8px 12px; border-radius:12px; line-height:1.35; font-size:14px;
           background:#18202a; border:1px solid var(--line); white-space:pre-wrap; word-wrap:break-word;
           animation:pop .18s ease-out; }
    @keyframes pop { from{opacity:0; transform:translateY(6px);} to{opacity:1;} }
    .alpha .msg { align-self:flex-start; border-left:3px solid var(--alpha); }
    .bravo .msg { align-self:flex-end; border-right:3px solid var(--bravo); }
    .msg .meta { display:block; font-size:11px; color:var(--muted); margin-bottom:4px; }
    .msg.battle { border-color:#2b3a4a; background:#1a2632; }
    .vs { display:flex; align-items:center; justify-content:center; }
    .vs .coin { width:52px; height:52px; border-radius:50%; background:var(--panel);
                border:1px solid var(--line); display:flex; align-items:center;
                justify-content:center; font-weight:800; color:var(--muted); }
    /* ---- Judge console ---- */
    .judge { background:var(--panel); border:1px solid var(--line); border-radius:10px;
             padding:12px 16px; display:grid; grid-template-columns:220px 1fr 1fr; gap:16px; align-items:center; }
    .judge .title { font-size:11px; letter-spacing:2px; color:var(--muted); text-transform:uppercase; }
    .judge .big { font-size:22px; font-weight:800; font-variant-numeric:tabular-nums; margin:2px 0; }
    .judge .rule { font-size:11px; color:var(--muted); }
    .judge .side { font-size:13px; }
    .judge .side .who { font-weight:700; }
    .judge .side.alpha .who{color:var(--alpha);} .judge .side.bravo .who{color:var(--bravo);}
    .judge .counts { color:var(--muted); font-size:12px; margin-left:6px; }
    .pips { display:inline-flex; gap:3px; margin-left:8px; vertical-align:middle; }
    .pip { width:10px; height:10px; border-radius:50%; background:#243041; border:1px solid var(--line); }
    .pip.err { background:var(--bad); border-color:var(--bad); }  /* incorrect */
    .pip.ok { background:var(--ok); border-color:var(--ok); }      /* correct (winning) */
    .judge .right { display:flex; flex-direction:column; gap:6px; align-items:flex-end; }
    .banner { font-weight:800; font-size:15px; padding:6px 14px; border-radius:8px; display:none; }
    .banner.show{display:block;} .banner.win{background:#14321f;color:var(--ok);border:1px solid #1f4a33;}
    .stat { color:var(--muted); font-size:13px; } .stat b { color:var(--text); }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div class="logo">&#9876; AgentArena</div>
      <div class="meta">live match</div>
      <div class="badge" id="phase">connecting&hellip;</div>
      <div class="clock" id="clock">T+ 00:00</div>
    </header>
    <main>
      <section class="pane alpha">
        <div class="head"><span class="name">ALPHA</span><span class="role">defending its treasure</span></div>
        <div class="chat" id="chat-alpha"></div>
      </section>
      <div class="vs"><div class="coin">VS</div></div>
      <section class="pane bravo">
        <div class="head"><span class="name">BRAVO</span><span class="role">defending its treasure</span></div>
        <div class="chat" id="chat-bravo"></div>
      </section>
    </main>
    <section class="judge">
      <div>
        <div class="title">&#9878; Judge</div>
        <div class="big" id="j-clock">T+ 00:00</div>
        <div class="rule">rate limit: <b id="rl-limit">10</b> submissions / min / model</div>
      </div>
      <div>
        <div class="side alpha" style="margin-bottom:8px;">
          <span class="who">ALPHA</span><span class="counts">total <b id="att-alpha">0</b> &middot; this min</span>
          <span class="pips" id="pips-alpha"></span>
        </div>
        <div class="side bravo">
          <span class="who">BRAVO</span><span class="counts">total <b id="att-bravo">0</b> &middot; this min</span>
          <span class="pips" id="pips-bravo"></span>
        </div>
      </div>
      <div class="right">
        <div class="stat" id="feed-status">waiting&hellip;</div>
        <div class="banner win" id="banner"></div>
      </div>
    </section>
  </div>
  <script>
    let since = 0; let start = Date.now();
    const counts = { alpha: 0, bravo: 0 };
    const $ = id => document.getElementById(id);
    function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function fmt(sec){ sec=Math.max(0,Math.floor(sec)); const m=String(Math.floor(sec/60)).padStart(2,'0'); const s=String(sec%60).padStart(2,'0'); return 'T+ '+m+':'+s; }

    // ---- commentary feed ----
    function addMsg(e){
      const chat = $('chat-' + e.side); if (!chat) return;
      counts[e.side] = (counts[e.side] || 0) + 1;
      const d = document.createElement('div');
      d.className = 'msg ' + (e.phase === 'battle' ? 'battle' : '');
      const t = new Date(e.ts * 1000).toLocaleTimeString();
      d.innerHTML = '<span class="meta">' + esc(t) + ' &middot; ' + esc(e.phase) + '</span>' + esc(e.text);
      chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
    }
    async function pollFeed(){
      try { const r = await fetch('/feed?since=' + since); const items = await r.json();
        for (const e of items) { since = Math.max(since, e.ts); addMsg(e); } } catch (err) {}
      setTimeout(pollFeed, 1000);
    }

    // ---- judge status ----
    function renderPips(side, verdicts, limit){
      const c = $('pips-' + side); c.innerHTML = '';
      for (let i = 0; i < limit; i++){
        const p = document.createElement('span');
        let cls = 'pip';
        if (i < verdicts.length) cls += verdicts[i] === 'correct' ? ' ok' : ' err';
        p.className = cls;
        c.appendChild(p);
      }
      c.title = verdicts.length + '/' + limit + ' used this minute (red=incorrect, green=correct)';
    }
    function setPhase(state){
      const map = { fortifying:'FORTIFY', battling:'BATTLE', finished:'FINISHED', fortify:'FORTIFY', battle:'BATTLE' };
      $('phase').textContent = map[state] || String(state || 'live').toUpperCase();
    }
    async function pollStatus(){
      let local = true;
      try {
        const s = await (await fetch('/status')).json();
        if (s && s.match_status !== 'unknown') {
          const match = s.match || {};
          const elapsed = (typeof match.elapsed_seconds === 'number') ? match.elapsed_seconds : (Date.now()-start)/1000;
          $('j-clock').textContent = fmt(elapsed); $('clock').textContent = fmt(elapsed); local = false;
          setPhase(match.state || s.match_status);
          const lim = (s.limits && s.limits.per_window) || 10;
          $('rl-limit').textContent = lim;
          const att = s.attempts || {}; const win = s.window || {};
          $('att-alpha').textContent = att.alpha || 0; $('att-bravo').textContent = att.bravo || 0;
          renderPips('alpha', (win.alpha && win.alpha.verdicts) || [], lim);
          renderPips('bravo', (win.bravo && win.bravo.verdicts) || [], lim);
          if (s.match_status === 'finished') {
            const b = $('banner');
            b.innerHTML = s.winner ? ('&#127942; ' + esc(String(s.winner).toUpperCase()) + ' WINS') : 'DRAW';
            b.classList.add('show'); $('feed-status').textContent = 'finished';
          } else { $('feed-status').textContent = 'live'; }
        } else {
          $('feed-status').textContent = (s && s.message) || 'no judge attached';
        }
      } catch (err) { $('feed-status').textContent = 'reconnecting&hellip;'; }
      if (local) { const t = fmt((Date.now()-start)/1000); $('j-clock').textContent = t; $('clock').textContent = t; }
      setTimeout(pollStatus, 1000);
    }
    pollFeed(); pollStatus();
  </script>
</body>
</html>"""


def create_app(store: FeedStore, status_provider=None) -> FastAPI:
    """Arena feed server.

    `status_provider` is an optional zero-arg callable returning judge/match
    status (attempts, rate-limit window usage, match state, winner). When
    provided, the UI's judge console shows live data; otherwise it shows a
    "no judge attached" placeholder.
    """
    app = FastAPI(title="AgentArena Feed", version="0.1.0")
    app.state.feed = store
    app.state.status_provider = status_provider

    @app.get("/feed")
    def feed(
        request: Request,
        side: str | None = Query(default=None),
        since: float | None = Query(default=None),
        limit: int | None = Query(default=None),
    ) -> list[dict]:
        store_: FeedStore = request.app.state.feed
        return store_.entries(side=side, since=since, limit=limit)

    @app.get("/status")
    def status(request: Request) -> dict:
        provider = request.app.state.status_provider
        if provider is None:
            return {"match_status": "unknown", "message": "no judge attached"}
        try:
            return provider()
        except Exception as exc:  # pragma: no cover - defensive
            return {"match_status": "error", "message": str(exc)}

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
