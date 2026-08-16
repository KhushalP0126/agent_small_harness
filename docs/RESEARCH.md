# Research protocol

Agent Small Harness is an evaluation prototype for approval-gated code
generation. Its research question is deliberately narrower than “can an agent
write code?”:

> Do bounded repository tools and deterministic validation improve task
> completion enough to justify their token, latency, and operational cost?

The answer must be supported by recorded runs. A passing unit test shows the
mechanism works; it is not a model-quality claim.

## Current multi-language scope

Structured specifications accept Python, C, C++, Rust, and JavaScript. Rust
and JavaScript use the existing parse, structural, compilation, and
project-relative import checks; CrossHair counterexample repair remains a
Python-only experiment. Non-Python libraries are documentation-first: run
`make discover-library LIB=<name> DOC_ARGS='--language rust --doc-agent kernel'`,
review the recorded HTTPS sources, then explicitly approve with
`make approve-library LIB=<name> APPROVE_DOCUMENTATION=1`. Documentation is
evidence for a reviewed allow-list entry, never automatic trust.

The initial non-Python entries are intentionally small: Rust `serde_json`
serialization helpers and JavaScript Lodash collection/object helpers, each
with source URLs stored alongside the allow-list. They establish and test the
review workflow; future entries should come from observed generated drafts and
the same documented approval path, not from broad pre-population.

## Claims and non-claims

The repository can currently support these claims:

- It has a typed, approval-gated coding loop with deterministic validation and
  a reproducible fixed-task benchmark runner.
- The final 20-task DeepSeek loop-hardening run recorded 19/20 shielded task
  completions against 20/20 for the comparable baseline.
- The frozen 10-task Qwen 1.5B Compute Shield run recorded 9/10 shielded task
  completions against 10/10 direct completions.

It does **not** support these claims yet:

- that the tool loop reduces model-token use overall;
- that results generalize across models, repositories, or developers;
- that the system is secure for hostile multi-user or hosted workloads.

Published evidence belongs in [`results/`](results/). The two observed runs
above were more expensive than their direct baselines, so this project reports
them as diagnostic evidence rather than a token-saving result.

## Formal counterexample repair study (Python only)

The CrossHair repair path is intentionally scoped to Python contracts. It does
not establish a verification result for C, C++, Rust, or JavaScript. The
versioned corpus contains eight small, varied postcondition failures:
clamping, identity, non-negativity, absolute value, doubling, successor,
maximum, and an evenness predicate. The A/B runner starts each variant from
the same broken fixture. The guided prompt receives CrossHair's concrete
failing call and the baseline omits only that line. Both variants receive the
same code-only repair contract. Every failed outcome records the verifier's
concrete witness in the raw JSON and rendered report.

```bash
make research-formal-repair RESEARCH_RUNS=3 MODEL=qwen2.5-coder:1.5b
make research-report \
  REPORT_INPUT=docs/results/raw/formal-counterexample-repair-YYYY-MM-DD.json \
  REPORT_OUTPUT=docs/results/formal-counterexample-repair-YYYY-MM-DD.md
```

This command intentionally performs local model calls. It writes every run,
including failures, and must not be presented as an effectiveness result until
the resulting raw JSON and dated report are committed. It is evidence from a
small, Python-only corpus with varied contract shapes, not a general
multi-language verification claim.

The original eight-task corpus is retained unchanged so published reports
remain reproducible. A separately versioned diverse-corpus follow-up adds a
two-argument ordering property, a string-normalization property, and a
loop-based prefix-sum property. Run it independently rather than silently
changing the original benchmark:

```bash
make research-formal-repair-diverse RESEARCH_RUNS=3 MODEL=qwen2.5-coder:1.5b
```

### Published 8-task observation (2026-08-15)

The committed three-repeat study in
[`results/formal-counterexample-repair-2026-08-15.md`](results/formal-counterexample-repair-2026-08-15.md)
records a mean of 7.67/8 guided completions versus 8.00/8 for the baseline.
The guided variant used 6.73% more mean model tokens (1,978.00 versus
1,853.33). It was 1.13 seconds faster on average, but the corpus is too small
to treat that as a performance claim. This is a retained negative result: the
one guided failure is `formal-nonnegative`, and its CrossHair witness is
preserved in the raw JSON.

### Postcondition-semantics diagnosis

