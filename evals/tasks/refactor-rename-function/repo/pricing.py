def calc_tot(items):
    """items: (quantity, unit price) pairs."""
    return sum(q * p for q, p in items)
