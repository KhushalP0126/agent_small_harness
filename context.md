# Harness Prompt Context

This repository implements a constrained, Python-first repair harness for
small-model code generation. The model should receive explicit structural rules,
behavioral examples, and grounded feedback from failed attempts instead of being
asked to infer constraints from raw code alone.

## Current Direction

The product direction is generalized code creation and repair. Domain examples
are useful as external stress tests, but they must not create permanent
task-specific logic inside the controller, Plan Mode, engines, or retry builder.

Current baseline:

- Python is the primary reliable path.
- Every valid Python draft must pass through the full registered Python engine set:
  - `engine-1-math`
  - `engine-2-hazards`
  - `engine-3-branching`
  - `engine-4-cost`
  - `engine-6-bounds`
  - `engine-7-state-flow`
  - `engine-5-lint` when Pylint is available.
- Repaired drafts and architect drafts must be rescanned by the same gates.
- Behavior validation is the semantic ground truth when examples are available.
- Optional CrossHair validation is available for contract/counterexample checks.
- The architect model is a second worker, not an authority. Its output is accepted
  only after normal validation.
- Dedicated app-fixture routing was removed from the source surface. Template
  routing remains available only through injected/configured routes.
- Library discovery is now part of the operator surface. It can inspect an
  importable Python package, ask DeepSeek or local Qwen for documentation
  candidates, and write reviewable proposal/docs files before any API surface is
  approved into the trusted registry.

## Environment And Model Backends

Local secrets belong in `.env`, which is ignored by git. Use `.env.example` as
the template and keep one of these keys configured:

```env
DEEPSEEK_API_KEY=your_key_here
# or
ARCHITECT_API_KEY=your_key_here
```

`make env-path` prints the active env-file path and supported key names. The
DeepSeek-backed architect client reads `.env` directly, so shell commands can use
the API without exporting the key globally.

Default backend roles:

- local worker: Ollama/Qwen via `MODEL`, defaulting to `qwen2.5-coder:1.5b`
- architect and documentation search: DeepSeek via `DEEPSEEK_API_KEY`
- optional documentation override: `DOC_AGENT=qwen` for local Qwen or
  `DOC_AGENT=none` for import-only discovery

## Library Discovery Workflow

The Makefile now connects discovery to DeepSeek by default:

```bash
make discover-library LIB=clang.cindex
```

This writes:

- `data/library_proposals/<LIB>.json` with discovered public symbols,
  environment metadata, model-search metadata, and documentation candidates
- `data/library_proposals/<LIB>.docs.md` with model-generated syntax and usage
  notes

Use these variants when needed:

```bash
make discover-library LIB=json DOC_AGENT=qwen
make discover-library LIB=json DOC_AGENT=none
make approve-library LIB=json
```

Discovery proposals are candidates, not trusted APIs. Review the proposal before
approving it into `data/library_registry.json`.

## Autonomous Repair Prompt

Use this general repair posture for Python tasks:

```text
You are an autonomous repair agent.

Target:
Refactor the provided code to satisfy the stated behavior and the harness gates.

Static requirements:
- avoid global or module-state mutation
- keep loop nesting small
- keep cyclomatic complexity within policy
- avoid avoidable O(N^2) patterns
- avoid unsafe calls, file I/O, network calls, eval, exec, and unapproved imports

Behavior requirements:
- preserve exact input/output behavior from the examples
- handle malformed, empty, and edge-case input explicitly
- do not return trivial or hardcoded values

Output:
- return only complete Python code
- no markdown fences
- no demo code
- no print-only examples
```

## Compact Worker Packet

Plan Mode has two renderings:

- full plan context for humans and architect workers
- compact worker packet for small local models

The small worker should receive concise packets:

```text
PLAN PACKET:
FUNCTION: parse_sectioned_config
LANGUAGE: python
EXAMPLES:
- parse_sectioned_config("[main]\na=1") == {"main": {"a": "1"}}
STATE RULES:
- track active parser state explicitly
- process records only after required state exists
- preserve overwrite semantics
ADAPTER RULES:
- treat external libraries as opaque dependencies behind local helpers
FINAL RULES:
- Return only complete Python code.
```

