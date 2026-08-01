import os
import unittest
from unittest.mock import PropertyMock, patch

from agents.plan_mode import PlanModeAgent
from backends.architect_client import (
    CONTRACT_PROFILE,
    REPAIR_PROFILE,
    ArchitectConfig,
    ArchitectModelSupplier,
    ContractArchitectError,
    ContractArchitectSupplier,
    ContractPlannerSupplier,
)
from scripts.run_structured_spec import (
    _accepted_type_context,
    _apply_contract_plan,
    _contract_queue_from_architect,
    _fallback_contract_queue,
    _run_contract_queue_sequentially,
    _run_integration_smoke_test,
    _single_contract_prompt,
    _validate_contract_source,
    _validate_structured_spec_output,
    _validated_source_event,
)
from harness_kernel.function_contracts import ContractQueue, ContractQueuePlan, DealExample, FunctionContract


class StructuredSpecRunnerTests(unittest.TestCase):
    def test_validated_source_event_requires_completed_final_status(self) -> None:
        source = "def main():\n    return 0\n"
        self.assertEqual(
            _validated_source_event(source, "python", "completed", "artifacts/run-1"),
            {
                "type": "validated_source",
                "language": "python",
                "source": source,
                "artifact_path": "artifacts/run-1",
            },
        )
        self.assertIsNone(
            _validated_source_event(source, "python", "manual_review_required", "")
        )

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

    def test_structured_spec_gate_rejects_missing_local_imports(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Files

- `app.py`
- `helpers.py`

## Required Components

- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "from missing_helpers import build\n\ndef main():\n    return build()\n"

        issues = _validate_structured_spec_output(source, plan)

        self.assertEqual(issues[0]["kind"], "missing_local_import")

    def test_structured_spec_gate_rejects_hallucinated_stdlib_symbol(self) -> None:
        plan = PlanModeAgent().plan(
            """
## Required Components

- `main()`

## Entrypoint

- `main()`
"""
        )
        source = "from dataclasses import FrozenDataclass\n\ndef main():\n    pass\n"

        issues = _validate_structured_spec_output(source, plan)

        self.assertEqual(issues[0]["kind"], "missing_import_symbol")
        self.assertEqual(issues[0]["details"], "dataclasses.FrozenDataclass")

    def test_contract_gate_rejects_hallucinated_stdlib_symbol_before_execution(self) -> None:
        contract = FunctionContract(name="helper", signature="def helper() -> int")
        source = "from dataclasses import FrozenDataclass\n\ndef helper() -> int:\n    return 1\n"

        issues = _validate_contract_source(source, contract)

        self.assertEqual(issues[0]["kind"], "contract_missing_import_symbol")
        self.assertEqual(issues[0]["details"], "dataclasses.FrozenDataclass")

    def test_contract_gate_rejects_hallucinated_stdlib_submodule(self) -> None:
        contract = FunctionContract(name="helper", signature="def helper() -> int")
        source = "from os.nonexistent_api import helper_value\n\ndef helper() -> int:\n    return 1\n"

        issues = _validate_contract_source(source, contract)

        self.assertEqual(issues[0]["kind"], "contract_missing_import_symbol")
        self.assertEqual(issues[0]["details"], "os.nonexistent_api")

    def test_contract_examples_do_not_receive_parent_api_keys(self) -> None:
        contract = FunctionContract(
            name="read_secret",
            signature="def read_secret()",
            examples=[DealExample("read_secret()", "None")],
        )
        source = "import os\n\ndef read_secret():\n    return os.environ.get('DEEPSEEK_API_KEY')\n"

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak"}):
            issues = _validate_contract_source(source, contract)

        self.assertEqual(issues, [])

    def test_downstream_prompt_includes_accepted_immutable_field_types(self) -> None:
        accepted = """
class Ball:
    def __init__(self):
        self.velocity = (3, -2)

    def next_position(self, x: int, y: int) -> tuple[int, int]:
        return (x + self.velocity[0], y + self.velocity[1])
"""
        type_context = _accepted_type_context([accepted])
        plan = PlanModeAgent().plan("")
        contract = FunctionContract(name="reflect", signature="def reflect(ball: Ball) -> None")

        prompt = _single_contract_prompt(
            plan,
            contract,
            [accepted],
            ["Ball"],
            accepted_type_context=type_context,
        )

        self.assertIn("ACCEPTED TYPE CONTRACTS:", prompt)
        self.assertIn("Ball.velocity: tuple[int, int] (immutable)", prompt)
        self.assertIn(
            "Ball.next_position(x, y) -> tuple[int, int]; call arity: exactly 2 positional (excluding self/cls)",
            prompt,
        )
        self.assertIn("must be replaced, not item-mutated", prompt)
        self.assertIn("method signatures and call arities are binding", prompt)

    def test_accepted_method_context_tracks_optional_and_variadic_arity(self) -> None:
        accepted = """
class Adapter:
    @classmethod
    def build(cls, source, mode="safe") -> "Adapter":
        return cls()

    @staticmethod
    def combine(first, *rest):
        return (first, *rest)
"""

        context = _accepted_type_context([accepted])

        self.assertIn(
            "Adapter.build(source, mode) -> 'Adapter'; call arity: 1 to 2 positional (excluding self/cls)",
            context,
        )
        self.assertIn(
            "Adapter.combine(first, *rest) -> unknown; call arity: at least 1 positional (excluding self/cls)",
            context,
        )

    def test_integration_smoke_gate_rejects_immediate_runtime_crash(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- language: python

## Entrypoint

- `main()`
"""
        )
        source = """
def main():
    velocity = (1, 2)
    velocity[0] *= -1

if __name__ == "__main__":
    main()
"""

        result = _run_integration_smoke_test(source, plan, timeout_seconds=0.5)

        self.assertFalse(result["is_compliant"])
        self.assertEqual(result["status"], "crashed")
        self.assertEqual(result["issues"][0]["kind"], "integration_smoke_crash")
        self.assertIn("tuple", result["issues"][0]["details"])

    def test_integration_smoke_gate_accepts_long_running_interactive_entrypoint(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- language: python

## Entrypoint

- `main()`
"""
        )
        source = """
import time

def main():
    while True:
        time.sleep(0.01)

if __name__ == "__main__":
    main()
"""

        result = _run_integration_smoke_test(source, plan, timeout_seconds=0.05)

        self.assertTrue(result["is_compliant"])
        self.assertEqual(result["status"], "running_after_smoke_window")

    def test_integration_smoke_does_not_receive_parent_api_keys(self) -> None:
        plan = PlanModeAgent().plan(
            """
## App Spec

- language: python

## Entrypoint

- `main()`
"""
        )
        source = """
import os

if os.environ.get("DEEPSEEK_API_KEY"):
    raise RuntimeError("secret leaked")
"""

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak"}):
            result = _run_integration_smoke_test(source, plan, timeout_seconds=0.5)

        self.assertTrue(result["is_compliant"])
        self.assertEqual(result["status"], "exited_cleanly")

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

    def test_contract_queue_resume_skips_checkpointed_contracts(self) -> None:
        plan = PlanModeAgent().plan("")
        queue = ContractQueue(
            [
                FunctionContract(
                    name="first",
                    signature="def first() -> int",
                    examples=[DealExample("first()", "1")],
                ),
                FunctionContract(
                    name="second",
                    signature="def second() -> int",
                    examples=[DealExample("second()", "2")],
                ),
            ]
        )
        checkpointed_results = []

        def interrupt_after_first(_sources, results) -> None:
            checkpointed_results[:] = [
                type(result)(**result.__dict__) for result in results
            ]
            raise RuntimeError("simulated hard interruption")

        with self.assertRaisesRegex(RuntimeError, "simulated hard interruption"):
            _run_contract_queue_sequentially(
                queue,
                plan,
                lambda prompt: (
                    "def first() -> int:\n    return 1\n"
                    if "NAME: first" in prompt
                    else self.fail("second must not run before interruption")
                ),
                checkpoint_writer=interrupt_after_first,
            )

        calls = []

        def resumed_generate(prompt: str) -> str:
            calls.append(prompt)
            self.assertIn("NAME: second", prompt)
            return "def second() -> int:\n    return 2\n"

        accepted_sources, results = _run_contract_queue_sequentially(
            queue,
            plan,
            resumed_generate,
            resume_results=checkpointed_results,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual([result.name for result in results], ["first", "second"])
        self.assertEqual(len(accepted_sources), 2)

    def test_contract_queue_isolates_independent_contract_failure(self) -> None:
        plan = PlanModeAgent().plan("")
        queue = ContractQueue(
            [
                FunctionContract(
                    name="doomed",
                    signature="def doomed() -> int",
                    examples=[DealExample("doomed()", "1")],
                ),
                FunctionContract(
                    name="independent",
                    signature="def independent() -> int",
                    examples=[DealExample("independent()", "5")],
                ),
                FunctionContract(
                    name="depends_on_doomed",
                    signature="def depends_on_doomed() -> int",
                    dependencies=["doomed"],
                ),
            ]
        )

        def generate(prompt: str) -> str:
            if "NAME: doomed" in prompt:
                return "def doomed() -> int:\n    return 0\n"
            if "NAME: independent" in prompt:
                return "def independent() -> int:\n    return 5\n"
            raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        accepted_sources, results = _run_contract_queue_sequentially(
            queue,
            plan,
            generate,
            repair_draft=lambda _draft, _prompt: "def doomed() -> int:\n    return -1\n",
            small_retries_per_contract=1,
        )

        results_by_name = {result.name: result for result in results}
        self.assertEqual(results_by_name["doomed"].status, "validation_failed")
        self.assertEqual(results_by_name["independent"].status, "accepted")
        self.assertIn("def independent", "\n".join(accepted_sources))
        blocked = next(
            result for result in results if result.status == "dependency_blocked"
        )
        self.assertIn("depends_on_doomed", blocked.issues[0]["details"])

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

    def test_contract_plan_applies_order_dependencies_and_notes(self) -> None:
        queue = ContractQueue(
            [
                FunctionContract(name="main", signature="def main()"),
                FunctionContract(name="update", signature="def update()"),
                FunctionContract(name="State", kind="class", signature="class State:"),
            ]
        )
        plan = ContractQueuePlan(
            contract_order=["State", "update", "main"],
            dependencies={"update": ["State"], "main": ["update"]},
            contract_notes={"update": "Preserve the state transition graph."},
        )

        planned = _apply_contract_plan(queue, plan)

        self.assertEqual([contract.name for contract in planned.contracts], ["State", "update", "main"])
        update = next(contract for contract in planned.contracts if contract.name == "update")
        main = next(contract for contract in planned.contracts if contract.name == "main")
        self.assertIn("State", update.dependencies)
        self.assertIn("update", main.dependencies)
        self.assertIn("Architect note: Preserve the state transition graph.", update.purpose)

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

    def test_contract_planner_uses_light_profile(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs):
                self.calls.append(kwargs)
                return '{"contract_order":["f"],"dependencies":{},"contract_notes":{"f":"keep it small"}}'

        client = StubClient()
        supplier = ContractPlannerSupplier(client=client)

        plan = supplier.build_contract_plan("PLAN PACKET:", "", available_contracts=["f"])

        self.assertEqual(plan.contract_order, ["f"])
        self.assertEqual(plan.contract_notes["f"], "keep it small")
        self.assertEqual(client.calls[0]["profile"], CONTRACT_PROFILE)

    def test_contract_planner_failure_uses_spec_derived_fallback_queue(self) -> None:
        class FailingPlanner:
            def __init__(self, profile=None):
                self.profile = profile

            def build_contract_plan(self, **kwargs):
                raise ContractArchitectError("architect_contract_plan_invalid_json", "bad json")

        base_queue = ContractQueue(
            [
                FunctionContract(name="prepare", signature="def prepare() -> int"),
                FunctionContract(name="main", signature="def main()", dependencies=["prepare"]),
            ]
        )

        with patch("scripts.run_structured_spec.ContractPlannerSupplier", FailingPlanner), patch.object(
            ArchitectConfig,
            "api_key_configured",
            new_callable=PropertyMock,
            return_value=True,
        ):
            queue, metadata = _contract_queue_from_architect(
                "PLAN PACKET:",
                "CONTEXT",
                CONTRACT_PROFILE,
                base_queue,
            )

        self.assertEqual([contract.name for contract in queue.contracts], ["prepare", "main"])
        self.assertTrue(metadata["architect_contracts_fallback_used"])
        self.assertEqual(metadata["architect_contract_count"], 2)

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

    def test_repair_architect_strips_unclosed_markdown_fence(self) -> None:
        class StubClient:
            def generate(self, **kwargs):
                return "```python\n\ndef fixed():\n    return 1\n"

        supplier = ArchitectModelSupplier(config=ArchitectConfig(repair_profile=REPAIR_PROFILE), client=StubClient())

        source = supplier.repair_draft("", "fix")

        self.assertEqual(source, "def fixed():\n    return 1")

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
