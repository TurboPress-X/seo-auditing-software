"""Async crawl orchestration: resolve origin, seed from sitemap, BFS the site
into the store, then sweep resource statuses. Networked logic only; pure
helpers live in normalize/parse/status. Crash-safe: a URL leaves the frontier
only after its page is saved (peek batch -> save -> mark_visited)."""

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx

from spider import store
from spider.identifiers import page_id
from spider.normalize import normalize_identity, same_site
from spider.parse import parse_page
from spider.robots import load_robots
from spider.status import check_status

# CONCURRENCY bounds in-flight requests (via the semaphore below). TIMEOUT and
# USER_AGENT are consumed by cli.py when it constructs the shared httpx client,
# so they apply to every request made through run_crawl.
CONCURRENCY = 10
TIMEOUT = 20.0
USER_AGENT = "TurboPress-Audit/1.0 (+https://turbopress.pro)"


async def resolve_origin(client: httpx.AsyncClient, start_url: str):
    """Follow the start URL's redirects to learn the canonical (scheme, host)."""
    try:
        r = await client.get(start_url, follow_redirects=True, timeout=TIMEOUT)
        return (r.url.scheme, r.url.host)
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
            r = await client.get(sm, timeout=TIMEOUT)
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
    robots = await load_robots(client, scheme, host, USER_AGENT)
    # A crawl-delay forces single-file, spaced requests; otherwise go concurrent.
    sem = asyncio.Semaphore(1 if robots.delay else CONCURRENCY)
    if robots.delay:
        print(f"  robots.txt crawl-delay: {robots.delay}s (crawling one page at a time)")

    if not resume:
        seeds = await fetch_sitemap_urls(client, scheme, host)
        if not seeds:
            seeds = {normalize_identity(start_url, scheme, host)}
        seeds = {s for s in seeds if robots.allowed(s)}
        store.enqueue(conn, [(s, s) for s in seeds])

    async def fetch_page(identity, display):
        pid = page_id(client_slug, identity)
        async with sem:
            try:
                r = await client.get(display, timeout=TIMEOUT)
            except Exception:
                store.delete_edges(conn, pid)
                store.save_page(conn, pid, identity, display, None, None,
                                None, [], None, False)
                if robots.delay:
                    await asyncio.sleep(robots.delay)
                return []
            if robots.delay:
                await asyncio.sleep(robots.delay)
        status = r.status_code
        ctype = r.headers.get("content-type", "")
        store.delete_edges(conn, pid)
        if status != 200 or "html" not in ctype:
            store.save_page(conn, pid, identity, display, status, None, None,
                            [], None, False)
            return []
        data = parse_page(r.text, display)
        store.save_page(conn, pid, identity, display, status, data.title,
                        data.description, data.og_present, data.og_image,
                        data.canonical_present)
        for link in data.links:
            store.save_link(conn, pid, display, link)
        for img in data.images:
            store.save_image(conn, pid, display, img, img in data.missing_alt)
        new = []
        for link in data.links:
            if same_site(root, link) and robots.allowed(link):
                new.append((normalize_identity(link, scheme, host), link))
        return new

    while store.count_frontier(conn) and store.count_visited(conn) < max_pages:
        remaining = max_pages - store.count_visited(conn)
        batch = store.next_batch(conn, min(CONCURRENCY, remaining))
        if not batch:
            break
        results = await asyncio.gather(*(fetch_page(i, d) for i, d in batch))
        for identity, _ in batch:
            store.mark_visited(conn, identity)
        discovered = [pair for sub in results for pair in sub]
        store.enqueue(conn, discovered)
        print(f"  crawled {store.count_visited(conn)} / queue {store.count_frontier(conn)}")

    # status sweep over all link + image + og:image targets. check_status acquires
    # `sem` internally, so gathering a chunk stays bounded to CONCURRENCY; chunking
    # keeps progress output flowing on large sites.
    targets = {l["target"] for l in store.iter_links(conn)}
    targets |= {i["src"] for i in store.iter_images(conn)}
    targets |= {p["og_image"] for p in store.iter_pages(conn) if p["og_image"]}
    targets = list(targets)
    total = len(targets)
    cache = {}
    for start in range(0, total, 50):
        chunk = targets[start:start + 50]
        results = await asyncio.gather(*(check_status(client, t, sem, cache) for t in chunk))
        for t, (code, hops, final) in zip(chunk, results):
            store.save_status(conn, t, code, hops, final)
        print(f"  checked {min(start + 50, total)} / {total}")

    return (scheme, host)
