#!/usr/bin/env python3
"""
Gather Preservica statistics once a month and write them into the DCDS stats
Google Sheet.

What it measures (everything the account can see):
  * counts: collections (top-level folders), folders, assets, files;
  * assets by kind: images, sound recordings, moving images, text, other;
  * what was added this month (by each record's created date);
  * storage: total, Hot and Cold tiers, each storage location, Preservation
    and Access representations, current and older generations, original and
    migrated files, files kept in only one tier, duplicate files (same SHA1
    in more than one asset), and terabytes per collection;
  * assets per security tag;
  * integrity checks: files checked this month, files whose last check failed;
  * downloads (Preservica's download counter; the month's figure is the
    change since last month's all-time count).

Where it writes, in the month's column (January..December in row 1):
  * the Preservica tab: every figure above. The tab is laid out like the
    others (Category | Metric rows), and the script adds rows it needs;
  * the Storage tab: Tier 1 #TBPreservicaHot and #TBPreservicaCold;
  * the DCDS tab: the Preservica rows (#Collections, #Assets, #Images,
    #SoundRecordings, #MovingImages, #Text, #Downloads). By default these are
    what was added that month, like the tab's other monthly rows; use
    --dcds-counts total for all-time totals instead.

Usage
    python preservica_monthly_stats.py --dry-run           # measure and print, write nothing
    python preservica_monthly_stats.py --sheet-id <ID>     # measure and write the sheet
    python preservica_monthly_stats.py --sheet-id <ID> --month 2026-10

Needs:  pip install requests google-auth   (and pyPreservica for the credentials helper)
Preservica login: PRESERVICA_* environment variables or credentials.properties,
exactly as for preservica_search_export.py.
Google login: a service account key, either in the GOOGLE_SERVICE_ACCOUNT_KEY
environment variable (the JSON text) or a file given with --google-key
(key.json is ignored by git). Share the spreadsheet with the service
account's e-mail address as an Editor.

The script prints no figures unless --show (or --dry-run) is given, because
the scheduled run's log is public.
"""

import argparse
import calendar
import datetime as dt
import json
import logging
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("preservica_monthly_stats")

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MONTHS = [calendar.month_name[i] for i in range(1, 13)]
TB = 10 ** 12          # decimal terabytes, as storage is billed
DEFAULT_COLD = r"glacier|deep|cold|archive|tape|offline"
DOWNLOAD_FIELD = "system_and_public_analytics.downloadcount"

# Preservica format groups (e.g. "pdf", "image", "audio") sorted into the
# DCDS kinds. Anything unmatched counts as Other; the Preservica tab lists
# every format group seen so this mapping can be checked.
KINDS = [
    ("Images", r"image|raster|photo|tif|jpe?g|jp2|png|gif|bmp|dng|raw"),
    ("SoundRecordings", r"audio|sound|wav|mp3|flac|aiff?"),
    ("MovingImages", r"video|moving|film|mp4|mov|avi|mkv|mxf|mpeg"),
    ("Text", r"pdf|text|txt|document|doc|word|office|epub|html?|xml|rtf|odt|book|presentation|spreadsheet"),
]


# --------------------------------------------------------------------------- Preservica

