"""
Offline tests for generate_arks.py, match_preservica.py, check_resolve.py
and check_github.py, with a fake
GitHub and a fake Preservica (MODS laid out like Rod Library's template).

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
import check_github as cg  # noqa: E402

GH_API = "https://api.github.com"
GH = GH_API + "/repos/rodlibrary/Resolver/contents/"
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


class FakeGitHub:
    """Enough of GitHub's repository contents API for the ARK scripts."""

    def __init__(self, files=None, branch="main"):
        self.files = dict(files or {})
        self.branch = branch
        self.pushes = []

    def handle(self, req):
        url = req.full_url
        if url == GH_API + "/repos/rodlibrary/Resolver":
            return Resp(json.dumps({"full_name": "rodlibrary/Resolver"}))
        if not url.startswith(GH):
            return None
        assert req.get_header("Authorization") == "Bearer tok"
        path = urllib.parse.unquote(url[len(GH):].split("?")[0])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        method = req.get_method()
        if method == "GET":
            if query.get("ref", [self.branch])[0] != self.branch:
                raise http_error(url, 404, b'{"message": "No commit found for the ref nope"}')
            if path not in self.files:
                raise http_error(url, 404, b'{"message": "Not Found"}')
            if "raw" in req.get_header("Accept"):
                return Resp(self.files[path])
            return Resp(json.dumps({"sha": f"sha-{len(self.files[path])}", "path": path}))
        assert method == "PUT"
        body = json.loads(req.data)
        if path in self.files:
            assert body.get("sha") == f"sha-{len(self.files[path])}", "an update must send the file's sha"
        else:
            assert "sha" not in body
        self.files[path] = base64.b64decode(body["content"]).decode()
        self.pushes.append((method, path, body["message"]))
        return Resp(json.dumps({"content": {"html_url": f"https://github.com/rodlibrary/Resolver/blob/main/{path}"}}), 201)


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
LEGACY_HEADER = list(ga.LEGACY_RESOLVE_FIELDS)
MASTER_HEADER = list(ga.MASTER_LIST_FIELDS)
ARK = "https://library.uni.edu/ark:/32498/"
PORTAL = "https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C"
import check_resolve as cr  # noqa: E402


