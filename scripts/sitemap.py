"""
Generate sitemap.xml and robots.txt for the built site.

Deliberately works by scanning the output directory rather than by being told
what was built: the sitemap then cannot drift from what is actually published.
Any page carrying <meta name="robots" content="noindex..."> is skipped, which
is what keeps the ~362 pages with no published remarks out of the index
while their addresses keep working.

Usage:
    python sitemap.py <site-dir> [--base-url https://medinsky.net]
"""

import argparse
import os
import re
import sys
from datetime import date
from typing import List, Optional
from xml.sax.saxutils import escape

NOINDEX_RE = re.compile(
    r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex', re.IGNORECASE
)

# Never advertise these: the editor surface, the duplicate kept for tests, and
# anything under the cabinet (authenticated) or the offline bundle staging.
SKIP_NAMES = {"document_index.html"}
SKIP_DIRS = {"cabinet", "app", ".git"}


def is_indexable(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            # robots/canonical live in <head>; no need to read whole pages.
            head = f.read(4096)
    except (OSError, UnicodeDecodeError):
        return False
    return not NOINDEX_RE.search(head)


def url_for(site_dir: str, path: str, base_url: str) -> str:
    rel = os.path.relpath(path, site_dir).replace(os.sep, "/")
    # Serve directory indexes as clean URLs: blog/x/index.html -> /blog/x/
    if rel == "index.html":
        return base_url + "/"
    if rel.endswith("/index.html"):
        return f"{base_url}/{rel[:-len('index.html')]}"
    return f"{base_url}/{rel}"


def collect_urls(site_dir: str, base_url: str) -> List[str]:
    urls = []
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".html") or name in SKIP_NAMES:
                continue
            path = os.path.join(root, name)
            if not is_indexable(path):
                continue
            urls.append(url_for(site_dir, path, base_url))
    return sorted(set(urls))


def render_sitemap(urls: List[str], lastmod: Optional[str] = None) -> str:
    stamp = lastmod or date.today().isoformat()
    entries = "\n".join(
        f"  <url><loc>{escape(u)}</loc><lastmod>{stamp}</lastmod></url>" for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def render_robots(base_url: str) -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        # The editor and the cabinet are behind auth and have nothing to index.
        "Disallow: /cabinet/\n"
        "Disallow: /app/\n"
        "Disallow: /*?editor=\n"
        f"\nSitemap: {base_url}/sitemap.xml\n"
    )


def generate(site_dir: str, base_url: str) -> List[str]:
    urls = collect_urls(site_dir, base_url.rstrip("/"))
    with open(os.path.join(site_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(render_sitemap(urls))
    with open(os.path.join(site_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(render_robots(base_url.rstrip("/")))
    print(f"[sitemap] wrote sitemap.xml with {len(urls)} URLs and robots.txt to {site_dir}")
    return urls


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_dir", help="built site root (redpen-publish or --target-dir)")
    parser.add_argument("--base-url", default=os.getenv("REDPEN_SITE_URL", "https://medinsky.net"))
    args = parser.parse_args(argv)
    if not os.path.isdir(args.site_dir):
        print(f"[sitemap] not a directory: {args.site_dir}", file=sys.stderr)
        return 1
    generate(args.site_dir, args.base_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
