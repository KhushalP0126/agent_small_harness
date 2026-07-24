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
| Snake and Pong structured-spec runs | Run in the current session and documented in `docs/structured-spec-repo-map-results-2026-07-24.md` |
| Repeating Pong three times | Would repeat the same command; excluded by the no-rerun instruction |
| Hard-killing a structured-spec run | Repeats the Pong command, and structured-spec currently has no `--resume-run` read path |
| Full `make test` | The complete 310-test suite already passed before this pass |
| `make test-adversarial` and `make test-engine-edge-cases` | Their tests were already included in the completed full suite |
| Repo-map checks | All formats, relative imports, and generated-output mapping were run in the current session |
| New CLI or ambiguous spec sheets | No such spec files exist yet; creating them is new test-fixture work rather than executing an existing unrun command |

## Follow-Up Signal

The repair-enabled comparison now demonstrates both sides of the central
claim: the harness blocks bad drafts and can convert some of them into correct
programs. The next stronger experiment is repeated paired samples with saved
raw drafts, fixed retry budgets, and confidence intervals so stochastic model
variation is separated from harness effect.
