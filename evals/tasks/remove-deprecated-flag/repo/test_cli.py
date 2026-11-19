from report import build_report


def test_report():
    assert build_report("2026-11") == "Report for 2026-11"


def test_report_legacy_mode():
    assert build_report("2026-11", legacy=True) == "REPORT 2026-11"
