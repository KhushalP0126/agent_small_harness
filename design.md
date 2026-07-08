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
4. Treat model output as untrusted until it passes static policy, behavior validation, and any enabled formal validation.
5. Prefer deterministic gates and explicit diagnostics over broad agent judgment.
6. Treat the architect LLM as a repair worker, not as an authority. Architect output is accepted only after the same parse, engine, policy, and behavior gates pass.

## Engine Pass Contract

For Python, the complete engine pass is:

```text
ParseContractAgent
-> EngineRegistry
-> MathEngine
-> HazardsEngine
-> BranchingEngine
-> CostEngine
-> BoundsEngine
-> StateFlowEngine
-> LintEngine when available
-> validation policy
-> RepairStrategyAgent
```

Every successful or failed attempt should preserve this audit trail. A repair is not accepted just because the model claims it fixed the code; it must be parsed and rescanned.

Bounds and state-flow have different policy defaults:

- Bounds warnings are advisory by default because full bounds proof requires broader dataflow and value reasoning.
- State-flow warnings are blocking by default because the engine is intentionally narrow: it flags state-like helper parameters that are reassigned without returning the updated value, a concrete failure mode observed in parser/config tasks.

## Formal Verification Design

Formal tooling is layered by responsibility:

```text
Plan Mode -> Deal contract candidates
Controller validation -> optional CrossHair counterexample checks
Architect tier -> Nagini-oriented formalization candidates
```

Rules:

- Deal belongs in the Plan layer. It turns extracted examples and explicit requirements into executable contract candidates.
- CrossHair belongs beside behavior validation. It is a semantic counterexample finder and may block completion only when enabled and available.
- Nagini belongs behind the architect model. The big model may rewrite small critical helpers into typed, proof-friendly Python and add pre/postconditions, but that output is still rechecked by the harness.
- The small worker should not be expected to invent formal contracts reliably. It should implement code against contracts supplied by Plan Mode or the architect.
- Missing optional verifier dependencies must not break the default harness; they should skip or remain disabled unless explicitly enabled.

## Escalation Design

The harness uses a two-worker repair model:

```text
small worker -> engines/validators -> retry
if repeated failure or stagnation -> architect worker -> engines/validators
if still failing -> manual_review_required
```

Rules:

- The small worker receives the first generation and the first repair opportunity.
- The architect worker receives the current draft plus engine feedback, behavior failures, diagnostic deltas, and prior failed attempt context.
- The controller records `repair_worker` for every repair prompt so logs can show whether `small_worker`, `architect_llm`, or `small_worker->architect_llm` was used.
- Backend failures from the architect API become structured `manual_review_required` payloads instead of uncaught crashes.
- The human-review payload remains the final safety boundary when both model tiers fail.

## Plan Mode Design

Plan Mode should improve first-pass worker success without becoming a task-specific template system.

Rules:

- Full Plan Mode context is for humans and architect workers.
- Small local workers receive the compact worker packet from `PlanModeAgent.to_worker_packet`.
- Parser/config tasks may include state-machine constraints, but should not receive full solution templates.
- Library tasks should include adapter rules so the worker treats dependencies as opaque boundaries with explicit inputs and outputs.
- If a state-machine/parser task fails after one small-worker repair, route to architect or human review instead of spending repeated small-worker retries.

## Artifact Review Design

Artifact directories are part of the safety boundary.

- Save the generated draft for each attempt.
- Save validation reports, retry prompts, findings, and session summaries.
- Save `attempt_timeline.json` for quick review and UI/dashboard consumption.
- Human review tooling should read artifacts instead of re-running model calls.

## Secret Handling Design

API secrets are local runtime configuration, not source code.

- `.env` is the local secret file and is ignored by git.
- `.env.example` is the committed template.
- `DEEPSEEK_API_KEY` enables the DeepSeek architect backend.
- Shell environment variables override `.env` values.
- Markdown docs, history files, run logs, and tests must not contain real API keys.

## Configuration Design

`config.yaml` is the declarative control surface for the harness. Runtime code should prefer reading validated config values over hard-coded thresholds when a setting is operational policy rather than implementation detail.

Config principles:

- Fail fast on malformed config before model execution starts.
- Reject unknown keys so typos do not silently weaken policy.
- Keep secrets out of `config.yaml`; secrets belong in `.env` or shell environment variables.
- Use config for thresholds, retry budgets, model names, and behavior timeouts.
- Keep engine implementation logic in code; keep deployment and policy choices in config.

Current parser tradeoff:

- The loader uses strict dataclasses plus a supported YAML subset.
- This avoids adding a required Pydantic/PyYAML dependency while the repo remains lightweight.
- A future UI can still use `agents/config_loader.py` as the schema boundary.

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

Additional diagnostic examples:

- `BOUNDS_RISK`: guard indices, iterate directly, or avoid one-past-end range/index patterns.
- `STATE_FLOW_RISK`: return updated parser/event state from helpers and assign it at the call site.

## Fixture Policy

Snake, maze-runner, and similar terminal-game prompts are stress fixtures only. They are useful because they create realistic loops, branches, input handling, and state transitions. They should not drive task-specific architecture, helper names, or permanent rules unless the rule generalizes to other generated code.

## Capability Test Design

Coding-capability tests should be hard enough to expose small-model weaknesses without becoming problem-specific templates. Good tests include:

- nested collection transforms
- parsing and normalization
- sorting and tie-breaking
- grouping and aggregation
- explicit edge cases
- behavior specs that catch trivial or hardcoded solutions

The fixture supplier may contain known-good implementations only to validate the harness offline. The model path should not receive task-specific solution templates unless a separate experiment explicitly tests template-directed synthesis.
