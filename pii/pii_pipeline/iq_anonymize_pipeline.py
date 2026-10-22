"""
IQ branch of the anonymization pipeline.

Runs anonymize_pipeline.py (which must be in the same folder) on results
from iq_analyze_pipeline.py, applying the IQ field policy
(iq_field_policy.csv) to SCDIF and Senate Archive Format exports, field by
field:

    redact    the whole value is replaced with the policy's label
              (e.g. <FIRST_NAME>) on every record, whether or not any PII
              was detected. Empty values stay empty.
    scan      the PII Presidio detected is replaced (general behavior).
              --keep_entities / --keep_names apply only to these fields.
    keep      the field is shown, but detected ID numbers (SSNs, phone
              numbers, emails, account numbers, ...) are still replaced.
    keep_all  the field is left exactly as it is.

The policy's redact and keep_all settings always win over --keep_entities.

If a field is now `scan` or `keep` but was `redact` or `keep_all` when the
analysis ran, it was never scanned, so it is redacted in full (fail-safe)
and reported. Re-run iq_analyze_pipeline.py to scan it.

Files that aren't IQ exports (PDFs, emails, attachments, CSVs, ...) are
anonymized exactly as by anonymize_pipeline.py.

Besides the usual summary CSV, iq_policy_report_<job_id>.csv lists, for
every export and field, the action applied and how many values / findings
were affected.

Usage:
    python iq_anonymize_pipeline.py --input_dir ... --pii_results_csv ... --output_dir ...
                                    [--policy iq_field_policy.csv] [--keep_ids TYPES]
                                    [any anonymize_pipeline.py option]
"""

import argparse
import csv
import glob
import json
import multiprocessing as mp
import os
import sys
from collections import defaultdict

import anonymize_pipeline as an
import iq_policy

_policy = None            # current policy
_analysis_policy = None   # policy used when the analysis ran
_id_types = set(iq_policy.DEFAULT_ID_TYPES)


def _was_scanned(record_type, field_no):
    return _analysis_policy.action(record_type, field_no) in iq_policy.SCANNED_ACTIONS


