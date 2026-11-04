#!/usr/bin/env python3
"""
uni_page_count.py - politely count the web pages on the uni.edu domain.

Non-invasive by design:
  * Primary method reads the site's own XML sitemaps (what search engines use),
    so a few dozen requests can account for thousands of pages.
  * Every request obeys robots.txt (including Crawl-delay), is rate limited per
    host (default 1 request every 2 seconds), identifies itself with a contact
    address, and backs off on 429/503 responses.
  * Optional subdomain discovery uses public Certificate Transparency logs
    (crt.sh), which sends no traffic to UNI beyond one robots.txt check per host.
  * Optional crawl mode is capped (default 500 pages) and fetches HTML only.

Standard library only (Python 3.8+). Examples:

  python uni_page_count.py --contact you@uni.edu
  python uni_page_count.py --contact you@uni.edu --discover-subdomains --out urls.csv
  python uni_page_count.py --contact you@uni.edu --crawl --max-pages 1000

If you are not UNI staff, consider asking UNI's web team first; they may be able
to give you an exact count from their CMS.
"""

import argparse
import collections
import csv
import gzip
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

# File types that are documents/assets rather than web pages.
NON_PAGE_EXT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf",
    ".csv", ".zip", ".gz", ".tar", ".rar", ".7z", ".jpg", ".jpeg", ".png",
    ".gif", ".svg", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".mp3", ".mp4",
    ".mov", ".avi", ".wmv", ".m4a", ".wav", ".css", ".js", ".json", ".xml",
    ".ics", ".woff", ".woff2", ".ttf", ".eot", ".exe", ".dmg", ".iso",
}


# --------------------------------------------------------------------------- #
# Polite HTTP client
# --------------------------------------------------------------------------- #
class PoliteFetcher:
    def __init__(self, user_agent, delay, timeout, max_bytes, verbose):
        self.ua = user_agent
        self.delay = delay
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.verbose = verbose
        self.last_hit = {}          # host -> monotonic time of last request
        self.robots = {}            # "scheme://host" -> RobotFileParser
        self.requests_made = 0

    def log(self, msg):
        if self.verbose:
            print(msg, file=sys.stderr)

    @staticmethod
    def _origin(url):
        p = urllib.parse.urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def _wait(self, host):
        gap = self.delay_for(host)
        last = self.last_hit.get(host)
        if last is not None:
            remaining = gap - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self.last_hit[host] = time.monotonic()

    def delay_for(self, host):
        rp = self.robots.get(host)
        crawl_delay = None
        if rp is not None:
            try:
                crawl_delay = rp.crawl_delay(self.ua)
            except Exception:
                crawl_delay = None
        return max(self.delay, float(crawl_delay or 0))

    def _raw_get(self, url, retries=2):
        """GET with rate limiting and back-off. Returns (status, headers, body, final_url)."""
        host = self._origin(url)
        for attempt in range(retries + 1):
            self._wait(host)
            req = urllib.request.Request(url, headers={
                "User-Agent": self.ua,
                "Accept": "text/html,application/xml,text/xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "identity",
            })
            self.requests_made += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        self.log(f"  ! {url} exceeds {self.max_bytes} bytes; truncated")
                        body = body[: self.max_bytes]
                    return resp.status, resp.headers, body, resp.geturl()
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt < retries:
                    retry_after = e.headers.get("Retry-After", "")
                    wait = int(retry_after) if retry_after.isdigit() else 60 * (attempt + 1)
                    wait = min(wait, 600)
                    self.log(f"  ! {e.code} from {url}; backing off {wait}s")
                    time.sleep(wait)
                    continue
                return e.code, e.headers, b"", url
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
                self.log(f"  ! {url}: {e}")
                return None, {}, b"", url
        return None, {}, b"", url

    def robots_for(self, url):
        host = self._origin(url)
        if host in self.robots:
            return self.robots[host]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host + "/robots.txt")
        status, _, body, _ = self._raw_get(host + "/robots.txt", retries=1)
        if status is None:
            rp = None                          # host unreachable
        elif status in (401, 403):
            rp.disallow_all = True
        elif status >= 400:
            rp.allow_all = True                # no robots.txt => everything allowed
        else:
            rp.parse(body.decode("utf-8", "replace").splitlines())
        self.robots[host] = rp
        return rp

    def allowed(self, url):
        rp = self.robots_for(url)
        return rp is not None and rp.can_fetch(self.ua, url)

    def get(self, url):
        if not self.allowed(url):
            self.log(f"  - robots.txt disallows {url}")
            return None, {}, b"", url
        return self._raw_get(url)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def in_domain(url, domain):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def normalize(url, keep_query=False):
    p = urllib.parse.urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    path = p.path or "/"
    if path.endswith("/index.html") or path.endswith("/index.htm") or path.endswith("/index.php"):
        path = path.rsplit("/", 1)[0] + "/"
    query = p.query if keep_query else ""
    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def page_key(url):
    """Scheme-agnostic key so http:// and https:// copies count once."""
    p = urllib.parse.urlsplit(url)
    return f"{p.netloc}{p.path}" + (f"?{p.query}" if p.query else "")


