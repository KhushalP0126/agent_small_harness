# Additional Harness Results — 2026-07-24

This pass intentionally avoided rerunning commands already covered by existing
reports, artifacts, or the current session.

Baseline: `main` at `f15dba8`

Worker model: Ollama `qwen2.5-coder:1.5b`

## Newly Executed Checks

### Raw model versus harness

Command:

```sh
make test-raw-vs-harness MODEL=qwen2.5-coder:1.5b
```

| Difficulty | Task | Raw behavior | Harness result | Static failures | Behavior failures | Attempts |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 1 | `sum_even_numbers` | Pass | Completed | 0 | 0 | 1 |
| 2 | `dedupe_by_key` | Pass | Completed | 0 | 0 | 1 |
| 3 | `parse_int_list` | Pass | Completed | 0 | 0 | 1 |
| 4 | `compact_ranges` | Fail | Manual review | 0 | 2 | 1 |
| 5 | `merge_inventory_events` | Pass | Completed | 0 | 0 | 1 |
| 6 | `parse_sectioned_config` | Fail | Manual review | 1 | 1 | 1 |
| 7 | `resolve_dependency_order` | Fail | Manual review | 1 | 2 | 1 |

Raw behavior pass rate: **4/7**

Harness completion rate: **4/7**

The harness did not increase the pass rate in this one-attempt comparison.
Its demonstrated benefit was rejection accuracy: all three incorrect raw
generations were stopped at `manual_review_required` with concrete static or
behavior evidence instead of being reported as completed. Because every row
used one attempt, this command did not exercise repair or architect recovery.

### Raw model versus repair and architect recovery

The follow-up added a separate comparison target so the baseline command keeps
its original behavior:

```sh
make test-raw-vs-harness-architect MODEL=qwen2.5-coder:1.5b
```

| Difficulty | Task | Raw behavior | Harness result | Attempts | Architect calls |
| ---: | --- | --- | --- | ---: | ---: |
| 1 | `sum_even_numbers` | Pass | Completed | 1 | 0 |
| 2 | `dedupe_by_key` | Pass | Completed | 1 | 0 |
| 3 | `parse_int_list` | Fail | Completed | 3 | 1 |
| 4 | `compact_ranges` | Fail | Completed | 2 | 1 |
| 5 | `merge_inventory_events` | Pass | Completed | 1 | 0 |
| 6 | `parse_sectioned_config` | Fail | Completed | 3 | 1 |
| 7 | `resolve_dependency_order` | Fail | Manual review | 2 | 1 |

Raw behavior pass rate: **3/7**

Repair-enabled harness completion rate: **6/7**

The harness recovered three incorrect raw drafts: `parse_int_list`,
`compact_ranges`, and `parse_sectioned_config`. Each recovery used one
architect call after bounded local repair. `resolve_dependency_order` remained
blocked with one static and two behavior failures, so the run preserved a real
negative result rather than inflating the completion score.

This is a separate stochastic sample from the one-attempt comparison above.
The raw pass-rate change from 4/7 to 3/7 is model sampling variance; the useful
within-run comparison is 3/7 raw versus 6/7 after the harness.

### Five repeated paired samples with retained evidence

Command:

```sh
make test-raw-vs-harness-repeated \
  MODEL=qwen2.5-coder:1.5b \
  RAW_VS_HARNESS_SAMPLES=5
```

Artifact batch:

```text
artifacts/runs/raw_vs_harness-20260724T183433Z-56d2008a
```

| Sample | Raw passes | Harness passes | Recovered raw failures |
| ---: | ---: | ---: | ---: |
| 1 | 3/7 | 7/7 | 4 |
| 2 | 3/7 | 6/7 | 3 |
| 3 | 4/7 | 6/7 | 2 |
| 4 | 3/7 | 7/7 | 4 |
| 5 | 4/7 | 7/7 | 3 |
| **Total** | **17/35** | **33/35** | **16** |