def master_row(name, blade=None, shoulder=""):
    return ["t", f"ark:/32498/{name}", blade or name, "32498", shoulder, "i", "t", "f", "2"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok", "PRESERVICA_USERNAME": "svc",
                                                "PRESERVICA_PASSWORD": "pw", "GITHUB_STEP_SUMMARY": ""})
        self.env.start()
        os.environ.pop("ALLOWED_TARGET_HOSTS", None)

    def tearDown(self):
        self.env.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def main(self, module, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["x", *argv]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            result = module.main()
        return out.getvalue() if result is None else (result, out.getvalue())


class GenerateArksTest(Base):
    def test_push_pulls_and_merges_github_copies_and_writes_resolve_csv(self):
        github = FakeGitHub({
            "ark_master_list.csv": "timestamp,ark,ark_blade,naan,shoulder,identifier,title,source_file,row\r\n"
                                   "2026-10-01T10:00:00,ark:/32498/s3remote01,remote01,32498,s3,r1,Remote,old.csv,2",
            # no line break at the end, as in rodlibrary/Resolver
            "resolve.csv": f"ark,p_path\r\n{ARK}s3remote01,{PORTAL}{uid(9)}",
        })
        # A local resolve.csv that is out of date, plus a row GitHub lacks.
        write("resolve.csv", RESOLVE_HEADER, [[f"{ARK}s3remote01", "https://digital.library.uni.edu/old"],
                                              [f"{ARK}s3local0001", "410"]])
        write("arks.csv", ["identifier", "title", "shoulder", "url"],
              [["n1", "New one", "s3", "https://digital.library.uni.edu/y/1"], ["n2", "No url yet", "s3", ""]])
        with fake_net(github):
            out = self.main(ga, "out", "--push-to-github")
        self.assertIn("Pulled resolve.csv from GitHub (1 row(s)); 1 local row(s) not yet in GitHub kept.", out)
        self.assertIn("Added 1 ARK(s) to resolve.csv; 1 with no url wait for match_preservica.py", out)
        rows = read("resolve.csv")
        self.assertEqual([r["ark"] for r in rows][:2], [f"{ARK}s3remote01", f"{ARK}s3local0001"])
        self.assertEqual(rows[0]["p_path"], PORTAL + uid(9))                 # GitHub's newer value kept
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[2]["ark"].startswith(ARK + "s3"))
        self.assertEqual(rows[2]["p_path"], "https://digital.library.uni.edu/y/1")
        self.assertEqual({p[1] for p in github.pushes}, {"ark_master_list.csv", "resolve.csv"})
        self.assertTrue(github.files["resolve.csv"].startswith("ark,p_path\r\n"))
        self.assertTrue(github.files["resolve.csv"].endswith("\r\n"))
        master = read("ark_master_list.csv")                  # the remote row isn't joined to the new ones
        self.assertEqual([m["ark"][:13] for m in master], ["ark:/32498/s3"] * 3)
        self.assertEqual(master[0]["ark"], "ark:/32498/s3remote01")
        self.assertEqual(ga.check_resolve(rows, text=text("resolve.csv")), [])

    def test_older_seven_column_resolve_csv_is_converted(self):
        write("resolve.csv", LEGACY_HEADER, [
            ["etd/1", "", "32498/s3active01", "IO_" + uid(1), PORTAL + uid(1), "active", ""],
            ["etd/2", "", "32498/s3gone0001", "", "", "withdrawn", ""],
            ["", "", "32498/s3waiting1", "", "", "unmatched", ""],
        ])
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        out = self.main(ga, "out", "--no-master-list")
        self.assertIn("older seven-column layout; converted it to ark, p_path (2 row(s); 1 unmatched", out)
        with open("resolve.csv", newline="", encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), RESOLVE_HEADER)
        self.assertEqual(read("resolve.csv"), [{"ark": f"{ARK}s3active01", "p_path": PORTAL + uid(1)},
                                               {"ark": f"{ARK}s3gone0001", "p_path": "410"}])

    def test_url_rules_match_redirects_csv(self):
        write("arks.csv", ["identifier", "title", "url", "preservica_ref"], [
            ["ok", "Portal", PORTAL + uid(3), ""],
            ["gone", "Withdrawn", "410", ""],
            ["ref", "From reference", "", "IO_" + uid(4)],
            ["pipe", "Raw pipe", "https://uni.access.preservica.com/portal/en-US/asset/sdb:IO|" + uid(5), ""],
            ["http", "Not https", "http://digital.library.uni.edu/x", ""],
            ["host", "Other host", "https://www.google.com/", ""],
        ])
        out = self.main(ga, "out")
        self.assertIn("Processed 6 row(s): 3 OK, 3 error(s).", out)
        self.assertIn("must be percent-encoded", out)
        self.assertIn("must be a full address starting with https://", out)
        self.assertIn("isn't one of uni.access.preservica.com, digital.library.uni.edu", out)
        self.assertEqual(len(read("ark_master_list.csv")), 3)          # no ARK minted for a bad row
        self.assertEqual([r["p_path"] for r in read("resolve.csv")], [PORTAL + uid(3), "410", PORTAL + uid(4)])

    def test_old_columns_and_options_are_noted_not_fatal(self):
        write("arks.csv", ["identifier", "title", "dc_path", "status"], [["n1", "One", "etd/1", "unmatched"]])
        out = self.main(ga, "out", "--no-master-list", "--slim-manifest", "paths.csv", "--no-slim-manifest")
        self.assertIn("--slim-manifest ignored", out)
        self.assertFalse(os.path.exists("paths.csv") or os.path.exists("resolve_paths.csv"))
        self.assertIn("dc_path ignored (old ScholarWorks addresses go in rodlibrary/Redirects", read("out.csv")[0]["notes"])

    def test_missing_token_mints_nothing(self):
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-github")
        self.assertIn("nothing was minted", str(e.exception))
        self.assertFalse(os.path.exists("ark_master_list.csv"))

    def test_pull_failure_mints_nothing(self):
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])

        def down(req, timeout=None):
            raise urllib.error.URLError("no route")
        with mock.patch("urllib.request.urlopen", side_effect=down), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-github")
        self.assertIn("nothing was minted", str(e.exception))
        self.assertFalse(os.path.exists("resolve.csv"))

    def test_a_resolve_csv_with_problems_stops_minting(self):
        github = FakeGitHub({"ark_master_list.csv": ",".join(MASTER_HEADER) + "\r\n",
                             "resolve.csv": f"ark,p_path\r\n{ARK}abc,https://example.com/x\r\n{ARK}abc,410\r\n"})
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with fake_net(github), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-github")
        self.assertIn("2 problem(s), so nothing was minted", str(e.exception))
        self.assertIn("line 3: " + ARK + "abc is already on line 2", str(e.exception))
        self.assertEqual(github.pushes, [])


