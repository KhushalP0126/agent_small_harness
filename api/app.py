from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.generation_controller import GenerationController
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaClient, OllamaModelSupplier


class SyncRunRequest(BaseModel):
    target: str = Field(..., min_length=1)
    spec: str = Field(..., min_length=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    language: str | None = None
    model: str | None = Field(default=None, min_length=1)
    ollama_url: str | None = Field(default=None, min_length=1)
    use_architect: bool = False


class SyncRunResponse(BaseModel):
    agent: str
    payload: dict[str, Any]


ControllerFactory = Callable[[SyncRunRequest], GenerationController]


def build_controller(request: SyncRunRequest) -> GenerationController:
    """Build the live backend wiring for one synchronous request."""
    ollama_client = OllamaClient(base_url=request.ollama_url or DEFAULT_OLLAMA_URL)
    worker = OllamaModelSupplier(
        client=ollama_client,
        model=request.model or DEFAULT_OLLAMA_MODEL,
    )
    architect_supplier = ArchitectModelSupplier().repair_draft if request.use_architect else None
    return GenerationController(
        max_retries=request.max_retries,
        draft_supplier=worker.generate_draft,
        repair_supplier=worker.repair_draft,
        architect_supplier=architect_supplier,
        architect_after_repair_attempts=1 if architect_supplier is not None else None,
        repair_strategy=RepairStrategyAgent(),
        language=request.language,
    )


def create_app(controller_factory: ControllerFactory = build_controller) -> FastAPI:
    app = FastAPI(title="Agent Small Harness API", version="0.1.0")


    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


    @app.post("/runs/sync", response_model=SyncRunResponse)
    def run_sync(request: SyncRunRequest) -> SyncRunResponse:
        try:
            controller = controller_factory(request)
            result = controller.run(target=request.target, initial_prompt=request.spec)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "backend_error",
                    "message": str(exc),
                    "action": "Check Ollama availability and architect configuration, then retry.",
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "generation_failed",
                    "message": f"{exc.__class__.__name__}: {exc}",
                },
            ) from exc
        return SyncRunResponse(agent=result.agent, payload=result.payload)

    return app


app = create_app()
