"""
Offline tests for archiveit_monthly_stats.py: a fake Partner API answering
with crawl_job rows shaped like Archive-It's real responses.

    python -m unittest discover -s web_archiving/tests
"""

import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import archiveit_monthly_stats as ams  # noqa: E402

T = 10 ** 12
JOBS = [
    {"id": 1, "collection": 11, "original_start_date": "2026-01-05T10:00:00Z",
     "processing_end_date": "2026-02-01T10:00:00Z", "test": False, "type": "MONTHLY",
     "novel_count": 100, "duplicate_count": 50, "discovered_count": 500,
     "novel_bytes": 2 * T, "duplicate_bytes": T, "warc_compressed_bytes": T},
    {"id": 2, "collection": 22, "original_start_date": "2026-01-20T10:00:00Z",
     "processing_end_date": "2026-01-21T10:00:00Z", "test": False,
     "novel_count": 10, "novel_bytes": 10 ** 9},
    # Unsaved test crawl: left out by default.
    {"id": 3, "collection": 11, "original_start_date": "2026-03-02T10:00:00Z",
     "processing_end_date": "2026-03-03T10:00:00Z", "test": True,
     "test_crawl_state": None, "novel_count": 7},
    # Saved test crawl: counted.
    {"id": 4, "collection": 11, "original_start_date": "2026-03-09T10:00:00Z",
     "processing_end_date": None, "test": True, "test_crawl_state": "SAVED",
     "novel_count": 9},
]


def fake_api(session, table, **params):
    if table == "auth":
        return [{"username": "u", "account": {"id": 42}}]
    if table == "crawl_job":
        assert params["account"] == 42
        return JOBS
    if table == "collection":
        return [{"id": 11, "name": "Athletics"}, {"id": 22, "name": "News"}]
    raise AssertionError(table)


def run(*args):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out.csv")
        env = {"ARCHIVEIT_USER": "u", "ARCHIVEIT_PASS": "p"}
        with mock.patch.object(ams, "api_get", fake_api), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["x", "-o", out, *args]), \
                contextlib.redirect_stderr(io.StringIO()):
            ams.main()
        with open(out, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))


class MonthlyStats(unittest.TestCase):
    def test_monthly_totals(self):
        rows = {r["month"]: r for r in run()}
        self.assertEqual(list(rows), ["2026-01", "2026-02", "2026-03", "TOTAL"])
        jan = rows["2026-01"]
        self.assertEqual(jan["crawls"], "2")
        self.assertEqual(jan["documents_new"], "110")
        self.assertEqual(jan["documents_total"], "160")
        self.assertEqual(float(jan["new_data_TB"]), 2.001)
        self.assertEqual(float(jan["total_data_TB"]), 3.001)
        self.assertEqual(float(jan["warc_compressed_TB"]), 1.0)

    def test_empty_month_is_zero(self):
        rows = {r["month"]: r for r in run()}
        self.assertEqual(rows["2026-02"]["crawls"], "0")

    def test_unsaved_test_crawls_left_out(self):
        rows = {r["month"]: r for r in run()}
        self.assertEqual(rows["2026-03"]["documents_new"], "9")
        rows = {r["month"]: r for r in run("--include-tests")}
        self.assertEqual(rows["2026-03"]["documents_new"], "16")

    def test_single_month(self):
        rows = run("--month", "2026-03")
        self.assertEqual([r["month"] for r in rows], ["2026-03", "TOTAL"])

    def test_by_end_date(self):
        rows = {r["month"]: r for r in run("--date-field", "end")}
        self.assertEqual(rows["2026-02"]["documents_new"], "100")
        self.assertNotIn("2026-03", rows)   # the March crawl has no end date yet

    def test_by_collection(self):
        rows = run("--by-collection")
        jan = [r for r in rows if r["month"] == "2026-01"]
        self.assertEqual({r["collection_name"] for r in jan}, {"Athletics", "News"})
        self.assertEqual(rows[-1]["documents_new"], "119")

    def test_credentials_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.properties")
            with open(p, "w") as fh:
                fh.write("[credentials]\nusername=alice\npassword=secret\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(ams.load_credentials(p), ("alice", "secret"))


if __name__ == "__main__":
    unittest.main()
