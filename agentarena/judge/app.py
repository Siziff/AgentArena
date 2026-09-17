"""FastAPI wrapper around JudgeCore.

Run standalone:
    uvicorn agentarena.judge.app:app --host 127.0.0.1 --port 8000

Or embed in the orchestrator via `create_app(core)`. The HTTP layer only
translates SubmitOutcome statuses to status codes; all logic lives in
JudgeCore (see judge/core.py).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core.types import Side, Verdict
from .core import JudgeCore, OutcomeStatus, SubmitOutcome


class SubmitRequest(BaseModel):
    side: str = Field(..., description='"alpha" or "bravo"')
    guess: str = Field(..., description="candidate 128-char code")


class SubmitResponse(BaseModel):
    verdict: str | None
    match_status: str
    winner: str | None
    remaining_in_window: int
    attempts_total: int
    detail: str


_STATUS_TO_HTTP = {
    OutcomeStatus.EVALUATED: 200,
    OutcomeStatus.INVALID: 400,
    OutcomeStatus.RATE_LIMITED: 429,
    OutcomeStatus.MATCH_FINISHED: 409,
}

_STATUS_TO_ERROR = {
    OutcomeStatus.INVALID: "invalid_request",
    OutcomeStatus.RATE_LIMITED: "rate_limited",
    OutcomeStatus.MATCH_FINISHED: "match_finished",
}


def _to_response(outcome: SubmitOutcome) -> JSONResponse:
    http_status = _STATUS_TO_HTTP[outcome.status]
    if outcome.status is OutcomeStatus.EVALUATED:
        body = SubmitResponse(
            verdict=outcome.verdict.value if outcome.verdict else None,
            match_status=outcome.match_status,
            winner=outcome.winner.value if outcome.winner else None,
            remaining_in_window=outcome.remaining_in_window,
            attempts_total=outcome.attempts_total,
            detail=outcome.detail,
        ).model_dump()
        return JSONResponse(status_code=http_status, content=body)
    # Error envelope for non-evaluated outcomes.
    return JSONResponse(
        status_code=http_status,
        content={
            "error": _STATUS_TO_ERROR[outcome.status],
            "message": outcome.detail,
            "retry_after_seconds": outcome.retry_after_seconds,
        },
    )


def create_app(core: JudgeCore) -> FastAPI:
    app = FastAPI(title="AgentArena Judge", version="0.1.0")
    app.state.judge = core

    @app.post("/submit")
    def submit(req: SubmitRequest, request: Request) -> JSONResponse:
        judge: JudgeCore = request.app.state.judge
        return _to_response(judge.submit(req.side, req.guess))

    @app.get("/status")
    def status(request: Request) -> dict:
        judge: JudgeCore = request.app.state.judge
        return judge.status()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


def create_default_app() -> FastAPI:
    """A bare app with an empty JudgeCore (secrets registered out-of-band).

    Useful for `uvicorn agentarena.judge.app:app`. Configure via environment
    variables if needed; the orchestrator normally builds its own core.
    """
    import os

    core = JudgeCore(
        rate_limit_per_minute=int(os.getenv("AA_RATE_LIMIT", "10")),
        window_seconds=float(os.getenv("AA_RATE_WINDOW_SECONDS", "60")),
        match_id=os.getenv("AA_MATCH_ID", "match"),
    )
    return create_app(core)


app = create_default_app()

__all__ = [
    "app",
    "create_app",
    "create_default_app",
    "SubmitRequest",
    "SubmitResponse",
    "Side",
    "Verdict",
]