def iq_anonymize_db_chunk(anonymizer, task, cfg):
    """
    Replacement for anonymize_pipeline.anonymize_db_chunk that applies the
    field policy. Same contract: copies lines starting in [start, end) to
    task["part_path"] and returns (entities_anonymized, unmatched).
    Per-field statistics are written to <part_path>.stats.json.
    """
    encoding = cfg["db_encoding"]
    fmt = task["source_type"]
    is_archive = fmt == "archive_format"

    by_line = defaultdict(lambda: defaultdict(list))   # offset -> field idx -> rows
    unmatched = 0
    for r in task["rows"]:
        try:
            _, offset, field_no, _ = an.parse_db_location(r["location"])
        except (KeyError, ValueError):
            field_no = None
        if not field_no or (not is_archive and field_no == 1):
            unmatched += 1
            continue
        by_line[offset][field_no - 1].append(r)

    # stats[(record_type, field_no)] = counters
    stats = defaultdict(lambda: {"values_redacted_by_policy": 0, "findings_applied": 0,
                                 "findings_not_applied": 0, "failsafe_redactions": 0})
    count = 0
    first = 1 if is_archive else 2   # never touch the SCDIF record type
    change_cache = {}

    def may_change(rt, n_fields):
        """True if the policy alone can change a line of this type/width."""
        key = (rt, n_fields)
        if key not in change_cache:
            change_cache[key] = any(
                _policy.action(rt, f) == "redact" or
                (_policy.action(rt, f) in iq_policy.SCANNED_ACTIONS and not _was_scanned(rt, f))
                for f in range(first, n_fields + 1))
        return change_cache[key]

    with open(task["path"], "rb") as src, open(task["part_path"], "wb") as dst:
        for offset, raw in an.iter_lines_in_range(src, task["start"], task["end"]):
            body = raw.rstrip(b"\r\n")
            ending = raw[len(body):]
            if not body.strip():
                dst.write(raw)
                continue
            if is_archive:
                rt = iq_policy.ARCHIVE
                if offset == 0 and iq_policy.clean_record_type(
                        body.split(b"\t", 1)[0].decode(encoding, errors="replace")) == "PREFIX":
                    dst.write(raw)  # header row
                    continue
            else:
                rt = iq_policy.clean_record_type(
                    body.split(b"\t", 1)[0].decode(encoding, errors="replace"))

            findings = by_line.pop(offset, {})
            if not findings and not may_change(rt, body.count(b"\t") + 1):
                dst.write(raw)  # fast path: nothing on this line can change
                continue

            values = body.decode(encoding, errors="surrogateescape").split("\t")
            for fno in range(first, len(values) + 1):
                idx = fno - 1
                rule = _policy.rule(rt, fno)
                action = rule["action"]
                rows = findings.pop(idx, [])
                st = stats[(rt, fno)]
                value = values[idx]

                if action in iq_policy.SCANNED_ACTIONS and not _was_scanned(rt, fno):
                    # Not scanned during analysis: fail-safe, redact in full.
                    if value.strip():
                        values[idx] = "<UNSCANNED>"
                        st["failsafe_redactions"] += 1
                        count += 1
                    continue
                if action == "keep_all":
                    st["findings_not_applied"] += len(rows)
                    continue
                if action == "redact":
                    if value.strip():
                        values[idx] = rule["label"]
                        st["values_redacted_by_policy"] += 1
                        count += 1
                    st["findings_not_applied"] += len(rows)
                    continue
                if action == "keep":
                    apply = [r for r in rows if r["entity_type"] in _id_types]
                else:  # scan
                    apply = rows
                st["findings_not_applied"] += len(rows) - len(apply)
                if apply:
                    new_value, n, u = an.anonymize_text(anonymizer, value, apply)
                    values[idx] = an._sanitize_db_value(new_value)
                    st["findings_applied"] += n
                    count += n
                    unmatched += u
            # findings pointing past the end of this line
            unmatched += sum(len(v) for v in findings.values())
            dst.write("\t".join(values).encode(encoding, errors="surrogateescape") + ending)

    unmatched += sum(len(v) for fields in by_line.values() for v in fields.values())
    with open(task["part_path"] + ".stats.json", "w", encoding="utf-8") as f:
        json.dump({"file": task["rel"], "stats": [[rt, fno, v] for (rt, fno), v in stats.items()]}, f)
    return count, unmatched


def make_build_tasks(original, exports):
    """Wrap build_tasks so every export found by the analysis gets processed,
    even when it has no findings (policy redactions still apply)."""
    def build_tasks(results_df, cfg, chunk_bytes, keep_types=frozenset()):
        tasks, db_files, kept_counts = original(results_df, cfg, chunk_bytes, keep_types)
        for rel, source_type in exports.items():
            if rel in db_files:
                continue
            path = os.path.join(cfg["input_dir"], rel)
            if not os.path.exists(path):
                continue
            size = os.path.getsize(path)
            n_chunks = max(1, -(-size // chunk_bytes))
            out = an.output_path(cfg["output_dir"], rel)
            parts = []
            for i in range(n_chunks):
                part = f"{out}.part{i:05d}"
                parts.append(part)
                tasks.append({"kind": "db_chunk", "rel": rel, "source_type": source_type,
                              "path": path, "start": i * chunk_bytes,
                              "end": size if i == n_chunks - 1 else (i + 1) * chunk_bytes,
                              "rows": [], "part_path": part, "chunk_index": i})
            db_files[rel] = {"source_type": source_type, "output": out, "parts": parts}
            kept_counts.setdefault(rel, 0)
        return tasks, db_files, kept_counts
    return build_tasks


def write_policy_report(output_dir, job_id):
    """Combine the per-chunk stats files into one report and delete them."""
    totals = defaultdict(lambda: defaultdict(int))
    stats_files = glob.glob(os.path.join(output_dir, "**", "*.stats.json"), recursive=True)
    for path in stats_files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for rt, fno, counters in data["stats"]:
            for k, v in counters.items():
                totals[(data["file"], rt, fno)][k] += v
        os.remove(path)
    suffix = f"_{job_id}" if job_id else ""
    report = os.path.join(output_dir, f"iq_policy_report{suffix}.csv")
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Original_File", "Record_Type", "Field_No", "Field_Name", "Action",
                    "Values_Redacted_By_Policy", "Findings_Applied",
                    "Findings_Not_Applied", "Failsafe_Redactions"])
        for (rel, rt, fno) in sorted(totals, key=lambda k: (k[0], k[1], int(k[2]))):
            c = totals[(rel, rt, fno)]
            rule = _policy.rule(rt, fno)
            w.writerow([rel, rt, fno, rule.get("field_name", "") if rule.get("field_no") == fno
                        else f"(field {fno})", rule["action"],
                        c["values_redacted_by_policy"], c["findings_applied"],
                        c["findings_not_applied"], c["failsafe_redactions"]])
    failsafe = sum(c["failsafe_redactions"] for c in totals.values())
    return report, failsafe