class Preservica:
    """A small client: logs in, refreshes the 15-minute token, retries politely."""

    def __init__(self, creds, timeout=120):
        self.creds = creds
        self.base = f"https://{creds['server']}"
        self.timeout = timeout
        self.session = requests.Session()
        self.token = None
        self.token_time = 0
        self.lock = threading.Lock()
        self.status = Counter()        # (kind of request, HTTP status) -> count

    def login(self):
        data = {"username": self.creds["username"], "password": self.creds["password"],
                "tenant": self.creds["tenant"]}
        if self.creds.get("two_fa"):
            data["twoFactorToken"] = self.creds["two_fa"]
        r = self.session.post(f"{self.base}/api/accesstoken/login", data=data, timeout=self.timeout)
        if r.status_code != 200:
            detail = r.text[:200].replace(self.creds["password"], "***")
            sys.exit(f"Preservica login failed (HTTP {r.status_code}): {detail}")
        self.token = r.json()["token"]
        self.token_time = time.time()

    def _token(self, force=False):
        with self.lock:
            if force or not self.token or time.time() - self.token_time > 12 * 60:
                self.login()
            return self.token

    def request(self, method, url, kind, **kw):
        """One request with the token; logs in again on 401 and retries busy or failing servers."""
        if not url.startswith("http"):
            url = self.base + url
        r, code, force = None, None, False
        for attempt in range(4):
            headers = {"Preservica-Access-Token": self._token(force=force)}
            force = False
            try:
                r = self.session.request(method, url, headers=headers, timeout=self.timeout, **kw)
            except requests.RequestException as exc:
                log.debug("%s %s failed: %s", method, url, exc)
                r, code = None, None
                time.sleep(2 ** attempt)
                continue
            code = r.status_code
            if code == 401 and attempt == 0:
                force = True
                continue
            if code == 429 or (code >= 500 and attempt < 2):
                time.sleep(2 ** (attempt + 1))
                continue
            break
        self.status[(kind, code)] += 1
        return r

    def get_xml(self, url, kind, **kw):
        r = self.request("GET", url, kind, **kw)
        if r is None or r.status_code != 200:
            return None
        try:
            return ET.fromstring(r.content)
        except ET.ParseError:
            self.status[(kind, "unreadable")] += 1
            return None

    def search(self, filters, fields, page=500):
        """Yield {field: value} dicts (plus 'ref') for every search hit."""
        q_fields = [{"name": k, "values": v} for k, v in filters.items()]
        q_fields += [{"name": f, "values": []} for f in fields if f not in filters]
        start, total = 0, None
        while total is None or start < total:
            data = [("start", str(start)), ("max", str(page)),
                    ("q", json.dumps({"q": "*", "fields": q_fields}))]
            data += [("metadata", f) for f in fields]
            r = self.request("POST", "/api/content/search", "search", data=data)
            if r is None or r.status_code != 200:
                sys.exit(f"Search failed (HTTP {getattr(r, 'status_code', 'no answer')}): "
                         f"{getattr(r, 'text', '')[:300]}")
            value = r.json().get("value", {})
            total = int(value.get("totalHits") or 0)
            hits = value.get("metadata") or []
            ids = value.get("objectIds") or []
            for i, rec in enumerate(hits):
                row = {f["name"]: f.get("value") for f in rec}
                row["ref"] = (ids[i] if i < len(ids) else "").split("|")[-1]
                yield row
            if not hits:
                break
            start += len(hits)

    def root_folders(self):
        """[(ref, title)] of the top-level folders."""
        out, url = [], "/api/entity/root/children?start=0&max=100"
        while url:
            doc = self.get_xml(url, "root children")
            if doc is None:
                break
            for child in iter_local(doc, "Child"):
                if child.get("type") == "SO":
                    out.append((child.get("ref"), child.get("title") or child.get("ref")))
            nxt = first_text(doc, "Next")
            url = nxt if nxt and nxt.startswith("http") else None
        return out


def local(tag):
    return tag.rsplit("}", 1)[-1]


def iter_local(doc, name):
    return (e for e in doc.iter() if local(e.tag) == name)


def first_text(doc, name):
    for e in iter_local(doc, name):
        if e.text and e.text.strip():
            return e.text.strip()
    return None


