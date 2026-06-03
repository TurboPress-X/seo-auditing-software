# TurboPress SEO Spider

Internal SEO auditing tool. Crawls a (WordPress) site politely and produces a
spider report of technical issues for a client to fix.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## Run

```powershell
.venv\Scripts\python seo_audit.py https://example.com --client "Client Name"
```

- `--client` matches the Xero invoicing account name (uppercased for codes).
- `--max-pages N` caps the crawl (default 5000).
- `--resume` continues the latest interrupted crawl for that domain instead of
  starting fresh. With no flags, every run is a fresh crawl.
- If the URL or `--client` is omitted, you'll be prompted.

Output lands in `runs/<domain>/<date>_<time>/`:
`crawl.db`, `page_audit.csv`, `link_issues.csv`, `summary.txt`.

## Output

- **page_audit.csv** — one row per page (HTTP 200) with at least one issue:
  missing/duplicate title or meta description, missing/broken Open Graph, missing
  canonical. Each page has a stable Page ID for month-over-month tracking.
- **link_issues.csv** — broken links, broken images, and redirects (with hop
  count and final destination), joined back to pages via Page ID.
- **summary.txt** — report code, timestamps, page count, and issue tallies.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Scope

This is the **spider report** (script 1). A later **content report** (script 2)
will cover H1/H2/H3 structure, duplicate/thin content, and canonical correctness.
