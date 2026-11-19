def total(prices, discount_percent=0):
    return sum(prices) * (100 - discount_percent) / 100