def as_text(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return "" if value is None else str(value)


def as_number(value):
    if isinstance(value, (list, tuple)):
        return sum(as_number(v) for v in value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_date(value):
    """Preservica index dates come as ISO text or epoch milliseconds."""
    v = as_text(value).strip()
    if not v:
        return None
    if re.fullmatch(r"\d{11,14}", v):
        return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
        return dt.datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=dt.timezone.utc) if m else None


def kind_of(format_group):
    fg = (format_group or "").lower()
    for kind, pattern in KINDS:
        if fg and re.search(pattern, fg):
            return kind
    return "Other"


# --------------------------------------------------------------------------- measuring

def walk_asset(p, ref, integrity, month_start):
    """
    Follow one asset to its files: representations -> content objects ->
    generations -> bitstreams. Returns ({bitstream URL: file info}, format group
    of the current Preservation generation).
    """
    reps = p.get_xml(f"/api/entity/information-objects/{ref}/representations", "representations")
    if reps is None:
        return {}, None
    co_reps = defaultdict(set)                 # content object UUID -> representation types
    for rep in iter_local(reps, "Representation"):
        url, rtype = (rep.text or "").strip(), rep.get("type") or "?"
        doc = p.get_xml(url, "representation") if url.startswith("http") else None
        for co in iter_local(doc, "ContentObject") if doc is not None else ():
            m = UUID_RE.search(co.get("ref") or co.text or "")
            if m:
                co_reps[m.group(0)].add(rtype)

    files, preservation_group = {}, None
    for co, rtypes in co_reps.items():
        gens = p.get_xml(f"/api/entity/content-objects/{co}/generations", "generations")
        for g in iter_local(gens, "Generation") if gens is not None else ():
            gurl = (g.text or "").strip()
            gdoc = p.get_xml(gurl, "generation") if gurl.startswith("http") else None
            if gdoc is None:
                continue
            xg = next((e for e in iter_local(gdoc, "Generation") if e.attrib), None)
            attrs = xg.attrib if xg is not None else {}
            active = (attrs.get("active") or g.get("active") or "true") == "true"
            original = (attrs.get("original") or "true") == "true"
            group = first_text(gdoc, "FormatGroup")
            if active and "Preservation" in rtypes and group and not preservation_group:
                preservation_group = group
            for b in iter_local(gdoc, "Bitstream"):
                burl = (b.text or "").strip()
                if not burl.startswith("http"):
                    continue                      # the xip:Bitstream copy holds only the file name
                info = files.setdefault(burl, {"reps": set(), "active": active,
                                               "original": original, "group": group})
                info["reps"] |= rtypes

    for burl, info in files.items():
        bdoc = p.get_xml(burl, "bitstream")
        info["size"], info["sha1"] = 0, None
        if bdoc is not None:
            info["size"] = int(as_number(first_text(bdoc, "FileSize")))
            for fx in iter_local(bdoc, "Fixity"):
                alg = (first_text(fx, "FixityAlgorithmRef") or "").upper().replace("-", "")
                val = first_text(fx, "FixityValue")
                if val and (info["sha1"] is None or alg == "SHA1"):
                    info["sha1"] = f"{alg}:{val.lower()}"
        sdoc = p.get_xml(burl + "/storage-locations", "storage-locations")
        info["adapters"] = sorted({a.get("name") for a in iter_local(sdoc, "Adapter") if a.get("name")}) \
            if sdoc is not None else []
        info["checked"] = info["failed"] = None    # None = not known
        if integrity:
            hdoc = p.get_xml(burl + "/integrity-check-history", "integrity-check-history",
                             params={"start": 0, "max": 10})
            if hdoc is not None:
                checks = []
                for h in iter_local(hdoc, "IntegrityCheckHistory"):
                    d = parse_date(first_text(h, "Date"))
                    if d:
                        checks.append((d, (first_text(h, "Success") or "").lower() == "true"))
                if checks:
                    when, ok = max(checks)
                    info["checked"], info["failed"] = when >= month_start, not ok
                else:
                    info["checked"], info["failed"] = False, False
                    info["never"] = True
    return files, preservation_group


def measure(p, month_start, month_end, cold_pattern, integrity=True, workers=4):
    cold_re = re.compile(cold_pattern, re.I)
    in_month = lambda d: d is not None and month_start <= d < month_end

    log.info("Listing top-level folders")
    collections = p.root_folders()
    col_title = dict(collections)

    log.info("Searching folders")
    folders = list(p.search({"xip.document_type": ["SO"]}, ["xip.created"]))
    folder_created = {f["ref"]: parse_date(f.get("xip.created")) for f in folders}

    log.info("Searching assets")
    asset_fields = ["xip.created", "xip.top_level_so", "xip.security_descriptor",
                    "xip.format_group_r_Preservation", DOWNLOAD_FIELD]
    assets = list(p.search({"xip.document_type": ["IO"]}, asset_fields))
    log.info("%d assets, %d folders, %d collections; measuring files", len(assets), len(folders), len(collections))

    done = [0]

    def one(a):
        files, group = walk_asset(p, a["ref"], integrity, month_start)
        done[0] += 1
        if done[0] % 100 == 0:
            log.info("Measured %d of %d assets", done[0], len(assets))
        return a, files, group

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, assets))

    s = Counter()          # flat figures
    by_location, by_collection, by_tag, by_group = Counter(), Counter(), Counter(), Counter()
    kinds, kinds_added = Counter(), Counter()
    sha_assets = defaultdict(set)
    sha_size = {}
    created_known = 0
    downloads_seen = False

    for a, files, group in results:
        created = parse_date(a.get("xip.created"))
        created_known += created is not None
        added = in_month(created)
        fg = as_text(a.get("xip.format_group_r_Preservation")) or group or ""
        kind = kind_of(fg)
        kinds[kind] += 1
        by_group[fg.lower() or "(none)"] += 1
        tag = as_text(a.get("xip.security_descriptor")) or "(none)"
        by_tag[tag] += 1
        top = as_text(a.get("xip.top_level_so"))
        if a.get(DOWNLOAD_FIELD) not in (None, "", []):
            downloads_seen = True
            s["downloads"] += as_number(a.get(DOWNLOAD_FIELD))
        s["assets"] += 1
        if added:
            s["assets_added"] += 1
            kinds_added[kind] += 1
        for info in files.values():
            size = info["size"]
            s["files"] += 1
            s["content"] += size
            if added:
                s["added_bytes"] += size
            by_collection[col_title.get(top, "(not in a collection)")] += size
            for rep in info["reps"]:
                s["rep:" + rep] += size
            s["active" if info["active"] else "older"] += size
            s["original" if info["original"] else "migrated"] += size
            tiers = set()
            for ad in info["adapters"]:
                tier = "cold" if cold_re.search(ad) else "hot"
                tiers.add(tier)
                by_location[ad] += size
                s[tier] += size
                s["stored"] += size
            s["files_" + ("both" if len(tiers) == 2 else (tiers.pop() + "_only") if tiers else "nowhere")] += 1
            if info["sha1"]:
                sha_assets[info["sha1"]].add(a["ref"])
                sha_size[info["sha1"]] = size
            if info["checked"]:
                s["checked"] += 1
            if info["failed"]:
                s["failed"] += 1
            if info.get("never"):
                s["never_checked"] += 1
    for sha, refs in sha_assets.items():
        if len(refs) > 1:
            s["dup_files"] += len(refs) - 1
            s["dup_bytes"] += sha_size[sha] * (len(refs) - 1)

    if assets and not created_known:
        log.warning("No asset had a readable created date, so 'added this month' figures are left blank.")

    return {
        "collections": collections, "folder_count": len(folders),
        "collections_added": sum(1 for ref, _ in collections if in_month(folder_created.get(ref))),
        "created_known": bool(created_known) or not assets,
        "s": s, "by_location": by_location, "by_collection": by_collection, "by_tag": by_tag,
        "by_group": by_group, "kinds": kinds, "kinds_added": kinds_added,
        "downloads_seen": downloads_seen, "integrity": integrity,
        "cold_re": cold_re,
    }


