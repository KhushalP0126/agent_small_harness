# Harness Prompt Context

This repository implements a constrained repair harness for small-model code generation.
The model should receive explicit structural rules, behavioral examples, and feedback from failed attempts instead of being asked to infer constraints from raw code alone.

## Autonomous Repair Prompt

You are an autonomous repair agent.

Target: Refactor the provided code to be production-ready.

Compliance Requirements (Static):

- Eliminate all global variable mutations.
- Remove references to `STATE` or other module/global state entirely unless that state is passed in as an explicit function argument.
- Reduce cyclomatic complexity to `< 5` by extracting helper functions.
- Use table-driven, arithmetic, or data-mapping logic instead of replacing one `if/elif` chain with another `if/elif` chain.
- Eliminate module-state mutations.

Functional Requirements (Behavioral):

- The refactored function must maintain strict input/output parity with the original specification.
- If the logic involves a matrix or collection, handle edge cases such as empty input explicitly.
- Do not over-optimize to the point of returning trivial or hardcoded values; the behavior validator checks real cases.
- For matrix scoring behavior, preserve the scoring classes: negative -> 1, zero -> 2, 1..9 -> 3, 10..99 -> 4, >=100 -> 5.

Output:

- Provide only the refactored code.
- Use clear, descriptive function names.
- Do not introduce imports, unresolved type annotations, file I/O, `eval`, `exec`, or external dependencies.
- Do not include demo code, print statements, example invocations, or `if __name__ == "__main__"` blocks.

## Feedback Injection Prompt

Prior failed attempts are binding context. When a retry prompt includes prior failures, the next draft must not repeat them.

Use this format:

```text
PRIOR FAILED ATTEMPTS:
- Attempt N:
  Static failure: <kind> had <current>; required <allowed>.
  Behavior failure: <case> expected <expected> but got <actual> (<details>).
Do not repeat any prior failed pattern.
```

## Behavioral Spec Injection Prompt

The model should see concrete unit-test-like examples before generating code.

Use this format:

```text
Behavioral Unit Test Specification:
- Function under test: analyze
- analyze([]) == 0  # empty matrix
- analyze([[], []]) == 0  # skips empty rows
- analyze([[], [-1, 0, 4, 10, 99, 100]]) == 19  # covers all value classes
- analyze([[1, 2, 3], [10, 0, -5]]) == 16  # mixed rows
```

## Plan-Layer Deal Contracts

Plan Mode may emit Deal contract candidates from extracted behavior examples. These are specification scaffolds for the architect and prompt builder, not proof that the implementation is correct.

Use this shape:

```python
import deal
# Attach these plan-layer contracts to `parse_int_list` before implementation.
@deal.example(lambda: parse_int_list("1, -2, +3") == [1, -2, 3])
@deal.example(lambda: parse_int_list("a, 4, -, 5") == [4, 5])
# Architect may add stronger @deal.pre/@deal.post clauses when requirements are explicit.
```

The small worker should usually satisfy contracts; it should not be asked to invent reliable contracts.

## Compact Worker Packet

Plan Mode has two renderings:

- full plan context for humans and architect workers
- compact worker packet for small local models

The small worker should receive the compact packet:

```text
PLAN PACKET:
FUNCTION: parse_sectioned_config
LANGUAGE: python
EXAMPLES:
- parse_sectioned_config(...) == {...}
STATE RULES:
- track parser state explicitly with an active section variable initialized to None
- only activate section state after a valid non-empty section header is found
- ignore key/value records until an active section exists
ADAPTER RULES:
- treat external libraries as opaque dependencies behind local adapter helpers
FINAL RULES:
- Return only complete Python code.
```

This is intentionally lower-noise than the full `PLAN MODE SPEC`.

## Optional Formal Validation

CrossHair is an optional semantic validator. When enabled in `config.yaml`, the controller runs it after static policy and behavior validation. If CrossHair is not installed, the formal gate is marked skipped and does not block the run.

Nagini is reserved for architect-tier formalization. The big model can translate small critical helpers into typed, proof-friendly Python with preconditions and postconditions, but Nagini output is still treated as untrusted until the normal harness gates accept it.

## Current Session Notes

The harness direction is generalized code creation and repair, not a Snake-game-specific generator. Snake-style tasks are useful smoke tests because they exercise loops, branching, imports, and state, but they are not the target architecture.

Current baseline:

- Python is the primary reliable path.
- Every valid Python draft must pass through the full registered Python engine set on every attempt:
  - `engine-1-math`
  - `engine-2-hazards`
  - `engine-3-branching`
  - `engine-4-cost`
  - `engine-6-bounds`
  - `engine-7-state-flow`
  - `engine-5-lint` when Pylint is available; otherwise it emits a low-severity skipped finding.
- Repaired drafts must be rescanned; no repair path should bypass loop analysis, hazard analysis, or branching analysis.
- The engine evaluator is at `overall_recall: 1.0`.
- The full unit suite last passed locally with `171` tests after adding bounds, state-flow, artifact-review, and ladder updates.
- The harder fixture coding-capability suite passes `7/7` without model calls.
- The live small-worker plus DeepSeek architect test reached `6/7` on the harder task suite.
- The 3B worker-limit ladder reaches difficulty 6 and still breaks on `parse_sectioned_config`; this is treated as a semantic/state-machine worker limit, not an engine-plumbing failure.
- A focused D6 architect run produced `manual_review_required` with contribution `small_made_progress_but_failed:0.25`; final failures were `cyclomatic_complexity`, `state_flow_risk`, and behavior mismatches.
- The stateful Python ladder completed `process_events` and broke on `parse_sectioned_config_stateful`, which confirms the stateful/parser boundary is still hard for the 3B worker.

## Small Worker And Architect Prompting

