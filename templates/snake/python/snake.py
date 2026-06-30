"""Snake skeleton (Python). Fill in the TODOs; keep functions small and flat."""


def step(snake, direction, food):
    # TODO: move the snake, handle food/growth, and return False on game over.
    return False


def main():
    snake = [(0, 0)]
    direction = (0, 1)
    food = (5, 5)
    running = True
    while running:
        running = step(snake, direction, food)


if __name__ == "__main__":
    main()
