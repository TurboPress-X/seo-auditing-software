"""Generate the two client CSVs and the summary from the store."""

import csv
from collections import Counter, defaultdict

from spider.parse import OG_TAGS
from spider.status import classify
from spider.store import get_status, iter_images, iter_links, iter_pages

PAGE_COLUMNS = ["Page ID", "URL", "Status Code", "Meta Title?", "Title Duplicated?",
                "Meta Description?", "Meta Duplicated?", "Open Graph?", "Canonical?"]
LINK_COLUMNS = ["Issue Type", "Found On (Page ID)", "Found On URL", "Target URL",
                "Status Code", "Redirect Destination", "Hops"]


def _dup_counts(pages, key):
    groups = defaultdict(int)
    for p in pages:
        v = p[key]
        if v:
            groups[v] += 1
    return groups


def _og_status(conn, page):
    """Combined Open Graph verdict: flags a broken og:image and/or missing core
    tags, so a page's full OG state shows in one cell (avoids 'fix one, find the
    next next month')."""
    parts = []
    if page["og_image"]:
        st = get_status(conn, page["og_image"])
        if st and classify(st[0], st[1]) == "broken":
            parts.append("Broken og:image")
    missing = [t for t in OG_TAGS if t not in page["og_present"]]
    if missing:
        parts.append("Missing: " + ", ".join(missing))
    return "; ".join(parts) if parts else "OK"


def write_page_audit(conn, path: str) -> int:
    pages = list(iter_pages(conn))
    title_dups = _dup_counts(pages, "title")
    desc_dups = _dup_counts(pages, "description")
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PAGE_COLUMNS)
        for p in pages:
            if p["status"] != 200:
                continue  # non-200 (redirects/errors) are covered by link_issues
            title_missing = not p["title"]
            desc_missing = not p["description"]
            title_dup = p["title"] and title_dups[p["title"]] > 1
            desc_dup = p["description"] and desc_dups[p["description"]] > 1
            og = _og_status(conn, p)
            canonical_ok = bool(p["canonical_present"])
            if not any([title_missing, desc_missing, title_dup, desc_dup,
                        og != "OK", not canonical_ok]):
                continue
            w.writerow([
                p["page_id"], p["display_url"], p["status"],
                "Missing" if title_missing else "OK",
                f"Yes ({title_dups[p['title']]})" if title_dup else "No",
                "Missing" if desc_missing else "OK",
                f"Yes ({desc_dups[p['description']]})" if desc_dup else "No",
                og,
                "OK" if canonical_ok else "Missing",
            ])
            written += 1
    return written


def _issue_rows(conn):
    for kind, items, url_key in (("link", iter_links(conn), "target"),
                                 ("image", iter_images(conn), "src")):
        for it in items:
            target = it[url_key]
            st = get_status(conn, target)
            if not st:
                continue
            code, hops, final = st
            verdict = classify(code, hops)
            if verdict == "ok":
                continue
            if verdict == "redirected":
                issue = "Redirected"
                dest, hop_s = final, str(hops)
            else:
                issue = "Broken Link" if kind == "link" else "Broken Image"
                dest, hop_s = "", ""
            yield [issue, it["found_on_id"], it["found_on_url"], target,
                   str(code), dest, hop_s]


def write_link_issues(conn, path: str) -> int:
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LINK_COLUMNS)
        for row in _issue_rows(conn):
            w.writerow(row)
            written += 1
    return written


def write_summary(conn, path: str, meta: dict) -> None:
    counts = Counter(row[0] for row in _issue_rows(conn))
    pages = list(iter_pages(conn))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Report:        {meta['report_code']}\n")
        f.write(f"Client:        {meta['client']}\n")
        f.write(f"Domain:        {meta['domain']}\n")
        f.write(f"Start URL:     {meta['start_url']}\n")
        f.write(f"Canonical:     {meta['origin']}\n")
        f.write(f"Started:       {meta['started']}\n")
        f.write(f"Finished:      {meta['finished']}\n")
        f.write(f"Resumed:       {meta['resumed']}\n")
        f.write(f"Pages crawled: {len(pages)}\n")
        f.write("\nLink & image issues:\n")
        for k in ("Broken Link", "Broken Image", "Redirected"):
            f.write(f"  {counts.get(k, 0):5d}  {k}\n")
