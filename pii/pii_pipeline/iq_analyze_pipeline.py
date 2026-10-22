"""
IQ branch of the PII analysis pipeline.

Runs analyze_pipeline.py (which must be in the same folder) with the field
layout taken from the IQ field policy (iq_field_policy.csv), so that:

  * fields set to `scan` or `keep` are scanned with Presidio - including
    fields the general pipeline skips, such as Middle Name or City;
  * fields set to `redact` or `keep_all` are NOT scanned (the anonymizer
    handles them without needing findings), which also saves time.

Everything else - PDFs, images, emails, CSVs, chunking, parallelism, the
results/error CSVs - works exactly as in analyze_pipeline.py, and all of
its options are accepted. Two extra files are written next to the results:

    pii_results_<job_id>_policy.csv   copy of the policy used, so the
                                      anonymizer can tell which fields
                                      were scanned
    pii_results_<job_id>_exports.csv  the IQ export files found (so they
                                      are anonymized even with no findings)

Usage:
    python iq_analyze_pipeline.py --input_dir /data/... [--policy iq_field_policy.csv]
                                  [any analyze_pipeline.py option]
    python iq_analyze_pipeline.py --write_default_policy iq_field_policy.csv
"""

import argparse
import csv
import multiprocessing as mp
import os
import sys

import analyze_pipeline as ap
import iq_policy

T, ID, N = ap.T, ap.ID, ap.N

# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------

# Field names (any record type) and their default action. Anything not
# matched here falls back to the rules in default_action() below.
_REDACT = {
    "First Name", "Middle Name", "Last Name", "Spouse Name", "Salutation",
    "Date of Birth", "Address Line 1", "Address Line 2", "Address Line 3",
    "Address Line 4", "Zip Code", "Email Address", "Household Name",
    "Household Salutation", "Contact",
    # IQ exports can put "The Honorable <full name>" here, not just "MD".
    "Appellation",
}
_KEEP = {
    "Prefix", "Suffix", "City", "County", "State Code",
    "Country", "District", "Precinct", "Carrier Route",
}
# Names of custom fields / letter fill-ins (e.g. "SSN"), not their values.
_KEEP_ALL = {"Fill-in Field Name", "Field Name"}
_SCAN = {"Organization Name", "Organization", "Title"}

# Record types whose address lines are event venues rather than home addresses.
_EVENT_TYPES = {"7A", "7I"}


def default_action(record_type, name, role):
    if name == "Record Type":
        return "keep_all", "Identifies the record type; never changed."
    if record_type == "1E" and role == "contact":
        return "redact", "Phone number, email address or web page of the person."
    if record_type in _EVENT_TYPES and (name.startswith("Address Line") or name == "Zip Code"):
        return "keep", ("Event venue address (usually public). Presidio does not detect "
                        "street addresses, so set to redact if venues may be homes.")
    if name in _KEEP_ALL:
        return "keep_all", "Name of a custom field or letter fill-in, not its value."
    if name in _REDACT:
        return "redact", "Directly identifies a person; replaced in full."
    if name in _KEEP:
        return "keep", "Kept for research value; ID numbers still removed."
    if name in _SCAN:
        return "scan", "Detected PII only."
    if role in (T, "kv_value", "code_desc"):
        return "scan", "Free text; detected PII only."
    if role and role.startswith("known:"):
        return "redact", "Known PII field; replaced in full."
    if role == ID:
        return "keep_all", "Internal ID or type code; needed to link records."
    return "keep_all", "Code, flag, date, status or staff user ID."


def _label(name):
    return "<" + ap._slug(name).upper() + ">"


def default_policy_rows():
    rows = []

    def add(rt, layout):
        for i, (name, role) in enumerate(layout, start=1):
            action, why = default_action(rt, name, role)
            rows.append({"record_type": rt, "field_no": i, "field_name": name,
                         "action": action,
                         "label": _label(name) if action == "redact" else "",
                         "notes": why})

    for rt in sorted(ap.SCDIF_SCHEMA):
        add(rt, ap.SCDIF_SCHEMA[rt])
    add(iq_policy.ARCHIVE, ap.ARCHIVE_FORMAT_FIELDS)
    rows.append({"record_type": "*", "field_no": "*",
                 "field_name": "(any field or record type not listed above)",
                 "action": "scan", "label": "<REDACTED>",
                 "notes": "Custom fields and unknown record types; detected PII only."})
    return rows


# ---------------------------------------------------------------------------
# Policy -> analyzer layouts
# ---------------------------------------------------------------------------

