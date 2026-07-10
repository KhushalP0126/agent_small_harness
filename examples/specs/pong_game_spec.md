# Pong Game Spec

This file is an external experiment spec. It is not part of the harness design,
controller logic, Plan Mode rules, engine rules, or retry strategy.

Use it only as a sample structured input when testing whether the generic
harness can process a stateful app request.

## Game Spec

- name: pong
- language: python
- library: pygame
- route: template_or_small_worker
- kernel_mode: generate_from_spec

## Files

- `pong_game.py`

## Entrypoint

- `main()`

## Required Components

- `PongConfig`
- `Paddle`
- `Ball`
- `PongState`
- `create_initial_state()`
- `handle_input()`
- `move_paddle()`
- `update_ball()`
- `check_wall_collision()`
- `check_paddle_collision()`
- `score_point()`
- `update_state()`
- `render()`
- `main()`

## State

`PongConfig`:

- `width`
- `height`
- `paddle_width`
- `paddle_height`
- `ball_size`
- `paddle_speed`
- `ball_speed_x`
- `ball_speed_y`
- `fps`

`Paddle`:

- `x: int`
- `y: int`
- `width: int`
- `height: int`

`Ball`:

- `x: int`
- `y: int`
- `vx: int`
- `vy: int`
- `size: int`

`PongState`:

- `left_paddle: Paddle`
- `right_paddle: Paddle`
- `ball: Ball`
- `left_score: int`
- `right_score: int`
- `running: bool`
- `paused: bool`

## Game Loop

```text
while running:
  1. read pygame events
  2. update paddle movement intent
  3. move paddles within the screen bounds
  4. move the ball
  5. reflect the ball on top or bottom wall collision
  6. reflect the ball on paddle collision
  7. score for the opposite player when the ball exits left or right
  8. render paddles, ball, center line, and score
  9. tick clock
```

## Input Rules

- `W` and `S` move the left paddle.
- Arrow up and arrow down move the right paddle.
- Escape or window close exits.
- Space may pause or unpause if implemented.

## Update Rules

- Paddles move vertically only.
- Paddles must stay inside the screen.
- The ball moves by adding velocity to position.
- If the ball touches the top or bottom wall, reverse vertical velocity.
- If the ball overlaps a paddle while moving toward it, reverse horizontal velocity.
- If the ball exits the left side, increment the right score and reset the ball to center.
- If the ball exits the right side, increment the left score and reset the ball to center.
- The update logic should be deterministic and testable without opening a pygame window.

## Render Rules

- Use a dark background.
- Draw both paddles.
- Draw the ball.
- Draw a center line.
- Draw the score for both players.

## Validation Rules

- Generated file must parse.
- No network access.
- No file I/O.
- No `eval` or `exec`.
- `pygame` import is allowed for this spec.
- The game loop must be guarded by `if __name__ == "__main__"`.
- Deterministic update helpers should be testable without opening a pygame window.

## Pure Testable Helpers

- `clamp(value, minimum, maximum) -> int`
- `next_position(x, y, vx, vy) -> tuple[int, int]`
- `rects_overlap(a, b) -> bool`
- `reflect_x(vx) -> int`
- `reflect_y(vy) -> int`
- `reset_ball(width, height, vx, vy, size) -> Ball`

## Dependency Graph

- Input events -> paddle movement intent -> `move_paddle`
- Paddle state + screen bounds -> clamped paddle position
- Ball position + velocity -> `next_position` -> wall and paddle collision checks
- Wall collision -> `reflect_y`
- Paddle collision -> `reflect_x`
- Ball exits side -> score update -> `reset_ball`
- State update -> render frame -> clock tick

## Behavior Examples

- `clamp(5, 0, 10) == 5`
- `clamp(-1, 0, 10) == 0`
- `clamp(11, 0, 10) == 10`
- `next_position(4, 7, 2, -3) == (6, 4)`
- `reflect_x(3) == -3`
- `reflect_y(-4) == 4`
