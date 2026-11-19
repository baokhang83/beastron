from pricing import calc_tot


def test_total():
    assert calc_tot([(2, 3.0), (1, 4.0)]) == 10.0
