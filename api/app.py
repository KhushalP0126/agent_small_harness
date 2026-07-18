from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.generation_controller import GenerationController
from agents.job_store import JobRecord, JsonlJobStore
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import ArchitectConfig, ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaClient, OllamaModelSupplier


class SyncRunRequest(BaseModel):
    target: str = Field(..., min_length=1)
    spec: str = Field(..., min_length=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    language: str | None = None
    model: str | None = Field(default=None, min_length=1)
    ollama_url: str | None = Field(default=None, min_length=1)
    use_architect: bool = False
    architect_model: str | None = Field(default=None, min_length=1)
    architect_url: str | None = Field(default=None, min_length=1)


class SyncRunResponse(BaseModel):
    agent: str
    payload: dict[str, Any]


ControllerFactory = Callable[[SyncRunRequest], GenerationController]
DEFAULT_JOB_STORE_PATH = Path("data/jobs.jsonl")


def build_controller(request: SyncRunRequest) -> GenerationController:
    """Build the live backend wiring for one synchronous request."""
    ollama_client = OllamaClient(base_url=request.ollama_url or DEFAULT_OLLAMA_URL)
    worker = OllamaModelSupplier(
        client=ollama_client,
        model=request.model or DEFAULT_OLLAMA_MODEL,
    )
    architect_config = ArchitectConfig(
        model_override=request.architect_model,
        base_url_override=request.architect_url,
    )
    architect_supplier = (
        ArchitectModelSupplier(config=architect_config).repair_draft
        if request.use_architect
        else None
    )
    return GenerationController(
        max_retries=request.max_retries,
        draft_supplier=worker.generate_draft,
        repair_supplier=worker.repair_draft,
        architect_supplier=architect_supplier,
        architect_after_repair_attempts=1 if architect_supplier is not None else None,
        repair_strategy=RepairStrategyAgent(),
        language=request.language,
    )


def _run_background_job(
    job_id: str,
    request: SyncRunRequest,
    controller_factory: ControllerFactory,
    job_store: JsonlJobStore,
) -> None:
    job_store.update_status(job_id, "running")
    try:
        controller = controller_factory(request)
        result = controller.run(target=request.target, initial_prompt=request.spec)
        job_store.append_event(
            job_id,
            "result",
            {"agent": result.agent, "payload": result.payload},
        )
        job_store.update_status(job_id, "completed")
    except Exception as exc:
        job_store.append_event(
            job_id,
            "error",
            {
                "error": "backend_error" if isinstance(exc, RuntimeError) else "generation_failed",
                "message": str(exc),
            },
        )
        job_store.update_status(job_id, "failed")
def create_app(
    controller_factory: ControllerFactory = build_controller,
    job_store: JsonlJobStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Agent Small Harness API", version="0.1.0")
    active_job_store = job_store or JsonlJobStore(
        os.environ.get("JOB_STORE_PATH", str(DEFAULT_JOB_STORE_PATH))
    )


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

    @app.post("/runs/async", status_code=202)
    def run_async(request: SyncRunRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
        job = active_job_store.create_job(request.target)
        background_tasks.add_task(
            _run_background_job,
            job.job_id,
            request,
            controller_factory,
            active_job_store,
        )
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/runs/{job_id}")
    def get_run(job_id: str) -> dict[str, Any]:
        job: JobRecord | None = active_job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "job_not_found", "job_id": job_id})
        return {
            "job_id": job.job_id,
            "status": job.status,
            "target": job.target,
            "created_at": job.created_at,
            "events": job.events,
        }

    return app


app = create_app()
