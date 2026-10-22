#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_scdif.py - synthetic IQ "SCDIF" flat files for CCDT import testing.

Everything produced is made up. Names come from generic name lists, email
addresses use the reserved example.com / example.org / example.net domains, and
phone numbers use the fictional 555-01xx range. Nothing is derived from real
constituent data.

What it writes (one tab-delimited file per record type, CRLF line endings,
latin-1 encoded, no header row, like the IQ exports):

  out_1a.dat ... out_8a.dat   35 record types (see RT_ORDER)
  manifest.json               expected data-row counts per file, quirk counts,
                              ID ranges and the arguments used

Record counts: --total-records is the TOTAL NUMBER OF ROWS ACROSS ALL RECORD
TYPES (default 15,000,000). A short pilot run measures how many rows each
synthetic person produces, then the number of persons is chosen so the total
lands close to the target (typically within about 2%). The exact figure is in
manifest.json.

Referential integrity holds across files: every child row points at a Person ID
that exists in 1A, a Communication ID that exists in 2A, a Workflow ID in 3A, a
Household ID in 5A, a Document ID in 6A, and every code used in 1C/2B/3B/6C/7B
exists in 8A.

Output is deterministic for a given --seed and --total-records, regardless of
--workers.

"Quirks" (--quirk-rate, default 0.005; 0 turns them off) imitate the problems
seen in real exports so the importer gets exercised:
  short   trailing blank fields dropped from the row (fewer tabs)
  blank   an empty line before the row
  sep     a "*********************" separator line before the row
  quote   the longest field wrapped in double quotes
  lf      row ends with LF instead of CRLF
  long    a 700-1900 character value in a free-text field (column widths are
          chosen from the first 10,000 lines, so these can overflow later)
Quirks never touch the first rows of a file, and the first line of every output
file is always a clean full-width row with no , ; | characters, so delimiter
detection and column creation are not affected.

Usage:
  python3 generate_scdif.py --out-dir ./scdif_data
  python3 generate_scdif.py --out-dir ./scdif_data --total-records 2000000 --seed 7
  python3 generate_scdif.py --out-dir ./scdif_data --max-rows-per-file 500000
  python3 generate_scdif.py --out-dir ./scdif_data --plan      # estimate only

