# Link-issue reporting: split into curated sheets + suppress false positives

**Date:** 2026-06-05
**Status:** Approved design, ready for implementation plan
**Area:** `spider/reports.py`, `spider/parse.py`, `spider/cli.py`, `tests/`

## Problem

The single `link_issues.csv` conflates four different things, producing ~55,520 rows
for washingtonparent.com where only a few thousand are actionable:

- **Geo-redirects from the crawl location.** The crawler runs from South Africa, so
  external sites bounce to their regional edge (`pinterest.com/washparent` →
  `za.pinterest.com/washparent`). A US visitor never sees this. One such link =
  5,468 rows.
- **External functional links treated as the site's faults.** Share buttons
  (`facebook.com/sharer.php`, `linkedin.com/shareArticle`, `pinterest.com/pin/create`,
  `tumblr.com/share`, `mailto:`) redirect or break by design — the site owner cannot
  fix them.
- **Site-wide template links counted once per page.** The broken footer credit link
  `washingtonparent.semantica.co.za` (a ConnectError) is one defect but appears 2,735
  times; 11,690 rows are exact byte-duplicates from share bars rendered twice per page.
- **Parse-layer garbage.** Elementor emits placeholder images as
  `src="image/svg+xml;base64,…"` (missing the `data:` prefix), which the crawler
  resolves as a *relative* URL → `washingtonparent.com/image/svg+xml;base64,…` and
  reports as 1,675 bogus broken images. A stray `OnsiteSearch…` target is likewise
  decoded out of inline base64 ad script.

Measured shape: 55,520 rows collapse to 12,988 distinct `(issue, target)` pairs.
Internal vs external split: external targets account for 19,670 redirects + 10,801
broken; internal for 11,329 redirects + 171 broken.

## Goals

Fix at the source (the report layer), so every client/run benefits. Produce
purpose-built sheets that match how the data is used: **external = awareness
(summary), internal = worklist (full detail), images = media workflow.**

## Non-goals

- `page_audit.csv` is untouched (separate title/desc/OG/canonical audit).
- No change to crawl/egress location; geo-redirects are *flagged*, not eliminated
  upstream.

## Outputs

The report invocation in `cli.py` (`_write_all`) emits, in addition to the unchanged
`page_audit.csv` and `summary.txt`:

### 1. `link_issues.csv` — raw audit trail (UNCHANGED)
Existing per-occurrence output, kept as-is for drill-down. `crawl.db` remains the full
source of truth.

### 2. `internal_link_issues.csv` — worklist, full detail
- **Scope:** `<a href>` targets where `is_internal(host)` is true. Both Broken
  (4xx/5xx/`ERR:*`) and Redirected (hops > 0).
- **Granularity:** per-occurrence, with exact byte-duplicate rows dropped (same
  `(Issue Type, Found On Page ID, Target URL, Status, Destination, Hops)` appearing
  twice → one row).
- **Columns:** `Issue Type, Found On (Page ID), Found On URL, Target URL, Status Code,
  Redirect Destination, Hops`.

### 3. `external_link_summary.csv` — smart, deduped
- **Scope:** `<a href>` targets where `is_internal(host)` is false. Both Broken and
  Redirected.
- **Granularity:** one row per `(Target URL, Status Code, Destination)`.
- **Columns:** `Status Code, Target URL, Destination URL, Pages Affected, Example Page,
  Note`.
  - `Pages Affected` = distinct count of `Found On URL` values for that group.
  - `Example Page` = one representative `Found On URL`.
  - `Note` = `geo-redirect` when `is_geo_redirect(target, destination)`; blank
    otherwise.
  - Broken rows have an empty `Destination URL`; Status Code disambiguates
    (`200` + destination = redirect; `4xx`/`ERR:*` + empty = broken).

### 4. `image_issues.csv` — content media, deduped
- **Scope:** Broken Image + Missing Alt, every host (no internal/external split — the
  site's own media is served via the `i0.wp.com` Photon CDN, so a host split would
  misclassify it).
- **Excluded as non-content / false positives** (skipped in `_collect`'s image loop,
  before classification):
  - **Data-URI srcs** (`_is_data_uri`): inline placeholders such as Elementor's
    prefix-less `image/svg+xml;base64,…`, which the crawler otherwise resolves to a
    bogus on-site URL that 404s. The parse-layer guard stops these entering *new*
    crawls; this report-layer skip also cleans reports regenerated from a `crawl.db`
    captured before that fix.
  - **Decorative images** (`_is_decorative_image`): gravatar avatars
    (`*.gravatar.com`) and known tracking pixels (`stats.wp.com`, `pixel.wp.com`) —
    not content, carry no meaningful alt text. Real content images (wp-content
    uploads, on-site `/images/…`, Photon CDN uploads) are NOT excluded.
