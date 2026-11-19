def calculate_total(items):
    """items: (quantity, unit price) pairs."""
    return sum(q * p for q, p in items)
