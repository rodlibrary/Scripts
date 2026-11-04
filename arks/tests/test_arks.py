"""
Offline tests for generate_arks.py and match_preservica.py, with a fake
GitLab and a fake Preservica (MODS laid out like Rod Library's template).

    python3 -m unittest discover -s arks/tests
"""

import base64
import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import generate_arks as ga  # noqa: E402
import match_preservica as mp  # noqa: E402

GL = "https://git.uni.edu/api/v4/projects/rod-library%2Fresolver/repository/files/"
PV = "https://us.preservica.com"
NS = 'xmlns="http://preservica.com/EntityAPI/v9.4" xmlns:xip="http://preservica.com/XIP/v9.4"'


class Resp(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))


class FakeGitLab:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.pushes = []

    def handle(self, req):
        url = req.full_url
        if not url.startswith(GL):
            return None
        assert req.get_header("Private-token") == "tok"
        path = urllib.parse.unquote(url[len(GL):].split("?")[0])
        if path.endswith("/raw"):
            path = path[:-4]
            if path not in self.files:
                raise http_error(url, 404)
            return Resp(self.files[path])
        if req.get_method() == "GET":
            if path not in self.files:
                raise http_error(url, 404)
            return Resp("{}")
        body = json.loads(req.data)
        self.files[path] = base64.b64decode(body["content"]).decode()
        self.pushes.append((req.get_method(), path, body["commit_message"]))
        return Resp("{}", 201)


def mods(blade, url, src="17.01_x"):
    ident = f">{blade}</identifier>" if blade else "/>"
    loc = f"<url>{url}</url>" if url else "<url/>"
    return (f'<MetadataResponse {NS}><xip:MetadataContainer><xip:Content><mods version="3.8" xmlns="http://www.loc.gov/mods/v3">\n'
            f'    <identifier displayLabel="Archival Resource Key" type="ark"{ident}\n'
            f'    <identifier displayLabel="Source ID" type="local">{src}</identifier>\n'
            f'    <location><physicalLocation displayLabel="Repository" type="original">Rod Library</physicalLocation>\n'
            f'      <physicalLocation displayLabel="Location (URL)" type="url">\n        {loc}\n      </physicalLocation></location>\n'
            f'    <relatedItem displayLabel="Finding Aid" type="url"><location><url>https://aspace.example/1</url></location></relatedItem>\n'
            f'</mods></xip:Content></xip:MetadataContainer></MetadataResponse>')


def uid(n):
    return f"{n:08d}-1111-4111-8111-111111111111"


def text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FakePreservica:
    def __init__(self, records):
        self.records = records          # {n: (blade, url)} ; None = no MODS
        self.logins = 0

    def handle(self, req):
        url = req.full_url
        if not url.startswith(PV):
            return None
        path = url[len(PV):]
        if path == "/api/accesstoken/login":
            self.logins += 1
            return Resp(json.dumps({"token": "T"}))
        assert req.get_header("Preservica-access-token") == "T"
        if path == "/api/content/search":
            form = urllib.parse.parse_qs(req.data.decode())
            assert json.loads(form["q"][0])["q"] == "*"
            start, mx = int(form["start"][0]), int(form["max"][0])
            ids = sorted(self.records)[start:start + mx]
            return Resp(json.dumps({"value": {"totalHits": len(self.records),
                                              "objectIds": [f"sdb:IO|{uid(n)}" for n in ids],
                                              "metadata": [[{"name": "xip.title", "value": f"Title {n}"}] for n in ids]}}))
        for n, rec in self.records.items():
            if path == f"/api/entity/information-objects/{uid(n)}":
                frag = (f'<Metadata><Fragment schema="http://www.loc.gov/mods/v3">{PV}/api/entity/information-objects/{uid(n)}/metadata/m</Fragment></Metadata>'
                        if rec is not None else "")
                return Resp(f"<EntityResponse {NS}><AdditionalInformation>{frag}</AdditionalInformation></EntityResponse>")
            if path == f"/api/entity/information-objects/{uid(n)}/metadata/m":
                return Resp(mods(*rec))
        raise http_error(url, 404, b"not found")


def fake_net(*servers):
    def urlopen(req, timeout=None):
        for s in servers:
            r = s.handle(req)
            if r is not None:
                return r
        raise AssertionError(f"unexpected request {req.full_url}")
    return mock.patch("urllib.request.urlopen", side_effect=urlopen)


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


RESOLVE_HEADER = list(ga.RESOLVE_FIELDS)
MASTER_HEADER = list(ga.MASTER_LIST_FIELDS)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"GITLAB_TOKEN": "tok", "PRESERVICA_USERNAME": "svc",
                                                "PRESERVICA_PASSWORD": "pw", "GITHUB_STEP_SUMMARY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def main(self, module, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["x", *argv]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            module.main()
        return out.getvalue()


