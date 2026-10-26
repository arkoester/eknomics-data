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


def income_quarter(scale=1.0):
    """One quarter of consistent NIPA lines (billions), keyed by our feed keys."""
    v = {"gdp": 32486.066 * scale, "incomeFromROW": 1711.539, "incomeToROW": 1634.740, "cfc": 5379.616,
         "statDisc": 240.924, "comp": 16251.872 * scale, "prodTaxes": 2371.204, "subsidies": 128.363,
         "netInterestDom": 500.0, "busTransfers": 268.914, "proprietors": 2162.796, "rental": 1150.156,
         "govEntSurplus": -65.009, "corpProfits": 4827.390, "prodTaxesLessSubs": 2244.065,
         "socialInsurance": 2099.039, "netInterestMisc": 136.416, "assetIncome": 4304.848,
         "transfers": 5169.332, "personalTaxes": 3500.0, "pop": 343000.0}
    v["gnp"] = v["gdp"] + v["incomeFromROW"] - v["incomeToROW"]
    v["nnp"] = v["gnp"] - v["cfc"]
    v["ni"] = v["nnp"] - v["statDisc"]
    v["gdi"] = v["gdp"] - v["statDisc"]
    v["nos"] = v["gdi"] - v["comp"] - v["prodTaxes"] + v["subsidies"] - v["cfc"]
    v["corpProfitsDom"] = v["nos"] - (v["netInterestDom"] + v["busTransfers"] + v["proprietors"]
                                      + v["rental"] + v["govEntSurplus"])
    v["pi"] = (v["ni"] - v["corpProfits"] - v["prodTaxesLessSubs"] - v["socialInsurance"] - v["netInterestMisc"]
               - v["busTransfers"] - v["govEntSurplus"] + v["assetIncome"] + v["transfers"])
    v["dpi"] = v["pi"] - v["personalTaxes"]
    v["outlays"] = v["dpi"] - 1200.0
    v["saving"] = v["dpi"] - v["outlays"]
    return v


def income_data(quarters=(("2026-01-01", 0.99), ("2026-04-01", 1.0))):
    out = {}
    for d, s in quarters:
        for k, x in income_quarter(s).items():
            out.setdefault(uf.INCOME[k], []).append((d, f"{x:.3f}"))
    return out


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
        self.data.update(income_data())
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


class IncomeSideTests(Base):
    def test_good_block(self):
        self.assertEqual(uf.run(), 0)
        b = self.feed()["incomeSide"]
        self.assertEqual(b["quarter"], "2026-04-01")
        self.assertAlmostEqual(b["values"]["gdp"], 32486.066, places=3)
        self.assertAlmostEqual(b["values"]["gdi"], 32486.066 - 240.924, places=3)
        self.assertEqual(set(b["values"]), set(uf.INCOME))
        self.assertNotIn("gdpAhead", b)

    def test_gdp_ahead_stays_out_of_the_block(self):
        self.data["GDP"] = self.data["GDP"] + [("2026-07-01", "33000.000")]
        self.assertEqual(uf.run(), 0)
        b = self.feed()["incomeSide"]
        self.assertEqual(b["quarter"], "2026-04-01")
        self.assertAlmostEqual(b["values"]["gdp"], 32486.066, places=3)
        self.assertEqual(b["gdpAhead"], {"date": "2026-07-01", "value": 33000.0})

    def test_broken_identity_keeps_last_good(self):
        uf.run()
        good = self.feed()["incomeSide"]
        self.data["GDI"] = [(d, f"{float(x) + 50:.3f}") for d, x in self.data["GDI"]]
        self.assertEqual(uf.run(), 1)
        f = self.feed()
        self.assertEqual(f["incomeSide"], good)
        self.assertTrue(any("incomeSide" in w and "GDI = GDP" in w for w in f["warnings"]))

    def test_one_line_down_keeps_last_good(self):
        uf.run()
        good = self.feed()["incomeSide"]

        def boom(sid):
            if sid == "PINCOME":
                raise RuntimeError("network down")
            return list(self.data[sid])
        uf.fetch_fred = boom
        self.assertEqual(uf.run(), 1)
        self.assertEqual(self.feed()["incomeSide"], good)

    def test_lines_on_different_quarters_use_the_common_one(self):
        self.data["PINCOME"] = self.data["PINCOME"][:1]
        self.assertEqual(uf.run(), 0)
        self.assertEqual(self.feed()["incomeSide"]["quarter"], "2026-01-01")

    def test_stale_quarter_rejected(self):
        uf.today = lambda: date(2027, 9, 1)
        uf.run()
        self.assertTrue(any("incomeSide" in w and "over a year old" in w for w in self.feed()["warnings"]))


