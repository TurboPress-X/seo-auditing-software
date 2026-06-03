# SEO Spider

A small, polite command-line crawler that audits a website for common technical
SEO problems and writes plain-CSV reports you can hand to a client or open in a
spreadsheet. Built for server-rendered sites (WordPress and similar), so no
headless browser is required.

It finds:

- **Broken links** (4xx/5xx) and **broken images**
- **Redirects** (with hop count and final destination)
- **Missing or duplicate** `<title>` and `meta description`
- **Missing or broken Open Graph** tags (`og:title`, `og:description`,
  `og:image`, `og:type`, `og:url`)
- **Missing canonical** tags
- **Images missing `alt` text** (accessibility + SEO)

Each page gets a **stable ID** (`CLIENT-<hash>`) derived from its normalised URL,
so reports from different months can be compared to spot new or lost pages.

## Why it's polite

This tool is designed not to hammer a server:

- Respects **`robots.txt`**: disallowed paths aren't crawled, and a
  `Crawl-delay` directive switches the crawler to one request at a time, spaced
  by the requested delay.
- Bounded concurrency (10 requests at a time by default).
- Seeds from the site's **sitemap** when available, so it doesn't brute-force URLs.
- Checks each unique link/image only once, HEAD-first with a GET fallback.

> ⚠️ **Only scan sites you own or are explicitly authorized to audit.** Crawling
> a site you don't have permission to test may breach its terms of service or
> local law. You are responsible for how you use this tool.

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

## Usage

```bash
python seo_audit.py https://example.com --client "Example Co"
```

- `--client` is a short name/code for the site being audited; it's uppercased and
  used in report codes and the output folder. If omitted (along with the URL),
  you'll be prompted.
- `--max-pages N` caps the crawl (default 5000).
- `--resume` continues the most recent interrupted crawl for that domain instead
  of starting over. With no flags, **every run is a fresh crawl**, handy for
  monthly re-audits.

Output is written to `runs/<domain>/<date>_<time>/`:

| File | Contents |
|------|----------|
| `page_audit.csv` | One row per page (HTTP 200) with at least one issue: missing/duplicate title or meta description, missing/broken Open Graph, missing canonical. |
| `link_issues.csv` | Broken links, broken images, redirects (with hop count + final destination), and images missing `alt` text, joined back to pages via Page ID. |
| `summary.txt` | Report code, timestamps, resolved canonical origin, pages crawled, and issue tallies. |
| `crawl.db` | SQLite store of the crawl (also what makes `--resume` possible). |

The crawl outputs are git-ignored, so the sites you audit never end up in version
control.

## How resume works

The crawl state lives in `crawl.db`. A URL only leaves the queue once its page has
been saved, so if you stop the run (Ctrl+C, or just close the laptop), `--resume`
picks up exactly where it left off without re-crawling finished pages or losing
in-flight ones.

## Tests

Install the dev dependencies (adds `pytest`), then run the suite:

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt   # Windows
.venv\Scripts\python -m pytest

.venv/bin/python -m pip install -r requirements-dev.txt       # macOS / Linux
.venv/bin/python -m pytest
```

## Roadmap

This is the **spider report**. A planned companion **content report** will cover
H1/H2/H3 heading structure, duplicate/thin content, and canonical *correctness*
(not just presence), reading the same `crawl.db` without re-crawling.

## License

[MIT](LICENSE) © 2026 Barry van Biljon

## Author

Built by **Barry van Biljon**. Contact [barry@turbopress.pro](mailto:barry@turbopress.pro)
or visit [www.turbopress.pro](https://www.turbopress.pro)
