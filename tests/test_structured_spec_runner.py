import unittest

from agents.plan_mode import PlanModeAgent
from backends.architect_client import (
    CONTRACT_PROFILE,
    REPAIR_PROFILE,
    ArchitectConfig,
    ArchitectModelSupplier,
    ContractArchitectError,
    ContractArchitectSupplier,
)
from scripts.run_structured_spec import _fallback_contract_queue, _validate_structured_spec_output


class StructuredSpecRunnerTests(unittest.TestCase):
    def test_plan_mode_extracts_markdown_structured_spec(self) -> None:
        prompt = """
## Game Spec

- name: snake
- language: python
- library: pygame
- kernel_mode: generate_from_spec

## Required Components

- `GameConfig`
- `main()`

## Entrypoint

- `main()`

## Dependency Graph

- state -> render
"""

        plan = PlanModeAgent().plan(prompt)

        self.assertEqual(plan.app_name, "snake")
        self.assertIn("pygame", plan.allowed_libraries)
        self.assertIn("`GameConfig`", plan.components)
        self.assertIn("`main()`", plan.components)
        self.assertIn("`main()`", plan.entrypoints)
        self.assertIn("state -> render", plan.dependency_graph_context)

    def test_structured_spec_gate_rejects_missing_components(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Required Components

- `GameConfig`
- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "def main():\n    pass\n"

        issues = _validate_structured_spec_output(source, plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["kind"], "missing_component")
        self.assertIn("GameConfig", issues[0]["summary"])

    def test_structured_spec_gate_accepts_required_components(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Required Components

- `GameConfig`
- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "class GameConfig:\n    pass\n\ndef main():\n    pass\n"

        self.assertEqual(_validate_structured_spec_output(source, plan), [])

    def test_structured_snake_spec_gets_fallback_contract_queue(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Game Spec

- name: snake
- language: python

## Required Components

- `main()`
"""
        )

        queue, fallback_used = _fallback_contract_queue(plan)

        self.assertTrue(fallback_used)
        names = [contract.name for contract in queue.contracts]
        self.assertIn("opposite_direction", names)
        self.assertIn("next_head", names)
        self.assertIn("main", names)

    def test_placeholder_main_fails_structured_spec_gate(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Game Spec

- name: snake

## Required Components

- `GameConfig`
- `main()`
"""
        )

        issues = _validate_structured_spec_output('def main():\n    print("snake")\n', plan)

        self.assertEqual([issue["kind"] for issue in issues], ["missing_component"])
        self.assertIn("GameConfig", issues[0]["summary"])

    def test_contract_architect_uses_light_profile(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs):
                self.calls.append(kwargs)
                return '{"contracts":[{"name":"f","signature":"def f() -> int"}]}'

        client = StubClient()
        supplier = ContractArchitectSupplier(client=client)

        queue = supplier.build_contract_queue("PLAN PACKET:", "")

        self.assertEqual(len(queue.contracts), 1)
        self.assertEqual(client.calls[0]["profile"], CONTRACT_PROFILE)

    def test_repair_architect_uses_separate_profile(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs):
                self.calls.append(kwargs)
                return "def fixed():\n    return 1\n"

        client = StubClient()
        supplier = ArchitectModelSupplier(config=ArchitectConfig(repair_profile=REPAIR_PROFILE), client=client)

        supplier.repair_draft("def bad():\n    pass\n", "fix")

        self.assertEqual(client.calls[0]["profile"], REPAIR_PROFILE)

    def test_empty_contract_response_becomes_typed_failure(self) -> None:
        class StubClient:
            def generate(self, **kwargs):
                raise RuntimeError("Architect API returned an empty response.")

        supplier = ContractArchitectSupplier(client=StubClient())

        with self.assertRaises(ContractArchitectError) as raised:
            supplier.build_contract_queue("PLAN PACKET:", "")

        self.assertEqual(raised.exception.code, "architect_contract_empty_response")

    def test_truncated_contract_json_becomes_typed_failure(self) -> None:
        class StubClient:
            def generate(self, **kwargs):
                return '{"contracts":[{"name":"f","signature":"def f() -> int"}'

        supplier = ContractArchitectSupplier(client=StubClient())

        with self.assertRaises(ContractArchitectError) as raised:
            supplier.build_contract_queue("PLAN PACKET:", "")

        self.assertEqual(raised.exception.code, "architect_contract_truncated_json")


if __name__ == "__main__":
    unittest.main()
