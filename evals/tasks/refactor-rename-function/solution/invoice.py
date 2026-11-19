import pricing


def invoice_total(lines):
    return round(pricing.calculate_total(lines), 2)
