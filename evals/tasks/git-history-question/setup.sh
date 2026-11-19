#!/bin/sh
# Builds the history the task asks about. Fixed identities and dates (set by beastron) make the commit ids reproducible.
set -e
git init -q -b main
git add -A
git commit -q -m "Add the statement reader"

cat >> ledger.py <<'EOF'


def write_csv(rows, path):
    """Write rows back out as CSV."""
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
EOF
git commit -q -am "Add CSV export"

cat > totals.py <<'EOF'
from collections import defaultdict


def monthly_totals(rows):
    """Total amount per month (YYYY-MM), in whole units."""
    totals = defaultdict(float)
    for date, _description, amount in rows:
        totals[date[:7]] += amount
    return {month: int(total) for month, total in totals.items()}
EOF
git add totals.py
git commit -q -m "Add monthly totals"

sed -i.bak 's/return {month: int(total) for month, total in totals.items()}/return {month: round(total, 2) for month, total in totals.items()}/' totals.py
sed -i.bak 's/in whole units/rounded to the cent/' totals.py
rm totals.py.bak
git commit -q -am "Fix rounding of monthly totals"

printf '\nRun `python -m ledger export` to write the cleaned statement as CSV.\n' >> README.md
git commit -q -am "Document the export command"
