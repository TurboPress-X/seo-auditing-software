"""Pure HTML parsing. Returns raw (as-written, absolutised) link/image URLs;
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


def _is_data_uri(ref: str) -> bool:
    """data: URIs (and the Elementor `image/svg+xml;base64,...` placeholder, which
    omits the `data:` prefix) are inline content, not crawlable URLs."""
    r = ref.strip().lower()
    return r.startswith("data:") or ";base64," in r


def parse_page(html: str, page_url: str) -> PageData:
    soup = BeautifulSoup(html, "lxml")
    data = PageData()

    if soup.title:
        title_text = soup.title.get_text(" ", strip=True)
        data.title = title_text or None

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
        href = a["href"].strip()
        if not href or href.startswith("#") or _is_data_uri(href):
            continue
        target = _abs(page_url, href)
        if target.startswith("http"):
            data.links.append(target)

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or _is_data_uri(src):
            continue
        full = _abs(page_url, src)
        data.images.append(full)
        if not (img.get("alt") or "").strip():
            data.missing_alt.append(full)

    return data
