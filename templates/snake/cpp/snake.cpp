// Snake skeleton (C++). Fill in the TODOs; keep methods small and flat.
#include <utility>
#include <vector>

constexpr int WIDTH = 20;
constexpr int HEIGHT = 20;

struct Game {
    std::vector<std::pair<int, int>> snake{{0, 0}};
    std::pair<int, int> direction{0, 1};

    // Returns false when the game is over.
    bool step() {
        // TODO: read input, move snake, handle food/growth, detect collisions.
        return false;
    }
};

int main() {
    Game game;
    bool running = true;
    while (running) {
        running = game.step();
    }
    return 0;
}
