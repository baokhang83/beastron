def categorize(description, rules):
    """Return the first category whose keyword appears in the transaction description."""
    text = description.lower()
    for category, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "uncategorized"
