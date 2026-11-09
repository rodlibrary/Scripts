#!/usr/bin/env python3
"""
Measure Preservica for the month and write the figures for the DCDS stats sheet
as a CSV and ready-to-paste blocks. The same figures as the "Preservica -
monthly stats" Postman collection, but much faster: it measures several assets
at once from your own computer (about a minute or two for a few hundred
assets), with no Postman Cloud Agent in between.

What it measures (everything the account can see):
  * counts: collections (top-level folders), folders, assets, files;
  * assets by kind: images, text, sound recordings, moving images, other,
    from the file extension of each asset's current Preservation file
    (.jpg/.tif images, .pdf/.doc text, .mp3/.wav sound, .mp4/.mov moving
    images); Preservica's format group when a file has no extension;
  * what was added this month (by each record's created date);
  * storage: total, Hot and Cold tiers, each storage location, Preservation
    and Access representations, current and older generations, original and
    migrated files, files kept in only one tier, duplicate files (same SHA1
    in more than one asset), and terabytes per collection and file type;
  * assets per security tag; integrity checks (checked this month, failed,
    never checked).
Downloads aren't recorded in Preservica: they come from the public portal's
Google Analytics (see preservica/postman/README.md, "Monthly downloads").

It lines the figures up with the rows of the sheet's Preservica tab, so each
month is two pastes: one block into the Preservica tab and one into the Storage
tab, both at the month's cell in row 3. When run in a terminal it puts each
block on the clipboard in turn and says where to paste it.

It also writes, in the folder you run it from:
  * preservica_stats_<YYYY-MM>.csv: every figure (Tab, Category, Metric, value);
  * preservica_stats_<YYYY-MM>_paste.txt: the two blocks, plus any figures with
    no row in the sheet (a new file type or collection) and any rows left blank.

The Preservica tab's rows are built in (PRESERVICA_TAB below). If you change
them, download the tab as a CSV and pass it with --layout.

Usage
    python preservica_monthly_stats.py                  # this month (the month just ended, in the first 5 days)
    python preservica_monthly_stats.py --month 2026-10
    python preservica_monthly_stats.py --layout "DCDS_stats_2026 - Preservica.csv"

Needs:  pip install pyPreservica requests
Preservica login: credentials.properties (or PRESERVICA_* environment
variables), exactly as for preservica_search_export.py.
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


# File extensions sorted into the DCDS kinds (the extension of the asset's
# current Preservation file decides).
EXT_KINDS = {
    "Images": "jpg jpeg jpe tif tiff png gif bmp jp2 j2k jpx dng cr2 nef arw raw heic webp psd svg",
    "Text": "pdf doc docx txt rtf odt xml html htm epub xls xlsx csv ppt pptx odp ods md",
    "SoundRecordings": "mp3 wav aif aiff flac m4a aac ogg oga wma bwf",
    "MovingImages": "mp4 mov avi mkv mxf mpg mpeg m4v wmv webm dv flv 3gp",
}


def ext_of(name):
    m = re.search(r"\.([A-Za-z0-9]{1,6})$", name or "")
    return m.group(1).lower() if m else ""


def ext_kind(ext):
    for kind, exts in EXT_KINDS.items():
        if ext and ext in exts.split():
            return kind
    return ""


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
    generations -> bitstreams.
    Returns ({bitstream URL: file info}, format group and file extension
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

    files, preservation_group, preservation_ext = {}, None, ""
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
                info = files.setdefault(burl, {"reps": set(), "active": active, "original": original,
                                               "group": group, "ext": ext_of(b.get("filename"))})
                info["reps"] |= rtypes
                if active and "Preservation" in rtypes and info["ext"] and not preservation_ext:
                    preservation_ext = info["ext"]

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
    return files, preservation_group, preservation_ext


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
                    "xip.format_group_r_Preservation"]
    assets = list(p.search({"xip.document_type": ["IO"]}, asset_fields))
    log.info("%d assets, %d folders, %d collections; measuring files", len(assets), len(folders), len(collections))

    done = [0]

    lock = threading.Lock()

    def one(a):
        try:
            files, group, ext = walk_asset(p, a["ref"], integrity, month_start)
            problem = None
        except Exception as exc:          # one bad asset must not stop the run
            files, group, ext, problem = {}, None, "", str(exc)
        with lock:
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(assets):
                log.info("Measured %d of %d assets", done[0], len(assets))
        return a, files, group, ext, problem

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, assets))

    s = Counter()          # flat figures
    by_location, by_collection, by_tag, by_group = Counter(), Counter(), Counter(), Counter()
    by_ext, ext_files, ext_bytes = Counter(), Counter(), Counter()
    failed_assets = []
    kinds, kinds_added = Counter(), Counter()
    sha_assets = defaultdict(set)
    sha_size = {}
    created_known = 0
    for a, files, group, ext, problem in results:
        if problem:
            failed_assets.append((a["ref"], problem))
        created = parse_date(a.get("xip.created"))
        created_known += created is not None
        added = in_month(created)
        fg = as_text(a.get("xip.format_group_r_Preservation")) or group or ""
        kind = ext_kind(ext) or kind_of(fg)
        kinds[kind] += 1
        by_group[fg.lower() or "(none)"] += 1
        by_ext[ext or "(no extension)"] += 1
        tag = as_text(a.get("xip.security_descriptor")) or "(none)"
        by_tag[tag] += 1
        top = as_text(a.get("xip.top_level_so"))
        s["assets"] += 1
        if added:
            s["assets_added"] += 1
            kinds_added[kind] += 1
        for info in files.values():
            size = info["size"]
            s["files"] += 1
            s["content"] += size
            ext_files[info["ext"] or "(no extension)"] += 1
            ext_bytes[info["ext"] or "(no extension)"] += size
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
        "by_ext": by_ext, "ext_files": ext_files, "ext_bytes": ext_bytes, "failed": failed_assets,
        "integrity": integrity,
        "cold_re": cold_re,
    }


# --------------------------------------------------------------------------- figures for the sheet

def tb(n):
    return round(n / TB, 4)


# The sheet's names for the top-level folders (TBByCollection rows such as #TBIR).
# Any other folder gets its initials.
COLLECTION_CODES = {
    "Institutional Repository": "IR",
    "Special Collections & University Archives": "SCUA",
    "Museum": "UNIM",
}
# The sheet's FormatGroups names for Preservica's format groups.
FORMAT_GROUP_NAMES = {"Images": "StillImages", "Text": "Text", "SoundRecordings": "Audio",
                      "MovingImages": "MovingImages", "Other": "Other"}


def collection_code(title):
    if title in COLLECTION_CODES:
        return COLLECTION_CODES[title]
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title or "") if w.lower() not in ("and", "of", "the")]
    return "".join(w[0].upper() for w in words) or "Other"


def figures(m):
    """
    [(category, metric, value)] for the Preservica tab, named as in the sheet.
    value None = not known (left blank).
    """
    s, added_ok = m["s"], m["created_known"]
    a = lambda v: v if added_ok else None
    kinds = [k for k, _ in KINDS] + ["Other"]
    rows = [("Content", "#Collections", len(m["collections"])),
            ("Content", "#Folders", m["folder_count"]),
            ("Content", "#Assets", s["assets"]),
            ("Content", "#Files", s["files"])]
    rows += [("AssetsByKind", f"#{k}", m["kinds"][k]) for k in kinds]
    rows += [("Additions", "#CollectionsAdded", a(m["collections_added"])),
             ("Additions", "#AssetsAdded", a(s["assets_added"]))]
    rows += [("AssetsAddedByKind", f"#{k}Added", a(m["kinds_added"][k])) for k in kinds]
    rows += [("Storage", label, value) for label, value in (
        ("#TBStored", tb(s["stored"])), ("#TBHot", tb(s["hot"])), ("#TBCold", tb(s["cold"])),
        ("#TBContent", tb(s["content"])), ("#TBAdded", tb(s["added_bytes"]) if added_ok else None),
        ("#TBPreservation", tb(s["rep:Preservation"])), ("#TBAccess", tb(s["rep:Access"])),
        ("#TBCurrentGenerations", tb(s["active"])), ("#TBOlderGenerations", tb(s["older"])),
        ("#TBOriginalFiles", tb(s["original"])), ("#TBMigratedFiles", tb(s["migrated"])),
        ("#TBDuplicates", tb(s["dup_bytes"])), ("#DuplicateFiles", s["dup_files"]))]
    rows += [("Tiers", "#FilesHotOnly", s["files_hot_only"]), ("Tiers", "#FilesColdOnly", s["files_cold_only"]),
             ("Tiers", "#FilesHotAndCold", s["files_both"]), ("Tiers", "#FilesNoStorageLocation", s["files_nowhere"])]
    for loc in sorted(m["by_location"]):
        tier = "Cold" if m["cold_re"].search(loc) else "Hot"
        rows.append(("StorageLocations", f"#TB {loc} ({tier})", tb(m["by_location"][loc])))
    by_code = Counter()
    for title, size in m["by_collection"].items():
        by_code[collection_code(title)] += size
    rows += [("TBByCollection", f"#TB{code}", tb(by_code[code])) for code in sorted(by_code)]
    rows += [("SecurityTags", f"#Assets {tag}", m["by_tag"][tag]) for tag in sorted(m["by_tag"])]
    label = lambda prefix, e: f"{prefix} .{e}" if not e.startswith("(") else f"{prefix} {e}"
    rows += [("AssetsByFileType", label("#Assets", e), m["by_ext"][e]) for e in sorted(m["by_ext"])]
    rows += [("FilesByFileType", label("#Files", e), m["ext_files"][e]) for e in sorted(m["ext_files"])]
    rows += [("TBByFileType", label("#TB", e), tb(m["ext_bytes"][e])) for e in sorted(m["ext_bytes"])]
    groups = Counter()
    for g, n in m["by_group"].items():
        groups[FORMAT_GROUP_NAMES[kind_of(g if g != "(none)" else "")]] += n
    rows += [("FormatGroups", f"#{name}", groups[name]) for name in ("StillImages", "Text", "Audio", "MovingImages")]
    if groups["Other"]:
        rows.append(("FormatGroups", "#Other", groups["Other"]))
    if m["integrity"]:
        rows += [("Integrity", "#FilesCheckedThisMonth", s["checked"]),
                 ("Integrity", "#FilesFailedLastCheck", s["failed"]),
                 ("Integrity", "#FilesNeverChecked", s["never_checked"])]
    return rows


def storage_figures(m):
    """The Storage tab's Tier 1 rows (TB, 4 decimals as the tab shows them)."""
    return [("Tier 1", "#TBPreservicaHot", tb(m["s"]["hot"])),
            ("Tier 1", "#TBPreservicaCold", tb(m["s"]["cold"]))]


