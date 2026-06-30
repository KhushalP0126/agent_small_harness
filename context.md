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

## Current Session Notes

The harness direction is generalized code creation and repair, not a Snake-game-specific generator. Snake-style tasks are useful smoke tests because they exercise loops, branching, imports, and state, but they are not the target architecture.

Current baseline:

- Python is the primary reliable path.
- Every valid Python draft must pass through all three engines on every attempt:
  - `engine-1-math`
  - `engine-2-hazards`
  - `engine-3-branching`
- Repaired drafts must be rescanned; no repair path should bypass loop analysis, hazard analysis, or branching analysis.
- The engine evaluator is at `overall_recall: 1.0`.
- The full unit suite last passed with `76` tests.

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
- All three Python engines ran.
- The model exceeded the structural policy with high cyclomatic complexity and, in the controller test, excessive loop depth.
- The controller generated a retry prompt containing targeted diagnostic instructions.
- The model returned an unchanged draft on the repair attempt, and the stagnation guard correctly stopped the loop with `manual_review_required`.

Interpretation:

The harness is correctly detecting, explaining, and refusing weak generations. The next improvement is model-side repair quality, not engine plumbing.
