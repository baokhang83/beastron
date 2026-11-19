from report import build_report


def test_report():
    assert build_report("2026-11") == "Report for 2026-11"