# --------------------------------------------------------------------------- the sheet's layout

# The Preservica tab's rows from row 3 down (Category, Metric; a blank line = a blank row).
# If you add, remove or rename rows in the sheet, download the tab as a CSV
# (File > Download > Comma-separated values, with the Preservica tab open) and
# run the script with --layout <that file>, or update this list.
PRESERVICA_TAB = """
Content,#Collections
,#Folders
,#Assets
,#Files

AssetsByKind,#Images
,#SoundRecordings
,#MovingImages
,#Text
,#Other

Additions,#CollectionsAdded
,#AssetsAdded

AssetsAddedByKind,#ImagesAdded
,#SoundRecordingsAdded
,#MovingImagesAdded
,#TextAdded
,#OtherAdded

Storage,#TBStored
,#TBHot
,#TBCold
,#TBContent
,#TBAdded
,#TBPreservation
,#TBAccess
,#TBCurrentGenerations
,#TBOlderGenerations
,#TBOriginalFiles
,#TBMigratedFiles
,#TBDuplicates
,#DuplicateFiles

Tiers,#FilesHotOnly
,#FilesColdOnly
,#FilesHotAndCold
,#FilesNoStorageLocation

StorageLocations,#TB Primary Adapter (Hot)
,#TB S3 Deep Glacier Adapter (Cold)

TBByCollection,#TBIR
,#TBSCUA

SecurityTags,#Assets open

AssetsByFileType,#Assets .jpg
,#Assets .pdf
,#Assets .png

FilesByFileType,#Files .jpg
,#Files .pdf
,#Files .png
,#Files .tiff

TBByFileType,#TB .jpg
,#TB .pdf
,#TB .png
,#TB .tiff

FormatGroups,#StillImages
,#Text
,#Audio
,#MovingImages

Integrity,#FilesCheckedThisMonth
,#FilesFailedLastCheck
,#FilesNeverChecked

Use,#Downloads
"""
FIRST_ROW = 3          # the tabs' first metric row (row 1 = months, row 2 = Category / Metric)


