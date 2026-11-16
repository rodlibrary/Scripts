#!/usr/bin/env python3
"""
match_preservica.py
===================

Add each minted ARK to resolve.csv once it's recorded in an asset's MODS in
Preservica, pointing it at that asset's page in the public portal.

Rod Library's MODS template records an asset's ARK in two places:

    <identifier displayLabel="Archival Resource Key" type="ark">BLADE</identifier>
    <location>
        <physicalLocation displayLabel="Location (URL)" type="url">
            <url>https://n2t.net/ark:/32498/BLADE</url>
        </physicalLocation>
    </location>

Preservica's search index keeps neither the identifier's type nor the URL, so
this script lists every asset and reads its MODS record (about two requests
per asset). resolve.csv has two columns, like redirects.csv in
rodlibrary/Redirects:

    ark,p_path
    https://library.uni.edu/ark:/32498/s3x7k9q2mb,https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C<uuid>

For every ARK found in Preservica that's in the master list (so it was minted
here):

  * not in resolve.csv yet: a row is added, with p_path the asset's page in
    Preservica's portal (--portal-url, default
    https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C{ref});
  * already there with that page: nothing to do;
  * already there with another address, or 410 (withdrawn): left alone and
    reported (--overwrite replaces another address; 410 is never replaced).

An ARK found in more than one asset is never added: it's reported, since a
resolver needs exactly one target. ARKs in Preservica that aren't in the
master list are reported too. resolve.csv is only written or pushed if it
passes the checks in check_resolve.py.

USAGE
-----
    python3 match_preservica.py                       # update resolve.csv here
    python3 match_preservica.py --dry-run             # report only, change nothing
    export GITHUB_TOKEN=github_pat_...
    python3 match_preservica.py --from-github --push-to-github   # GitHub's copy, then push it back

The copies of record of resolve.csv and the master list are in the GitHub
repository rodlibrary/Resolver (--github-repo). The token needs Contents: Read
and write on it.

Writes match_report.csv (one row per asset with ARK fields, and the outcome).

Preservica login: the environment variables PRESERVICA_USERNAME,
PRESERVICA_PASSWORD, PRESERVICA_TENANT (default UNIOWA) and PRESERVICA_SERVER
(default us.preservica.com), or a credentials.properties file (the same one the
preservica scripts use) in this folder, the current folder or ../preservica.
The account needs Read Metadata on the assets' security tags.

Standard library only; run `python3 match_preservica.py -h` for every option.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate_arks as ga  # noqa: E402  (shares the GitHub helpers and file layout)

if not hasattr(ga, "fetch_file_from_github"):
    sys.exit(f"The generate_arks.py next to this script ({ga.__file__}) is an older version. "
             "Download the current one from https://github.com/rodlibrary/Scripts/tree/main/arks "
             "into the same folder (both scripts need to be up to date).")

DEFAULT_PORTAL_URL = ga.DEFAULT_PORTAL_URL
DEFAULT_REPORT = "match_report.csv"
REPORT_FIELDS = ["preservica_ref", "title", "ark_blade", "location_url", "ark", "outcome", "resolve_line"]
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


# --------------------------------------------------------------------------- ARKs

def ark_name(text: str, naan: str = "") -> str:
    """
    The comparable name of an ARK ("NAME" without NAAN, hyphens dropped since
    they carry no meaning in ARKs), from any of: ark:/32498/x7k9q, ark:32498/x7k9q,
    https://n2t.net/ark:/32498/x7k9q, 32498/x7k9q, or a bare blade x7k9q.
    '' if there's nothing usable, or the ARK is under a different NAAN.
    """
    t = (text or "").strip()
    m = re.search(r"ark:/?(\d{5,})/([^\s?#]+)", t) or re.fullmatch(r"(\d{5,})/([^\s?#/][^\s?#]*)", t)
    if m:
        if naan and m.group(1) != naan:
            return ""
        name = m.group(2)
    elif re.fullmatch(r"[A-Za-z0-9=~*+@_$.\-]+", t):
        name = t                      # a bare blade (or shoulder + blade)
    else:
        return ""
    return name.rstrip("./").replace("-", "")


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: str, fields: list[str], rows: list[dict]) -> None:
    tmp = path + ".partial"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") or "" for k in fields})
    os.replace(tmp, path)


# --------------------------------------------------------------------------- Preservica

def load_login() -> dict:
    """Preservica login from PRESERVICA_* variables or credentials.properties."""
    keys = {"username": "PRESERVICA_USERNAME", "password": "PRESERVICA_PASSWORD",
            "tenant": "PRESERVICA_TENANT", "server": "PRESERVICA_SERVER"}
    found = {}
    for path in ("credentials.properties", os.path.join(HERE, "credentials.properties"),
                 os.path.join(HERE, "..", "preservica", "credentials.properties")):
        if os.path.isfile(path):
            cfg = configparser.RawConfigParser(interpolation=None)
            cfg.read(path, encoding="utf-8-sig")
            section = next((s for s in cfg.sections() if s.strip().lower() == "credentials"), None)
            if section:
                found = {k.lower(): v.strip().strip("\"'") for k, v in cfg[section].items()}
                break
    login = {k: (os.environ.get(env) or "").strip() or found.get(k, "") for k, env in keys.items()}
    login["tenant"] = login["tenant"] or "UNIOWA"
    login["server"] = (login["server"] or "us.preservica.com").replace("https://", "").strip("/")
    missing = [k for k in ("username", "password") if not login[k]]
    if missing:
        sys.exit("No Preservica login: set PRESERVICA_USERNAME and PRESERVICA_PASSWORD, or put a "
                 "credentials.properties file ([credentials] username= password=) in this folder.")
    return login


class Preservica:
    def __init__(self, login: dict, timeout: int = 120):
        self.login_data = login
        self.base = f"https://{login['server']}"
        self.timeout = timeout
        self.token, self.token_time = None, 0.0
        self.lock = threading.Lock()

    def _open(self, req):
        return urllib.request.urlopen(req, timeout=self.timeout)

    def login(self) -> None:
        data = urllib.parse.urlencode({k: self.login_data[k] for k in ("username", "password", "tenant")}).encode()
        req = urllib.request.Request(f"{self.base}/api/accesstoken/login", data=data, method="POST",
                                     headers={"User-Agent": "match_preservica.py"})
        try:
            with self._open(req) as resp:
                self.token = json.loads(resp.read())["token"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200].replace(self.login_data["password"], "***")
            sys.exit(f"Preservica login failed (HTTP {e.code}): {body}")
        self.token_time = time.time()

    def _token(self, force=False) -> str:
        with self.lock:
            if force or not self.token or time.time() - self.token_time > 12 * 60:
                self.login()
            return self.token

    def request(self, url: str, data: "dict | list | None" = None) -> tuple[int, bytes]:
        if not url.startswith("http"):
            url = self.base + url
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        force = False
        for attempt in range(4):
            req = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                         headers={"Preservica-Access-Token": self._token(force),
                                                  "User-Agent": "match_preservica.py"})
            force = False
            try:
                with self._open(req) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    force = True
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                return e.code, e.read()
            except urllib.error.URLError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        return 0, b""

    def assets(self, page: int = 100):
        """Yield (reference, title) for every asset the account can see."""
        start, total = 0, None
        while total is None or start < total:
            q = json.dumps({"q": "*", "fields": [{"name": "xip.document_type", "values": ["IO"]},
                                                 {"name": "xip.title", "values": []}]})
            code, raw = self.request("/api/content/search",
                                     [("start", start), ("max", page), ("q", q), ("metadata", "xip.title")])
            if code != 200:
                sys.exit(f"Preservica search failed (HTTP {code}): {raw[:300].decode('utf-8', 'replace')}")
            value = json.loads(raw).get("value", {})
            total = int(value.get("totalHits") or 0)
            ids, meta = value.get("objectIds") or [], value.get("metadata") or []
            for i, oid in enumerate(ids):
                m = re.search(rf"IO\|({UUID})", oid)
                if not m:
                    continue
                fields = {f["name"]: f.get("value") for f in (meta[i] if i < len(meta) else [])}
                title = fields.get("xip.title")
                yield m.group(1).lower(), (title[0] if isinstance(title, list) and title else title) or ""
            if not ids:
                break
            start += len(ids)

    def mods_ark_fields(self, ref: str) -> tuple[list[str], list[str], str]:
        """(ARK blades, Location URLs, problem) from the asset's MODS record(s)."""
        code, raw = self.request(f"/api/entity/information-objects/{ref}")
        if code != 200:
            return [], [], f"HTTP {code} reading the asset"
        frags = [(e.get("schema") or "", (e.text or "").strip()) for e in iter_local(parse(raw), "Fragment")]
        frags = [url for schema, url in frags if "mods" in schema.lower() and url.startswith("http")]
        if not frags:
            return [], [], "no MODS record"
        blades, urls, problem = [], [], ""
        for url in frags:
            code, raw = self.request(url)
            if code != 200:
                problem = f"HTTP {code} reading MODS"
                continue
            b, u = read_mods(raw)
            blades += [x for x in b if x not in blades]
            urls += [x for x in u if x not in urls]
        return blades, urls, problem


