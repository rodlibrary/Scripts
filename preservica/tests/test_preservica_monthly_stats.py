"""
Offline tests for preservica_monthly_stats.py: a fake Preservica (answering
with XML shaped like Preservica 9.4's real responses) and a fake Google Sheet
laid out like DCDS_stats_2026.

    python -m unittest discover -s preservica/tests
"""

import datetime as dt
import json
import os
import re
import sys
import unittest
from collections import Counter
from unittest import mock
from urllib.parse import unquote

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


def gen(co, n, original, active, group, nfiles=1):
    b = "".join(f'<Bitstream filename="f{k}">{S}/api/entity/content-objects/{co}/generations/{n}/bitstreams/{k}'
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
    f"/api/entity/content-objects/{CO1}/generations/1": gen(CO1, 1, "true", "true", "tiff"),
    bs(CO1, 1): bit(T // 2, "AAA"),
    bs(CO1, 1) + "/storage-locations": locs(GLACIER, HOT),
    bs(CO1, 1) + "/integrity-check-history": history(("2026-09-02T00:00:00Z", "true"),
                                                     ("2026-10-03T00:00:00Z", "true")),
    f"/api/entity/content-objects/{CO2}/generations": gens(CO2, ["true"]),
    f"/api/entity/content-objects/{CO2}/generations/1": gen(CO2, 1, "true", "true", "image"),
    bs(CO2, 1): bit(1000, "BBB"),
    bs(CO2, 1) + "/storage-locations": locs(HOT),
    bs(CO2, 1) + "/integrity-check-history": history(),
    # Asset 2: PDF from last year. Original generation 1 (same SHA1 as asset 1's file),
    # migrated generation 2 is current. Both in Glacier only.
    f"/api/entity/information-objects/{IO2}/representations": rep_list(IO2, ["Preservation"]),
    f"/api/entity/information-objects/{IO2}/representations/Preservation/1": rep([CO3]),
    f"/api/entity/content-objects/{CO3}/generations": gens(CO3, ["false", "true"]),
    f"/api/entity/content-objects/{CO3}/generations/1": gen(CO3, 1, "true", "false", "pdf"),
    f"/api/entity/content-objects/{CO3}/generations/2": gen(CO3, 2, "false", "true", "pdf"),
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
                  "xip.format_group_r_Preservation": "tiff", pms.DOWNLOAD_FIELD: "10"}),
           (IO2, {"xip.created": OLD, "xip.top_level_so": C2, "xip.security_descriptor": "closed",
                  "xip.format_group_r_Preservation": "pdf", pms.DOWNLOAD_FIELD: "5"})],
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


class FakeSheets:
    def __init__(self, tabs, title="DCDS_stats_2026"):
        self.tabs, self.title, self.writes = tabs, title, 0

    def request(self, method, url, timeout=None, params=None, json=None):
        path = unquote(url.split("/spreadsheets/", 1)[1])
        if method == "GET" and "/values/" not in path:
            return Resp(200, {"properties": {"title": self.title},
                              "sheets": [{"properties": {"title": t, "sheetId": i}} for i, t in enumerate(self.tabs)]})
        if method == "GET":
            tab = re.search(r"/values/'(.+)'!", path)[1]
            return Resp(200, {"values": [list(r) for r in self.tabs[tab]]})
        if path.endswith("values:batchUpdate"):
            self.writes += 1
            for d in json["data"]:
                tab, ref = re.fullmatch(r"'(.+)'!(.+)", d["range"]).groups()
                r0, c0 = a1(ref)
                g = self.tabs[tab]
                for i, row in enumerate(d["values"]):
                    for j, v in enumerate(row):
                        while len(g) <= r0 + i:
                            g.append([])
                        while len(g[r0 + i]) <= c0 + j:
                            g[r0 + i].append("")
                        g[r0 + i][c0 + j] = v
            return Resp(200, {})
        raise AssertionError(f"unexpected {method} {path}")


def find(grid, category, metric):
    cat = None
    for r, row in enumerate(grid):
        a = row[0] if row else ""
        b = row[1] if len(row) > 1 else ""
        if a == category:
            cat = r
        if cat is not None and r >= cat and b == metric and (r == cat or not a):
            return row
        if cat is not None and r > cat and (a or not b):
            cat = None
    raise AssertionError(f"{category} / {metric} not found")


OCT = 11      # column L