class CheckResolveTest(Base):
    def test_good_file_passes_and_lists_waiting_arks(self):
        write("resolve.csv", RESOLVE_HEADER, [[f"{ARK}abc", PORTAL + uid(1)], [f"{ARK}def", "410"]])
        write("ark_master_list.csv", MASTER_HEADER, [master_row("abc"), master_row("def"), master_row("ghi")])
        code, out = self.main(cr, "--master-list", "ark_master_list.csv")
        self.assertEqual(code, 0, out)
        self.assertIn("2 ARK(s) (1 redirect, 1 withdrawn); 0 problem(s)", out)
        self.assertIn("3 ARK(s) minted; 1 not in resolve.csv yet", out)

    def test_problems_are_named_by_line(self):
        with open("resolve.csv", "w", newline="") as f:
            f.write(f"ark,p_path\r\n32498/abc,{PORTAL}{uid(1)}\r\n{ARK}a-bc,410\r\n"
                    f"{ARK}x,https://uni.access.preservica.com/a b\r\n"
                    f"https://library.uni.edu/ark:/99999/z,410")
        code, out = self.main(cr)
        self.assertEqual(code, 1)
        self.assertIn("line 2: ark '32498/abc' should be a full address like " + ARK, out)
        self.assertIn("line 3: " + ARK + "a-bc is already on line 2", out)
        self.assertIn("line 4: p_path contains a space", out)
        self.assertIn("line 5: ark '" + "https://library.uni.edu/ark:/99999/z' isn't under our NAAN 32498", out)
        self.assertIn("doesn't end with a line break", out)

    def test_old_layout_is_reported(self):
        write("resolve.csv", LEGACY_HEADER, [])
        code, out = self.main(cr)
        self.assertEqual(code, 1)
        self.assertIn("older seven-column layout", out)

    def test_from_github(self):
        github = FakeGitHub({"resolve.csv": f"ark,p_path\r\n{ARK}abc,410\r\n",
                             "ark_master_list.csv": ",".join(MASTER_HEADER) + "\r\n" + ",".join(master_row("abc")) + "\r\n"})
        with fake_net(github):
            code, out = self.main(cr, "--from-github")
        self.assertEqual(code, 0, out)
        self.assertIn("rodlibrary/Resolver/resolve.csv: 1 ARK(s)", out)


