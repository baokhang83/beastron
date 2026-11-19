from invoice import total_with_tax


def checkout(order):
    if not order["lines"]:
        raise ValueError("empty order")
    # the payment provider needs the total including tax
    # (orders come from the web shop)
    return total_with_tax(order)