class MonthlyStatsTest(unittest.TestCase):
    def run_script(self, tabs, *argv, sheets=None, month="2026-10"):
        fake_p = FakePreservica()
        sheets = sheets or FakeSheets(tabs)
        env = {"PRESERVICA_USERNAME": "svc", "PRESERVICA_PASSWORD": "pw", "PRESERVICA_TENANT": "UNIOWA",
               "PRESERVICA_SERVER": "us.preservica.com", "GOOGLE_SHEET_ID": "sheet1",
               "GOOGLE_SERVICE_ACCOUNT_KEY": json.dumps({"type": "service_account", "client_email": "bot@x"})}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(pms.requests, "Session", return_value=fake_p), \
                mock.patch("google.oauth2.service_account.Credentials.from_service_account_info"), \
                mock.patch("google.auth.transport.requests.AuthorizedSession", return_value=sheets), \
                mock.patch.object(sys, "argv", ["x", "--month", month, *argv]):
            pms.main()
        return sheets, fake_p

    def test_fills_all_three_tabs(self):
        tabs = grid_from_layout()
        sheets, fake = self.run_script(tabs)
        p, st, dc = tabs["Preservica"], tabs["Storage"], tabs["DCDS"]
        self.assertEqual(p[0][2], "January")
        self.assertEqual(p[1][:2], ["Category", "Metric"])
        v = lambda c, m: find(p, c, m)[OCT]
        self.assertEqual(v("Content", "#Collections"), 2)
        self.assertEqual(v("Content", "#CollectionsAdded"), 1)
        self.assertEqual(v("Content", "#Assets"), 2)
        self.assertEqual(v("Content", "#AssetsAdded"), 1)
        self.assertEqual(v("Content", "#Files"), 4)
        self.assertEqual(v("AssetsByKind", "#Images"), 1)
        self.assertEqual(v("AssetsByKind", "#Text"), 1)
        self.assertEqual(v("AssetsAddedByKind", "#ImagesAdded"), 1)
        self.assertEqual(v("AssetsAddedByKind", "#TextAdded"), 0)
        # Storage: CO1 0.5 TB in Glacier and Hot, CO2 1000 bytes Hot, CO3 0.25 + 0.25 TB Glacier.
        self.assertEqual(v("Storage", "#TBCold"), 1.0)
        self.assertEqual(v("Storage", "#TBHot"), 0.5)
        self.assertEqual(v("Storage", "#TBStored"), 1.5)
        self.assertEqual(v("Storage", "#TBContent"), 1.0)
        self.assertEqual(v("Storage", "#TBAdded"), 0.5)
        self.assertEqual(v("Storage", "#TBPreservation"), 1.0)
        self.assertEqual(v("Storage", "#TBOlderGenerations"), 0.25)
        self.assertEqual(v("Storage", "#TBMigratedFiles"), 0.25)
        self.assertEqual(v("Storage", "#DuplicateFiles"), 1)
        self.assertEqual(v("Storage", "#TBDuplicates"), 0.25)
        self.assertEqual(v("Tiers", "#FilesHotAndCold"), 1)
        self.assertEqual(v("Tiers", "#FilesColdOnly"), 2)
        self.assertEqual(v("Tiers", "#FilesHotOnly"), 1)
        self.assertEqual(v("StorageLocations", f"#TB {GLACIER} (Cold)"), 1.0)
        self.assertEqual(v("StorageLocations", f"#TB {HOT} (Hot)"), 0.5)
        self.assertEqual(v("TBByCollection", "#TB Uprising"), 0.5)
        self.assertEqual(v("TBByCollection", "#TB Old Photos"), 0.5)
        self.assertEqual(v("SecurityTags", "#Assets open"), 1)
        self.assertEqual(v("Integrity", "#FilesCheckedThisMonth"), 2)
        self.assertEqual(v("Integrity", "#FilesFailedLastCheck"), 1)
        self.assertEqual(v("Integrity", "#FilesNeverChecked"), 1)
        self.assertEqual(v("Access", "#DownloadsAllTime"), 15)
        self.assertEqual(find(p, "Access", "#Downloads")[OCT], "")   # no September figure to compare with
        self.assertTrue(find(p, "Storage", "#TBHot")[14].startswith("=IFERROR(LOOKUP"))
        self.assertTrue(find(p, "Content", "#AssetsAdded")[14].startswith("=SUM(C"))
        # Storage tab: October filled, nothing else changed.
        self.assertEqual(find(st, "Tier 1", "#TBPreservicaHot")[OCT], 0.5)
        self.assertEqual(find(st, "Tier 1", "#TBPreservicaCold")[OCT], 1.0)
        self.assertEqual(len(st), 6)
        # DCDS tab: the Preservica block, not the Institutional Repository's #Downloads.
        self.assertEqual(find(dc, "Preservica", "#Collections")[OCT], 1)
        self.assertEqual(find(dc, "Preservica", "#Assets")[OCT], 1)
        self.assertEqual(find(dc, "Preservica", "#Images")[OCT], 1)
        self.assertEqual(find(dc, "Preservica", "#Text")[OCT], 0)
        self.assertEqual(len(find(dc, "InstitutionalRepository", "#Downloads")), 3)
        self.assertEqual(len(find(dc, "Preservica", "#Downloads")), 2)
        self.assertEqual(len(dc), 14)
        self.assertEqual(sheets.writes, 3)
        # The shared file under both representations is measured once.
        self.assertEqual(fake.calls[bs(CO1, 1)], 1)

    def test_second_month_keeps_layout_and_computes_downloads(self):
        tabs = grid_from_layout()
        sheets = FakeSheets(tabs)
        self.run_script(tabs, sheets=sheets)
        p = tabs["Preservica"]
        rows_before = [r[:2] for r in p]
        find(p, "Access", "#DownloadsAllTime")[OCT] = 9          # pretend October was 9
        SEARCH["IO"].append(("33333333-3333-3333-3333-333333333333",
                             {"xip.created": "2026-11-02T00:00:00Z", "xip.top_level_so": C1,
                              "xip.security_descriptor": "public", "xip.format_group_r_Preservation": "wav"}))
        try:
            self.run_script(tabs, sheets=sheets, month="2026-11")
        finally:
            SEARCH["IO"].pop()
        NOV = 12
        self.assertEqual(find(p, "Access", "#Downloads")[NOV], 15 - 9)
        self.assertEqual(find(p, "AssetsAddedByKind", "#SoundRecordingsAdded")[NOV], 1)
        self.assertEqual(find(p, "SecurityTags", "#Assets public")[NOV], 1)
        self.assertEqual(find(p, "Content", "#Assets")[OCT], 2)            # October left alone
        # New rows were added inside their categories; the old rows are all still there in order.
        labels = [r[:2] for r in p]
        self.assertEqual([x for x in labels if x in rows_before], rows_before)
        sec = [i for i, r in enumerate(labels) if r[1].startswith("#Assets ") and "(" not in r[1]]
        self.assertEqual(len(sec), 3)
        self.assertEqual(sec, list(range(sec[0], sec[0] + 3)))
        self.assertEqual(find(tabs["DCDS"], "Preservica", "#Downloads")[NOV], 6)
        self.assertEqual(find(tabs["DCDS"], "Preservica", "#SoundRecordings")[NOV], 1)

    def test_wrong_year_spreadsheet_stops_before_measuring(self):
        sheets = FakeSheets(grid_from_layout(), title="DCDS_stats_2026")
        with self.assertRaises(SystemExit) as e:
            env = {"GOOGLE_SHEET_ID": "s", "GOOGLE_SERVICE_ACCOUNT_KEY": json.dumps({"type": "service_account"})}
            with mock.patch.dict(os.environ, env), \
                    mock.patch("google.oauth2.service_account.Credentials.from_service_account_info"), \
                    mock.patch("google.auth.transport.requests.AuthorizedSession", return_value=sheets), \
                    mock.patch.object(sys, "argv", ["x", "--month", "2027-01"]):
                pms.main()
        self.assertIn("2027", str(e.exception))

    def test_report_month_late_on_the_first_counts_as_previous_month(self):
        class Fixed(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2026, 11, 1, 0, 30, tzinfo=tz)
        with mock.patch.object(pms.dt, "datetime", Fixed):
            self.assertEqual(pms.report_month(None), (2026, 10))
        self.assertEqual(pms.report_month("2026-03"), (2026, 3))

    def test_kinds(self):
        self.assertEqual(pms.kind_of("pdf"), "Text")
        self.assertEqual(pms.kind_of("image"), "Images")
        self.assertEqual(pms.kind_of("audio"), "SoundRecordings")
        self.assertEqual(pms.kind_of("video"), "MovingImages")
        self.assertEqual(pms.kind_of(""), "Other")

    def test_parse_date(self):
        self.assertEqual(pms.parse_date("2026-10-12T21:27:42Z").month, 10)
        self.assertEqual(pms.parse_date("1760304462000").year, 2025)
        self.assertEqual(pms.parse_date(["2026-10-12T21:27:42.123+00:00"]).day, 12)
        self.assertIsNone(pms.parse_date(""))


if __name__ == "__main__":
    unittest.main()
