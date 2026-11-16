#!/usr/bin/env python3
"""
generate_arks.py
=================

Batch-generate Archival Resource Keys (ARKs) from a CSV of source records.

An ARK has the form:

    ark:/NAAN/NAME

where NAME is normally built from an optional "shoulder" (a prefix that
marks a sub-namespace, policy, or minter) followed by a unique identifier
string ("blade"). This script does not register ARKs with a resolver
(e.g. N2T / EZID) -- it constructs well-formed ARK strings and matching
ANVL metadata records from data you already have, which you can then load
into whatever resolver / registry you use.

INPUT CSV COLUMNS
------------------
Column headers are case-insensitive and surrounding whitespace is ignored.

    naan          optional  Name Assigning Authority Number (digits only).
                            If omitted (or blank), defaults automatically
                            to 32498. Override per-run with --naan, or
                            per-row by filling in this column.
    shoulder      optional  ARK shoulder / prefix (e.g. "s3", "z9")
    identifier    REQUIRED  Local identifier for this object. Kept for
                            provenance/traceability in the output only --
                            it is NOT used to build the ARK string. Each
                            ARK's "blade" (the part after the shoulder) is
                            instead generated as a random, unique
                            alphanumeric string (see --blade-length),
                            independent of this value.
    title         REQUIRED  Title / short description of the resource
    url           optional  Where the ARK should resolve to: a full https://
                            address (its p_path in resolve.csv), or 410 for a
                            withdrawn item. Leave blank for Preservica items:
                            match_preservica.py adds them once the ARK is in
                            the asset's MODS.
    type          optional  Resource type (e.g. Text, Image, Dataset)
    commitment    optional  Persistence commitment statement
    format        optional  File / media format (e.g. application/pdf)
    relation      optional  Related identifier or resource
    source        optional  Source / provenance citation
    preservica_ref optional Preservica reference (IO_<uuid>), if already
                            known: with no url, the ARK resolves to the
                            asset's page in Preservica's portal.

    Old ScholarWorks addresses (dc_path, dc_article) aren't used here: they
    go in redirects.csv in github.com/rodlibrary/Redirects. A status column
    is no longer needed: 410 in url marks a withdrawn item.

OUTPUT
------
    <output>.csv   One row per input record with the generated ARK,
                   a resolvable https://n2t.net/... URL, and all metadata,
                   plus a status/notes column reporting any problems.
    <output>.anvl  ANVL-formatted metadata records (the format used by
                   ARK resolvers such as N2T/EZID) suitable for import.

RESOLVE LIST (resolve.csv)
---------------------------
    resolve.csv is the list Apache uses to resolve ARKs (override with
    --resolve-manifest; disable with --no-resolve-manifest). Like
    redirects.csv in github.com/rodlibrary/Redirects, it has two columns of
    full addresses, one row per ARK:

        ark      https://library.uni.edu/ark:/32498/s3x7k9q2mb   (--ark-base)
        p_path   https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C<uuid>
                 or 410 for a withdrawn item

    Only ARKs with somewhere to go are listed. A newly minted ARK is added
    when its row has a url (or a preservica_ref); otherwise it is recorded in
    the master list only, and match_preservica.py adds it once the ARK is in
    the asset's MODS in Preservica. Pass --match-preservica to run that
    matching in the same command. Existing rows are never changed.

    Every p_path must be a full https:// address on one of the allowed hosts
    (--allowed-host; default uni.access.preservica.com and
    digital.library.uni.edu), with characters such as | percent-encoded, as
    in redirects.csv. A row with a bad url is rejected before an ARK is
    minted for it. check_resolve.py checks a whole resolve.csv.

    A resolve.csv in the older seven-column layout (dc_path, dc_article,
    ark, preservica_ref, target_url, status, notes) is converted when it's
    read: active rows keep their target_url, withdrawn rows become 410, and
    unmatched rows are left out (their ARKs stay in the master list).

MASTER LIST (RECORDKEEPING & CROSS-RUN UNIQUENESS)
----------------------------------------------------
    In addition to the per-run outputs above, every successfully-generated
    ARK is appended to a persistent master list (default:
    ark_master_list.csv, override with --master-list). This file is never
    overwritten -- only appended to -- and serves two purposes:

      1. An ongoing, human-readable log of every ARK this script has ever
         minted, with a timestamp and the source CSV it came from.
      2. The source of truth for uniqueness: at startup, this script loads
         every blade already recorded in the master list so that a newly
         generated ARK can never collide with one minted in a previous
         run (even against a different input file, run months apart).

    Use --no-master-list to opt out (uniqueness is then only guaranteed
    within a single run).

PULLING FROM AND PUSHING TO GITHUB
------------------------------------
    Pass --push-to-github to commit the updated master list and resolve.csv
    to the GitHub resolver repository after each run, via the GitHub
    repository contents API (creates each file on first push, updates it
    after that -- no external HTTP library required). resolve.csv is only
    pushed if it passes the same checks as check_resolve.py.

    Before minting, --push-to-github first pulls the GitHub copies of both
    files and merges them into the local ones: rows already in GitHub are
    kept as they are, and local rows GitHub doesn't have yet are added. So a
    run never mints a blade someone else minted elsewhere, and never pushes
    an out-of-date copy over newer rows (such as ARKs added by
    match_preservica.py). Use --no-pull to skip this.

    --github-repo names the repository as owner/name (default:
    rodlibrary/Resolver, i.e. https://github.com/rodlibrary/Resolver).

    Requires a GitHub token that can write to that repository (a
    fine-grained personal access token with Contents: Read and write),
    supplied via an environment variable (default: GITHUB_TOKEN) -- never
    pass a token on the command line. Example:

        export GITHUB_TOKEN=github_pat_...your_token...
        python3 generate_arks.py output --push-to-github

    The resolver used to live in GitLab (git.uni.edu/rod-library/resolver).
    The old --push-to-gitlab and --gitlab-* options have been removed; the
    script stops with a message saying what to use instead.

USAGE
-----
    The input CSV path is optional and defaults to arks.csv in the current
    directory; pass a different path as the first argument to override it.

    python3 generate_arks.py output
    python3 generate_arks.py output --add-checksum
    python3 generate_arks.py output --resolver https://n2t.net
    python3 generate_arks.py output --master-list /path/to/ark_master_list.csv
    python3 generate_arks.py output --push-to-github
    python3 generate_arks.py output --push-to-github --match-preservica   # everything in one step
    python3 generate_arks.py other_input.csv output   # override the input file

Run `python3 generate_arks.py -h` for all options.
"""

