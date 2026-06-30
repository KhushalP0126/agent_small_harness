/* Snake skeleton (C). Fill in the TODOs; keep functions small and flat. */
#include <stddef.h>

#define WIDTH 20
#define HEIGHT 20

/* Returns 0 when the game is over, non-zero to keep running. */
static int step(void) {
    /* TODO: read input, move snake, handle food/growth, detect collisions. */
    return 0;
}

int main(void) {
    int running = 1;
    while (running) {
        running = step();
    }
    return 0;
}
