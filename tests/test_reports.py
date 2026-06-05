import csv
from spider.store import (connect, init_schema, save_page, save_link,
                          save_image, save_status)
from spider.reports import write_page_audit, write_link_issues, write_summary
from spider.reports import is_internal, is_geo_redirect


def populated(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)
    # page A: missing description, dup title "Home", og:image broken
    save_page(conn, "C-1", "https://e.com/a", "https://e.com/a", 200,
              "Home", None, ["og:image"], "https://e.com/og.png", True)
    # page B: dup title "Home", everything else fine, no canonical
    save_page(conn, "C-2", "https://e.com/b", "https://e.com/b", 200,
              "Home", "desc b",
              ["og:title", "og:description", "og:image", "og:type", "og:url"],
              "https://e.com/ok.png", False)
    # page C: clean (should NOT appear in audit)
    save_page(conn, "C-3", "https://e.com/c", "https://e.com/c", 200,
              "Unique", "desc c",
              ["og:title", "og:description", "og:image", "og:type", "og:url"],
              "https://e.com/ok.png", True)
    save_status(conn, "https://e.com/og.png", 404, 0, "https://e.com/og.png")
    save_status(conn, "https://e.com/ok.png", 200, 0, "https://e.com/ok.png")
    # links
    save_link(conn, "C-1", "https://e.com/a", "https://e.com/dead")
    save_link(conn, "C-1", "https://e.com/a", "https://e.com/redir")
    save_link(conn, "C-1", "https://e.com/a", "https://e.com/fine")
    save_status(conn, "https://e.com/dead", 404, 0, "https://e.com/dead")
    save_status(conn, "https://e.com/redir", 200, 2, "https://e.com/final")
    save_status(conn, "https://e.com/fine", 200, 0, "https://e.com/fine")
    save_image(conn, "C-1", "https://e.com/a", "https://e.com/broke.jpg", False)
    save_status(conn, "https://e.com/broke.jpg", 500, 0, "https://e.com/broke.jpg")
    # image that loads fine (200) but has no alt text
    save_image(conn, "C-1", "https://e.com/a", "https://e.com/noalt.png", True)
    save_status(conn, "https://e.com/noalt.png", 200, 0, "https://e.com/noalt.png")
    return conn


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_page_audit_columns_and_filtering(tmp_path):
    conn = populated(tmp_path)
    out = tmp_path / "page_audit.csv"
    write_page_audit(conn, str(out))
    rows = read_csv(out)
    ids = {r["Page ID"]: r for r in rows}
    assert "C-3" not in ids  # clean page excluded
    assert "C-1" in ids and "C-2" in ids
    a = ids["C-1"]
    assert a["Meta Description?"] == "Missing"
    assert a["Title Duplicated?"] == "Yes (2)"
    assert a["Open Graph?"] == "Broken og:image; Missing: og:title, og:description, og:type, og:url"
    assert a["Canonical?"] == "OK"
    b = ids["C-2"]
    assert b["Canonical?"] == "Missing"
    assert b["Open Graph?"] == "OK"
    assert b["Meta Duplicated?"] == "No"


def test_page_audit_excludes_non_200_pages(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)
    save_page(conn, "C-9", "https://e.com/old", "https://e.com/old", 301,
              None, None, [], None, False)
    out = tmp_path / "page_audit.csv"
    write_page_audit(conn, str(out))
    rows = read_csv(out)
    assert all(r["Page ID"] != "C-9" for r in rows)


def test_link_issues_classification(tmp_path):
    conn = populated(tmp_path)
    out = tmp_path / "link_issues.csv"
    write_link_issues(conn, str(out))
    rows = read_csv(out)
    by_target = {r["Target URL"]: r for r in rows}
    assert by_target["https://e.com/dead"]["Issue Type"] == "Broken Link"
    assert by_target["https://e.com/dead"]["Status Code"] == "404"
    assert by_target["https://e.com/redir"]["Issue Type"] == "Redirected"
    assert by_target["https://e.com/redir"]["Redirect Destination"] == "https://e.com/final"
    assert by_target["https://e.com/redir"]["Hops"] == "2"
    assert by_target["https://e.com/broke.jpg"]["Issue Type"] == "Broken Image"
    assert by_target["https://e.com/noalt.png"]["Issue Type"] == "Missing Alt"
    assert "https://e.com/fine" not in by_target  # ok links excluded


def test_summary_written(tmp_path):
    conn = populated(tmp_path)
    out = tmp_path / "summary.txt"
    write_summary(conn, str(out), {"report_code": "C-20260603",
                                   "client": "C", "domain": "e.com",
                                   "start_url": "https://e.com",
                                   "origin": "https://e.com",
                                   "started": "2026-06-03T09:00",
                                   "finished": "2026-06-03T09:05",
                                   "resumed": False})
    text = out.read_text(encoding="utf-8")
    assert "C-20260603" in text
    assert "Broken Link" in text
    assert "1  Broken Link" in text
    assert "1  Broken Image" in text
    assert "1  Redirected" in text
    assert "1  Missing Alt" in text


def test_is_internal():
    assert is_internal("washingtonparent.com", "washingtonparent.com")
    assert is_internal("www.washingtonparent.com", "washingtonparent.com")
    assert is_internal("picks.washingtonparent.com", "washingtonparent.com")
    assert not is_internal("za.pinterest.com", "washingtonparent.com")
    assert not is_internal("i0.wp.com", "washingtonparent.com")
    assert not is_internal("washingtonparent.semantica.co.za", "washingtonparent.com")


def test_is_geo_redirect():
    assert is_geo_redirect("https://www.pinterest.com/washparent",
                           "https://za.pinterest.com/washparent")
    assert is_geo_redirect("https://pinterest.com/pin/create/button/?x=1",
                           "https://za.pinterest.com/pin/create/button/?x=1")
    assert not is_geo_redirect("https://www.linkedin.com/shareArticle?x",
                               "https://www.linkedin.com/uas/login?x")
    assert not is_geo_redirect("https://www.facebook.com/sharer.php?u=x",
                               "https://www.facebook.com/share_channel/?x")