def parse(raw: bytes):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return ET.Element("empty")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def iter_local(root, name: str):
    return (e for e in root.iter() if local(e.tag) == name)


def read_mods(raw: bytes) -> tuple[list[str], list[str]]:
    """The Archival Resource Key identifiers and Location (URL) urls in a MODS response."""
    root = parse(raw)
    mods = next(iter_local(root, "mods"), None)
    if mods is None:                       # MODS sent as escaped text inside <Content>
        content = next(iter_local(root, "Content"), None)
        if content is not None and (content.text or "").strip().startswith("<"):
            mods = next(iter_local(parse(content.text.strip().encode()), "mods"), None)
    if mods is None:
        return [], []
    blades, urls = [], []
    for e in iter_local(mods, "identifier"):
        label = (e.get("displayLabel") or "").lower()
        if (e.get("type") or "").lower() == "ark" or "archival resource key" in label:
            v = (e.text or "").strip()
            if v and v not in blades:
                blades.append(v)
    for e in iter_local(mods, "physicalLocation"):
        if (e.get("type") or "").lower() != "url" and "location (url)" not in (e.get("displayLabel") or "").lower():
            continue
        inner = next(iter_local(e, "url"), None)
        v = ((inner.text if inner is not None else e.text) or "").strip()
        if v and v not in urls:
            urls.append(v)
    for e in iter_local(mods, "url"):          # <location><url displayLabel="Location (URL)">
        v = (e.text or "").strip()
        if v and "location (url)" in (e.get("displayLabel") or "").lower() and v not in urls:
            urls.append(v)
    return blades, urls


