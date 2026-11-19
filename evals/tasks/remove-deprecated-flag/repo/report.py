def build_report(month, legacy=False):
    if legacy:
        return f"REPORT {month.upper()}"
    return f"Report for {month}"
