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
