from pricing import calculate_total


def test_total():
    assert calculate_total([(2, 3.0), (1, 4.0)]) == 10.0
