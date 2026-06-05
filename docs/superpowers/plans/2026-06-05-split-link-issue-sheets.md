# Split Link-Issue Reports Into Curated Sheets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single noisy `link_issues.csv` with three purpose-built CSVs (internal worklist, smart external summary, image issues) and suppress base64 data-URI false positives at the parse layer — while keeping the raw `link_issues.csv` unchanged as an audit trail.

**Architecture:** All reporting logic lives in `spider/reports.py`. A single `_collect(conn, origin)` pass classifies every link/image issue into three structured buckets (internal links / external links / images), and thin writer functions plus the summary all consume that one source of truth. A small parse-layer guard in `spider/parse.py` drops data-URI `href`/`src` values before they become bogus URLs. The report call site in `spider/cli.py` wires up the three new outputs.

**Tech Stack:** Python 3, stdlib `csv` + `urllib.parse`, SQLite via `spider/store.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-05-link-issues-sheets-design.md`

---

## File Structure

- `spider/parse.py` — add `_is_data_uri` guard; applied in the `<a>` and `<img>` loops of `parse_page`.
- `spider/reports.py` — add helpers (`_host`, `is_internal`, `is_geo_redirect`), the `_collect` classifier, three writers (`write_internal_link_issues`, `write_external_link_summary`, `write_image_issues`), and an updated `write_summary`. `_issue_rows`/`write_link_issues`/`write_page_audit` are left unchanged.
- `spider/cli.py` — `_write_all` calls the three new writers and imports them.
- `tests/test_parse.py` — data-URI guard test.
- `tests/test_reports.py` — helper unit tests, one shared `seeded()` fixture, three writer tests, updated summary test.

---

## Task 1: Parse-layer data-URI guard

**Files:**
- Modify: `spider/parse.py`
- Test: `tests/test_parse.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parse.py`:

```python
def test_skips_data_uri_links_and_images():
    html = """
    <html><body>
      <a href="data:text/html,hi">d</a>
      <img src="image/svg+xml;base64,PHN2Zz48L3N2Zz4=">
      <img src="data:image/png;base64,iVBOR">
      <a href="/real">r</a>
      <img src="/real.png" alt="x">
    </body></html>
    """
    p = parse_page(html, "https://example.com/page")
    assert "https://example.com/real" in p.links
    assert all(";base64," not in u and "image/svg" not in u for u in p.links)
    assert all(";base64," not in s and "image/svg" not in s for s in p.images)
    assert "https://example.com/real.png" in p.images
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parse.py::test_skips_data_uri_links_and_images -v`
Expected: FAIL — the `image/svg+xml;base64,…` src is resolved to `https://example.com/image/svg+xml;base64,…` and appears in `p.images`.

- [ ] **Step 3: Add the guard helper and apply it**

In `spider/parse.py`, add this function just above `parse_page` (after `_abs`):

```python
def _is_data_uri(ref: str) -> bool:
    """data: URIs (and the Elementor `image/svg+xml;base64,...` placeholder, which
    omits the `data:` prefix) are inline content, not crawlable URLs."""
    r = ref.strip().lower()
    return r.startswith("data:") or ";base64," in r
```

In the `<a>` loop, change the skip guard:

```python
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or _is_data_uri(href):
            continue
        target = _abs(page_url, href)
        if target.startswith("http"):
            data.links.append(target)
```

In the `<img>` loop, skip data-URI srcs before resolving:

```python
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or _is_data_uri(src):
            continue
        full = _abs(page_url, src)
        data.images.append(full)
        if not (img.get("alt") or "").strip():
            data.missing_alt.append(full)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parse.py -v`
