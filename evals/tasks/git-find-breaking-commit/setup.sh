#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Add totals with discounts"
printf '# Totals\n' > README.md && git add README.md && git commit -q -m "Add a readme"
printf '\n\ndef count(prices):\n    return len(prices)\n' >> totals.py && git commit -q -am "Add count()"
cat > totals.py <<'EOF'
def total(prices, discount_percent=0):
    return float(sum(prices))


def count(prices):
    return len(prices)
EOF
git commit -q -am "Speed up total()"
printf 'Run the tests with pytest.\n' >> README.md && git commit -q -am "Document how to test"
