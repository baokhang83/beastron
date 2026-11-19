import csv


def read_statement(path):
    """Rows of (date, description, amount) from a bank CSV export."""
    with open(path, newline="") as f:
        return [(row["date"], row["description"], float(row["amount"])) for row in csv.DictReader(f)]