# --------------------------------------------------------------------------- figures for the sheet

FLOW, SNAPSHOT = "flow", "snapshot"     # how the Totals column treats a row


def tb(n):
    return round(n / TB, 4)


def preservica_rows(m, previous_downloads):
    """[(category, metric, value, kind)] for the Preservica tab. value None = leave blank."""
    s, added_ok = m["s"], m["created_known"]
    a = lambda v: v if added_ok else None
    rows = [
        ("Content", "#Collections", len(m["collections"]), SNAPSHOT),
        ("Content", "#CollectionsAdded", a(m["collections_added"]), FLOW),
        ("Content", "#Folders", m["folder_count"], SNAPSHOT),
        ("Content", "#Assets", s["assets"], SNAPSHOT),
        ("Content", "#AssetsAdded", a(s["assets_added"]), FLOW),
        ("Content", "#Files", s["files"], SNAPSHOT),
    ]
    for kind, _ in KINDS + [("Other", None)]:
        rows.append(("AssetsByKind", f"#{kind}", m["kinds"][kind], SNAPSHOT))
    for kind, _ in KINDS + [("Other", None)]:
        rows.append(("AssetsAddedByKind", f"#{kind}Added", a(m["kinds_added"][kind]), FLOW))
    rows += [
        ("Storage", "#TBStored", tb(s["stored"]), SNAPSHOT),
        ("Storage", "#TBHot", tb(s["hot"]), SNAPSHOT),
        ("Storage", "#TBCold", tb(s["cold"]), SNAPSHOT),
        ("Storage", "#TBContent", tb(s["content"]), SNAPSHOT),
        ("Storage", "#TBAdded", tb(s["added_bytes"]) if added_ok else None, FLOW),
        ("Storage", "#TBPreservation", tb(s["rep:Preservation"]), SNAPSHOT),
        ("Storage", "#TBAccess", tb(s["rep:Access"]), SNAPSHOT),
        ("Storage", "#TBCurrentGenerations", tb(s["active"]), SNAPSHOT),
        ("Storage", "#TBOlderGenerations", tb(s["older"]), SNAPSHOT),
        ("Storage", "#TBOriginalFiles", tb(s["original"]), SNAPSHOT),
        ("Storage", "#TBMigratedFiles", tb(s["migrated"]), SNAPSHOT),
        ("Storage", "#TBDuplicates", tb(s["dup_bytes"]), SNAPSHOT),
        ("Storage", "#DuplicateFiles", s["dup_files"], SNAPSHOT),
        ("Tiers", "#FilesHotOnly", s["files_hot_only"], SNAPSHOT),
        ("Tiers", "#FilesColdOnly", s["files_cold_only"], SNAPSHOT),
        ("Tiers", "#FilesHotAndCold", s["files_both"], SNAPSHOT),
        ("Tiers", "#FilesNoStorageLocation", s["files_nowhere"], SNAPSHOT),
    ]
    for loc in sorted(m["by_location"]):
        tier = "Cold" if m["cold_re"].search(loc) else "Hot"
        rows.append(("StorageLocations", f"#TB {loc} ({tier})", tb(m["by_location"][loc]), SNAPSHOT))
    for col in sorted(m["by_collection"]):
        rows.append(("TBByCollection", f"#TB {col}", tb(m["by_collection"][col]), SNAPSHOT))
    for tag in sorted(m["by_tag"]):
        rows.append(("SecurityTags", f"#Assets {tag}", m["by_tag"][tag], SNAPSHOT))
    for g in sorted(m["by_group"]):
        rows.append(("FormatGroups", f"#Assets {g} ({kind_of(g if g != '(none)' else '')})",
                     m["by_group"][g], SNAPSHOT))
    if m["integrity"]:
        rows += [
            ("Integrity", "#FilesCheckedThisMonth", s["checked"], FLOW),
            ("Integrity", "#FilesFailedLastCheck", s["failed"], SNAPSHOT),
            ("Integrity", "#FilesNeverChecked", s["never_checked"], SNAPSHOT),
        ]
    if m["downloads_seen"]:
        total = int(s["downloads"])
        month = total - previous_downloads if previous_downloads is not None else None
        rows += [("Access", "#DownloadsAllTime", total, SNAPSHOT),
                 ("Access", "#Downloads", month if month is None or month >= 0 else None, FLOW)]
    return rows


