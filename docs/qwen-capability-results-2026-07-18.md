# Qwen coding-capability results — 2026-07-18

## Setup

- Worker backend: local Ollama at `http://127.0.0.1:11434`
- Worker model: `qwen2.5-coder:1.5b`
- Architect repair model: configured `deepseek-chat` API backend
- Raw coding-capability records: `data/runs.jsonl`, lines 49–76 in this commit

The first two invocations were made without permission to reach the host's
localhost interface. Those 14 records are retained as environment diagnostics,
but they are excluded from the model scores below. After local Ollama access was
enabled, `ollama list` confirmed that `qwen2.5-coder:1.5b` was installed and the
tests produced real generations.

## Commands run

```text
make test-coding-capability
make test-coding-capability-architect
make test-worker-limit
make test-python-ladder-parsing
make test-python-ladder-data
make test-python-ladder-algorithmic
make test-python-ladder-stateful
```

## Coding-capability summary

| Mode | Completed | Manual review | Score |
| --- | ---: | ---: | ---: |
| Qwen small worker only | 3 | 4 | 3/7 |
| Qwen plus architect escalation | 6 | 1 | 6/7 |

The architect path recovered three of the four tasks that the small-worker-only
run could not complete. `group_top_scores` remained unresolved after the maximum
two repair attempts.

## Task and retry detail

| Task | Qwen-only result | Qwen-only retries | Architect-mode result | Architect-mode retries | Recovery |
| --- | --- | ---: | --- | ---: | --- |
| `matrix_scoring` | completed | 0 | completed | 0 | Qwen solved initially |
| `dedupe_preserve_order` | completed | 0 | completed | 0 | Qwen solved initially |
| `clamp_values` | completed | 0 | completed | 0 | Qwen solved initially |
| `merge_intervals` | manual review | 0 | completed | 1 | Architect solved after Qwen stalled |
| `parse_key_value_lines` | manual review | 1 | completed | 2 | Qwen made progress; architect completed |
| `group_top_scores` | manual review | 1 | manual review | 2 | Maximum retries exhausted |
| `summarize_transactions` | manual review | 0 | completed | 1 | Architect solved after Qwen stalled |

The retry count is the `repair_attempts` value stored in `data/runs.jsonl`.
Initial generation is not counted as a retry. In architect mode, a retry may be
a second Qwen attempt, an architect attempt, or a Qwen-to-architect escalation.

### Qwen-only failures

- `merge_intervals`: one behavior failure; the repair loop stopped as stagnant.
- `parse_key_value_lines`: three behavior failures plus cyclomatic complexity;
  Qwen changed the draft once but did not finish.
- `group_top_scores`: four behavior failures plus cyclomatic complexity; Qwen
  changed the draft once but did not finish.
- `summarize_transactions`: one behavior failure plus cyclomatic complexity;
  the repair loop stopped as stagnant.

### Architect-mode recovery

- `merge_intervals`: completed after one architect repair.
- `parse_key_value_lines`: completed after one Qwen retry and one architect
  retry.
- `summarize_transactions`: completed after one architect repair.
- `group_top_scores`: still failed the tie-order behavior check after one Qwen
  retry and one architect retry; cyclomatic complexity also appeared in the run
  history.

## Worker-limit and Python ladder results

These commands print their tables to the terminal rather than appending them to
`data/runs.jsonl`. Their final tables are summarized here.

| Suite | Tasks completed before break | Breaking task | Difficulty |
| --- | ---: | --- | ---: |
| General worker limit | 3/4 | `compact_ranges` | 4 |
| Parsing ladder | 1/2 | `parse_key_value_lines` | 2 |
| Data-transform ladder | 2/3 | `summarize_transactions` | 3 |
| Algorithmic ladder | 1/2 | `merge_intervals` | 2 |
| Stateful ladder | 1/2 | `parse_sectioned_config_stateful` | 2 |

The reliable boundary is task-dependent, but Qwen usually begins failing at
difficulty 2 or 3. The general ladder reached difficulty 4. `compact_ranges`
failed there but passed when generated again as the first algorithmic-ladder
task, demonstrating sampling variance rather than a perfectly fixed boundary.

## Environment-diagnostic attempts

Before host-local access was enabled, the same two coding-capability commands
added 14 diagnostic records:

- Seven Qwen-only records ended with
  `small_worker_initial_backend_unreachable`.
