#!/usr/bin/env python3
"""
validate_scdif.py - check the output of generate_scdif.py.

  python3 validate_scdif.py --data-dir ./scdif_data [--policy iq_field_policy.csv]

Streams the files (memory use is a few hundred MB even at 15 million rows) and
checks:
  * per-file row counts against manifest.json
  * field counts per row, and against the policy CSV when given
  * the first line of every file (clean, tab is the detected delimiter)
  * unique keys: Person, Address, Communication, Workflow, Household, Document, Event IDs
  * referential integrity between record types (child rows point at real parents
    and, where the parent is person-owned, at the same person)
  * code and staff references into the 8A code tables
Exit status is 1 if any check fails.
"""
import argparse
import collections
import csv
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--policy")
    a = ap.parse_args()
    man = json.load(open(os.path.join(a.data_dir, "manifest.json")))
    rts = man["record_types"]
    fails = []
    npass = [0]

    def check(ok, msg):
        if ok:
            npass[0] += 1
        else:
            fails.append(msg)
            print("FAIL  " + msg)

    policy = collections.defaultdict(int)
    if a.policy:
        for r in csv.DictReader(open(a.policy, newline="", encoding="utf-8")):
            if r["record_type"] != "*":
                policy[r["record_type"]] = max(policy[r["record_type"]], int(r["field_no"]))

    def stream(rt):
        """Yield token lists (stripped, quotes removed) for each data row of a record type."""
        for f in rts[rt]["files"]:
            with open(os.path.join(a.data_dir, f["name"]), "rb") as fh:
                for raw in fh:
                    line = raw.decode("latin-1")
                    if "\t" in line:
                        yield [x.strip().strip('"').strip() for x in line.rstrip("\r\n").split("\t")]

    def num(s):
        return int(s) if s.isdigit() else -1

    # ---- file level: manifest counts, first lines, field counts
    short = collections.Counter()
    for rt, info in sorted(rts.items()):
        exp = info["fields"]
        for f in info["files"]:
            with open(os.path.join(a.data_dir, f["name"]), "rb") as fh:
                first = fh.readline().decode("latin-1").rstrip("\r\n")
            counts = {d: len(first.split(d)) for d in (";", ",", "\t", "|")}
            check(max(counts, key=counts.get) == "\t" and counts["\t"] >= exp,
                  "%s first line is clean and tab-delimited with %d fields (got %r)" % (f["name"], exp, counts))
        n = longer = wrong = 0
        for t in stream(rt):
            n += 1
            if len(t) > exp:
                longer += 1
            elif len(t) < exp:
                short[rt] += 1
            if t[0] != rt:
                wrong += 1
        check(n == info["total_rows"], "%s row count %d matches manifest %d" % (rt, n, info["total_rows"]))
        check(longer == 0 and wrong == 0, "%s: no row longer than %d fields (%d), no wrong record type (%d)" % (rt, exp, longer, wrong))
        if policy:
            check(exp in (policy[rt], policy[rt] + 1), "%s field count %d vs policy %d" % (rt, exp, policy[rt]))
    q = man["quirks_applied"]
    check(sum(short.values()) <= q.get("short", 0), "short rows found (%d) not more than 'short' quirks (%d)" % (sum(short.values()), q.get("short", 0)))

    # ---- reference tables
    pers, hh, docs, ev = set(), set(), set(), set()
    addr, comm, wf = {}, {}, {}
    codes = collections.defaultdict(set)
    last, ordered = -1, True
    n1a = 0
    for t in stream("1A"):
        v = num(t[1]); n1a += 1
        pers.add(v)
        ordered &= v > last
        last = v
    check(len(pers) == n1a, "1A Person IDs unique (%d)" % n1a)
    check(ordered, "1A is in ascending Person ID order")
    for t in stream("1B"):
        addr[num(t[2])] = num(t[1])
    check(len(addr) == rts["1B"]["total_rows"], "1B Address IDs unique (%d)" % len(addr))
    for t in stream("2A"):
        comm[num(t[2])] = num(t[1])
    check(len(comm) == rts["2A"]["total_rows"], "2A Communication IDs unique (%d)" % len(comm))
    for t in stream("3A"):
        wf[num(t[2])] = num(t[1])
    check(len(wf) == rts["3A"]["total_rows"], "3A Workflow IDs unique (%d)" % len(wf))
    for t in stream("5A"):
        hh.add(num(t[1]))
    check(len(hh) == rts["5A"]["total_rows"], "5A Household IDs unique (%d)" % len(hh))
    for t in stream("6A"):
        docs.add(num(t[1]))
    check(len(docs) == rts["6A"]["total_rows"], "6A Document IDs unique (%d)" % len(docs))
    for t in stream("7A"):
        ev.add(num(t[1]))
    check(len(ev) == rts["7A"]["total_rows"], "7A Event IDs unique (%d)" % len(ev))
    for t in stream("8A"):
        codes[t[1]].add(t[2])
    staff = codes["STAFF"]

    # ---- row level integrity (streamed)
    bad = collections.Counter()
    seen = collections.Counter()
    ex = {}

    def miss(key, row):
        bad[key] += 1
        ex.setdefault(key, row[:6])

    for rt in rts:
        for t in stream(rt):
            n = len(t)

            def g(i):
                return t[i] if n > i else ""
            if rt not in ("5A", "5B", "5C", "5D", "6A", "6B", "6C", "6D", "6E", "6F", "7A", "7B", "7C", "7D", "7E", "7F", "8A"):
                seen[rt + " person ref"] += 1
                if num(g(1)) not in pers:
                    miss(rt + " Person ID not in 1A", t)
            if rt in ("2B", "2C", "2D", "2E"):
                seen[rt + " comm ref"] += 1
                if comm.get(num(g(2))) != num(g(1)):
                    miss(rt + " Communication ID missing or owned by another person", t)
            elif rt in ("3B", "3D", "3E", "3F", "3L"):
                seen[rt + " wf ref"] += 1
                if wf.get(num(g(2))) != num(g(1)):
                    miss(rt + " Workflow ID missing or owned by another person", t)
            elif rt == "2A":
                if g(3) and wf.get(num(g(3))) != num(g(1)):
                    miss("2A Workflow ID missing or owned by another person", t)
                if g(14) and addr.get(num(g(14))) != num(g(1)):
                    miss("2A Address ID missing or owned by another person", t)
                if g(17) and num(g(17)) not in hh:
                    miss("2A Household ID not in 5A", t)
                if g(6) not in staff:
                    miss("2A user not in 8A STAFF", t)
                if g(9) and g(10) and g(9) > g(10):
                    miss("2A date in after date out", t)
                if g(7) and g(7) not in staff:
                    miss("2A approved-by not in 8A STAFF", t)
            elif rt == "2C":
                if g(5) and num(g(5)) not in docs:
                    miss("2C Document ID not in 6A", t)
            elif rt == "1C":
                if g(3) not in codes[g(2)]:
                    miss("1C code not in 8A", t)
            elif rt == "2B":
                if g(3) not in codes["COM"]:
                    miss("2B code not in 8A COM", t)
            elif rt == "3B":
                if g(3) not in codes["WORK"]:
                    miss("3B code not in 8A WORK", t)
            elif rt == "6C":
                if g(3) not in codes[g(2)]:
                    miss("6C code not in 8A", t)
            elif rt == "7B":
                if g(2) not in codes["COM"]:
                    miss("7B code not in 8A COM", t)
            elif rt == "5B":
                if num(g(1)) not in hh or num(g(2)) not in pers:
                    miss("5B Household or Person ID missing", t)
            elif rt in ("5C", "5D"):
                if num(g(1)) not in hh:
                    miss(rt + " Household ID not in 5A", t)
            elif rt in ("6B", "6C", "6D", "6E", "6F"):
                if num(g(1)) not in docs:
                    miss(rt + " Document ID not in 6A", t)
            elif rt in ("7B", "7C", "7D", "7E", "7F"):
                if num(g(1)) not in ev:
                    miss(rt + " Event ID not in 7A", t)
            if rt == "7C" and not ((g(2) == "PERS" and num(g(3)) in pers) or (g(2) == "STAFF" and g(3) in staff)):
                miss("7C participant missing", t)
            if rt == "7F" and num(g(2)) not in wf:
                miss("7F Workflow ID not in 3A", t)
            if rt in ("3A", "3D", "6F") and g({"3A": 4, "3D": 7, "6F": 2}[rt]) not in staff:
                miss(rt + " user not in 8A STAFF", t)
    for k, v in sorted(bad.items()):
        check(False, "%s: %d rows (e.g. %r)" % (k, v, ex[k]))
    if not bad:
        check(True, "referential integrity and code references")
        print("referential integrity: %d child-to-parent links of %d kinds checked, all resolve" % (sum(seen.values()), len(seen)))

    print("\nID digit lengths (importer sizes integer columns from the first 10,000 lines):")
    for label, vals in (("Person", pers), ("Address", addr), ("Communication", comm), ("Workflow", wf), ("Document", docs)):
        c = collections.Counter(len(str(v)) for v in vals)
        print("  %-14s %s  max=%d" % (label, dict(sorted(c.items())), max(vals)))
    print("\n%d checks passed, %d failed" % (npass[0], len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
