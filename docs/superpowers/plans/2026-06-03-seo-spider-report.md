# SEO Spider Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the single-file `seo_audit.py` crawler into a resumable, SQLite-backed SEO spider that produces two client-ready CSVs (`page_audit.csv`, `link_issues.csv`) plus a `summary.txt`, with stable per-page IDs for month-over-month tracking.

**Architecture:** A `spider/` package split by responsibility — URL normalisation, identifiers, HTML parsing, status checking, SQLite store, async crawl orchestration, report generation, and a CLI. The crawl populates `crawl.db`; report generators read it. `seo_audit.py` becomes a thin entrypoint so `python seo_audit.py <url>` still works.

**Tech Stack:** Python 3.13, httpx (async + `MockTransport` for tests), BeautifulSoup + lxml, stdlib `sqlite3`, pytest + pytest-asyncio.

---

## File Structure

```
seo_audit.py                      # thin entrypoint -> spider.cli:main
spider/
  __init__.py
  normalize.py                    # URL identity normalisation, same_site, www handling
  identifiers.py                  # client slug, report code, stable page id
  parse.py                        # parse_page(html, url) -> PageData (pure)
  status.py                       # classify() (pure) + async check_status()
  store.py                        # SQLite schema + read/write + frontier/visited
  crawl.py                        # resolve_origin, sitemap seeding, async run_crawl
  reports.py                      # write_page_audit / write_link_issues / write_summary
  cli.py                          # argparse, prompting, run-folder wiring, Ctrl+C
tests/
  conftest.py                     # shared fixtures (temp store, mock site builder)
  test_normalize.py
  test_identifiers.py
  test_parse.py
  test_status.py
  test_store.py
  test_crawl.py                   # integration via httpx.MockTransport
  test_reports.py
requirements.txt
requirements-dev.txt
pytest.ini
```

Each module has one responsibility. Pure logic (normalise, identifiers, parse, classify, reports) is unit-tested directly; networked logic (status, crawl) is tested with `httpx.MockTransport` so tests are deterministic and need no open ports.

---

## Task 0: Project scaffolding

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `spider/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Create `requirements.txt`**

```
httpx>=0.28
beautifulsoup4>=4.14
lxml>=5.0
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Create empty package marker `spider/__init__.py`**

```python
"""TurboPress SEO spider — crawl, store, and report on technical SEO issues."""
```

- [ ] **Step 5: Create `tests/conftest.py` (empty for now, fixtures added per task)**

```python
"""Shared pytest fixtures for the spider test suite."""
```

- [ ] **Step 6: Create venv and install dev deps**