def parse_layout(text, from_sheet_csv=False):
    """
    [(category, metric)] per sheet row from FIRST_ROW down; ("", "") = a blank row.
    A blank category carries the one above it down, as in the sheet.
    """
    import csv
    import io
    rows = list(csv.reader(io.StringIO(text.strip("\n") if not from_sheet_csv else text)))
    if from_sheet_csv:
        # A CSV downloaded from the tab: row 1 = months, row 2 = Category, Metric.
        start = next((i + 1 for i, r in enumerate(rows[:5]) if [c.strip().lower() for c in r[:2]] == ["category", "metric"]),
                     None)
        if start is None:
            sys.exit("The layout CSV should be the Preservica tab downloaded as CSV (row 2 = Category, Metric).")
        rows = rows[start:]
    layout, category = [], ""
    for r in rows:
        c, metric = ((r + ["", ""])[:2])
        c, metric = c.strip(), metric.strip()
        if not c and not metric:
            layout.append(("", ""))
            continue
        category = c or category
        layout.append((category, metric))
    while layout and layout[-1] == ("", ""):
        layout.pop()
    return layout


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def align(rows, layout):
    """
    Line the figures up with the sheet's rows.
    Returns (values per layout row, figures with no row in the sheet, sheet rows with no figure).
    """
    by_key = {(norm(c), norm(m)): v for c, m, v in rows}
    values, used, unfilled = [], set(), []
    for c, metric in layout:
        key = (norm(c), norm(metric))
        if not metric:
            values.append(None)
        elif key in by_key:
            values.append(by_key[key])
            used.add(key)
        else:
            values.append(None)
            unfilled.append((c, metric))
    extra = [(c, m, v) for c, m, v in rows if (norm(c), norm(m)) not in used]
    return values, extra, unfilled


