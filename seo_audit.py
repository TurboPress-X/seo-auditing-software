#!/usr/bin/env python3
"""
seo_audit.py
Focused site auditor: broken links and images (404 etc), missing image alt
text, missing/duplicate meta descriptions, duplicate titles, and exact
duplicate page content.

Built for server-rendered sites (WordPress, etc) so no headless browser is
needed. If you ever point it at a JavaScript SPA, swap the page fetch for a
Browserless render.

Usage:
    python seo_audit.py https://example.com [max_pages]

Output:
    seo_issues.csv  (one row per issue; issue types map to the Master Task List)
"""

import asyncio
import csv
import hashlib
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

CONCURRENCY = 10
TIMEOUT = 20.0
USER_AGENT = "TurboPress-Audit/1.0 (+https://turbopress.pro)"


def reg_netloc(url):
    n = urlparse(url).netloc.lower()
    return n[4:] if n.startswith("www.") else n


def same_site(a, b):
    return reg_netloc(a) == reg_netloc(b)


def normalise(url):
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


async def fetch_sitemap_urls(client, root):
    """Pull page URLs from sitemap, recursing into nested sitemap indexes."""
    found = set()
    seen = set()
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
                found.add(normalise(loc))
    return found


async def check_status(client, url, sem, cache):
    if url in cache:
        return cache[url]
    async with sem:
        try:
            r = await client.head(url, follow_redirects=True)
            if r.status_code in (403, 405, 501):  # some servers mishandle HEAD
                r = await client.get(url, follow_redirects=True)
            code = r.status_code
        except Exception as e:
            code = f"ERR:{type(e).__name__}"
    cache[url] = code
    return code


async def crawl(start, max_pages):
    root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {"User-Agent": USER_AGENT}

    pages = {}           # url -> {status, title, description, content_hash}
    links_found = []     # (found_on, target)  internal + external
    images_found = []    # (found_on, src)
    missing_alt = []     # (found_on, src)
    status_cache = {}

    async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT,
                                 follow_redirects=True) as client:
        seeds = await fetch_sitemap_urls(client, root)
        frontier = set(seeds) or {normalise(start)}
        visited = set()

        async def fetch_page(url):
            async with sem:
                try:
                    r = await client.get(url)
                except Exception as e:
                    pages[url] = {"status": f"ERR:{type(e).__name__}"}
                    return []
            page = pages.setdefault(url, {})
            page["status"] = r.status_code
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return []

            soup = BeautifulSoup(r.text, "lxml")
            page["title"] = (soup.title.string or "").strip() if soup.title else ""
            md = soup.find("meta", attrs={"name": "description"})
            page["description"] = md.get("content", "").strip() if md else ""
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).lower()
            page["content_hash"] = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()

            new_internal = []
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if not src:
                    continue
                full = normalise(urljoin(url, src))
                images_found.append((url, full))
                if not (img.get("alt") or "").strip():
                    missing_alt.append((url, full))
            for a in soup.find_all("a", href=True):
                target = normalise(urljoin(url, a["href"]))
                if not target.startswith("http"):
                    continue
                links_found.append((url, target))
                if same_site(root, target) and target not in visited and target not in frontier:
                    new_internal.append(target)
            return new_internal

        while frontier and len(visited) < max_pages:
            snapshot = list(frontier)
            batch = snapshot[: max_pages - len(visited)]
            frontier = set(snapshot[len(batch):])
            visited.update(batch)
            for found in await asyncio.gather(*(fetch_page(u) for u in batch)):
                frontier.update(nu for nu in found if nu not in visited)

        targets = {t for _, t in links_found} | {s for _, s in images_found}
        await asyncio.gather(*(check_status(client, t, sem, status_cache) for t in targets))

    return pages, links_found, images_found, missing_alt, status_cache


def build_issues(pages, links_found, images_found, missing_alt, status_cache):
    rows = []  # Issue Type, URL, Detail, Found On

    def broken(code):
        return isinstance(code, str) or (isinstance(code, int) and code >= 400)

    for found_on, target in links_found:
        code = status_cache.get(target)
        if broken(code):
            rows.append(("F - Broken link", target, f"status {code}", found_on))

    for found_on, src in images_found:
        code = status_cache.get(src)
        if broken(code):
            rows.append(("C - Broken image", src, f"status {code}", found_on))

    for found_on, src in missing_alt:
        rows.append(("D - Missing alt text", src, "no alt attribute", found_on))

    for url, p in pages.items():
        if p.get("status") == 200 and p.get("description") == "":
            rows.append(("A - Missing meta description", url, "empty or absent", ""))

    def report_dupes(key, label):
        groups = defaultdict(list)
        for url, p in pages.items():
            v = p.get(key)
            if v:
                groups[v].append(url)
        for v, urls in groups.items():
            if len(urls) > 1:
                for url in urls:
                    rows.append((label, url, f"shared by {len(urls)} pages", ""))

    report_dupes("title", "B - Duplicate title")
    report_dupes("description", "A - Duplicate meta description")
    report_dupes("content_hash", "Duplicate content")
    return rows


def main():
    if len(sys.argv) < 2:
        print("usage: python seo_audit.py https://example.com [max_pages]")
        sys.exit(1)
    start = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    pages, links, images, missing_alt, status_cache = asyncio.run(crawl(start, max_pages))
    rows = build_issues(pages, links, images, missing_alt, status_cache)

    with open("seo_issues.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Issue Type", "URL", "Detail", "Found On"])
        w.writerows(rows)

    counts = Counter(r[0] for r in rows)
    print(f"\nCrawled {len(pages)} pages, checked {len(status_cache)} unique resources.")
    print(f"Wrote {len(rows)} issues to seo_issues.csv\n")
    for k, v in sorted(counts.items()):
        print(f"  {v:5d}  {k}")


if __name__ == "__main__":
    main()
