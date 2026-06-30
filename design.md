# Design Constraints

This file is treated as static prompt context. Generated code must respect these design constraints when a task involves UI, style, components, or architecture.

## Visual & Architectural Design Constraints

You are provided with a `design.md` context. You must adhere to the design system described within it when generating code.

Instructions:

1. Token Adherence: When defining styles, spacing, or component properties, use the values defined in `design.md`. Do not invent new magic numbers or colors.
2. Component Logic: Use the component patterns defined in `design.md`. If a component exists in the design system, use it rather than building a custom implementation.
3. Validation: Generated code may be audited against the tokens in `design.md`. Any violation of the design system can be treated as a `STATIC_VIOLATION` by the harness.

## Default Tokens

- Spacing: use `4`, `8`, `12`, `16`, `24`, `32`, or `48`.
- Radius: use `4` or `8`.
- Layout: prefer explicit, readable hierarchy over decorative layout.
- Color: use named design tokens from the project when available. Do not invent raw hex colors unless the task explicitly requires a new token.

## Current Task Contract

Refactor target code to satisfy static engine rules, behavioral correctness, and design constraints when design constraints are relevant to the code being generated.

## Harness Architecture Design

The harness is designed as a generalized code-generation and repair system. It should not become specialized around a single fixture, game, or demo prompt.

Design principles:

1. Parse first. No engine should analyze source that failed the parse contract.
2. Run the complete engine set for every valid draft and every repaired draft.
3. Keep engine outputs structured enough for both humans and `RepairStrategyAgent`.
4. Treat model output as untrusted until it passes static policy and, when available, behavior validation.
5. Prefer deterministic gates and explicit diagnostics over broad agent judgment.

## Engine Pass Contract

For Python, the complete engine pass is:

```text
ParseContractAgent
-> EngineRegistry
-> MathEngine
-> HazardsEngine
-> BranchingEngine
-> validation policy
-> RepairStrategyAgent
```

Every successful or failed attempt should preserve this audit trail. A repair is not accepted just because the model claims it fixed the code; it must be parsed and rescanned.

## Diagnostic Repair Design

Engine findings should expose a stable diagnostic shape:

```json
{
  "violation": "CYCLOMATIC_COMPLEXITY_EXCEEDED",
  "threshold": "<= 7",
  "actual": "13",
  "location": "5 conditional branches",
  "recommended_refactor": "Extract branch-heavy decisions into small helper functions and replace repeated if/elif chains with lookup tables or guard clauses."
}
```

The repair strategy should consume this diagnostic directly. Generic fallback instructions are acceptable, but engine-provided recommendations should take priority because they are grounded in the structural scan.

## Fixture Policy

Snake, maze-runner, and similar terminal-game prompts are stress fixtures only. They are useful because they create realistic loops, branches, input handling, and state transitions. They should not drive task-specific architecture, helper names, or permanent rules unless the rule generalizes to other generated code.
