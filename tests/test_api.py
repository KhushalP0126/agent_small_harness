import unittest

from fastapi.testclient import TestClient

from api.app import app


class ApiTests(unittest.TestCase):
    def test_health_endpoint_reports_ok(self) -> None:
        client = TestClient(app)
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_sync_run_endpoint_calls_generation_controller(self) -> None:
        client = TestClient(app)
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


if __name__ == "__main__":
    unittest.main()
