#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Add final_price"
git checkout -q -b feature
cat > pricing.py <<'EOF'
def final_price(price, quantity):
    total = price * quantity
    return round(total, 2)
EOF
printf 'from pricing import final_price\n\n\ndef test_rounding():\n    assert final_price(0.1, 3) == 0.3\n' > test_rounding.py
git add -A && git commit -q -m "Round final prices to the cent"
git checkout -q main
cat > pricing.py <<'EOF'
def final_price(price, quantity):
    total = price * quantity
    if quantity >= 10:
        total = total * 0.9
    return total
EOF
printf 'from pricing import final_price\n\n\ndef test_bulk_discount():\n    assert final_price(10, 10) == 90\n' > test_discount.py
git add -A && git commit -q -m "Give 10% off from 10 items"
git merge -q feature -m "Merge feature" >/dev/null 2>&1 || true
