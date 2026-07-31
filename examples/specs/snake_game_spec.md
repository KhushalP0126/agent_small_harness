# Snake Game Spec

> Fixture audit: 2026-07-30. Plan without worker generation using
> `make structured-spec-plan SPEC_PATH=examples/specs/snake_game_spec.md`.

This file is an external experiment spec. It is not part of the harness design,
controller logic, Plan Mode rules, engine rules, or retry strategy.

Use it only as a sample structured input when testing whether the generic
harness can process a stateful app request.

## Game Spec

- name: snake
- language: python
- library: pygame
- route: template_or_small_worker
- kernel_mode: generate_from_spec

## Files

- `snake_game.py`

## Entrypoint

- `main()`

## Required Components

- `GameConfig`
- `Direction`
- `SnakeState`
- `create_initial_state()`
- `handle_input()`
- `update_state()`
- `spawn_food()`
- `check_collision()`
- `render()`
- `main()`

## State

`GameConfig`:

- `width`
- `height`
- `cell_size`
- `fps`

`SnakeState`:

- `snake_body: list[tuple[int, int]]`
- `direction: tuple[int, int]`
- `next_direction: tuple[int, int]`
- `food: tuple[int, int]`
- `score: int`
- `game_over: bool`
- `rng_seed: optional int`

## Game Loop

```text
while running:
  1. read pygame events
  2. update next_direction
  3. update snake position
  4. detect food collision
  5. grow snake or move normally
  6. detect wall/self collision
  7. render board, snake, food, score
  8. tick clock
```

## Input Rules

- Arrow keys or WASD change direction.
- The snake cannot reverse directly into itself.
- Escape or window close exits.
- After game over, `R` may restart if implemented.

## Update Rules

- The snake moves one grid cell per tick.
- `head = old_head + direction`.
- If `head == food`, grow the snake, increment score, and spawn new food.
- If `head != food`, move by adding the new head and removing the tail.
- Hitting the wall ends the game.
- Hitting the snake body ends the game.
- New food must not spawn inside the snake body.

## Render Rules

- Use a dark background.
- Draw snake cells.
- Draw one food cell.
- Draw the score.
- Draw a game-over message when `game_over` is true.

## Validation Rules

- Generated file must parse.
- No network access.
- No file I/O.
- No `eval` or `exec`.
- `pygame` import is allowed for this spec.
- The game loop must be guarded by `if __name__ == "__main__"`.
- Deterministic update helpers should be testable without opening a pygame window.

## Pure Testable Helpers

- `opposite_direction(a, b) -> bool`
- `next_head(head, direction) -> tuple[int, int]`
- `hits_wall(head, width, height) -> bool`
- `hits_self(head, body) -> bool`
- `choose_food(width, height, occupied, rng) -> tuple[int, int]`
- `step_state(state, config) -> SnakeState`

## Dependency Graph

- Input events -> `next_direction` -> direction update
- Snake body + direction -> next head -> collision checks
- Food position + new head -> growth, score, and respawn
- State update -> render frame -> clock tick

## Behavior Examples

- `opposite_direction((1, 0), (-1, 0)) == True`
- `opposite_direction((1, 0), (0, 1)) == False`
- `next_head((5, 5), (1, 0)) == (6, 5)`
- `hits_wall((-1, 5), 20, 20) == True`
- `hits_wall((10, 10), 20, 20) == False`
- `hits_self((3, 3), [(1, 1), (3, 3)]) == True`
