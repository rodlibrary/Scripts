"""
Field policy for the Intranet Quorum (IQ) branch of the PII pipeline.

The policy is a CSV file (iq_field_policy.csv by default) with one row per
field of an SCDIF or Senate Archive Format export:

    record_type  SCDIF record type ("1A", "2D", ...), "ARCHIVE" for the
                 32-field Archive Format, or "*" for record types that are
                 not listed anywhere in the file.
    field_no     1-based position of the field in the record, as in the
                 SCDIF specification, or "*" for "any field of this record
                 type that is not listed" (e.g. office-specific custom
                 fields added at the end of a record).
    field_name   The field's name. Informational: positions decide which
                 field a row applies to. iq_analyze_pipeline.py warns when
                 a name doesn't match the standard layout, which usually
                 means the office's export uses a different layout.
    action       What to do with the field:
                   redact    always replace the whole value with `label`,
                             whether or not any PII was detected in it.
                   scan      replace only the PII Presidio detects in it
                             (the general pipeline's behavior).
                   keep      show the field, but still remove ID numbers
                             detected in it (SSNs, phone numbers, emails,
                             account/passport/license numbers, ...).
                   keep_all  show the field exactly as it is, unchanged.
    label        Replacement text for `redact` fields, e.g. <FIRST_NAME>.
                 Empty values stay empty.
    notes        Free text; ignored by the scripts.

Rows whose record_type starts with "#" are ignored (use them for comments).
Precedence: an exact (record_type, field_no) row, then (record_type, "*"),
then ("*", "*"). A policy without a ("*", "*") row defaults to "scan".

Changing a field to `redact` or `keep_all` only needs the anonymize step
re-run. Changing a field that was `redact`/`keep_all` during analysis to
`scan`/`keep` needs the analysis re-run, because that field was never
scanned; until then the anonymizer redacts that field in full (fail-safe).
"""

import csv
import re

ACTIONS = ("redact", "scan", "keep", "keep_all")
SCANNED_ACTIONS = ("scan", "keep")  # actions that need analysis findings

# Entity types still removed from `keep` fields. US_DRIVER_LICENSE is left
# out on purpose: its pattern matches short codes such as carrier routes
# ("C001") and precincts ("P12"), which `keep` fields are full of.
DEFAULT_ID_TYPES = (
    "US_SSN", "US_ITIN", "US_PASSPORT", "US_BANK_NUMBER",
    "CREDIT_CARD", "IBAN_CODE", "PHONE_NUMBER", "UK_NHS", "EMAIL_ADDRESS",
    "IP_ADDRESS", "MEDICAL_LICENSE", "CRYPTO", "ALIEN_NUMBER",
    "MEDICARE_MEDICAID_ID",
)

FIELDNAMES = ["record_type", "field_no", "field_name", "action", "label", "notes"]
ARCHIVE = "ARCHIVE"
_RECORD_TYPE_RE = re.compile(r"^([1-9][A-Z]|ARCHIVE|\*|[A-Z0-9]{1,8})$")


class PolicyError(ValueError):
    pass


def clean_record_type(field: str) -> str:
    """Record type with any UTF-8 BOM, quotes and padding removed."""
    return field.lstrip("﻿").strip().strip('"\'').strip().upper()


class FieldPolicy:
    def __init__(self, rows, source="(policy)"):
        self.source = source
        self.rows = rows                 # list of dicts, as loaded
        self.exact = {}                  # (rt, field_no) -> row
        self.per_type_default = {}       # rt -> row
        self.global_default = None
        for r in rows:
            rt, fno = r["record_type"], r["field_no"]
            if rt == "*" and fno == "*":
                self.global_default = r
            elif fno == "*":
                self.per_type_default[rt] = r
            else:
                self.exact[(rt, fno)] = r

    # -- lookups --------------------------------------------------------
    def rule(self, record_type, field_no):
        """Row that applies to this field (dict with action/label/...)."""
        r = self.exact.get((record_type, field_no))
        if r is None:
            r = self.per_type_default.get(record_type)
        if r is None and record_type not in self.record_types():
            r = self.global_default
        if r is None:
            r = self.global_default or {"action": "scan", "label": "<REDACTED>",
                                        "field_name": "", "record_type": "*",
                                        "field_no": "*"}
        return r

    def action(self, record_type, field_no):
        return self.rule(record_type, field_no)["action"]

    def record_types(self):
        return {rt for rt, _ in self.exact} | set(self.per_type_default)

    def fields_of(self, record_type):
        """Sorted [(field_no, row)] of explicitly listed fields."""
        return sorted((fno, r) for (rt, fno), r in self.exact.items() if rt == record_type)

    def redact_fields(self, record_type):
        """Field numbers explicitly set to redact for this record type."""
        return {fno for (rt, fno), r in self.exact.items()
                if rt == record_type and r["action"] == "redact"}


