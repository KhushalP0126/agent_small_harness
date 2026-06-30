#include <vector>

int analyze(const std::vector<std::vector<int>>& matrix) {
    int total = 0;
    for (const auto& row : matrix) {
        for (int value : row) {
            if (value < 0) {
                total += 1;
            } else if (value == 0) {
                total += 2;
            } else {
                total += 3;
            }
        }
    }
    return total;
}
