from spider.store import (
    connect, init_schema, enqueue, next_batch, mark_visited,
    count_visited, count_frontier, save_page, save_link, save_image,
    save_status, get_status, iter_pages, iter_links, iter_images,
)


def make(tmp_path):
    conn = connect(str(tmp_path / "crawl.db"))
    init_schema(conn)
    return conn


def test_frontier_enqueue_dedup_and_batch(tmp_path):
    conn = make(tmp_path)
    enqueue(conn, [("https://e.com/a", "https://e.com/a"),
                   ("https://e.com/b", "https://e.com/b")])
    enqueue(conn, [("https://e.com/a", "https://e.com/a")])  # dup ignored
    assert count_frontier(conn) == 2
    batch = next_batch(conn, 10)
    assert len(batch) == 2
    assert count_frontier(conn) == 0  # next_batch removes from frontier


def test_visited_blocks_requeue(tmp_path):
    conn = make(tmp_path)
    mark_visited(conn, "https://e.com/a")
    enqueue(conn, [("https://e.com/a", "https://e.com/a")])
    assert count_frontier(conn) == 0
    assert count_visited(conn) == 1


def test_save_and_iter_pages(tmp_path):
    conn = make(tmp_path)
    save_page(conn, "ID-1", "https://e.com/a", "https://e.com/a/", 200,
              "Title", "Desc", ["og:title"], "https://e.com/og.png", True)
    rows = list(iter_pages(conn))
    assert len(rows) == 1
    r = rows[0]
    assert r["page_id"] == "ID-1"
    assert r["display_url"] == "https://e.com/a/"
    assert r["status"] == 200
    assert r["title"] == "Title"
    assert r["og_present"] == ["og:title"]
    assert r["og_image"] == "https://e.com/og.png"
    assert r["canonical_present"] == 1


def test_save_links_and_images(tmp_path):
    conn = make(tmp_path)
    save_link(conn, "ID-1", "https://e.com/a", "https://e.com/x")
    save_image(conn, "ID-1", "https://e.com/a", "https://e.com/i.png", True)
    links = list(iter_links(conn))
    images = list(iter_images(conn))
    assert links[0]["target"] == "https://e.com/x"
    assert images[0]["missing_alt"] == 1


def test_status_roundtrip(tmp_path):
    conn = make(tmp_path)
    save_status(conn, "https://e.com/x", 301, 1, "https://e.com/y")
    assert get_status(conn, "https://e.com/x") == (301, 1, "https://e.com/y")
    assert get_status(conn, "https://e.com/missing") is None
