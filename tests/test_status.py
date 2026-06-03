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


def test_check_status_retries_once_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    async def run():
        async with _client(handler) as client:
            sem = asyncio.Semaphore(2)
            return await check_status(client, "https://x.test/p", sem, {})

    code, hops, final = asyncio.run(run())
    assert code == 200
    assert calls["n"] == 2