def tax_year(std=16100, scale=1.0, cap=184500, checked="2026-09-30"):
    br = [[0, 10], [12400, 12], [50400, 22], [105700, 24], [201775, 32], [256225, 35], [640600, 37]]
    return {"checkedAt": checked,
            "fed": {"std": std, "brackets": [[round(b[0] * scale), b[1]] for b in br]},
            "fedSource": "IRS", "ss": {"rate": 6.2, "cap": cap, "medicare": 1.45}, "ssSource": "SSA",
            "il": {"rate": 4.95, "exemption": 2925, "exemptionAgiLimit": 250000}, "ilSource": "Illinois Comptroller",
            "tips": {"max": 25000, "phaseStart": 150000, "from": 2025, "through": 2028}}


FUEL = {"cents": 48.3, "date": "2026-07-01", "to": "2026-12-31", "source": "IDOR FY 2026-23", "checkedAt": "2026-09-30"}


class TaxYearTests(Base):
    def setUp(self):
        super().setUp()
        with open(uf.MANUAL) as f:
            self.manual = json.load(f)
        self.manual["taxYears"] = {"2026": tax_year()}
        self.manual["ilMotorFuel"] = dict(FUEL)
        self.write_manual(self.manual)

    def test_pass_through(self):
        self.assertEqual(uf.run(), 0)
        m = self.feed()["manual"]
        self.assertEqual(m["taxYears"]["2026"]["fed"]["std"], 16100)
        self.assertEqual(m["taxYears"]["2026"]["fed"]["brackets"][2], [50400, 22])
        self.assertEqual(m["ilMotorFuel"]["cents"], 48.3)
        self.assertEqual(m["arm51"]["value"], 6.10)

    def test_bad_brackets_keep_last_good(self):
        uf.run()
        bad = tax_year()
        bad["fed"]["brackets"][3] = [40000, 24]
        self.manual["taxYears"]["2026"] = bad
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 1)
        f = self.feed()
        self.assertEqual(f["manual"]["taxYears"]["2026"]["fed"]["brackets"][3], [105700, 24])
        self.assertTrue(any("taxYears 2026" in w for w in f["warnings"]))

    def test_next_year_accepted(self):
        self.manual["taxYears"]["2027"] = tax_year(std=16600, scale=1.032, cap=192300)
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 0)
        self.assertEqual(sorted(self.feed()["manual"]["taxYears"]), ["2026", "2027"])

    def test_typo_next_year_rejected(self):
        self.manual["taxYears"]["2027"] = tax_year(std=166000, scale=1.032, cap=192300)
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 1)
        f = self.feed()
        self.assertNotIn("2027", f["manual"]["taxYears"])
        self.assertTrue(any("taxYears 2027" in w and "standard deduction" in w for w in f["warnings"]))

    def test_missing_current_year_warns(self):
        uf.today = lambda: date(2027, 2, 15)
        self.assertEqual(uf.run(), 1)
        self.assertTrue(any("no 2027 figures" in w for w in self.feed()["warnings"]))

    def test_missing_source_rejected(self):
        bad = tax_year()
        bad["fedSource"] = ""
        self.manual["taxYears"]["2026"] = bad
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 1)
        self.assertNotIn("2026", self.feed()["manual"].get("taxYears", {}))

    def test_bad_fuel_keeps_last_good(self):
        uf.run()
        self.manual["ilMotorFuel"] = dict(FUEL, cents=483)
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 1)
        self.assertEqual(self.feed()["manual"]["ilMotorFuel"]["cents"], 48.3)

    def test_fuel_typo_jump_rejected(self):
        uf.run()
        self.manual["ilMotorFuel"] = dict(FUEL, cents=68.3)
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 1)
        self.assertEqual(self.feed()["manual"]["ilMotorFuel"]["cents"], 48.3)

    def test_without_tax_blocks_no_warning(self):
        del self.manual["taxYears"], self.manual["ilMotorFuel"]
        self.write_manual(self.manual)
        self.assertEqual(uf.run(), 0)
        self.assertNotIn("taxYears", self.feed()["manual"])


if __name__ == "__main__":
    unittest.main()
