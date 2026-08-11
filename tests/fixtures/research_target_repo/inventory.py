"""Minimal inventory state with deterministic mutation rules."""


def apply_delta(items: dict[str, int], name: str, delta: int) -> dict[str, int]:
    updated = dict(items)
    next_count = updated.get(name, 0) + delta
    if next_count < 0:
        raise ValueError("inventory cannot become negative")
    updated[name] = next_count
    return updated