def main():
    global _policy, _analysis_policy, _id_types
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--policy", default=os.path.join(here, "iq_field_policy.csv"))
    parser.add_argument("--keep_ids", default=None,
                        help="comma-separated entity types still removed from 'keep' fields")
    parser.add_argument("-h", "--help", action="store_true")
    own, rest = parser.parse_known_args()

    if own.help:
        print(__doc__)
        print("IQ options:\n  --policy PATH    field policy CSV (default: iq_field_policy.csv "
              "next to this script)\n  --keep_ids TYPES entity types still removed from "
              f"'keep' fields (default: {','.join(iq_policy.DEFAULT_ID_TYPES)})\n")
        print("All other options are passed to anonymize_pipeline.py:\n")
        sys.argv = [sys.argv[0], "--help"]
        an.main()
        return

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--pii_results_csv", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--job_id", default=os.environ.get("SLURM_JOB_ID"))
    a, _ = p.parse_known_args(rest)

    try:
        _policy = iq_policy.load_policy(own.policy)
    except (iq_policy.PolicyError, OSError) as e:
        sys.exit(f"ERROR: {e}")
    used = iq_policy.policy_copy_path(a.pii_results_csv)
    if not os.path.exists(used):
        sys.exit(f"ERROR: {used} not found. Results for the IQ branch must come from "
                 f"iq_analyze_pipeline.py, which saves the policy it used there.")
    _analysis_policy = iq_policy.load_policy(used)
    exports = {}
    exports_path = iq_policy.exports_list_path(a.pii_results_csv)
    if os.path.exists(exports_path):
        with open(exports_path, newline="", encoding="utf-8") as f:
            exports = {r["filename"]: r["source_type"] for r in csv.DictReader(f)}
    if own.keep_ids is not None:
        _id_types = {t.strip().upper() for t in own.keep_ids.split(",") if t.strip()}

    # Fields that will be redacted in full because they were not scanned.
    gaps = sorted({(r["record_type"], r["field_no"], r["field_name"]) for r in _policy.rows
                   if r["field_no"] != "*" and r["action"] in iq_policy.SCANNED_ACTIONS
                   and not _was_scanned(r["record_type"], r["field_no"])})
    print(f"IQ field policy: {own.policy}")
    for rt, fno, name in gaps:
        print(f"WARNING: {rt} field {fno} ({name}) is set to scan/keep but was not scanned "
              f"in this analysis; it will be redacted in full. Re-run iq_analyze to scan it.")

    an.anonymize_db_chunk = iq_anonymize_db_chunk
    an.build_tasks = make_build_tasks(an.build_tasks, exports)
    an.FILE_TYPE_LABELS["scdif"] = "SCDIF export (IQ / Senate CSS), IQ field policy"
    an.FILE_TYPE_LABELS["archive_format"] = "Senate Archive Format export, IQ field policy"
    rest = iq_policy.prepare_workers(rest)  # workers inherit the policy via fork

    sys.argv = [sys.argv[0]] + rest
    an.main()

    report, failsafe = write_policy_report(a.output_dir, a.job_id)
    print(f"Per-field policy report written to {report}")
    if failsafe:
        print(f"WARNING: {failsafe} values were redacted in full because their field was "
              f"not scanned during analysis (see Failsafe_Redactions in the report).")


if __name__ == "__main__":
    main()
