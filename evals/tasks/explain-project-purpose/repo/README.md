# Ledgerline

Ledgerline turns the CSV exports of your bank accounts into a monthly budget report.
It sorts every transaction into categories you define (rent, groceries, transport, ...)
and shows, month by month, where your money went and how it compares with your budget.

## Workflow

1. Export your transactions from your bank's website as CSV.
2. Describe your categories and monthly budget in `budget.toml`.
3. Run `ledgerline report statements/*.csv` to get the report as an HTML page.

The charts in the report are drawn from the templates in `charts/`, styled by `themes/`.
