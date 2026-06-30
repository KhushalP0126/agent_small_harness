def classify(values):
    total = 0
    for value in values:
        if value < 0:
            total -= value
        elif value == 0:
            total += 0
        elif value < 10:
            total += value
        elif value < 100:
            total += value // 2
        else:
            total += 1
    return total