class MatchTest(Base):
    def setUp(self):
        super().setUp()
        write("resolve.csv", RESOLVE_HEADER, [
            [f"{ARK}taken", PORTAL + uid(8)],                         # another asset already
            [f"{ARK}withdrawn1", "410"],                              # withdrawn stays withdrawn
            [f"{ARK}keep", "https://digital.library.uni.edu/keep"],   # sent elsewhere by hand
        ])
        write("ark_master_list.csv", MASTER_HEADER, [
            master_row("s3abc123", "abc123", "s3"), master_row("xyz"), master_row("dup"),
            master_row("taken"), master_row("withdrawn1"), master_row("keep")])
        self.pv = FakePreservica({
            1: ("abc123", "https://n2t.net/ark:/32498/s3abc123"),   # bare blade + URL
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

    def test_match_adds_only_unambiguous_arks(self):
        with fake_net(self.pv):
            out = self.main(mp, "--workers", "2")
        r = {row["ark"]: row["p_path"] for row in read("resolve.csv")}
        self.assertEqual(r[f"{ARK}s3abc123"], PORTAL + uid(1))
        self.assertEqual(r[f"{ARK}xyz"], PORTAL + uid(2))
        self.assertNotIn(f"{ARK}dup", r)                              # in two assets: not added
        self.assertEqual(r[f"{ARK}taken"], PORTAL + uid(8))           # different asset: left alone
        self.assertEqual(r[f"{ARK}withdrawn1"], "410")
        self.assertEqual(r[f"{ARK}keep"], "https://digital.library.uni.edu/keep")
        self.assertEqual(len(r), 5)
        rep = {x["preservica_ref"][3:11]: x["outcome"] for x in read("match_report.csv")}
        self.assertEqual(rep["00000001"], "added")
        self.assertTrue(rep["00000003"].startswith("ARK found in 2 assets"))
        self.assertEqual(rep["00000005"], "resolve.csv already sends it to " + PORTAL + uid(8))
        self.assertEqual(rep["00000006"], "ARK not in the master list")
        self.assertEqual(rep["00000007"], "withdrawn (410) in resolve.csv: left as it is")
        self.assertEqual(rep["00000011"], "blade and URL name different ARKs")
        self.assertNotIn("00000009", rep)
        self.assertNotIn("00000010", rep)
        self.assertIn("2 ARK(s) added to resolve.csv", out)
        self.assertIn("1 minted ARK(s) not in resolve.csv yet", out)
        self.assertEqual(ga.check_resolve(read("resolve.csv"), text=text("resolve.csv")), [])

    def test_overwrite_replaces_an_address_but_never_410(self):
        with fake_net(self.pv):
            self.main(mp, "--overwrite")
        r = {row["ark"]: row["p_path"] for row in read("resolve.csv")}
        self.assertEqual(r[f"{ARK}taken"], PORTAL + uid(5))
        self.assertEqual(r[f"{ARK}keep"], PORTAL + uid(8))
        self.assertEqual(r[f"{ARK}withdrawn1"], "410")

    def test_dry_run_changes_nothing(self):
        before = text("resolve.csv")
        with fake_net(self.pv):
            self.main(mp, "--dry-run")
        self.assertEqual(text("resolve.csv"), before)
        self.assertTrue(os.path.exists("match_report.csv"))

    def test_from_github_and_push_only_when_changed(self):
        github = FakeGitHub({"resolve.csv": text("resolve.csv"),
                             "ark_master_list.csv": text("ark_master_list.csv")})
        os.remove("resolve.csv")
        with fake_net(self.pv, github):
            self.main(mp, "--from-github", "--push-to-github")
        self.assertEqual(github.pushes, [("PUT", "resolve.csv", "Add 2 ARK(s) matched in Preservica")])
        self.assertIn(PORTAL + uid(1), github.files["resolve.csv"])
        with fake_net(self.pv, github):
            out = self.main(mp, "--from-github", "--push-to-github", "--push-report")
        self.assertIn("nothing to push", out)
        self.assertEqual([p[1] for p in github.pushes], ["resolve.csv", "match_report.csv"])
        with fake_net(self.pv, github):                     # an unchanged report isn't pushed again
            self.main(mp, "--from-github", "--push-to-github", "--push-report")
        self.assertEqual(len(github.pushes), 2)

    def test_github_copy_in_the_old_layout_is_converted_and_pushed(self):
        github = FakeGitHub({"resolve.csv": ",".join(LEGACY_HEADER) + "\r\n"
                                            f",,32498/taken,IO_{uid(5)},{PORTAL}{uid(5)},active,\r\n",
                             "ark_master_list.csv": text("ark_master_list.csv")})
        with fake_net(FakePreservica({}), github):
            self.main(mp, "--from-github", "--push-to-github")
        self.assertEqual(github.pushes[0][2], "Write resolve.csv as ark, p_path")
        self.assertEqual(github.files["resolve.csv"], f"ark,p_path\r\n{ARK}taken,{PORTAL}{uid(5)}\r\n")

    def test_a_resolve_csv_with_problems_is_not_pushed(self):
        github = FakeGitHub({"resolve.csv": f"ark,p_path\r\n{ARK}taken,https://example.com/\r\n",
                             "ark_master_list.csv": text("ark_master_list.csv")})
        with fake_net(self.pv, github), self.assertRaises(SystemExit) as e:
            self.main(mp, "--from-github", "--push-to-github")
        self.assertIn("wasn't pushed", str(e.exception))
        self.assertEqual(github.pushes, [])

    def test_generate_arks_match_preservica_in_one_step(self):
        github = FakeGitHub({"resolve.csv": text("resolve.csv"),
                             "ark_master_list.csv": text("ark_master_list.csv")})
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with fake_net(self.pv, github):
            out = self.main(ga, "out", "--push-to-github", "--match-preservica")
        self.assertIn("2 ARK(s) added to resolve.csv", out)
        self.assertIn("2 matched in Preservica", [p[2] for p in github.pushes if p[1] == "resolve.csv"][0])
        self.assertIn(PORTAL + uid(2), github.files["resolve.csv"])


class FakeGitHubServer:
    """Enough of GitHub's API for check_github.py."""

    def __init__(self, token="tok", can_write=True, files=("resolve.csv", "ark_master_list.csv")):
        self.token, self.can_write = token, can_write
        self.contents = FakeGitHub({f: "a,b\r\n1,2\r\n" for f in files})
        self.branches = {"main": "abc123"}
        self.writes = []

    def handle(self, req):
        url = req.full_url
        if not url.startswith(GH_API):
            return None
        path = urllib.parse.unquote(url[len(GH_API):].split("?")[0])
        method = req.get_method()
        if path == "/zen":
            return Resp("Keep it logically awesome.")
        if req.get_header("Authorization") != f"Bearer {self.token}":
            raise http_error(url, 401, b'{"message": "Bad credentials"}')
        repo = "/repos/rodlibrary/Resolver"
        if path == repo:
            return Resp(json.dumps({"full_name": "rodlibrary/Resolver", "default_branch": "main",
                                    "visibility": "public"}))
        if path.startswith(repo + "/branches/"):
            name = path[len(repo + "/branches/"):]
            if name not in self.branches:
                raise http_error(url, 404, b'{"message": "Branch not found"}')
            return Resp(json.dumps({"name": name, "protected": False, "commit": {"sha": self.branches[name]}}))
        if path == repo + "/git/refs" and method == "POST":
            if not self.can_write:
                raise http_error(url, 403, b'{"message": "Resource not accessible by personal access token"}')
            body = json.loads(req.data)
            assert body["sha"] == "abc123"
            self.branches[body["ref"][len("refs/heads/"):]] = body["sha"]
            return Resp("{}", 201)
        if path.startswith(repo + "/git/refs/heads/") and method == "DELETE":
            self.branches.pop(path[len(repo + "/git/refs/heads/"):], None)
            return Resp("", 204)
        if path.startswith(repo + "/contents/"):
            ref = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("ref", ["main"])[0]
            if method == "PUT":
                body = json.loads(req.data)
                assert body["branch"] != "main", "check_github.py must never write to main"
                self.writes.append((path[len(repo + "/contents/"):], body["branch"]))
                return Resp("{}", 201)
            if ref != "main":
                raise http_error(url, 404, b'{"message": "Not Found"}')   # the test file isn't there yet
            return self.contents.handle(req)
        raise http_error(url, 404, b'{"message": "Not Found"}')


class CheckGitHubTest(Base):
    def run_check(self, server, *argv):
        out = io.StringIO()
        with fake_net(server), mock.patch.object(sys, "argv", ["x", *argv]), contextlib.redirect_stdout(out):
            code = cg.main()
        return code, out.getvalue()

    def test_all_checks_pass_and_main_is_untouched(self):
        server = FakeGitHubServer()
        code, out = self.run_check(server)
        self.assertEqual(code, 0, out)
        self.assertIn("the token can see rodlibrary/Resolver (public, default branch main)", out)
        self.assertIn("resolve.csv can be read (1 row(s))", out)
        self.assertIn("All checks passed", out)
        self.assertEqual(len(server.writes), 1)
        self.assertEqual(server.writes[0][0], "push-test.txt")
        self.assertEqual(set(server.branches), {"main"})          # the temporary branch was deleted

    def test_no_write(self):
        server = FakeGitHubServer()
        code, out = self.run_check(server, "--no-write")
        self.assertEqual(code, 0)
        self.assertEqual(server.writes, [])

    def test_bad_token(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "wrong"}):
            code, out = self.run_check(FakeGitHubServer())
        self.assertEqual(code, 1)
        self.assertIn("GitHub rejected the token", out)

    def test_read_only_token(self):
        server = FakeGitHubServer(can_write=False)
        code, out = self.run_check(server)
        self.assertEqual(code, 1)
        self.assertIn("can read but not write", out)
        self.assertIn("Contents: Read and write", out)
        self.assertEqual(server.writes, [])

    def test_misnamed_master_list(self):
        code, out = self.run_check(FakeGitHubServer(files=("resolve.csv", "arks_master_list.csv")), "--no-write")
        self.assertEqual(code, 0)
        self.assertIn("Not on branch main: ark_master_list.csv", out)

    def test_wrong_branch(self):
        code, out = self.run_check(FakeGitHubServer(), "--github-branch", "master", "--no-write")
        self.assertEqual(code, 1)
        self.assertIn("use --github-branch main", out)

    def test_unreachable(self):
        def down(req, timeout=None):
            raise urllib.error.URLError("timed out")
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=down), mock.patch.object(sys, "argv", ["x"]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cg.main(), 1)
        self.assertIn("Can't reach https://api.github.com", out.getvalue())


