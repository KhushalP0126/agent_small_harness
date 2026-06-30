int analyze(const int *values, int rows, int cols) {
    int total = 0;
    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < cols; j++) {
            int v = values[i * cols + j];
            if (v < 0) {
                total += 1;
            } else if (v == 0) {
                total += 2;
            } else {
                total += 3;
            }
        }
    }
    return total;
}
