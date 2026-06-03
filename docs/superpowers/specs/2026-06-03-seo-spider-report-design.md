# SEO Spider Report — Design

**Date:** 2026-06-03
**Author:** TurboPress (Barry)
**Status:** Approved for planning

## Purpose

An internal tool for TurboPress to audit WordPress client sites and produce a
"spider report" — a CSV of technical SEO issues a client can act on. Replaces
paying for SEMrush/Ahrefs for the issue-identification use case (no keyword
tracking needed). Runs on monthly intervals. Internal now; productizable later.

This is **script 1 of two**. Script 2 (a later, separate effort) is a content
report covering body-content quality. The two share a single crawl — script 2
reads the same store with no re-crawl.

## Non-goals

- No keyword tracking, rank tracking, or backlink analysis.
- No customer-facing UI. Output is a CSV (a polished `.xlsx` is a later step).
- No JavaScript rendering / Browserless / headless browser — targets are
  server-rendered WordPress, so `httpx` + `lxml` sees everything.
- No N8N, no Docker for now. (N8N could later *call* the script as a scheduler;
  Docker only earns its place if runs move to the NetCup box or productize.)

## Architecture

One script, three stages:

```
   crawl once  ──►  SQLite store  ──►  report generators
   (httpx/lxml)     (crawl.db)         ├─ spider report  (this project)
                                       └─ content report (script 2, later)
```

The crawl populates a local SQLite store. The spider report reads that store to
emit a CSV. The store is also the resume mechanism and the source script 2 will
later read. **Split the reporting, not the crawl** — crawling a site once avoids
doubling load on the client's server.

### Run layout

Each run is a dated folder, so monthly runs accumulate and stay comparable:

```
runs/
  <domain>/
    2026-06-03_0900/
      crawl.db            # crawl state + store (resumable; script-2 source)
      spider_issues.csv   # the report; date carried in the folder name
      summary.txt         # domain, started/finished timestamps, page + issue counts
```

The CSV stays clean (no metadata header rows that would break spreadsheet
import). Full timestamps live in `summary.txt` and in the folder name.

## Crawl

Keep the existing async core: `httpx.AsyncClient` + `lxml`/BeautifulSoup,
sitemap-seeded frontier (recursing nested sitemap indexes), `Semaphore`-bounded
concurrency (default 10), HEAD-first status checks with a GET fallback for
servers that mishandle HEAD (403/405/501), and a per-resource status cache so
each URL is checked once. User-Agent stays `TurboPress-Audit/1.0`.

Changes:

1. **Live progress.** Print `crawled N / queue M` during the crawl and
   `checked N / total` during the final link/image status sweep. Fixes the
   "looks frozen, gets Ctrl+C'd" problem that motivated this work.
2. **Redirect capture.** Currently `follow_redirects=True` swallows the chain.
   Read `r.history` (intermediate redirects → hop count) and `r.url` (final
   destination) so 3xx is reported distinctly from a hard 4xx/5xx.
3. **Graceful Ctrl+C.** Catch `KeyboardInterrupt`, commit the DB, exit cleanly,
   and print "run with `--resume` to continue." No traceback.
4. **Open Graph capture.** In the same parse pass that reads `<title>` and
   `meta description`, record which core OG tags are present
   (`og:title`, `og:description`, `og:image`, `og:type`, `og:url`) and add the
   `og:image` URL to the resource set so it rides the existing status sweep.

## Store (SQLite, stdlib `sqlite3`)

A single `crawl.db` per run holds:

- **pages** — url, status, title, description, present OG tags, (cheap structural
  fields captured for script 2 even though the spider report ignores them).
- **links** — (found_on, target).
- **images** — (found_on, src), and missing-alt flag.
- **status_cache** — url → final code, hop count, final destination URL.
- **frontier / visited** — so a kill never loses progress.

Committed periodically (e.g. every batch) using WAL mode. Because the crawl is
single-threaded asyncio, one connection with periodic commits is sufficient.

### Resume semantics

- **No flags = fresh crawl, every time.** Even if an incomplete run exists,
  default starts a new dated run folder. Correct for monthly cadence — the site
  changed, you want current data.
- **`--resume` = opt-in.** Finds the latest incomplete run for the domain and
  drains the remaining frontier, skipping already-visited URLs (dedup is
  automatic — they are marked visited in the DB).

There is exactly one flag (`--resume`); fresh is the default. The existing
optional positional `max_pages` argument is retained.

## Spider report (CSV)

Single `spider_issues.csv`. Existing columns retained:
`Issue Type, URL, Detail, Found On`.

Issue types:

| Issue Type | Trigger | Detail field |
|---|---|---|
| Broken link | link target 4xx/5xx or connection error | `status <code>` |
| Broken image | image src 4xx/5xx or error | `status <code>` |
| Redirect | link/image target is 3xx | `<hops> hop(s) -> <final URL>` |
| Missing alt text | `<img>` with empty/absent `alt` | `no alt attribute` |
| Missing meta description | 200 page, empty/absent meta description | `empty or absent` |
| Duplicate title | `<title>` shared by >1 page | `shared by N pages` |
| Duplicate meta description | description shared by >1 page | `shared by N pages` |
| Missing Open Graph tags | 200 page missing ≥1 core OG tag | `missing: og:image, og:type` (one row per page) |
| Broken og:image | `og:image` URL 4xx/5xx | `status <code>` |

Notes:

- **Missing-OG is one row per page** with the missing tags listed in Detail —
  not a row per tag — so a site with no OG setup does not explode into thousands
  of rows.
- **Redirects** are reported as their own type, separate from hard breakage.
- On Ctrl+C, the report is written from whatever the store contains so far.

## Deferred to script 2 (content report)

Captured-or-recomputable from the same `crawl.db`, surfaced later, not now:

- Missing H1, full H1/H2/H3 hierarchy, multiple-H1.
- Duplicate / thin content (paragraph-level, replacing the current exact-hash
  dedup, which is removed from script 1).
- Canonical reporting and canonical-correctness.

## Deferred further / explicitly out

- Twitter/X Card tags (`twitter:card`, `twitter:image`, …) — same pattern as OG;
  code structure makes them a one-line add later, but modern themes fall back to
  OG, so not now.
- Polished multi-tab `.xlsx` deliverable (Overview + per-category tabs),
  generated *from* the CSV — a later presentation layer.
- Month-over-month diffing (new vs resolved issues) — enabled by the dated-run
  layout, but not built now.
- Scheduling (cron / N8N calling the script) and Docker packaging.

## Testing

- Unit: URL normalisation, `same_site`/`reg_netloc`, redirect-chain parsing from
  a synthetic `r.history`, OG-presence detection, missing-alt detection, the
  duplicate-grouping logic.
- Integration: a small local fixture site (handful of HTML files served by a
  test server) exercising each issue type end-to-end, including a known redirect
  chain and a known broken og:image.
- Resume: crawl a fixture, interrupt mid-frontier, `--resume`, assert no URL is
  re-fetched and the final issue set matches an uninterrupted run.
```