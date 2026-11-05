"""
Offline tests for preservica_monthly_stats.py: a fake Preservica answering
with XML shaped like Preservica 9.4's real responses.

    python -m unittest discover -s preservica/tests
"""

import contextlib
import csv
import datetime as dt
import io
import tempfile
import json
import os
import re
import sys
import unittest
from collections import Counter
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import preservica_monthly_stats as pms  # noqa: E402

S = "https://us.preservica.com"
NS = 'xmlns="http://preservica.com/EntityAPI/v9.4" xmlns:xip="http://preservica.com/XIP/v9.4"'
T = 10 ** 12
NOW = "2026-10-15T12:00:00Z"
OLD = "2025-03-01T12:00:00Z"


class Resp:
    def __init__(self, code, body):
        self.status_code = code
        self.content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


def rep_list(io, types):
    reps = "".join(f'<Representation type="{t}">{S}/api/entity/information-objects/{io}/representations/{t}/1'
                   f'</Representation>' for t in types)
    return f'<RepresentationsResponse {NS}><Representations>{reps}</Representations></RepresentationsResponse>'


def rep(cos):
    xip = "".join(f"<xip:ContentObject>{c}</xip:ContentObject>" for c in cos)
    ent = "".join(f'<ContentObject title="x" ref="{c}" type="CO">{S}/api/entity/content-objects/{c}</ContentObject>'
                  for c in cos)
    return (f'<RepresentationResponse {NS}><xip:Representation><xip:ContentObjects>{xip}</xip:ContentObjects>'
            f'</xip:Representation><ContentObjects>{ent}</ContentObjects></RepresentationResponse>')


def gens(co, actives):
    g = "".join(f'<Generation active="{a}">{S}/api/entity/content-objects/{co}/generations/{i + 1}</Generation>'
                for i, a in enumerate(actives))
    return f'<GenerationsResponse {NS}><Generations>{g}</Generations></GenerationsResponse>'


def gen(co, n, original, active, group, nfiles=1, name="f"):
    b = "".join(f'<Bitstream filename="{name}">{S}/api/entity/content-objects/{co}/generations/{n}/bitstreams/{k}'
                f'</Bitstream>' for k in range(1, nfiles + 1))
    return (f'<GenerationResponse {NS}><xip:Generation original="{original}" active="{active}">'
            f'<xip:ContentObject>{co}</xip:ContentObject><xip:FormatGroup>{group}</xip:FormatGroup>'
            f'<xip:Bitstreams><xip:Bitstream>f1</xip:Bitstream></xip:Bitstreams></xip:Generation>'
            f'<Bitstreams>{b}</Bitstreams></GenerationResponse>')


def bit(size, sha):
    return (f'<BitstreamResponse {NS}><xip:Bitstream><xip:Filename>f</xip:Filename><xip:FileSize>{size}'
            f'</xip:FileSize><xip:Fixities><xip:Fixity><xip:FixityAlgorithmRef>SHA1</xip:FixityAlgorithmRef>'
            f'<xip:FixityValue>{sha}</xip:FixityValue></xip:Fixity></xip:Fixities></xip:Bitstream>'
            f'</BitstreamResponse>')


def locs(*names):
    a = "".join(f'<StorageLocation><Adapter name="{n}"/></StorageLocation>' for n in names)
    return f'<StorageLocationsResponse {NS}><StorageLocations>{a}</StorageLocations></StorageLocationsResponse>'


def history(*checks):
    h = "".join(f"<IntegrityCheckHistory><xip:Type>x</xip:Type><xip:Success>{ok}</xip:Success>"
                f"<xip:Date>{d}</xip:Date></IntegrityCheckHistory>" for d, ok in checks)
    return f'<IntegrityCheckHistoryResponse {NS}><IntegrityCheckHistories>{h}</IntegrityCheckHistories>' \
           f'<Paging><TotalResults>1</TotalResults></Paging></IntegrityCheckHistoryResponse>'


IO1, IO2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
CO1, CO2, CO3 = ("aaaaaaaa-0000-0000-0000-00000000000" + str(i) for i in (1, 2, 3))
C1, C2 = "c1c1c1c1-0000-0000-0000-000000000000", "c2c2c2c2-0000-0000-0000-000000000000"
GLACIER, HOT = "S3 Deep Glacier Adapter", "S3 Standard Adapter"


