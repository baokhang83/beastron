from totals import total


def test_discount_is_applied():
    assert total([100, 100], discount_percent=10) == 180