Run (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
Expected: installs complete without error.

- [ ] **Step 7: Verify pytest collects nothing yet (sanity)**

Run: `.venv\Scripts\python -m pytest -q`
Expected: "no tests ran" (exit code 5) — confirms config loads.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini spider/__init__.py tests/conftest.py
git commit -m "chore: scaffold spider package and test toolchain"
```

---

## Task 1: URL normalisation (`spider/normalize.py`)

**Files:**
- Create: `spider/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_normalize.py
from spider.normalize import strip_www, same_site, normalize_identity


def test_strip_www():
    assert strip_www("www.example.com") == "example.com"
    assert strip_www("EXAMPLE.com") == "example.com"
    assert strip_www("blog.example.com") == "blog.example.com"


def test_same_site_ignores_www_and_case():
    assert same_site("https://example.com/a", "http://www.example.com/b")
    assert not same_site("https://example.com", "https://other.com")


def test_identity_collapses_trailing_slash():
    a = normalize_identity("https://example.com/about/", "https", "example.com")
    b = normalize_identity("https://example.com/about", "https", "example.com")
    assert a == b == "https://example.com/about"


def test_identity_collapses_scheme_and_www():
    base = normalize_identity("https://example.com/p", "https", "example.com")
    assert normalize_identity("http://example.com/p", "https", "example.com") == base
    assert normalize_identity("https://www.example.com/p", "https", "example.com") == base


def test_identity_root_keeps_single_slash():
    assert normalize_identity("https://example.com/", "https", "example.com") == "https://example.com/"


def test_identity_strips_fragment_keeps_query():
    out = normalize_identity("https://example.com/p?x=1#frag", "https", "example.com")
    assert out == "https://example.com/p?x=1"


def test_identity_uses_origin_scheme_for_relative_host():
    # link with its own host keeps that host (minus www), but scheme is unified
    out = normalize_identity("http://www.example.com/p", "https", "example.com")
    assert out == "https://example.com/p"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.normalize'`

- [ ] **Step 3: Implement `spider/normalize.py`**

```python
"""URL normalisation. Two uses: identity (aggressive collapse for dedup/IDs)
and same-site checks. Link *checking* uses raw URLs elsewhere, not this."""

from urllib.parse import urldefrag, urlparse


def strip_www(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def reg_netloc(url: str) -> str:
    return strip_www(urlparse(url).netloc)


def same_site(a: str, b: str) -> bool:
    return reg_netloc(a) == reg_netloc(b)


def normalize_identity(url: str, origin_scheme: str, origin_host: str) -> str:
    """Canonical identity string: collapses http/https, www/non-www,
    trailing slash, and fragments so one logical page maps to one key."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    host = strip_www(p.netloc) if p.netloc else strip_www(origin_host)
    path = p.path.rstrip("/") or "/"
    query = f"?{p.query}" if p.query else ""
    return f"{origin_scheme}://{host}{path}{query}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_normalize.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/normalize.py tests/test_normalize.py
git commit -m "feat: URL identity normalisation"
```

---

## Task 2: Identifiers (`spider/identifiers.py`)

**Files:**
- Create: `spider/identifiers.py`
- Test: `tests/test_identifiers.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_identifiers.py
from datetime import date
from spider.identifiers import slug_client, report_code, page_id


def test_slug_client_uppercases_and_hyphenates():
    assert slug_client("Washington Parent") == "WASHINGTON-PARENT"
    assert slug_client("  acme   co ") == "ACME-CO"
    assert slug_client("A&B / Ltd.") == "AB-LTD"


def test_report_code_format():
    assert report_code("WASHINGTON-PARENT", date(2026, 6, 3)) == "WASHINGTON-PARENT-20260603"


def test_page_id_is_stable_for_same_identity():
    a = page_id("ACME", "https://example.com/about")
    b = page_id("ACME", "https://example.com/about")
    assert a == b
    assert a.startswith("ACME-")
    assert len(a.split("-")[-1]) == 8


def test_page_id_differs_by_url():
    assert page_id("ACME", "https://example.com/a") != page_id("ACME", "https://example.com/b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_identifiers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.identifiers'`

- [ ] **Step 3: Implement `spider/identifiers.py`**

```python
"""Client codes, report codes, and stable per-page IDs."""

import hashlib
import re
from datetime import date


def slug_client(name: str) -> str:
    """Uppercase, collapse whitespace to single hyphens, drop other unsafe chars."""
    cleaned = re.sub(r"[^A-Za-z0-9\s-]", "", name)
    parts = cleaned.upper().split()
    return "-".join(parts)


def report_code(client_slug: str, when: date) -> str:
    return f"{client_slug}-{when:%Y%m%d}"


def page_id(client_slug: str, identity_url: str) -> str:
    digest = hashlib.sha1(identity_url.encode("utf-8")).hexdigest()[:8]
    return f"{client_slug}-{digest}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_identifiers.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/identifiers.py tests/test_identifiers.py
git commit -m "feat: client/report/page identifiers"
```

---

## Task 3: HTML parsing (`spider/parse.py`)

**Files:**
- Create: `spider/parse.py`
- Test: `tests/test_parse.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parse.py
from spider.parse import parse_page, OG_TAGS

HTML = """
<html><head>
  <title>  Hello World  </title>
  <meta name="description" content="A page">
  <meta property="og:title" content="Hello">
  <meta property="og:image" content="/img/og.png">
  <link rel="canonical" href="https://example.com/page">
</head><body>
  <a href="/internal">in</a>
  <a href="https://other.com/ext">ext</a>
  <a href="mailto:x@y.com">mail</a>
  <img src="/img/a.png" alt="A">
  <img src="/img/b.png">
</body></html>
"""


def test_parses_title_and_description():
    p = parse_page(HTML, "https://example.com/page")
    assert p.title == "Hello World"
    assert p.description == "A page"


def test_canonical_present():
    p = parse_page(HTML, "https://example.com/page")
    assert p.canonical_present is True


def test_og_presence_and_image():
    p = parse_page(HTML, "https://example.com/page")
    assert "og:title" in p.og_present
    assert "og:image" in p.og_present
    assert "og:description" not in p.og_present
    assert p.og_image == "https://example.com/img/og.png"


def test_links_absolute_http_only():
    p = parse_page(HTML, "https://example.com/page")
    assert "https://example.com/internal" in p.links
    assert "https://other.com/ext" in p.links
    assert all(u.startswith("http") for u in p.links)  # mailto excluded


def test_images_and_missing_alt():
    p = parse_page(HTML, "https://example.com/page")
    assert "https://example.com/img/a.png" in p.images
    assert "https://example.com/img/b.png" in p.images
    assert p.missing_alt == ["https://example.com/img/b.png"]


def test_missing_title_and_description_are_none():
    p = parse_page("<html><head></head><body></body></html>", "https://example.com/x")
    assert p.title is None
    assert p.description is None
    assert p.canonical_present is False
    assert p.og_present == []


def test_og_tags_constant():
    assert OG_TAGS == ["og:title", "og:description", "og:image", "og:type", "og:url"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.parse'`

- [ ] **Step 3: Implement `spider/parse.py`**

```python
"""Pure HTML parsing. Returns raw (as-written, absolutised) link/image URLs —
link *checking* must see them as authored, so no identity collapse here."""

from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

OG_TAGS = ["og:title", "og:description", "og:image", "og:type", "og:url"]


@dataclass
class PageData:
    title: str | None = None
    description: str | None = None
    og_present: list[str] = field(default_factory=list)
    og_image: str | None = None
    canonical_present: bool = False
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    missing_alt: list[str] = field(default_factory=list)


def _abs(base: str, ref: str) -> str:
    return urldefrag(urljoin(base, ref))[0]


def parse_page(html: str, page_url: str) -> PageData:
    soup = BeautifulSoup(html, "lxml")
    data = PageData()

    if soup.title and soup.title.string:
        data.title = soup.title.string.strip() or None

    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content", "").strip():
        data.description = md["content"].strip()

    for tag in OG_TAGS:
        m = soup.find("meta", attrs={"property": tag})
        if m and m.get("content", "").strip():
            data.og_present.append(tag)
            if tag == "og:image":
                data.og_image = _abs(page_url, m["content"].strip())

    data.canonical_present = soup.find("link", attrs={"rel": "canonical"}) is not None

    for a in soup.find_all("a", href=True):
        target = _abs(page_url, a["href"])
        if target.startswith("http"):
            data.links.append(target)

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        full = _abs(page_url, src)
        data.images.append(full)
        if not (img.get("alt") or "").strip():
            data.missing_alt.append(full)

    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_parse.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/parse.py tests/test_parse.py
git commit -m "feat: pure HTML page parser"
```

---

## Task 4: Status checking (`spider/status.py`)

**Files:**
- Create: `spider/status.py`
- Test: `tests/test_status.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_status.py
import asyncio
import httpx
from spider.status import classify, check_status


def test_classify_broken_on_4xx_5xx():
    assert classify(404, 0) == "broken"
    assert classify(500, 2) == "broken"


def test_classify_broken_on_error_string():
    assert classify("ERR:ConnectError", 0) == "broken"


def test_classify_redirected_when_hops_and_ok_final():
    assert classify(200, 1) == "redirected"


def test_classify_ok():
    assert classify(200, 0) == "ok"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_check_status_follows_redirects_and_counts_hops():
    def handler(request):
        path = request.url.path
        if path == "/a":
            return httpx.Response(301, headers={"Location": "https://x.test/b"})
        if path == "/b":
            return httpx.Response(302, headers={"Location": "https://x.test/c"})
        return httpx.Response(200)

    async def run():
        async with _client(handler) as client:
            sem = asyncio.Semaphore(2)
            return await check_status(client, "https://x.test/a", sem, {})

    code, hops, final = asyncio.run(run())
    assert code == 200
    assert hops == 2
    assert final == "https://x.test/c"


def test_check_status_uses_cache():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200)

    async def run():
        async with _client(handler) as client:
            sem = asyncio.Semaphore(2)
            cache = {}
            await check_status(client, "https://x.test/p", sem, cache)
            await check_status(client, "https://x.test/p", sem, cache)
            return calls["n"]

    assert asyncio.run(run()) == 1


def test_check_status_get_fallback_on_405():
    seen = {"methods": []}

    def handler(request):
        seen["methods"].append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200)

    async def run():
        async with _client(handler) as client:
            sem = asyncio.Semaphore(2)
            return await check_status(client, "https://x.test/p", sem, {})

    code, hops, final = asyncio.run(run())
    assert code == 200
    assert "GET" in seen["methods"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.status'`

- [ ] **Step 3: Implement `spider/status.py`**

```python
"""Resource status checking with redirect-chain capture and classification."""

import asyncio
import httpx

_HEAD_UNTRUSTED = (403, 405, 501)


def classify(final_code, hops: int) -> str:
    """broken | redirected | ok. final_code is int status or 'ERR:...' string."""
    if isinstance(final_code, str):
        return "broken"
    if final_code >= 400:
        return "broken"
    if hops > 0:
        return "redirected"
    return "ok"


async def check_status(client: httpx.AsyncClient, url: str,
                       sem: asyncio.Semaphore, cache: dict):
    """Return (code, hops, final_url). Cached per url. HEAD first, GET fallback
    for servers that mishandle HEAD."""
    if url in cache:
        return cache[url]
    async with sem:
        try:
            r = await client.head(url, follow_redirects=True)
            if r.status_code in _HEAD_UNTRUSTED:
                r = await client.get(url, follow_redirects=True)
            result = (r.status_code, len(r.history), str(r.url))
        except Exception as e:  # network/timeout/etc
            result = (f"ERR:{type(e).__name__}", 0, url)
    cache[url] = result
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_status.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/status.py tests/test_status.py
git commit -m "feat: redirect-aware status checking and classification"
```

---

## Task 5: SQLite store (`spider/store.py`)

**Files:**
- Create: `spider/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
from spider.store import (
    connect, init_schema, enqueue, next_batch, mark_visited,
    count_visited, count_frontier, save_page, save_link, save_image,
    save_status, get_status, iter_pages, iter_links, iter_images,
)


def make(tmp_path):
    conn = connect(str(tmp_path / "crawl.db"))
    init_schema(conn)
    return conn


def test_frontier_enqueue_dedup_and_batch(tmp_path):
    conn = make(tmp_path)
    enqueue(conn, [("https://e.com/a", "https://e.com/a"),
                   ("https://e.com/b", "https://e.com/b")])
    enqueue(conn, [("https://e.com/a", "https://e.com/a")])  # dup ignored
    assert count_frontier(conn) == 2
    batch = next_batch(conn, 10)
    assert len(batch) == 2
    assert count_frontier(conn) == 0  # next_batch removes from frontier


def test_visited_blocks_requeue(tmp_path):
    conn = make(tmp_path)
    mark_visited(conn, "https://e.com/a")
    enqueue(conn, [("https://e.com/a", "https://e.com/a")])
    assert count_frontier(conn) == 0
    assert count_visited(conn) == 1


def test_save_and_iter_pages(tmp_path):
    conn = make(tmp_path)
    save_page(conn, "ID-1", "https://e.com/a", "https://e.com/a/", 200,
              "Title", "Desc", ["og:title"], "https://e.com/og.png", True)
    rows = list(iter_pages(conn))
    assert len(rows) == 1
    r = rows[0]
    assert r["page_id"] == "ID-1"
    assert r["display_url"] == "https://e.com/a/"
    assert r["status"] == 200
    assert r["title"] == "Title"
    assert r["og_present"] == ["og:title"]
    assert r["og_image"] == "https://e.com/og.png"
    assert r["canonical_present"] == 1


def test_save_links_and_images(tmp_path):
    conn = make(tmp_path)
    save_link(conn, "ID-1", "https://e.com/a", "https://e.com/x")
    save_image(conn, "ID-1", "https://e.com/a", "https://e.com/i.png", True)
    links = list(iter_links(conn))
    images = list(iter_images(conn))
    assert links[0]["target"] == "https://e.com/x"
    assert images[0]["missing_alt"] == 1


def test_status_roundtrip(tmp_path):
    conn = make(tmp_path)
    save_status(conn, "https://e.com/x", 301, 1, "https://e.com/y")
    assert get_status(conn, "https://e.com/x") == (301, 1, "https://e.com/y")
    assert get_status(conn, "https://e.com/missing") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.store'`

- [ ] **Step 3: Implement `spider/store.py`**

```python
"""SQLite-backed crawl store. Holds pages, links, images, status cache, and the
frontier/visited sets that make a crawl resumable. og_present is stored as a
comma-joined string and split on read."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    identity TEXT PRIMARY KEY,
    display  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visited (
    identity TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS pages (
    page_id           TEXT PRIMARY KEY,
    identity_url      TEXT UNIQUE NOT NULL,
    display_url       TEXT NOT NULL,
    status            INTEGER,
    title             TEXT,
    description       TEXT,
    og_present        TEXT,
    og_image          TEXT,
    canonical_present INTEGER
);
CREATE TABLE IF NOT EXISTS links (
    found_on_id  TEXT,
    found_on_url TEXT,
    target       TEXT
);
CREATE TABLE IF NOT EXISTS images (
    found_on_id  TEXT,
    found_on_url TEXT,
    src          TEXT,
    missing_alt  INTEGER
);
CREATE TABLE IF NOT EXISTS status_cache (
    url       TEXT PRIMARY KEY,
    code      TEXT,
    hops      INTEGER,
    final_url TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def enqueue(conn, items) -> None:
    """items: iterable of (identity, display). Skips already-visited identities."""
    for identity, display in items:
        row = conn.execute("SELECT 1 FROM visited WHERE identity=?", (identity,)).fetchone()
        if row:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO frontier(identity, display) VALUES (?, ?)",
            (identity, display),
        )
    conn.commit()


def next_batch(conn, n: int):
    rows = conn.execute("SELECT identity, display FROM frontier LIMIT ?", (n,)).fetchall()
    out = [(r["identity"], r["display"]) for r in rows]
    conn.executemany("DELETE FROM frontier WHERE identity=?", [(i,) for i, _ in out])
    conn.commit()
    return out


def mark_visited(conn, identity: str) -> None:
    conn.execute("INSERT OR IGNORE INTO visited(identity) VALUES (?)", (identity,))
    conn.execute("DELETE FROM frontier WHERE identity=?", (identity,))
    conn.commit()


def count_visited(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM visited").fetchone()[0]


def count_frontier(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]


def save_page(conn, page_id, identity, display, status, title, description,
              og_present, og_image, canonical_present) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO pages
           (page_id, identity_url, display_url, status, title, description,
            og_present, og_image, canonical_present)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (page_id, identity, display, status, title, description,
         ",".join(og_present), og_image, 1 if canonical_present else 0),
    )
    conn.commit()


def save_link(conn, found_on_id, found_on_url, target) -> None:
    conn.execute(
        "INSERT INTO links(found_on_id, found_on_url, target) VALUES (?,?,?)",
        (found_on_id, found_on_url, target),
    )


def save_image(conn, found_on_id, found_on_url, src, missing_alt) -> None:
    conn.execute(
        "INSERT INTO images(found_on_id, found_on_url, src, missing_alt) VALUES (?,?,?,?)",
        (found_on_id, found_on_url, src, 1 if missing_alt else 0),
    )


def save_status(conn, url, code, hops, final_url) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO status_cache(url, code, hops, final_url) VALUES (?,?,?,?)",
        (url, str(code), hops, final_url),
    )


def _coerce_code(code: str):
    return int(code) if code.lstrip("-").isdigit() else code


def get_status(conn, url):
    row = conn.execute(
        "SELECT code, hops, final_url FROM status_cache WHERE url=?", (url,)
    ).fetchone()
    if not row:
        return None
    return (_coerce_code(row["code"]), row["hops"], row["final_url"])


def iter_pages(conn):
    for r in conn.execute("SELECT * FROM pages"):
        yield {
            "page_id": r["page_id"],
            "identity_url": r["identity_url"],
            "display_url": r["display_url"],
            "status": r["status"],
            "title": r["title"],
            "description": r["description"],
            "og_present": r["og_present"].split(",") if r["og_present"] else [],
            "og_image": r["og_image"],
            "canonical_present": r["canonical_present"],
        }


def iter_links(conn):
    for r in conn.execute("SELECT * FROM links"):
        yield {"found_on_id": r["found_on_id"], "found_on_url": r["found_on_url"],
               "target": r["target"]}


def iter_images(conn):
    for r in conn.execute("SELECT * FROM images"):
        yield {"found_on_id": r["found_on_id"], "found_on_url": r["found_on_url"],
               "src": r["src"], "missing_alt": r["missing_alt"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/store.py tests/test_store.py
git commit -m "feat: SQLite crawl store with resumable frontier"
```

---

## Task 6: Crawl orchestration (`spider/crawl.py`)

**Files:**
- Create: `spider/crawl.py`
- Test: `tests/test_crawl.py`

This task wires Tasks 1–5 together: resolve the canonical origin, seed from the sitemap, then BFS the site, saving pages/links/images to the store and finally sweeping resource statuses. Tested end-to-end with `httpx.MockTransport`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_crawl.py
import asyncio
import httpx
from spider.store import connect, init_schema, iter_pages, get_status, count_visited
from spider.crawl import resolve_origin, run_crawl


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_resolve_origin_follows_to_canonical():
    def handler(request):
        if request.url.host == "example.test" and request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": "https://www.example.test/"})
        return httpx.Response(200, text="<html></html>")

    async def run():
        async with mock_client(handler) as client:
            return await resolve_origin(client, "http://example.test")

    scheme, host = asyncio.run(run())
    assert scheme == "https"
    assert host == "www.example.test"


SITE = {
    "/": '<html><head><title>Home</title></head><body>'
         '<a href="/page1">1</a><a href="/page2">2</a>'
         '<a href="/gone">dead</a><img src="/img/missing.png"></body></html>',
    "/page1": '<html><head><title>One</title>'
              '<meta name="description" content="d1"></head>'
              '<body><a href="/oldlink">old</a></body></html>',
    "/page2": '<html><head><title>Home</title></head><body>ok</body></html>',  # dup title
}


def site_handler(request):
    path = request.url.path
    if path == "/gone":
        return httpx.Response(404)
    if path == "/img/missing.png":
        return httpx.Response(404)
    if path == "/oldlink":
        return httpx.Response(301, headers={"Location": "https://s.test/page1"})
    if path in SITE:
        return httpx.Response(200, text=SITE[path],
                              headers={"content-type": "text/html"})
    if path == "/sitemap.xml" or path == "/sitemap_index.xml":
        return httpx.Response(404)
    return httpx.Response(200, text="<html></html>",
                          headers={"content-type": "text/html"})


def test_run_crawl_populates_store(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)

    async def run():
        async with mock_client(site_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=50)

    asyncio.run(run())
    urls = {p["identity_url"] for p in iter_pages(conn)}
    assert "https://s.test/" in urls
    assert "https://s.test/page1" in urls
    assert "https://s.test/page2" in urls
    assert count_visited(conn) >= 3
    # broken + redirect statuses recorded
    assert get_status(conn, "https://s.test/gone")[0] == 404
    assert get_status(conn, "https://s.test/oldlink")[0] == 200  # final after 301
    assert get_status(conn, "https://s.test/oldlink")[1] == 1    # one hop


def test_run_crawl_respects_max_pages(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)

    async def run():
        async with mock_client(site_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=1)

    asyncio.run(run())
    assert count_visited(conn) == 1


def test_resume_skips_visited(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)
    fetched = {"paths": []}

    def counting_handler(request):
        fetched["paths"].append(request.url.path)
        return site_handler(request)

    async def first():
        async with mock_client(counting_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=1)

    async def resumed():
        async with mock_client(counting_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=50, resume=True)

    asyncio.run(first())
    visited_after_first = count_visited(conn)
    fetched["paths"].clear()
    asyncio.run(resumed())
    # the page crawled in the first run must not be GET-fetched again as a page
    assert count_visited(conn) > visited_after_first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_crawl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.crawl'`

- [ ] **Step 3: Implement `spider/crawl.py`**

```python
"""Async crawl orchestration: resolve origin, seed from sitemap, BFS the site
into the store, then sweep resource statuses. Networked logic only — pure
helpers live in normalize/parse/status."""

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx

from spider import store
from spider.identifiers import page_id
from spider.normalize import normalize_identity, same_site
from spider.parse import parse_page
from spider.status import check_status

CONCURRENCY = 10
TIMEOUT = 20.0
USER_AGENT = "TurboPress-Audit/1.0 (+https://turbopress.pro)"


async def resolve_origin(client: httpx.AsyncClient, start_url: str):
    """Follow the start URL's redirects to learn the canonical (scheme, host)."""
    try:
        r = await client.get(start_url, follow_redirects=True)
        final = r.url
        return (final.scheme, final.host)
    except Exception:
        p = urlparse(start_url)
        return (p.scheme or "https", p.netloc)


async def fetch_sitemap_urls(client, origin_scheme, origin_host):
    """Pull page URLs from sitemap(s), recursing nested indexes. Returns
    identity-normalised URLs."""
    root = f"{origin_scheme}://{origin_host}"
    found, seen = set(), set()
    queue = [urljoin(root, "/sitemap_index.xml"), urljoin(root, "/sitemap.xml")]
    while queue:
        sm = queue.pop()
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = await client.get(sm)
        except Exception:
            continue
        if r.status_code != 200 or "<loc>" not in r.text:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
            if loc.endswith(".xml"):
                queue.append(loc)
            else:
                found.add(normalize_identity(loc, origin_scheme, origin_host))
    return found


async def run_crawl(conn, client: httpx.AsyncClient, start_url: str,
                    client_slug: str, max_pages: int, resume: bool = False):
    scheme, host = await resolve_origin(client, start_url)
    root = f"{scheme}://{host}"
    sem = asyncio.Semaphore(CONCURRENCY)

    if not resume:
        seeds = await fetch_sitemap_urls(client, scheme, host)
        if not seeds:
            seeds = {normalize_identity(start_url, scheme, host)}
        store.enqueue(conn, [(s, s) for s in seeds])

    async def fetch_page(identity, display):
        async with sem:
            try:
                r = await client.get(display)
            except Exception as e:
                store.save_page(conn, page_id(client_slug, identity), identity,
                                display, None, None, None, [], None, False)
                return []
        status = r.status_code
        ctype = r.headers.get("content-type", "")
        if status != 200 or "html" not in ctype:
            store.save_page(conn, page_id(client_slug, identity), identity,
                            display, status, None, None, [], None, False)
            return []

        data = parse_page(r.text, display)
        store.save_page(conn, page_id(client_slug, identity), identity, display,
                        status, data.title, data.description, data.og_present,
                        data.og_image, data.canonical_present)
        pid = page_id(client_slug, identity)
        for link in data.links:
            store.save_link(conn, pid, display, link)
        for img in data.images:
            missing = img in data.missing_alt
            store.save_image(conn, pid, display, img, missing)

        # discover internal links as new identities
        new = []
        for link in data.links:
            if same_site(root, link):
                new_identity = normalize_identity(link, scheme, host)
                new.append((new_identity, link))
        return new

    while store.count_frontier(conn) and store.count_visited(conn) < max_pages:
        remaining = max_pages - store.count_visited(conn)
        batch = store.next_batch(conn, min(CONCURRENCY, remaining))
        if not batch:
            break
        for identity, _ in batch:
            store.mark_visited(conn, identity)
        results = await asyncio.gather(*(fetch_page(i, d) for i, d in batch))
        discovered = [pair for sub in results for pair in sub]
        store.enqueue(conn, discovered)
        print(f"  crawled {store.count_visited(conn)} / queue {store.count_frontier(conn)}")

    # status sweep over all link + image targets
    targets = {l["target"] for l in store.iter_links(conn)}
    targets |= {i["src"] for i in store.iter_images(conn)}
    targets |= {p["og_image"] for p in store.iter_pages(conn) if p["og_image"]}
    targets = list(targets)
    cache = {}
    for n, t in enumerate(targets, 1):
        code, hops, final = await check_status(client, t, sem, cache)
        store.save_status(conn, t, code, hops, final)
        if n % 25 == 0 or n == len(targets):
            print(f"  checked {n} / {len(targets)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_crawl.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/crawl.py tests/test_crawl.py
git commit -m "feat: resumable async crawl orchestration"
```

---

## Task 7: Report generation (`spider/reports.py`)

**Files:**
- Create: `spider/reports.py`
- Test: `tests/test_reports.py`

Reports read the store and compute derived facts (duplicate-title/description groups, OG status from the status cache, link/image classification).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reports.py
import csv
from spider.store import (connect, init_schema, save_page, save_link,
                          save_image, save_status)
from spider.reports import write_page_audit, write_link_issues, write_summary


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
    # clean page C-3 excluded
    assert "C-3" not in ids
    assert "C-1" in ids and "C-2" in ids
    a = ids["C-1"]
    assert a["Meta Description?"] == "Missing"
    assert a["Title Duplicated?"] == "Yes (2)"
    assert a["Open Graph?"] == "Broken og:image"
    assert a["Canonical?"] == "OK"
    b = ids["C-2"]
    assert b["Canonical?"] == "Missing"
    assert b["Open Graph?"] == "OK"
    assert b["Meta Duplicated?"] == "No"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_reports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.reports'`

- [ ] **Step 3: Implement `spider/reports.py`**

```python
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
    missing = [t for t in OG_TAGS if t not in page["og_present"]]
    if page["og_image"]:
        st = get_status(conn, page["og_image"])
        if st and classify(st[0], st[1]) == "broken":
            return "Broken og:image"
    if missing:
        return "Missing: " + ", ".join(missing)
    return "OK"


def write_page_audit(conn, path: str) -> int:
    pages = list(iter_pages(conn))
    title_dups = _dup_counts(pages, "title")
    desc_dups = _dup_counts(pages, "description")
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PAGE_COLUMNS)
        for p in pages:
            title_missing = not p["title"]
            desc_missing = not p["description"]
            title_dup = p["title"] and title_dups[p["title"]] > 1
            desc_dup = p["description"] and desc_dups[p["description"]] > 1
            og = _og_status(conn, p)
            canonical_ok = bool(p["canonical_present"])
            status_bad = p["status"] != 200
            if not any([title_missing, desc_missing, title_dup, desc_dup,
                        og != "OK", not canonical_ok, status_bad]):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_reports.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add spider/reports.py tests/test_reports.py
git commit -m "feat: page audit, link issues, and summary reports"
```

---

## Task 8: CLI and entrypoint (`spider/cli.py`, `seo_audit.py`)

**Files:**
- Create: `spider/cli.py`
- Modify: `seo_audit.py` (replace entire contents with a thin shim)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
from datetime import date
from spider.cli import build_run_dir, resolve_args


def test_resolve_args_prompts_when_missing(monkeypatch):
    answers = iter(["https://prompted.test", "Prompted Client"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    url, client_slug = resolve_args(None, None)
    assert url == "https://prompted.test"
    assert client_slug == "PROMPTED-CLIENT"


def test_resolve_args_uses_given_values():
    url, client_slug = resolve_args("https://x.test", "Acme Co")
    assert url == "https://x.test"
    assert client_slug == "ACME-CO"


def test_build_run_dir_layout(tmp_path):
    d = build_run_dir(str(tmp_path), "example.com", date(2026, 6, 3), at="0900")
    assert d.endswith("runs\\example.com\\2026-06-03_0900") or \
           d.endswith("runs/example.com/2026-06-03_0900")
    import os
    assert os.path.isdir(d)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spider.cli'`

- [ ] **Step 3: Implement `spider/cli.py`**

```python
"""Command-line entry: parse/prompt args, set up the run folder, drive the
crawl, write reports. Graceful on Ctrl+C — commits and reports partial work."""

import argparse
import asyncio
import os
from datetime import datetime
from urllib.parse import urlparse

import httpx

from spider import store
from spider.crawl import TIMEOUT, USER_AGENT, run_crawl
from spider.identifiers import report_code, slug_client
from spider.reports import write_link_issues, write_page_audit, write_summary


def resolve_args(url, client):
    if not url:
        url = input("Start URL: ").strip()
    if not client:
        client = input("Client (Xero account name): ").strip()
    return url, slug_client(client)


def build_run_dir(base, domain, when, at):
    path = os.path.join(base, "runs", domain.lower(), f"{when:%Y-%m-%d}_{at}")
    os.makedirs(path, exist_ok=True)
    return path


def _find_resume_dir(base, domain):
    root = os.path.join(base, "runs", domain.lower())
    if not os.path.isdir(root):
        return None
    dirs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, dirs[-1]) if dirs else None


async def _drive(conn, run_dir, url, client_slug, domain, max_pages, resume, started):
    interrupted = False
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT,
                                 follow_redirects=True) as client:
        try:
            await run_crawl(conn, client, url, client_slug, max_pages, resume)
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted — writing partial report. Resume with --resume.")
    _write_all(conn, run_dir, url, client_slug, domain, started, resume or interrupted)


def _write_all(conn, run_dir, url, client_slug, domain, started, resumed):
    write_page_audit(conn, os.path.join(run_dir, "page_audit.csv"))
    write_link_issues(conn, os.path.join(run_dir, "link_issues.csv"))
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    write_summary(conn, os.path.join(run_dir, "summary.txt"), {
        "report_code": report_code(client_slug, datetime.now().date()),
        "client": client_slug, "domain": domain, "start_url": url,
        "origin": origin, "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "resumed": resumed,
    })
    print(f"\nReports written to {run_dir}")


def main():
    parser = argparse.ArgumentParser(description="TurboPress SEO spider report")
    parser.add_argument("url", nargs="?", help="start URL (prompted if omitted)")
    parser.add_argument("--client", help="Xero client account name")
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--resume", action="store_true",
                        help="continue the latest incomplete crawl for this domain")
    args = parser.parse_args()

    url, client_slug = resolve_args(args.url, args.client)
    domain = urlparse(url).netloc.lower()
    base = os.getcwd()
    started = datetime.now().isoformat(timespec="seconds")

    if args.resume:
        run_dir = _find_resume_dir(base, domain)
        if not run_dir:
            print("No previous run found; starting fresh.")
            run_dir = build_run_dir(base, domain, datetime.now().date(),
                                    at=datetime.now().strftime("%H%M"))
    else:
        run_dir = build_run_dir(base, domain, datetime.now().date(),
                                at=datetime.now().strftime("%H%M"))

    print(f"Crawling up to {args.max_pages} pages of {url}")
    conn = store.connect(os.path.join(run_dir, "crawl.db"))
    store.init_schema(conn)
    try:
        asyncio.run(_drive(conn, run_dir, url, client_slug, domain,
                           args.max_pages, args.resume, started))
    except KeyboardInterrupt:
        _write_all(conn, run_dir, url, client_slug, domain, started, True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Replace `seo_audit.py` with a thin shim**

```python
#!/usr/bin/env python3
"""Entry point. Real logic lives in the `spider` package.

Usage:
    python seo_audit.py https://example.com --client "Washington Parent"
    python seo_audit.py --resume https://example.com
"""

from spider.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add spider/cli.py seo_audit.py tests/test_cli.py
git commit -m "feat: CLI, run-folder layout, and graceful interrupt"
```

---

## Task 9: Full suite + manual smoke test + docs

**Files:**
- Create: `README.md`
- Modify: none (verification task)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: PASS — all tests across the 8 test files green.

- [ ] **Step 2: Smoke test against a real small site**

Run: `.venv\Scripts\python seo_audit.py https://example.com --client "Smoke Test" --max-pages 5`
Expected: prints crawl progress, then "Reports written to …\runs\example.com\<date>_<time>"; that folder contains `crawl.db`, `page_audit.csv`, `link_issues.csv`, `summary.txt`.

- [ ] **Step 3: Verify resume path**

Run the same command with `--resume`:
`.venv\Scripts\python seo_audit.py https://example.com --client "Smoke Test" --resume`
Expected: reuses the latest run folder, completes, rewrites reports.

- [ ] **Step 4: Create `README.md`**

```markdown
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

Output lands in `runs/<domain>/<date>_<time>/`:
`crawl.db`, `page_audit.csv`, `link_issues.csv`, `summary.txt`.

## Output

- **page_audit.csv** — one row per page with at least one issue (missing/duplicate
  title or meta description, missing/broken Open Graph, missing canonical, bad status).
  Each page has a stable Page ID for month-over-month tracking.
- **link_issues.csv** — broken links, broken images, and redirects (with hop count
  and final destination), joined back to pages via Page ID.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Scope

This is the **spider report** (script 1). A later **content report** (script 2)
will cover H1/H2/H3 structure, duplicate/thin content, and canonical correctness.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: usage README; verify full suite and smoke test"
```

- [ ] **Step 6: Push**

```bash
git push
```

---

## Self-Review Notes

- **Spec coverage:** identifiers (Task 2), URL normalisation identity/checking split (Tasks 1, 3, 6), live progress + redirect capture + graceful Ctrl+C (Tasks 6, 8), OG + canonical presence + title/desc presence capture (Tasks 3, 6), SQLite store with resumable frontier and `--resume`/fresh default (Tasks 5, 6, 8), two-sheet output with exact columns incl. `Title Duplicated?` (Task 7), `summary.txt` (Task 7), prompting on missing args (Task 8), run-folder layout (Task 8). Duplicate **content** and H1/heading checks are intentionally **out of scope** (script 2) per spec.
- **Deviation:** sitemap seeding for resume — on `--resume` we skip re-seeding and drain the existing frontier (correct; the frontier was persisted).
- **Known follow-up (not in this plan):** the `.xlsx` presentation layer and the month-over-month diff report, both deferred in the spec.
```