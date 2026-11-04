#!/usr/bin/env python3
"""
match_preservica.py
===================

Fill in the Preservica reference of each ARK in resolve.csv, from the ARKs
recorded in Preservica's MODS metadata.

Rod Library's MODS template records an asset's ARK in two places:

    <identifier displayLabel="Archival Resource Key" type="ark">BLADE</identifier>
    <location>
        <physicalLocation displayLabel="Location (URL)" type="url">
            <url>https://n2t.net/ark:/32498/BLADE</url>
        </physicalLocation>
    </location>

Preservica's search index keeps neither the identifier's type nor the URL, so
this script lists every asset and reads its MODS record (about two requests
per asset). For every ARK found it looks the ARK up in resolve.csv and:

  * fills in preservica_ref (IO_<reference>) when it's blank;
  * fills in target_url with the asset's page in Preservica's public portal
    (--portal-url, default https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C{ref})
    when it's blank, and changes the status from "unmatched" to "active";
  * leaves rows that already have a different reference or target alone and
    reports them (--overwrite replaces them).

An ARK found in more than one asset is never filled in: it's reported, since
a resolver needs exactly one target. ARKs in Preservica that resolve.csv
doesn't have are reported too.

USAGE
-----
    python3 match_preservica.py                       # update resolve.csv here
    python3 match_preservica.py --dry-run             # report only, change nothing
    export GITLAB_TOKEN=glpat-...
    python3 match_preservica.py --from-gitlab --push-to-gitlab   # GitLab's copy, then push it back

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
import datetime
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
import generate_arks as ga  # noqa: E402  (shares the GitLab helpers and file layout)

if not hasattr(ga, "fetch_file_from_gitlab"):
    sys.exit(f"The generate_arks.py next to this script ({ga.__file__}) is an older version. "
             "Download the current one from https://github.com/rodlibrary/Scripts/tree/main/arks "
             "into the same folder (both scripts need to be up to date).")

DEFAULT_PORTAL_URL = "https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C{ref}"
DEFAULT_REPORT = "match_report.csv"
REPORT_FIELDS = ["preservica_ref", "title", "ark_blade", "location_url", "ark", "outcome", "resolve_row"]
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
          overwrite: bool = False, today: str = "") -> tuple[list[dict], dict]:
    """
    Apply the Preservica ARKs to the resolve.csv rows (in place). Returns the
    report rows and a summary of counts.
    """
    today = today or datetime.date.today().isoformat()
    by_name = {}
    for i, r in enumerate(rows):
        n = ark_name(r.get("ark", ""), naan) or ark_name("ark:/" + r.get("ark", ""), naan)
        if n:
            by_name.setdefault(n, i)
    # A bare blade in MODS may leave out the shoulder: the master list records each blade.
    by_blade = {}
    for m in master:
        blade = (m.get("ark_blade") or "").replace("-", "")
        n = ark_name(m.get("ark", ""), naan)
        if blade and n in by_name:
            by_blade.setdefault(blade, by_name[n])

    report, holders = [], {}
    for a in assets:
        ref = f"IO_{a['ref']}"
        url_names = [n for n in (ark_name(u, naan) for u in a["urls"]) if n]
        blade_names = [n for n in (ark_name(b, naan) for b in a["blades"]) if n]
        idx = None
        for n in url_names + blade_names:
            idx = by_name.get(n, by_blade.get(n))
            if idx is not None:
                break
        names = {n for n in url_names + blade_names}
        rows_hit = {by_name.get(n, by_blade.get(n)) for n in names} - {None}
        if a["problem"] and not names:
            outcome = a["problem"]
        elif not names:
            outcome = "ARK fields hold no ARK"
        elif len(rows_hit) > 1:
            outcome = "blade and URL name different ARKs in resolve.csv"
            idx = None
        elif idx is None:
            outcome = "ARK not in resolve.csv"
        else:
            outcome = "found"
            holders.setdefault(idx, []).append(ref)
        report.append({"preservica_ref": ref, "title": a["title"], "ark_blade": " | ".join(a["blades"]),
                       "location_url": " | ".join(a["urls"]),
                       "ark": rows[idx].get("ark", "") if idx is not None else "",
                       "outcome": outcome, "resolve_row": (idx + 2) if idx is not None else ""})

    summary = {"assets_with_ark_fields": len(assets), "updated": 0, "already": 0, "conflicts": 0,
               "shared": 0, "unknown": 0, "problems": 0}
    for rep in report:
        if rep["outcome"] != "found":
            o = rep["outcome"]
            summary["unknown" if o == "ARK not in resolve.csv" else "conflicts" if o.startswith("blade and URL") else "problems"] += 1
            continue
        idx = rep["resolve_row"] - 2
        row, ref = rows[idx], rep["preservica_ref"]
        if len(holders[idx]) > 1:
            rep["outcome"] = f"ARK found in {len(holders[idx])} assets: {', '.join(holders[idx])}"
            summary["shared"] += 1
            continue
        current = (row.get("preservica_ref") or "").strip()
        current_ref = (re.search(UUID, current) or [""])[0].lower()
        if current and current_ref != ref[3:] and not overwrite:
            rep["outcome"] = f"resolve.csv already has {current}"
            summary["conflicts"] += 1
            continue
        changes = []
        if current_ref != ref[3:]:
            row["preservica_ref"] = ref
            changes.append("preservica_ref")
        target = portal_url.format(ref=a_ref(ref), io_ref=ref)
        if portal_url and (not (row.get("target_url") or "").strip() or (overwrite and row.get("target_url") != target)):
            if row.get("target_url") != target:
                row["target_url"] = target
                changes.append("target_url")
        if changes and (row.get("status") or "").strip().lower() in ("", "unmatched") and row.get("target_url"):
            row["status"] = "active"
            changes.append("status")
        if changes:
            note = f"Preservica {ref} matched {today}"
            row["notes"] = "; ".join(x for x in [(row.get("notes") or "").strip(), note] if x)
            rep["outcome"] = "updated: " + ", ".join(changes)
            summary["updated"] += 1
        else:
            rep["outcome"] = "already up to date"
            summary["already"] += 1
    return report, summary


def a_ref(io_ref: str) -> str:
    return io_ref[3:] if io_ref.startswith("IO_") else io_ref


# --------------------------------------------------------------------------- main

def run(resolve_path: str, master_path: "str | None", naan: str = ga.DEFAULT_NAAN,
        portal_url: str = DEFAULT_PORTAL_URL, overwrite: bool = False, dry_run: bool = False,
        report_path: str = DEFAULT_REPORT, workers: int = 4, api: "Preservica | None" = None) -> dict:
    """Match resolve.csv against Preservica and update it. Returns the summary."""
    if not os.path.exists(resolve_path):
        sys.exit(f"{resolve_path} not found. Run generate_arks.py first, or use --from-gitlab.")
    fields, rows = read_csv(resolve_path)
    master = read_csv(master_path)[1] if master_path and os.path.exists(master_path) else []
    api = api or Preservica(load_login())
    assets = gather(api, workers)
    report, summary = match(rows, master, assets, naan, portal_url, overwrite)
    write_csv(report_path, REPORT_FIELDS, report)
    if summary["updated"] and not dry_run:
        write_csv(resolve_path, fields or list(ga.RESOLVE_FIELDS), rows)
    print(f"{summary['assets_with_ark_fields']} asset(s) have ARK fields: {summary['updated']} ARK(s) updated"
          f"{' (dry run: resolve.csv not changed)' if dry_run and summary['updated'] else ''}, "
          f"{summary['already']} already up to date, {summary['unknown']} not in resolve.csv, "
          f"{summary['shared']} in more than one asset, {summary['conflicts']} conflict(s), "
          f"{summary['problems']} asset(s) with unreadable ARK fields. "
          f"Details: {report_path}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill in resolve.csv's Preservica references from the ARKs in Preservica's MODS.")
    ap.add_argument("--resolve", default=ga.DEFAULT_RESOLVE_MANIFEST, help="resolve.csv to update (default: resolve.csv)")
    ap.add_argument("--master-list", default=ga.DEFAULT_MASTER_LIST,
                    help="ARK master list, to recognise bare blades that leave out the shoulder (default: ark_master_list.csv)")
    ap.add_argument("--report", default=DEFAULT_REPORT, help=f"report CSV (default: {DEFAULT_REPORT})")
    ap.add_argument("--naan", default=ga.DEFAULT_NAAN, help=f"only ARKs under this NAAN (default: {ga.DEFAULT_NAAN})")
    ap.add_argument("--portal-url", default=os.environ.get("PRESERVICA_PORTAL_URL") or DEFAULT_PORTAL_URL,
                    help="target_url for a matched ARK with none; {ref} is the asset's reference "
                         f"(default: {DEFAULT_PORTAL_URL}). Use '' to leave target_url alone.")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace a preservica_ref or target_url that differs from what Preservica says")
    ap.add_argument("--dry-run", action="store_true", help="write the report only; don't change or push resolve.csv")
    ap.add_argument("--workers", type=int, default=4, help="assets read at once (default 4)")
    ap.add_argument("--from-gitlab", action="store_true", help="start from GitLab's resolve.csv and master list")
    ap.add_argument("--push-to-gitlab", action="store_true", help="push resolve.csv to GitLab if it changed")
    ap.add_argument("--gitlab-url", default=os.environ.get("GITLAB_URL") or ga.DEFAULT_GITLAB_URL)
    ap.add_argument("--gitlab-project", default=os.environ.get("GITLAB_PROJECT") or ga.DEFAULT_GITLAB_PROJECT)
    ap.add_argument("--gitlab-branch", default=os.environ.get("GITLAB_BRANCH") or "main")
    ap.add_argument("--gitlab-manifest-path", default=None, help="resolve.csv's path in GitLab (default: same filename)")
    ap.add_argument("--gitlab-master-path", default=None, help="the master list's path in GitLab (default: same filename)")
    ap.add_argument("--push-report", action="store_true",
                    help="with --push-to-gitlab, also push the report to GitLab when it changed "
                         "(keeps it out of public places such as GitHub run logs)")
    ap.add_argument("--gitlab-report-path", default=None, help="the report's path in GitLab (default: same filename)")
    ap.add_argument("--gitlab-token-env", default="GITLAB_TOKEN")
    args = ap.parse_args()

    token = os.environ.get(args.gitlab_token_env, "").strip()
    paths = {"resolve": args.gitlab_manifest_path or os.path.basename(args.resolve),
             "master": args.gitlab_master_path or os.path.basename(args.master_list)}
    if (args.from_gitlab or args.push_to_gitlab) and not token:
        sys.exit(f"Set {args.gitlab_token_env} to a GitLab token with api scope for {args.gitlab_project}.")
    if args.from_gitlab:
        for local_path, key in ((args.resolve, "resolve"), (args.master_list, "master")):
            try:
                data = ga.fetch_file_from_gitlab(args.gitlab_url, args.gitlab_project, paths[key],
                                                 args.gitlab_branch, token)
            except RuntimeError as e:
                sys.exit(f"Couldn't download {paths[key]} from GitLab: {e}")
            if data is None:
                if key == "resolve":
                    sys.exit(f"{paths[key]} isn't in {args.gitlab_project} yet. Run generate_arks.py --push-to-gitlab first.")
                continue
            with open(local_path, "wb") as f:
                f.write(data)
            print(f"Downloaded {paths[key]} from {args.gitlab_project}.")
    elif args.push_to_gitlab:
        # Merge GitLab's newer rows in first, so the push never drops them.
        try:
            remote = ga.fetch_file_from_gitlab(args.gitlab_url, args.gitlab_project, paths["resolve"],
                                               args.gitlab_branch, token)
        except RuntimeError as e:
            sys.exit(f"Couldn't download {paths['resolve']} from GitLab: {e}")
        ga.merge_into_local(args.resolve, remote)

    summary = run(args.resolve, args.master_list, args.naan, args.portal_url, args.overwrite,
                  args.dry_run, args.report, args.workers)

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"**{summary['updated']}** ARK(s) updated from Preservica"
                    f"{' (dry run)' if args.dry_run else ''}; {summary['already']} already up to date; "
                    f"{summary['unknown']} ARK(s) in Preservica not in resolve.csv; "
                    f"{summary['shared']} in more than one asset; {summary['conflicts']} conflict(s); "
                    f"{summary['problems']} asset(s) with unreadable ARK fields. "
                    f"{summary['assets_with_ark_fields']} asset(s) have ARK fields.\n")

    if args.push_to_gitlab and args.push_report:
        report_path = args.gitlab_report_path or os.path.basename(args.report)
        try:
            remote = ga.fetch_file_from_gitlab(args.gitlab_url, args.gitlab_project, report_path,
                                               args.gitlab_branch, token)
            with open(args.report, "rb") as f:
                if f.read() != remote:
                    ga.push_file_to_gitlab(args.report, args.gitlab_url, args.gitlab_project, report_path,
                                           args.gitlab_branch, token, "Update the Preservica ARK match report")
                    print(f"Pushed {args.report} to GitLab.")
        except RuntimeError as e:
            print(f"Warning: the report couldn't be pushed to GitLab: {e}", file=sys.stderr)

    if args.push_to_gitlab and not args.dry_run:
        if not summary["updated"]:
            print("resolve.csv unchanged; nothing to push.")
            return
        try:
            url = ga.push_file_to_gitlab(args.resolve, args.gitlab_url, args.gitlab_project, paths["resolve"],
                                         args.gitlab_branch, token,
                                         f"Add Preservica references for {summary['updated']} ARK(s)")
        except RuntimeError as e:
            sys.exit(f"GitLab push failed: {e}")
        print(f"Pushed {args.resolve} to GitLab: {url}")


if __name__ == "__main__":
    main()
