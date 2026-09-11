from ml.src.features.engineering import chronological_split, lag


def test_lag_cannot_read_current_target() -> None:
    values = [1.0, 2.0, 99.0]
    assert lag(values, 2, 1) == 2.0


def test_chronological_split_preserves_order() -> None:
    train, test = chronological_split(list(range(10)), 3)
    assert list(train) == list(range(7)) and list(test) == [7, 8, 9]
