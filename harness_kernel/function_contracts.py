from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class DealExample:
    """A concrete example that can be rendered as a deal.example decorator."""

    call: str
    expected: str

    def decorator(self) -> str:
        return f"@deal.example(lambda: {self.call} == {self.expected})"


@dataclass
class FunctionContract:
    """A small function-level contract emitted by the architect layer."""

    name: str
    signature: str
    kind: str = "function"
    purpose: str = ""
    inputs: list[str] = field(default_factory=list)
    output: str = ""
    invariants: list[str] = field(default_factory=list)
    examples: list[DealExample] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    target_file: str = ""

    def normalized_signature(self) -> str:
        signature = self.signature.strip()
        if self.kind == "class":
            if signature.startswith("class "):
                return signature[:-1] if signature.endswith(":") else signature
            return f"class {self.name}"
        if signature.startswith("def "):
            return signature[:-1] if signature.endswith(":") else signature
        return f"def {signature[:-1] if signature.endswith(':') else signature}"

    def to_deal_scaffold(self, include_import: bool = True) -> str:
        lines: list[str] = []
        if include_import:
            lines.extend(["import deal", ""])
        lines.extend(example.decorator() for example in self.examples)
        lines.append(f"{self.normalized_signature()}:")
        lines.append(f'    """{self.purpose or "Implement this contract."}"""')
        for item in self.inputs:
            lines.append(f"    # input: {item}")
        if self.output:
            lines.append(f"    # output: {self.output}")
        for invariant in self.invariants:
            lines.append(f"    # invariant: {invariant}")
        if self.dependencies:
            lines.append(f"    # dependencies: {', '.join(self.dependencies)}")
        if self.kind == "class":
            lines.append("    pass")
        else:
            lines.append("    raise NotImplementedError(\"worker must implement this contract\")")
        return "\n".join(lines)

    def to_worker_packet(self) -> str:
        lines = [
            "FUNCTION CONTRACT PACKET:",
            f"NAME: {self.name}",
            f"KIND: {self.kind}",
            f"SIGNATURE: {self.normalized_signature()}",
        ]
        if self.purpose:
            lines.append(f"PURPOSE: {self.purpose}")
        if self.inputs:
            lines.append("INPUTS:")
            lines.extend(f"- {item}" for item in self.inputs)
        if self.output:
            lines.append(f"OUTPUT: {self.output}")
        if self.examples:
            lines.append("DEAL EXAMPLES:")
            lines.extend(f"- {example.call} == {example.expected}" for example in self.examples)
        if self.invariants:
            lines.append("INVARIANTS:")
            lines.extend(f"- {item}" for item in self.invariants)
        if self.dependencies:
            lines.append("DEPENDENCIES:")
            lines.extend(f"- {item}" for item in self.dependencies)
        if self.target_file:
            lines.append(f"TARGET FILE: {self.target_file}")
        lines.extend(
            [
                "FINAL RULES:",
                "- Implement only this contract.",
                f"- Implement only this {self.kind} contract.",
                "- Return only complete Python code for this symbol and required tiny local helpers.",
                "- Preserve the signature exactly.",
                "- Do not add imports unless the contract explicitly requires them.",
            ]
        )
        return "\n".join(lines)


@dataclass
class ContractQueue:
    """Ordered function contracts for functionwise implementation."""

    contracts: list[FunctionContract] = field(default_factory=list)

    def to_deal_scaffold(self) -> str:
        if not self.contracts:
            return "import deal\n"
        chunks = ["import deal"]
        for contract in self.contracts:
            chunks.append(contract.to_deal_scaffold(include_import=False))
        return "\n\n".join(chunks)

    def to_worker_packets(self) -> list[str]:
        return [contract.to_worker_packet() for contract in self.contracts]


@dataclass
class ContractQueuePlan:
    """Compact architect plan applied to spec-derived contracts."""

    contract_order: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    contract_notes: dict[str, str] = field(default_factory=dict)
    file_ownership: dict[str, str] = field(default_factory=dict)


def parse_contract_queue_json(text: str) -> ContractQueue:
    """Parse architect JSON into a ContractQueue.

    The architect is asked for strict JSON, but API responses often wrap JSON in
    fences. This parser accepts either raw JSON or a fenced JSON block.
    """

    payload = _json_payload(text)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("contract queue must be a JSON object")
    raw_contracts = data.get("contracts", [])
    if not isinstance(raw_contracts, list):
        raise ValueError("contracts must be a list")
    return ContractQueue(contracts=[_contract_from_mapping(item) for item in raw_contracts])


def parse_contract_queue_plan_json(text: str) -> ContractQueuePlan:
    """Parse compact architect planner JSON.

    This intentionally avoids requiring full signatures/examples from the
    architect. The harness owns contract construction from the structured spec;
    the architect only chooses queue order, dependencies, and focused notes.
    """

    payload = _json_payload(text)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("contract queue plan must be a JSON object")
    order = _string_list(data.get("contract_order", data.get("order", [])), "contract_order")
    dependencies = _string_list_mapping(data.get("dependencies", {}), "dependencies")
    notes = _string_mapping(data.get("contract_notes", data.get("notes", {})), "contract_notes")
    ownership = _string_mapping(
        data.get("file_ownership", data.get("target_files", {})),
        "file_ownership",
    )
    if not order and not dependencies and not notes and not ownership:
        raise ValueError("contract queue plan must contain an order, dependencies, or notes")
    return ContractQueuePlan(
        contract_order=order,
        dependencies=dependencies,
        contract_notes=notes,
        file_ownership=ownership,
    )


def _json_payload(text: str) -> str:
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    return match.group("body").strip() if match else stripped


def _contract_from_mapping(item: Any) -> FunctionContract:
    if not isinstance(item, dict):
        raise ValueError("each contract must be an object")
    name = _required_str(item, "name")
    signature = _required_str(item, "signature")
    examples = [_example_from_value(value) for value in _list(item.get("deal_examples", item.get("examples", [])))]
    return FunctionContract(
        name=name,
        signature=signature,
        kind=str(item.get("kind", "function")).strip() or "function",
        purpose=str(item.get("purpose", "")).strip(),
        inputs=[str(value).strip() for value in _list(item.get("inputs", [])) if str(value).strip()],
        output=str(item.get("output", "")).strip(),
        invariants=[str(value).strip() for value in _list(item.get("invariants", [])) if str(value).strip()],
        examples=examples,
        dependencies=[str(value).strip() for value in _list(item.get("dependencies", [])) if str(value).strip()],
        target_file=str(
            item.get("target_file", item.get("file_path", item.get("file", "")))
        ).strip(),
    )


def _example_from_value(value: Any) -> DealExample:
    if isinstance(value, dict):
        return DealExample(call=_required_str(value, "call"), expected=_required_str(value, "expected"))
    if isinstance(value, str) and "==" in value:
        call, expected = value.split("==", 1)
        return DealExample(call=call.strip(), expected=expected.strip())
    raise ValueError("deal examples must be objects with call/expected or 'call == expected' strings")


def _required_str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _string_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _string_list_mapping(value: Any, key: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    result: dict[str, list[str]] = {}
    for raw_name, raw_dependencies in value.items():
        name = str(raw_name).strip()
        if not name:
            continue
        result[name] = _string_list(raw_dependencies, f"{key}.{name}")
    return result


def _string_mapping(value: Any, key: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return {
        str(raw_name).strip(): str(raw_note).strip()
        for raw_name, raw_note in value.items()
        if str(raw_name).strip() and str(raw_note).strip()
    }
