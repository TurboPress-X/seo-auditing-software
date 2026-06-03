"""robots.txt support: fetch, parse, and answer allow / crawl-delay questions.

Politeness matters for a tool other people will run: page crawling is gated by
the target site's robots.txt rules for our user-agent. A missing, empty, or
unreadable robots.txt is treated as allow-all (the web's conventional default).
"""

from urllib.robotparser import RobotFileParser


class Robots:
    """Allow/crawl-delay answers for a single origin. `parser is None` means
    allow-all (no usable robots.txt)."""

    def __init__(self, parser, user_agent, delay):
        self._parser = parser
        self._ua = user_agent
        self.delay = delay

    def allowed(self, url: str) -> bool:
        if self._parser is None:
            return True
        return self._parser.can_fetch(self._ua, url)


async def load_robots(client, origin_scheme: str, origin_host: str,
                      user_agent: str) -> Robots:
    """Fetch and parse /robots.txt for the origin. Any failure (network error,
    non-200, empty body) yields an allow-all Robots."""
    url = f"{origin_scheme}://{origin_host}/robots.txt"
    try:
        r = await client.get(url)
    except Exception:
        return Robots(None, user_agent, None)
    if r.status_code != 200 or not r.text.strip():
        return Robots(None, user_agent, None)
    parser = RobotFileParser()
    parser.parse(r.text.splitlines())
    return Robots(parser, user_agent, parser.crawl_delay(user_agent))