def dcds_rows(prows, mode):
    get = {(c, k): v for c, k, v, _ in prows}
    added = mode == "added"
    pick = lambda c, k_added, c_total, k_total: get.get((c, k_added)) if added else get.get((c_total, k_total))
    return [
        ("Preservica", "#Collections", pick("Content", "#CollectionsAdded", "Content", "#Collections")),
        ("Preservica", "#Assets", pick("Content", "#AssetsAdded", "Content", "#Assets")),
        ("Preservica", "#Images", pick("AssetsAddedByKind", "#ImagesAdded", "AssetsByKind", "#Images")),
        ("Preservica", "#SoundRecordings", pick("AssetsAddedByKind", "#SoundRecordingsAdded",
                                                "AssetsByKind", "#SoundRecordings")),
        ("Preservica", "#MovingImages", pick("AssetsAddedByKind", "#MovingImagesAdded",
                                             "AssetsByKind", "#MovingImages")),
        ("Preservica", "#Text", pick("AssetsAddedByKind", "#TextAdded", "AssetsByKind", "#Text")),
        ("Preservica", "#Downloads", get.get(("Access", "#Downloads"))),
    ]


def storage_rows(prows):
    get = {(c, k): v for c, k, v, _ in prows}
    return [("Tier 1", "#TBPreservicaHot", get[("Storage", "#TBHot")]),
            ("Tier 1", "#TBPreservicaCold", get[("Storage", "#TBCold")])]


# --------------------------------------------------------------------------- Google Sheets

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def quote_tab(tab):
    return "'" + tab.replace("'", "''") + "'"