def is_page_url(url):
    path = urllib.parse.urlsplit(url).path.lower()
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return True
    return "." + last.rsplit(".", 1)[-1] not in NON_PAGE_EXT


def maybe_gunzip(body):
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError:
            return b""
    return body


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


# --------------------------------------------------------------------------- #
# Method 1: sitemaps
# --------------------------------------------------------------------------- #
def sitemap_urls_for_host(fetcher, origin):
    """Sitemap locations declared in robots.txt, else common defaults."""
    rp = fetcher.robots_for(origin)
    if rp is None:
        return []
    declared = list(rp.site_maps() or []) if hasattr(rp, "site_maps") else []
    if declared:
        return declared
    return [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]


def collect_from_sitemaps(fetcher, origins, domain, keep_query, max_sitemaps):
    pages, seen_sitemaps = {}, set()
    queue = collections.deque()
    for origin in origins:
        for sm in sitemap_urls_for_host(fetcher, origin):
            queue.append(sm)

    while queue and len(seen_sitemaps) < max_sitemaps:
        sm = queue.popleft()
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        status, _, body, _ = fetcher.get(sm)
        if status != 200 or not body:
            fetcher.log(f"  - sitemap {sm}: status {status}")
            continue
        body = maybe_gunzip(body)
        before = len(pages)
        try:
            root = ET.fromstring(body)
            kind = local(root.tag)
            for child in root:
                loc = next((c.text for c in child if local(c.tag) == "loc" and c.text), None)
                if not loc:
                    continue
                loc = loc.strip()
                if kind == "sitemapindex":
                    # Child sitemaps may live on a CDN host; the pages they list
                    # are still filtered to the domain below.
                    queue.append(loc)
                elif kind == "urlset" and in_domain(loc, domain):
                    u = normalize(loc, keep_query)
                    pages.setdefault(page_key(u), u)
        except ET.ParseError:
            # Plain-text sitemap: one URL per line.
            for line in body.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line.startswith("http") and in_domain(line, domain):
                    u = normalize(line, keep_query)
                    pages.setdefault(page_key(u), u)
        fetcher.log(f"  + {sm}: {len(pages) - before} new URLs ({len(pages)} total)")

    if queue:
        print(f"Note: stopped after {max_sitemaps} sitemap files; {len(queue)} more were queued. "
              f"Raise --max-sitemaps for a complete count.", file=sys.stderr)
    return list(pages.values()), len(seen_sitemaps)


# --------------------------------------------------------------------------- #
# Optional: subdomain discovery via Certificate Transparency
# --------------------------------------------------------------------------- #
def discover_subdomains(domain, user_agent, timeout):
    url = "https://crt.sh/?q=" + urllib.parse.quote(f"%.{domain}") + "&output=json"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=max(timeout, 60)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"Subdomain discovery via crt.sh failed ({e}); continuing with defaults.", file=sys.stderr)
        return []
    hosts = set()
    for row in data:
        for name in str(row.get("name_value", "")).splitlines():
            name = name.strip().lower().lstrip("*.")
            if name == domain or name.endswith("." + domain):
                hosts.add(name)
    return sorted(hosts)


