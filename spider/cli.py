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
