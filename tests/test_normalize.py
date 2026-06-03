from spider.normalize import strip_www, same_site, normalize_identity


def test_strip_www():
    assert strip_www("www.example.com") == "example.com"
    assert strip_www("EXAMPLE.com") == "example.com"
    assert strip_www("blog.example.com") == "blog.example.com"


def test_same_site_ignores_www_and_case():
    assert same_site("https://example.com/a", "http://www.example.com/b")
    assert not same_site("https://example.com", "https://other.com")


def test_identity_collapses_trailing_slash():
    a = normalize_identity("https://example.com/about/", "https", "example.com")
    b = normalize_identity("https://example.com/about", "https", "example.com")
    assert a == b == "https://example.com/about"


def test_identity_collapses_scheme_and_www():
    base = normalize_identity("https://example.com/p", "https", "example.com")
    assert normalize_identity("http://example.com/p", "https", "example.com") == base
    assert normalize_identity("https://www.example.com/p", "https", "example.com") == base


def test_identity_root_keeps_single_slash():
    assert normalize_identity("https://example.com/", "https", "example.com") == "https://example.com/"


def test_identity_strips_fragment_keeps_query():
    out = normalize_identity("https://example.com/p?x=1#frag", "https", "example.com")
    assert out == "https://example.com/p?x=1"


def test_identity_uses_origin_scheme_for_relative_host():
    out = normalize_identity("http://www.example.com/p", "https", "example.com")
    assert out == "https://example.com/p"
