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

It writes, in the folder you run it from:
  * preservica_stats_<YYYY-MM>.csv: Tab, Category, Metric and the month's value
    for every figure, in the order of the sheet's rows;
  * preservica_stats_<YYYY-MM>_paste.txt: blocks to paste into the sheet: the
    DCDS tab's Preservica rows, the Storage tab's Tier 1 rows, and the
    Preservica tab (labels + value in the month's column, and values only).

Usage
    python preservica_monthly_stats.py                  # this month (the month just ended, in the first 5 days)
    python preservica_monthly_stats.py --month 2026-10
    python preservica_monthly_stats.py --dcds-counts total   # all-time counts for the DCDS rows

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

FLOW, SNAPSHOT = "flow", "snapshot"     # how the Totals column treats a row


def tb(n):
    return round(n / TB, 4)


def tb2(n):
    return round(n / TB, 2)


def preservica_rows(m):
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
    label = lambda prefix, e: f"{prefix} .{e}" if not e.startswith("(") else f"{prefix} {e}"
    for e in sorted(m["by_ext"]):
        rows.append(("AssetsByFileType", label("#Assets", e), m["by_ext"][e], SNAPSHOT))
    for e in sorted(m["ext_files"]):
        rows.append(("FilesByFileType", label("#Files", e), m["ext_files"][e], SNAPSHOT))
    for e in sorted(m["ext_bytes"]):
        rows.append(("TBByFileType", label("#TB", e), tb(m["ext_bytes"][e]), SNAPSHOT))
    for g in sorted(m["by_group"]):
        rows.append(("FormatGroups", f"#Assets {g} ({kind_of(g if g != '(none)' else '')})",
                     m["by_group"][g], SNAPSHOT))
    if m["integrity"]:
        rows += [
            ("Integrity", "#FilesCheckedThisMonth", s["checked"], FLOW),
            ("Integrity", "#FilesFailedLastCheck", s["failed"], SNAPSHOT),
            ("Integrity", "#FilesNeverChecked", s["never_checked"], SNAPSHOT),
        ]
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
    ]


def storage_rows(m):
    """TB with 2 decimals, as the Storage tab shows them."""
    return [("Tier 1", "#TBPreservicaHot", tb2(m["s"]["hot"])),
            ("Tier 1", "#TBPreservicaCold", tb2(m["s"]["cold"]))]


# --------------------------------------------------------------------------- output

def value_text(v):
    return "" if v is None else str(v)


def write_outputs(base, month_name, year, month, prows, srows, drows, dcds_counts):
    """The CSV and the paste file. Returns their names."""
    import csv
    csv_path, paste_path = f"{base}.csv", f"{base}_paste.txt"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Tab", "Category", "Metric", f"{month_name} {year}"])
        last = None
        for tab, rows in (("DCDS", drows), ("Storage", srows), ("Preservica", [r[:3] for r in prows])):
            for cat, metric, v in rows:
                w.writerow([tab, cat if (tab, cat) != last else "", metric, value_text(v)])
                last = (tab, cat)
    blank_cols = "\t" * (month - 1)            # January is column C: pad to the month's column
    with open(paste_path, "w", encoding="utf-8") as f:
        f.write(f"Preservica figures for {month_name} {year}. Copy a block (not its heading) and paste it into the sheet.\n\n")
        f.write(f"== DCDS tab: click the {month_name} cell of the Preservica #Collections row, then paste "
                f"({'added that month' if dcds_counts == 'added' else 'all-time totals'}; "
                "#Downloads comes from Google Analytics)\n")
        f.write("\n".join(value_text(v) for _, _, v in drows) + "\n\n")
        f.write(f"== Storage tab: click the {month_name} cell of the Tier 1 #TBPreservicaHot row, then paste (TB)\n")
        f.write("\n".join(value_text(v) for _, _, v in srows) + "\n\n")
        f.write(f"== Preservica tab, FIRST TIME only (empty tab): click cell A3, then paste. It fills in the "
                f"labels and puts the values in the {month_name} column\n")
        last = None
        for cat, metric, v, _ in prows:
            f.write(f"{cat if cat != last else ''}\t{metric}\t{blank_cols}{value_text(v)}\n")
            last = cat
        f.write(f"\n== Preservica tab, later months: if the labels match the tab's rows, click the {month_name} "
                "cell of the Content #Collections row, then paste\n")
        f.write("\n".join(value_text(v) for _, _, v, _ in prows) + "\n")
    return csv_path, paste_path


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
    ap.add_argument("--month", help="Month to report, e.g. 2026-10. Default: this month (the month just "
                                    "ended, in the first 5 days of a month).")
    ap.add_argument("--dcds-counts", choices=["added", "total"], default="added",
                    help="DCDS Preservica rows: what was added that month (default) or all-time totals.")
    ap.add_argument("--cold-pattern", default=DEFAULT_COLD,
                    help=f"Storage locations whose names match count as Cold (default: {DEFAULT_COLD}).")
    ap.add_argument("--no-integrity", action="store_true",
                    help="Skip the integrity-check history (one request fewer per file; a little faster).")
    ap.add_argument("--workers", type=int, default=8, help="Assets measured at once (default 8).")
    ap.add_argument("--output", help="Base name for the output files (default: preservica_stats_<YYYY-MM>).")
    ap.add_argument("--quiet", action="store_true", help="Don't print the figures.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

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

    prows = preservica_rows(m)
    drows = dcds_rows(prows, args.dcds_counts)
    srows = storage_rows(m)
    base = args.output or f"preservica_stats_{year}-{month:02d}"
    csv_path, paste_path = write_outputs(base, month_name, year, month, prows, srows, drows, args.dcds_counts)

    if not args.quiet:
        for title, rows in ((f"DCDS tab, Preservica rows ({args.dcds_counts})", drows),
                            ("Storage tab, Tier 1 (TB)", srows), ("Preservica tab", [r[:3] for r in prows])):
            print(f"\n== {title}, {month_name} {year}")
            for r in rows:
                print(f"  {r[0]:<20} {r[1]:<45} {value_text(r[2])}")
    refused = {k: n for k, n in p.status.items() if k[1] != 200}
    for (kind, code), n in sorted(refused.items(), key=str):
        log.warning("%d %s request(s) got %s from Preservica", n, kind, f"HTTP {code}" if code else "no answer")
    for ref, problem in m["failed"][:10]:
        log.warning("Asset %s couldn't be measured: %s", ref, problem)
    if any(code in (401, 403) for _, code in refused):
        log.warning("HTTP 401/403: the account needs the Access functional role with Read Metadata and Read Content.")
    log.info("\n%d assets measured in %.0f s. Wrote %s and %s.", m["s"]["assets"], time.time() - started,
             csv_path, paste_path)


if __name__ == "__main__":
    main()
