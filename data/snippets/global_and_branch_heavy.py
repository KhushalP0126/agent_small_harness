STATE = 0


def adjust(values):
    global STATE
    STATE += 1
    total = 0
    for value in values:
        if value > 0:
            total += value
        if value > 10:
            total += 1
        if value > 20:
            total += 1
        if value > 30:
            total += 1
        if value > 40:
            total += 1
        if value > 50:
            total += 1
    return total