Expected: PASS (all parse tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add spider/parse.py tests/test_parse.py
git commit -m "fix: drop data-URI href/src before they become bogus crawl URLs"
```

---

## Task 2: Classification helpers (`is_internal`, `is_geo_redirect`)

**Files:**
- Modify: `spider/reports.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reports.py` (top-level, after the existing imports — extend the `from spider.reports import ...` line to include the helpers):

```python
from spider.reports import is_internal, is_geo_redirect


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports.py::test_is_internal tests/test_reports.py::test_is_geo_redirect -v`
Expected: FAIL — `ImportError: cannot import name 'is_internal'`.

- [ ] **Step 3: Implement the helpers**

At the top of `spider/reports.py`, add `from urllib.parse import urlparse` to the imports, then add these functions after the existing imports (before `_dup_counts`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reports.py::test_is_internal tests/test_reports.py::test_is_geo_redirect -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: add is_internal and is_geo_redirect report helpers"
```

---

## Task 3: `_collect` classifier + `write_internal_link_issues`

**Files:**
- Modify: `spider/reports.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the shared fixture and the failing test**

Add to `tests/test_reports.py` (the `seeded` fixture is reused by Tasks 3–5):

```python
def seeded(tmp_path):
    """Crawl store with origin https://e.com and a mix of internal/external link
    issues plus image issues, for the curated-sheet writers."""
    conn = connect(str(tmp_path / "s.db"))
    init_schema(conn)
    # --- internal redirect e.com/old -> e.com/new, found on P1 (twice) and P2 ---
    save_link(conn, "P-1", "https://e.com/p1", "https://e.com/old")
    save_link(conn, "P-1", "https://e.com/p1", "https://e.com/old")  # byte-duplicate
    save_link(conn, "P-2", "https://e.com/p2", "https://e.com/old")
    save_status(conn, "https://e.com/old", 200, 1, "https://e.com/new")
    # --- internal broken e.com/gone 404, found on P1 ---
    save_link(conn, "P-1", "https://e.com/p1", "https://e.com/gone")
    save_status(conn, "https://e.com/gone", 404, 0, "https://e.com/gone")
    # --- internal OK link (excluded) ---
    save_link(conn, "P-1", "https://e.com/p1", "https://e.com/fine")
    save_status(conn, "https://e.com/fine", 200, 0, "https://e.com/fine")
    # --- external geo-redirect pinterest, found on P1 and P2 ---
    save_link(conn, "P-1", "https://e.com/p1", "https://www.pinterest.com/washparent")
    save_link(conn, "P-2", "https://e.com/p2", "https://www.pinterest.com/washparent")
    save_status(conn, "https://www.pinterest.com/washparent", 200, 1,
                "https://za.pinterest.com/washparent")
    # --- external non-geo redirect linkedin, found on P1 ---
    save_link(conn, "P-1", "https://e.com/p1", "https://www.linkedin.com/shareArticle?x")
    save_status(conn, "https://www.linkedin.com/shareArticle?x", 200, 1,
                "https://www.linkedin.com/uas/login?x")
    # --- external broken semantica (connect error), found on P1 and P2 ---
    save_link(conn, "P-1", "https://e.com/p1", "https://semantica.co.za")
    save_link(conn, "P-2", "https://e.com/p2", "https://semantica.co.za")
    save_status(conn, "https://semantica.co.za", "ERR:ConnectError", 0, "https://semantica.co.za")
    # --- broken image (CDN-ish), found on P1 and P2 ---
    save_image(conn, "P-1", "https://e.com/p1", "https://e.com/broke.jpg", False)
    save_image(conn, "P-2", "https://e.com/p2", "https://e.com/broke.jpg", False)
    save_status(conn, "https://e.com/broke.jpg", 500, 0, "https://e.com/broke.jpg")
    # --- image missing alt, found on P1 ---
    save_image(conn, "P-1", "https://e.com/p1", "https://e.com/noalt.png", True)
    save_status(conn, "https://e.com/noalt.png", 200, 0, "https://e.com/noalt.png")
    # --- redirected image (must NOT appear on image sheet) ---
    save_image(conn, "P-1", "https://e.com/p1", "https://e.com/imgredir.png", False)
    save_status(conn, "https://e.com/imgredir.png", 200, 1, "https://e.com/final.png")
    conn.commit()
    return conn


def test_internal_link_issues_sheet(tmp_path):
    from spider.reports import write_internal_link_issues
    conn = seeded(tmp_path)
    out = tmp_path / "internal_link_issues.csv"
    write_internal_link_issues(conn, str(out), "https://e.com")
    rows = read_csv(out)
    # only internal targets
    targets = [r["Target URL"] for r in rows]
    assert all(t.startswith("https://e.com/") for t in targets)
    assert "https://www.pinterest.com/washparent" not in targets
    assert "https://semantica.co.za" not in targets
    # byte-duplicate dropped: e.com/old on P-1 appears once, on P-2 once => 2 rows
    old_rows = [r for r in rows if r["Target URL"] == "https://e.com/old"]
    assert len(old_rows) == 2
    assert {r["Found On (Page ID)"] for r in old_rows} == {"P-1", "P-2"}
    redir = next(r for r in old_rows if r["Found On (Page ID)"] == "P-1")
    assert redir["Issue Type"] == "Redirected"
    assert redir["Redirect Destination"] == "https://e.com/new"
    assert redir["Hops"] == "1"
    gone = next(r for r in rows if r["Target URL"] == "https://e.com/gone")
    assert gone["Issue Type"] == "Broken Link"
    assert gone["Status Code"] == "404"
    assert "https://e.com/fine" not in targets  # ok excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports.py::test_internal_link_issues_sheet -v`
Expected: FAIL — `ImportError: cannot import name 'write_internal_link_issues'`.

- [ ] **Step 3: Implement `_collect` and the internal writer**

In `spider/reports.py`, add the column constant near the existing `LINK_COLUMNS`:

```python
EXTERNAL_SUMMARY_COLUMNS = ["Status Code", "Target URL", "Destination URL",
                            "Pages Affected", "Example Page", "Note"]
IMAGE_ISSUE_COLUMNS = ["Issue Type", "Image URL", "Status Code",
                       "Pages Affected", "Example Page"]
```

Add the shared classifier and the internal writer (after `write_link_issues`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reports.py::test_internal_link_issues_sheet -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: add _collect classifier and internal_link_issues sheet"
```

---

## Task 4: `write_external_link_summary`

**Files:**
- Modify: `spider/reports.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reports.py`:

```python
def test_external_link_summary_sheet(tmp_path):
    from spider.reports import write_external_link_summary
    conn = seeded(tmp_path)
    out = tmp_path / "external_link_summary.csv"
    write_external_link_summary(conn, str(out), "https://e.com")
    rows = read_csv(out)
    by_target = {r["Target URL"]: r for r in rows}
    # only external targets, no internal leakage
    assert "https://e.com/old" not in by_target
    pin = by_target["https://www.pinterest.com/washparent"]
    assert pin["Status Code"] == "200"
    assert pin["Destination URL"] == "https://za.pinterest.com/washparent"
    assert pin["Pages Affected"] == "2"
    assert pin["Example Page"] in {"https://e.com/p1", "https://e.com/p2"}
    assert pin["Note"] == "geo-redirect"
    li = by_target["https://www.linkedin.com/shareArticle?x"]
    assert li["Pages Affected"] == "1"
    assert li["Note"] == ""
    assert li["Destination URL"] == "https://www.linkedin.com/uas/login?x"
    sem = by_target["https://semantica.co.za"]
    assert sem["Status Code"] == "ERR:ConnectError"
    assert sem["Destination URL"] == ""
    assert sem["Pages Affected"] == "2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports.py::test_external_link_summary_sheet -v`
Expected: FAIL — `ImportError: cannot import name 'write_external_link_summary'`.

- [ ] **Step 3: Implement the writer**

Add to `spider/reports.py` (after `write_internal_link_issues`):

```python
def write_external_link_summary(conn, path: str, origin: str) -> int:
    _, (ext, order), _ = _collect(conn, origin)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(EXTERNAL_SUMMARY_COLUMNS)
        for key in order:
            code, target, dest = key
            g = ext[key]
            w.writerow([code, target, dest, len(g["pages"]), g["example"], g["note"]])
    return len(order)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reports.py::test_external_link_summary_sheet -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: add smart external_link_summary sheet"
```

---

## Task 5: `write_image_issues`

**Files:**
- Modify: `spider/reports.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reports.py`:

```python
def test_image_issues_sheet(tmp_path):
    from spider.reports import write_image_issues
    conn = seeded(tmp_path)
    out = tmp_path / "image_issues.csv"
    write_image_issues(conn, str(out))
    rows = read_csv(out)
    by_key = {(r["Issue Type"], r["Image URL"]): r for r in rows}
    broke = by_key[("Broken Image", "https://e.com/broke.jpg")]
    assert broke["Status Code"] == "500"
    assert broke["Pages Affected"] == "2"
    noalt = by_key[("Missing Alt", "https://e.com/noalt.png")]
    assert noalt["Status Code"] == ""
    assert noalt["Pages Affected"] == "1"
    # redirected image is not an image issue
    assert ("Broken Image", "https://e.com/imgredir.png") not in by_key
    assert ("Redirected", "https://e.com/imgredir.png") not in by_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports.py::test_image_issues_sheet -v`
Expected: FAIL — `ImportError: cannot import name 'write_image_issues'`.

- [ ] **Step 3: Implement the writer**

Add to `spider/reports.py` (after `write_external_link_summary`):

```python
def write_image_issues(conn, path: str) -> int:
    _, _, (img, order) = _collect(conn, origin="")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(IMAGE_ISSUE_COLUMNS)
        for key in order:
            issue, src = key
            g = img[key]
            w.writerow([issue, src, g["code"], len(g["pages"]), g["example"]])
    return len(order)
```

Note: image classification does not depend on `origin`, so passing `origin=""` is
safe — the link buckets it produces are simply ignored here.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reports.py::test_image_issues_sheet -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: add deduped image_issues sheet"
```

---

## Task 6: Update `write_summary` to count the three sheets

**Files:**
- Modify: `spider/reports.py:112-127` (the `write_summary` body)
- Test: `tests/test_reports.py` (replace `test_summary_written`)

- [ ] **Step 1: Replace the summary test**

In `tests/test_reports.py`, replace the existing `test_summary_written` with:

```python
def test_summary_counts_three_sheets(tmp_path):
    from spider.reports import write_summary
    conn = seeded(tmp_path)
    out = tmp_path / "summary.txt"
    write_summary(conn, str(out), {"report_code": "E-20260605",
                                   "client": "E", "domain": "e.com",
                                   "start_url": "https://e.com",
                                   "origin": "https://e.com",
                                   "started": "2026-06-05T09:00",
                                   "finished": "2026-06-05T09:05",
                                   "resumed": False})
    text = out.read_text(encoding="utf-8")
    assert "E-20260605" in text
    # internal: 2 redirected (old on P1+P2) + 1 broken (gone) = 3 rows
    assert "Internal link issues:" in text
    assert "broken 1" in text and "redirected 2" in text
    # external: pinterest(geo) + linkedin + semantica = 3 distinct; 1 broken, 2 redirects, 1 geo
    assert "External problems:" in text
    assert "redirects 2" in text
    assert "geo 1" in text
    # images: 1 broken + 1 missing alt
    assert "Image issues:" in text
    assert "missing alt 1" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports.py::test_summary_counts_three_sheets -v`
Expected: FAIL — current summary writes the old `N  Broken Link` format, missing the new section labels.

- [ ] **Step 3: Rewrite `write_summary`**

Replace the body of `write_summary` in `spider/reports.py` (keep the signature
`def write_summary(conn, path, meta)`) with:

```python
def write_summary(conn, path: str, meta: dict) -> None:
    internal, (ext, ext_order), (img, img_order) = _collect(conn, meta["origin"])
    pages = list(iter_pages(conn))

    int_broken = sum(1 for r in internal if r[0] == "Broken Link")
    int_redir = sum(1 for r in internal if r[0] == "Redirected")
    ext_broken = sum(1 for k in ext_order if ext[k]["verdict"] == "broken")
    ext_redir = sum(1 for k in ext_order if ext[k]["verdict"] == "redirected")
    ext_geo = sum(1 for k in ext_order if ext[k]["note"] == "geo-redirect")
    img_broken = sum(1 for k in img_order if k[0] == "Broken Image")
    img_alt = sum(1 for k in img_order if k[0] == "Missing Alt")

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
        f.write(f"  Internal link issues:  {len(internal):6d}  "
                f"(broken {int_broken}, redirected {int_redir})\n")
        f.write(f"  External problems:     {len(ext_order):6d}  "
                f"(broken {ext_broken}, redirects {ext_redir}; of which geo {ext_geo})\n")
        f.write(f"  Image issues:          {len(img_order):6d}  "
                f"(broken {img_broken}, missing alt {img_alt})\n")
```

- [ ] **Step 4: Run the full reports suite**

Run: `python -m pytest tests/test_reports.py -v`
Expected: PASS — all tests, including `test_summary_counts_three_sheets`. (The old `test_summary_written` no longer exists; `test_link_issues_classification` and the page-audit tests still pass, since `write_link_issues` is unchanged.)

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: summary reports counts for the three curated sheets"
```

---

## Task 7: Wire the new writers into the CLI

**Files:**
- Modify: `spider/cli.py:15` (import) and `spider/cli.py:40-50` (`_write_all`)
- Test: `tests/test_cli.py` (add a smoke assertion if the existing tests exercise `_write_all`; otherwise rely on the reports suite)

- [ ] **Step 1: Update the import**

In `spider/cli.py`, change line 15 from:

```python
from spider.reports import write_link_issues, write_page_audit, write_summary
```

to:

```python
from spider.reports import (write_link_issues, write_page_audit, write_summary,
                            write_internal_link_issues, write_external_link_summary,
                            write_image_issues)
```

- [ ] **Step 2: Call the new writers in `_write_all`**

In `spider/cli.py`, inside `_write_all`, add the three calls right after the existing
`write_link_issues(...)` line (line 42), so the block reads:

```python
    write_page_audit(conn, os.path.join(run_dir, "page_audit.csv"))
    write_link_issues(conn, os.path.join(run_dir, "link_issues.csv"))
    write_internal_link_issues(conn, os.path.join(run_dir, "internal_link_issues.csv"), origin)
    write_external_link_summary(conn, os.path.join(run_dir, "external_link_summary.csv"), origin)
    write_image_issues(conn, os.path.join(run_dir, "image_issues.csv"))
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS — entire suite green.

- [ ] **Step 4: Regenerate reports for the existing Washington Parent crawl and eyeball them**

The latest crawl DB already exists, so reports can be regenerated without re-crawling:

```bash
python -c "from spider.store import connect; from spider.reports import write_internal_link_issues, write_external_link_summary, write_image_issues; d='runs/washingtonparent.com/2026-06-04_1846'; c=connect(d+'/crawl.db'); print('internal', write_internal_link_issues(c, d+'/internal_link_issues.csv', 'https://washingtonparent.com')); print('external', write_external_link_summary(c, d+'/external_link_summary.csv', 'https://washingtonparent.com')); print('images', write_image_issues(c, d+'/image_issues.csv'))"
```

Expected: three counts printed; `external` should be a few hundred (deduped),
`internal` in the low thousands, `images` deduped. Spot-check
`external_link_summary.csv`: the Pinterest row should be a single `geo-redirect` line
with a high `Pages Affected`, and no `washingtonparent.com` targets should appear in it.

- [ ] **Step 5: Commit**

```bash
git add spider/cli.py
git commit -m "feat: emit internal/external/image sheets from the crawl CLI"
```

---

## Task 8: Update README and finish the branch

**Files:**
- Modify: `README.md` (the section describing run outputs, if present)

- [ ] **Step 1: Document the new outputs**

In `README.md`, find where `link_issues.csv` / `page_audit.csv` outputs are described
and add the three new files with one-line descriptions:

```markdown
- `internal_link_issues.csv` — on-site broken links and redirects to fix (full detail: page ID + hops).
- `external_link_summary.csv` — deduped off-site link problems, one row per problem, with geo-redirect flagging.
- `image_issues.csv` — deduped broken images and missing alt text.
- `link_issues.csv` — raw per-occurrence audit trail (all of the above, ungrouped).
```

(If the README has no outputs section, add a short "Run outputs" subsection listing all
the CSVs.)

- [ ] **Step 2: Run the full suite one last time**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: describe the curated link/image report sheets"
```

---

## Self-Review Notes (for the implementer)

- The raw `link_issues.csv`, `write_link_issues`, `_issue_rows`, and `write_page_audit` are intentionally **unchanged** — do not modify them.
- `_collect` is the single source of truth: all three writers and the summary consume it, so their numbers always agree.
- `get_status` returns the status code already coerced (`int` for numeric, `str` like `"ERR:ConnectError"` otherwise); `str(code)` in rows renders both correctly.
- Image issues deliberately exclude redirected images (only Broken Image + Missing Alt), matching the spec.