class GenerateArksTest(Base):
    def test_push_pulls_and_merges_gitlab_copies_and_writes_resolve_csv(self):
        gitlab = FakeGitLab({
            "ark_master_list.csv": "timestamp,ark,ark_blade,naan,shoulder,identifier,title,source_file,row\r\n"
                                   "2026-10-01T10:00:00,ark:/32498/s3remote01,remote01,32498,s3,r1,Remote,old.csv,2\r\n",
            "resolve.csv": "dc_path,dc_article,ark,preservica_ref,target_url,status,notes\r\n"
                           "etd/1,etd/11,32498/s3remote01,IO_" + uid(9) + ",https://x/1,active,\r\n",
        })
        # A local resolve.csv that is out of date (no Preservica reference) plus a row GitLab lacks.
        write("resolve.csv", RESOLVE_HEADER, [["etd/1", "etd/11", "32498/s3remote01", "", "https://x/1", "active", ""],
                                              ["etd/2", "", "32498/s3local0001", "", "", "unmatched", ""]])
        write("arks.csv", ["identifier", "title", "shoulder", "url"], [["n1", "New one", "s3", "https://y/1"]])
        with fake_net(gitlab):
            out = self.main(ga, "out", "--push-to-gitlab")
        self.assertIn("Pulled resolve.csv from GitLab (1 row(s)); 1 local row(s) not yet in GitLab kept.", out)
        rows = read("resolve.csv")
        self.assertEqual([r["ark"] for r in rows][:2], ["32498/s3remote01", "32498/s3local0001"])
        self.assertEqual(rows[0]["preservica_ref"], "IO_" + uid(9))       # GitLab's newer value kept
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[2]["ark"].startswith("32498/s3"))
        self.assertEqual(rows[2]["target_url"], "https://y/1")
        # The new ARK can't reuse the blade minted elsewhere, and both files were pushed to the resolver.
        self.assertEqual({p[1] for p in gitlab.pushes}, {"ark_master_list.csv", "resolve.csv"})
        self.assertIn("32498/s3remote01", gitlab.files["resolve.csv"])
        self.assertIn("IO_" + uid(9), gitlab.files["resolve.csv"])
        self.assertEqual(len(read("ark_master_list.csv")), 2)
        self.assertFalse(os.path.exists("redirects.csv"))

    def test_first_run_copies_redirects_csv(self):
        write("redirects.csv", RESOLVE_HEADER, [["etd/1", "", "32498/abc", "", "https://x", "active", ""]])
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        out = self.main(ga, "out", "--no-master-list")
        self.assertIn("resolve.csv replaces redirects.csv", out)
        rows = read("resolve.csv")
        self.assertEqual([r["ark"] for r in rows][0], "32498/abc")
        self.assertEqual(len(rows), 2)

    def test_missing_token_mints_nothing(self):
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with mock.patch.dict(os.environ, {"GITLAB_TOKEN": ""}), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-gitlab")
        self.assertIn("nothing was minted", str(e.exception))
        self.assertFalse(os.path.exists("ark_master_list.csv"))

    def test_pull_failure_mints_nothing(self):
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])

        def down(req, timeout=None):
            raise urllib.error.URLError("no route")
        with mock.patch("urllib.request.urlopen", side_effect=down), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-gitlab")
        self.assertIn("nothing was minted", str(e.exception))
        self.assertFalse(os.path.exists("resolve.csv"))


