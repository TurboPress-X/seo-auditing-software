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


async def _probe(client: httpx.AsyncClient, url: str):
    """One status probe: HEAD first, GET fallback for servers that mishandle HEAD.
    Returns (code, hops, final_url); errors come back as ('ERR:<Name>', 0, url)."""
    try:
        r = await client.head(url, follow_redirects=True)
        if r.status_code in _HEAD_UNTRUSTED:
            r = await client.get(url, follow_redirects=True)
        return (r.status_code, len(r.history), str(r.url))
    except Exception as e:
        return (f"ERR:{type(e).__name__}", 0, url)


async def check_status(client: httpx.AsyncClient, url: str,
                       sem: asyncio.Semaphore, cache: dict):
    """Return (code, hops, final_url), cached per url so each resource is checked
    once. Retries once on a transient error to avoid false 'broken' results, then
    caches whatever the outcome is."""
    if url in cache:
        return cache[url]
    async with sem:
        result = await _probe(client, url)
        if isinstance(result[0], str):  # transient failure — retry once
            result = await _probe(client, url)
    cache[url] = result
    return result
