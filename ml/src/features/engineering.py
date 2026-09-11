"""Past-only feature helpers; target at index i is never used to build its own features."""
from collections.abc import Sequence


def lag(values: Sequence[float], index: int, periods: int) -> float:
    if index - periods < 0:
        raise ValueError("Insufficient historical observations for lag")
    return values[index - periods]


def chronological_split(rows: Sequence[object], test_size: int) -> tuple[Sequence[object], Sequence[object]]:
    if test_size <= 0 or test_size >= len(rows):
        raise ValueError("test_size must leave training history")
    return rows[:-test_size], rows[-test_size:]
