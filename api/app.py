from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
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
from harness_kernel.orchestration_store import OrchestrationStore, graph_from_dict
from harness_kernel.orchestration_runtime import PersistedOrchestrationRuntime, TypedNodeExecutor
from harness_kernel.merge_queue import TrustedValidator


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
    repo_root: str | None = Field(default=None, min_length=1)
    execution_trace: bool = True
    debugger_hints: bool = True
    debugger_type_contracts: list[str] = Field(default_factory=list, max_length=32)


class SyncRunResponse(BaseModel):
    agent: str
    payload: dict[str, Any]


ControllerFactory = Callable[[SyncRunRequest], GenerationController]
DEFAULT_JOB_STORE_PATH = Path(__file__).resolve().parents[1] / "data/jobs.jsonl"


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
    repository_root = _validated_repo_root(request.repo_root)
    return GenerationController(
        max_retries=request.max_retries,
        draft_supplier=worker.generate_draft,
        repair_supplier=worker.repair_draft,
        architect_supplier=architect_supplier,
        architect_after_repair_attempts=1 if architect_supplier is not None else None,
        repair_strategy=RepairStrategyAgent(),
        language=request.language,
        repository_root=repository_root,
        enable_execution_trace=request.execution_trace,
        enable_debugger_hints=request.debugger_hints,
        debugger_type_contracts=request.debugger_type_contracts,
    )


def _validated_repo_root(raw: str | None) -> Path | None:
    """Keep public API repository access inside the launched workspace."""

    if not raw:
        return None
    workspace = Path.cwd().resolve()
    candidate = Path(raw).expanduser().resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError("repo_root must be the API workspace or one of its descendants")
    return candidate


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
    trusted_merge_validator: TrustedValidator | None = None,
) -> FastAPI:
    app = FastAPI(title="Agent Small Harness API", version="0.1.0")
    active_job_store = job_store or JsonlJobStore(
        os.environ.get("JOB_STORE_PATH", str(DEFAULT_JOB_STORE_PATH))
    )
    orchestration_store = OrchestrationStore(
        Path(os.environ.get("ORCHESTRATION_STORE_PATH", str(Path.cwd() / ".harness/orchestrations")))
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

    @app.post("/orchestrations/plan", status_code=201)
    def plan_orchestration(request: dict[str, Any]) -> dict[str, Any]:
        try:
            graph = graph_from_dict(request.get("graph", {}))
            if not graph.nodes:
                raise ValueError("a candidate graph needs at least one node")
            return orchestration_store.create(graph, goal=str(request.get("goal", "")))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_graph", "message": str(exc)}) from exc

    @app.get("/orchestrations/{session_id}")
    def inspect_orchestration(session_id: str) -> dict[str, Any]:
        try:
            return orchestration_store.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_not_found"}) from exc

    @app.post("/orchestrations/{session_id}/approve")
    def approve_orchestration(session_id: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return orchestration_store.approve(session_id, str(request.get("revision_hash", "")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": "approval_hash_mismatch", "message": str(exc)}) from exc

    def control_orchestration(session_id: str, status: str) -> dict[str, Any]:
        try:
            return orchestration_store.transition(session_id, status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_not_found"}) from exc
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail={"error": "invalid_transition", "message": str(exc)}) from exc

    @app.post("/orchestrations/{session_id}/start")
    def start_orchestration(session_id: str) -> dict[str, Any]:
        return control_orchestration(session_id, "running")

    @app.post("/orchestrations/{session_id}/pause")
    def pause_orchestration(session_id: str) -> dict[str, Any]:
        return control_orchestration(session_id, "paused")

    @app.post("/orchestrations/{session_id}/resume")
    def resume_orchestration(session_id: str) -> dict[str, Any]:
        return control_orchestration(session_id, "running")

    @app.post("/orchestrations/{session_id}/cancel")
    def cancel_orchestration(session_id: str) -> dict[str, Any]:
        return control_orchestration(session_id, "cancelled")

    @app.post("/orchestrations/{session_id}/revise")
    def revise_orchestration(session_id: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            graph = graph_from_dict(request.get("graph", {}))
            return orchestration_store.revise(session_id, graph, str(request.get("reason", "graph revision")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_not_found"}) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_graph", "message": str(exc)}) from exc

    @app.post("/orchestrations/{session_id}/nodes/{node_id}/retry")
    def retry_orchestration_node(session_id: str, node_id: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            provider = request.get("provider")
            return orchestration_store.retry(session_id, node_id, str(provider) if provider else None)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_or_node_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": "invalid_retry", "message": str(exc)}) from exc

    @app.get("/orchestrations/{session_id}/replay")
    def replay_orchestration(session_id: str) -> dict[str, Any]:
        try:
            orchestration_store.get(session_id)
            events = [asdict(event) for event in orchestration_store.journal(session_id).replay()]
            return {"session_id": session_id, "mode": "replay", "external_actions": False, "events": events}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_not_found"}) from exc

    @app.post("/orchestrations/{session_id}/merges/{node_id}/review")
    def review_orchestration_merge(session_id: str, node_id: str,
                                   request: dict[str, Any]) -> dict[str, Any]:
        if trusted_merge_validator is None:
            raise HTTPException(status_code=503, detail={"error": "trusted_validator_unavailable"})
        try:
            runtime = PersistedOrchestrationRuntime(
                orchestration_store, session_id, Path.cwd(), TypedNodeExecutor({})
            )
            checkpoint_id = runtime.review_merge(
                node_id, approved=bool(request.get("approved")), validate=trusted_merge_validator
            )
            return {"node_id": node_id, "approved": bool(request.get("approved")),
                    "checkpoint_id": checkpoint_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "session_or_node_not_found"}) from exc
        except (ValueError, RuntimeError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail={"error": "merge_rejected", "message": str(exc)}) from exc

    return app


app = create_app()
