import unittest

from fastapi.testclient import TestClient

from agents.generation_controller import GenerationController
from api.app import app, create_app


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


if __name__ == "__main__":
    unittest.main()
