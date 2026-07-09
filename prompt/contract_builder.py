from __future__ import annotations


def build_deal_contract_architect_prompt(
    plan_packet: str,
    preserved_context: str = "",
) -> str:
    """Build an architect prompt that asks for function contracts, not code."""

    return "\n".join(
        [
            "DEAL CONTRACT ARCHITECT MODE",
            "",
            "You are not implementing the program yet.",
            "Break the task into small Python function contracts that a local worker can implement one at a time.",
            "Use Deal-compatible concrete examples where behavior is known.",
            "",
            "Return JSON only. Do not return markdown. Do not implement function bodies.",
            "",
            "JSON SCHEMA:",
            "{",
            '  "contracts": [',
            "    {",
            '      "name": "function_name",',
            '      "signature": "def function_name(arg: type) -> type",',
            '      "purpose": "one sentence describing the function",',
            '      "inputs": ["input constraints in plain English"],',
            '      "output": "return value contract in plain English",',
            '      "invariants": ["state or correctness constraints"],',
            '      "deal_examples": [',
            '        {"call": "function_name(example)", "expected": "expected_value"}',
            "      ],",
            '      "dependencies": ["earlier_contract_name_if_needed"]',
            "    }",
            "  ]",
            "}",
            "",
            "CONTRACT RULES:",
            "- Each contract must be independently testable.",
            "- Keep each function small enough for a local worker to implement.",
            "- Preserve state-machine and dependency-graph rules from the context.",
            "- Put integration last; helpers must appear before functions that depend on them.",
            "- Use concrete deal_examples only when the expected value is explicit.",
            "- Put vague requirements in invariants, not fake executable examples.",
            "",
            "PRESERVED CONTEXT:",
            preserved_context.strip() or "(none)",
            "",
            "PLAN PACKET:",
            plan_packet.strip(),
        ]
    )