The retained guided failure was traced to a concrete candidate that raised
`ValueError` for the verifier's negative input. That candidate incorrectly
treated `post: _ >= 0` as an input restriction even though the source declared
no `pre:` condition. A separate three-repeat follow-up added an explicit rule
that postconditions constrain returned values and that the input must not be
rejected. It still produced the identical `ValueError` branch twice, reducing
guided completion to 7.33/8 while baseline remained 8.00/8. The small-model
failure is therefore task-pattern-specific rather than a missing prompt
clarification by itself.

The retained fix is intentionally narrower: for a formal `post: _ >= 0`
source, the small-worker prompt names the bad branch (`if value < 0: raise`)
and gives the valid transformation (`return max(value, 0)`). The separate
six-repeat paired diagnosis in
[`results/formal-nonnegative-directive-2026-08-15.md`](results/formal-nonnegative-directive-2026-08-15.md)
records 6/6 guided and 6/6 baseline completions; every guided candidate used
the intended clamp. This removes the observed regression but does not show a
guided success-rate advantage, and still costs 16 additional model tokens per
repair. It is a targeted prompt-engineering fix, not evidence that
counterexample-guided repair helps generally.

### Full-corpus narrow-directive follow-up

The narrow directive was then returned to the original eight-task corpus in
two independent, three-repeat paired batches. Both
[`batch A`](results/formal-counterexample-repair-nonnegative-directive-2026-08-15.md)
and
[`batch B`](results/formal-counterexample-repair-nonnegative-directive-followup-2026-08-15.md)
record 8.00/8 completions for baseline and guided variants in every
repetition. The guided `formal-nonnegative` candidate used `return
max(value, 0)` in all six runs, so the previously retained failure did not
recur at corpus scale. Batch A used 126.33 more guided tokens on average
(1,989.00 versus 1,862.67; 6.78% more); batch B used 121.67 more
(1,989.00 versus 1,867.33; 6.52% more). This replicates removal of the known
failure mode, but does not establish a general completion benefit or token
saving for counterexample-guided repair.

### Diverse-shape corpus follow-up

The eight numeric-property fixtures are not sufficient to generalize from, so
the separately versioned 11-task corpus adds a two-argument ordering property,
a string-normalization property, and a loop-based prefix-sum property. Its
three-repeat result is published in
[`results/formal-counterexample-repair-diverse-2026-08-15.md`](results/formal-counterexample-repair-diverse-2026-08-15.md).
Baseline completed 10.00/11 tasks and guided repair completed 9.33/11; guided
repair used 186.33 more mean model tokens (2,817.00 versus 2,630.67; 7.08%
more). The loop fixture passed in both variants. The retained failures are
task-specific: baseline and guided both mishandled the ordering repair in some
runs, while guided consistently used `lstrip()` rather than `strip()` for the
string contract. This is evidence against treating the successful
`nonnegative` directive as a general formal-repair improvement. The raw JSON
retains every candidate, prompt, and CrossHair witness for follow-up analysis.

### General-directive control

To test whether a single verifier-aligned rule beats task-specific guidance,
the same 11-task corpus was run three times with the narrow guided prompt as
the control and one general directive as the comparison: accept the verifier's
counterexample unless a source `pre:` condition excludes it, and satisfy the
postcondition rather than reject the input. The result in
[`results/formal-counterexample-repair-general-directive-2026-08-16.md`](results/formal-counterexample-repair-general-directive-2026-08-16.md)
is unambiguously negative for this small model: narrow guidance averaged
9.00/11 completions; the general directive averaged 6.67/11, used 455.33 more
mean tokens (16.10% more), and was 7.39 seconds slower. The general wording
also reintroduced the known negative-input rejection in `nonnegative` and
produced rejection branches for evenness, doubling, ordering, and the loop
fixture. The retained evidence therefore supports a narrower conclusion:
prompt guidance is task-pattern-sensitive for Qwen 1.5B; a generic
"respect the witness" instruction does not replace focused repair guidance.

Future raw formal benchmark artifacts retain both the exact repair prompt and
candidate source, so task-level outcomes can be audited without reconstruction.
The unsuccessful clarification follow-up is preserved in
[`results/formal-counterexample-repair-postcondition-semantics-2026-08-15.md`](results/formal-counterexample-repair-postcondition-semantics-2026-08-15.md).

## Cost-aware route evidence

API calls can report an estimated dollar cost; local Ollama calls are instead
marked `local_unpriced`. They are **not** recorded as measured `$0.00` calls.
When routes tie on success and their pricing differs in availability, the
routing policy compares observed tokens before any available dollar subtotal.
Use the report below to determine whether real history has enough multi-route
data to support a routing claim:

```bash
make routing-report
```

## Reproduce a deterministic checkout

```bash
make setup
make research-check
```

