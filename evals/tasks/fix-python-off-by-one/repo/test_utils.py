from utils import chunk


def test_chunk_even():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunk_uneven():
    assert chunk([1, 2, 3], 2) == [[1, 2], [3]]


def test_chunk_single():
    assert chunk([1], 1) == [[1]]
