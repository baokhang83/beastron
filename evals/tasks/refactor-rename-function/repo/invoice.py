import pricing


def invoice_total(lines):
    return round(pricing.calc_tot(lines), 2)