`make research-check` runs Python and Rust tests without calling a model. It
verifies the protocol, approval workflow, tool handling, result accounting,
and TUI state transitions.

## Pre-registered repeated benchmark

Use the same task corpus, commands, and output location for every repetition.
Do not discard failed tasks or rerun only favorable cases.

```bash
make research-agent-benchmark \
  RESEARCH_RUNS=3 \
  BASELINE_CMD="python3 scripts/run_deepseek_benchmark_agent.py --mode baseline" \
  SHIELDED_CMD="python3 scripts/run_deepseek_benchmark_agent.py --mode shielded" \
  RESEARCH_OUTPUT=artifacts/research/deepseek-repeated.json
```

The report preserves every task-level run and adds descriptive mean, standard
deviation, range, and a normal-approximation interval. The interval is a
variance summary, not a statistical-significance claim. Publish the raw JSON
artifact alongside a dated Markdown result report.

Every repeated report records a secret-free provenance manifest: UTC timestamp,
commit and dirty state, OS, Python version, task-corpus SHA-256, configured
model/provider, context window, thinking settings, token counts, latency,
retries, and tool calls. Render a report from committed raw JSON with:

```bash
make research-report \
  REPORT_INPUT=docs/results/raw/deepseek-repeated.json \
  REPORT_OUTPUT=docs/results/deepseek-repeated-YYYY-MM-DD.md
```

For the frozen local-model experiment, run the existing fixed corpus rather
than substituting a larger model:

```bash
make research-compute-shield \
  COMPUTE_SHIELD_ARGS="--output artifacts/research/compute-shield-10-rerun.json"
```

Run local-model experiments only on hardware that can sustain the selected
model. The 2026-08-11 Qwen 1.5B/3B comparison is published, but it is a
single fixed-corpus observation rather than a general 3B performance claim.

## Local model-size comparison

The frozen ten-task Compute Shield corpus can compare the installed local
`qwen2.5-coder:1.5b` and `qwen2.5-coder:3b` models. The comparison renderer
shows completion, token, tool-call, and wall-clock deltas, but explicitly
labels the result as a two-model observation rather than a parameter-scaling
law:

```bash
make research-model-comparison
```

## Independent fixture corpus

The second corpus is a compact, versioned repository under
`tests/fixtures/research_target_repo/`, with fixed tasks in
`data/research_fixture_tasks.json`. Run the same protocol independently for
the API and local model:

```bash
make research-fixture-deepseek
make research-fixture-qwen
```

These commands write raw JSON under `docs/results/raw/`. Render and commit a
dated Markdown report for each run; retain every failure and raw task record.

## Live end-to-end evaluation

Before demoing a new release, record one real session for each task shape:

1. A plain question: no plan, no file mutation.
2. A small coding request: one proposed diff, explicit approval, validation.
3. A multi-file coding request: each diff appears sequentially; accept one and
   reject one to prove review is real.
4. A planning request: questionnaire, spec review, explicit execution approval.
5. An unavailable-API case: a visible error with no accidental fallback write.

Record the prompt, model/provider, exact commit, artifact path, tool calls,
validation result, latency, and whether a human approved a change. Do not turn
an anecdotal successful session into a benchmark result.

Use `make record-live-session SESSION_ARGS='...'` to write secret-free,
machine-readable receipts. The five receipts are: `plain_question`,
`small_edit`, `multi_file_edit` (one approved and one rejected proposal),
`planning_review`, and `unavailable_api`. Store only prompt summaries and diff
hashes—not keys, raw private prompts, or absolute local paths.

## Publication checklist

- Commit hash, operating system, Python version, model identifier, and
  relevant context/thinking configuration are recorded.
- Baseline and shielded runners use the same tasks and repository index.
- Raw JSON is retained, including failures, token accounting, retries, and
  wall-clock durations.
- A result report states both the improvement and the cost; negative results
  remain published.
- Any new model is reported as a separate row rather than replacing earlier
  measurements.

## Remaining research work

1. Run at least three repeated 20-task DeepSeek comparisons on the current
   revision and render the committed raw JSON.
2. Rerun the frozen Qwen 1.5B corpus, then run both Qwen 1.5B and DeepSeek on
   the independent fixture corpus.
3. Record the five controlled approval-reviewed session receipts.
4. Expand the Python/CrossHair study beyond single-function contracts with a
   repository or multi-function corpus; retain its Python-only scope. The
   single-function general-directive control is complete and negative.
5. Accumulate further real sessions only after the controlled receipt set is
   complete; do not elevate anecdotal success into a benchmark claim.
