import pytest

from durations import parse_duration


def test_hours_minutes_seconds():
    assert parse_duration("2h") == 7200
    assert parse_duration("15m") == 900
    assert parse_duration("45s") == 45


def test_combinations():
    assert parse_duration("1h30m") == 5400
    assert parse_duration("1h1m1s") == 3661


@pytest.mark.parametrize("bad", ["", "1x", "h1", "1.5h", "ten minutes"])
def test_invalid_input(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)
