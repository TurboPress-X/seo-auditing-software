# SEO Spider Report — Design

**Date:** 2026-06-03
**Author:** TurboPress (Barry)
**Status:** Approved for planning

## Purpose

An internal tool for TurboPress to audit WordPress client sites and produce a
"spider report" — CSVs of technical SEO issues a client can act on. Replaces
paying for SEMrush/Ahrefs for the issue-identification use case (no keyword
tracking needed). Runs on monthly intervals. Internal now; productizable later.

This is **script 1 of two**. Script 2 (a later, separate effort) is a content
report covering body-content quality (H1/H2/H3 hierarchy, duplicate/thin content,
canonical correctness). Script 2 may reuse this crawl's store or run its own
crawl — we do **not** pre-capture content fields now.

## Non-goals

- No keyword tracking, rank tracking, or backlink analysis.
- No customer-facing UI. Output is CSVs (a polished `.xlsx` is a later step).
- No JavaScript rendering / Browserless / headless browser — targets are
  server-rendered WordPress, so `httpx` + `lxml` sees everything.
- No N8N, no Docker for now. (N8N could later *call* the script as a scheduler;
  Docker only earns its place if runs move to the NetCup box or productize.)

## Architecture

One script, three stages:

```
   crawl once  ──►  SQLite store  ──►  report generators
   (httpx/lxml)     (crawl.db)         ├─ Page Audit      (page_audit.csv)
                                       └─ Link & Image    (link_issues.csv)
```

The crawl populates a local SQLite store. Two report generators read that store
to emit two CSVs. The store is also the resume mechanism. **Split the reporting,
not the crawl** — crawling a site once avoids doubling load on the client's
server.

### Run layout

Each run is a dated folder, so monthly runs accumulate and stay comparable:

```
runs/
  <domain>/                      # domain lowercased, e.g. washingtonparent.com
    2026-06-03_0900/
      crawl.db                   # crawl state + store (resumable)
      page_audit.csv             # Sheet 1
      link_issues.csv            # Sheet 2
      summary.txt                # report code, timestamps, page + issue counts
```

The CSVs stay clean (no metadata header rows that would break spreadsheet
import). Full context lives in `summary.txt` and the folder name.

## Identifiers

### Client code
- Supplied via `--client`, intended to match the TurboPress **Xero invoicing
  account name**.
- Normalised to **UPPERCASE** and slugified for filesystem/ID safety
  (whitespace → `-`, drop other unsafe chars). e.g. `Washington Parent` →
  `WASHINGTON-PARENT`.

### Report code
- `<CLIENT>-<YYYYMMDD>`, e.g. `WASHINGTON-PARENT-20260603`.
- Identifies *this run*. Recorded in `summary.txt` and the folder name. **The
  date belongs here, not in the Page ID.**

### Page ID
- `<CLIENT>-<hash8>` where `hash8` = first 8 hex chars of a hash of the page's
  **normalised identity URL** (see Normalisation). e.g. `WASHINGTON-PARENT-3f9a1c2e`.
- **Deterministic and stable across months** — the same page yields the same
  Page ID every run. This is what makes month-over-month new/lost-page tracking
  possible (diff the Page-ID sets of two reports — that diff report is future
  work, but the ID scheme enables it).
- The date must **not** appear in the Page ID, or it would change every month
  and break tracking.

### Prompting
- If `--client` is absent, or the start URL is absent, the script prompts for it
  interactively (`input()`).
- Prompting only fires when an argument is missing, so a future scheduled run
  that passes both arguments never blocks.

## URL normalisation

Two distinct uses of a URL, treated differently on purpose:

### Identity (dedup, visited set, Page-ID hash) — normalise aggressively
So `/about`, `/about/`, `http://…/about`, and `www.…/about` all collapse to one
page with one Page ID (otherwise the page is crawled twice and gets two IDs):

1. On startup, resolve the start URL's redirects to learn the site's real
   **canonical origin** (e.g. the site forces `https://` + non-`www`). Adopt
   that origin as the normalisation basis.
2. For every internal URL: lowercase scheme + host, unify www/non-www to the
   canonical origin, strip fragment, strip trailing slash (except root), keep
   the query string.

### Link checking (Sheet 2) — preserve the URL as written
Check links exactly as they appear in the HTML. If an internal link points to
`http://` or a trailing-slash URL that 301s to the canonical, that is a **real
finding** ("internal links redirect") and surfaces as a `Redirected` row —
wasted crawl budget / link equity the client wants flagged.

**Rule:** collapse for *identity*, preserve for *checking*.

## Crawl

Keep the existing async core: `httpx.AsyncClient` + `lxml`/BeautifulSoup,
sitemap-seeded frontier (recursing nested sitemap indexes), `Semaphore`-bounded
concurrency (default 10), HEAD-first status checks with a GET fallback for
servers that mishandle HEAD (403/405/501), and a per-resource status cache so
each URL is checked once. User-Agent stays `TurboPress-Audit/1.0`.

Changes:

