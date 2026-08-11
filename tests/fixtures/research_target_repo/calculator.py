"""Small arithmetic helpers used by the research fixture corpus."""


def add(left: int, right: int) -> int:
    return left + right


def clamp(value: int, lower: int, upper: int) -> int:
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return min(max(value, lower), upper)
