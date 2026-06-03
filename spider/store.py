"""SQLite-backed crawl store. Holds pages, links, images, status cache, and the
frontier/visited sets that make a crawl resumable. og_present is stored as a
comma-joined string and split on read."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    identity TEXT PRIMARY KEY,
    display  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visited (
    identity TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS pages (
    page_id           TEXT PRIMARY KEY,
    identity_url      TEXT UNIQUE NOT NULL,
    display_url       TEXT NOT NULL,
    status            INTEGER,
    title             TEXT,
    description       TEXT,
    og_present        TEXT,
    og_image          TEXT,
    canonical_present INTEGER
);
CREATE TABLE IF NOT EXISTS links (
    found_on_id  TEXT,
    found_on_url TEXT,
    target       TEXT
);
CREATE TABLE IF NOT EXISTS images (
    found_on_id  TEXT,
    found_on_url TEXT,
    src          TEXT,
    missing_alt  INTEGER
);
CREATE TABLE IF NOT EXISTS status_cache (
    url       TEXT PRIMARY KEY,
    code      TEXT,
    hops      INTEGER,
    final_url TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def enqueue(conn, items) -> None:
    """items: iterable of (identity, display). Skips already-visited identities."""
    for identity, display in items:
        row = conn.execute("SELECT 1 FROM visited WHERE identity=?", (identity,)).fetchone()
        if row:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO frontier(identity, display) VALUES (?, ?)",
            (identity, display),
        )
    conn.commit()


def next_batch(conn, n: int):
    """Read up to n queued items WITHOUT removing them. A URL leaves the frontier
    only when mark_visited records it (after its page is saved), so an interrupted
    crawl re-processes in-flight URLs on resume rather than losing them."""
    rows = conn.execute("SELECT identity, display FROM frontier LIMIT ?", (n,)).fetchall()
    return [(r["identity"], r["display"]) for r in rows]


def mark_visited(conn, identity: str) -> None:
    conn.execute("INSERT OR IGNORE INTO visited(identity) VALUES (?)", (identity,))
    conn.execute("DELETE FROM frontier WHERE identity=?", (identity,))
    conn.commit()


def count_visited(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM visited").fetchone()[0]


def count_frontier(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]


def save_page(conn, page_id, identity, display, status, title, description,
              og_present, og_image, canonical_present) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO pages
           (page_id, identity_url, display_url, status, title, description,
            og_present, og_image, canonical_present)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (page_id, identity, display, status, title, description,
         ",".join(og_present), og_image, 1 if canonical_present else 0),
    )
    conn.commit()


def save_link(conn, found_on_id, found_on_url, target) -> None:
    conn.execute(
        "INSERT INTO links(found_on_id, found_on_url, target) VALUES (?,?,?)",
        (found_on_id, found_on_url, target),
    )


def save_image(conn, found_on_id, found_on_url, src, missing_alt) -> None:
    conn.execute(
        "INSERT INTO images(found_on_id, found_on_url, src, missing_alt) VALUES (?,?,?,?)",
        (found_on_id, found_on_url, src, 1 if missing_alt else 0),
    )


def delete_edges(conn, found_on_id: str) -> None:
    """Remove prior link/image rows for a page so a re-crawl (e.g. after resume)
    does not duplicate them. Flushed by the next committing call."""
    conn.execute("DELETE FROM links WHERE found_on_id=?", (found_on_id,))
    conn.execute("DELETE FROM images WHERE found_on_id=?", (found_on_id,))


def save_status(conn, url, code, hops, final_url) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO status_cache(url, code, hops, final_url) VALUES (?,?,?,?)",
        (url, str(code), hops, final_url),
    )
    conn.commit()


def _coerce_code(code: str):
    return int(code) if code.lstrip("-").isdigit() else code


def get_status(conn, url):
    row = conn.execute(
        "SELECT code, hops, final_url FROM status_cache WHERE url=?", (url,)
    ).fetchone()
    if not row:
        return None
    return (_coerce_code(row["code"]), row["hops"], row["final_url"])


def iter_pages(conn):
    rows = conn.execute("SELECT * FROM pages").fetchall()
    for r in rows:
        yield {
            "page_id": r["page_id"],
            "identity_url": r["identity_url"],
            "display_url": r["display_url"],
            "status": r["status"],
            "title": r["title"],
            "description": r["description"],
            "og_present": r["og_present"].split(",") if r["og_present"] else [],
            "og_image": r["og_image"],
            "canonical_present": r["canonical_present"],
        }


def iter_links(conn):
    rows = conn.execute("SELECT * FROM links").fetchall()
    for r in rows:
        yield {"found_on_id": r["found_on_id"], "found_on_url": r["found_on_url"],
               "target": r["target"]}


def iter_images(conn):
    rows = conn.execute("SELECT * FROM images").fetchall()
    for r in rows:
        yield {"found_on_id": r["found_on_id"], "found_on_url": r["found_on_url"],
               "src": r["src"], "missing_alt": r["missing_alt"]}