class GitLabOptionsTest(Base):
    def test_old_gitlab_options_stop_with_a_pointer_to_github(self):
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        for module, argv in ((ga, ["out", "--push-to-gitlab"]),
                             (mp, ["--from-gitlab", "--push-to-gitlab", "--gitlab-project=rod-library/resolver"])):
            with self.assertRaises(SystemExit) as e:
                self.main(module, *argv)
            self.assertIn("moved from GitLab to GitHub", str(e.exception))
            self.assertIn("--push-to-gitlab  ->  use --push-to-github", str(e.exception))
        self.assertFalse(os.path.exists("ark_master_list.csv"))      # nothing was minted

    def test_pull_reports_a_repository_the_token_cannot_see(self):
        def handle(req, timeout=None):
            raise http_error(req.full_url, 404, b'{"message": "Not Found"}')
        write("arks.csv", ["identifier", "title"], [["n1", "One"]])
        with mock.patch("urllib.request.urlopen", side_effect=handle), self.assertRaises(SystemExit) as e:
            self.main(ga, "out", "--push-to-github")
        self.assertIn("rodlibrary/Resolver not found", str(e.exception))
        self.assertIn("nothing was minted", str(e.exception))


class HelpTest(unittest.TestCase):
    def test_help_renders(self):
        # Python 3.14 checks every help string when the script starts; older versions only on -h.
        for module in (ga, cr, mp):
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["x", "-h"]), contextlib.redirect_stdout(out), \
                    self.assertRaises(SystemExit) as e:
                module.main()
            self.assertEqual(e.exception.code, 0)
            self.assertIn("usage:", out.getvalue())
        self.assertIn("sdb%3AIO%7C{ref}", out.getvalue().replace("\n", "").replace(" ", ""))


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
        self.assertEqual(ga.ark_key("https://library.uni.edu/ark:/32498/S3-abc/"), "32498/s3abc")
        self.assertEqual(ga.ark_key("ark:/99999/zz", "32498"), "")
        self.assertEqual(ga.ark_address("ark:/32498/s3abc"), "https://library.uni.edu/ark:/32498/s3abc")


if __name__ == "__main__":
    unittest.main()