class Sheet:
    def __init__(self, sheet_id, key_info):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(
            key_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.email = key_info.get("client_email", "the service account")
        self.http = AuthorizedSession(creds)
        self.id = sheet_id
        meta = self._call("GET", f"{SHEETS}/{sheet_id}",
                          params={"fields": "properties.title,sheets.properties(sheetId,title)"})
        self.title = meta["properties"]["title"]
        self.tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    def _call(self, method, url, **kw):
        r = self.http.request(method, url, timeout=60, **kw)
        if r.status_code in (403, 404):
            sys.exit(f"Google Sheets refused access (HTTP {r.status_code}). Share the spreadsheet with "
                     f"{self.email if hasattr(self, 'email') else 'the service account'} as an Editor, "
                     "and check that the Google Sheets API is enabled for its project.")
        if r.status_code >= 400:
            sys.exit(f"Google Sheets error (HTTP {r.status_code}): {r.text[:300]}")
        return r.json() if r.content else {}

    def tab(self, name):
        real = next((t for t in self.tabs if t.strip().lower() == name.lower()), None)
        if real is None:
            sys.exit(f"The spreadsheet has no '{name}' tab (it has: {', '.join(self.tabs)}).")
        return Tab(self, real)


class Tab:
    """
    One tab, read once. Cells are changed in memory and sent in one request by
    save(), which keeps well inside Google's limit of 60 writes a minute.
    """

    WIDTH = 15          # A..O: Category, Metric, January..December, Totals

    def __init__(self, sheet, name):
        self.sheet, self.name = sheet, name
        data = sheet._call("GET", f"{SHEETS}/{sheet.id}/values/{quote_tab(name)}!A1:Z2000",
                           params={"valueRenderOption": "FORMULA"})
        self.grid = [list(r) for r in data.get("values", [])]
        self.changed = set()          # (row, col) to send
        self.rebuilt = False          # True when rows were added: the whole tab is sent

    def cell(self, r, c):
        return self.grid[r][c] if r < len(self.grid) and c < len(self.grid[r]) else ""

    def set(self, r, c, value):
        while len(self.grid) <= r:
            self.grid.append([])
        row = self.grid[r]
        while len(row) <= c:
            row.append("")
        row[c] = value
        self.changed.add((r, c))

    def ensure_header(self):
        if not any(str(self.cell(0, c)).strip() for c in range(self.WIDTH)):
            for c, v in enumerate(["", ""] + MONTHS + ["Totals"]):
                self.set(0, c, v)
            self.set(1, 0, "Category")
            self.set(1, 1, "Metric")

    def month_col(self, month_name):
        for c in range(len(self.grid[0]) if self.grid else 0):
            if str(self.cell(0, c)).strip().lower() == month_name.lower():
                return c
        sys.exit(f"Row 1 of the {self.name} tab has no '{month_name}' column.")

    def totals_col(self):
        for c in range(len(self.grid[0]) if self.grid else 0):
            if str(self.cell(0, c)).strip().lower() in ("totals", "total"):
                return c
        return None

    def find(self, category, metric):
        """(row of the metric or None, last row of the category's block or None)."""
        cat_row = next((r for r in range(len(self.grid))
                        if str(self.cell(r, 0)).strip().lower() == category.lower()), None)
        if cat_row is None:
            return None, None
        last = cat_row
        for r in range(cat_row, len(self.grid)):
            a, b = str(self.cell(r, 0)).strip(), str(self.cell(r, 1)).strip()
            if r > cat_row and (a or not b):
                break
            if b.lower() == metric.lower():
                return r, r
            last = r
        return None, last

    def add_row(self, category, metric):
        """Add a metric row inside its category, or a new category at the end."""
        _, last = self.find(category, metric)
        self.rebuilt = True
        if last is None:
            used = len(self.grid)
            while used > 0 and not any(str(x).strip() for x in self.grid[used - 1]):
                used -= 1
            row = used + (1 if used > 2 else 0)        # a blank row between categories
            self.set(row, 0, category)
        else:
            row = last + 1
            self.grid.insert(row, [])
        self.set(row, 1, metric)
        return row

    def write(self, rows, month_name, create=False, overwrite=True):
        """rows: [(category, metric, value)]. Returns (cells filled, labels not found)."""
        if create:
            self.ensure_header()
        col = self.month_col(month_name)
        filled, missing = 0, []
        for category, metric, value in rows:
            if value is None and not create:
                continue
            r, _ = self.find(category, metric)
            if r is None:
                if not create:
                    missing.append(f"{category} / {metric}")
                    continue
                r = self.add_row(category, metric)     # rows are kept even while blank
            if value is None:
                continue
            if not overwrite and str(self.cell(r, col)).strip():
                continue
            self.set(r, col, value)
            filled += 1
        return filled, missing

    def set_totals(self, kinds):
        """Totals formulas for every metric row: a sum for monthly rows, the latest value for snapshots."""
        tcol = self.totals_col()
        if tcol is None:
            return
        for r in range(2, len(self.grid)):
            metric = str(self.cell(r, 1)).strip()
            if not metric:
                continue
            rng = f"C{r + 1}:N{r + 1}"
            formula = (f"=SUM({rng})" if kinds.get(metric) == FLOW
                       else f'=IFERROR(LOOKUP(2,1/({rng}<>""),{rng}),"")')
            if self.cell(r, tcol) != formula:
                self.set(r, tcol, formula)

    def save(self):
        if not self.changed:
            return
        if self.rebuilt:
            width = max(self.WIDTH, max((len(r) for r in self.grid), default=0))
            values = [list(r) + [""] * (width - len(r)) for r in self.grid]
            data = [{"range": f"{quote_tab(self.name)}!A1:{col_letter(width - 1)}{len(values)}", "values": values}]
        else:
            data = [{"range": f"{quote_tab(self.name)}!{col_letter(c)}{r + 1}", "values": [[self.grid[r][c]]]}
                    for r, c in sorted(self.changed)]
        self.sheet._call("POST", f"{SHEETS}/{self.sheet.id}/values:batchUpdate",
                         json={"valueInputOption": "USER_ENTERED", "data": data})
        self.changed.clear()

    def value(self, category, metric, col):
        r, _ = self.find(category, metric)
        v = self.cell(r, col) if r is not None else ""
        try:
            return int(float(str(v).replace(",", ""))) if str(v).strip() else None
        except ValueError:
            return None


def google_key(path):
    text = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY") or "").strip()
    if not text and path:
        if not os.path.isfile(path):
            sys.exit(f"Google key file {path} not found.")
        text = open(path, encoding="utf-8-sig").read()
    if not text:
        sys.exit("No Google service account key: set GOOGLE_SERVICE_ACCOUNT_KEY or use --google-key key.json.")
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        sys.exit("The Google service account key isn't valid JSON. Paste the whole downloaded .json file.")
    if info.get("type") != "service_account":
        sys.exit("The Google key isn't a service account key (its \"type\" should be \"service_account\").")
    return info


# --------------------------------------------------------------------------- main

def report_month(arg):
    """The month being reported, as (year, month). Late-night runs on the 1st count as last month."""
    if arg:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", arg.strip())
        if not m or not 1 <= int(m[2]) <= 12:
            sys.exit("--month should look like 2026-10.")
        return int(m[1]), int(m[2])
    now = dt.datetime.now(ZoneInfo("America/Chicago")) if ZoneInfo else dt.datetime.now()
    if now.day == 1 and now.hour < 6:
        now -= dt.timedelta(hours=7)
    return now.year, now.month


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet-id", default=os.environ.get("GOOGLE_SHEET_ID"),
                    help="Spreadsheet ID (the long code in its URL). Default: GOOGLE_SHEET_ID.")
    ap.add_argument("--google-key", help="Service account key file (default: GOOGLE_SERVICE_ACCOUNT_KEY).")
    ap.add_argument("--month", help="Month to fill, e.g. 2026-10. Default: this month (Central time).")
    ap.add_argument("--preservica-tab", default="Preservica")
    ap.add_argument("--storage-tab", default="Storage")
    ap.add_argument("--dcds-tab", default="DCDS")
    ap.add_argument("--dcds-counts", choices=["added", "total"],
                    default=os.environ.get("DCDS_COUNTS") or "added",
                    help="DCDS Preservica rows: what was added that month (default) or all-time totals.")
    ap.add_argument("--cold-pattern", default=os.environ.get("COLD_PATTERN") or DEFAULT_COLD,
                    help=f"Storage locations whose names match count as Cold (default: {DEFAULT_COLD}).")
    ap.add_argument("--no-integrity", action="store_true", help="Skip reading integrity check history.")
    ap.add_argument("--workers", type=int, default=4, help="Assets measured at once (default 4).")
    ap.add_argument("--keep-existing", action="store_true", help="Don't overwrite cells that already have a value.")
    ap.add_argument("--dry-run", action="store_true", help="Measure and print the figures; write nothing.")
    ap.add_argument("--show", action="store_true", help="Print the figures (off by default: logs may be public).")
    ap.add_argument("--stats-json", help="Also save the figures to this JSON file.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    year, month = report_month(args.month)
    month_name = MONTHS[month - 1]
    month_start = dt.datetime(year, month, 1, tzinfo=ZoneInfo("America/Chicago") if ZoneInfo else dt.timezone.utc)
    month_end = (month_start + dt.timedelta(days=32)).replace(day=1)
    log.info("Reporting %s %d", month_name, year)

    sheet = ptab = None
    if not args.dry_run:
        if not args.sheet_id:
            sys.exit("No spreadsheet: set GOOGLE_SHEET_ID or use --sheet-id (or use --dry-run).")
        sheet = Sheet(args.sheet_id, google_key(args.google_key))
        years = re.findall(r"(?<!\d)(20\d\d)(?!\d)", sheet.title)
        if years and str(year) not in years:
            sys.exit(f"The spreadsheet is for {', '.join(years)} but the month is {month_name} {year}. "
                     "Point GOOGLE_SHEET_ID at that year's spreadsheet.")
        ptab = sheet.tab(args.preservica_tab)     # checks the tabs exist before the long measuring
        stab, dtab = sheet.tab(args.storage_tab), sheet.tab(args.dcds_tab)

    from preservica_search_export import load_credentials
    p = Preservica(load_credentials())
    p.login()
    m = measure(p, month_start, month_end, args.cold_pattern, integrity=not args.no_integrity,
                workers=args.workers)

    refused = {k: n for k, n in p.status.items() if k[1] != 200}
    for (kind, code), n in sorted(refused.items(), key=str):
        log.warning("%d %s request(s) got %s from Preservica", n, kind, f"HTTP {code}" if code else "no answer")

    previous_downloads = None
    if ptab is not None and month > 1:
        ptab.ensure_header()
        previous_downloads = ptab.value("Access", "#DownloadsAllTime", ptab.month_col(MONTHS[month - 2]))
    prows = preservica_rows(m, previous_downloads)
    drows = dcds_rows(prows, args.dcds_counts)
    srows = storage_rows(prows)

    if args.stats_json:
        with open(args.stats_json, "w", encoding="utf-8") as fh:
            json.dump({"month": f"{year}-{month:02d}",
                       "preservica": [list(r) for r in prows], "dcds": [list(r) for r in drows],
                       "storage": [list(r) for r in srows], "requests": {f"{k} {c}": n for (k, c), n in p.status.items()}},
                      fh, indent=1)
    if args.show or args.dry_run:
        for title, rows in (("Preservica tab", prows), ("Storage tab", srows), (f"DCDS tab ({args.dcds_counts})", drows)):
            print(f"\n== {title}, {month_name} {year}")
            for r in rows:
                print(f"  {r[0]:<20} {r[1]:<45} {'' if r[2] is None else r[2]}")
    if args.dry_run:
        return

    overwrite = not args.keep_existing
    n1, _ = ptab.write([r[:3] for r in prows], month_name, create=True, overwrite=overwrite)
    ptab.set_totals({r[1]: r[3] for r in prows})
    n2, miss2 = stab.write(srows, month_name, overwrite=overwrite)
    n3, miss3 = dtab.write(drows, month_name, overwrite=overwrite)
    for tab in (ptab, stab, dtab):
        tab.save()
    for tab, miss in ((stab.name, miss2), (dtab.name, miss3)):
        for label in miss:
            log.warning("The %s tab has no row for %s, so it was skipped.", tab, label)
    blanks = [r[1] for r in drows if r[2] is None]
    if blanks:
        log.info("Left blank on the DCDS tab: %s", ", ".join(blanks))
    log.info("Wrote %s %d: %d cells on %s, %d on %s, %d on %s.", month_name, year,
             n1, ptab.name, n2, stab.name, n3, dtab.name)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"Filled **{month_name} {year}** in the stats sheet: {n1} cells on {ptab.name}, "
                     f"{n2} on {stab.name}, {n3} on {dtab.name}.\n")
            if refused:
                fh.write("\nSome Preservica requests were refused; see the log.\n")
    if any(code in (401, 403) for _, code in refused):
        sys.exit("Some Preservica requests were refused, so the figures may be too low (see above).")


if __name__ == "__main__":
    main()