def load_policy(path) -> FieldPolicy:
    rows, errors, seen = [], [], set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {"record_type", "field_no", "action"} - set(reader.fieldnames or [])
        if missing:
            raise PolicyError(f"{path}: missing column(s): {', '.join(sorted(missing))}")
        for line_no, raw in enumerate(reader, start=2):
            rt = (raw.get("record_type") or "").strip().upper()
            if not rt or rt.startswith("#"):
                continue
            fno_raw = (raw.get("field_no") or "").strip()
            action = (raw.get("action") or "").strip().lower()
            label = (raw.get("label") or "").strip() or "<REDACTED>"
            name = (raw.get("field_name") or "").strip()
            where = f"line {line_no} ({rt}, field {fno_raw or '?'} {name})"
            if not _RECORD_TYPE_RE.match(rt):
                errors.append(f"{where}: invalid record_type '{rt}'")
                continue
            if fno_raw == "*":
                fno = "*"
            elif fno_raw.isdigit() and int(fno_raw) >= 1:
                fno = int(fno_raw)
            else:
                errors.append(f"{where}: field_no must be a number >= 1 or '*'")
                continue
            if rt == "*" and fno != "*":
                errors.append(f"{where}: record_type '*' only allows field_no '*'")
                continue
            if action not in ACTIONS:
                errors.append(f"{where}: action '{action}' must be one of {', '.join(ACTIONS)}")
                continue
            if any(ch in label for ch in "\t\r\n|"):
                errors.append(f"{where}: label may not contain tabs, line breaks or '|'")
                continue
            if rt not in ("*", ARCHIVE) and fno == 1 and action != "keep_all":
                errors.append(f"{where}: field 1 is the SCDIF record type and must be keep_all")
                continue
            if (rt, fno) in seen:
                errors.append(f"{where}: duplicate row for this field")
                continue
            seen.add((rt, fno))
            rows.append({"record_type": rt, "field_no": fno, "field_name": name,
                         "action": action, "label": label,
                         "notes": (raw.get("notes") or "").strip()})
    if errors:
        raise PolicyError(f"{path} has {len(errors)} problem(s):\n  " + "\n  ".join(errors[:50]))
    if not rows:
        raise PolicyError(f"{path} has no policy rows")
    return FieldPolicy(rows, source=path)


def write_policy(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})


def prepare_workers(args):
    """
    The IQ scripts hand the policy to worker processes by forking them, which
    Windows (and macOS by default) can't do. Where fork is unavailable, run in
    a single process instead (same results, just slower). Returns the
    argument list to pass on.
    """
    import multiprocessing as mp
    if "fork" in mp.get_all_start_methods():
        mp.set_start_method("fork", force=True)
        return args
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--num_procs":
            skip = True
            continue
        if a.startswith("--num_procs="):
            continue
        out.append(a)
    print("Note: this system can't fork worker processes (e.g. Windows), so the IQ "
          "pipeline runs in a single process.")
    return out + ["--num_procs", "1"]


def policy_copy_path(results_csv):
    """Where iq_analyze_pipeline.py saves the policy used for a results file."""
    base = results_csv[:-4] if results_csv.lower().endswith(".csv") else results_csv
    return base + "_policy.csv"


def exports_list_path(results_csv):
    """Where iq_analyze_pipeline.py lists the exports it found."""
    base = results_csv[:-4] if results_csv.lower().endswith(".csv") else results_csv
    return base + "_exports.csv"


if __name__ == "__main__":
    # python iq_policy.py check <policy.csv>  -> validates the file
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "check":
        sys.exit("Usage: python iq_policy.py check <policy.csv>")
    try:
        p = load_policy(sys.argv[2])
    except (PolicyError, OSError) as e:
        sys.exit(f"ERROR: {e}")
    counts = {a: sum(1 for r in p.rows if r["action"] == a) for a in ACTIONS}
    print(f"Policy file OK: {sys.argv[2]} ({', '.join(f'{n} {a}' for a, n in counts.items())})")
