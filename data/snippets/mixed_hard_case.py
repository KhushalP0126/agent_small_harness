STATE = {}


def analyze(matrix):
    global STATE
    count = 0
    for row in matrix:
        if not row:
            continue
        for value in row:
            if value < 0:
                count += 1
            elif value == 0:
                count += 2
            elif value < 10:
                count += 3
            elif value < 100:
                count += 4
            else:
                count += 5
    STATE["last"] = count
    return count
