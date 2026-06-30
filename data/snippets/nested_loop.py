def compare_pairs(values):
    total = 0
    for left in values:
        for right in values:
            total += left * right
    return total
