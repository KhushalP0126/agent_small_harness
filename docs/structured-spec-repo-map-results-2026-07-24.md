# Structured-Spec and Repo-Map Results — 2026-07-24

Baseline: `main` at `38a1f22`

Worker model: Ollama `qwen2.5-coder:1.5b`

## Summary

| Check | Result | Evidence |
| --- | --- | --- |
| Snake plan-only | Pass | Architect plan parsed and applied; 16-contract DAG; no fallback |
| Pong plan-only | Pass | Architect plan parsed and applied; 20-contract DAG; no fallback |
| Snake full run | Pass | 16/16 contracts accepted; static, structured-spec, smoke, and formal checks compliant |
| Pong full run | Manual review | 20/20 contracts accepted, but final static lint found six undefined pygame constants |
| Contract failure isolation | Pass | Pong continued through `main` after `render` required retries and architect escalation |
| Snake artifact review | Pass | Completed in two integration attempts |
| Pong artifact review | Pass | Correctly reports `architect_static_gate_failed` and six blocking lint errors |
| Structured-spec resume CLI | Gap | `scripts/run_structured_spec.py` does not expose `--resume-run` |
| Repo-map context | Pass | 105 Python files; 125 output lines |
| Repo-map Mermaid | Pass | 29,200 output lines |
| Repo-map JSON | Pass | 105 files, 10,995 nodes, 18,203 edges |
| Relative-import mapping | Pass | `module:backends` imports `module:backends.ollama_client` as a local edge |
| Generated-output mapping | Pass | Snake artifact map found both `attempt_0.py` and `attempt_1.py` |

## Commands

```sh
make structured-spec-plan SPEC_PATH=examples/specs/snake_game_spec.md
make structured-spec-plan SPEC_PATH=examples/specs/pong_game_spec.md
make structured-spec SPEC_PATH=examples/specs/snake_game_spec.md
make structured-spec SPEC_PATH=examples/specs/pong_game_spec.md
make review-run RUN=structured_spec_snake_game_spec-20260724T044659Z-f11ac56b
make review-run RUN=structured_spec_pong_game_spec-20260724T045455Z-cd3181e7
make repo-map REPO_ROOT=. REPO_MAP_FORMAT=context
make repo-map REPO_ROOT=. REPO_MAP_FORMAT=mermaid
make repo-map REPO_ROOT=. REPO_MAP_FORMAT=json
```

The first sandboxed Snake plan attempt could not resolve the architect API
hostname and used the deterministic fallback. The reported plan results above
come from the immediate network-enabled rerun, where the architect response
parsed and applied successfully.

## Snake

Plan-only artifact:

```text
artifacts/runs/structured_spec_plan_snake_game_spec-20260724T044005Z-ad42f3da
```

Full-run artifact:

```text
artifacts/runs/structured_spec_snake_game_spec-20260724T044659Z-f11ac56b
```

All 16 contracts were accepted. `opposite_direction` used two small-worker
repairs and one architect escalation before acceptance. The integrated result
then passed:

- static validation
- structured-spec validation
- the headless integration smoke window
- formal validation

The smoke result was `running_after_smoke_window`, which is compliant for the
game loop.

## Pong

Plan-only artifact:

```text
artifacts/runs/structured_spec_plan_pong_game_spec-20260724T044154Z-237bfc83
```

Full-run artifact:

```text
artifacts/runs/structured_spec_pong_game_spec-20260724T045455Z-cd3181e7
```

All 20 contracts were attempted and accepted. `render` and `main` each used two
small-worker repairs followed by architect escalation. The queue continued to
`main` after the earlier `render` difficulty, confirming that the queue did not
silently short-circuit.

The final integrated result correctly stopped at `manual_review_required`.
Structured-spec, integration-smoke, and formal validation passed, but Pylint
reported six undefined pygame constants:

- `KEYDOWN`
- `K_w`
- `K_s`
- `K_UP`
- `K_DOWN`
- `QUIT`

The review tool classified the result as `architect_static_gate_failed`. This
is a generated-code bug, not a false successful run: the blocking static gate
caught it.

## Repo Mapper

Mapping this repository produced:

| Format | Result |
| --- | ---: |
| Context | 125 lines, 11,397 bytes |
| Mermaid | 29,200 lines, 1,226,642 bytes |
| JSON files | 105 |
| JSON nodes | 10,995 |
| JSON edges | 18,203 |

The relative import in `backends/__init__.py` is present in JSON as:

```json
{
  "source": "module:backends",
  "target": "module:backends.ollama_client",
  "kind": "imports",
  "label": "local",
  "line": 1
}
```

Mapping the completed Snake artifact directory also succeeded and found two
generated Python drafts, with 267 nodes and 425 edges.

## Follow-Up

- Add checkpoint/resume support to `scripts/run_structured_spec.py`, matching
  the capability and worker-limit CLI paths.
- Improve final integration repair so imported pygame constants remain
  qualified or are explicitly imported before the final static gate.
- Repeat the stochastic Pong run after that repair and require a clean final
  static result before treating Pong as completed.
