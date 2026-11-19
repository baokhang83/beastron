#!/bin/sh
set -e
cat > pricing.py <<'EOF'
def final_price(price, quantity):
    total = price * quantity
    if quantity >= 10:
        total = total * 0.9
    return round(total, 2)
EOF
git add pricing.py
git commit -q --no-edit