# --------------------------------------------------------------------------- matching

def gather(api: Preservica, workers: int = 4, log=print) -> list[dict]:
    """Every asset with ARK fields: {ref, title, blades, urls, problem}."""
    assets = list(api.assets())
    log(f"{len(assets)} asset(s) in Preservica; reading their MODS records.")
    done = [0]

    def one(item):
        ref, title = item
        blades, urls, problem = api.mods_ark_fields(ref)
        done[0] += 1
        if done[0] % 100 == 0:
            log(f"  read {done[0]} of {len(assets)}")
        return {"ref": ref, "title": title, "blades": blades, "urls": urls, "problem": problem}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        found = list(pool.map(one, assets))
    # Assets without a MODS record simply have no ARK; read errors are kept for the report.
    return [a for a in found if a["blades"] or a["urls"] or (a["problem"] and a["problem"] != "no MODS record")]


def match(rows: list[dict], master: list[dict], assets: list[dict], naan: str, portal_url: str,
          overwrite: bool = False, ark_base: str = ga.DEFAULT_ARK_BASE) -> tuple[list[dict], dict]:
    """
    Apply the Preservica ARKs to resolve.csv's rows (in place: rows are
    added or, with overwrite, replaced). Returns the report rows and a summary
    of counts.
    """
    # Every minted ARK, by its name and by its bare blade (MODS may leave out the shoulder).
    minted, by_blade = {}, {}
    for m in master:
        key = ga.ark_key(m.get("ark", ""), naan)
        if not key:
            continue
        minted.setdefault(key, m["ark"].strip())
        blade = (m.get("ark_blade") or "").replace("-", "").lower()
        if blade:
            by_blade.setdefault(blade, key)
    line_of = {}
    for i, r in enumerate(rows):
        key = ga.ark_key(r.get("ark", ""), naan)
        if key:
            line_of.setdefault(key, i)
            minted.setdefault(key, r["ark"])         # listed by hand without being in the master list

    def key_for(name: str) -> "str | None":
        k = f"{naan}/{name.lower()}"
        return k if k in minted else by_blade.get(name.lower())

    report, holders = [], {}
    for a in assets:
        ref = f"IO_{a['ref']}"
        names = [n for n in (ark_name(x, naan) for x in a["urls"] + a["blades"]) if n]
        keys = {key_for(n) for n in names}
        found = keys - {None}
        if a["problem"] and not names:
            outcome = a["problem"]
        elif not names:
            outcome = "ARK fields hold no ARK"
        elif len(found) > 1:
            outcome = "blade and URL name different ARKs"
        elif not found:
            outcome = "ARK not in the master list"
        else:
            outcome = "found"
            holders.setdefault(next(iter(found)), []).append(ref)
        key = next(iter(found)) if len(found) == 1 else ""
        report.append({"preservica_ref": ref, "title": a["title"], "ark_blade": " | ".join(a["blades"]),
                       "location_url": " | ".join(a["urls"]),
                       "ark": ga.ark_address(minted[key], ark_base) if key else "",
                       "outcome": outcome, "resolve_line": "", "_key": key})

    summary = {"assets_with_ark_fields": len(assets), "updated": 0, "added": 0, "replaced": 0,
               "already": 0, "conflicts": 0, "shared": 0, "unknown": 0, "problems": 0}
    for rep in report:
        key = rep.pop("_key")
        if rep["outcome"] != "found":
            o = rep["outcome"]
            summary["unknown" if o == "ARK not in the master list" else
                    "conflicts" if o.startswith("blade and URL") else "problems"] += 1
            continue
        if len(holders[key]) > 1:
            rep["outcome"] = f"ARK found in {len(holders[key])} assets: {', '.join(holders[key])}"
            summary["shared"] += 1
            continue
        target = portal_url.format(ref=a_ref(rep["preservica_ref"]), io_ref=rep["preservica_ref"]) if portal_url else ""
        idx = line_of.get(key)
        if idx is None:
            if not target:
                rep["outcome"] = "not in resolve.csv (no --portal-url, so not added)"
                summary["already"] += 1
                continue
            rows.append({"ark": rep["ark"], "p_path": target})
            line_of[key] = len(rows) - 1
            rep["outcome"] = "added"
            summary["added"] += 1
            summary["updated"] += 1
        else:
            current = (rows[idx].get("p_path") or "").strip()
            if not target or current == target:
                rep["outcome"] = "already up to date"
                summary["already"] += 1
            elif current.lower() in (ga.GONE, "gone"):
                rep["outcome"] = "withdrawn (410) in resolve.csv: left as it is"
                summary["already"] += 1
            elif overwrite:
                rows[idx]["p_path"] = target
                rep["outcome"] = f"replaced {current}"
                summary["replaced"] += 1
                summary["updated"] += 1
            else:
                rep["outcome"] = f"resolve.csv already sends it to {current}"
                summary["conflicts"] += 1
        rep["resolve_line"] = line_of[key] + 2
    summary["waiting"] = sum(1 for k in minted if k not in line_of)
    return report, summary