1. **Live progress.** Print `crawled N / queue M` during the crawl and
   `checked N / total` during the link/image status sweep. Fixes the "looks
   frozen, gets Ctrl+C'd" problem that motivated this work.
2. **Redirect capture.** `follow_redirects=True` currently swallows the chain.
   Read `r.history` (hop count) and `r.url` (final destination). Classify by
   **final** status: final ≥ 400 → Broken; final 200 with ≥1 hop → Redirected;
   final 200, 0 hops → not reported.
3. **Graceful Ctrl+C.** Catch `KeyboardInterrupt`, commit the DB, write reports
   from what exists so far, exit cleanly, print "run with `--resume` to
   continue." No traceback.
4. **Per-page capture** (same parse pass): `<title>` presence, meta description
   presence + value (for duplication grouping), core Open Graph tags present
   (`og:title`, `og:description`, `og:image`, `og:type`, `og:url`), the
   `og:image` URL (added to the resource status sweep), and `<link
   rel="canonical">` presence.

## Store (SQLite, stdlib `sqlite3`)

A single `crawl.db` per run holds only what the spider report needs:

- **pages** — page id, identity url, display url, status, title (presence/value),
  description (presence/value), og-tag presence flags, og:image url, canonical
  presence.
- **links** — (found_on page id, found_on url, target url as written).
- **images** — (found_on page id, found_on url, src as written, alt-missing flag).
- **status_cache** — url → final code, hop count, final destination url.
- **frontier / visited** — so a kill never loses progress.

Committed periodically (per batch), WAL mode. Single-threaded asyncio → one
connection with periodic commits is sufficient.

### Resume semantics
- **No flags = fresh crawl, every time.** Even if an incomplete run exists,
  default starts a new dated run folder. Correct for monthly cadence.
- **`--resume`** finds the latest incomplete run for the domain and drains the
  remaining frontier, skipping already-visited URLs (dedup automatic via the
  visited table).

### CLI
`argparse`-based:
- positional `url` (optional; prompt if absent)
- `--client CODE` (optional; prompt if absent; uppercased + slugified)
- `--max-pages N` (default 5000)
- `--resume`

## Output

### Sheet 1 — `page_audit.csv`
One row per crawled page **that has ≥1 issue** (clean pages omitted, so it stays
short on large sites).

| Column | Values |
|---|---|
| Page ID | `WASHINGTON-PARENT-3f9a1c2e` |
| URL | page URL (display form) |
| Status Code | e.g. `200` |
| Meta Title? | `OK` / `Missing` |
| Title Duplicated? | `No` / `Yes (N)` |
| Meta Description? | `OK` / `Missing` |
| Meta Duplicated? | `No` / `Yes (N)` |
| Open Graph? | `OK` / `Missing: og:image` / `Broken og:image` |
| Canonical? | `OK` / `Missing` |

(`Title Duplicated?` is an addition beyond the original sketch — duplicate titles
is a standard SEO issue we already compute. Drop if unwanted.)

### Sheet 2 — `link_issues.csv`
One row per broken/redirected link or image. References Sheet 1 via Page ID.

| Column | Values |
|---|---|
| Issue Type | `Broken Link` / `Broken Image` / `Redirected` |
| Found On (Page ID) | Page ID of the page containing the link |
| Found On URL | URL of that page |
| Target URL | the link/image target, as written |
| Status Code | final status (e.g. `404`, `301`) |
| Redirect Destination | final URL (Redirected rows only) |
| Hops | redirect hop count (Redirected rows only) |

Empty cells in the redirect columns on Broken rows are expected and acceptable.

### `summary.txt`
Report code, client, domain, start URL, canonical origin resolved, crawl
started/finished timestamps, whether resumed, pages crawled, resources checked,
and per-category issue counts.

## Deferred to script 2 (content report)
- Missing H1, full H1/H2/H3 hierarchy, multiple-H1.
- Duplicate / thin content (paragraph-level; replaces the current exact-hash
  dedup, which is removed from script 1).
- Canonical *correctness* (does it point to the right URL) — script 1 only
  checks canonical *presence*.

## Deferred further / explicitly out
- Twitter/X Card tags — same pattern as OG; one-line add later. Not now.
- Polished multi-tab `.xlsx` (Overview + per-sheet tabs) generated from the CSVs.
- Month-over-month diff report (new vs lost pages) — enabled by stable Page IDs.
- Scheduling (cron / N8N) and Docker packaging.

## Testing
- Unit: identity normalisation (trailing slash, http/https, www/non-www collapse),
  canonical-origin resolution, redirect-chain parsing from synthetic `r.history`,
  final-status classification (broken vs redirected vs ok), OG presence detection,
  missing-alt detection, duplicate title/description grouping, Page-ID stability
  (same URL → same ID across runs), client-code slugification.
- Integration: a small local fixture site (HTML served by a test server)
  exercising each issue type end-to-end, including a known redirect chain, a
  broken og:image, and trailing-slash/www variants that must collapse to one page.
- Resume: crawl a fixture, interrupt mid-frontier, `--resume`, assert no URL is
  re-fetched and the final issue set matches an uninterrupted run.
```