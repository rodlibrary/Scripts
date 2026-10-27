#!/usr/bin/env python3
"""
find_scholarworks_links.py

Crawl uni.edu websites and record every link that points to
https://scholarworks.uni.edu/ in a CSV file.

Usage examples:
    python find_scholarworks_links.py
    python find_scholarworks_links.py --max-pages 20000 --delay 0.5
    python find_scholarworks_links.py --start https://library.uni.edu/ --no-subdomains
    python find_scholarworks_links.py --exclude-host calendar.uni.edu --ignore-query
    python find_scholarworks_links.py --retries 3 --max-host-failures 10

Excluded sites:
    aspace.library.uni.edu (ArchivesSpace) is always skipped: links to it are
    ignored and never fetched. Add more with --exclude-host.

Failure handling:
    Each page is retried (--retries, with doubling --retry-backoff) on timeouts,
    connection errors, HTTP 429 and 5xx. If --max-host-failures pages on the same
    host fail in a row, that host is skipped for the rest of the run. If
    --max-consecutive-failures pages fail in a row across all hosts, the crawl
    stops (this usually means the network is down). Results found so far are kept.

Requirements:
    pip install requests beautifulsoup4

Output CSV columns:
    source_page   - the uni.edu page that contains the link
    page_title    - <title> of that page
    link_url      - the ScholarWorks URL that was found (absolute)
    link_text     - visible anchor text (or alt/title text if none)
    html_tag      - the tag the link came from (a, area, iframe, embed)
    found_at      - timestamp (UTC) when the link was recorded
"""

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT_DOMAIN = "uni.edu"
TARGET_HOST = "scholarworks.uni.edu"

# Hosts that are never crawled. Links to them are ignored and the crawl moves on.
# ArchivesSpace generates a huge number of finding-aid pages that slow the crawl.
ALWAYS_EXCLUDED_HOSTS = {
    "aspace.library.uni.edu",
}
USER_AGENT = "UNI-ScholarWorks-LinkAudit/1.0 (+https://www.uni.edu/)"

# File types that are never HTML pages, so there is no point downloading them.
SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|gz|tar|rar|7z|jpe?g|png|gif|svg|webp|ico|bmp|tiff?"
    r"|mp3|mp4|m4a|mov|avi|wmv|webm|ogg|wav|css|js|json|xml|rss|ics|exe|dmg|iso|csv|txt)$",
    re.IGNORECASE,
)