def a_ref(io_ref: str) -> str:
    return io_ref[3:] if io_ref.startswith("IO_") else io_ref


# --------------------------------------------------------------------------- main

def run(resolve_path: str, master_path: "str | None", naan: str = ga.DEFAULT_NAAN,
        portal_url: str = DEFAULT_PORTAL_URL, overwrite: bool = False, dry_run: bool = False,
        report_path: str = DEFAULT_REPORT, workers: int = 4, api: "Preservica | None" = None,
        ark_base: str = ga.DEFAULT_ARK_BASE, allowed_hosts=ga.DEFAULT_ALLOWED_TARGET_HOSTS) -> dict:
    """Match the master list against Preservica and update resolve.csv. Returns the summary."""
    if not os.path.exists(resolve_path) and not (master_path and os.path.exists(master_path)):
        sys.exit(f"Neither {resolve_path} nor the master list was found. Run generate_arks.py first, "
                 "or use --from-github.")
    rows = ga.read_resolve(resolve_path, ark_base)
    converted = ga.read_resolve_raw(resolve_path) is None
    master = read_csv(master_path)[1] if master_path and os.path.exists(master_path) else []
    api = api or Preservica(load_login())
    assets = gather(api, workers)
    report, summary = match(rows, master, assets, naan, portal_url, overwrite, ark_base)
    write_csv(report_path, REPORT_FIELDS, report)
    errors = ga.check_resolve(rows, naan, ark_base, allowed_hosts)
    summary["errors"] = errors
    if errors:
        print(f"resolve.csv has {len(errors)} problem(s), so it wasn't changed:", file=sys.stderr)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)
    elif (summary["updated"] or converted) and not dry_run:
        ga.write_resolve(resolve_path, rows)
    print(f"{summary['assets_with_ark_fields']} asset(s) have ARK fields: {summary['added']} ARK(s) added to resolve.csv"
          f"{', ' + str(summary['replaced']) + ' replaced' if summary['replaced'] else ''}"
          f"{' (dry run: resolve.csv not changed)' if dry_run and summary['updated'] else ''}, "
          f"{summary['already']} already listed, {summary['unknown']} not in the master list, "
          f"{summary['shared']} in more than one asset, {summary['conflicts']} conflict(s), "
          f"{summary['problems']} asset(s) with unreadable ARK fields; "
          f"{summary['waiting']} minted ARK(s) not in resolve.csv yet. Details: {report_path}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Add the minted ARKs found in Preservica's MODS to resolve.csv.")
    ap.add_argument("--resolve", default=ga.DEFAULT_RESOLVE_MANIFEST, help="resolve.csv to update (default: resolve.csv)")
    ap.add_argument("--master-list", default=ga.DEFAULT_MASTER_LIST,
                    help="ARK master list, to recognise bare blades that leave out the shoulder (default: ark_master_list.csv)")
    ap.add_argument("--report", default=DEFAULT_REPORT, help=f"report CSV (default: {DEFAULT_REPORT})")
    ap.add_argument("--naan", default=ga.DEFAULT_NAAN, help=f"only ARKs under this NAAN (default: {ga.DEFAULT_NAAN})")
    ap.add_argument("--portal-url", default=os.environ.get("PRESERVICA_PORTAL_URL") or DEFAULT_PORTAL_URL,
                    help="p_path for a matched ARK; {ref} is the asset's reference "
                         # argparse treats % in help as formatting, so the URL's %3A and %7C are doubled.
                         f"(default: {DEFAULT_PORTAL_URL.replace('%', '%%')}). Use '' to report only.")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace a p_path that differs from the asset's page (410 is never replaced)")
    ap.add_argument("--ark-base", default=os.environ.get("ARK_BASE_URL") or ga.DEFAULT_ARK_BASE,
                    help=f"address ARKs resolve at, for resolve.csv's ark column (default: {ga.DEFAULT_ARK_BASE})")
    ap.add_argument("--allowed-host", action="append",
                    help="host a p_path may point to; repeat for several (default: "
                         + ", ".join(ga.DEFAULT_ALLOWED_TARGET_HOSTS) + ", or ALLOWED_TARGET_HOSTS)")
    ap.add_argument("--dry-run", action="store_true", help="write the report only; don't change or push resolve.csv")
    ap.add_argument("--workers", type=int, default=4, help="assets read at once (default 4)")
    ap.add_argument("--from-github", action="store_true", help="start from GitHub's resolve.csv and master list")
    ap.add_argument("--push-to-github", action="store_true", help="push resolve.csv to GitHub if it changed")
    ap.add_argument("--github-repo", default=os.environ.get("RESOLVER_REPO") or ga.DEFAULT_GITHUB_REPO,
                    help=f"resolver repository as owner/name (default: {ga.DEFAULT_GITHUB_REPO})")
    ap.add_argument("--github-branch", default=os.environ.get("RESOLVER_BRANCH") or ga.DEFAULT_GITHUB_BRANCH,
                    help=f"its branch (default: {ga.DEFAULT_GITHUB_BRANCH})")
    ap.add_argument("--github-manifest-path", default=None, help="resolve.csv's path in GitHub (default: same filename)")
    ap.add_argument("--github-master-path", default=None, help="the master list's path in GitHub (default: same filename)")
    ap.add_argument("--push-report", action="store_true",
                    help="with --push-to-github, also push the report to the resolver repository when it changed")
    ap.add_argument("--github-report-path", default=None, help="the report's path in GitHub (default: same filename)")
    ap.add_argument("--github-token-env", default=ga.DEFAULT_GITHUB_TOKEN_ENV,
                    help=f"environment variable holding the GitHub token (default: {ga.DEFAULT_GITHUB_TOKEN_ENV})")
    ap.add_argument("--github-api-url", default=ga.DEFAULT_GITHUB_API_URL, help=argparse.SUPPRESS)
    ga.reject_gitlab_options()
    args = ap.parse_args()
    hosts = ga.allowed_hosts(args.allowed_host)

    token = os.environ.get(args.github_token_env, "").strip()
    repo, branch, api_url = args.github_repo, args.github_branch, args.github_api_url
    paths = {"resolve": args.github_manifest_path or os.path.basename(args.resolve),
             "master": args.github_master_path or os.path.basename(args.master_list)}
    if (args.from_github or args.push_to_github) and not token:
        sys.exit(f"Set {args.github_token_env} to a GitHub token with Contents: Read and write on {repo}.")
    pushed_before = None      # GitHub's resolve.csv, to push only when it changes
    if args.from_github:
        for local_path, key in ((args.resolve, "resolve"), (args.master_list, "master")):
            try:
                data = ga.fetch_file_from_github(api_url, repo, paths[key], branch, token)
            except RuntimeError as e:
                sys.exit(f"Couldn't download {paths[key]} from GitHub: {e}")
            if data is None:
                if key == "master":
                    sys.exit(f"{paths[key]} isn't in {repo} yet. Run generate_arks.py --push-to-github first.")
                continue
            if key == "resolve":
                pushed_before = data
            with open(local_path, "wb") as f:
                f.write(data)
            print(f"Downloaded {paths[key]} from {repo}.")
    elif args.push_to_github:
        # Merge GitHub's newer rows in first, so the push never drops them.
        try:
            remote = ga.fetch_file_from_github(api_url, repo, paths["resolve"], branch, token)
        except RuntimeError as e:
            sys.exit(f"Couldn't download {paths['resolve']} from GitHub: {e}")
        ga.merge_resolve(args.resolve, remote, args.ark_base)
        pushed_before = remote

    summary = run(args.resolve, args.master_list, args.naan, args.portal_url, args.overwrite,
                  args.dry_run, args.report, args.workers, ark_base=args.ark_base, allowed_hosts=hosts)

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"**{summary['added']}** ARK(s) added to resolve.csv"
                    f"{', ' + str(summary['replaced']) + ' replaced' if summary['replaced'] else ''}"
                    f"{' (dry run)' if args.dry_run else ''}; {summary['already']} already listed; "
                    f"{summary['unknown']} ARK(s) in Preservica not in the master list; "
                    f"{summary['shared']} in more than one asset; {summary['conflicts']} conflict(s); "
                    f"{summary['problems']} asset(s) with unreadable ARK fields; "
                    f"{summary['waiting']} minted ARK(s) not in resolve.csv yet. "
                    f"{summary['assets_with_ark_fields']} asset(s) have ARK fields."
                    + (f" **resolve.csv has {len(summary['errors'])} problem(s) and wasn't changed.**"
                       if summary["errors"] else "") + "\n")

    if args.push_to_github and args.push_report:
        report_path = args.github_report_path or os.path.basename(args.report)
        try:
            remote = ga.fetch_file_from_github(api_url, repo, report_path, branch, token)
            with open(args.report, "rb") as f:
                if f.read() != remote:
                    ga.push_file_to_github(args.report, api_url, repo, report_path, branch, token,
                                           "Update the Preservica ARK match report")
                    print(f"Pushed {args.report} to GitHub.")
        except RuntimeError as e:
            print(f"Warning: the report couldn't be pushed to GitHub: {e}", file=sys.stderr)

    if args.push_to_github and not args.dry_run:
        if summary["errors"]:
            sys.exit("resolve.csv has problems (listed above), so it wasn't pushed. "
                     "Fix them in the resolver repository; check_resolve.py --from-github lists them.")
        with open(args.resolve, "rb") as f:
            if f.read() == pushed_before:
                print("resolve.csv unchanged; nothing to push.")
                return
        message = (f"Add {summary['added']} ARK(s) matched in Preservica"
                   + (f", replace {summary['replaced']}" if summary["replaced"] else "")
                   if summary["updated"] else "Write resolve.csv as ark, p_path")
        try:
            url = ga.push_file_to_github(args.resolve, api_url, repo, paths["resolve"], branch, token,
                                         message)
        except RuntimeError as e:
            sys.exit(f"GitHub push failed: {e}")
        print(f"Pushed {args.resolve} to GitHub: {url}")


if __name__ == "__main__":
    main()
