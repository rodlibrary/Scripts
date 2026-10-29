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
    url           optional  URL the ARK should resolve to
    type          optional  Resource type (e.g. Text, Image, Dataset)
    commitment    optional  Persistence commitment statement
    format        optional  File / media format (e.g. application/pdf)
    relation      optional  Related identifier or resource
    source        optional  Source / provenance citation
    dc_path       optional  Digital Commons path for the redirect manifest:
                            a landing-page key (etd/123, pias/vol94/iss4/3)
                            or a collection key (etd, pias/vol94/iss4)
    dc_article    optional  Digital Commons file key (etd/1124) -- the
                            article number from viewcontent.cgi, which
                            differs from the number in the landing URL
    preservica_ref optional Preservica reference, passed through as-is
    status        optional  Redirect status: active, withdrawn (served as
                            HTTP 410) or unmatched (ignored, kept for
                            tracking). If blank: "active" when url is
                            filled, otherwise "unmatched". "active"
                            requires url; any other value is an error.

OUTPUT
------
    <output>.csv   One row per input record with the generated ARK,
                   a resolvable https://n2t.net/... URL, and all metadata,
                   plus a status/notes column reporting any problems.
    <output>.anvl  ANVL-formatted metadata records (the format used by
                   ARK resolvers such as N2T/EZID) suitable for import.

REDIRECT MANIFEST (redirects.csv)
-----------------------------------
    Every successfully-minted ARK is also appended to redirects.csv
    (override with --redirects-manifest; disable with
    --no-redirects-manifest) with exactly these columns:

        dc_path, dc_article, ark, preservica_ref, target_url, status, notes

    "ark" is the bare NAAN/NAME form (e.g. 32498/s3abc...), without the
    "ark:/" prefix. "target_url" comes from the input "url" column. The
    file is append-only; a warning is printed if a dc_path/dc_article
    pair is already in it. With --push-to-gitlab both this file and the
    master list are pushed.

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

