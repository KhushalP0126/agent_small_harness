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
            "Break the task into small Python implementation contracts that a local worker can implement one at a time.",
            "You are the composer: decide the safest order of work.",
            "Use Deal-compatible concrete examples where behavior is known.",
            "",
            "Return JSON only. Do not return markdown. Do not implement function bodies.",
            "",
            "JSON SCHEMA:",
            "{",
            '  "contracts": [',
            "    {",
            '      "name": "symbol_name",',
            '      "kind": "function",',
            '      "signature": "def function_name(arg: type) -> type",',
            '      "purpose": "one sentence describing the class, data object, or function",',
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
            "- Include data/class contracts before functions that use those types.",
            "- For classes, use kind='class' and a signature such as 'class DataState:'.",
            "- For constants/enums, use kind='class' when a small namespace class is enough.",
            "- Each contract must be independently implementable.",
            "- Keep each contract small enough for a local worker to implement.",
            "- Preserve state-machine and dependency-graph rules from the context.",
            "- Put integration last; classes and helpers must appear before functions that depend on them.",
            "- Fill dependencies with every earlier symbol needed by the contract.",
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


def build_contract_queue_planner_prompt(
    plan_packet: str,
    preserved_context: str = "",
    available_contracts: list[str] | None = None,
) -> str:
    """Build a compact architect prompt for ordering spec-derived contracts."""

    available = available_contracts or []
    return "\n".join(
        [
            "CONTRACT QUEUE PLANNER MODE",
            "",
            "You are not implementing code and you are not writing full contracts.",
            "The harness already derived function/class contracts from the structured spec.",
            "Your job is only to decide the safest sequential queue plan for a small local worker.",
            "",
            "Return JSON only. Do not return markdown. Do not include signatures, examples, code, or full contracts.",
            "",
            "JSON SCHEMA:",
            "{",
            '  "contract_order": ["symbol_name"],',
            '  "dependencies": {',
            '    "symbol_name": ["earlier_symbol_required_first"]',
            "  },",
            '  "contract_notes": {',
            '    "symbol_name": "short implementation note for this contract"',
            "  }",
            "}",
            "",
            "PLANNING RULES:",
            "- Use only names listed in AVAILABLE CONTRACTS.",
            "- Put data/classes/constants before helpers that use them.",
            "- Put pure helpers before state update functions.",
            "- Put state update functions before render and main.",
            "- Put integration/entrypoint functions last.",
            "- Dependencies must point only to earlier required symbols.",
            "- Keep notes short and focused on state, edge cases, or validation risks.",
            "",
            "AVAILABLE CONTRACTS:",
            "\n".join(f"- {name}" for name in available) if available else "(none)",
            "",
            "PRESERVED CONTEXT:",
            preserved_context.strip() or "(none)",
            "",
            "PLAN PACKET:",
            plan_packet.strip(),
        ]
    )