import argparse
import base64
import csv
import datetime
import io
import json
import os
import re
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

# NAAN used automatically whenever a row doesn't supply its own.
DEFAULT_NAAN = "32498"

# Input CSV path used automatically when none is given on the command line.
DEFAULT_INPUT_CSV = "arks.csv"

# Persistent, append-only record of every ARK ever generated by this script
# (across all runs). Used both as a recordkeeping log and as the source of
# truth for uniqueness checking, so a blade generated last year can never
# be reissued today. Override with --master-list; disable with
# --no-master-list (not recommended outside of testing).
DEFAULT_MASTER_LIST = "ark_master_list.csv"
MASTER_LIST_FIELDS = (
    "timestamp", "ark", "ark_blade", "naan", "shoulder",
    "identifier", "title", "source_file", "row",
)

# Resolve list (resolve.csv): the file Apache uses to resolve ARKs. Two
# columns of full addresses, like redirects.csv in rodlibrary/Redirects.
DEFAULT_RESOLVE_MANIFEST = "resolve.csv"
RESOLVE_FIELDS = ("ark", "p_path")
# The layout resolve.csv had until November 2026; converted when read.
LEGACY_RESOLVE_FIELDS = (
    "dc_path", "dc_article", "ark", "preservica_ref", "target_url",
    "status", "notes",
)
# Where ARKs resolve: N2T sends ark:/32498/... to https://library.uni.edu/ark:/32498/...
DEFAULT_ARK_BASE = "https://library.uni.edu"
# Hosts a p_path may point to (the same as in rodlibrary/Redirects' settings.env).
DEFAULT_ALLOWED_TARGET_HOSTS = ("uni.access.preservica.com", "digital.library.uni.edu")
# p_path for a withdrawn item: Apache answers 410 Gone.
GONE = "410"
# A Preservica asset's page in the public portal; {ref} is its UUID.
DEFAULT_PORTAL_URL = "https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C{ref}"

