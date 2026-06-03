from spider.parse import parse_page, OG_TAGS

HTML = """
<html><head>
  <title>  Hello World  </title>
  <meta name="description" content="A page">
  <meta property="og:title" content="Hello">
  <meta property="og:image" content="/img/og.png">
  <link rel="canonical" href="https://example.com/page">
</head><body>
  <a href="/internal">in</a>
  <a href="https://other.com/ext">ext</a>
  <a href="mailto:x@y.com">mail</a>
  <img src="/img/a.png" alt="A">
  <img src="/img/b.png">
</body></html>
"""


def test_parses_title_and_description():
    p = parse_page(HTML, "https://example.com/page")
    assert p.title == "Hello World"
    assert p.description == "A page"


def test_canonical_present():
    p = parse_page(HTML, "https://example.com/page")
    assert p.canonical_present is True


def test_og_presence_and_image():
    p = parse_page(HTML, "https://example.com/page")
    assert "og:title" in p.og_present
    assert "og:image" in p.og_present
    assert "og:description" not in p.og_present
    assert p.og_image == "https://example.com/img/og.png"


def test_links_absolute_http_only():
    p = parse_page(HTML, "https://example.com/page")
    assert "https://example.com/internal" in p.links
    assert "https://other.com/ext" in p.links
    assert all(u.startswith("http") for u in p.links)  # mailto excluded


def test_images_and_missing_alt():
    p = parse_page(HTML, "https://example.com/page")
    assert "https://example.com/img/a.png" in p.images
    assert "https://example.com/img/b.png" in p.images
    assert p.missing_alt == ["https://example.com/img/b.png"]


def test_missing_title_and_description_are_none():
    p = parse_page("<html><head></head><body></body></html>", "https://example.com/x")
    assert p.title is None
    assert p.description is None
    assert p.canonical_present is False
    assert p.og_present == []


def test_og_tags_constant():
    assert OG_TAGS == ["og:title", "og:description", "og:image", "og:type", "og:url"]