def _role_for(action, base_role):
    if base_role == "kv_name":
        # Name of a custom field / fill-in (e.g. "SSN"). Never scanned itself,
        # but the analyzer uses it to recognize the value that follows.
        return "kv_name"
    if action in ("redact", "keep_all"):
        return ID if base_role == ID else N      # not scanned
    # scan / keep: scan it, using the field's normal role when it has one
    if base_role in (None, ID):
        return T
    return base_role


def build_layouts(policy):
    """Returns (scdif_schema, archive_layout, warnings)."""
    warnings = []
    schema = {}
    record_types = (set(ap.SCDIF_SCHEMA) | policy.record_types()) - {"*", iq_policy.ARCHIVE}
    for rt in sorted(record_types):
        base = list(ap.SCDIF_SCHEMA.get(rt, []))
        listed = dict(policy.fields_of(rt))
        n = max([len(base)] + list(listed))
        layout = []
        for fno in range(1, n + 1):
            if fno <= len(base):
                name, base_role = base[fno - 1]
            else:
                name, base_role = (listed[fno]["field_name"] or f"Extra Field {fno}"), None
            if fno in listed and fno <= len(base):
                pname = listed[fno]["field_name"]
                if pname and pname.lower() != name.lower():
                    warnings.append(f"{rt} field {fno}: policy says '{pname}', standard "
                                    f"layout says '{name}'. Check the export's layout.")
                if pname:
                    name = pname
            layout.append((name, _role_for(policy.action(rt, fno), base_role)))
        schema[rt] = layout

    archive = []
    for fno, (name, base_role) in enumerate(ap.ARCHIVE_FORMAT_FIELDS, start=1):
        archive.append((name, _role_for(policy.action(iq_policy.ARCHIVE, fno), base_role)))
    return schema, archive, warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--policy", default=os.path.join(here, "iq_field_policy.csv"))
    parser.add_argument("--write_default_policy", default=None)
    parser.add_argument("-h", "--help", action="store_true")
    own, rest = parser.parse_known_args()

    if own.help:
        print(__doc__)
        print("IQ options:\n  --policy PATH                field policy CSV "
              "(default: iq_field_policy.csv next to this script)\n"
              "  --write_default_policy PATH  write the default policy and exit\n")
        print("All other options are passed to analyze_pipeline.py:\n")
        sys.argv = [sys.argv[0], "--help"]
        ap.main()
        return

    if own.write_default_policy:
        iq_policy.write_policy(own.write_default_policy, default_policy_rows())
        print(f"Default IQ field policy written to {own.write_default_policy}")
        return

    if any(a == "--db_schema" or a.startswith("--db_schema=") for a in rest):
        sys.exit("ERROR: --db_schema is not used in the IQ branch; describe the "
                 "export's fields in the policy file instead.")

    try:
        policy = iq_policy.load_policy(own.policy)
    except (iq_policy.PolicyError, OSError) as e:
        sys.exit(f"ERROR: {e}")
    schema, archive, warnings = build_layouts(policy)
    print(f"IQ field policy: {own.policy}")
    for w in warnings:
        print(f"WARNING: {w}")

    # Hand the policy-derived layouts to analyze_pipeline. Workers are forked,
    # so they inherit these module-level settings.
    ap.SCDIF_SCHEMA = schema
    ap.ARCHIVE_FORMAT_FIELDS = archive
    found_exports = {}
    original_build_tasks = ap.build_tasks

    def build_tasks_recording_exports(file_paths, args, input_dir):
        tasks, pre_errors = original_build_tasks(file_paths, args, input_dir)
        for t in tasks:
            if t["kind"] == "db_chunk":
                found_exports[os.path.relpath(t["path"], input_dir)] = t["format"]
        return tasks, pre_errors

    ap.build_tasks = build_tasks_recording_exports
    rest = iq_policy.prepare_workers(rest)  # workers inherit the policy via fork

    sys.argv = [sys.argv[0]] + rest
    ap.main()

    # Same output-path rules as analyze_pipeline.py
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input_dir", default="pii")
    p.add_argument("--output_dir", default=None)
    p.add_argument("--job_id", default=os.environ.get("SLURM_JOB_ID"))
    a, _ = p.parse_known_args(rest)
    out_dir = a.output_dir or os.path.join(a.input_dir, "results")
    suffix = f"_{a.job_id}" if a.job_id else ""
    results_csv = os.path.join(out_dir, f"pii_results{suffix}.csv")

    iq_policy.write_policy(iq_policy.policy_copy_path(results_csv), policy.rows)
    with open(iq_policy.exports_list_path(results_csv), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename", "source_type"])
        for rel, fmt in sorted(found_exports.items()):
            w.writerow([rel, fmt])
    print(f"IQ exports found: {len(found_exports)}")
    print(f"Policy used saved to {iq_policy.policy_copy_path(results_csv)}")


if __name__ == "__main__":
    main()
