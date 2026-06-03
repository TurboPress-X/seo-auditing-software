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
