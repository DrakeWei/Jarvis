from report import build_report


def test_build_report_uses_double_hash_markdown_headline() -> None:
    expected = "## Weekly Summary\n\nBody text"
    assert build_report("Weekly Summary", "Body text") == expected