def bs(co, g, k=1):
    return f"/api/entity/content-objects/{co}/generations/{g}/bitstreams/{k}"


PAGES = {
    "/api/entity/root/children": f'<ChildrenResponse {NS}><Children>'
        f'<Child title="Uprising" ref="{C1}" type="SO">{S}/x</Child>'
        f'<Child title="Old Photos" ref="{C2}" type="SO">{S}/y</Child>'
        f'<Child title="loose" ref="{IO1}" type="IO">{S}/z</Child></Children></ChildrenResponse>',
    # Asset 1: image, added this month. Preservation CO1 (one file, Hot and Cold), Access CO2 (Hot).
    f"/api/entity/information-objects/{IO1}/representations": rep_list(IO1, ["Preservation", "Access"]),
    f"/api/entity/information-objects/{IO1}/representations/Preservation/1": rep([CO1]),
    f"/api/entity/information-objects/{IO1}/representations/Access/1": rep([CO2]),
    f"/api/entity/content-objects/{CO1}/generations": gens(CO1, ["true"]),
    f"/api/entity/content-objects/{CO1}/generations/1": gen(CO1, 1, "true", "true", "tiff", name="Scan_001.TIF"),
    bs(CO1, 1): bit(T // 2, "AAA"),
    bs(CO1, 1) + "/storage-locations": locs(GLACIER, HOT),
    bs(CO1, 1) + "/integrity-check-history": history(("2026-09-02T00:00:00Z", "true"),
                                                     ("2026-10-03T00:00:00Z", "true")),
    f"/api/entity/content-objects/{CO2}/generations": gens(CO2, ["true"]),
    f"/api/entity/content-objects/{CO2}/generations/1": gen(CO2, 1, "true", "true", "image", name="Scan_001.jpg"),
    bs(CO2, 1): bit(1000, "BBB"),
    bs(CO2, 1) + "/storage-locations": locs(HOT),
    bs(CO2, 1) + "/integrity-check-history": history(),
    # Asset 2: PDF from last year. Original generation 1 (same SHA1 as asset 1's file),
    # migrated generation 2 is current. Both in Glacier only.
    f"/api/entity/information-objects/{IO2}/representations": rep_list(IO2, ["Preservation"]),
    f"/api/entity/information-objects/{IO2}/representations/Preservation/1": rep([CO3]),
    f"/api/entity/content-objects/{CO3}/generations": gens(CO3, ["false", "true"]),
    f"/api/entity/content-objects/{CO3}/generations/1": gen(CO3, 1, "true", "false", "pdf", name="Interview.wav"),
    f"/api/entity/content-objects/{CO3}/generations/2": gen(CO3, 2, "false", "true", "pdf", name="Interview.mp3"),
    bs(CO3, 1): bit(T // 4, "aaa"),
    bs(CO3, 1) + "/storage-locations": locs(GLACIER),
    bs(CO3, 1) + "/integrity-check-history": history(("2026-08-01T00:00:00Z", "false")),
    bs(CO3, 2): bit(T // 4, "CCC"),
    bs(CO3, 2) + "/storage-locations": locs(GLACIER),
    bs(CO3, 2) + "/integrity-check-history": history(("2026-10-20T00:00:00Z", "true")),
}

SEARCH = {
    "SO": [(C1, {"xip.created": NOW}), (C2, {"xip.created": OLD})],
    "IO": [(IO1, {"xip.created": NOW, "xip.top_level_so": C1, "xip.security_descriptor": "open",
                  "xip.format_group_r_Preservation": "tiff"}),
           (IO2, {"xip.created": OLD, "xip.top_level_so": C2, "xip.security_descriptor": "closed",
                  "xip.format_group_r_Preservation": "pdf"})],
}


class FakePreservica:
    def __init__(self):
        self.calls = Counter()

    def post(self, url, data=None, timeout=None):
        assert url.endswith("/api/accesstoken/login") and data["tenant"] == "UNIOWA"
        return Resp(200, {"token": "tok"})

    def request(self, method, url, headers=None, timeout=None, data=None, params=None):
        assert headers["Preservica-Access-Token"] == "tok"
        path = url.replace(S, "").split("?")[0]
        self.calls[path] += 1
        if path == "/api/content/search":
            form = dict(data)
            q = json.loads(form["q"])
            assert q["q"] == "*"
            doc_type = next(f["values"][0] for f in q["fields"] if f["name"] == "xip.document_type")
            hits = SEARCH[doc_type]
            start, mx = int(form["start"]), int(form["max"])
            page = hits[start:start + mx]
            wanted = [v for k, v in data if k == "metadata"]
            return Resp(200, {"success": True, "value": {
                "totalHits": len(hits), "objectIds": [f"sdb:{doc_type}|{r}" for r, _ in page],
                "metadata": [[{"name": f, "value": m.get(f)} for f in wanted if f in m] for _, m in page]}})
        if path in PAGES:
            return Resp(200, PAGES[path])
        return Resp(404, "not found")


def grid_from_layout():
    months = ["", ""] + pms.MONTHS + ["Totals"]
    dcds = [months, ["Category", "Metric"],
            ["InstitutionalRepository", "#Downloads", 225552], ["", "#Uploads", 92], [],
            ["Preservica", "#Collections"], ["", "#Assets"], ["", "#Images"], ["", "#SoundRecordings"],
            ["", "#MovingImages"], ["", "#Text"], ["", "#Downloads"], [],
            ["Reference", "#WebPageVisits", 138769]]
    storage = [months, ["Category", "Metric"],
               ["Tier 1", "#TBPreservicaHot", "", "", "", "", "", "", "", "", "", 0.03],
               ["", "#TBPreservicaCold", "", "", "", "", "", "", "", "", "", 0.03], [],
               ["Tier2", "#TBAWS"]]
    return {"DCDS": dcds, "Storage": storage, "Preservica": []}


def a1(ref):
    m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", ref)
    col = lambda s: sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(s))) - 1
    return int(m[2]) - 1, col(m[1])



def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


class MonthlyStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def run_script(self, *argv, month="2026-10"):
        fake = FakePreservica()
        env = {"PRESERVICA_USERNAME": "svc", "PRESERVICA_PASSWORD": "pw", "PRESERVICA_TENANT": "UNIOWA",
               "PRESERVICA_SERVER": "us.preservica.com"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(pms.requests, "Session", return_value=fake), \
                mock.patch.object(sys, "argv", ["x", "--month", month, *argv]), contextlib.redirect_stdout(out):
            pms.main()
        return fake, out.getvalue()

    def figures(self, path="preservica_stats_2026-10.csv"):
        rows, tab, cat, got = read_csv(path), None, None, {}
        self.assertEqual(rows[0], ["Tab", "Category", "Metric", "October 2026"])
        for t, c, metric, v in rows[1:]:
            tab, cat = t or tab, c or (cat if t == "" or t == tab else c)
            got[(t, c or cat, metric)] = v
        return got

    def test_csv_has_every_figure(self):
        fake, out = self.run_script()
        f = self.figures()
        v = lambda tab, cat, metric: f[(tab, cat, metric)]
        # DCDS rows: added in October (asset 1 is from October, asset 2 from 2025).
        self.assertEqual(v("DCDS", "Preservica", "#Collections"), "1")
        self.assertEqual(v("DCDS", "Preservica", "#Assets"), "1")
        self.assertEqual(v("DCDS", "Preservica", "#Images"), "1")
        self.assertEqual(v("DCDS", "Preservica", "#Text"), "0")
        self.assertNotIn(("DCDS", "Preservica", "#Downloads"), f)
        # Storage tab, 2 decimals: Hot 0.5 TB (one file), Cold 1.0 TB (three files).
        self.assertEqual(v("Storage", "Tier 1", "#TBPreservicaHot"), "0.5")
        self.assertEqual(v("Storage", "Tier 1", "#TBPreservicaCold"), "1.0")
        # Preservica tab.
        self.assertEqual(v("Preservica", "Content", "#Assets"), "2")
        self.assertEqual(v("Preservica", "Content", "#Files"), "4")
        # Kinds come from the current Preservation file's extension: the .mp3 is a sound recording
        # although Preservica's format group says pdf.
        self.assertEqual(v("Preservica", "AssetsByKind", "#Images"), "1")
        self.assertEqual(v("Preservica", "AssetsByKind", "#SoundRecordings"), "1")
        self.assertEqual(v("Preservica", "AssetsByKind", "#Text"), "0")
        self.assertEqual(v("Preservica", "AssetsByFileType", "#Assets .tif"), "1")
        self.assertEqual(v("Preservica", "AssetsByFileType", "#Assets .mp3"), "1")
        self.assertEqual(v("Preservica", "FilesByFileType", "#Files .wav"), "1")
        self.assertEqual(v("Preservica", "TBByFileType", "#TB .tif"), "0.5")
        self.assertEqual(v("Preservica", "Storage", "#TBStored"), "1.5")
        self.assertEqual(v("Preservica", "Storage", "#TBOlderGenerations"), "0.25")
        self.assertEqual(v("Preservica", "Storage", "#DuplicateFiles"), "1")
        self.assertEqual(v("Preservica", "Tiers", "#FilesHotAndCold"), "1")
        self.assertEqual(v("Preservica", "TBByCollection", "#TB Uprising"), "0.5")
        self.assertEqual(v("Preservica", "Integrity", "#FilesCheckedThisMonth"), "2")
        self.assertEqual(v("Preservica", "Integrity", "#FilesNeverChecked"), "1")
        self.assertIn("#Assets .mp3", out)                        # the figures are printed too
        self.assertEqual(fake.calls[bs(CO1, 1)], 1)               # a file shared by two representations: read once

    def test_paste_file_lines_up_with_the_sheet(self):
        self.run_script()
        with open("preservica_stats_2026-10_paste.txt", encoding="utf-8") as f:
            text = f.read()
        blocks = text.split("\n== ")
        dcds = blocks[1].splitlines()[1:]
        self.assertEqual(dcds[:6], ["1", "1", "1", "0", "0", "0"])   # #Collections ... #Text
        storage = blocks[2].splitlines()[1:3]
        self.assertEqual(storage, ["0.5", "1.0"])
        first = blocks[3].splitlines()[1]
        cols = first.split("\t")
        self.assertEqual(cols[:2], ["Content", "#Collections"])
        self.assertEqual(len(cols), 2 + 10)                          # October = the 10th month column (L)
        self.assertEqual(cols[-1], "2")
        values = [l for l in blocks[4].splitlines()[1:] if l != ""]
        firsts = [l for l in blocks[3].splitlines()[1:] if l]
        self.assertEqual(len(values), len(firsts))

    def test_total_counts_and_options(self):
        self.run_script("--dcds-counts", "total", "--no-integrity", "--workers", "1", "--output", "x")
        f = self.figures("x.csv")
        self.assertEqual(f[("DCDS", "Preservica", "#Assets")], "2")
        self.assertEqual(f[("DCDS", "Preservica", "#Collections")], "2")
        self.assertFalse(any(k[1] == "Integrity" for k in f))

    def test_report_month_late_on_the_first_counts_as_previous_month(self):
        class Fixed(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2026, 11, 1, 0, 30, tzinfo=tz)
        with mock.patch.object(pms.dt, "datetime", Fixed):
            self.assertEqual(pms.report_month(None), (2026, 10))
        self.assertEqual(pms.report_month("2026-03"), (2026, 3))

    def test_kinds(self):
        self.assertEqual(pms.ext_kind("tif"), "Images")
        self.assertEqual(pms.ext_kind("pdf"), "Text")
        self.assertEqual(pms.ext_kind("wav"), "SoundRecordings")
        self.assertEqual(pms.ext_kind("mov"), "MovingImages")
        self.assertEqual(pms.ext_kind("zip"), "")
        self.assertEqual(pms.ext_of("A.B.TIFF"), "tiff")
        self.assertEqual(pms.kind_of("pdf"), "Text")
        self.assertEqual(pms.kind_of(""), "Other")

    def test_parse_date(self):
        self.assertEqual(pms.parse_date("2026-10-12T21:27:42Z").month, 10)
        self.assertEqual(pms.parse_date(1760304462000).year, 2025)
        self.assertEqual(pms.parse_date(["2026-10-12T21:27:42.123+00:00"]).day, 12)
        self.assertIsNone(pms.parse_date(""))


if __name__ == "__main__":
    unittest.main()
