import unittest

from agents.generation_controller import GenerationController
from engines.hazards_engine import HazardsEngine
from kernel.function_contracts import (
    ContractQueue,
    DealExample,
    FunctionContract,
    parse_contract_queue_json,
    parse_contract_queue_plan_json,
)
from prompt.contract_builder import build_contract_queue_planner_prompt, build_deal_contract_architect_prompt
from scripts.run_structured_spec import _initial_prompt
from validation.deal_contracts import is_deal_available, validate_deal_examples
from validation.policy import validate_findings


class DealContractQueueTests(unittest.TestCase):
    def test_architect_prompt_requests_contracts_not_code(self) -> None:
        prompt = build_deal_contract_architect_prompt(
            plan_packet="PLAN PACKET:\nFUNCTION: parse_int_list",
            preserved_context="DEPENDENCY GRAPH:\n- tokens -> validation -> integers",
        )

        self.assertIn("DEAL CONTRACT ARCHITECT MODE", prompt)
        self.assertIn("Return JSON only", prompt)
        self.assertIn('"contracts"', prompt)
        self.assertIn('"deal_examples"', prompt)
        self.assertIn('"dependencies"', prompt)
        self.assertIn("Do not return markdown. Do not implement function bodies.", prompt)
        self.assertIn("DEPENDENCY GRAPH:", prompt)

    def test_contract_planner_prompt_requests_plan_not_full_contracts(self) -> None:
        prompt = build_contract_queue_planner_prompt(
            plan_packet="PLAN PACKET:\nTASK: app",
            preserved_context="DEPENDENCY GRAPH:\n- State -> update -> main",
            available_contracts=["State", "update", "main"],
        )

        self.assertIn("CONTRACT QUEUE PLANNER MODE", prompt)
        self.assertIn('"contract_order"', prompt)
        self.assertIn('"dependencies"', prompt)
        self.assertIn('"contract_notes"', prompt)
        self.assertIn("- State", prompt)
        self.assertIn("Do not include signatures, examples, code, or full contracts.", prompt)
        self.assertNotIn('"signature"', prompt)
        self.assertNotIn('"contracts"', prompt)

    def test_parse_contract_queue_plan_json_accepts_minimal_plan(self) -> None:
        plan = parse_contract_queue_plan_json(
            """
```json
{
  "contract_order": ["State", "update", "main"],
  "dependencies": {"update": ["State"], "main": ["update"]},
  "contract_notes": {"update": "preserve state transition semantics"}
}
```
"""
        )

        self.assertEqual(plan.contract_order, ["State", "update", "main"])
        self.assertEqual(plan.dependencies["main"], ["update"])
        self.assertEqual(plan.contract_notes["update"], "preserve state transition semantics")

    def test_parses_fenced_contract_queue_json(self) -> None:
        queue = parse_contract_queue_json(
            """
```json
{
  "contracts": [
    {
      "name": "parse_token",
      "signature": "def parse_token(token: str) -> int | None",
      "purpose": "Parse one signed integer token or return None.",
      "inputs": ["token may contain surrounding whitespace"],
      "output": "integer or None",
      "invariants": ["accept optional leading plus or minus sign"],
      "deal_examples": [
        {"call": "parse_token('-2')", "expected": "-2"},
        "parse_token('x') == None"
      ],
      "dependencies": []
    }
  ]
}
```
"""
        )

        self.assertEqual(len(queue.contracts), 1)
        contract = queue.contracts[0]
        self.assertEqual(contract.name, "parse_token")
        self.assertEqual(contract.normalized_signature(), "def parse_token(token: str) -> int | None")
        self.assertEqual(len(contract.examples), 2)
        self.assertIn("accept optional leading plus or minus sign", contract.invariants)

    def test_deal_scaffold_uses_real_deal_decorators(self) -> None:
        queue = ContractQueue(
            contracts=[
                FunctionContract(
                    name="parse_token",
                    signature="parse_token(token: str) -> int | None",
                    purpose="Parse a signed integer token.",
                    examples=[DealExample(call="parse_token('-2')", expected="-2")],
                )
            ]
        )

        scaffold = queue.to_deal_scaffold()

        self.assertIn("import deal", scaffold)
        self.assertIn("@deal.example(lambda: parse_token('-2') == -2)", scaffold)
        self.assertIn("def parse_token(token: str) -> int | None:", scaffold)
        self.assertIn("raise NotImplementedError", scaffold)

    def test_worker_packet_is_one_function_contract(self) -> None:
        contract = FunctionContract(
            name="parse_token",
            signature="def parse_token(token: str) -> int | None",
            purpose="Parse one token.",
            examples=[DealExample(call="parse_token('+3')", expected="3")],
            dependencies=["none"],
        )

        packet = contract.to_worker_packet()

        self.assertIn("FUNCTION CONTRACT PACKET:", packet)
        self.assertIn("SIGNATURE: def parse_token(token: str) -> int | None", packet)
        self.assertIn("DEAL EXAMPLES:", packet)
        self.assertIn("parse_token('+3') == 3", packet)
        self.assertIn("Implement only this function contract.", packet)

    def test_structured_spec_prompt_includes_contract_queue(self) -> None:
        queue = ContractQueue(
            contracts=[
                FunctionContract(
                    name="next_head",
                    signature="def next_head(head: tuple[int, int], direction: tuple[int, int]) -> tuple[int, int]",
                    purpose="Move one grid cell.",
                    examples=[DealExample(call="next_head((5, 5), (1, 0))", expected="(6, 5)")],
                )
            ]
        )

        prompt = _initial_prompt("PLAN PACKET:\nFUNCTION: app", queue)

        self.assertIn("ARCHITECT FUNCTION CONTRACT QUEUE:", prompt)
        self.assertIn("import deal", prompt)
        self.assertIn("@deal.example(lambda: next_head((5, 5), (1, 0)) == (6, 5))", prompt)
        self.assertIn("FUNCTIONWISE WORKER PACKETS:", prompt)

    def test_deal_import_is_allowed_for_contract_decorators(self) -> None:
        source = """
import deal

@deal.example(lambda: identity(1) == 1)
def identity(value):
    return value
"""

        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}

        self.assertNotIn("External dependency usage", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)

    @unittest.skipUnless(is_deal_available(), "deal is not installed")
    def test_deal_validator_executes_examples_with_deal_library(self) -> None:
        source = """
import deal

@deal.example(lambda: parse_token("-2") == -2)
def parse_token(token: str) -> int | None:
    if token.strip().isdigit():
        return int(token)
    return None
"""

        result = validate_deal_examples(source)

        self.assertFalse(result.is_compliant)
        self.assertEqual(result.checked_examples, 1)
        self.assertEqual(result.issues[0].function, "parse_token")
        self.assertIn("ExampleContractError", result.issues[0].details)

    @unittest.skipUnless(is_deal_available(), "deal is not installed")
    def test_deal_validator_passes_satisfied_examples(self) -> None:
        source = """
import deal

@deal.example(lambda: parse_token("-2") == -2)
def parse_token(token: str) -> int | None:
    text = token.strip()
    if text.startswith(("+", "-")):
        digits = text[1:]
    else:
        digits = text
    if not digits.isdigit():
        return None
    return int(text)
"""

        result = validate_deal_examples(source)

        self.assertTrue(result.is_compliant)
        self.assertFalse(result.skipped)
        self.assertEqual(result.checked_examples, 1)

    @unittest.skipUnless(is_deal_available(), "deal is not installed")
    def test_generation_controller_blocks_failed_deal_example(self) -> None:
        source = """
import deal

@deal.example(lambda: parse_token("-2") == -2)
def parse_token(token: str) -> int | None:
    if token.strip().isdigit():
        return int(token)
    return None
"""

        controller = GenerationController(max_retries=0, draft_supplier=lambda _prompt: source)
        result = controller.run(target="deal-contract", initial_prompt="generate")

        self.assertEqual(result.payload["final_status"], "manual_review_required")
        formal_validation = result.payload["attempts"][0]["formal_validation"]
        self.assertEqual(formal_validation["tool"], "deal")
        self.assertFalse(formal_validation["is_compliant"])
        self.assertEqual(result.payload["human_review"]["formal_issues"][0]["function"], "parse_token")


if __name__ == "__main__":
    unittest.main()