- **Granularity:** one row per `(Issue Type, Image URL)`.
- **Columns:** `Issue Type, Image URL, Status Code, Pages Affected, Example Page`
  (Status Code blank for Missing Alt).

## Helper rules (dependency-free)

Both derive what they need from the crawl `origin` (e.g. `https://washingtonparent.com`).

### `is_internal(host, origin_host)`
`host == origin_host or host.endswith("." + origin_host)`. So `www.` and `picks.`
subdomains are internal; `za.pinterest.com`, `i0.wp.com`, `semantica.co.za` are
external. `origin_host` is `urlparse(origin).netloc.lower()`.

### `is_geo_redirect(target_url, destination_url)`
True when the destination host equals the target host (or the target's last two labels)
with a 2-letter country label prepended. Examples that flag:
`pinterest.com` → `za.pinterest.com`; `www.pinterest.com` → `za.pinterest.com`.
Does **not** flag `linkedin.com/shareArticle` → `linkedin.com/uas/login` (same host, no
country prefix) or `facebook.com/sharer.php` → `facebook.com/share_channel` (path-only
change). Algorithm:
- `t = urlparse(target).netloc.lower()`, `d = urlparse(destination).netloc.lower()`
- strip a leading `www.` from `t`
- if `d` splits as `<label>.<rest>` where `label` is exactly 2 ASCII letters and
  (`rest == t` or `rest == last_two_labels(t)`), return True.

## Parse-layer guard (`spider/parse.py`)

In `parse_page`, before `_abs(page_url, ref)` is called for both `<a href>` and
`<img src>`/`data-src`, skip refs that are data URIs:

```python
def _is_data_uri(ref: str) -> bool:
    r = ref.strip().lower()
    return r.startswith("data:") or ";base64," in r
```

- For `<a>`: extend the existing skip (`not href or href.startswith("#")`) with
  `or _is_data_uri(href)`.
- For `<img>`: after resolving `src = img.get("src") or img.get("data-src")`, skip when
  `_is_data_uri(src)` (guard on the raw value, since the Elementor case
  `image/svg+xml;base64,…` would otherwise be turned into an absolute URL by `_abs`).

This removes the 1,675 bogus broken images and the base64-decoded stray targets at the
source, for every page.

## Code changes

- **`spider/parse.py`:** add `_is_data_uri`, apply in both loops as above.
- **`spider/reports.py`:**
  - Add `is_internal` and `is_geo_redirect` helpers (or import `is_internal` from
    `normalize.py` if a host helper already fits there).
  - Refactor `_issue_rows` (or add a sibling) so issues are bucketed into
    internal-link / external-link / image streams rather than one flat stream.
  - Add writers: `write_internal_link_issues(conn, path, origin)`,
    `write_external_link_summary(conn, path, origin)`, `write_image_issues(conn, path)`.
  - Keep `write_link_issues` (raw) and `write_page_audit`.
  - Update `write_summary` to count per new sheet (see below).
- **`spider/cli.py`:** in `_write_all`, call the three new writers with the output
  paths and `origin`.

### `summary.txt` counts (updated section)
```
Link & image issues:
  Internal link issues:   <rows>   (broken <n>, redirected <n>)
  External problems:      <distinct> (broken <n>, redirects <n>; of which geo <n>)
  Image issues:           <distinct> (broken <n>, missing alt <n>)
```

## Testing (TDD, `tests/test_reports.py`)

- `is_internal`: apex, `www.`/`picks.` subdomain (internal); `za.pinterest.com`,
  `i0.wp.com`, `semantica.co.za` (external).
- `is_geo_redirect`: `pinterest.com`→`za.pinterest.com` and `www.`-prefixed variant
  flag; LinkedIn login and Facebook share-channel do not.
- External summary: N occurrences of one redirect across N pages → 1 row,
  `Pages Affected == N`, `Example Page` is one of them, `Note == "geo-redirect"` for the
  Pinterest case.
- External broken: kept with empty `Destination URL`.
- Internal redirect: kept with `Found On (Page ID)`, `Redirect Destination`, `Hops`.
- Internal byte-duplicate: same row twice on a page → one row out.
- Image issues: deduped per `(Issue Type, src)`; Missing Alt has blank Status; Broken
  Image has Status populated; CDN-host image stays on the sheet (no external split).
- Parse guard (`tests/test_parse.py`): `<img src="image/svg+xml;base64,…">` and
  `<a href="data:…">` produce no link/image entries.
- `write_summary`: counts match the three sheets.

## Migration / compatibility

Additive: the raw `link_issues.csv` keeps its name and format, so any existing
downstream consumers are unaffected. New files appear alongside it.
