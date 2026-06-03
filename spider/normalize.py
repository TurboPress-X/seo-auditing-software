"""URL normalisation. Two uses: identity (aggressive collapse for dedup/IDs)
and same-site checks. Link *checking* uses raw URLs elsewhere, not this."""

from urllib.parse import urldefrag, urlparse


def strip_www(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def reg_netloc(url: str) -> str:
    return strip_www(urlparse(url).hostname or "")


def same_site(a: str, b: str) -> bool:
    return reg_netloc(a) == reg_netloc(b)


def normalize_identity(url: str, origin_scheme: str, origin_host: str) -> str:
    """Canonical identity string: collapses http/https, www/non-www,
    trailing slash, and fragments so one logical page maps to one key.
    An absolute URL is expected; callers resolve relative links via urljoin before calling."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    host = strip_www(p.netloc) if p.netloc else strip_www(origin_host)
    path = p.path.rstrip("/") or "/"
    query = f"?{p.query}" if p.query else ""
    return f"{origin_scheme}://{host}{path}{query}"  # origin_scheme is a bare scheme, e.g. "https" (no "://")
