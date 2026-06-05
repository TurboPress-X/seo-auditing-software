"""Generate the two client CSVs and the summary from the store."""

import csv
from collections import Counter, defaultdict
from urllib.parse import urlparse

from spider.parse import OG_TAGS
from spider.status import classify
from spider.store import get_status, iter_images, iter_links, iter_pages

PAGE_COLUMNS = ["Page ID", "URL", "Status Code", "Meta Title?", "Title Duplicated?",
                "Meta Description?", "Meta Duplicated?", "Open Graph?", "Canonical?"]
LINK_COLUMNS = ["Issue Type", "Found On (Page ID)", "Found On URL", "Target URL",
                "Status Code", "Redirect Destination", "Hops"]
EXTERNAL_SUMMARY_COLUMNS = ["Status Code", "Target URL", "Destination URL",
                            "Pages Affected", "Example Page", "Note"]
IMAGE_ISSUE_COLUMNS = ["Issue Type", "Image URL", "Status Code",
                       "Pages Affected", "Example Page"]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_internal(host: str, origin_host: str) -> bool:
    """Same-site test for <a> targets: the origin host or any subdomain of it."""
    return host == origin_host or host.endswith("." + origin_host)


def _last_two(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_geo_redirect(target_url: str, destination_url: str) -> bool:
    """True when a redirect only prepends a 2-letter country label to the same host
    (e.g. pinterest.com -> za.pinterest.com), i.e. a crawl-location geo-route, not a
    real destination change."""
    t = _host(target_url)
    d = _host(destination_url)
    if t.startswith("www."):
        t = t[4:]
    if not t or not d:
        return False
    label, _, rest = d.partition(".")
    if len(label) == 2 and label.isascii() and label.isalpha():
        return rest == t or rest == _last_two(t)
    return False


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
    # images with missing/empty alt text (an accessibility + SEO issue,
    # independent of whether the image itself loads)
    for img in iter_images(conn):
        if img["missing_alt"]:
            yield ["Missing Alt", img["found_on_id"], img["found_on_url"],
                   img["src"], "", "", ""]


def write_link_issues(conn, path: str) -> int:
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LINK_COLUMNS)
        for row in _issue_rows(conn):
            w.writerow(row)
            written += 1
    return written


def _collect(conn, origin):
    """Single classification pass -> (internal_rows, external, image) where:
      internal_rows: list of LINK_COLUMNS tuples, exact byte-duplicates removed.
      external: (groups, order); groups[key]={pages:set, example, note, verdict},
                key=(status_str, target, destination).
      image:    (groups, order); groups[key]={pages:set, example, code},
                key=(issue_type, src)."""
    origin_host = urlparse(origin).netloc.lower()
    internal, seen = [], set()
    ext, ext_order = {}, []
    img, img_order = {}, []

    for it in iter_links(conn):
        target = it["target"]
        st = get_status(conn, target)
        if not st:
            continue
        code, hops, final = st
        verdict = classify(code, hops)
        if verdict == "ok":
            continue
        if is_internal(_host(target), origin_host):
            if verdict == "redirected":
                issue, dest, hop_s = "Redirected", final, str(hops)
            else:
                issue, dest, hop_s = "Broken Link", "", ""
            row = (issue, it["found_on_id"], it["found_on_url"], target,
                   str(code), dest, hop_s)
            if row not in seen:
                seen.add(row)
                internal.append(row)
        else:
            dest = final if verdict == "redirected" else ""
            key = (str(code), target, dest)
            if key not in ext:
                note = "geo-redirect" if (verdict == "redirected"
                                          and is_geo_redirect(target, final)) else ""
                ext[key] = {"pages": set(), "example": it["found_on_url"],
                            "note": note, "verdict": verdict}
                ext_order.append(key)
            ext[key]["pages"].add(it["found_on_url"])

    for im in iter_images(conn):
        src = im["src"]
        st = get_status(conn, src)
        if st and classify(st[0], st[1]) == "broken":
            key = ("Broken Image", src)
            if key not in img:
                img[key] = {"pages": set(), "example": im["found_on_url"], "code": str(st[0])}
                img_order.append(key)
            img[key]["pages"].add(im["found_on_url"])
        if im["missing_alt"]:
            key = ("Missing Alt", src)
            if key not in img:
                img[key] = {"pages": set(), "example": im["found_on_url"], "code": ""}
                img_order.append(key)
            img[key]["pages"].add(im["found_on_url"])

    return internal, (ext, ext_order), (img, img_order)


def write_internal_link_issues(conn, path: str, origin: str) -> int:
    internal, _, _ = _collect(conn, origin)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LINK_COLUMNS)
        for row in internal:
            w.writerow(row)
    return len(internal)


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
        for k in ("Broken Link", "Broken Image", "Redirected", "Missing Alt"):
            f.write(f"  {counts.get(k, 0):5d}  {k}\n")
