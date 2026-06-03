#!/usr/bin/env python3
"""Entry point. Real logic lives in the `spider` package.

Usage:
    python seo_audit.py https://example.com --client "Example Co"
    python seo_audit.py --resume https://example.com
"""

from spider.cli import main

if __name__ == "__main__":
    main()