# URL patterns that commonly produce endless crawl traps on university sites.
TRAP_PATTERNS = re.compile(
    r"(/calendar/|/events?/\d{4}|[?&](page|start|offset|sort|order|month|year|date)=)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #
def normalize(url, ignore_query=False):
    """Return a canonical form of url (no fragment, lowercase host, no default port)."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or "/"
    query = "" if ignore_query else parsed.query
    return urlunparse((scheme, netloc, path, "", query, ""))


def host_of(url):
    return (urlparse(url).hostname or "").lower()


def is_target_link(url):
    """True if url points to scholarworks.uni.edu (http or https)."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() == TARGET_HOST


def in_scope(url, allowed_hosts, include_subdomains, excluded_hosts):
    """True if url is a uni.edu page we should crawl."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in excluded_hosts:
        return False
    if include_subdomains:
        if not (host == ROOT_DOMAIN or host.endswith("." + ROOT_DOMAIN)):
            return False
    elif host not in allowed_hosts:
        return False
    if SKIP_EXTENSIONS.search(parsed.path):
        return False
    return True


# --------------------------------------------------------------------------- #
# robots.txt handling (cached per host)
# --------------------------------------------------------------------------- #
class RobotsCache:
    def __init__(self, session, timeout):
        self.session = session
        self.timeout = timeout
        self.cache = {}

    def _load(self, scheme, host):
        rp = robotparser.RobotFileParser()
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # no robots.txt -> everything allowed
        except requests.RequestException:
            rp.parse([])
        return rp

    def allowed(self, url):
        parsed = urlparse(url)
        key = (parsed.scheme, parsed.netloc)
        if key not in self.cache:
            self.cache[key] = self._load(parsed.scheme, parsed.netloc)
        return self.cache[key].can_fetch(USER_AGENT, url)

    def sitemaps(self, url):
        parsed = urlparse(url)
        key = (parsed.scheme, parsed.netloc)
        if key not in self.cache:
            self.cache[key] = self._load(parsed.scheme, parsed.netloc)
        return self.cache[key].site_maps() or []


# --------------------------------------------------------------------------- #
# Sitemap seeding
# --------------------------------------------------------------------------- #
def urls_from_sitemap(session, sitemap_url, timeout, limit=50000, _depth=0):
    """Collect page URLs from a sitemap or sitemap index (recursive, bounded)."""
    found = []
    if _depth > 3:
        return found
    try:
        resp = session.get(sitemap_url, timeout=timeout)
        if resp.status_code != 200:
            return found
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError):
        return found

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if root.tag.endswith("sitemapindex"):
        for loc in root.findall(".//sm:sitemap/sm:loc", ns):
            if len(found) >= limit:
                break
            found.extend(
                urls_from_sitemap(session, loc.text.strip(), timeout, limit - len(found), _depth + 1)
            )
    else:
        for loc in root.findall(".//sm:url/sm:loc", ns):
            if loc.text:
                found.append(loc.text.strip())
            if len(found) >= limit:
                break
    return found


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def link_text_for(tag):
    text = " ".join(tag.get_text(" ", strip=True).split())
    if not text:
        img = tag.find("img")
        if img is not None:
            text = img.get("alt", "") or img.get("title", "")
    if not text:
        text = tag.get("title", "") or tag.get("aria-label", "") or tag.get("alt", "")
    return text.strip()


def extract_links(html, page_url):
    """Return (title, list of (absolute_url, text, tag_name)) for a page."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    # Respect <base href> if present.
    base = soup.find("base", href=True)
    base_url = urljoin(page_url, base["href"]) if base else page_url

    links = []
    for tag in soup.find_all(["a", "area"], href=True):
        links.append((urljoin(base_url, tag["href"].strip()), link_text_for(tag), tag.name))
    for tag in soup.find_all(["iframe", "embed"], src=True):
        links.append((urljoin(base_url, tag["src"].strip()), tag.get("title", ""), tag.name))
    return title, links


# --------------------------------------------------------------------------- #
# Fetching with retries
# --------------------------------------------------------------------------- #
RETRY_STATUSES = {429, 500, 502, 503, 504}


def fetch_with_retries(session, url, timeout, retries, backoff):
    """
    GET url, retrying on connection errors, timeouts, 429 and 5xx responses.

    Returns (response, None) on success, or (None, reason) once every attempt failed.
    A 404 or other 4xx is a real answer from the server, not a failure, so it is
    returned straight away without retrying.
    """
    reason = "unknown error"
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            reason = type(exc).__name__
            wait = backoff * (2 ** attempt)
        else:
            if resp.status_code not in RETRY_STATUSES:
                return resp, None
            reason = f"HTTP {resp.status_code}"
            wait = backoff * (2 ** attempt)
            retry_after = resp.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = max(wait, min(int(retry_after), 60))  # honour it, capped at 60s
        if attempt < retries:
            time.sleep(wait)
    return None, reason


# --------------------------------------------------------------------------- #
# Main crawl
# --------------------------------------------------------------------------- #
def crawl(args):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    robots = RobotsCache(session, args.timeout)

    start_urls = [normalize(u, args.ignore_query) for u in args.start]
    allowed_hosts = {host_of(u) for u in start_urls}
    excluded_hosts = {h.lower() for h in args.exclude_host} | {TARGET_HOST} | ALWAYS_EXCLUDED_HOSTS

    def scoped(u):
        return in_scope(u, allowed_hosts, not args.no_subdomains, excluded_hosts)

    start_urls = [u for u in start_urls if host_of(u) not in excluded_hosts]
    if not start_urls:
        sys.exit("All --start URLs are on excluded hosts; nothing to crawl.")
    queue = deque((u, 0) for u in start_urls)
    seen = set(start_urls)

    # Seed the queue from sitemaps (robots.txt-declared ones plus /sitemap.xml).
    if not args.no_sitemaps:
        for start in start_urls:
            parsed = urlparse(start)
            candidates = set(robots.sitemaps(start)) | {f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"}
            for sm in candidates:
                for u in urls_from_sitemap(session, sm, args.timeout, limit=args.max_pages):
                    nu = normalize(u, args.ignore_query)
                    if nu not in seen and scoped(nu):
                        seen.add(nu)
                        queue.append((nu, 1))
        print(f"Queued {len(queue)} URLs after sitemap seeding.", file=sys.stderr)

    found_pairs = set()
    pages_crawled = 0
    errors = 0

    # Failure tracking: a host that keeps failing is skipped for the rest of the
    # run, and the whole crawl stops if everything is failing (e.g. network down).
    host_failures = {}        # host -> consecutive failed pages on that host
    skipped_hosts = {}        # host -> reason it was skipped
    skipped_urls = 0
    consecutive_failures = 0  # across all hosts
    aborted = False

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_page", "page_title", "link_url", "link_text", "html_tag", "found_at"])

        try:
            while queue and pages_crawled < args.max_pages:
                url, depth = queue.popleft()
                host = host_of(url)

                if host in skipped_hosts:
                    skipped_urls += 1
                    continue

                if not robots.allowed(url):
                    continue

                resp, reason = fetch_with_retries(
                    session, url, args.timeout, args.retries, args.retry_backoff
                )
                if resp is None:
                    errors += 1
                    consecutive_failures += 1
                    host_failures[host] = host_failures.get(host, 0) + 1
                    if args.verbose:
                        print(f"  ! {url}: {reason} (after {args.retries + 1} attempts)", file=sys.stderr)

                    if host_failures[host] >= args.max_host_failures:
                        skipped_hosts[host] = f"{host_failures[host]} failures in a row, last: {reason}"
                        print(
                            f"  Skipping {host} for the rest of the run: "
                            f"{host_failures[host]} pages failed in a row (last: {reason}).",
                            file=sys.stderr,
                        )

                    if args.max_consecutive_failures and consecutive_failures >= args.max_consecutive_failures:
                        print(
                            f"\nStopping: {consecutive_failures} pages failed in a row across all hosts "
                            f"(last: {reason}). Check your network connection.",
                            file=sys.stderr,
                        )
                        aborted = True
                        break
                    continue

                consecutive_failures = 0
                host_failures[host] = 0
                pages_crawled += 1
                final_url = normalize(resp.url, args.ignore_query)

                # Redirected off uni.edu (or to an excluded host)? Don't parse it.
                if final_url != url and not scoped(final_url):
                    continue
                seen.add(final_url)

                ctype = resp.headers.get("Content-Type", "")
                if resp.status_code != 200 or "html" not in ctype.lower():
                    continue

                title, links = extract_links(resp.text, resp.url)

                new_hits = 0
                for link, text, tag_name in links:
                    if is_target_link(link):
                        key = (final_url, link)
                        if key not in found_pairs:
                            found_pairs.add(key)
                            new_hits += 1
                            writer.writerow([
                                final_url, title, link, text, tag_name,
                                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            ])
                        continue

                    if args.max_depth is not None and depth + 1 > args.max_depth:
                        continue
                    nu = normalize(link, args.ignore_query)
                    if nu in seen or not scoped(nu):
                        continue
                    if not args.allow_traps and TRAP_PATTERNS.search(nu):
                        continue
                    seen.add(nu)
                    queue.append((nu, depth + 1))

                if new_hits:
                    fh.flush()  # keep results safe if the run is interrupted

                if args.verbose or new_hits:
                    print(
                        f"[{pages_crawled}/{args.max_pages}] queue={len(queue)} "
                        f"hits+{new_hits} total={len(found_pairs)}  {final_url}",
                        file=sys.stderr,
                    )
                elif pages_crawled % 50 == 0:
                    print(
                        f"[{pages_crawled}/{args.max_pages}] queue={len(queue)} "
                        f"total links found={len(found_pairs)}",
                        file=sys.stderr,
                    )

                time.sleep(args.delay)

        except KeyboardInterrupt:
            print("\nInterrupted; results so far have been saved.", file=sys.stderr)

    print(
        f"\nDone. Crawled {pages_crawled} pages ({errors} errors). "
        f"Found {len(found_pairs)} ScholarWorks links on "
        f"{len({p for p, _ in found_pairs})} pages. Saved to {args.output}",
        file=sys.stderr,
    )
    if skipped_hosts:
        print(f"Skipped {len(skipped_hosts)} host(s) after repeated failures "
              f"({skipped_urls} queued URLs not fetched):", file=sys.stderr)
        for h, why in sorted(skipped_hosts.items()):
            print(f"  - {h}: {why}", file=sys.stderr)
    if aborted:
        print(f"Note: crawl stopped early with {len(queue)} URLs still queued.", file=sys.stderr)
    elif queue and pages_crawled >= args.max_pages:
        print(f"Note: stopped at --max-pages with {len(queue)} URLs still queued.", file=sys.stderr)


def parse_args():
    p = argparse.ArgumentParser(description="Find links to scholarworks.uni.edu across uni.edu websites.")
    p.add_argument("--start", nargs="+", default=["https://www.uni.edu/"],
                   help="Starting URL(s). Default: https://www.uni.edu/")
    p.add_argument("--output", default="scholarworks_links.csv", help="CSV output path.")
    p.add_argument("--max-pages", type=int, default=5000, help="Maximum pages to fetch (default 5000).")
    p.add_argument("--max-depth", type=int, default=None, help="Maximum link depth from start pages.")
    p.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between requests (default 0.5).")
    p.add_argument("--timeout", type=float, default=15, help="Request timeout in seconds (default 15).")
    p.add_argument("--retries", type=int, default=2,
                   help="Extra attempts per page after a timeout, connection error, 429 or 5xx (default 2).")
    p.add_argument("--retry-backoff", type=float, default=1.0,
                   help="Seconds before the first retry; doubles each retry (default 1.0).")
    p.add_argument("--max-host-failures", type=int, default=5,
                   help="Skip a host after this many pages on it fail in a row (default 5).")
    p.add_argument("--max-consecutive-failures", type=int, default=25,
                   help="Stop the whole crawl after this many failures in a row across all hosts "
                        "(default 25; 0 = never stop).")
    p.add_argument("--no-subdomains", action="store_true",
                   help="Only crawl the start URLs' hosts instead of all *.uni.edu subdomains.")
    p.add_argument("--exclude-host", nargs="*", default=[],
                   help="Hosts to skip, e.g. calendar.uni.edu catalog.uni.edu")
    p.add_argument("--ignore-query", action="store_true",
                   help="Drop ?query strings when deduplicating pages (reduces duplicates).")
    p.add_argument("--allow-traps", action="store_true",
                   help="Crawl calendar/pagination URLs that are skipped by default.")
    p.add_argument("--no-sitemaps", action="store_true", help="Don't seed the crawl from sitemaps.")
    p.add_argument("--verbose", action="store_true", help="Print every page crawled.")
    return p.parse_args()


if __name__ == "__main__":
    crawl(parse_args())
