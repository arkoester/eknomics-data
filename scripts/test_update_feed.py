"""Offline tests for update_feed.py. Run: python -m unittest discover -s scripts -p "test_*.py" -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_feed as uf  # noqa: E402


def weekly(start, n, value=6.5, step=0.0):
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(weeks=i)).isoformat(), f"{value + step * i:.2f}") for i in range(n)]


def monthly(first_year, last_year, value):
    return [(f"{y}-{m:02d}-01", str(value)) for y in range(first_year, last_year + 1) for m in range(1, 13)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "feed"))
        self.orig = (uf.FEED, uf.MANUAL, uf.fetch_fred, uf.today)
        uf.FEED = os.path.join(self.tmp, "feed", "eknomics.json")
        uf.MANUAL = os.path.join(self.tmp, "feed", "manual.json")
        uf.today = lambda: date(2026, 10, 2)
        self.data = {
            "MORTGAGE30US": weekly("2015-01-01", 600, 6.0, 0.001)[:2] + weekly("2016-01-07", 560, 5.0, 0.0),
            "MORTGAGE15US": weekly("2016-01-07", 560, 4.5),
            "ACTLISCOUIL": monthly(2016, 2025, 20000) + [("2026-07-01", "20504"), ("2026-08-01", "21354")],
            "MEDLISPRI17119": monthly(2024, 2025, 215000) + [("2026-08-01", "229500")],
            "MEDDAYONMAR17119": monthly(2025, 2025, 40) + [("2026-07-01", "36"), ("2026-08-01", ".")],
            "ATNHPIUS17119A": [(f"{y}-01-01", str(100 + y - 2000)) for y in range(2010, 2026)],
        }
        uf.fetch_fred = lambda sid: list(self.data[sid])
        self.write_manual({
            "arm51": {"value": 6.10, "date": "2026-09-18", "source": "MBA", "armShare": 9.8},
            "insuranceIL200k": {"value": 2039, "date": "2026-08-04", "source": "Insure.com"},
            "taxRateMadison": {"value": 1.75, "date": "2026-09-29", "source": "SmartAsset"},
        })

    def tearDown(self):
        uf.FEED, uf.MANUAL, uf.fetch_fred, uf.today = self.orig
        shutil.rmtree(self.tmp)

    def write_manual(self, m):
        with open(uf.MANUAL, "w") as f:
            json.dump(m, f)

    def feed(self):
        with open(uf.FEED) as f:
            return json.load(f)


class ParseTests(unittest.TestCase):
    def test_both_fred_headers_and_gaps(self):
        for header in ("DATE,MORTGAGE30US", "observation_date,MORTGAGE30US"):
            obs = uf.clean(uf.parse_csv(header + "\n2026-09-17,6.95\n2026-09-24,7.03\n2026-10-01,.\n"))
            self.assertEqual(obs, [("2026-09-17", 6.95), ("2026-09-24", 7.03)])

    def test_bad_layout(self):
        with self.assertRaises(ValueError):
            uf.parse_csv("just one column\n1\n")


class RunTests(Base):
    def test_good_run(self):
        self.assertEqual(uf.run(), 0)
        f = self.feed()
        self.assertEqual(f["schema"], 1)
        self.assertEqual(f["warnings"], [])
        m30 = f["series"]["mortgage30"]
        self.assertEqual(len(m30["recent"]), 13)
        self.assertIn("2016", m30["annual"])
        self.assertNotIn("2015", m30["annual"])
        self.assertEqual(m30["annualThrough"], m30["latest"]["date"])
        self.assertEqual(f["series"]["ilActiveListings"]["july"]["2026"], 20504)
        self.assertEqual(f["series"]["madisonMedianListPrice"]["latest"], {"date": "2026-08-01", "value": 229500})
        self.assertEqual(f["series"]["madisonMedianDaysOnMarket"]["latest"], {"date": "2026-07-01", "value": 36})
        self.assertEqual(f["series"]["madisonHPI"]["annual"]["2025"], 125)
        self.assertEqual(f["manual"]["arm51"]["value"], 6.10)

    def test_annual_average_math(self):
        self.data["MORTGAGE30US"] = [("2016-01-07", "4.00"), ("2016-06-02", "5.00"), ("2017-01-05", "3.00")]
        uf.run()
        self.assertEqual(self.feed()["series"]["mortgage30"]["annual"], {"2016": 4.5, "2017": 3.0})

    def test_out_of_range_keeps_last_good(self):
        uf.run()
        good = self.feed()["series"]["madisonMedianListPrice"]
        self.data["MEDLISPRI17119"] = [("2026-09-01", "5")]
        self.assertEqual(uf.run(), 1)
        f = self.feed()
        self.assertEqual(f["series"]["madisonMedianListPrice"], good)
        self.assertTrue(any("madisonMedianListPrice" in w for w in f["warnings"]))

    def test_implausible_jump_rejected(self):
        uf.run()
        good = self.feed()["series"]["mortgage30"]
        self.data["MORTGAGE30US"] = self.data["MORTGAGE30US"] + [("2026-10-01", "9.90")]
        self.assertEqual(uf.run(), 1)
        self.assertEqual(self.feed()["series"]["mortgage30"], good)

    def test_future_date_rejected(self):
        self.data["MORTGAGE15US"] = self.data["MORTGAGE15US"] + [("2027-01-07", "6.00")]
        self.assertEqual(uf.run(), 1)

    def test_download_failure_keeps_last_good(self):
        uf.run()
        good = self.feed()["series"]["ilActiveListings"]

        def boom(sid):
            if sid == "ACTLISCOUIL":
                raise RuntimeError("network down")
            return list(self.data[sid])
        uf.fetch_fred = boom
        self.assertEqual(uf.run(), 1)
        f = self.feed()
        self.assertEqual(f["series"]["ilActiveListings"], good)
        self.assertEqual(f["series"]["mortgage30"]["latest"]["date"], self.data["MORTGAGE30US"][-1][0])

    def test_manual_invalid_keeps_last_good(self):
        uf.run()
        self.write_manual({"arm51": {"value": 45, "date": "2026-09-18"},
                           "insuranceIL200k": {"value": 2039, "date": "2026-08-04"},
                           "taxRateMadison": {"value": 1.75, "date": "2026-09-29"}})
        self.assertEqual(uf.run(), 1)
        self.assertEqual(self.feed()["manual"]["arm51"]["value"], 6.10)

    def test_manual_overdue_warns(self):
        uf.today = lambda: date(2027, 3, 1)
        self.data = {k: [o for o in v] for k, v in self.data.items()}
        self.assertEqual(uf.run(), 1)
        self.assertTrue(any("arm51" in w and "overdue" in w for w in self.feed()["warnings"]))

    def test_checked_at_changes_every_run(self):
        uf.run()
        self.assertRegex(self.feed()["checkedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
