import asyncio

import httpx

from spider.robots import load_robots

UA = "TurboPress-Audit/1.0"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _load(handler):
    async def run():
        async with _client(handler) as c:
            return await load_robots(c, "https", "e.test", UA)
    return asyncio.run(run())


def test_disallow_respected():
    body = "User-agent: *\nDisallow: /private\n"

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=body)
        return httpx.Response(200)

    r = _load(h)
    assert r.allowed("https://e.test/public") is True
    assert r.allowed("https://e.test/private/x") is False


def test_missing_robots_allows_all():
    def h(req):
        return httpx.Response(404)

    r = _load(h)
    assert r.allowed("https://e.test/anything") is True
    assert r.delay is None


def test_empty_robots_allows_all():
    def h(req):
        return httpx.Response(200, text="   \n")

    r = _load(h)
    assert r.allowed("https://e.test/anything") is True


def test_network_error_allows_all():
    def h(req):
        raise httpx.ConnectError("boom")

    r = _load(h)
    assert r.allowed("https://e.test/anything") is True


def test_crawl_delay_parsed():
    body = "User-agent: *\nCrawl-delay: 2\n"

    def h(req):
        return httpx.Response(200, text=body) if req.url.path == "/robots.txt" else httpx.Response(200)

    r = _load(h)
    assert r.delay == 2
