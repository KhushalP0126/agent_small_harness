import unittest

from agents.generation_controller import GenerationController
from kernel.function_contracts import ContractQueue, DealExample, FunctionContract, parse_contract_queue_json
from prompt.contract_builder import build_deal_contract_architect_prompt
from validation.deal_contracts import is_deal_available, validate_deal_examples


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
