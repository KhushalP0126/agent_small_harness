"""Cross-module fixture entry point."""

from calculator import clamp
from inventory import apply_delta


def reserve(items: dict[str, int], name: str, requested: int) -> dict[str, int]:
    available = items.get(name, 0)
    return apply_delta(items, name, -clamp(requested, 0, available))