class MatchTest(Base):
    def setUp(self):
        super().setUp()
        write("resolve.csv", RESOLVE_HEADER, [
            ["", "", "32498/s3abc123", "", "", "unmatched", ""],          # 1: blade without shoulder + URL
            ["etd/2", "", "32498/xyz", "", "", "", "old note"],            # 2: bare blade
            ["", "", "32498/dup", "", "", "unmatched", ""],                # 3: in two assets
            ["", "", "32498/taken", "IO_" + uid(8), "https://dc/4", "active", ""],  # 4: other ref already
            ["", "", "32498/withdrawn1", "", "", "withdrawn", ""],         # 5: withdrawn stays withdrawn
            ["", "", "32498/keep", "", "https://dc/6", "active", ""],      # 6: has a target already
        ])
        write("ark_master_list.csv", MASTER_HEADER, [["t", "ark:/32498/s3abc123", "abc123", "32498", "s3", "i", "t", "f", "2"]])
        self.pv = FakePreservica({
            1: ("abc123", "https://n2t.net/ark:/32498/s3abc123"),
            2: ("xyz", ""),
            3: ("dup", ""), 4: ("", "https://n2t.net/ark:/32498/dup"),
            5: ("taken", ""),
            6: ("notminted", ""),
            7: ("", "https://n2t.net/ark:/32498/withdrawn1"),
            8: ("ke-ep", ""),
            9: None,                      # no MODS record
            10: ("", ""),                 # empty ARK fields, like the real template
            11: ("abc123", "https://n2t.net/ark:/32498/xyz"),   # blade and URL disagree
        })

    def test_match_updates_only_unambiguous_rows(self):
        with fake_net(self.pv):
            out = self.main(mp, "--workers", "2")
        rows = read("resolve.csv")
        r = {row["ark"]: row for row in rows}
        portal = "https://uni.access.preservica.com/uncategorized/IO_{}/"
        # 1 and 2 are each claimed by asset 11 as well (blade vs URL disagree), so asset 11 is a conflict only.
        self.assertEqual(r["32498/s3abc123"]["preservica_ref"], "IO_" + uid(1))
        self.assertEqual(r["32498/s3abc123"]["target_url"], portal.format(uid(1)))
        self.assertEqual(r["32498/s3abc123"]["status"], "active")
        self.assertIn("Preservica IO_" + uid(1) + " matched", r["32498/s3abc123"]["notes"])
        self.assertEqual(r["32498/xyz"]["preservica_ref"], "IO_" + uid(2))
        self.assertEqual(r["32498/xyz"]["status"], "active")
        self.assertTrue(r["32498/xyz"]["notes"].startswith("old note; "))
        self.assertEqual(r["32498/dup"]["preservica_ref"], "")                # in two assets: left alone
        self.assertEqual(r["32498/taken"]["preservica_ref"], "IO_" + uid(8))   # different ref: left alone
        self.assertEqual(r["32498/withdrawn1"]["status"], "withdrawn")
        self.assertEqual(r["32498/withdrawn1"]["preservica_ref"], "IO_" + uid(7))
        self.assertEqual(r["32498/keep"]["target_url"], "https://dc/6")       # existing target kept
        self.assertEqual(r["32498/keep"]["preservica_ref"], "IO_" + uid(8))
        rep = {x["preservica_ref"][3:11]: x["outcome"] for x in read("match_report.csv")}
        self.assertTrue(rep["00000003"].startswith("ARK found in 2 assets"))
        self.assertTrue(rep["00000005"].startswith("resolve.csv already has IO_"))
        self.assertEqual(rep["00000006"], "ARK not in resolve.csv")
        self.assertEqual(rep["00000011"], "blade and URL name different ARKs in resolve.csv")
        self.assertNotIn("00000009", rep)
        self.assertNotIn("00000010", rep)
        self.assertIn("4 ARK(s) updated", out)

    def test_dry_run_changes_nothing(self):
        before = text("resolve.csv")
        with fake_net(self.pv):
            self.main(mp, "--dry-run")
        self.assertEqual(text("resolve.csv"), before)
        self.assertTrue(os.path.exists("match_report.csv"))

    def test_from_gitlab_and_push_only_when_changed(self):
        gitlab = FakeGitLab({"resolve.csv": text("resolve.csv"),
                             "ark_master_list.csv": text("ark_master_list.csv")})
        os.remove("resolve.csv")
        with fake_net(self.pv, gitlab):
            self.main(mp, "--from-gitlab", "--push-to-gitlab")
        self.assertEqual(len(gitlab.pushes), 1)
        self.assertEqual(gitlab.pushes[0][1], "resolve.csv")
        self.assertIn("IO_" + uid(1), gitlab.files["resolve.csv"])
        with fake_net(self.pv, gitlab):
            out = self.main(mp, "--from-gitlab", "--push-to-gitlab", "--push-report")
        self.assertIn("nothing to push", out)
        self.assertEqual([p[1] for p in gitlab.pushes], ["resolve.csv", "match_report.csv"])
        with fake_net(self.pv, gitlab):                     # an unchanged report isn't pushed again
            self.main(mp, "--from-gitlab", "--push-to-gitlab", "--push-report")
        self.assertEqual(len(gitlab.pushes), 2)

    def test_generate_arks_match_preservica_in_one_step(self):
        gitlab = FakeGitLab({"resolve.csv": text("resolve.csv"),
                             "ark_master_list.csv": text("ark_master_list.csv")})
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with fake_net(self.pv, gitlab):
            out = self.main(ga, "out", "--push-to-gitlab", "--match-preservica")
        self.assertIn("4 ARK(s) updated", out)
        self.assertIn("4 Preservica reference(s) added", [p[2] for p in gitlab.pushes if p[1] == "resolve.csv"][0])
        self.assertIn("IO_" + uid(2), gitlab.files["resolve.csv"])


class ArkNameTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(mp.ark_name("ark:/32498/x7-k9q."), "x7k9q")
        self.assertEqual(mp.ark_name("https://n2t.net/ark:/32498/s3abc"), "s3abc")
        self.assertEqual(mp.ark_name("ark:32498/s3abc"), "s3abc")
        self.assertEqual(mp.ark_name("32498/s3abc"), "s3abc")
        self.assertEqual(mp.ark_name("s3abc"), "s3abc")
        self.assertEqual(mp.ark_name("ark:/99999/zz", "32498"), "")
        self.assertEqual(mp.ark_name("not an ark!"), "")
        self.assertEqual(mp.ark_name(""), "")


if __name__ == "__main__":
    unittest.main()