This is intentionally lower-noise than full telemetry.

## Feedback Injection Prompt

Prior failures are binding context. Retry prompts should include the smallest
useful failure frame:

```text
PRIOR FAILED ATTEMPTS:
- Attempt N:
  Static failure: <kind> had <current>; required <allowed>.
  Behavior failure: <case> expected <expected> but got <actual>.
Do not repeat any prior failed pattern.
```

For small workers, prefer one primary violation at a time. For architect workers,
include the full finding set plus deltas and preserved graph/state context.

## Plan-Layer Contracts

Plan Mode or the architect may emit Deal contract candidates from extracted
examples. These contracts serve two jobs:

- narrow the small worker's function-level implementation target
- provide executable `@deal.example` checks for the controller

```python
import deal

@deal.example(lambda: parse_int_list("1, -2, +3") == [1, -2, 3])
@deal.example(lambda: parse_int_list("a, 4, -, 5") == [4, 5])
def parse_int_list(text: str) -> list[int]:
    ...
```

The small worker should implement against one function contract at a time. It
should not be expected to invent reliable formal contracts.

The controller runs explicit Deal examples when generated code contains Deal
decorators. A failed `@deal.example` becomes a formal validation failure and
routes to repair or manual review like any other blocking gate.

## Optional Formal Validation

Formal tooling is tiered:

- Deal belongs in the Plan/architect layer as function contract scaffolding and
  in the controller as an executable example gate.
- CrossHair belongs beside behavior validation as an optional semantic validator.
- Nagini belongs at the architect tier for proof-friendly rewrites of critical helpers.

Missing optional verifier dependencies must not break normal tests.

## Kernel / TaskIR Boundary

The shared execution core is represented by:

- `kernel/task_ir.py`
- `kernel/execution_kernel.py`
- `agents/generation_controller.py`

Design rule:

```text
Plan Mode decides structure.
Routing policy chooses the path.
The execution kernel runs generation and validation.
Engines and behavior validators decide acceptance.
```

The kernel should receive structured intent:

```yaml
target:
  function_or_module: name
behavior:
  examples: [...]
state:
  variables: [...]
  transitions: [...]
libraries:
  allowed: [...]
validation:
  engines: [...]
  behavior_examples: [...]
```

## Engine Diagnostic Contract

Engine findings should expose stable diagnostic metadata:

```json
{
  "violation": "CYCLOMATIC_COMPLEXITY_EXCEEDED",
  "threshold": "<= 7",
  "actual": "13",
  "location": "5 conditional branches",
  "recommended_refactor": "Extract branch-heavy decisions into small helper functions."
}
```

The repair strategy should consume `recommended_refactor` when present.

## Known Research Boundaries

- Small local workers are useful for straightforward tasks and some repairs.
- Stateful parser/event tasks expose the reasoning limit of 1.5B and 3B models.
- Low-noise retry packets and decomposition can improve contribution, but do not
  guarantee completion on hard semantic tasks.
- Architect escalation helps when it produces meaningful structural changes, but
  it must still pass the same gates.
- Some complexity failures can be metric-scope ambiguity rather than bad code;
  those should route to manual review instead of burning retries.

## Measurement Commands

Use these commands to measure without changing the harness shape:

```bash
make test
make evaluate-engines
make test-plan-mode-ladder
make test-worker-limit MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-worker-limit-auto SAVE_ARTIFACTS=1
make test-worker-limit-architect MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-python-ladder-parsing MODEL=qwen2.5-coder:3b
make test-python-ladder-data MODEL=qwen2.5-coder:3b
make test-python-ladder-algorithmic MODEL=qwen2.5-coder:3b
make test-python-ladder-stateful MODEL=qwen2.5-coder:3b
make test-python-ladder-stateful-architect MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-raw-vs-harness MODEL=qwen2.5-coder:3b
make discover-library LIB=clang.cindex
make review-run RUN=<artifact-run-id-or-path>
```

## Session Cleanup Note

The source surface is kept to runnable harness code, durable docs, fixtures, and
reviewable data. One-off model review notes and local runtime clutter should not
live in the repository. Generated caches, local env files, coverage output,
build products, logs, and artifacts are ignored.