# --------------------------------------------------------------------------- #
# Method 2 (optional): capped, polite crawl
# --------------------------------------------------------------------------- #
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.noindex_nofollow = [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            if "nofollow" not in (a.get("rel") or "").lower():
                self.links.append(a["href"])
        elif tag == "meta" and (a.get("name") or "").lower() == "robots":
            if "nofollow" in (a.get("content") or "").lower():
                self.noindex_nofollow = True


def crawl(fetcher, start_urls, domain, keep_query, max_pages):
    queue = collections.deque(normalize(u, keep_query) for u in start_urls)
    queued = {page_key(u) for u in queue}
    pages = {}
    while queue and len(pages) < max_pages:
        url = queue.popleft()
        if not is_page_url(url):
            continue
        status, headers, body, final = fetcher.get(url)
        ctype = (headers.get("Content-Type", "") if headers else "").lower()
        if status != 200 or "text/html" not in ctype:
            continue
        final = normalize(final, keep_query)
        if not in_domain(final, domain):
            continue
        pages.setdefault(page_key(final), final)
        if len(pages) % 25 == 0:
            print(f"  crawled {len(pages)} pages, {len(queue)} queued", file=sys.stderr)
        parser = LinkParser()
        try:
            parser.feed(body.decode(headers.get_content_charset() or "utf-8", "replace"))
        except Exception:
            continue
        if parser.noindex_nofollow:
            continue
        for href in parser.links:
            absu = urllib.parse.urljoin(final, href)
            if not absu.startswith(("http://", "https://")) or not in_domain(absu, domain):
                continue
            u = normalize(absu, keep_query)
            k = page_key(u)
            if k not in queued:
                queued.add(k)
                queue.append(u)
    return list(pages.values()), len(queue)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(urls, top):
    html = [u for u in urls if is_page_url(u)]
    docs = len(urls) - len(html)
    by_host = collections.Counter(urllib.parse.urlsplit(u).netloc for u in html)
    by_section = collections.Counter()
    for u in html:
        p = urllib.parse.urlsplit(u)
        first = p.path.strip("/").split("/", 1)[0]
        by_section[f"{p.netloc}/{first}"] += 1

    print("\n================ RESULTS ================")
    print(f"Unique web pages (HTML):   {len(html):,}")
    print(f"Documents/files excluded:  {docs:,}")
    print(f"Hosts with pages:          {len(by_host):,}")
    print("\nPages by host:")
    for host, n in by_host.most_common(top):
        print(f"  {n:>8,}  {host}")
    print(f"\nLargest sections (top {top}):")
    for sec, n in by_section.most_common(top):
        print(f"  {n:>8,}  {sec}")
    return html


def main():
    ap = argparse.ArgumentParser(description="Politely count web pages on a domain (default uni.edu).")
    ap.add_argument("--domain", default="uni.edu", help="Domain to count (subdomains included).")
    ap.add_argument("--contact", required=True,
                    help="Your email; sent in the User-Agent so site admins can reach you.")
    ap.add_argument("--hosts", nargs="*", default=None,
                    help="Hosts to check (default: the domain and www.domain).")
    ap.add_argument("--discover-subdomains", action="store_true",
                    help="Find more hosts via public certificate logs (crt.sh).")
    ap.add_argument("--crawl", action="store_true",
                    help="Also do a capped crawl (use when sitemaps are missing or incomplete).")
    ap.add_argument("--max-pages", type=int, default=500, help="Crawl cap (default 500).")
    ap.add_argument("--max-sitemaps", type=int, default=500, help="Max sitemap files to read.")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="Minimum seconds between requests to the same host (default 2.0).")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--keep-query", action="store_true",
                    help="Treat URLs that differ only by ?query as separate pages.")
    ap.add_argument("--out", help="Write the list of page URLs to this CSV file.")
    ap.add_argument("--top", type=int, default=20, help="Rows to show in breakdowns.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.delay < 1.0:
        ap.error("--delay below 1 second is not considered polite; use 1.0 or more.")

    domain = args.domain.lower().strip(".")
    ua = f"PageCountBot/1.0 (non-commercial page count; contact: {args.contact})"
    fetcher = PoliteFetcher(ua, args.delay, args.timeout, 60 * 1024 * 1024, args.verbose)

    hosts = args.hosts or [domain, f"www.{domain}"]
    if args.discover_subdomains:
        found = discover_subdomains(domain, ua, args.timeout)
        print(f"Certificate logs list {len(found)} hostnames under {domain}.", file=sys.stderr)
        hosts = sorted(set(hosts) | set(found))
    origins = [h if h.startswith("http") else f"https://{h}" for h in hosts]

    print(f"Reading robots.txt and sitemaps for {len(origins)} host(s)...", file=sys.stderr)
    urls, n_sitemaps = collect_from_sitemaps(fetcher, origins, domain, args.keep_query, args.max_sitemaps)
    print(f"Sitemaps read: {n_sitemaps}; URLs listed: {len(urls):,}", file=sys.stderr)

    if args.crawl:
        live = [o for o in origins if fetcher.robots.get(PoliteFetcher._origin(o)) is not None]
        print(f"Crawling up to {args.max_pages} pages from {len(live)} reachable host(s)...",
              file=sys.stderr)
        crawled, left = crawl(fetcher, [o + "/" for o in live], domain, args.keep_query, args.max_pages)
        known = {page_key(u) for u in urls}
        extra = [u for u in crawled if page_key(u) not in known]
        print(f"Crawl found {len(crawled):,} pages, {len(extra):,} not in any sitemap"
              + (f"; {left:,} links left unvisited (cap reached)" if left else ""), file=sys.stderr)
        urls += extra

    html = report(urls, args.top)
    print(f"\nHTTP requests made to the site: {fetcher.requests_made:,}")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["url", "host"])
            for u in sorted(html):
                w.writerow([u, urllib.parse.urlsplit(u).netloc])
        print(f"Wrote {len(html):,} URLs to {args.out}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
