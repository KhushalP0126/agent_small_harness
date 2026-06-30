COUNTER = 0


def bump():
    global COUNTER
    COUNTER += 1
    return COUNTER


def compute(values):
    total = 0
    for value in values:
        if value % 2 == 0:
            total += bump()
        else:
            total += value
    return total