Needs Python 3.8+ and nothing outside the standard library.
"""
import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import random
import shutil
import sys
import time
import unicodedata
from collections import Counter

VERSION = "1.0"

RT_ORDER = ["1A", "1B", "1C", "1D", "1E", "1F", "1G",
            "2A", "2B", "2C", "2D", "2E",
            "3A", "3B", "3D", "3E", "3F", "3L",
            "5A", "5B", "5C", "5D",
            "6A", "6B", "6C", "6D", "6E", "6F",
            "7A", "7B", "7C", "7D", "7E", "7F",
            "8A"]

# Tokens per row (tab-separated). 2C and 3E carry one trailing empty field in
# the real exports, so they are one higher than the policy CSV.
FIELD_COUNTS = {"1A": 16, "1B": 22, "1C": 4, "1D": 7, "1E": 6, "1F": 8, "1G": 24,
                "2A": 21, "2B": 5, "2C": 8, "2D": 8, "2E": 5,
                "3A": 11, "3B": 4, "3D": 8, "3E": 5, "3F": 9, "3L": 6,
                "5A": 4, "5B": 4, "5C": 7, "5D": 7,
                "6A": 17, "6B": 4, "6C": 4, "6D": 7, "6E": 7, "6F": 3,
                "7A": 27, "7B": 3, "7C": 4, "7D": 7, "7E": 7, "7F": 3,
                "8A": 6}

# 0-based index of the free-text field, used by the "long" quirk
TEXT_FIELD = {"1D": 3, "2D": 4, "3D": 4, "5D": 3, "6E": 3, "7E": 3}

# First ID of each ID family. IDs advance by 1..GAP so they have gaps like real
# exports, and each shard owns its own range so output does not depend on the
# number of worker processes.
BASES = {"person": 100002, "addr": 2000000, "comm": 4500000,
         "wf": 500000, "hh": 30000, "ev": 1000}
GAP = 7
FAMILY_ROWS = {"addr": "1B", "comm": "2A", "wf": "3A", "hh": "5A", "ev": "7A"}

PILOT_PERSONS = 15000
PERSONS_PER_SHARD = 15000

# ---------------------------------------------------------------- word lists
FIRST_M = ("James John Robert Michael William David Richard Joseph Thomas Charles Christopher "
           "Daniel Matthew Anthony Mark Donald Steven Paul Andrew Joshua Kenneth Kevin Brian "
           "George Timothy Ronald Edward Jason Jeffrey Ryan Jacob Gary Nicholas Eric Jonathan "
           "Stephen Larry Justin Scott Brandon Benjamin Samuel Gregory Alexander Frank Patrick "
           "Raymond Jack Dennis Jerry Tyler Aaron José Adam Nathan Henry Douglas Zachary Peter "
           "Kyle Walter Ethan Jeremy Harold Keith Christian Roger Noah Gerald Carl Terry Sean "
           "Austin Arthur Lawrence Jesse Dylan Bryan Joe Jordan Billy Bruce Albert Willie "
           "Gabriel Logan Alan Juan Wayne Roy Ralph Randy Eugene Vincent Russell Elijah Louis "
           "Bobby Philip Johnny André Mateo Hunter Dutch Lewis Keon Garry Chuck Danny Don").split()
FIRST_F = ("Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Sarah Karen Lisa Nancy "
           "Betty Margaret Sandra Ashley Kimberly Emily Donna Michelle Carol Amanda Dorothy "
           "Melissa Deborah Stephanie Rebecca Sharon Laura Cynthia Kathleen Amy Angela Shirley "
           "Anna Brenda Pamela Emma Nicole Helen Samantha Katherine Christine Debra Rachel "
           "Carolyn Janet Catherine Maria María Heather Diane Ruth Julie Olivia Joyce Virginia "
           "Victoria Kelly Lauren Christina Joan Evelyn Judith Megan Andrea Cheryl Hannah "
           "Jacqueline Martha Gloria Teresa Ann Sara Madison Frances Kathryn Janice Jean Abigail "
           "Alice Julia Judy Sophia Grace Denise Amber Doris Marilyn Danielle Beverly Isabella "
           "Theresa Diana Natalie Brittany Charlotte Marie Kayla Alexis Lori Renée Zoë Jan "
           "Gayanne Tammie Kristen").split()
LAST_COMMON = ("Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez "
               "Hernandez Lopez Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin Lee "
               "Perez Thompson White Harris Sanchez Clark Ramirez Lewis Robinson Walker Young "
               "Allen King Wright Scott Torres Nguyen Hill Flores Green Adams Nelson Baker Hall "
               "Rivera Campbell Mitchell Carter Roberts Gomez Phillips Evans Turner Diaz Parker "
               "Cruz Edwards Collins Reyes Stewart Morris Morales Murphy Cook Rogers Gutierrez "
               "Ortiz Morgan Cooper Peterson Bailey Reed Kelly Howard Ramos Kim Cox Ward "
               "Richardson Watson Brooks Chavez Wood James Bennett Gray Mendoza Ruiz Hughes Price "
               "Alvarez Castillo Sanders Patel Myers Long Ross Foster Jimenez Powell Jenkins "
               "Perry Russell Sullivan Bell Coleman Butler Henderson Barnes Fisher Vasquez "
               "Simmons Romero Jordan Patterson Alexander Hamilton Graham Reynolds Griffin "
               "Wallace Moreno West Cole Hayes Bryant Herrera Gibson Ellis Tran Medina Aguilar "
               "Stevens Murray Ford Castro Marshall Owens Harrison Fernandez McDonald Woods "
               "Washington Kennedy Wells Vargas Henry Chen Freeman Webb Tucker Guzman Burns "
               "Crawford Olson Simpson Porter Hunter Gordon Mendez Silva Shaw Snyder Mason Dixon "
               "Munoz Hunt Hicks Holmes Palmer Wagner Black Robertson Boyd Rose Stone Salazar "
               "Fox Warren Mills Meyer Rice Schmidt Garza Daniels Ferguson Nichols Stephens Soto "
               "Weaver Ryan Gardner Payne Grant Dunn Kelley Spencer Hawkins Crislip Heckler "
               "Falkner Hogue Avery Crews Roehl Langston Fort Cates Ritchey Bland Traverse "
               "Vanderpool Quorum Evans").split()
LAST_SPECIAL = ["O'Brien", "O'Connor", "Müller", "Peña", "Núñez", "Hernández", "González",
                "Pérez", "Ramírez", "López", "Smith-Jones", "Garcia-Lopez", "Van Der Berg",
                "De La Cruz", "St. John", "McAllister", "Perine Beard", "Ångström", "Dubois-Fontaine"]
STREETS = ("Main Oak Maple Cedar Elm Washington Lake Hill Park Pine Church Spring Walnut "
           "Jefferson Madison Lincoln Highland Sunset River Forest Meadow Orchard Ridge Valley "
           "Mill Franklin Jackson Cherry Chestnut Poplar Union Broad Market College Center "
           "Third Fourth Fifth Sixth Seventh Poplar-Grove Hickory Magnolia Dogwood Willow "
           "Sycamore Bluff Cypress Harbor Prospect Summit Liberty Independence Commerce").split()
SUFFIX = ["Street", "Avenue", "Road", "Lane", "Drive", "Boulevard", "Court", "Way", "Circle", "Place"]
# city, state, county, district, zip3, area code, weight
LOCS = [("Memphis", "TN", "Shelby", "TN09", "381", "901", 9), ("Germantown", "TN", "Shelby", "TN09", "381", "901", 3),
        ("Collierville", "TN", "Shelby", "TN08", "380", "901", 3), ("Jackson", "TN", "Madison", "TN08", "383", "731", 3),
        ("Dyersburg", "TN", "Dyer", "TN08", "383", "731", 2), ("Somerville", "TN", "Fayette", "TN08", "380", "901", 2),
        ("Nashville", "TN", "Davidson", "TN05", "372", "615", 8), ("Knoxville", "TN", "Knox", "TN02", "379", "865", 6),
        ("Chattanooga", "TN", "Hamilton", "TN03", "374", "423", 6), ("Johnson City", "TN", "Washington", "TN01", "376", "423", 3),
        ("Cleveland", "TN", "Bradley", "TN03", "373", "423", 2), ("Fairfax", "VA", "Fairfax", "VA11", "220", "703", 2),
        ("Alexandria", "VA", "Alexandria", "VA08", "223", "703", 2), ("Richmond", "VA", "Richmond", "VA04", "232", "804", 2),
        ("Norfolk", "VA", "Norfolk", "VA03", "235", "757", 1), ("Charleston", "WV", "Kanawha", "WV02", "253", "304", 2),
        ("Morgantown", "WV", "Monongalia", "WV01", "265", "304", 2), ("Huntington", "WV", "Cabell", "WV03", "257", "304", 1),
        ("Lexington", "KY", "Fayette", "KY06", "405", "859", 1), ("Louisville", "KY", "Jefferson", "KY03", "402", "502", 1),
        ("Birmingham", "AL", "Jefferson", "AL06", "352", "205", 1), ("Huntsville", "AL", "Madison", "AL05", "358", "256", 1),
        ("Atlanta", "GA", "Fulton", "GA05", "303", "404", 1), ("Columbus", "OH", "Franklin", "OH03", "432", "614", 1),
        ("Pittsburgh", "PA", "Allegheny", "PA18", "152", "412", 1)]
LOC_POOL = [l for l in LOCS for _ in range(l[6])]
INDUSTRY = ["Dental", "Hardware", "Insurance Agency", "Realty", "Bank", "Credit Union", "Plumbing",
            "Farm Supply", "Law Office", "Pharmacy", "Veterinary Clinic", "Construction", "Auto Repair",
            "Church", "Elementary School", "Fire Department", "Chamber of Commerce", "Rotary Club",
            "Hospital", "Manufacturing"]
JOBS = ["Owner", "President", "Branch Manager", "Pastor", "Principal", "Fire Chief", "Director",
        "Attorney", "Teacher", "Executive Director", "Senior Branch Executive"]
EMAIL_DOMAINS = ["example.com", "example.org", "example.net"]
WF_TYPES = [("Outreach", 30), ("Casework", 20), ("Boy Scout", 8), ("Flag Request", 8), ("Tour Request", 8),
            ("Academy Nomination", 4), ("Grant Support", 4), ("Appointment Request", 6),
            ("Veterans Case", 5), ("Immigration Case", 4), ("Social Security Case", 3)]
WF_POOL = [n for n, w in WF_TYPES for _ in range(w)]
WF_DESC = ["Eagle Scout request", "Flag request", "Tour of the Capitol", "Service academy nomination",
           "Federal grant support letter", "Appointment request", "VA claim assistance",
           "Immigration status inquiry", "Social Security benefit inquiry", "Constituent outreach",
           "Casework: agency follow-up", "Meeting request"]
CUSTOM_FIELDS = {"Outreach": ["interest1", "interest2", "interest3", "interest4", "interest5",
                              "interest6", "asstphone", "Region", "lastmeet", "notes"],
                 "Boy Scout": ["Boy Scout Name", "Award Name", "Troop"],
                 "Casework": ["Agency", "Case Number", "Benefit Type"]}
CUSTOM_VALUES = ["Financial Services - Bankers", "Health - Hospitals", "Business - All", "Memphis",
                 "Education - K12", "Agriculture", "Energy", "Veterans", "Transportation", "Small Business"]
FILL_FIELDS = [("Case Number", "num"), ("Agency", "agency"), ("Meeting Date", "date"),
               ("Bill Number", "bill"), ("Grant ID", "num")]
AGENCIES = ["VA", "SSA", "IRS", "USCIS", "HHS", "USDA", "FEMA", "DOT", "State Department"]
SENT = ["I am writing to share my concern about the direction of this legislation.",
        "Please let me know where the Senator stands on this issue.",
        "My family has lived in this community for more than thirty years.",
        "I would appreciate a response at your earliest convenience.",
        "The cost of this proposal falls on small businesses in our area.",
        "Thank you for your service and for taking the time to read my message.",
        "I have attached documentation that explains my situation in more detail.",
        "Our office tried calling the agency three times without a response.",
        "The deadline for this application is approaching and we need help.",
        "Constituent asked that this be shared with the appropriate committee staff.",
        "Follow up with the district office about next steps on this request.",
        "Please update the mailing address on file before sending the response."]
SUBJ = ["Re: %s", "%s", "Question about %s", "Please support %s", "Opposition to %s", "Update on %s",
        "Request for assistance: %s", "Thank you for your message on %s", "Concern regarding %s"]
NOTES = ["Spoke with %(n)s on %(d)s about %(t)s. Follow up needed.",
         "Left voicemail for %(n)s at %(p)s. Will try again next week.",
         "%(n)s emailed from %(e)s regarding %(t)s. Standard response sent.",
         "Constituent lives at %(a)s and asked for a status update on %(t)s.",
         "Letter reviewed by %(u)s - salutation needs correction. Thx!",
         "Met with %(n)s at the district office re: %(t)s on %(d)s.",
         "Callback requested at %(p)s. Preferred contact is %(e)s.",
         "Please confirm mailing address (%(a)s) before the letter goes out."]
NOTE_SHORT = ["Created by IMA", "Retired", "Prefers email", "Do not call", "VIP - see staff",
              "Address verified", "confirmed %s", "Spouse of %s"]
EVENT_DESC = ["Town Hall - %s", "Roundtable: %s", "Ribbon Cutting - %s", "Constituent Coffee - %s",
              "Veterans Day Event - %s", "Chamber of Commerce Luncheon - %s", "Site Visit: %s"]
VENUES = ["Public Library", "Community Center", "City Hall", "Civic Auditorium", "County Courthouse Annex",
          "High School Gymnasium", "Fire Station", "Senior Center"]
COM_CATS = {"FIN": [("BANK", "financial services and banking"), ("TAX", "tax policy"), ("CREDIT", "consumer credit"),
                    ("SEC", "securities regulation")],
            "HEA": [("ACA", "the Affordable Care Act"), ("MEDICARE", "Medicare reimbursement"), ("VA", "veterans health care"),
                    ("SC", "health see comments"), ("DRUG", "prescription drug costs")],
            "EDU": [("K12", "K-12 education funding"), ("LOAN", "student loan relief"), ("HE", "higher education")],
            "ENV": [("EPA", "EPA rulemaking"), ("WATER", "clean water"), ("ENERGY", "energy policy")],
            "AGR": [("FARM", "the farm bill"), ("TRADE", "agricultural trade")],
            "DEF": [("BUDGET", "defense appropriations"), ("VET", "veterans benefits")],
            "TRN": [("HWY", "highway funding"), ("AIR", "air travel")],
            "JUD": [("NOM", "judicial nominations"), ("GUN", "firearms legislation")],
            "IMM": [("REFORM", "immigration reform"), ("VISA", "visa processing")],
            "TEL": [("NET", "net neutrality"), ("PRIV", "data privacy")],
            "HOU": [("GSE", "GSE reform"), ("FHA", "FHA lending")],
            "LAB": [("MINWAGE", "the minimum wage"), ("UNION", "labor law")]}
VERBS = ["support", "oppose", "question about", "information on", "concern about"]

# ------------------------------------------------------------------ dates
_D0 = datetime.date(2005, 1, 3)
_NDAYS = (datetime.date(2026, 10, 1) - _D0).days
DATES = [(_D0 + datetime.timedelta(i)).strftime("%Y%m%d") for i in range(_NDAYS + 1)]
LAST_DAY = len(DATES) - 1


def date_us(ds):
    return "%d/%d/%s" % (int(ds[4:6]), int(ds[6:8]), ds[:4])


def _asc(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


FIRST_M_T = [(n, _asc(n)) for n in FIRST_M]
FIRST_F_T = [(n, _asc(n)) for n in FIRST_F]
LAST_T = [(n, _asc(n)) for n in LAST_COMMON * 2 + LAST_SPECIAL]


def long_text(r, lo=700, hi=1900):
    target = lo + int(r.random() * (hi - lo))
    out, n = [], 0
    while n < target:
        s = r.choice(SENT)
        out.append(s)
        n += len(s) + 1
    return " ".join(out)[:hi]


# --------------------------------------------------------------- globals
def build_globals(seed, ndocs):
    """Tables that are the same for every shard: staff, code tables (8A), documents (6A)."""
    rs = random.Random("%s:staff" % seed)
    staff_ids = rs.sample(range(100, 400), 220)
    staff = []
    for sid in staff_ids:
        fn = rs.choice(FIRST_M_T + FIRST_F_T)[0]
        ln = rs.choice(LAST_T)[0]
        staff.append((str(sid), "%s %s" % (fn, ln), "Y" if rs.random() < 0.35 else ""))
    staff_ids = [s[0] for s in staff] + ["QC"]

    rc = random.Random("%s:codes" % seed)
    pers = [("OUTREACH DATABASE", "Contacts from the Outreach Database"), ("NEWSLETTER", "NEWSLETTER"),
            ("BOY SCOUT - STUDENT", "Boy Scout - Student"), ("VIP", "Very Important Person"),
            ("DECEASED", "Deceased"), ("NO.MAIL", "Do Not Mail"), ("EO", "Elected Official"),
            ("NO.MERGE", "Do Not Merge"), ("FW", "Frequent Writer"), ("EMAIL.OPTIN", "Email Optin to Newsletter Subscription"),
            ("EMAIL.OPTOUT", "Unsubscribe to Email Newsletter"), ("MOC.ALL", "Member of Congress"),
            ("ARCHIVE - OUTREACH", "Archived Outreach Database Record")]
    for i in range(1, 240):
        pers.append(("LIST.%03d" % i, "Mailing list %d" % i))
    com = []
    seen = set()
    for cat, subs in COM_CATS.items():
        for sub, desc in subs:
            code = "%s_%s" % (cat, sub)
            com.append((code, "%s %s" % (rc.choice(VERBS), desc)))
            seen.add(code)
    while len(com) < 1200:
        cat = rc.choice(list(COM_CATS))
        sub, desc = rc.choice(COM_CATS[cat])
        bill = rc.randint(100, 3500)
        code = "%s_%s-S%d" % (cat, sub, bill)
        if code in seen:
            continue
        seen.add(code)
        com.append((code, "%s %s (S.%d)" % (rc.choice(VERBS), desc, bill)))
    work = [("SCOUTS", "Scouts workflow"), ("CASEWORK", "Casework"), ("FLAG", "Flag requests"), ("TOURS", "Tours"),
            ("ACADEMY", "Academy nominations"), ("APPT", "Appointments"), ("GRANT", "Grant support"),
            ("VA", "Veterans Affairs"), ("SSA", "Social Security"), ("IMMIG", "Immigration"), ("IRS", "IRS")]
    for i in range(1, 110):
        work.append(("WF.%03d" % i, "Workflow category %d" % i))
    work_cat = [(c, d) for c, d in [("CASE", "Casework"), ("EAGLE", "Eagle Scout"), ("STATE", "State agency"),
                                    ("FED", "Federal agency"), ("VA", "Veterans"), ("SSA", "Social Security"),
                                    ("IRS", "IRS"), ("MIL", "Military"), ("IMM", "Immigration"), ("OTH", "Other")]]

    rd = random.Random("%s:docs" % seed)
    docids = sorted(rd.sample(range(100000, 400000), ndocs))
    docs = []
    for did in docids:
        verb = rd.choice(["Thank You", "Follow Up", "Oppose", "Support", "Response", "Casework", "Information"])
        name = "%s - %s" % (verb, rd.choice(com)[1])
        status = rd.choice(["Approved"] * 5 + ["Inactive"] * 3 + ["Draft"] * 2 + ["Pending"])
        docs.append((did, name, status))
    return {"staff": staff, "staff_ids": staff_ids, "pers": pers, "com": com, "work": work,
            "work_cat": work_cat, "docs": docs, "docids": docids,
            "topics": [c[1] for c in com]}


_G = None


def _init(G):
    global _G
    _G = G


# ----------------------------------------------------------------- output
class Sink:
    """Collects rows per record type, applies quirks, writes shard part files."""

    def __init__(self, parts_dir, tag, r, qrate, enable):
        self.tag, self.r, self.q, self.enable = tag, r, qrate, enable
        self.counts, self.qcounts = Counter(), Counter()
        self.fh, self.buf = {}, {}
        if enable:
            for rt in RT_ORDER:
                self.fh[rt] = open(os.path.join(parts_dir, "%s.%s.part" % (rt, tag)), "wb")
                self.buf[rt] = []

    def add(self, rt, f):
        c = self.counts
        c[rt] += 1
        term, pre = "\r\n", ""
        q = self.q
        if q and self.r.random() < q and not (self.tag == "0000" and c[rt] <= 3):
            k = self.r.random()
            qc = self.qcounts
            if k < 0.45:
                n = len(f)
                while n > 2 and not f[n - 1].strip():
                    n -= 1
                if n < len(f):
                    f = f[:n]
                    qc["short"] += 1
            elif k < 0.57:
                pre = "\r\n"
                qc["blank"] += 1
            elif k < 0.65:
                pre = "\r\n\r\n*********************\r\n"
                qc["sep"] += 1
            elif k < 0.80:
                i = max(range(1, len(f)), key=lambda j: len(f[j]))
                if len(f[i]) > 2:
                    f = list(f)
                    f[i] = '"%s"' % f[i]
                    qc["quote"] += 1
            elif k < 0.90:
                term = "\n"
                qc["lf"] += 1
            elif rt in TEXT_FIELD:
                f = list(f)
                f[TEXT_FIELD[rt]] = long_text(self.r)
                qc["long"] += 1
        if self.enable:
            b = self.buf[rt]
            b.append(pre + "\t".join(f) + term)
            if len(b) >= 4096:
                self._flush(rt)

    def _flush(self, rt):
        b = self.buf[rt]
        if b:
            self.fh[rt].write("".join(b).encode("latin-1", "replace"))
            del b[:]

    def close(self):
        if self.enable:
            for rt in RT_ORDER:
                self._flush(rt)
                self.fh[rt].close()


# ------------------------------------------------- global tables (6x and 8A)
def gen_global(G, seed, parts_dir, qrate, enable):
    r = random.Random("%s:globalrows" % seed)
    rnd, ch = r.random, r.choice
    sink = Sink(parts_dir, "global", r, qrate, enable)
    add = sink.add
    staff_ids = G["staff_ids"]
    FL = "..\\documents\\BlobExport\\formletters\\"
    IMG = "..\\documents\\BlobExport\\formletter_ima\\"
    # 8A: code tables
    for code, desc in G["pers"]:
        add("8A", ["8A", "PERS", code, desc, "Y" if rnd() < 0.15 else "", ""])
    for code, desc in G["com"]:
        add("8A", ["8A", "COM", code, desc, "Y" if rnd() < 0.2 else "", ch(staff_ids) if rnd() < 0.3 else ""])
    for code, desc in G["work"]:
        add("8A", ["8A", "WORK", code, desc, "", ""])
    for code, desc in G["work_cat"]:
        add("8A", ["8A", "WORK_CAT", code, desc, "", ""])
    for sid, name, inactive in G["staff"]:
        add("8A", ["8A", "STAFF", sid, name, inactive, ""])
    add("8A", ["8A", "STAFF", "QC", "QC", "", ""])
    # 6x: documents (form letters)
    for did, name, status in G["docs"]:
        sdid = str(did)
        d0 = int(LAST_DAY * (1 - rnd() ** 1.6))
        d1 = min(LAST_DAY, d0 + int(rnd() * 900))
        d2 = min(LAST_DAY, d1 + int(rnd() * 700))
        add("6A", ["6A", sdid, str(1 if rnd() < 0.8 else 2 + int(rnd() * 7)), sdid if rnd() < 0.9 else str(did - 1),
                   "Form", name, "", FL + sdid + ".doc", ch(staff_ids), ch(staff_ids) if rnd() < 0.7 else "",
                   "", DATES[d0], DATES[d1], DATES[d2] if rnd() < 0.8 else "", status,
                   "Y" if status == "Inactive" else "",
                   "Form Letters" if rnd() < 0.85 else "Formats\\Casework Letters"])
        for k in range(int(rnd() * 3.2)):
            add("6B", ["6B", sdid, "field_%d" % (k + 1), ch(["Name", "Award Name", "Issue", "Date", "Agency", "Bill Number"])])
        for k in range(1 + int(rnd() * 4)):
            ct = ch(["COM", "COM", "WORK", "WORK_CAT"])
            code = ch(G["com"])[0] if ct == "COM" else ch(G["work"])[0] if ct == "WORK" else ch(G["work_cat"])[0]
            add("6C", ["6C", sdid, ct, code])
        if rnd() < 0.3:
            add("6D", ["6D", sdid, IMG + "%s_%s.pdf" % (sdid, ch(["Fact Sheet", "Flyer", "Brochure"])), ch(staff_ids),
                       DATES[d1], "", "Y" if rnd() < 0.5 else ""])
        for k in range(int(rnd() * 2.2) if rnd() < 0.4 else 0):
            add("6E", ["6E", sdid, str(k + 1), ch(SENT), DATES[d1], "%02d:%02d:%02d" % (int(rnd() * 24), int(rnd() * 60), int(rnd() * 60)), ch(staff_ids)])
        add("6F", ["6F", sdid, ch(staff_ids)])
    sink.close()
    return sink.counts, sink.qcounts


# ------------------------------------------------------------- one shard
def run_shard(task):
    shard, p0, npers, cfg, enable = task
    G = _G
    tag = "%04d" % shard if isinstance(shard, int) else str(shard)
    r = random.Random("%s:%s" % (cfg["seed"], tag))
    rnd, ch = r.random, r.choice
    sink = Sink(cfg["parts_dir"], tag, r, cfg["qrate"] if enable else 0.0, enable)
    add = sink.add

    staff_ids, com_codes, work_codes = G["staff_ids"], G["com"], G["work"]
    pers_codes, work_cats, docids, topics = G["pers"], G["work_cat"], G["docids"], G["topics"]
    FL = "..\\documents\\formletters\\"
    OBJ = "..\\documents\\objects\\"
    IMAIL = "..\\documents\\imail\\"
    BLOB = "..\\documents\\BlobExport\\objects\\"

    sid = shard if isinstance(shard, int) else 0
    bases, spans = cfg["bases"], cfg["spans"]
    state = {}
    for fam in FAMILY_ROWS:
        state[fam] = [bases[fam] + sid * spans[fam]]

    def nid(fam):
        s = state[fam]
        s[0] += 1 + int(rnd() * GAP)
        return s[0]

    def tm():
        return "%02d:%02d:%02d" % (int(rnd() * 24), int(rnd() * 60), int(rnd() * 60))

    def note(P, tpc, usr, dus):
        return ch(NOTES) % {"n": P[0], "p": P[2], "e": P[1], "a": P[3], "t": tpc, "u": usr, "d": dus}

    hh_left, hh_id, hh_last = 0, "", None

    for pid in range(p0, p0 + npers):
        spid = str(pid)
        female = rnd() < 0.52
        fn, fn_a = ch(FIRST_F_T if female else FIRST_M_T)
        in_hh = False
        if hh_left > 0:
            ln, ln_a = hh_last
            hh_left -= 1
            in_hh = True
            primary = ""
        else:
            ln, ln_a = ch(LAST_T)
            if rnd() < 0.30:
                hh_id = str(nid("hh"))
                hh_last = (ln, ln_a)
                hh_left = 1 + (rnd() < 0.35) + (rnd() < 0.1)
                in_hh = True
                primary = "Y"
                add("5A", ["5A", hh_id, "The %s Family" % ln,
                           "%s %s and Family" % ("Ms." if female else "Mr.", ln)])
                if rnd() < 0.01:
                    add("5C", ["5C", hh_id, "Household letter.pdf", "", ch(staff_ids), ch(DATES), ch(SENT)])
                if rnd() < 0.02:
                    add("5D", ["5D", hh_id, "1", ch(SENT), ch(DATES), tm(), ch(staff_ids)])
        if in_hh:
            add("5B", ["5B", hh_id, spid, primary])

        x = rnd()
        ptype = "CS" if x < 0.94 else "AG" if x < 0.985 else "USER"
        if female:
            prefix = "Ms." if rnd() < 0.88 else "Mrs." if rnd() < 0.8 else "Dr."
        else:
            prefix = "Mr." if rnd() < 0.96 else "Dr."
        middle = " " if rnd() > 0.18 else ch(["J.", "A.", "M.", "L.", "R."])
        suffix = " " if (female or rnd() > 0.04) else ch(["Jr.", "Sr.", "II", "III"])
        org = ""
        if ptype == "AG" and rnd() < 0.7:
            org = "%s %s" % (ln, ch(INDUSTRY))
        salut = "%s %s" % (prefix, ln)
        dob = "19%02d%02d%02d" % (25 + int(rnd() * 70), 1 + int(rnd() * 12), 1 + int(rnd() * 28)) if rnd() < 0.04 else ""
        add("1A", ["1A", spid, ptype, prefix, fn, middle, ln, suffix, " " if rnd() > 0.01 else "Esq.", org,
                   salut, dob, "Y" if rnd() < 0.03 else "", "Y" if rnd() < 0.01 else "",
                   ch(FIRST_F_T if not female else FIRST_M_T)[0] if rnd() < 0.06 else "", "Y" if rnd() < 0.05 else ""])

        # addresses
        loc = ch(LOC_POOL)
        ac = loc[5]
        paid = ""
        paddr = ""
        for k in range(1 + (rnd() < 0.13)):
            aid = str(nid("addr"))
            lc = loc if k == 0 else ch(LOC_POOL)
            biz = (k == 0 and rnd() < 0.10) or k == 1 and rnd() < 0.5
            l1 = "%d %s %s" % (100 + int(rnd() * 9800), ch(STREETS), ch(SUFFIX)) if rnd() > 0.05 else "PO Box %d" % (1 + int(rnd() * 9000))
            l2 = (ch(["Apt %d%s" % (1 + int(rnd() * 40), ch("ABCD")), "Suite %d" % (100 + int(rnd() * 400))])
                  if rnd() < 0.12 else " ")
            zipc = lc[4] + "%02d" % int(rnd() * 100)
            if k == 0:
                paid, paddr = aid, "%s, %s, %s %s" % (l1, lc[0], lc[1], zipc)
            add("1B", ["1B", spid, aid, "BU" if biz else "HO" if k == 0 else "SE", "Y" if k == 0 else "",
                       "Y" if (k == 0 and rnd() > 0.03) else "",
                       ch(JOBS) if biz else "", ("%s %s" % (ln, ch(INDUSTRY))) if biz else "",
                       l1, l2, " ", " ", lc[0], lc[1], zipc,
                       "%s%03d" % (ch("CRBH"), int(rnd() * 60)) if rnd() < 0.85 else "", lc[2], "USA", lc[3],
                       " " if rnd() > 0.02 else str(10000 + int(rnd() * 9000)), "Y" if rnd() < 0.02 else "", ""])

        # phones / emails
        pemail, pphone = "", ""
        loc_ac = "(%s) 555-01%02d" % (ac, int(rnd() * 100))
        em = "%s.%s%s@%s" % (fn_a, ln_a, int(rnd() * 1000) if rnd() < 0.5 else "", ch(EMAIL_DOMAINS))
        if rnd() < 0.55:
            pemail = em
            add("1E", ["1E", spid, "EMAIL", em, "Y", ""])
        if rnd() < 0.50:
            pphone = loc_ac
            add("1E", ["1E", spid, "PHONE", loc_ac, "Y" if rnd() < 0.9 else "", "Y" if rnd() < 0.01 else ""])
        if rnd() < 0.25:
            add("1E", ["1E", spid, "CELL", "(%s) 555-01%02d" % (ac, int(rnd() * 100)), "", ""])
        if rnd() < 0.06:
            add("1E", ["1E", spid, "FAX", "(%s) 555-01%02d" % (ac, int(rnd() * 100)), "", ""])
        if rnd() < 0.04:
            add("1E", ["1E", spid, "HOME", "(%s) 555-01%02d" % (ac, int(rnd() * 100)), "", ""])
        if not pphone:
            pphone = loc_ac
        P = ("%s %s" % (fn, ln), pemail or em, pphone, paddr)

        # codes, notes, attachments, demographics
        for k in range(1 + min(7, int(-math.log(1 - rnd()) * 0.9))):
            if rnd() < 0.9:
                add("1C", ["1C", spid, "PERS", ch(pers_codes)[0]])
            else:
                add("1C", ["1C", spid, "COM", ch(com_codes)[0]])
        if rnd() < 0.12:
            d = ch(DATES)
            txt = ch(NOTE_SHORT)
            if "%s" in txt:
                txt = txt % (date_us(d) if "confirmed" in txt else P[0])
            if rnd() < 0.3:
                add("1D", ["1D", spid, "1", txt, d, tm(), ch(staff_ids)])
            else:
                add("1D", ["1D", spid, "1", txt, "", "", ""])
        if rnd() < 0.03:
            fname = ch(["Letter", "Scan", "Photo", "Form", "Brochure"]) + "%d.%s" % (int(rnd() * 99), ch(["tif", "pdf", "txt", "doc", "jpg"]))
            add("1F", ["1F", spid, BLOB + "test%d%s" % (int(rnd() * 99999), fname[fname.rfind("."):]), "",
                       ch(staff_ids), ch(DATES), "", fname])
        if rnd() < 0.25:
            lang = "English" if rnd() < 0.9 else ch(["Spanish", "Spanish", "Vietnamese", "Arabic", "Korean"])
            f = ["1G", spid, "Import", "", "", ch(DATES), "", "", "", "", "", "", "", "", lang, "", "", "", "", "", "", "", "", dob]
            if rnd() < 0.4:
                f[8] = ch(["Married", "Single", "Divorced", "Widowed"])
            if rnd() < 0.05:
                f[9] = "Y"
            if rnd() < 0.3:
                f[15] = ch(["High School", "College", "Graduate School", "Some College"])
            add("1G", f)

        # workflows
        wfs = []
        if rnd() < 0.38:
            nw = 1
            while rnd() < 0.35 and nw < 8:
                nw += 1
            for _ in range(nw):
                wid = str(nid("wf"))
                wfs.append(wid)
                wtype = ch(WF_POOL)
                usr = ch(staff_ids)
                d0 = int(LAST_DAY * (1 - rnd() ** 1.6))
                x = rnd()
                status = "CLOSED" if x < 0.70 else "OPEN" if x < 0.82 else "CL.FAV" if x < 0.92 else "CL.UNF" if x < 0.97 else "PENDING"
                d1 = min(LAST_DAY, d0 + int(rnd() * 120))
                desc = ch(WF_DESC)
                add("3A", ["3A", spid, wid, wtype, usr, DATES[d0], DATES[d1] if status != "OPEN" else "", "",
                           DATES[d1], desc, status])
                for _k in range(1 + (rnd() < 0.3)):
                    add("3B", ["3B", spid, wid, ch(work_codes)[0]])
                seq = 1
                add("3D", ["3D", spid, wid, str(seq), "Workflow created by %s." % usr, DATES[d0], tm(), usr])
                for _k in range(int(rnd() * 3.2)):
                    seq += 1
                    add("3D", ["3D", spid, wid, str(seq), note(P, ch(topics), usr, date_us(DATES[d1])), DATES[d1], tm(), usr])
                if status != "OPEN" and status != "PENDING":
                    seq += 1
                    hh12 = 1 + int(rnd() * 12)
                    add("3D", ["3D", spid, wid, str(seq),
                               "This workflow was Closed with the '%s' Status Code on %s %d:%02d %s by %s." %
                               (status, date_us(DATES[d1]), hh12, int(rnd() * 60), ch(["AM", "PM"]), usr),
                               DATES[d1], tm(), usr])
                if rnd() < 0.3:
                    add("3E", ["3E", spid, wid, str(p0 + int(rnd() * npers)), ""])
                if rnd() < 0.10:
                    fname = "Attachment%d.%s" % (int(rnd() * 99), ch(["html", "pdf", "docx"]))
                    add("3F", ["3F", spid, wid, BLOB + "%d.%s" % (int(rnd() * 99999), fname.split(".")[1]), "",
                               ch(staff_ids), DATES[d1], "Attached File - ", fname])
                if wtype in CUSTOM_FIELDS:
                    flds = CUSTOM_FIELDS[wtype]
                    for _k in range(1 + int(rnd() * 3)):
                        fname = ch(flds)
                        val = (date_us(ch(DATES)) if fname == "lastmeet" else
                               "(%s) 555-01%02d" % (ac, int(rnd() * 100)) if fname == "asstphone" else
                               ch(FIRST_F_T + FIRST_M_T)[0] + " " + ch(LAST_T)[0] if fname.endswith("Name") else
                               ch(CUSTOM_VALUES))
                        add("3L", ["3L", spid, wid, wtype, fname, val if rnd() > 0.2 else ""])

        # communications
        x = rnd()
        if x < 0.30:
            nc = 0
        elif x > 0.999:
            nc = 30 + int(rnd() * 120)
        else:
            nc = 1 + int(-math.log(1 - rnd()) * 2.3)
        for _ in range(nc):
            cid = str(nid("comm"))
            tpc = ch(topics)
            usr = ch(staff_ids)
            ct = ch(["usmail", "usmail", "usmail", "imail", "imail", "imail", "NONE", "phone"])
            resp = "usmail" if rnd() < 0.5 else "imail"
            d0 = int(LAST_DAY * (1 - rnd() ** 1.6))
            d1 = min(LAST_DAY, d0 + int(rnd() * 90))
            hh_flag = in_hh and rnd() < 0.10
            salut_c = salut if rnd() < 0.6 else fn
            subj = (ch(SUBJ) % tpc) if rnd() < 0.6 else ""
            add("2A", ["2A", spid, cid, ch(wfs) if (wfs and rnd() < 0.12) else "", "", ct, usr,
                       usr if rnd() < 0.12 else "", "C" if rnd() < 0.92 else ch(["O", "P"]),
                       DATES[d0], DATES[d1], "", DATES[d0], resp,
                       paid if resp == "usmail" else "", P[1] if resp == "imail" else "",
                       "Y" if hh_flag else "", hh_id if hh_flag else "", "", salut_c, subj])
            for _k in range(1 + (rnd() < 0.2) + (rnd() < 0.05)):
                add("2B", ["2B", spid, cid, ch(com_codes)[0], ch(["Neutral", "Neutral", "Pro", "Con"])])
            if rnd() < 0.55:
                did = ch(docids)
                add("2C", ["2C", spid, cid, "OUTGOING", FL + "%d.doc" % did, str(did), " ", ""])
            elif rnd() < 0.8:
                ext = ch(["eml", "eml", "pdf", "TIF", "doc"])
                add("2C", ["2C", spid, cid, "INCOMING", OBJ + "%s.%s" % (cid, ext), "",
                           ch(["%08x%08x.eml" % (int(rnd() * 2 ** 32), int(rnd() * 2 ** 32)), "scan%04d.pdf" % int(rnd() * 9999),
                               "%s.pdf" % P[0].lower(), "00000064.TIF"]), ""])
            if rnd() < 0.08:
                add("2C", ["2C", spid, cid, ch(["AT_IN1", "AT_OUT1"]), IMAIL + "%s_1.txt" % cid, "", ch(["Test Email.txt", "Message.txt"]), ""])
            if rnd() < 0.25:
                for k in range(1 + (rnd() < 0.2)):
                    add("2D", ["2D", spid, cid, str(k + 1), note(P, tpc, usr, date_us(DATES[d1])), "", "", ch(staff_ids)])
            if rnd() < 0.06:
                fname, kind = ch(FILL_FIELDS)
                val = (str(10000 + int(rnd() * 90000)) if kind == "num" else ch(AGENCIES) if kind == "agency" else
                       date_us(ch(DATES)) if kind == "date" else "S.%d" % (100 + int(rnd() * 3400)))
                add("2E", ["2E", spid, cid, fname, val])

        # events (public venues; a few participants from this shard)
        if rnd() < 0.012:
            eid = str(nid("ev"))
            lc = ch(LOC_POOL)
            d0 = int(LAST_DAY * (1 - rnd() ** 1.3))
            d1 = min(LAST_DAY, d0 + int(rnd() * 3))
            usr = ch(staff_ids)
            add("7A", ["7A", eid, "", ch(EVENT_DESC) % lc[0], P[0], lc[2], "", DATES[d0], "%02d:00:00" % (8 + int(rnd() * 11)),
                       DATES[d0], "%02d:00:00" % (9 + int(rnd() * 11)), ch(["EST", "CST"]),
                       "%s %s" % (lc[0], ch(VENUES)), ch(["Scheduled", "Scheduled", "Completed", "Cancelled"]),
                       "Y" if rnd() < 0.05 else "", DATES[d0], usr, DATES[d1], usr,
                       "%d %s %s" % (100 + int(rnd() * 9800), ch(STREETS), ch(SUFFIX)), " ", " ", " ",
                       lc[0], lc[1], lc[4] + "%02d" % int(rnd() * 100), "USA"])
            for _k in range(1 + (rnd() < 0.5)):
                add("7B", ["7B", eid, ch(com_codes)[0]])
            for _k in range(2 + int(rnd() * 4)):
                if rnd() < 0.8:
                    add("7C", ["7C", eid, "PERS", str(p0 + int(rnd() * npers))])
                else:
                    add("7C", ["7C", eid, "STAFF", ch(staff_ids)])
            if rnd() < 0.15:
                add("7D", ["7D", eid, "Agenda.pdf", str(100 + int(rnd() * 900)), ch(staff_ids), ch(DATES), ch(SENT)])
            for k in range(int(rnd() * 2.2) if rnd() < 0.5 else 0):
                add("7E", ["7E", eid, str(k + 1), ch(SENT), ch(DATES), tm(), usr])
            if wfs and rnd() < 0.3:
                add("7F", ["7F", eid, wfs[0]])

    sink.close()
    for fam, s in state.items():
        if s[0] >= bases[fam] + (sid + 1) * spans[fam]:
            raise RuntimeError("ID range for %s exhausted in shard %s; rerun with a different --total-records" % (fam, tag))
    return shard, dict(sink.counts), dict(sink.qcounts), {fam: s[0] for fam, s in state.items()}


# ------------------------------------------------------------------ merge
def merge(out_dir, parts_dir, nshards, max_rows, manifest_files):
    first_fix = 0
    tags = ["%04d" % i for i in range(nshards)] + ["global"]
    for rt in RT_ORDER:
        parts = [os.path.join(parts_dir, "%s.%s.part" % (rt, t)) for t in tags]
        parts = [p for p in parts if os.path.exists(p) and os.path.getsize(p) > 0]
        if not parts:
            continue
        need = FIELD_COUNTS[rt]
        file_no, rows_in, fh, name, files = 0, 0, None, None, []
        data_rows_in = 0
        total_rows = 0

        def open_next():
            nonlocal file_no, fh, name, rows_in, data_rows_in
            file_no += 1
            name = "out_%s.dat" % rt.lower() if max_rows is None else "out_%s_%03d.dat" % (rt.lower(), file_no)
            fh = open(os.path.join(out_dir, name), "wb")
            rows_in = data_rows_in = 0

        def close_cur():
            if fh:
                fh.close()
                files.append({"name": name, "rows": data_rows_in, "bytes": os.path.getsize(os.path.join(out_dir, name))})

        open_next()
        fresh = True
        for p in parts:
            with open(p, "rb") as f:
                for line in f:
                    has_tab = b"\t" in line
                    if max_rows is not None and rows_in >= max_rows and has_tab:
                        close_cur()
                        open_next()
                        fresh = True
                    if fresh:
                        if not has_tab:
                            continue            # never start a file with a blank/separator line
                        body = line.rstrip(b"\r\n")
                        fixed = body.translate(None, b",;|")
                        have = fixed.count(b"\t") + 1
                        if have < need:
                            fixed += b"\t" * (need - have)
                        if fixed != body:
                            first_fix += 1
                            line = fixed + b"\r\n"
                        fresh = False
                    fh.write(line)
                    rows_in += 1
                    if has_tab:
                        data_rows_in += 1
                        total_rows += 1
        close_cur()
        manifest_files[rt] = {"fields": need, "total_rows": total_rows, "files": files}
    return first_fix


# ------------------------------------------------------------------- main
def pick_workers(n):
    return n if n and n > 0 else max(1, (os.cpu_count() or 2))


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic IQ SCDIF flat files for CCDT import testing.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--total-records", type=int, default=15000000, help="total rows across all record types (default 15,000,000)")
    ap.add_argument("--seed", default="1")
    ap.add_argument("--workers", type=int, default=0, help="worker processes (default: all CPUs)")
    ap.add_argument("--quirk-rate", type=float, default=0.005, help="fraction of rows given a real-world defect (0 disables)")
    ap.add_argument("--max-rows-per-file", type=int, default=None, help="split each record type into files of at most this many rows")
    ap.add_argument("--plan", action="store_true", help="only run the pilot and print the plan")
    a = ap.parse_args()

    t0 = time.time()
    seed = str(a.seed)
    workers = pick_workers(a.workers)
    out_dir = os.path.abspath(a.out_dir)
    parts_dir = os.path.join(out_dir, "_parts")
    log = lambda m: sys.stderr.write("[%6.1fs] %s\n" % (time.time() - t0, m))

    # 1. pilot: how many rows does one person produce?
    G = build_globals(seed, 1000)
    _init(G)
    huge = {k: 10 ** 9 for k in FAMILY_ROWS}
    pcfg = {"seed": seed, "qrate": 0.0, "parts_dir": parts_dir, "bases": BASES, "spans": huge}
    log("pilot run (%d persons)..." % PILOT_PERSONS)
    _, pc, _, _ = run_shard(("pilot", BASES["person"], PILOT_PERSONS, pcfg, False))
    pp = {rt: pc.get(rt, 0) / float(PILOT_PERSONS) for rt in RT_ORDER}
    per_person = sum(pp.values())

    # 2. size the data set
    n0 = a.total_records / per_person
    G = build_globals(seed, max(200, int(n0 * 0.004)))
    _init(G)
    gc, _ = gen_global(G, seed, parts_dir, 0.0, False)
    fixed = sum(gc.values())
    npers = max(100, int(round((a.total_records - fixed) / per_person)))
    nshards = max(1, min(400, int(math.ceil(npers / float(PERSONS_PER_SHARD)))))
    shard_size = int(math.ceil(npers / float(nshards)))
    spans = {fam: int(pp[rt] * shard_size * ((GAP + 1) / 2.0) * 1.6) + 5000 for fam, rt in FAMILY_ROWS.items()}
    est = int(npers * per_person + fixed)
    log("plan: %d persons in %d shards, %.2f rows/person, %d fixed rows, ~%d rows total" % (npers, nshards, per_person, fixed, est))
    if a.plan:
        for rt in RT_ORDER:
            sys.stderr.write("   %s  ~%d rows\n" % (rt, pp[rt] * npers + gc.get(rt, 0)))
        return 0

    # 3. generate
    if os.path.isdir(parts_dir):
        shutil.rmtree(parts_dir)
    os.makedirs(parts_dir)
    cfg = {"seed": seed, "qrate": a.quirk_rate, "parts_dir": parts_dir, "bases": BASES, "spans": spans}
    tasks = []
    for s in range(nshards):
        p0 = BASES["person"] + s * shard_size
        n = min(shard_size, BASES["person"] + npers - p0)
        if n > 0:
            tasks.append((s, p0, n, cfg, True))
    counts, qcounts, idmax = Counter(), Counter(), Counter()
    done = 0
    with mp.Pool(workers, initializer=_init, initargs=(G,)) as pool:
        for shard, c, q, ids in pool.imap_unordered(run_shard, tasks):
            counts.update(c)
            qcounts.update(q)
            for k, v in ids.items():
                idmax[k] = max(idmax[k], v)
            done += 1
            log("shard %d/%d done (%d rows so far)" % (done, len(tasks), sum(counts.values())))
    gcnt, gq = gen_global(G, seed, parts_dir, a.quirk_rate, True)
    counts.update(gcnt)
    qcounts.update(gq)

    # 4. merge
    os.makedirs(out_dir, exist_ok=True)
    for fn in os.listdir(out_dir):
        if fn.startswith("out_") and fn.endswith(".dat"):
            os.remove(os.path.join(out_dir, fn))
    log("merging part files...")
    files = {}
    first_fix = merge(out_dir, parts_dir, len(tasks), a.max_rows_per_file, files)
    shutil.rmtree(parts_dir)
    total = sum(v["total_rows"] for v in files.values())
    manifest = {
        "generator": "generate_scdif.py", "version": VERSION,
        "args": {"total_records": a.total_records, "seed": seed, "quirk_rate": a.quirk_rate,
                 "max_rows_per_file": a.max_rows_per_file},
        "persons": npers, "person_id_range": [BASES["person"], BASES["person"] + npers - 1],
        "max_ids": dict(idmax), "total_data_rows": total,
        "quirks_applied": dict(qcounts), "first_lines_adjusted": first_fix,
        "record_types": files,
        "notes": ["No header row in any file: import into a collection flagged as CMS so line 1 is not skipped.",
                  "total_rows counts lines containing data; blank and ******** lines are not counted.",
                  "Files are latin-1 encoded with CRLF line endings (a few LF-only rows when quirks are on)."]}
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    log("done: %d data rows in %d files, quirks: %s" % (total, sum(len(v["files"]) for v in files.values()), dict(qcounts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
