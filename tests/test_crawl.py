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
    "/page2": '<html><head><title>Home</title></head><body>ok</body></html>',
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
        return httpx.Response(200, text=SITE[path], headers={"content-type": "text/html"})
    if path in ("/sitemap.xml", "/sitemap_index.xml"):
        return httpx.Response(404)
    return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})


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

    async def first():
        async with mock_client(site_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=1)

    async def resumed():
        async with mock_client(site_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=50, resume=True)

    asyncio.run(first())
    visited_after_first = count_visited(conn)
    asyncio.run(resumed())
    assert count_visited(conn) > visited_after_first


def robots_site_handler(request):
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
    if path == "/":
        return httpx.Response(
            200,
            text='<html><head><title>Home</title></head><body>'
                 '<a href="/ok">ok</a><a href="/secret/x">no</a></body></html>',
            headers={"content-type": "text/html"},
        )
    if path == "/ok":
        return httpx.Response(
            200,
            text='<html><head><title>OK</title></head><body></body></html>',
            headers={"content-type": "text/html"},
        )
    if path in ("/sitemap.xml", "/sitemap_index.xml"):
        return httpx.Response(404)
    return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})


def test_run_crawl_respects_robots_disallow(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    init_schema(conn)

    async def run():
        async with mock_client(robots_site_handler) as client:
            await run_crawl(conn, client, "https://s.test", "S", max_pages=50)

    asyncio.run(run())
    urls = {p["identity_url"] for p in iter_pages(conn)}
    assert "https://s.test/ok" in urls
    assert "https://s.test/secret/x" not in urls  # blocked by robots.txt