The small model is the first coding worker. The API-backed architect model is a second repair worker that takes over only after the controller has engine/behavior evidence that the small worker is failing.

The architect is not an engine. It is not trusted by default. Its output must pass the same parse contract, engine registry, static policy, and behavior validator before the controller can mark a task `completed`.

Current backend contract:

- Local worker: Ollama, default `qwen2.5-coder:1.5b`.
- Architect worker: DeepSeek API, default `deepseek-v4-pro`.
- Secrets: read from `.env` or shell env via `DEEPSEEK_API_KEY` or `ARCHITECT_API_KEY`.
- `.env` is ignored by git; `.env.example` is the committed template.
- Escalation command: `make test-coding-capability-architect`.
- Default escalation threshold: `ARCHITECT_AFTER=1`.
- Default architect retry budget: `ARCHITECT_MAX_RETRIES=2`.

## Configuration Source Of Truth

The harness now has a declarative `config.yaml` layer. The loader lives in `agents/config_loader.py` and validates the supported schema before execution.

Config-owned defaults:

- platform environment and log level
- static policy thresholds and allow flags
- behavior validator timeout
- optional CrossHair timeout
- worker and architect model names
- difficulty-based model routing for worker ladders
- architect escalation threshold
- retry/manual-review gate settings

The coding-capability runner accepts `--config config.yaml` and passes config-derived policy and behavior timeout into `GenerationController`. Make targets use `CONFIG_PATH=config.yaml` by default.

Worker ladders can use `MODEL=auto` to select a model from `execution.models.difficulty_models`.

The config loader is intentionally dependency-light. It uses strict dataclasses and a small YAML-subset parser instead of requiring Pydantic/PyYAML. If the project later needs UI-driven config editing, this schema is the contract the UI should read/write.

## Engine Diagnostic Contract

Engines now emit diagnostic metadata in addition to summaries and metrics. This gives the repair loop concrete guidance instead of forcing the model to infer the reason for rejection.

Diagnostic fields:

- `violation`
- `threshold`
- `actual`
- `location`
- `recommended_refactor`

Examples:

- `LOOP_DEPTH_EXCEEDED`: extract inner-loop work into helpers or precompute lookup structures.
- `CYCLOMATIC_COMPLEXITY_EXCEEDED`: extract branch-heavy decisions into helpers, lookup tables, or guard clauses.
- `GLOBAL_MUTATION`: pass state through explicit function arguments or return values.
- `MODULE_STATE_MUTATION`: replace module-state mutation with local data construction and explicit return values.
- `EXTERNAL_DEPENDENCY`: remove third-party imports and use standard-library or local helpers.

The `RepairStrategyAgent` consumes `recommended_refactor` from `violation.evidence["diagnostic"]` and injects it into retry prompts as targeted repair instructions.

## Hard Coding-Capability Suite

The current general-purpose task suite is intentionally more complicated than simple smoke tests. It exercises parsing, grouping, sorting, edge-case behavior, and structural validation:

- `matrix_scoring`
- `dedupe_preserve_order`
- `clamp_values`
- `merge_intervals`
- `parse_key_value_lines`
- `group_top_scores`
- `summarize_transactions`

The suite is designed to measure coding capability, not template matching. Task-specific generation shortcuts should not be added to the normal model path. Fixture solutions exist only to prove the harness and behavior specs are valid without model/API calls.

Recent live result:

- Small worker plus architect completed `6/7`.
- The architect visibly repaired `matrix_scoring`, `merge_intervals`, `group_top_scores`, and `summarize_transactions`.
- `parse_key_value_lines` still required manual review after architect escalation, which is acceptable behavior because the harness refused an incorrect draft.

## Python-First Measurement Commands

Use these commands to measure the harness without expanding into multi-file/full-stack work yet:

```bash
make test-plan-mode-ladder
make test-worker-limit MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-worker-limit-auto SAVE_ARTIFACTS=1
make test-python-ladder-parsing MODEL=qwen2.5-coder:3b
make test-python-ladder-data MODEL=qwen2.5-coder:3b
make test-python-ladder-algorithmic MODEL=qwen2.5-coder:3b
make test-python-ladder-stateful MODEL=qwen2.5-coder:3b
make test-python-ladder-stateful-architect MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-raw-vs-harness MODEL=qwen2.5-coder:3b
make review-run RUN=<artifact-run-id-or-path>
```

## Added Engine Checks

Two newer Python engines expand structural feedback:

- `engine-6-bounds`: warning-first detection for high-confidence one-past-end reads/writes such as `items[len(items)]` and loops over `range(len(items) + 1)`.
- `engine-7-state-flow`: blocking-by-default diagnostic for helpers that assign to state-like parameters such as `section`, `state`, `current`, or `total` without returning the updated state.

The state-flow engine is intentionally narrow because it is meant to catch the observed parser-state failure class without pretending to be a full dataflow verifier.

## Live Prompt Test

A non-Snake terminal-game prompt was tested with a maze-runner request using local `qwen2.5-coder:1.5b`.

Prompt shape:

```text
Write a complete, single-file terminal maze runner game in Python using only the standard library curses module.
Include a game loop, player movement with arrow keys, wall collision, collectible dots, win/game-over condition, and screen drawing.
Return only runnable Python code with no explanation and no markdown fences.
```

Observed result:

- Initial generated code parsed successfully.
- The registered Python engine set ran.
- The model exceeded the structural policy with high cyclomatic complexity and, in the controller test, excessive loop depth.
- The controller generated a retry prompt containing targeted diagnostic instructions.
- The model returned an unchanged draft on the repair attempt, and the stagnation guard correctly stopped the loop with `manual_review_required`.

Interpretation:

The harness is correctly detecting, explaining, and refusing weak generations. The next improvement is model-side repair quality, not engine plumbing.
