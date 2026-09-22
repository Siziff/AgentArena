"""Match-control server: a setup screen + live arena in one page.

Flow:
1. GET / shows a setup overlay with the match parameters (for now: the stage-1
   fortification duration, default 60s) and a "Start battle" button.
2. POST /api/start launches a MatchRunner in a background thread.
3. The overlay hides and the live arena (two chat panes + judge console) is
   shown, driven by /feed (commentary) and /status (judge + match).

The parameter form is intentionally minimal and easy to extend: POST /api/start
accepts a JSON body, and the setup overlay is plain HTML — add more fields here
as the battle options grow.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..agent.prompts import DefensePolicy
from ..core.types import Side
from ..judge.core import JudgeCore
from ..orchestrator.runner import MatchRunner, RunnerConfig
from .feed import FeedStore


class StartRequest(BaseModel):
    fortify_seconds: int = Field(default=60, ge=0, le=3600)
    # one-click canned system check: forces a 60s fortify with the reference models
    test: bool = False
    # room for future battle parameters (rate limit, models, policy, ...)


class MatchController:
    """Owns the current match lifecycle for the control server."""

    def __init__(
        self,
        provider_factory,
        run_root,
        rate_limit_per_minute: int = 10,
        max_match_seconds: int = 0,
        policy: DefensePolicy | None = None,
        treasures: dict[Side, str] | None = None,
        max_agent_iterations: int = 50,
        model_names: dict[str, str] | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.run_root = Path(run_root)
        self.rate_limit_per_minute = rate_limit_per_minute
        self.max_match_seconds = max_match_seconds
        self.policy = policy or DefensePolicy()
        self.treasures = treasures
        self.max_agent_iterations = max_agent_iterations
        self.model_names = model_names or {}

        self.runner: MatchRunner | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.state = "setup"  # setup | running | finished
        self._stop_event = threading.Event()
        self._last_req: StartRequest | None = None

    def start(self, req: StartRequest) -> dict:
        fortify_seconds = 60 if req.test else req.fortify_seconds
        with self._lock:
            if self.state == "running":
                raise RuntimeError("a match is already running")
            # fresh commentary files for the new match
            arena_dir = self.run_root / "arena"
            arena_dir.mkdir(parents=True, exist_ok=True)
            for fh in arena_dir.glob("*.jsonl"):
                fh.unlink()

            self._stop_event = threading.Event()  # fresh stop signal for this match
            self.runner = MatchRunner(
                RunnerConfig(
                    root=self.run_root,
                    provider_factory=self.provider_factory,
                    fortify_seconds=fortify_seconds,
                    max_match_seconds=self.max_match_seconds,
                    rate_limit_per_minute=self.rate_limit_per_minute,
                    policy=self.policy,
                    treasures=self.treasures,
                    max_agent_iterations=self.max_agent_iterations,
                    external_stop=self._stop_event,
                )
            )
            self.state = "running"
            self._last_req = req
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return {"ok": True, "match_id": self.runner.match.match_id}

    def stop(self) -> dict:
        """Immediately signal both agents to stop; the match then aborts."""
        with self._lock:
            if self.state != "running":
                return {"ok": False, "error": "no match is running"}
            self._stop_event.set()
            return {"ok": True}

    def restart(self) -> dict:
        """Stop the current match (if running) and start a fresh one with the
        same parameters used last time (or a canned test battle)."""
        req = self._last_req or StartRequest(test=True)
        if self.state == "running":
            self._stop_event.set()
            deadline = time.time() + 10.0
            while self.state == "running" and time.time() < deadline:
                time.sleep(0.1)
        try:
            return self.start(req)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    def _run(self) -> None:
        try:
            self.runner.run()
        except Exception:
            import traceback

            traceback.print_exc()
        finally:
            with self._lock:
                self.state = "finished"

    def status(self) -> dict:
        with self._lock:
            if self.runner is None:
                return {
                    "control_state": self.state,
                    "match_status": "unknown",
                    "model_names": self.model_names,
                }
            status: dict = self.runner.judge.status()
            status["match"] = self.runner.match.summary()
            status["control_state"] = self.state
            status["model_names"] = self.model_names
            return status


_CONTROL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AgentArena — Battle</title>
<style>
  :root { --bg:#0b0e14; --panel:#11151d; --panel2:#0d1117; --line:#1f2733;
          --text:#d4dde8; --muted:#7d8a9c; --alpha:#4fb3ff; --bravo:#f0a04b;
          --ok:#3fb96b; --bad:#e5534b; --warn:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  /* subtle vignette (no scanlines) */
  .crt { position:fixed; inset:0; pointer-events:none; z-index:5;
         box-shadow:inset 0 0 160px rgba(0,0,0,.5); }
  .app { max-width:1240px; margin:0 auto; padding:16px; height:100vh;
         display:flex; flex-direction:column; gap:12px; }
  .hidden { display:none !important; }
  header { display:flex; align-items:center; gap:14px; padding:12px 16px;
           background:var(--panel); border:1px solid var(--line); border-radius:10px; }
  header .logo { font-size:18px; font-weight:700; }
  header .meta { color:var(--muted); font-size:13px; }
  .badge { padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700;
           border:1px solid var(--line); background:var(--panel2); color:var(--muted); }
  header .clock { margin-left:auto; font-variant-numeric:tabular-nums; color:var(--muted); }
  main { flex:1; display:grid; grid-template-columns:1fr 60px 1fr; gap:12px; min-height:0; }
  .pane { display:flex; flex-direction:column; background:var(--panel);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; min-height:0; }
  .pane.alpha{border-top:3px solid var(--alpha);} .pane.bravo{border-top:3px solid var(--bravo);}
  .pane .head { padding:10px 14px; border-bottom:1px solid var(--line);
                display:flex; align-items:baseline; gap:10px; background:var(--panel2); }
  .pane .name{font-weight:700;} .pane.alpha .name{color:var(--alpha);} .pane.bravo .name{color:var(--bravo);}
  .pane .role{color:var(--muted); font-size:12px;}
  .chat { flex:1; overflow-y:auto; padding:12px 14px; display:flex; flex-direction:column; gap:8px; }
  .msg { max-width:88%; padding:6px 11px; border-radius:6px; line-height:1.45; font-size:12.5px;
         background:#151b26; white-space:pre-wrap; word-wrap:break-word;
         animation:pop .15s ease-out; }
  @keyframes pop { from{opacity:0;transform:translateY(4px);} to{opacity:1;} }
  .alpha .msg{align-self:flex-start;border-left:2px solid var(--alpha);}
  .bravo .msg{align-self:flex-end;border-right:2px solid var(--bravo);}
  .msg .meta{display:block;font-size:10px;color:var(--muted);margin-bottom:3px;}
  .msg.battle{background:#182030;}
  .think { padding:2px 14px 10px; font-size:11px; color:var(--muted); text-shadow:none;
           display:none; align-items:center; gap:7px; }
  .think.show { display:flex; }
  .think .cursor { display:inline-block; width:8px; height:13px; background:var(--alpha); animation:blink 1s steps(1) infinite; }
  .pane.bravo .think .cursor { background:var(--bravo); }
  @keyframes blink { 50%{opacity:0} }
  .vs{display:flex;align-items:center;justify-content:center;}
  .vs .coin{width:50px;height:50px;border-radius:50%;background:var(--panel);border:1px solid var(--line);
            display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--muted);}
  .judge { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:12px 16px; display:grid; grid-template-columns:220px 1fr 1fr; gap:16px; align-items:center; }
  .judge .title { font-size:11px; letter-spacing:2px; color:var(--muted); text-transform:uppercase; }
  .judge .big { font-size:22px; font-weight:800; font-variant-numeric:tabular-nums; margin:2px 0; }
  .judge .rule { font-size:11px; color:var(--muted); }
  .judge .side { font-size:13px; } .judge .side .who { font-weight:700; }
  .judge .side.alpha .who{color:var(--alpha);} .judge .side.bravo .who{color:var(--bravo);}
  .judge .counts { color:var(--muted); font-size:12px; margin-left:6px; }
  .pips { display:inline-flex; gap:3px; margin-left:8px; vertical-align:middle; }
  .pip { width:10px; height:10px; border-radius:50%; background:#243041; border:1px solid var(--line); }
  .pip.err { background:var(--bad); border-color:var(--bad); }
  .pip.ok { background:var(--ok); border-color:var(--ok); }
  .judge .right { display:flex; flex-direction:column; gap:6px; align-items:flex-end; }
  .banner { font-weight:800; font-size:15px; padding:6px 14px; border-radius:8px; display:none; }
  .banner.show{display:block;} .banner.win{background:#14321f;color:var(--ok);border:1px solid #1f4a33;}
  .stat { color:var(--muted); font-size:13px; } .stat b { color:var(--text); }

  /* ---- setup overlay ---- */
  .overlay { position:fixed; inset:0; background:rgba(4,7,10,.78); backdrop-filter:blur(3px);
             display:flex; align-items:center; justify-content:center; z-index:50; }
  .card { width:420px; max-width:92vw; background:var(--panel); border:1px solid var(--line);
          border-radius:14px; padding:24px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  .card h1 { font-size:20px; margin:0 0 4px; }
  .card .sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
  .field { margin-bottom:14px; }
  .field label { display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }
  .field input { width:100%; padding:10px 12px; background:var(--panel2); border:1px solid var(--line);
                 border-radius:8px; color:var(--text); font-family:inherit; font-size:15px; }
  .field .hint { font-size:11px; color:var(--muted); margin-top:5px; }
  .startbtn { width:100%; padding:12px; border:none; border-radius:8px; cursor:pointer;
              background:linear-gradient(90deg,#2f7df6,#1f5fd0); color:#fff; font-weight:800;
              font-size:15px; font-family:inherit; }
  .startbtn:hover { filter:brightness(1.1); }
  .startbtn:disabled { opacity:.6; cursor:default; }
  .testbtn { width:100%; padding:12px; border-radius:8px; cursor:pointer; font-family:inherit;
             background:linear-gradient(90deg,#2ea36b,#1f7a4d); color:#fff; font-weight:800; font-size:15px;
             border:1px solid #1f4a33; }
  .testbtn:hover { filter:brightness(1.1); }
  .testbtn:disabled { opacity:.6; cursor:default; }
  .divider { display:flex; align-items:center; gap:10px; color:var(--muted); font-size:11px;
             margin:18px 0 14px; }
  .divider::before, .divider::after { content:''; flex:1; border-top:1px dashed var(--line); }
  .soon { margin-top:14px; font-size:11px; color:var(--muted); border-top:1px dashed var(--line); padding-top:12px; }
  .err { color:var(--bad); font-size:12px; margin-top:10px; min-height:14px; }
  .lastresult { margin-top:10px; font-size:13px; color:var(--ok); min-height:16px; }
  .syscheck { margin-top:6px; font-size:13px; color:var(--ok); min-height:16px; }
  .ctrlbtn { padding:6px 12px; border-radius:7px; cursor:pointer; font-family:inherit;
             font-size:12px; font-weight:700; border:1px solid var(--line); background:var(--panel2); color:var(--text); }
  .ctrlbtn:hover { filter:brightness(1.15); }
  .ctrlbtn.stop { background:#3a1d1d; border-color:#5a2a2a; color:#ff8a80; }
  .ctrlbtn.restart { background:#16283d; border-color:#2b3a4a; color:#7db3f6; }
</style>
</head>
<body>
<div class="crt"></div>
<div class="app hidden" id="arena">
  <header>
    <div class="logo">&#9876; AgentArena</div>
    <div class="meta">match <span id="match-id">—</span></div>
    <div class="badge" id="phase">—</div>
    <button class="ctrlbtn stop" onclick="stopBattle()" title="Stop both agents now">&#9632; Stop</button>
    <button class="ctrlbtn restart" onclick="restartBattle()" title="Stop and start a fresh battle">&#10227; Restart</button>
    <div class="clock" id="clock">T+ 00:00</div>
  </header>
  <main>
    <section class="pane alpha">
      <div class="head"><span class="name">ALPHA</span><span class="role" id="role-alpha">defending its treasure</span></div>
      <div class="chat" id="chat-alpha"></div>
      <div class="think" id="think-alpha"><span class="cursor"></span><span>thinking&hellip;</span></div>
    </section>
    <div class="vs"><div class="coin">VS</div></div>
    <section class="pane bravo">
      <div class="head"><span class="name">BRAVO</span><span class="role" id="role-bravo">defending its treasure</span></div>
      <div class="chat" id="chat-bravo"></div>
      <div class="think" id="think-bravo"><span class="cursor"></span><span>thinking&hellip;</span></div>
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

<!-- setup overlay -->
<div class="overlay" id="overlay">
  <div class="card">
    <h1>&#9876; AgentArena</h1>
    <div class="sub">LLM agents attack &amp; defend 128-char treasure codes.</div>

    <button class="testbtn" id="testbtn" onclick="startTest()">&#10003; Quick test battle</button>
    <div class="hint" style="text-align:center;margin-top:6px;">1-minute fortify + the two reference models.
      Use it to verify your setup works end-to-end before configuring your own models.</div>
    <div class="syscheck" id="syscheck"></div>

    <div class="divider"><span>or configure a battle</span></div>

    <div class="field">
      <label for="fortify">Stage 1 &mdash; Fortification duration (seconds)</label>
      <input id="fortify" type="number" min="0" max="3600" step="1" value="60" />
    </div>
    <button class="startbtn" id="startbtn" onclick="startBattle()">Start battle</button>
    <div class="err" id="err"></div>
    <div class="lastresult" id="lastresult"></div>
    <div class="soon">More battle parameters coming soon (rate limit, per-side models, defense policy, &hellip;).</div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let since = 0, pollTimers = [], testMode = false;
let lastMsg = { alpha: 0, bravo: 0 }, matchLive = false;
function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(sec){ sec=Math.max(0,Math.floor(sec)); const m=String(Math.floor(sec/60)).padStart(2,'0'); const s=String(sec%60).padStart(2,'0'); return 'T+ '+m+':'+s; }

// ---------- state machine ----------
async function pollState(){
  try {
    const s = await (await fetch('/api/state')).json();
    if (s.state === 'running') showArena(); else showSetup(s.state);
  } catch(e){}
  setTimeout(pollState, 1000);
}
function showSetup(state){
  $('overlay').classList.remove('hidden');
  if (state === 'finished') $('startbtn').textContent = 'Start new battle';
}
function showArena(){
  // a match is already running (e.g. opened the page mid-battle): show it
  $('overlay').classList.add('hidden');
  $('arena').classList.remove('hidden');
}

// ---------- start ----------
async function startBattle(){
  testMode = false;
  $('err').textContent = ''; $('startbtn').disabled = true;
  const fortify = parseInt($('fortify').value || '60', 10);
  try {
    const r = await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ fortify_seconds: fortify }) });
    const body = await r.json();
    if (!r.ok) { $('err').textContent = body.error || 'failed to start'; $('startbtn').disabled = false; return; }
    enterArena(body.match_id);
  } catch(e){ $('err').textContent = String(e); $('startbtn').disabled = false; }
}
async function stopBattle(){
  try { await fetch('/api/stop', { method:'POST' }); } catch(e){}
}
async function restartBattle(){
  try {
    const r = await fetch('/api/restart', { method:'POST' });
    const body = await r.json();
    if (body.ok) enterArena(body.match_id);
  } catch(e){}
}
async function startTest(){
  testMode = true;
  $('err').textContent = ''; $('syscheck').textContent = ''; $('testbtn').disabled = true;
  try {
    const r = await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ test: true, fortify_seconds: 60 }) });
    const body = await r.json();
    if (!r.ok) { $('err').textContent = body.error || 'failed to start'; $('testbtn').disabled = false; return; }
    enterArena(body.match_id);
  } catch(e){ $('err').textContent = String(e); $('testbtn').disabled = false; }
}
function enterArena(matchId){
  // reset UI for a fresh match
  since = 0; matchLive = true; lastMsg = { alpha: 0, bravo: 0 };
  $('chat-alpha').innerHTML = ''; $('chat-bravo').innerHTML = '';
  $('att-alpha').textContent = 0; $('att-bravo').textContent = 0;
  $('pips-alpha').innerHTML = ''; $('pips-bravo').innerHTML = '';
  $('banner').classList.remove('show'); $('feed-status').textContent = 'live';
  $('match-id').textContent = matchId || '—';
  $('overlay').classList.add('hidden');
  $('arena').classList.remove('hidden');
  $('startbtn').disabled = false; $('testbtn').disabled = false;
}

// ---------- commentary feed ----------
function addMsg(e){
  const chat = $('chat-' + e.side); if (!chat) return;
  lastMsg[e.side] = Date.now();
  $('think-' + e.side).classList.remove('show');
  const d = document.createElement('div');
  d.className = 'msg ' + (e.phase === 'battle' ? 'battle' : '');
  const t = new Date(e.ts * 1000).toLocaleTimeString();
  d.innerHTML = '<span class="meta">' + esc(t) + ' &middot; ' + esc(e.phase) + '</span>' + esc(e.text);
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
}
// show a "thinking…" indicator on a side that has been quiet for a while
setInterval(() => {
  const now = Date.now();
  const arenaVisible = !$('arena').classList.contains('hidden');
  for (const side of ['alpha', 'bravo']) {
    const el = $('think-' + side); if (!el) continue;
    const quiet = now - (lastMsg[side] || 0) > 4000;
    el.classList.toggle('show', arenaVisible && matchLive && quiet);
  }
}, 700);
async function pollFeed(){
  if (!$('arena').classList.contains('hidden')) {
    try { const r = await fetch('/feed?since=' + since); const items = await r.json();
      for (const e of items) { since = Math.max(since, e.ts); addMsg(e); } } catch(e){}
  }
  setTimeout(pollFeed, 800);
}

// ---------- judge status ----------
function renderPips(side, verdicts, limit){
  const c = $('pips-' + side); c.innerHTML = '';
  for (let i = 0; i < limit; i++){
    const p = document.createElement('span');
    let cls = 'pip';
    if (i < verdicts.length) cls += verdicts[i] === 'correct' ? ' ok' : ' err';
    p.className = cls; c.appendChild(p);
  }
  c.title = verdicts.length + '/' + limit + ' used this minute (red=miss, green=win)';
}
function setPhase(state){
  const map = { fortifying:'FORTIFY', battling:'BATTLE', finished:'FINISHED', provisioning:'SETUP' };
  $('phase').textContent = map[state] || String(state || '—').toUpperCase();
}
async function pollStatus(){
  if (!$('arena').classList.contains('hidden')) {
    try {
      const s = await (await fetch('/status')).json();
      if (s && s.match_status !== 'unknown') {
        if (s.model_names) {
          if (s.model_names.alpha) $('role-alpha').textContent = s.model_names.alpha;
          if (s.model_names.bravo) $('role-bravo').textContent = s.model_names.bravo;
        }
        const match = s.match || {};
        const elapsed = (typeof match.elapsed_seconds === 'number') ? match.elapsed_seconds : 0;
        $('j-clock').textContent = fmt(elapsed); $('clock').textContent = fmt(elapsed);
        setPhase(match.state);
        const lim = (s.limits && s.limits.per_window) || 10; $('rl-limit').textContent = lim;
        const att = s.attempts || {}; const win = s.window || {};
        $('att-alpha').textContent = att.alpha || 0; $('att-bravo').textContent = att.bravo || 0;
        renderPips('alpha', (win.alpha && win.alpha.verdicts) || [], lim);
        renderPips('bravo', (win.bravo && win.bravo.verdicts) || [], lim);
        if (s.match_status === 'finished') {
          matchLive = false;
          const b = $('banner');
          b.innerHTML = s.winner ? ('&#127942; ' + esc(String(s.winner).toUpperCase()) + ' WINS') : 'DRAW';
          b.classList.add('show'); $('feed-status').textContent = 'finished';
          $('lastresult').textContent = s.winner ? ('Last match: ' + s.winner + ' won.') : 'Last match: draw.';
          if (testMode) {
            $('syscheck').textContent = s.winner
              ? '\u2713 System check passed — both models battled and a winner was decided.'
              : 'System check ended in a draw — inspect the models/settings.';
          }
        } else { matchLive = true; $('feed-status').textContent = 'live'; }
      }
    } catch(e){ $('feed-status').textContent = 'reconnecting&hellip;'; }
  }
  setTimeout(pollStatus, 1000);
}
pollState(); pollFeed(); pollStatus();
</script>
</body>
</html>"""


def create_app(controller: MatchController) -> FastAPI:
    app = FastAPI(title="AgentArena Battle", version="0.1.0")
    app.state.controller = controller
    arena_dir = controller.run_root / "arena"
    store = FeedStore([arena_dir / f"{s.value}.jsonl" for s in (Side.ALPHA, Side.BRAVO)])
    app.state.feed = store

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _CONTROL_HTML

    @app.get("/api/state")
    def state(request: Request) -> dict:
        ctrl: MatchController = request.app.state.controller
        return {"state": ctrl.state}

    @app.post("/api/start")
    def start(req: StartRequest, request: Request) -> JSONResponse:
        ctrl: MatchController = request.app.state.controller
        try:
            result = ctrl.start(req)
        except RuntimeError as exc:
            return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})
        return JSONResponse(content=result)

    @app.post("/api/stop")
    def stop(request: Request) -> JSONResponse:
        ctrl: MatchController = request.app.state.controller
        return JSONResponse(content=ctrl.stop())

    @app.post("/api/restart")
    def restart(request: Request) -> JSONResponse:
        ctrl: MatchController = request.app.state.controller
        return JSONResponse(content=ctrl.restart())

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
        ctrl: MatchController = request.app.state.controller
        return ctrl.status()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
