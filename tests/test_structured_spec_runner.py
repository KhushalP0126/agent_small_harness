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
from scripts.run_structured_spec import _run_contract_queue_sequentially, _single_contract_prompt
from kernel.function_contracts import ContractQueue, DealExample, FunctionContract


class StructuredSpecRunnerTests(unittest.TestCase):
    def test_plan_mode_extracts_markdown_structured_spec(self) -> None:
        prompt = """
## App Spec

- name: workflow_app
- language: python
- library: visualkit
- kernel_mode: generate_from_spec

## Required Components

- `AppConfig`
- `main()`

## Entrypoint

- `main()`

## Dependency Graph

- state -> render
"""

        plan = PlanModeAgent().plan(prompt)

        self.assertEqual(plan.app_name, "workflow_app")
        self.assertIn("visualkit", plan.allowed_libraries)
        self.assertIn("`AppConfig`", plan.components)
        self.assertIn("`main()`", plan.components)
        self.assertIn("`main()`", plan.entrypoints)
        self.assertIn("state -> render", plan.dependency_graph_context)

    def test_structured_spec_gate_rejects_missing_components(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Required Components

- `AppConfig`
- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "def main():\n    pass\n"

        issues = _validate_structured_spec_output(source, plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["kind"], "missing_component")
        self.assertIn("AppConfig", issues[0]["summary"])

    def test_structured_spec_gate_accepts_required_components(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Required Components

- `AppConfig`
- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "class AppConfig:\n    pass\n\ndef main():\n    pass\n"

        self.assertEqual(_validate_structured_spec_output(source, plan), [])

    def test_structured_spec_gets_generic_fallback_contract_queue(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- name: workflow_app
- language: python

## Required Components

- `AppConfig`
- `DataState`
- `main()`
- `update_state()`
- `render()`

## Entrypoint

- `main()`

## State Rules

- `normalize_value(value: int) -> int` converts input values before state updates.

## Behavior Examples

- normalize_value(-1) == 0

## Dependency Graph

- AppConfig -> update_state -> main
- DataState -> update_state -> render -> main
- normalize_value -> update_state
"""
        )

        queue, fallback_used = _fallback_contract_queue(plan)

        self.assertTrue(fallback_used)
        names = [contract.name for contract in queue.contracts]
        self.assertLess(names.index("AppConfig"), names.index("update_state"))
        self.assertLess(names.index("DataState"), names.index("update_state"))
        self.assertLess(names.index("normalize_value"), names.index("update_state"))
        self.assertIn("update_state", names)
        self.assertIn("render", names)
        self.assertIn("main", names)
        normalize_value = next(contract for contract in queue.contracts if contract.name == "normalize_value")
        self.assertEqual(normalize_value.signature, "def normalize_value(value: int) -> int")
        self.assertEqual(normalize_value.examples[0].call, "normalize_value(-1)")
        main = next(contract for contract in queue.contracts if contract.name == "main")
        self.assertIn("update_state", main.dependencies)
        self.assertIn("render", main.dependencies)

    def test_contract_queue_uses_dependency_stack_before_generation(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Dependency Graph

- helper_value -> final_value
- unrelated_ui -> render
"""
        )
        queue = ContractQueue(
            [
                FunctionContract(
                    name="final_value",
                    signature="def final_value() -> int",
                    purpose="Return helper value plus one.",
                    examples=[DealExample("final_value()", "2")],
                    dependencies=["helper_value"],
                ),
                FunctionContract(
                    name="helper_value",
                    signature="def helper_value() -> int",
                    purpose="Return the base value.",
                    examples=[DealExample("helper_value()", "1")],
                ),
            ]
        )
        calls = []

        def generate(prompt: str) -> str:
            if "NAME: helper_value" in prompt:
                calls.append("helper_value")
                return "def helper_value() -> int:\n    return 1\n"
            if "NAME: final_value" in prompt:
                calls.append("final_value")
                return "def final_value() -> int:\n    return helper_value() + 1\n"
            raise AssertionError("unexpected prompt")

        accepted_sources, results = _run_contract_queue_sequentially(queue, plan, generate)

        self.assertEqual(calls, ["helper_value", "final_value"])
        self.assertEqual([result.status for result in results], ["accepted", "accepted"])
        self.assertEqual(len(accepted_sources), 2)

    def test_contract_queue_infers_class_dependencies_from_examples(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Dependency Graph

- DataState -> apply_delta
"""
        )
        queue = ContractQueue(
            [
                FunctionContract(
                    name="apply_delta",
                    signature="def apply_delta(state: DataState) -> DataState",
                    examples=[DealExample("apply_delta(DataState(1)).value", "2")],
                ),
                FunctionContract(
                    name="DataState",
                    kind="class",
                    signature="class DataState:",
                    purpose="Hold a value.",
                ),
            ]
        )
        calls = []

        def generate(prompt: str) -> str:
            if "NAME: DataState" in prompt:
                calls.append("DataState")
                return "class DataState:\n    def __init__(self, value):\n        self.value = value\n"
            if "NAME: apply_delta" in prompt:
                calls.append("apply_delta")
                return "def apply_delta(state: DataState) -> DataState:\n    return DataState(state.value + 1)\n"
            raise AssertionError("unexpected prompt")

        accepted_sources, results = _run_contract_queue_sequentially(queue, plan, generate)

        self.assertEqual(calls, ["DataState", "apply_delta"])
        self.assertEqual([result.status for result in results], ["accepted", "accepted"])
        self.assertEqual(len(accepted_sources), 2)

    def test_contract_queue_retries_failed_contract_without_discarding_queue(self) -> None:
        plan = PlanModeAgent().plan("")
        queue = ContractQueue(
            [
                FunctionContract(name="first", signature="def first() -> int"),
                FunctionContract(
                    name="second",
                    signature="def second() -> int",
                    examples=[DealExample("second()", "2")],
                    dependencies=["first"],
                ),
            ]
        )
        calls = []

        def generate(prompt: str) -> str:
            if "NAME: first" in prompt:
                calls.append("generate:first")
                return "def first() -> int:\n    return 1\n"
            if "NAME: second" in prompt:
                calls.append("generate:second")
                return "def second() -> int:\n    return\n        2\n"
            raise AssertionError("unexpected prompt")

        def repair(draft: str, prompt: str) -> str:
            calls.append("repair:second")
            self.assertIn("FUNCTION CONTRACT REPAIR", prompt)
            self.assertIn("contract_parse_error", prompt)
            self.assertIn("def first", prompt)
            return "def second() -> int:\n    return first() + 1\n"

        accepted_sources, results = _run_contract_queue_sequentially(
            queue,
            plan,
            generate,
            repair_draft=repair,
            small_retries_per_contract=1,
        )

        self.assertEqual(calls, ["generate:first", "generate:second", "repair:second"])
        self.assertEqual([result.status for result in results], ["accepted", "accepted"])
        self.assertEqual(results[1].repair_attempts[0]["worker"], "small_worker")
        self.assertEqual(results[1].repair_attempts[0]["status"], "accepted")
        self.assertEqual(len(accepted_sources), 2)

    def test_contract_queue_escalates_single_failed_contract_to_architect(self) -> None:
        plan = PlanModeAgent().plan("")
        queue = ContractQueue(
            [
                FunctionContract(
                    name="stubborn",
                    signature="def stubborn() -> int",
                    examples=[DealExample("stubborn()", "3")],
                ),
            ]
        )
        calls = []

        def generate(prompt: str) -> str:
            calls.append("generate")
            return "def stubborn() -> int:\n    return 0\n"

        def repair(draft: str, prompt: str) -> str:
            calls.append("repair")
            return "def stubborn() -> int:\n    return 1\n"

        def architect(draft: str, prompt: str) -> str:
            calls.append("architect")
            self.assertIn("architect_llm", prompt)
            self.assertIn("contract_example_failed", prompt)
            return "def stubborn() -> int:\n    return 3\n"

        accepted_sources, results = _run_contract_queue_sequentially(
            queue,
            plan,
            generate,
            repair_draft=repair,
            architect_repair_draft=architect,
            small_retries_per_contract=1,
            architect_retries_per_contract=1,
        )

        self.assertEqual(calls, ["generate", "repair", "architect"])
        self.assertEqual(results[0].status, "accepted")
        self.assertEqual([attempt["worker"] for attempt in results[0].repair_attempts], ["small_worker", "architect_llm"])
        self.assertEqual(results[0].repair_attempts[-1]["status"], "accepted")
        self.assertEqual(len(accepted_sources), 1)

    def test_small_worker_contract_prompt_uses_local_graph_slice(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- name: workflow_app
- language: python
- library: visualkit

## Dependency Graph

- DataState -> apply_delta -> render
- unrelated_menu -> splash_screen

## Update Rules

- apply_delta updates the accumulated value.
- render draws the current frame.
"""
        )
        contract = FunctionContract(
            name="apply_delta",
            signature="def apply_delta(state: DataState) -> DataState",
            dependencies=["DataState"],
        )

        prompt = _single_contract_prompt(
            plan,
            contract,
            ["class DataState:\n    pass\n"],
            ["DataState"],
        )

        self.assertIn("LOCAL GRAPH CONTEXT:", prompt)
        self.assertIn("DataState -> apply_delta -> render", prompt)
        self.assertIn("apply_delta updates the accumulated value", prompt)
        self.assertIn("class DataState", prompt)
        self.assertNotIn("unrelated_menu", prompt)

    def test_placeholder_main_fails_structured_spec_gate(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- name: workflow_app

## Required Components

- `AppConfig`
- `main()`
"""
        )

        issues = _validate_structured_spec_output('def main():\n    print("placeholder")\n', plan)

        self.assertEqual([issue["kind"] for issue in issues], ["missing_component"])
        self.assertIn("AppConfig", issues[0]["summary"])

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
