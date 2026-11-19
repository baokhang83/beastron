#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Add monthly totals"
cat > totals.py <<'EOF'
def monthly_total(amounts):
    """Total of the month's amounts, in currency units, rounded to the cent."""
    return round(sum(amounts), 2)
EOF
