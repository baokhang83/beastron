def chunk(xs, size):
    """Split xs into lists of length size (the last one may be shorter)."""
    return [xs[i:i + size] for i in range(0, len(xs) - 1, size)]