# GitHub repository that receives resolve.csv and the master list.
DEFAULT_GITHUB_API_URL = "https://api.github.com"
DEFAULT_GITHUB_REPO = "rodlibrary/Resolver"
DEFAULT_GITHUB_BRANCH = "main"
DEFAULT_GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
REQUIRED_FIELDS = ("identifier", "title")
OPTIONAL_FIELDS = (
    "shoulder", "url", "type", "commitment", "format", "relation", "source",
)
ALL_FIELDS = (
    ("naan",) + OPTIONAL_FIELDS[:1] + ("identifier", "title") + OPTIONAL_FIELDS[1:]
    + ("preservica_ref",)
)
# Input columns that belonged to the older resolve.csv layout; ignored, with a note.
IGNORED_INPUT_FIELDS = ("dc_path", "dc_article", "status")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Characters allowed unencoded in a p_path (as in rodlibrary/Redirects' build_map.py).
_URL_CHARS_RE = re.compile(r"^(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+$")
_ARK_IN_TEXT_RE = re.compile(r"ark:/?(\d{5,})/([^\s?#]+)")

# Base-32 alphabet used by the NOID/ARK check-character algorithm.
_XDIGIT_ALPHABET = "0123456789bcdfghjkmnpqrstvwxyz"

# Alphabet used to generate the random ARK "blade" itself. Deliberately a
# plain lowercase-alphanumeric set (not the restricted NOID xdigit set)
# since the blade is generated fresh rather than derived from user data.
_BLADE_ALPHABET = string.ascii_lowercase + string.digits

# Characters that are not allowed to appear literally inside an ARK name
# (shoulder + identifier). Anything matching this is stripped out.
_ILLEGAL_ARK_CHARS = re.compile(r"[\s\"<>\^`{|}\\%#?]")


def normalize_header(name: str) -> str:
    return name.strip().lower()


def sanitize_ark_component(value: str) -> tuple[str, list[str]]:
    """Remove characters that must not appear in an ARK name; report changes."""
    notes = []
    cleaned = _ILLEGAL_ARK_CHARS.sub("", value.strip())
    cleaned = cleaned.strip("/")
    if cleaned != value.strip():
        notes.append(f"sanitized '{value}' -> '{cleaned}'")
    return cleaned, notes


def noid_checksum_char(identifier_body: str) -> str:
    """
    Compute the trailing ARK check character using the standard NOID
    algorithm: each character is weighted by its 1-based position; the
    character's value is its index in the xdigit alphabet, or its ASCII
    ordinal if it is not a member of that alphabet (this covers "/" and
    any punctuation). The checksum is (sum mod 32) mapped back through
    the alphabet.

    `identifier_body` should be the ARK content after "ark:" -- e.g.
    "13030/tf5p30086k" -- WITHOUT the check character itself.
    """
    total = 0
    for position, char in enumerate(identifier_body, start=1):
        value = _XDIGIT_ALPHABET.find(char)
        if value == -1:
            value = ord(char)
        total += value * position
    return _XDIGIT_ALPHABET[total % 32]


def generate_unique_blade(used_blades: set, length: int = 10) -> str:
    """
    Generate a random, unique, alphanumeric ARK blade using a
    cryptographically strong RNG (secrets), independent of any input
    identifier. `used_blades` is a set shared across the whole run so
    that no two rows can collide; on collision (astronomically unlikely
    for reasonable lengths) a new candidate is drawn.
    """
    while True:
        candidate = "".join(
            secrets.choice(_BLADE_ALPHABET) for _ in range(length)
        )
        if candidate not in used_blades:
            used_blades.add(candidate)
            return candidate


def build_ark(naan: str, shoulder: str, blade: str) -> str:
    name = f"{shoulder}{blade}"
    return f"ark:/{naan}/{name}"


def validate_naan(naan: str) -> list[str]:
    errors = []
    if not naan.isdigit():
        errors.append(f"NAAN '{naan}' should normally be numeric")
    return errors


def read_rows(input_path: str) -> list[dict]:
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [normalize_header(h) for h in reader.fieldnames]
        return list(reader)


def load_master_blades(master_list_path: str) -> set[str]:
    """
    Load every ARK blade already recorded in the master list, so that new
    runs -- even on a completely different input file, weeks or years
    later -- can never mint a blade that's already been issued.
    Returns an empty set if the master list doesn't exist yet.
    """
    blades: set[str] = set()
    if not os.path.exists(master_list_path):
        return blades
    with open(master_list_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return blades
        reader.fieldnames = [normalize_header(h) for h in reader.fieldnames]
        for row in reader:
            blade = (row.get("ark_blade") or "").strip()
            if blade:
                blades.add(blade)
    return blades


def append_master_list(
    master_list_path: str, results: list[dict], source_file: str
) -> int:
    """
    Append every successfully-generated ARK from this run to the master
    list (creating it, with a header, if it doesn't exist yet). This file
    is the durable, ongoing record of every ARK ever minted by this
    script and is never overwritten or truncated -- only appended to.
    Returns the number of rows appended.
    """
    new_rows = [r for r in results if r["status"] != "ERROR"]
    if not new_rows:
        return 0
    file_exists = os.path.exists(master_list_path)
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    end_with_line_break(master_list_path)    # else the first new row joins the last old one
    with open(master_list_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MASTER_LIST_FIELDS)
        if not file_exists:
            writer.writeheader()
        for r in new_rows:
            writer.writerow({
                "timestamp": timestamp,
                "ark": r["ark"],
                "ark_blade": r["ark_blade"],
                "naan": r["naan"],
                "shoulder": r["shoulder"],
                "identifier": r["identifier"],
                "title": r["title"],
                "source_file": source_file,
                "row": r["row"],
            })
    return len(new_rows)


def end_with_line_break(path: str) -> None:
    """Add a line break to the end of a file that lacks one (before appending to it)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) not in (b"\n", b"\r"):
            f.write(b"\r\n")


def process_row(
    row_num: int,
    row: dict,
    resolver: str,
    add_checksum: bool,
    default_naan: str,
    used_blades: set,
    blade_length: int = 10,
    allowed_hosts: "tuple[str, ...] | list[str]" = DEFAULT_ALLOWED_TARGET_HOSTS,
    portal_url: str = DEFAULT_PORTAL_URL,
) -> dict:
    notes = []
    row = {k: (v or "").strip() for k, v in row.items()}

    if not row.get("naan"):
        row["naan"] = default_naan
        notes.append(f"naan not supplied; defaulted to {default_naan}")

    missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
    if missing:
        return {
            "row": row_num,
            "status": "ERROR",
            "notes": f"missing required field(s): {', '.join(missing)}",
            "ark": "",
            "resolvable_url": "",
            **{f: row.get(f, "") for f in ALL_FIELDS},
        }

    # Where the ARK resolves (its p_path in resolve.csv): the url column, or
    # 410 for a withdrawn item, or the portal page of a known Preservica
    # reference. Blank: recorded in the master list only, until
    # match_preservica.py finds it in Preservica.
    for col in IGNORED_INPUT_FIELDS:
        if row.get(col) and not (col == "status" and row[col].lower() == "withdrawn"):
            notes.append(
                f"{col} ignored (old ScholarWorks addresses go in rodlibrary/Redirects' "
                "redirects.csv; 410 in url marks a withdrawn item)" if col != "status"
                else "status ignored (410 in url marks a withdrawn item)"
            )
    problem = None
    p_path = ""
    if (row.get("status") or "").lower() == "withdrawn" and not row.get("url"):
        p_path = GONE                     # older inputs: status withdrawn
    if row.get("url"):
        try:
            p_path = check_p_path(row["url"], allowed_hosts)
        except ValueError as e:
            problem = f"url: {e}"
    elif row.get("preservica_ref") and not p_path:
        ref = _UUID_RE.search(row["preservica_ref"])
        if ref and portal_url:
            p_path = portal_url.format(ref=ref.group(0).lower())
        elif not ref:
            problem = f"preservica_ref '{row['preservica_ref']}' holds no Preservica reference (IO_<uuid>)"
    if problem:
        # Rejected before minting so no ARK is consumed for a bad row.
        return {
            "row": row_num,
            "status": "ERROR",
            "notes": problem,
            "ark": "",
            "resolvable_url": "",
            **{f: row.get(f, "") for f in ALL_FIELDS},
        }

    notes.extend(validate_naan(row["naan"]))

    shoulder, sh_notes = sanitize_ark_component(row.get("shoulder", ""))
    identifier, id_notes = sanitize_ark_component(row["identifier"])
    notes.extend(sh_notes)
    notes.extend(id_notes)

    if not identifier:
        notes.append(
            "identifier was empty after sanitization "
            "(informational only -- identifier is not used in the ARK itself)"
        )

    # The ARK blade is generated as a random, unique alphanumeric string,
    # completely independent of the source identifier. The identifier is
    # still carried through to the output for provenance/traceability.
    blade = generate_unique_blade(used_blades, blade_length)

    ark = build_ark(row["naan"], shoulder, blade)

    if add_checksum:
        body = ark.split("ark:", 1)[1].lstrip("/")  # e.g. "naan/shouldername"
        check = noid_checksum_char(body)
        ark = f"{ark}{check}"

    ark_path = ark.split("ark:", 1)[1]
    resolvable_url = f"{resolver.rstrip('/')}/ark:{ark_path}"

    return {
        "row": row_num,
        "status": "OK" if not notes else "OK (with notes)",
        "notes": "; ".join(notes),
        "ark": ark,
        "resolvable_url": resolvable_url,
        "naan": row["naan"],
        "shoulder": shoulder,
        "ark_blade": blade,
        "identifier": identifier,
        "title": row["title"],
        "url": row.get("url", ""),
        "type": row.get("type", ""),
        "commitment": row.get("commitment", ""),
        "format": row.get("format", ""),
        "relation": row.get("relation", ""),
        "source": row.get("source", ""),
        "preservica_ref": row.get("preservica_ref", ""),
        "p_path": p_path,
    }


# --------------------------------------------------------------------------- resolve.csv

def ark_key(value: str, naan: str = "") -> str:
    """
    The comparable form of an ARK, "NAAN/name" in lower case without hyphens
    (they carry no meaning in ARKs), from ark:/32498/x7k9q, ark:32498/x7k9q,
    https://library.uni.edu/ark:/32498/x7k9q, https://n2t.net/ark:/32498/x7k9q
    or 32498/x7k9q. '' if there's no ARK, or it's under a different NAAN.
    """
    t = (value or "").strip()
    m = _ARK_IN_TEXT_RE.search(t) or re.fullmatch(r"(\d{5,})/([^\s?#/][^\s?#]*)", t)
    if not m or (naan and m.group(1) != naan):
        return ""
    name = m.group(2).rstrip("./").replace("-", "").lower()
    return f"{m.group(1)}/{name}" if name else ""


def ark_address(ark: str, base: str = DEFAULT_ARK_BASE) -> str:
    """resolve.csv's ark column for an ARK: https://library.uni.edu/ark:/32498/name."""
    m = _ARK_IN_TEXT_RE.search(ark or "") or re.fullmatch(r"(\d{5,})/(\S+)", (ark or "").strip())
    if not m:
        raise ValueError(f"'{ark}' isn't an ARK")
    return f"{base.rstrip('/')}/ark:/{m.group(1)}/{m.group(2).rstrip('/')}"


def check_p_path(value: str, allowed_hosts=DEFAULT_ALLOWED_TARGET_HOSTS) -> str:
    """
    A p_path as resolve.csv stores it, or ValueError saying what's wrong. The
    rules are those of redirects.csv in rodlibrary/Redirects: a full https://
    address on an allowed host with characters such as | percent-encoded, or
    410 (or "gone") for a withdrawn item.
    """
    v = (value or "").strip()
    if v.lower() in ("410", "gone"):
        return GONE
    if not v:
        raise ValueError("p_path is empty (use 410 for a withdrawn item)")
    if any(c.isspace() for c in v):
        raise ValueError("p_path contains a space")
    if not _URL_CHARS_RE.match(v):
        raise ValueError("p_path has characters that must be percent-encoded (e.g. | as %7C)")
    u = urllib.parse.urlparse(v)
    if u.scheme != "https" or not u.netloc:
        raise ValueError("p_path must be a full address starting with https://")
    hosts = [h.lower() for h in allowed_hosts or ()]
    if hosts and (u.hostname or "").lower() not in hosts:
        raise ValueError(f"p_path host '{u.hostname}' isn't one of {', '.join(hosts)}")
    return v


def _convert_legacy_rows(rows: list[dict], base: str) -> tuple[list[dict], list[str]]:
    """Rows of the older seven-column resolve.csv as ark/p_path rows."""
    out, dropped = [], []
    for r in rows:
        ark = (r.get("ark") or "").strip()
        if not ark:
            continue
        status = (r.get("status") or "").strip().lower()
        target = (r.get("target_url") or "").strip()
        if status == "withdrawn":
            p_path = GONE
        elif status in ("active", "") and target:
            p_path = target
        else:
            dropped.append(ark)       # unmatched: stays in the master list only
            continue
        try:
            out.append({"ark": ark_address(ark, base), "p_path": p_path})
        except ValueError:
            dropped.append(ark)
    return out, dropped


def parse_resolve(text: str, base: str = DEFAULT_ARK_BASE, source: str = "resolve.csv") -> list[dict]:
    """
    resolve.csv's rows as {"ark", "p_path"} dicts, from its text. A file in
    the older seven-column layout is converted (see the module docstring).
    """
    reader = csv.DictReader(text.splitlines())
    fields = [normalize_header(h) for h in (reader.fieldnames or [])]
    reader.fieldnames = fields
    rows = list(reader)
    if not fields:
        return []
    if "target_url" in fields:
        converted, dropped = _convert_legacy_rows(rows, base)
        print(
            f"{source} is in the older seven-column layout; converted it to ark, p_path "
            f"({len(converted)} row(s); {len(dropped)} unmatched ARK(s) left to the master list)."
        )
        return converted
    if not {"ark", "p_path"} <= set(fields):
        raise ValueError(f"{source} needs the columns ark and p_path; found {', '.join(fields)}")
    return [{"ark": (r.get("ark") or "").strip(), "p_path": (r.get("p_path") or "").strip()}
            for r in rows if any((v or "").strip() for v in r.values() if isinstance(v, str))]


def read_resolve(path: str, base: str = DEFAULT_ARK_BASE) -> list[dict]:
    """The rows of a local resolve.csv ([] if it doesn't exist)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return parse_resolve(f.read(), base, path)


def resolve_text(rows: list[dict]) -> str:
    """resolve.csv's text: header ark,p_path, one row per line, ending with a line break."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=RESOLVE_FIELDS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k) or "") for k in RESOLVE_FIELDS})
    return buf.getvalue()


def write_resolve(path: str, rows: list[dict]) -> None:
    tmp = path + ".partial"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.write(resolve_text(rows))
    os.replace(tmp, path)


def check_resolve(
    rows: list[dict],
    naan: str = DEFAULT_NAAN,
    base: str = DEFAULT_ARK_BASE,
    allowed_hosts=DEFAULT_ALLOWED_TARGET_HOSTS,
    text: "str | None" = None,
) -> list[str]:
    """
    Problems with resolve.csv's rows, each naming its line, by the rules
    redirects.csv follows in rodlibrary/Redirects: every ark a full address
    under `base` and our NAAN, listed once; every p_path a full https://
    address on an allowed host, or 410. With `text`, also that the file ends
    with a line break.
    """
    errors, seen = [], {}
    prefix = f"{base.rstrip('/')}/ark:/"
    for line, r in enumerate(rows, start=2):
        ark = r.get("ark", "")
        key = ark_key(ark, naan)
        if not ark.startswith(prefix):
            errors.append(f"line {line}: ark '{ark}' should be a full address like {prefix}{naan}/x7k9q2mbd")
        elif not key:
            errors.append(f"line {line}: ark '{ark}' isn't under our NAAN {naan}")
        if key and key in seen:
            errors.append(f"line {line}: {ark} is already on line {seen[key]}")
        elif key:
            seen[key] = line
        try:
            check_p_path(r.get("p_path", ""), allowed_hosts)
        except ValueError as e:
            errors.append(f"line {line}: {e}")
    if text is not None and text and not text.endswith(("\n", "\r")):
        errors.append("the file doesn't end with a line break")
    return errors


def add_to_resolve(path: str, results: list[dict], base: str = DEFAULT_ARK_BASE) -> int:
    """
    Add a row to resolve.csv for every minted ARK that has a p_path. Existing
    rows are kept as they are. Returns the number of rows added.
    """
    rows = read_resolve(path, base)
    keys = {ark_key(r["ark"]) for r in rows}
    added = 0
    for r in results:
        if r["status"] == "ERROR" or not r.get("p_path"):
            continue
        address = ark_address(r["ark"], base)
        if ark_key(address) in keys:
            continue
        rows.append({"ark": address, "p_path": r["p_path"]})
        keys.add(ark_key(address))
        added += 1
    if added or read_resolve_raw(path) is None:   # new rows, or a file to create or convert
        write_resolve(path, rows)
    return added


def read_resolve_raw(path: str) -> "list[dict] | None":
    """
    resolve.csv's rows exactly as stored, or None if the file needs writing
    afresh: it doesn't exist, isn't ark,p_path yet, or doesn't end with a line break.
    """
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        text = f.read()
    if text and not text.endswith(("\n", "\r")):
        return None
    reader = csv.DictReader(text.splitlines())
    if [normalize_header(h) for h in (reader.fieldnames or [])] != list(RESOLVE_FIELDS):
        return None
    return [{"ark": (r.get("ark") or "").strip(), "p_path": (r.get("p_path") or "").strip()} for r in reader]


def merge_resolve(local_path: str, remote: "bytes | None", base: str = DEFAULT_ARK_BASE) -> tuple[int, int]:
    """
    Merge GitHub's resolve.csv into the local one: every GitHub row is kept as
    it is, and local rows for ARKs GitHub doesn't have are added after them.
    Returns (rows from GitHub, local rows added). With no GitHub copy the local
    file is left alone.
    """
    if remote is None:
        return 0, 0
    remote_rows = parse_resolve(remote.decode("utf-8-sig"), base, "GitHub's resolve.csv")
    local_rows = read_resolve(local_path, base)
    seen = {ark_key(r["ark"]) for r in remote_rows}
    extra = [r for r in local_rows if ark_key(r["ark"]) and ark_key(r["ark"]) not in seen]
    write_resolve(local_path, remote_rows + extra)
    return len(remote_rows), len(extra)


# Options the scripts had while the resolver lived in GitLab. They now stop
# the script with a pointer to the GitHub equivalent instead of being ignored,
# so an old command or pipeline can't quietly do nothing.
REMOVED_GITLAB_OPTIONS = {
    "--push-to-gitlab": "--push-to-github",
    "--from-gitlab": "--from-github",
    "--gitlab-project": "--github-repo",
    "--gitlab-branch": "--github-branch",
    "--gitlab-token-env": "--github-token-env (default GITHUB_TOKEN)",
    "--gitlab-path": "--github-path",
    "--gitlab-manifest-path": "--github-manifest-path",
    "--gitlab-master-path": "--github-master-path",
    "--gitlab-report-path": "--github-report-path",
    "--gitlab-url": "--github-api-url (rarely needed)",
}


def reject_gitlab_options(argv: "list[str] | None" = None) -> None:
    """Stop with a clear message if a removed GitLab option is used."""
    used = []
    for arg in (sys.argv[1:] if argv is None else argv):
        name = arg.split("=", 1)[0]
        if name in REMOVED_GITLAB_OPTIONS and name not in used:
            used.append(name)
    if used:
        sys.exit(
            "The ARK resolver moved from GitLab to GitHub "
            f"(https://github.com/{DEFAULT_GITHUB_REPO}), so these options are gone:\n"
            + "".join(f"  {u}  ->  use {REMOVED_GITLAB_OPTIONS[u]}\n" for u in used)
            + "Set GITHUB_TOKEN to a GitHub token with Contents: Read and write on the "
            "repository. See arks/README.md in rodlibrary/Scripts."
        )


def _github_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rodlibrary-ark-scripts",
    }


def _github_contents_url(api_url: str, repo: str, path: str) -> str:
    return (
        f"{api_url.rstrip('/')}/repos/{repo.strip('/')}/contents/"
        f"{urllib.parse.quote(path.lstrip('/'), safe='/')}"
    )


def _github_error(e: urllib.error.HTTPError) -> str:
    """GitHub's error message from an HTTPError, short and without the token."""
    raw = e.read().decode("utf-8", "ignore")
    try:
        return str(json.loads(raw).get("message") or raw)[:300]
    except (ValueError, AttributeError):
        return raw[:300]


def _github_token_hint(repo: str) -> str:
    return (
        f" -- the token can't see or write to {repo}. Use a fine-grained personal "
        f"access token whose resource owner is {repo.split('/')[0]}, with access to "
        f"{repo} and the permission Contents: Read and write (and check it hasn't "
        "expired). check_github.py tests this."
    )


def fetch_file_from_github(
    api_url: str, repo: str, path: str, branch: str, token: str,
) -> "bytes | None":
    """
    The contents of `path` in the GitHub repository `repo` (owner/name) on
    `branch`, or None if the file doesn't exist there yet. Raises
    RuntimeError -- with a readable message that never contains the token --
    if the repository or branch can't be found, or on any other failure.
    """
    url = f"{_github_contents_url(api_url, repo, path)}?ref={urllib.parse.quote(branch, safe='')}"
    req = urllib.request.Request(
        url, headers=_github_headers(token, "application/vnd.github.raw+json"), method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        message = _github_error(e)
        if e.code == 404:
            if "no commit found" in message.lower():
                raise RuntimeError(f"{repo} has no branch {branch!r}") from e
            # GitHub answers 404 both for a missing file and for a repository
            # the token can't see, so tell the two apart before saying "new file".
            check = urllib.request.Request(
                f"{api_url.rstrip('/')}/repos/{repo.strip('/')}",
                headers=_github_headers(token), method="GET",
            )
            try:
                with urllib.request.urlopen(check, timeout=60) as resp:
                    resp.read()
            except urllib.error.HTTPError as e2:
                raise RuntimeError(
                    f"{repo} not found ({e2.code}){_github_token_hint(repo)}"
                ) from e2
            return None
        hint = _github_token_hint(repo) if e.code in (401, 403) else ""
        raise RuntimeError(f"download of {path} failed ({e.code}): {message}{hint}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach {api_url}: {e.reason}") from e


def _csv_rows(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(text.splitlines())
    return list(reader.fieldnames or []), list(reader)


def merge_into_local(local_path: str, remote: "bytes | None", key: str = "ark") -> tuple[int, int]:
    """
    Merge the GitHub copy of a CSV into the local file: every GitHub row is
    kept as it is (GitHub may hold newer values, such as a Preservica
    reference), and local rows whose `key` GitHub doesn't have are added
    after them. Returns (rows from GitHub, local rows added). With no GitHub
    copy the local file is left alone.
    """
    if remote is None:
        return 0, 0
    remote_text = remote.decode("utf-8-sig")
    remote_fields, remote_rows = _csv_rows(remote_text)
    local_fields, local_rows = [], []
    if os.path.exists(local_path):
        with open(local_path, newline="", encoding="utf-8-sig") as f:
            local_fields, local_rows = _csv_rows(f.read())
    seen = {(r.get(key) or "").strip() for r in remote_rows}
    extra = [r for r in local_rows if (r.get(key) or "").strip() and (r.get(key) or "").strip() not in seen]
    fields = remote_fields + [f for f in local_fields if f not in remote_fields]
    if not fields:
        return 0, 0
    with open(local_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in remote_rows + extra:
            writer.writerow({k: r.get(k, "") or "" for k in fields})
    return len(remote_rows), len(extra)


def push_file_to_github(
    file_path: str,
    api_url: str,
    repo: str,
    dest_path: str,
    branch: str,
    token: str,
    commit_message: str,
) -> str:
    """
    Create or update `dest_path` in the GitHub repository `repo` (owner/name,
    e.g. "rodlibrary/Resolver") on `branch` with the current contents of
    `file_path`, via the GitHub repository contents API (stdlib only).

    GitHub needs the current blob SHA to update an existing file, so this
    looks it up first; a file that isn't there yet is created.

    Returns the file's web URL on success. Raises RuntimeError with a
    readable message (never containing the token) on any failure.
    """
    url = _github_contents_url(api_url, repo, dest_path)
    sha = None
    lookup = urllib.request.Request(
        f"{url}?ref={urllib.parse.quote(branch, safe='')}",
        headers=_github_headers(token), method="GET",
    )
    try:
        with urllib.request.urlopen(lookup, timeout=120) as resp:
            info = json.loads(resp.read() or b"{}")
            sha = info.get("sha") if isinstance(info, dict) else None
    except urllib.error.HTTPError as e:
        message = _github_error(e)
        if e.code != 404 or "no commit found" in message.lower():
            hint = _github_token_hint(repo) if e.code in (401, 403) else ""
            raise RuntimeError(f"lookup of {dest_path} failed ({e.code}): {message}{hint}") from e
        # 404 just means the file doesn't exist in the repository yet.
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach {api_url}: {e.reason}") from e

    with open(file_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")
    body = {"message": commit_message, "content": content_b64, "branch": branch}
    if sha:
        body["sha"] = sha

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(token), "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        message = _github_error(e)
        hint = ""
        if e.code in (401, 403, 404):
            hint = _github_token_hint(repo)
            if "protected" in message.lower() or "rule" in message.lower():
                hint = (f" -- a branch protection rule or ruleset on {branch} blocks direct "
                        "pushes. Allow the token's account to bypass it, or remove the rule.")
        elif e.code in (409, 422):
            hint = (f" -- {dest_path} changed in {repo} while this script was running. "
                    "Run the script again: it pulls the newer copy first.")
        raise RuntimeError(f"push failed ({e.code}): {message}{hint}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach {api_url}: {e.reason}") from e

    html = ((result.get("content") or {}).get("html_url")) if isinstance(result, dict) else None
    return html or f"https://github.com/{repo.strip('/')}/blob/{branch}/{dest_path.lstrip('/')}"


def write_csv(results: list[dict], output_csv: str) -> None:
    fieldnames = [
        "row", "status", "ark", "resolvable_url", "naan", "shoulder",
        "ark_blade", "identifier", "title", "url", "type", "commitment",
        "format", "relation", "source", "preservica_ref", "p_path", "notes",
    ]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def write_anvl(results: list[dict], output_anvl: str) -> None:
    today = datetime.date.today().isoformat()
    lines = []
    for r in results:
        if r["status"] == "ERROR":
            continue
        lines.append(f":: {r['ark']}")
        lines.append(f"erc.who: {r.get('source') or 'unknown'}")
        lines.append(f"erc.what: {r['title']}")
        lines.append(f"erc.when: {today}")
        where = r.get("p_path") if r.get("p_path") not in ("", GONE) else r["resolvable_url"]
        lines.append(f"erc.where: {where}")
        if r.get("type"):
            lines.append(f"dc.type: {r['type']}")
        if r.get("format"):
            lines.append(f"dc.format: {r['format']}")
        if r.get("relation"):
            lines.append(f"dc.relation: {r['relation']}")
        if r.get("commitment"):
            lines.append(f"_target: {r.get('url', '')}")
            lines.append(f"commitment: {r['commitment']}")
        lines.append("")  # blank line between records
    with open(output_anvl, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


# Options for files that are no longer written. They're accepted and ignored,
# with a note, so an older command still mints.
REMOVED_OPTIONS = {
    "--slim-manifest": ("resolve.csv now has just ark and p_path, so resolve_paths.csv "
                        "is no longer written", True),
    "--no-slim-manifest": ("resolve_paths.csv is no longer written", False),
    "--redirects-manifest": ("use --resolve-manifest", True),
    "--no-redirects-manifest": ("use --no-resolve-manifest", False),
}


def drop_removed_options(argv: "list[str] | None" = None) -> list[str]:
    """The command line without the options in REMOVED_OPTIONS (noting each one)."""
    args = list(sys.argv[1:] if argv is None else argv)
    out, i = [], 0
    while i < len(args):
        name = args[i].split("=", 1)[0]
        if name in REMOVED_OPTIONS:
            why, takes_value = REMOVED_OPTIONS[name]
            if name in ("--redirects-manifest", "--no-redirects-manifest"):
                out.append(args[i].replace(name, name.replace("redirects", "resolve"), 1))
                if takes_value and "=" not in args[i] and i + 1 < len(args):
                    out.append(args[i + 1])
            print(f"Note: {name} {'is the old name: ' if 'redirects' in name else 'ignored: '}{why}.")
            i += 2 if takes_value and "=" not in args[i] else 1
            continue
        out.append(args[i])
        i += 1
    return out


def allowed_hosts(given: "list[str] | None" = None) -> tuple[str, ...]:
    """Hosts a p_path may point to: --allowed-host, else ALLOWED_TARGET_HOSTS, else the defaults."""
    if given:
        return tuple(h.strip().lower() for h in given if h.strip())
    env = os.environ.get("ALLOWED_TARGET_HOSTS", "")
    hosts = tuple(h.strip().lower() for h in env.split(",") if h.strip())
    return hosts or DEFAULT_ALLOWED_TARGET_HOSTS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-generate ARKs and ANVL metadata from a CSV file."
    )
    parser.add_argument(
        "input_csv",
        nargs="?",
        default=DEFAULT_INPUT_CSV,
        help=f"Path to the input CSV file (default: {DEFAULT_INPUT_CSV})",
    )
    parser.add_argument(
        "output_basename",
        help="Base path/name for outputs (produces <name>.csv and <name>.anvl)",
    )
    parser.add_argument(
        "--resolver",
        default="https://n2t.net",
        help="Resolver base URL used to build resolvable links (default: https://n2t.net)",
    )
    parser.add_argument(
        "--naan",
        default=DEFAULT_NAAN,
        help=(
            f"NAAN to use automatically for any row that doesn't supply its "
            f"own 'naan' value (default: {DEFAULT_NAAN})"
        ),
    )
    parser.add_argument(
        "--add-checksum",
        action="store_true",
        help=(
            "Append a NOID-style trailing check character to each ARK. "
            "Uses the standard NOID checksum algorithm; verify against your "
            "resolver/minter if exact compatibility is required."
        ),
    )
    parser.add_argument(
        "--blade-length",
        type=int,
        default=10,
        help=(
            "Length of the randomly generated alphanumeric ARK blade "
            "(the unique part after the shoulder). Default: 10."
        ),
    )
    parser.add_argument(
        "--master-list",
        default=DEFAULT_MASTER_LIST,
        help=(
            "Path to the persistent, append-only master list of every ARK "
            "ever generated by this script. Loaded at startup so new "
            f"blades can never collide with past runs, and appended to "
            f"after this run completes. (default: {DEFAULT_MASTER_LIST})"
        ),
    )
    parser.add_argument(
        "--no-master-list",
        action="store_true",
        help=(
            "Skip loading and updating the master list. Uniqueness is then "
            "only guaranteed within this run, not across runs. Not "
            "recommended for real recordkeeping -- mainly useful for tests."
        ),
    )
    parser.add_argument(
        "--resolve-manifest",
        dest="resolve_manifest",
        default=DEFAULT_RESOLVE_MANIFEST,
        help=(
            "Path to resolve.csv, the list Apache uses (columns: "
            + ", ".join(RESOLVE_FIELDS)
            + f"). Minted ARKs with a url are added. (default: {DEFAULT_RESOLVE_MANIFEST})"
        ),
    )
    parser.add_argument(
        "--no-resolve-manifest",
        dest="no_resolve_manifest",
        action="store_true",
        help="Do not write or push resolve.csv.",
    )
    parser.add_argument(
        "--ark-base",
        default=os.environ.get("ARK_BASE_URL") or DEFAULT_ARK_BASE,
        help=f"Address ARKs resolve at, for resolve.csv's ark column (default: {DEFAULT_ARK_BASE}).",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        help=(
            "Host a url (p_path) may point to; repeat for several (default: "
            + ", ".join(DEFAULT_ALLOWED_TARGET_HOSTS) + ", or ALLOWED_TARGET_HOSTS)."
        ),
    )
    parser.add_argument(
        "--push-to-github",
        action="store_true",
        help=(
            "Pull the GitHub copies of the master list and resolve manifest "
            "and merge them into the local files before minting, then push "
            "both back after the run. Requires a token (see --github-token-env)."
        ),
    )
    parser.add_argument(
        "--github-repo",
        default=os.environ.get("RESOLVER_REPO") or DEFAULT_GITHUB_REPO,
        help=f"Resolver repository as owner/name (default: {DEFAULT_GITHUB_REPO}).",
    )
    parser.add_argument(
        "--github-branch",
        default=os.environ.get("RESOLVER_BRANCH") or DEFAULT_GITHUB_BRANCH,
        help=f"Branch to pull from and commit to (default: {DEFAULT_GITHUB_BRANCH}).",
    )
    parser.add_argument(
        "--github-path",
        default=None,
        help=(
            "Path within the repository for the master list (default: same "
            "filename as --master-list, at the repository root)."
        ),
    )
    parser.add_argument(
        "--github-manifest-path",
        default=None,
        help=(
            "Path within the repository for the resolve manifest (default: "
            "same filename as --resolve-manifest, at the repository root)."
        ),
    )
    parser.add_argument(
        "--github-token-env",
        default=DEFAULT_GITHUB_TOKEN_ENV,
        help=(
            "Name of the environment variable holding a GitHub token with "
            "Contents: Read and write on the repository (default: "
            f"{DEFAULT_GITHUB_TOKEN_ENV}). The token is read from the environment "
            "only -- never pass it as a command-line argument, where it could "
            "leak into shell history or process listings."
        ),
    )
    parser.add_argument(
        "--github-api-url",
        default=DEFAULT_GITHUB_API_URL,
        help=f"GitHub API base URL (default: {DEFAULT_GITHUB_API_URL}).",
    )
    parser.add_argument(
        "--no-pull",
        action="store_true",
        help=(
            "With --push-to-github, don't pull and merge the GitHub copies "
            "before minting (not recommended: the push then overwrites them "
            "with the local files)."
        ),
    )
    parser.add_argument(
        "--match-preservica",
        action="store_true",
        help=(
            "After minting, run match_preservica.py: read each Preservica "
            "asset's MODS Archival Resource Key and Location (URL) and add "
            "the ARKs found to resolve.csv, pointing at the asset's portal "
            "page. Needs the Preservica login (PRESERVICA_* environment "
            "variables or credentials.properties)."
        ),
    )
    reject_gitlab_options()
    args = parser.parse_args(drop_removed_options())
    args.allowed_hosts = allowed_hosts(args.allowed_host)

    github_paths = {
        "master": args.github_path or os.path.basename(args.master_list),
        "resolve": args.github_manifest_path or os.path.basename(args.resolve_manifest),
    }
    token = (os.environ.get(args.github_token_env) or "").strip()

    # Bring in what GitHub already has before minting, so blades stay unique
    # across computers and the push never overwrites newer rows.
    if args.push_to_github and not args.no_pull:
        if not token:
            sys.exit(
                f"{args.github_token_env} is not set, so the GitHub copies can't be pulled "
                f"and nothing was minted. Set it (export {args.github_token_env}=github_pat_...), "
                "or leave off --push-to-github to mint with the local files only."
            )
        pulls = []
        if not args.no_master_list:
            pulls.append((args.master_list, github_paths["master"]))
        if not args.no_resolve_manifest:
            pulls.append((args.resolve_manifest, github_paths["resolve"]))
        for local_path, dest_path in pulls:
            try:
                remote = fetch_file_from_github(
                    args.github_api_url, args.github_repo, dest_path,
                    args.github_branch, token,
                )
            except RuntimeError as e:
                sys.exit(
                    f"Couldn't pull {dest_path} from GitHub, so nothing was minted: {e}\n"
                    "Check the token and --github-repo, or use --no-pull to run "
                    "with the local files only."
                )
            if local_path == args.resolve_manifest:
                from_github, local_only = merge_resolve(local_path, remote, args.ark_base)
            else:
                from_github, local_only = merge_into_local(local_path, remote)
            if remote is None:
                print(f"{dest_path} isn't in {args.github_repo} yet; it will be created by the push.")
            else:
                print(
                    f"Pulled {dest_path} from GitHub ({from_github} row(s)); "
                    f"{local_only} local row(s) not yet in GitHub kept."
                )

    # resolve.csv must pass the checks before anything is minted into it, or
    # the push at the end would be refused with new ARKs already issued.
    if args.push_to_github and not args.no_resolve_manifest and os.path.exists(args.resolve_manifest):
        try:
            problems = check_resolve(read_resolve(args.resolve_manifest, args.ark_base),
                                     args.naan, args.ark_base, args.allowed_hosts)
        except ValueError as e:
            problems = [str(e)]
        if problems:
            sys.exit(
                f"{args.resolve_manifest} has {len(problems)} problem(s), so nothing was minted:\n"
                + "".join(f"  {e}\n" for e in problems[:20])
                + "Fix them in the resolver repository (check_resolve.py --from-github lists them), "
                "then run again."
            )

    rows = read_rows(args.input_csv)
    if args.no_master_list:
        used_blades: set = set()
    else:
        used_blades = load_master_blades(args.master_list)

    results = [
        process_row(
            i, row, args.resolver, args.add_checksum, args.naan,
            used_blades, args.blade_length, args.allowed_hosts,
        )
        for i, row in enumerate(rows, start=2)  # row 1 is the header
    ]

    output_csv = f"{args.output_basename}.csv"
    output_anvl = f"{args.output_basename}.anvl"
    write_csv(results, output_csv)
    write_anvl(results, output_anvl)

    appended = 0
    if not args.no_master_list:
        appended = append_master_list(args.master_list, results, args.input_csv)
        print(f"Recorded {appended} new ARK(s) in master list {args.master_list}")

    manifest_appended = 0
    if not args.no_resolve_manifest:
        manifest_appended = add_to_resolve(args.resolve_manifest, results, args.ark_base)
        waiting = sum(1 for r in results if r["status"] != "ERROR" and not r.get("p_path"))
        print(
            f"Added {manifest_appended} ARK(s) to {args.resolve_manifest}"
            + (f"; {waiting} with no url wait for match_preservica.py" if waiting else "")
        )

    matched = 0
    if args.match_preservica and not args.no_resolve_manifest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import match_preservica
        summary = match_preservica.run(
            args.resolve_manifest,
            None if args.no_master_list else args.master_list,
            naan=args.naan, ark_base=args.ark_base, allowed_hosts=args.allowed_hosts,
        )
        matched = summary.get("updated", 0)

    resolve_errors = []
    if not args.no_resolve_manifest and os.path.exists(args.resolve_manifest):
        resolve_errors = check_resolve(read_resolve(args.resolve_manifest, args.ark_base),
                                       args.naan, args.ark_base, args.allowed_hosts)
        for e in resolve_errors[:20]:
            print(f"  {args.resolve_manifest} {e}", file=sys.stderr)

    if args.push_to_github:
        # (local file, destination path in the repository, commit message)
        to_push = []
        source_name = os.path.basename(args.input_csv)
        if not args.no_master_list:
            to_push.append((
                args.master_list,
                github_paths["master"],
                f"Update ARK master list ({appended} new from {source_name})",
            ))
        if not args.no_resolve_manifest:
            to_push.append((
                args.resolve_manifest,
                github_paths["resolve"],
                f"Update resolve.csv ({manifest_appended} new from {source_name}"
                + (f", {matched} matched in Preservica" if matched else "") + ")",
            ))
        if resolve_errors:
            to_push = [t for t in to_push if t[0] != args.resolve_manifest]
            print(
                f"Not pushing {args.resolve_manifest}: it has {len(resolve_errors)} problem(s), "
                "listed above. Fix them (python3 check_resolve.py lists them too) and run again.",
                file=sys.stderr,
            )

        if not to_push:
            print(
                "Skipped GitHub push: there is nothing to push.",
                file=sys.stderr,
            )
        elif not args.github_repo:
            print(
                "Skipped GitHub push: --github-repo is required with "
                "--push-to-github.",
                file=sys.stderr,
            )
        elif not token:
            print(
                f"Skipped GitHub push: environment variable "
                f"{args.github_token_env} is not set.",
                file=sys.stderr,
            )
        else:
            for local_path, dest_path, commit_message in to_push:
                try:
                    url = push_file_to_github(
                        local_path, args.github_api_url, args.github_repo,
                        dest_path, args.github_branch, token, commit_message,
                    )
                    print(f"Pushed {local_path} to GitHub: {url}")
                except RuntimeError as e:
                    print(f"GitHub push of {local_path} failed: {e}", file=sys.stderr)

    ok = sum(1 for r in results if r["status"].startswith("OK"))
    errors = sum(1 for r in results if r["status"] == "ERROR")
    print(f"Processed {len(results)} row(s): {ok} OK, {errors} error(s).")
    print(f"Wrote {output_csv}")
    print(f"Wrote {output_anvl}")
    if errors:
        print("Rows with errors (see 'notes' column in the output CSV):")
        for r in results:
            if r["status"] == "ERROR":
                print(f"  row {r['row']}: {r['notes']}")


if __name__ == "__main__":
    main()
