import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agents.generation_controller import GenerationController
from agents.job_store import JsonlJobStore
from api.app import SyncRunRequest, app, build_controller, create_app


class ApiTests(unittest.TestCase):
    def test_health_endpoint_reports_ok(self) -> None:
        client = TestClient(app)
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_sync_run_endpoint_calls_generation_controller(self) -> None:
        def controller_factory(request):
            return GenerationController(
                max_retries=request.max_retries,
                language=request.language,
                draft_supplier=lambda _prompt: "def ok():\n    return 1\n",
            )

        client = TestClient(create_app(controller_factory))
        response = client.post(
            "/runs/sync",
            json={
                "target": "unit-function",
                "spec": "def ok():\n    return 1\n",
                "max_retries": 0,
                "language": "python",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agent"], "agent-generation-controller")
        self.assertEqual(payload["payload"]["target"], "unit-function")
        self.assertEqual(payload["payload"]["final_status"], "completed")

    def test_sync_run_exposes_backend_configuration_to_factory(self) -> None:
        received = {}

        def controller_factory(request):
            received.update(request.model_dump())
            return GenerationController(
                max_retries=0,
                draft_supplier=lambda _prompt: "def ok():\n    return 1\n",
            )

        client = TestClient(create_app(controller_factory))
        response = client.post(
            "/runs/sync",
            json={
                "target": "configured-function",
                "spec": "write ok",
                "model": "qwen2.5-coder:3b",
                "ollama_url": "http://ollama:11434",
                "use_architect": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(received["model"], "qwen2.5-coder:3b")
        self.assertEqual(received["ollama_url"], "http://ollama:11434")
        self.assertTrue(received["use_architect"])

    def test_build_controller_wires_real_backend_suppliers(self) -> None:
        request = SyncRunRequest(
            target="configured-function",
            spec="write code",
            model="qwen2.5-coder:3b",
            ollama_url="http://ollama:11434",
            use_architect=True,
            architect_model="deepseek-chat",
            architect_url="https://architect.example/v1/chat/completions",
        )
        with patch("api.app.OllamaModelSupplier") as worker_cls, patch(
            "api.app.ArchitectModelSupplier"
        ) as architect_cls:
            controller = build_controller(request)

        worker_cls.assert_called_once()
        worker_kwargs = worker_cls.call_args.kwargs
        self.assertEqual(worker_kwargs["model"], "qwen2.5-coder:3b")
        self.assertEqual(worker_kwargs["client"].base_url, "http://ollama:11434")
        architect_config = architect_cls.call_args.kwargs["config"]
        self.assertEqual(architect_config.model, "deepseek-chat")
        self.assertEqual(architect_config.base_url, "https://architect.example/v1/chat/completions")
        self.assertIsNotNone(controller.architect_supplier)

    def test_sync_run_returns_structured_backend_error(self) -> None:
        def controller_factory(_request):
            raise RuntimeError("Ollama is not reachable")

        client = TestClient(create_app(controller_factory))
        response = client.post(
            "/runs/sync",
            json={"target": "unit-function", "spec": "write code"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error"], "backend_error")

    def test_async_run_persists_lifecycle_and_result(self) -> None:
        def controller_factory(request):
            return GenerationController(
                max_retries=request.max_retries,
                draft_supplier=lambda _prompt: "def ok():\n    return 1\n",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(
                create_app(
                    controller_factory,
                    job_store=JsonlJobStore(Path(temp_dir) / "jobs.jsonl"),
                )
            )
            queued = client.post(
                "/runs/async",
                json={"target": "async-function", "spec": "write ok"},
            )

            self.assertEqual(queued.status_code, 202)
            job_id = queued.json()["job_id"]
            result = client.get(f"/runs/{job_id}")

            self.assertEqual(result.status_code, 200)
            payload = result.json()
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["events"][-1]["event_type"], "status")
            self.assertEqual(payload["events"][-2]["event_type"], "result")

    def test_async_run_returns_not_found_for_unknown_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(
                create_app(job_store=JsonlJobStore(Path(temp_dir) / "jobs.jsonl"))
            )
            response = client.get("/runs/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["error"], "job_not_found")

    def test_async_run_records_failed_job_and_error_event(self) -> None:
        def controller_factory(_request):
            raise RuntimeError("Ollama is not reachable")

        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(
                create_app(
                    controller_factory,
                    job_store=JsonlJobStore(Path(temp_dir) / "jobs.jsonl"),
                )
            )
            queued = client.post(
                "/runs/async",
                json={"target": "failed-function", "spec": "write code"},
            )
            job_id = queued.json()["job_id"]
            result = client.get(f"/runs/{job_id}")

        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["status"], "failed")
        error_events = [event for event in payload["events"] if event["event_type"] == "error"]
        self.assertEqual(error_events[0]["payload"]["error"], "backend_error")


if __name__ == "__main__":
    unittest.main()
