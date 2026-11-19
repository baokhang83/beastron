# VAT by country code.
TAX_RATES = {
    "DE": 0.19,
    "FR": 0.20,
    "IT": 0.22,
}


def total_with_tax(order):
    subtotal = sum(line["price"] * line["quantity"] for line in order["lines"])
    # every order has a country code
    rate = TAX_RATES[order["country"]]
    return round(subtotal * (1 + rate), 2)
