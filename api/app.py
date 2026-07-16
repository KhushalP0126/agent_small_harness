from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agents.generation_controller import GenerationController


class SyncRunRequest(BaseModel):
    target: str = Field(..., min_length=1)
    spec: str = Field(..., min_length=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    language: str | None = None


class SyncRunResponse(BaseModel):
    agent: str
    payload: dict[str, Any]


app = FastAPI(title="Agent Small Harness API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs/sync", response_model=SyncRunResponse)
def run_sync(request: SyncRunRequest) -> SyncRunResponse:
    controller = GenerationController(
        max_retries=request.max_retries,
        language=request.language,
    )
    result = controller.run(target=request.target, initial_prompt=request.spec)
    return SyncRunResponse(agent=result.agent, payload=result.payload)
