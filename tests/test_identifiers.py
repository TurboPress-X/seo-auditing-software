from datetime import date
from spider.identifiers import slug_client, report_code, page_id


def test_slug_client_uppercases_and_hyphenates():
    assert slug_client("Example Co") == "EXAMPLE-CO"
    assert slug_client("  acme   co ") == "ACME-CO"
    assert slug_client("A&B / Ltd.") == "AB-LTD"


def test_report_code_format():
    assert report_code("EXAMPLE-CO", date(2026, 6, 3)) == "EXAMPLE-CO-20260603"


def test_page_id_is_stable_for_same_identity():
    a = page_id("ACME", "https://example.com/about")
    b = page_id("ACME", "https://example.com/about")
    assert a == b
    assert a.startswith("ACME-")
    assert len(a.split("-")[-1]) == 8


def test_page_id_differs_by_url():
    assert page_id("ACME", "https://example.com/a") != page_id("ACME", "https://example.com/b")