def column_block(values):
    """The values as lines for one paste, from the first row to the last row with a value.
    Stopping there keeps a paste from blanking rows filled in by hand below it (#Downloads)."""
    last = max((i for i, v in enumerate(values) if v is not None), default=-1)
    return "\n".join(value_text(v) for v in values[:last + 1])


# --------------------------------------------------------------------------- output

def value_text(v):
    return "" if v is None else str(v)


def col_letter(month):
    return chr(ord("B") + month)            # January = C, October = L


def write_outputs(base, month_name, year, month, rows, srows, pblock, sblock, extra, unfilled):
    """The CSV of every figure and the paste file. Returns their names."""
    import csv
    csv_path, paste_path = f"{base}.csv", f"{base}_paste.txt"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Tab", "Category", "Metric", f"{month_name} {year}"])
        last = None
        for tab, tab_rows in (("Storage", srows), ("Preservica", rows)):
            for cat, metric, v in tab_rows:
                w.writerow([tab, cat if (tab, cat) != last else "", metric, value_text(v)])
                last = (tab, cat)
    cell = f"{col_letter(month)}{FIRST_ROW}"
    with open(paste_path, "w", encoding="utf-8") as f:
        f.write(f"Preservica figures for {month_name} {year}: two pastes.\n\n")
        f.write(f"== 1. Preservica tab: click cell {cell} ({month_name}, Content #Collections) and paste these lines\n")
        f.write(pblock + "\n\n")
        f.write(f"== 2. Storage tab: click cell {cell} ({month_name}, Tier 1 #TBPreservicaHot) and paste these lines\n")
        f.write(sblock + "\n")
        if extra:
            f.write("\n== Figures with no row in the Preservica tab (add a row for any you want to keep)\n")
            f.write("\n".join(f"{c}\t{m}\t{value_text(v)}" for c, m, v in extra) + "\n")
        if unfilled:
            f.write("\n== Rows in the Preservica tab with no figure this month (left blank)\n")
            f.write("\n".join(f"{c}\t{m}" for c, m in unfilled) + "\n")
    return csv_path, paste_path


