#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Add invoices in EUR"
printf 'CURRENCY = "USD"\n' > currency.py && git commit -q -am "Switch prices to USD"
printf 'def render(total):\n    return f"Total: {total}\\nThank you for your order!"\n' > invoice.py
git commit -q -am "Thank the customer on invoices"