- Seven architect-mode records ended with
  `architect_after_backend_failure_failed` because the initial Ollama call was
  blocked and the fallback could not produce a valid recovery.

These attempts scored 0/7 in each mode, but they measure sandbox connectivity,
not Qwen capability, and must not be mixed into the 3/7 and 6/7 model results.

## Conclusion

The end-to-end harness is working against the intended local Qwen model. The
small worker solved the three simplest coding-capability tasks without repair.
Architect escalation materially improved completion from 3/7 to 6/7, rescuing
three failures. The most persistent weakness was grouped ranking with tie-order
requirements, and the ladders show additional brittleness in parsing,
interval-merging, stateful parsing, and transaction aggregation.

## Post-fix focused verification

After merging simultaneous static and behavioral failures into retry prompts
and enabling artifacts by default, `group_top_scores` was rerun alone against
Ollama `qwen2.5-coder:1.5b` with architect escalation available.

| Result | Attempts | Qwen repairs | Architect calls | Final failures |
| --- | ---: | ---: | ---: | ---: |
| completed | 1 | 0 | 0 | 0 |

Qwen solved the focused rerun in its initial draft, so no retry was required.
The run was recorded in `data/runs.jsonl` with artifact path
`artifacts/runs/group_top_scores-20260719T015612Z-2830cb30`. Deterministic
regression coverage separately verifies that when static and behavioral gates
fail together, both categories and their exact expected/actual values reach the
small-worker retry prompt in stable, deduplicated order.

## Prompt-routing leakage fix

A later artifact-backed sweep exposed unrelated section-parser state-machine
instructions in an architect repair prompt for `summarize_transactions`. The
specialized prompt had been selected whenever examples were preserved and a
broad failure kind such as behavioral mismatch or cyclomatic complexity was
present.

The route now additionally requires explicit section-parser state context:
state rules naming active-section state plus key/value or nested-dictionary
flow. Ordinary aggregation tasks use the generic architect repair prompt.

A focused live rerun exercised the corrected retry path:

| Result | Attempts | Initial static failures | Initial behavior failures | Architect repairs |
| --- | ---: | ---: | ---: | ---: |
| completed | 3 | 1 | 1 | 2 |

The saved prompts included cyclomatic complexity and the exact `zero retained`
failure (`{}` versus `{'a': 0}`), while excluding state-machine mode,
`active_section`, section-header, and nested-dictionary instructions. The trace
is stored at
`artifacts/runs/summarize_transactions-20260720T003952Z-9f113d9f`.

## Post-fix broader stress evaluation

The maintained worker-limit suite and all four Python ladders were rerun with
`qwen2.5-coder:1.5b`, artifact capture, and continuation after failure. This
evaluated all 19 cases rather than stopping at each suite's first failure.

| Suite | Completed | Total | Earliest failure |
| --- | ---: | ---: | --- |
| General worker limit | 4 | 7 | difficulty 4, `compact_ranges` |
| Parsing | 1 | 3 | difficulty 1, `parse_int_list` |
| Data transform | 2 | 3 | difficulty 3, `summarize_transactions` |
| Algorithmic | 0 | 3 | difficulty 1, `compact_ranges` |
| Stateful | 2 | 3 | difficulty 2, `parse_sectioned_config_stateful` |
| **Total** | **9** | **19** | — |

The previous earliest breaks were difficulty 4, 2, 3, 2, and 2 respectively.
Parsing and algorithmic results moved earlier in this sample, while the other
three were unchanged. Individual tasks also moved in both directions—for
example, parsing difficulty 2 passed after failing previously. This confirms
that a single 1.5B-model sample is too variable to treat a ladder breakpoint as
a fixed capability boundary.

The seven general worker-limit tasks were then rerun with architect escalation:

| Completed | Qwen initial solves | Architect recoveries | Remaining failures |
| ---: | ---: | ---: | ---: |
| 7/7 | 3 | 4 | 0 |

Artifact inspection confirmed clean routing across the broader suite. Generic
tasks (`parse_int_list`, `compact_ranges`, and `resolve_dependency_order`) used
generic architect prompts. Only the actual sectioned-config parser used
`STATE MACHINE ARCHITECT MODE`.

Continuation mode also exposed a reporting defect: both ladder runners replaced
the stored first failure on every later failure and therefore printed the last
break. They now retain the earliest failed row, with regression coverage for
both worker-limit and plan-mode ladders.