def copy_to_clipboard(text):
    """Put text on the clipboard. Returns True if it worked."""
    import shutil
    import subprocess
    commands = []
    if sys.platform.startswith("win"):
        commands.append(["clip"])
    elif sys.platform == "darwin":
        commands.append(["pbcopy"])
    else:
        commands += [c for c in (["wl-copy"], ["xclip", "-selection", "clipboard"]) if shutil.which(c[0])]
    for cmd in commands:
        try:
            # The blocks hold only numbers, so plain text is safe; Windows wants CRLF line ends.
            data = (text.replace("\n", "\r\n") if cmd == ["clip"] else text).encode("utf-8")
            subprocess.run(cmd, input=data, check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


def report_month(arg):
    """The month being reported, as (year, month). In the first 5 days of a month: the month just ended."""
    if arg:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", arg.strip())
        if not m or not 1 <= int(m[2]) <= 12:
            sys.exit("--month should look like 2026-10.")
        return int(m[1]), int(m[2])
    now = dt.datetime.now(ZoneInfo("America/Chicago")) if ZoneInfo else dt.datetime.now()
    if now.day <= 5:
        now = now.replace(day=1) - dt.timedelta(days=1)
    return now.year, now.month


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", help="Month to report, e.g. 2026-10. Default: this month (the month just "
                                    "ended, in the first 5 days of a month).")
    ap.add_argument("--layout", help="The Preservica tab downloaded as CSV, if its rows differ from the "
                                     "built-in layout.")
    ap.add_argument("--cold-pattern", default=DEFAULT_COLD,
                    help=f"Storage locations whose names match count as Cold (default: {DEFAULT_COLD}).")
    ap.add_argument("--no-integrity", action="store_true",
                    help="Skip the integrity-check history (one request fewer per file; a little faster).")
    ap.add_argument("--workers", type=int, default=8, help="Assets measured at once (default 8).")
    ap.add_argument("--output", help="Base name for the output files (default: preservica_stats_<YYYY-MM>).")
    ap.add_argument("--no-clipboard", action="store_true", help="Don't offer to copy the blocks to the clipboard.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.layout:
        with open(args.layout, encoding="utf-8-sig") as f:
            layout = parse_layout(f.read(), from_sheet_csv=True)
    else:
        layout = parse_layout(PRESERVICA_TAB)

    year, month = report_month(args.month)
    month_name = MONTHS[month - 1]
    tz = ZoneInfo("America/Chicago") if ZoneInfo else dt.timezone.utc
    month_start = dt.datetime(year, month, 1, tzinfo=tz)
    month_end = (month_start + dt.timedelta(days=32)).replace(day=1)
    log.info("Reporting %s %d", month_name, year)

    from preservica_search_export import load_credentials
    p = Preservica(load_credentials())
    p.login()
    started = time.time()
    m = measure(p, month_start, month_end, args.cold_pattern, integrity=not args.no_integrity,
                workers=args.workers)

    rows, srows = figures(m), storage_figures(m)
    values, extra, unfilled = align(rows, layout)
    pblock = column_block(values)
    sblock = "\n".join(value_text(v) for _, _, v in srows)
    base = args.output or f"preservica_stats_{year}-{month:02d}"
    csv_path, paste_path = write_outputs(base, month_name, year, month, rows, srows, pblock, sblock, extra, unfilled)

    refused = {k: n for k, n in p.status.items() if k[1] != 200}
    for (kind, code), n in sorted(refused.items(), key=str):
        log.warning("%d %s request(s) got %s from Preservica", n, kind, f"HTTP {code}" if code else "no answer")
    for ref, problem in m["failed"][:10]:
        log.warning("Asset %s couldn't be measured: %s", ref, problem)
    if any(code in (401, 403) for _, code in refused):
        log.warning("HTTP 401/403: the account needs the Access functional role with Read Metadata and Read Content.")
    print(f"\n{m['s']['assets']} assets measured in {time.time() - started:.0f} s. Wrote {csv_path} and {paste_path}.")
    if extra:
        print(f"\n{len(extra)} figure(s) have no row in the Preservica tab, so they're not in the paste "
              "(add a row in the sheet, then run again with --layout, to include them):")
        for c, metric, v in extra:
            print(f"  {c:<18} {metric:<40} {value_text(v)}")
    if unfilled:
        print("\nRows left blank (nothing measured for them this month): "
              + ", ".join(f"{c} {mt}" for c, mt in unfilled))

    cell = f"{col_letter(month)}{FIRST_ROW}"
    steps = [(f"Preservica tab: click cell {cell} ({month_name}, Content #Collections) and paste", pblock),
             (f"Storage tab: click cell {cell} ({month_name}, Tier 1 #TBPreservicaHot) and paste", sblock)]
    interactive = not args.no_clipboard and sys.stdin.isatty() and sys.stdout.isatty()
    print("")
    for i, (where, block) in enumerate(steps, start=1):
        if interactive and copy_to_clipboard(block):
            input(f"Step {i} of 2: copied {block.count(chr(10)) + 1} value(s). {where} (Ctrl+V). Press Enter when done... ")
        else:
            print(f"Step {i} of 2: {where} the block {i} from {paste_path}.")
    if interactive:
        print("All done. #Downloads (Use) comes from Google Analytics.")


if __name__ == "__main__":
    main()
