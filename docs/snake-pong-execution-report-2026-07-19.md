# Snake and Pong execution report — 2026-07-19

## Setup

- Worker backend: local Ollama at `http://127.0.0.1:11434`
- Worker model: `qwen2.5-coder:1.5b`
- Architect repair model: configured API architect
- Artifact capture: enabled with `SAVE_ARTIFACTS=1`
- Runtime: Python 3.11.9, pygame 2.6.1, SDL dummy video/audio drivers

Both examples were run through the full structured-spec generation path, not
the plan-only path. Pong's generated source was then compiled and executed
headlessly. Snake did not produce an executable source file because its contract
queue failed before integration.

## Commands

```text
make structured-spec SPEC_PATH=examples/specs/snake_game_spec.md MODEL=qwen2.5-coder:1.5b SAVE_ARTIFACTS=1 ARCHITECT_AFTER=1 ARCHITECT_MAX_RETRIES=2
make structured-spec SPEC_PATH=examples/specs/pong_game_spec.md MODEL=qwen2.5-coder:1.5b SAVE_ARTIFACTS=1 ARCHITECT_AFTER=1 ARCHITECT_MAX_RETRIES=2
python3 -m py_compile artifacts/runs/structured_spec_pong_game_spec-20260720T035320Z-d119933b/attempt_1.py
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 artifacts/runs/structured_spec_pong_game_spec-20260720T035320Z-d119933b/attempt_1.py
```

## Results

| Game | Contract generation | Harness result | Compile | Execution | Final result |
| --- | --- | --- | --- | --- | --- |
| Snake | Stopped at 4/16 | `manual_review_required` | Not available | Not available | Failed |
| Pong | 20/20 accepted | `completed` | Passed | Immediate `TypeError` | Failed |

## Snake failure

Artifact:
`artifacts/runs/structured_spec_snake_game_spec-20260720T034359Z-f076c4f8`

The first three contracts (`GameConfig`, `Direction`, and `SnakeState`) were
accepted. `opposite_direction` failed after two Qwen repair attempts and an
architect escalation. Its example setup crashed with:

```text
ImportError: cannot import name 'FrozenDataclass' from 'dataclasses'
```

`FrozenDataclass` is not an export of Python's `dataclasses` module. Because the
contract queue is sequential, the harness correctly stopped and reported
`manual_review_required`; integration never ran and no game source was emitted.

## Pong failure

Artifact:
`artifacts/runs/structured_spec_pong_game_spec-20260720T035320Z-d119933b`

All 20 contracts were accepted and the integrated result passed the harness's
static, structured-spec, and formal checks. Architect recovery was needed for
several contracts. The final source also passed `py_compile`.

Actual execution nevertheless crashed immediately in the game loop:

```text
Traceback (most recent call last):
  File "attempt_1.py", line 232, in <module>
    main()
  File "attempt_1.py", line 229, in main
    render(state=state)
  File "attempt_1.py", line 214, in render
    state.reflect_x(state.ball.position, state.ball.velocity)
  File "attempt_1.py", line 91, in reflect_x
    ball_velocity[0] *= -1
TypeError: 'tuple' object does not support item assignment
```

The generated `Ball.velocity` value is a tuple, while
`PongState.reflect_x()` treats it as a mutable list. The initial ball position
immediately takes the collision branch, so the mismatch crashes the first
runtime iteration.

## Harness gaps exposed

1. Contract validation did not prevent the Snake repair/escalation result from
   inventing an invalid standard-library import.
2. Pong's static, structured-spec, formal, and contract checks all passed even
   though the integrated program crashes on its first loop iteration.
3. The Pong workflow needs an integration-level runtime smoke test that starts
   `main()` with dummy SDL drivers and exercises at least one update/render tick.
4. The Pong contracts should enforce one consistent velocity representation,
   or collision functions should return a replacement tuple instead of mutating
   one in place.

## Original status

Neither generated game is currently executable end-to-end. The failures are
reproducible and the local artifacts retain the complete generation, retry,
validation, and escalation records.

## Post-fix verification — 2026-07-20

The same full commands were rerun after adding symbol-level import validation,
accepted cross-contract field types, blocking lint-skip signaling, and the
five-second headless integration smoke gate.

| Game/sample | Contracts | Static/spec/formal | Smoke execution | Final status |
| --- | ---: | --- | --- | --- |
| Snake | 16/16 accepted | Passed | Running after five-second window | `completed` |
| Pong sample A | 20/20 accepted | Initially blocked by registry false positive | Running after five-second window | `manual_review_required` |
| Pong sample B | 20/20 accepted | Complexity 8, limit 7 | Method-arity `TypeError` | `manual_review_required` |

### Snake closure

Artifact:
`artifacts/runs/structured_spec_snake_game_spec-20260720T075236Z-511081d0`

Snake completed all 16 contracts. `opposite_direction`, the original failure
point, needed two Qwen retries and one architect repair but was accepted without
the hallucinated `dataclasses.FrozenDataclass` import. The integrated game passed
static, structured-spec, and formal checks and remained running through the
bounded smoke window. This is a clean end-to-end recovery from the original
failure.

### Pong registry correction

Artifact:
`artifacts/runs/structured_spec_pong_game_spec-20260720T075757Z-1cd1473a`

The first post-fix Pong sample accepted all 20 contracts and passed the runtime
smoke check, so the original tuple-mutation crash was absent. It was downgraded
because Pylint reported `pygame.KEYUP` as a missing dynamic member. Runtime
inspection confirmed `pygame.KEYUP` exists in pygame 2.6.1; the trusted registry
listed `KEYDOWN` but omitted `KEYUP`. Adding `KEYUP` to
`data/library_registry.json` makes this exact artifact statically compliant with
zero violations, while unknown pygame members remain blocking.

### Pong smoke-gate confirmation

Artifact:
`artifacts/runs/structured_spec_pong_game_spec-20260720T080610Z-eff265b8`

A fresh stochastic Pong generation again accepted all 20 contracts, but the
architect-integrated source exceeded the complexity limit in
`check_wall_collision` and crashed during smoke execution:

```text
TypeError: Ball.next_position() takes 3 positional arguments but 5 were given
```

The new gate therefore converted the run to `manual_review_required` and saved
the traceback instead of reporting a false completion. This is a different
cross-contract mismatch from the original tuple mutation and shows that Pong is
not yet reliably generated across samples. It also confirms the integration
smoke gate works on a newly generated live failure, not only on a regression
fixture.

## Updated status

- Snake is verified end-to-end on the repaired pipeline.
- Pong can produce a runtime-clean sample, but a fresh sample exposed a separate
  method-signature mismatch and was correctly rejected.
- The pygame registry now recognizes the real `KEYUP` event constant without
  weakening rejection of unknown dynamic members.

### Pong method-arity follow-up

Artifact:
`artifacts/runs/structured_spec_pong_game_spec-20260720T152429Z-0675ada6`

After accepted-contract context was extended from field types to binding method
signatures and positional arity, another full Pong run accepted all 20 contracts
and survived the five-second integration smoke window. The earlier
`Ball.next_position()` argument-count crash did not recur.

The run still ended as `manual_review_required` because the integrated
`handle_input` function had cyclomatic complexity 10 against the configured
limit of 7. This cleanly separates the outcomes: the method-arity failure was
closed in this live sample, runtime execution passed, and the remaining blocker
is an ordinary static-complexity violation rather than cross-contract interface
drift.