PUSHING THE MASTER LIST TO GITLAB
------------------------------------
    Pass --push-to-gitlab (with --gitlab-project namespace/project) to also
    commit the updated master list to a GitLab project after each run, via
    the GitLab Repository Files API (creates the file on first push,
    updates it on every push after that -- no external HTTP library
    required).

    --gitlab-project must name a specific PROJECT (repository), not a
    GitLab group -- a group can hold several projects, so the one to write
    to has to be named explicitly (default: rod-library/redirects, i.e.
    https://git.uni.edu/rod-library/redirects). --gitlab-url points at the
    GitLab instance itself (default: https://git.uni.edu).

    Requires a GitLab token (personal or project access token) with
    'api' scope for the project, supplied via an environment
    variable (default: GITLAB_TOKEN) -- never pass a token on the command
    line. Example:

        export GITLAB_TOKEN=glpat-...your_token...
        python3 generate_arks.py output --push-to-gitlab --gitlab-project rod-library/redirects

USAGE
-----
    The input CSV path is optional and defaults to arks.csv in the current
    directory; pass a different path as the first argument to override it.

    python3 generate_arks.py output
    python3 generate_arks.py output --add-checksum
    python3 generate_arks.py output --resolver https://n2t.net
    python3 generate_arks.py output --master-list /path/to/ark_master_list.csv
    python3 generate_arks.py output --push-to-gitlab --gitlab-project rod-library/redirects
    python3 generate_arks.py other_input.csv output   # override the input file

Run `python3 generate_arks.py -h` for all options.
"""

import argparse
import base64
import csv
import datetime
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

# Redirect manifest (redirects.csv): the append-only file consumed by the
# redirect tooling. Column order must match the manifest header exactly.
DEFAULT_REDIRECTS_MANIFEST = "redirects.csv"
REDIRECT_FIELDS = (
    "dc_path", "dc_article", "ark", "preservica_ref", "target_url",
    "status", "notes",
)
# active     -> redirect to target_url
# withdrawn  -> served as HTTP 410 (target_url may be blank)
# unmatched  -> ignored by the redirect service, kept for tracking
REDIRECT_STATUSES = ("active", "withdrawn", "unmatched")

REQUIRED_FIELDS = ("identifier", "title")
OPTIONAL_FIELDS = (
    "shoulder", "url", "type", "commitment", "format", "relation", "source",
)
ALL_FIELDS = (
    ("naan",) + OPTIONAL_FIELDS[:1] + ("identifier", "title") + OPTIONAL_FIELDS[1:]
    + ("dc_path", "dc_article", "preservica_ref")
)

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


def process_row(
    row_num: int,
    row: dict,
    resolver: str,
    add_checksum: bool,
    default_naan: str,
    used_blades: set,
    blade_length: int = 10,
) -> dict:
    notes = []
    routine_notes = []  # informational only; kept out of the redirect manifest
    row = {k: (v or "").strip() for k, v in row.items()}

    if not row.get("naan"):
        row["naan"] = default_naan
        notes.append(f"naan not supplied; defaulted to {default_naan}")
        routine_notes.append(notes[-1])

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

    # Redirect-manifest status. Optional in the input; when omitted it is
    # inferred: a row with a target URL is "active", otherwise "unmatched".
    redirect_status = row.get("status", "").lower()
    if not redirect_status:
        redirect_status = "active" if row.get("url") else "unmatched"
        notes.append(f"status not supplied; set to {redirect_status}")
        routine_notes.append(notes[-1])
    problem = None
    if redirect_status not in REDIRECT_STATUSES:
        problem = (
            f"invalid status '{redirect_status}' "
            f"(expected one of: {', '.join(REDIRECT_STATUSES)})"
        )
    elif redirect_status == "active" and not row.get("url"):
        problem = "status 'active' requires a url (target_url)"
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
        "manifest_notes": "; ".join(n for n in notes if n not in routine_notes),
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
        "dc_path": row.get("dc_path", ""),
        "dc_article": row.get("dc_article", ""),
        "preservica_ref": row.get("preservica_ref", ""),
        "redirect_status": redirect_status,
    }


def append_redirects_manifest(manifest_path: str, results: list[dict]) -> int:
    """
    Append one row per successfully-minted ARK to the redirect manifest
    (creating it, with a header, if needed). Append-only: existing rows
    are never rewritten. The `ark` column holds the bare "NAAN/NAME" form
    (no "ark:/" prefix). Warns on stderr if a dc_path/dc_article pair is
    already present, since one item should normally have one ARK.
    """
    new_rows = [r for r in results if r["status"] != "ERROR"]
    if not new_rows:
        return 0

    seen = set()
    file_exists = os.path.exists(manifest_path)
    if file_exists:
        with open(manifest_path, newline="", encoding="utf-8-sig") as f:
            for existing in csv.DictReader(f):
                seen.add((existing.get("dc_path", ""), existing.get("dc_article", "")))

    with open(manifest_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REDIRECT_FIELDS)
        if not file_exists:
            writer.writeheader()
        for r in new_rows:
            key = (r.get("dc_path", ""), r.get("dc_article", ""))
            if key != ("", "") and key in seen:
                print(
                    f"Warning: dc_path='{key[0]}' dc_article='{key[1]}' "
                    f"(row {r['row']}) is already in {manifest_path}; "
                    f"a second ARK was added.",
                    file=sys.stderr,
                )
            seen.add(key)
            writer.writerow({
                "dc_path": r.get("dc_path", ""),
                "dc_article": r.get("dc_article", ""),
                "ark": r["ark"].split("ark:/", 1)[1],
                "preservica_ref": r.get("preservica_ref", ""),
                "target_url": r.get("url", ""),
                "status": r["redirect_status"],
                "notes": r.get("manifest_notes", ""),
            })
    return len(new_rows)


def push_file_to_gitlab(
    file_path: str,
    gitlab_url: str,
    project: str,
    dest_path: str,
    branch: str,
    token: str,
    commit_message: str,
) -> str:
    """
    Create or update `dest_path` in the GitLab project `project` (a
    "namespace/project" or "group/subgroup/project" path, e.g.
    "rod-library/redirects") on `branch` with the current contents of
    `file_path`, via the GitLab Repository Files API (stdlib-only -- no
    external HTTP library required). `gitlab_url` is the GitLab instance's
    base URL (e.g. "https://git.uni.edu").

    A GitLab *group* URL (such as .../groups/redirects) is not itself a
    place files can be pushed to -- a group can hold several projects, so
    the caller must supply the specific project within it.

    Looks up whether the file already exists first, so this correctly
    updates an existing file (PUT) instead of trying to create one that's
    already there (which the GitLab API rejects).

    Returns the file's web URL on success. Raises RuntimeError with a
    readable message (never containing the token) on any failure.
    """
    project_id = urllib.parse.quote(project, safe="")
    file_id = urllib.parse.quote(dest_path, safe="")
    api_base = (
        f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_id}"
        f"/repository/files/{file_id}"
    )
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "generate_arks.py",
    }

    file_exists = False
    lookup_req = urllib.request.Request(
        f"{api_base}?ref={urllib.parse.quote(branch, safe='')}",
        headers=headers, method="GET",
    )
    try:
        with urllib.request.urlopen(lookup_req) as resp:
            resp.read()
            file_exists = True
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(
                f"lookup of existing file failed ({e.code}): "
                f"{e.read().decode('utf-8', 'ignore')}"
            ) from e
        # 404 just means the file doesn't exist in the project yet.

    with open(file_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    body = {
        "branch": branch,
        "content": content_b64,
        "encoding": "base64",
        "commit_message": commit_message,
    }

    req = urllib.request.Request(
        api_base,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="PUT" if file_exists else "POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"push failed ({e.code}): {e.read().decode('utf-8', 'ignore')}"
        ) from e

    return (
        f"{gitlab_url.rstrip('/')}/{project}/-/blob/{branch}/{dest_path}"
    )


def write_csv(results: list[dict], output_csv: str) -> None:
    fieldnames = [
        "row", "status", "ark", "resolvable_url", "naan", "shoulder",
        "ark_blade", "identifier", "title", "url", "type", "commitment",
        "format", "relation", "source", "dc_path", "dc_article",
        "preservica_ref", "redirect_status", "notes",
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
        lines.append(f"erc.where: {r.get('url') or r['resolvable_url']}")
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
        "--redirects-manifest",
        default=DEFAULT_REDIRECTS_MANIFEST,
        help=(
            "Path to the append-only redirect manifest (columns: "
            + ", ".join(REDIRECT_FIELDS)
            + f"). New ARKs are appended after each run. (default: "
            f"{DEFAULT_REDIRECTS_MANIFEST})"
        ),
    )
    parser.add_argument(
        "--no-redirects-manifest",
        action="store_true",
        help="Do not write or push the redirect manifest.",
    )
    parser.add_argument(
        "--gitlab-manifest-path",
        default=None,
        help=(
            "Path within the project for the redirect manifest (default: "
            "same filename as --redirects-manifest, at the project root)."
        ),
    )
    parser.add_argument(
        "--push-to-gitlab",
        action="store_true",
        help=(
            "After updating the master list, push it to a GitLab project "
            "via the GitLab Repository Files API. Requires --gitlab-project "
            "and a token (see --gitlab-token-env). Incompatible with "
            "--no-master-list."
        ),
    )
    parser.add_argument(
        "--gitlab-url",
        default="https://git.uni.edu",
        help="Base URL of the GitLab instance (default: https://git.uni.edu).",
    )
    parser.add_argument(
        "--gitlab-project",
        default="rod-library/redirects",
        help=(
            "Target project as 'namespace/project' (default: "
            "rod-library/redirects). Note this must be a specific project, "
            "not a group -- e.g. the 'rod-library' GROUP can hold several "
            "projects, so a project within it (such as 'rod-library/redirects') "
            "has to be named explicitly."
        ),
    )
    parser.add_argument(
        "--gitlab-path",
        default=None,
        help=(
            "Path within the project to write the master list to (default: "
            "same filename as --master-list, written to the project root)."
        ),
    )
    parser.add_argument(
        "--gitlab-branch",
        default="main",
        help=(
            "Branch to commit to (default: main -- some GitLab projects "
            "default to 'master' instead, so check yours if the push fails "
            "with a branch-not-found error)."
        ),
    )
    parser.add_argument(
        "--gitlab-token-env",
        default="GITLAB_TOKEN",
        help=(
            "Name of the environment variable holding a GitLab token "
            "(personal or project access token) with 'api' "
            "scope for the project (default: GITLAB_TOKEN). The token is "
            "read from the environment only -- never pass it as a "
            "command-line argument, where it could leak into shell history "
            "or process listings."
        ),
    )
    args = parser.parse_args()

    rows = read_rows(args.input_csv)
    if args.no_master_list:
        used_blades: set = set()
    else:
        used_blades = load_master_blades(args.master_list)

    results = [
        process_row(
            i, row, args.resolver, args.add_checksum, args.naan,
            used_blades, args.blade_length,
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
    if not args.no_redirects_manifest:
        manifest_appended = append_redirects_manifest(args.redirects_manifest, results)
        print(
            f"Appended {manifest_appended} row(s) to redirect manifest "
            f"{args.redirects_manifest}"
        )

    if args.push_to_gitlab:
        # (local file, destination path in project, commit message)
        to_push = []
        source_name = os.path.basename(args.input_csv)
        if not args.no_master_list:
            to_push.append((
                args.master_list,
                args.gitlab_path or os.path.basename(args.master_list),
                f"Update ARK master list ({appended} new from {source_name})",
            ))
        if not args.no_redirects_manifest:
            to_push.append((
                args.redirects_manifest,
                args.gitlab_manifest_path or os.path.basename(args.redirects_manifest),
                f"Update redirect manifest ({manifest_appended} new from {source_name})",
            ))

        token = os.environ.get(args.gitlab_token_env)
        if not to_push:
            print(
                "Skipped GitLab push: both the master list and the redirect "
                "manifest are disabled, so there is nothing to push.",
                file=sys.stderr,
            )
        elif not args.gitlab_project:
            print(
                "Skipped GitLab push: --gitlab-project is required with "
                "--push-to-gitlab.",
                file=sys.stderr,
            )
        elif not token:
            print(
                f"Skipped GitLab push: environment variable "
                f"{args.gitlab_token_env} is not set.",
                file=sys.stderr,
            )
        else:
            for local_path, dest_path, commit_message in to_push:
                try:
                    url = push_file_to_gitlab(
                        local_path, args.gitlab_url, args.gitlab_project,
                        dest_path, args.gitlab_branch, token, commit_message,
                    )
                    print(f"Pushed {local_path} to GitLab: {url}")
                except RuntimeError as e:
                    print(f"GitLab push of {local_path} failed: {e}", file=sys.stderr)

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