Raw completion was **48.6%** and harness completion was **94.3%**, a
**45.7 percentage-point lift** within these paired samples. Per-sample raw
completion ranged from 3–4/7; harness completion ranged from 6–7/7. There were
18 architect calls across 35 pairs.

Each pair retains `raw_draft.py`, `raw_behavior.json`, the harness attempt
sources/prompts/validation, and an attempt timeline. The batch-level
`raw_vs_harness_summary.json` contains all rows and aggregate ranges.

This is stronger evidence than a single run, but it remains one model and seven
tasks. It should not be generalized into a universal harness-effect estimate.
The completion label is a validation recommendation for human review, not an
autonomous decision to merge or deploy generated code.

### Structured-spec resume and Pong import repair

Structured-spec runs now create their artifact directory before contract
generation, checkpoint after every terminal contract result, and expose:

```sh
make resume-structured-spec \
  SPEC_PATH=<original-spec> \
  RESUME_RUN=<artifact-run-id>
```

The contract-queue regression test simulates an interruption after the first
accepted contract and verifies that resumption generates only the remaining
contract.

Wildcard imports are now blocking lint findings with generalized repair
guidance. The post-fix Pong run no longer contained the earlier wildcard import
or undefined pygame constants:

```text
artifacts/runs/structured_spec_pong_game_spec-20260724T184357Z-29e2d625
```

Pong still ended at manual review for different issues: `handle_input`
cyclomatic complexity was 8 against a limit of 7, and the integrated module
omitted required `check_wall_collision` and `check_paddle_collision` wrappers.
The constants failure is closed; Pong as a whole is not yet a clean pass.

### Stateful ladder with architect recovery

Command:

```sh
make test-python-ladder-stateful-architect \
  MODEL=qwen2.5-coder:1.5b \
  SAVE_ARTIFACTS=1
```

| Difficulty | Task | Result | Attempts | Small-worker failures | Architect calls | Contribution |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | `process_events` | Completed | 2 | 1 | 1 | Architect solved after small-worker stall |
| 2 | `parse_sectioned_config_stateful` | Completed | 2 | 1 | 1 | Architect solved after small-worker stall |
| 3 | `sessionize_events` | Completed | 1 | 0 | 0 | Small worker solved the initial draft |

Completion rate: **3/3**

No breaking point was found. Architect recovery materially changed the result:
two tasks that stalled under Qwen were completed by one architect call each.
The third task required no escalation.

Artifacts:

```text
artifacts/runs/worker_limit_1_process_events-20260724T053047Z-c2bc7db3
artifacts/runs/worker_limit_2_parse_sectioned_config_stateful-20260724T053101Z-60ac8b9e
artifacts/runs/worker_limit_3_sessionize_events-20260724T053122Z-a5461a7f
```

### Formal verification

Dependency check:

```text
deal: installed
crosshair: installed
```

Command:

```sh
make test-formal-experiment
```

Result:

```text
CrossHair compliant: True
```

The formal smoke path executed rather than being skipped and accepted its
contract-bearing sample.

## Deliberately Not Rerun

| Screenshot item | Reason skipped |
| --- | --- |
| Parsing, data, algorithmic, and stateful ladders | Already run and documented in `docs/qwen-capability-results-2026-07-18.md` |
| Snake structured-spec run | Already documented in `docs/structured-spec-repo-map-results-2026-07-24.md` |
| Repeating Pong three times | Would repeat the same command; excluded by the no-rerun instruction |
| Full `make test` | The complete 310-test suite already passed before this pass |
| `make test-adversarial` and `make test-engine-edge-cases` | Their tests were already included in the completed full suite |
| Repo-map checks | All formats, relative imports, and generated-output mapping were run in the current session |
| New CLI or ambiguous spec sheets | No such spec files exist yet; creating them is new test-fixture work rather than executing an existing unrun command |

## Follow-Up Signal

The repeated paired comparison now demonstrates both sides of the central
claim: the harness blocks bad drafts and converted 16 raw failures into passing
programs. The next stronger experiment is the same retained-evidence protocol
across more models and tasks, with confidence intervals.
