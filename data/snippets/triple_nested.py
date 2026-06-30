def cube_compare(values):
    total = 0
    for left in values:
        for middle in values:
            for right in values:
                total += left + middle + right
    return total
