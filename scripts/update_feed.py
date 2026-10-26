#!/usr/bin/env python3
"""Build feed/eknomics.json, the public numbers that eKnomics SCORM modules read.

Runs weekly in GitHub Actions (see .github/workflows/weekly.yml). Standard library only.

- Pulls each FRED series. No key is needed; if FRED ever blocks the download,
  add a free FRED API key as the repository secret FRED_API_KEY and the script
  switches to the FRED API automatically.
- Checks every number against a sane range, rejects future dates, and rejects
  week-to-week jumps too large to be real.
- If any series fails, it keeps last week's good values for that series,
  records a warning, and exits with status 1 so the workflow opens an issue.
- Merges the hand-checked values in feed/manual.json (ARM rate quarterly,
  insurance and property-tax averages yearly) and warns when one is overdue.
- Passes through the yearly tax figures for Ch 12 "Who Pays?" (taxYears, one
  block per tax year, and ilMotorFuel) after checking each one; a block that
  fails a check is replaced by last week's good copy.
- Builds incomeSide for Ch 23 "Follow the Dollar": the GDP-to-saving lines of
  NIPA Tables 1.7.5, 1.10 and 2.1, all from one quarter, checked against every
  accounting identity before publishing.
"""
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "feed", "eknomics.json")
MANUAL = os.path.join(ROOT, "feed", "manual.json")
SCHEMA = 1
FIRST_YEAR = 2016
UA = "eknomics-data/1.0 (+https://github.com/arkoester/eknomics-data)"

SERIES = {
    "mortgage30": {"fred": "MORTGAGE30US", "label": "30-year fixed mortgage rate", "unit": "percent",
                   "source": "Freddie Mac Primary Mortgage Market Survey (via FRED)", "frequency": "weekly",
                   "range": (1, 20), "maxJump": 1.5, "recent": 13, "annualAverage": True, "decimals": 2},
    "mortgage15": {"fred": "MORTGAGE15US", "label": "15-year fixed mortgage rate", "unit": "percent",
                   "source": "Freddie Mac Primary Mortgage Market Survey (via FRED)", "frequency": "weekly",
                   "range": (1, 20), "maxJump": 1.5, "recent": 13, "decimals": 2},
    "ilActiveListings": {"fred": "ACTLISCOUIL", "label": "Active home listings in Illinois", "unit": "listings",
                         "source": "Realtor.com (via FRED)", "frequency": "monthly",
                         "range": (1000, 200000), "july": True, "decimals": 0},
    "madisonMedianListPrice": {"fred": "MEDLISPRI17119", "label": "Median listing price, Madison County, IL",
                               "unit": "dollars", "source": "Realtor.com (via FRED)", "frequency": "monthly",
                               "range": (30000, 2000000), "decimals": 0},
    "madisonMedianDaysOnMarket": {"fred": "MEDDAYONMAR17119", "label": "Median days on market, Madison County, IL",
                                  "unit": "days", "source": "Realtor.com (via FRED)", "frequency": "monthly",
                                  "range": (1, 365), "decimals": 0},
    "madisonHPI": {"fred": "ATNHPIUS17119A", "label": "House price index, Madison County, IL (2000 = 100)",
                   "unit": "index", "source": "FHFA (via FRED)", "frequency": "annual",
                   "range": (50, 1000), "annualValues": True, "decimals": 2},
}

MANUAL_RULES = {
    "arm51": {"range": (1, 20), "maxAgeDays": 150},            # refreshed quarterly
    "insuranceIL200k": {"range": (300, 10000), "maxAgeDays": 450},  # refreshed yearly
    "taxRateMadison": {"range": (0.3, 5), "maxAgeDays": 450},       # refreshed yearly
}


def today():
    return date.today()


def _get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # network hiccup: wait and retry
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"download failed after {tries} tries: {last}")


def parse_csv(text):
    """FRED CSV (header DATE,... or observation_date,...) -> [(date, value-string)]."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise ValueError("unexpected CSV layout")
    return [(r[0].strip(), r[1].strip()) for r in rows[1:] if len(r) >= 2]


def fetch_fred(series_id):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
            {"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": "2015-01-01"})
        data = json.loads(_get(url))
        return [(o["date"], o["value"]) for o in data.get("observations", [])]
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode(
        {"id": series_id, "cosd": "2015-01-01"})
    return parse_csv(_get(url))


def clean(obs):
    """[(date, value-string)] -> sorted [(date, float)], skipping FRED's '.' gaps."""
    out = []
    for d, v in obs:
        if v in ("", "."):
            continue
        try:
            datetime.strptime(d, "%Y-%m-%d")
            out.append((d, float(v)))
        except ValueError:
            continue
    out.sort()
    if not out:
        raise ValueError("no usable observations")
    return out


def _num(v, decimals):
    return int(round(v)) if decimals == 0 else round(v, decimals)


def build_series(spec, obs, prev):
    lo, hi = spec["range"]
    bad = [(d, v) for d, v in obs if not lo <= v <= hi]
    if bad:
        raise ValueError(f"{len(bad)} value(s) outside {lo} to {hi}, latest bad one {bad[-1]}")
    if obs[-1][0] > today().isoformat():
        raise ValueError(f"latest date {obs[-1][0]} is in the future")
    dec = spec["decimals"]
    latest = {"date": obs[-1][0], "value": _num(obs[-1][1], dec)}
    if spec.get("maxJump") and prev and prev.get("latest"):
        p = prev["latest"]
        if p.get("date") != latest["date"] and abs(latest["value"] - float(p.get("value", latest["value"]))) > spec["maxJump"]:
            raise ValueError(f"jump from {p.get('value')} to {latest['value']} is too large to trust")
    s = {"label": spec["label"], "unit": spec["unit"], "source": spec["source"], "fred": spec["fred"],
         "frequency": spec["frequency"], "latest": latest}
    if spec.get("recent"):
        s["recent"] = [[d, _num(v, dec)] for d, v in obs[-spec["recent"]:]]
    if spec.get("annualAverage"):
        years = {}
        for d, v in obs:
            if int(d[:4]) >= FIRST_YEAR:
                years.setdefault(d[:4], []).append(v)
        s["annual"] = {y: round(sum(vs) / len(vs), 2) for y, vs in sorted(years.items())}
        s["annualThrough"] = obs[-1][0]  # the latest year's average is year to date
    if spec.get("july"):
        s["july"] = {d[:4]: _num(v, dec) for d, v in obs if d[5:7] == "07" and int(d[:4]) >= FIRST_YEAR}
    if spec.get("annualValues"):
        s["annual"] = {d[:4]: _num(v, dec) for d, v in obs if int(d[:4]) >= FIRST_YEAR}
    return s


def build_manual(manual, prev_manual, warnings):
    out = {}
    for key, rule in MANUAL_RULES.items():
        item = manual.get(key)
        try:
            if not isinstance(item, dict):
                raise ValueError("missing")
            v = float(item["value"])
            d = str(item["date"])
            datetime.strptime(d, "%Y-%m-%d")
            lo, hi = rule["range"]
            if not lo <= v <= hi:
                raise ValueError(f"{v} outside {lo} to {hi}")
            if d > today().isoformat():
                raise ValueError("date is in the future")
            out[key] = item
            age = (today() - date.fromisoformat(d)).days
            if age > rule["maxAgeDays"]:
                warnings.append(f"{key} is {age} days old; its scheduled check is overdue")
        except Exception as e:
            warnings.append(f"manual value {key} is invalid ({e}); kept the last good value")
            if prev_manual and key in prev_manual:
                out[key] = prev_manual[key]
    return out


# ---------- Ch 12 "Who Pays?": yearly tax figures (single filer) ----------
TAX_SOURCES = ("fedSource", "ssSource", "ilSource")


def _num_in(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{what} {v!r} outside {lo} to {hi}")


def _iso(d, what):
    try:
        datetime.strptime(str(d), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{what} {d!r} is not a YYYY-MM-DD date")


def check_tax_year(year, t, prev_year=None):
    """Raise ValueError if a tax-year block is malformed or looks like a typo."""
    if not (len(year) == 4 and year.isdigit() and 2020 <= int(year) <= 2100):
        raise ValueError(f"bad year {year!r}")
    if not isinstance(t, dict):
        raise ValueError("not an object")
    fed = t.get("fed") or {}
    _num_in(fed.get("std"), 5000, 60000, "standard deduction")
    br = fed.get("brackets")
    if not isinstance(br, list) or not 5 <= len(br) <= 10:
        raise ValueError("brackets must be a list of 5 to 10 [threshold, rate] pairs")
    for i, b in enumerate(br):
        if not (isinstance(b, list) and len(b) == 2):
            raise ValueError(f"bracket {i} is not [threshold, rate]")
        _num_in(b[0], 0, 5000000, f"bracket {i} threshold")
        _num_in(b[1], 0.1, 60, f"bracket {i} rate")
        if i == 0 and b[0] != 0:
            raise ValueError("first bracket must start at 0")
        if i > 0 and not (b[0] > br[i - 1][0] and b[1] > br[i - 1][1]):
            raise ValueError(f"bracket {i} must have a higher threshold and rate than bracket {i - 1}")
    ss = t.get("ss") or {}
    _num_in(ss.get("rate"), 1, 15, "Social Security rate")
    _num_in(ss.get("cap"), 100000, 600000, "Social Security wage cap")
    _num_in(ss.get("medicare"), 0.5, 5, "Medicare rate")
    il = t.get("il") or {}
    _num_in(il.get("rate"), 1, 15, "Illinois rate")
    _num_in(il.get("exemption"), 0, 20000, "Illinois exemption")
    _num_in(il.get("exemptionAgiLimit"), 50000, 2000000, "Illinois exemption income limit")
    tips = t.get("tips")
    if tips is not None:
        _num_in(tips.get("max"), 0, 100000, "tips deduction maximum")
        _num_in(tips.get("phaseStart"), 0, 10000000, "tips phase-out start")
        _num_in(tips.get("from"), 2025, 2100, "tips first year")
        _num_in(tips.get("through"), 2025, 2100, "tips last year")
    _iso(t.get("checkedAt"), "checkedAt")
    if str(t["checkedAt"]) > today().isoformat():
        raise ValueError("checkedAt is in the future")
    for k in TAX_SOURCES:
        if not str(t.get(k, "")).strip():
            raise ValueError(f"missing {k}")
    if prev_year:
        # inflation adjustments move these a few percent a year; anything else is probably a typo
        pf = prev_year["fed"]
        ratio = fed["std"] / pf["std"]
        if not 0.95 <= ratio <= 1.15:
            raise ValueError(f"standard deduction moved {ratio:.2f}x from the year before")
        if [b[1] for b in br] == [b[1] for b in pf["brackets"]]:
            for i in range(1, len(br)):
                r = br[i][0] / pf["brackets"][i][0]
                if not 0.95 <= r <= 1.15:
                    raise ValueError(f"bracket {i} threshold moved {r:.2f}x from the year before")
        r = ss["cap"] / prev_year["ss"]["cap"]
        if not 0.99 <= r <= 1.15:
            raise ValueError(f"Social Security cap moved {r:.2f}x from the year before")


def build_tax_years(manual, prev_manual, warnings):
    src = manual.get("taxYears") or {}
    prev = (prev_manual or {}).get("taxYears") or {}
    out = {}
    for year in sorted(src):
        before = out.get(str(int(year) - 1)) if year.isdigit() else None
        try:
            check_tax_year(year, src[year], before)
            out[year] = src[year]
        except Exception as e:
            warnings.append(f"taxYears {year} is invalid ({e}); kept the last good copy")
            if year in prev:
                out[year] = prev[year]
    for year, t in prev.items():
        if year not in out and year not in src:
            warnings.append(f"taxYears {year} disappeared from manual.json; kept the last good copy")
            out[year] = t
    d = today()
    if (src or prev) and d.month >= 2 and str(d.year) not in out:
        warnings.append(f"taxYears has no {d.year} figures yet; the scheduled check is overdue")
    return out


def check_fuel(m, prev=None):
    if not isinstance(m, dict):
        raise ValueError("missing")
    _num_in(m.get("cents"), 10, 150, "gas tax cents")
    _iso(m.get("date"), "date")
    _iso(m.get("checkedAt"), "checkedAt")
    if not str(m.get("source", "")).strip():
        raise ValueError("missing source")
    nxt = m.get("next")
    if nxt is not None:
        _num_in(nxt.get("cents"), 10, 150, "next gas tax cents")
        _iso(nxt.get("date"), "next date")
    if prev and abs(m["cents"] - prev.get("cents", m["cents"])) > 15:
        raise ValueError(f"gas tax moved from {prev.get('cents')} to {m['cents']} cents; probably a typo")


def build_fuel(manual, prev_manual, warnings):
    m = manual.get("ilMotorFuel")
    prev = (prev_manual or {}).get("ilMotorFuel")
    if m is None and prev is None:
        return None
    try:
        check_fuel(m, prev)
        newest = m.get("next", {}).get("date") if m.get("next") else m["date"]
        if (today() - date.fromisoformat(max(m["date"], newest or m["date"]))).days > 380:
            warnings.append("ilMotorFuel is over a year old; its scheduled check is overdue")
        return m
    except Exception as e:
        warnings.append(f"ilMotorFuel is invalid ({e}); kept the last good copy")
        return prev


# ---------- Ch 23 "Follow the Dollar": the income side of GDP (NIPA, quarterly) ----------
# Billions of dollars, seasonally adjusted annual rates, except pop (thousands of people).
# Every line is published for ONE quarter: the latest quarter that has all of them.
# GDP's advance estimate comes out a month before GDI, so GDP alone can run a quarter ahead;
# that newer GDP is reported separately as gdpAhead and never mixed into the block.
INCOME = {
    # NIPA Table 1.7.5: GDP to GNP, NNP and national income
    "gdp": "GDP", "incomeFromROW": "B645RC1Q027SBEA", "incomeToROW": "A655RC1Q027SBEA",
    "gnp": "GNP", "cfc": "COFC", "nnp": "A027RC1Q027SBEA", "statDisc": "A030RC1Q027SBEA",
    "ni": "NICUR", "gdi": "GDI",
    # NIPA Table 1.10: gross domestic income by type of income
    "comp": "GDICOMP", "prodTaxes": "GDITAXES", "subsidies": "GDISUBS", "nos": "GDINOS",
    "netInterestDom": "W272RC1Q027SBEA", "busTransfers": "B029RC1Q027SBEA", "proprietors": "PROPINC",
    "rental": "RENTIN", "corpProfitsDom": "A445RC1Q027SBEA", "govEntSurplus": "A108RC1Q027SBEA",
    # NIPA Table 1.7.5 lines 17 to 25: national income to personal income
    "corpProfits": "CPROFIT", "prodTaxesLessSubs": "W254RC1Q027SBEA", "socialInsurance": "A061RC1Q027SBEA",
    "netInterestMisc": "W255RC1Q027SBEA", "assetIncome": "W210RC1Q027SBEA", "transfers": "A577RC1Q027SBEA",
    "pi": "PINCOME",
    # NIPA Table 2.1: personal income and its disposition
    "personalTaxes": "W055RC1Q027SBEA", "outlays": "A068RC1Q027SBEA",
    # corporate profits split (NIPA Table 1.10 lines 16 and 19), for the NI to PI step
    "corpTaxes": "A054RC1Q027SBEA", "retained": "W274RC1Q027SBEA",
    # population (thousands), for per-person figures
    "pop": "B230RC0Q173SBEA",
}
INCOME_SIGNED = {"statDisc", "govEntSurplus", "netInterestMisc", "netInterestDom"}  # can be negative
INCOME_POP_RANGE = (250000, 450000)
INCOME_RANGE = (50, 200000)
INCOME_TOL = 3.0  # billions; BEA rounds each line to the million, so identities hold within a few tenths


def income_identities(v):
    """(name, left, right) for every accounting identity the block must satisfy."""
    return [
        ("GNP = GDP + income from abroad - income paid abroad",
         v["gnp"], v["gdp"] + v["incomeFromROW"] - v["incomeToROW"]),
        ("NNP = GNP - depreciation", v["nnp"], v["gnp"] - v["cfc"]),
        ("national income = NNP - statistical discrepancy", v["ni"], v["nnp"] - v["statDisc"]),
        ("GDI = GDP - statistical discrepancy", v["gdi"], v["gdp"] - v["statDisc"]),
        ("GDI = compensation + taxes - subsidies + net operating surplus + depreciation",
         v["gdi"], v["comp"] + v["prodTaxes"] - v["subsidies"] + v["nos"] + v["cfc"]),
        ("net operating surplus = interest + transfers + proprietors + rent + profits + gov enterprises",
         v["nos"], v["netInterestDom"] + v["busTransfers"] + v["proprietors"] + v["rental"]
         + v["corpProfitsDom"] + v["govEntSurplus"]),
        ("personal income = national income - kept or taxed + paid to households", v["pi"],
         v["ni"] - v["corpProfits"] - v["prodTaxesLessSubs"] - v["socialInsurance"] - v["netInterestMisc"]
         - v["busTransfers"] - v["govEntSurplus"] + v["assetIncome"] + v["transfers"]),
    ]


def todays_chain(v):
    """The chain as BEA builds it today (NIPA Table 1.7.5), in the order the Ch 23 module teaches it.
    GDP + net foreign factor income = GNP; GNP - depreciation = NNP; NNP - statistical discrepancy = NI.
    NI INCLUDES indirect business taxes (BEA, since 2003); they leave on the way from NI to PI.
    NDP (GDP - depreciation) is kept as a side figure because the class chapter's older chain uses it."""
    b = {"gdp": v["gdp"]}
    b["netForeignIncome"] = v["incomeFromROW"] - v["incomeToROW"]
    b["gnp"] = v["gnp"]
    b["depreciation"] = v["cfc"]
    b["nnp"] = v["nnp"]
    b["statDisc"] = v["statDisc"]
    b["ni"] = v["ni"]
    b["indirectTaxes"] = v["prodTaxesLessSubs"]
    b["corpTaxes"] = v["corpTaxes"]
    b["retained"] = v["retained"]
    b["socialSecurityTaxes"] = v["socialInsurance"]
    b["transfers"] = v["transfers"]
    b["pi"] = v["pi"]
    # net interest plus the few small BEA lines the class chapter skips: whatever closes NI to PI exactly
    b["netInterest"] = b["pi"] - (b["ni"] - b["indirectTaxes"] - b["corpTaxes"] - b["retained"]
                                  - b["socialSecurityTaxes"] + b["transfers"])
    b["personalTaxes"] = v["personalTaxes"]
    b["di"] = v["pi"] - v["personalTaxes"]
    b["spending"] = v["outlays"]
    b["saving"] = b["di"] - v["outlays"]
    b["savingRate"] = 100 * b["saving"] / b["di"]
    b["ndp"] = v["gdp"] - v["cfc"]
    return b


def build_income(fetched, prev_block, warnings):
    """fetched: {key: [(date, float)]}. Returns the incomeSide block, or last week's on any failure."""
    try:
        missing = [k for k in INCOME if k not in fetched]
        if missing:
            raise ValueError("no data for " + ", ".join(missing))
        dates = None
        for k in INCOME:
            ds = {d for d, _ in fetched[k]}
            dates = ds if dates is None else dates & ds
        if not dates:
            raise ValueError("no quarter has every line")
        q = max(dates)
        if q > today().isoformat():
            raise ValueError(f"quarter {q} is in the future")
        if (today() - date.fromisoformat(q)).days > 400:
            raise ValueError(f"newest complete quarter {q} is over a year old")
        v = {k: dict(fetched[k])[q] for k in INCOME}
        for k, x in v.items():
            if k == "pop":
                lo, hi = INCOME_POP_RANGE
            elif k in INCOME_SIGNED:
                lo, hi = -INCOME_RANGE[1], INCOME_RANGE[1]
            else:
                lo, hi = INCOME_RANGE
            if not lo <= x <= hi:
                raise ValueError(f"{k} = {x} outside {lo} to {hi}")
        for name, left, right in income_identities(v):
            if abs(left - right) > INCOME_TOL:
                raise ValueError(f"{name} is off by {left - right:.1f} billion")
        book = todays_chain(v)
        if not 0 <= book["netInterest"] <= 0.15 * book["pi"]:
            raise ValueError(f"net interest {book['netInterest']:.1f} looks wrong")
        if not -5 <= book["savingRate"] <= 40:
            raise ValueError(f"saving rate {book['savingRate']:.1f} looks wrong")
        if prev_block and prev_block.get("quarter") and q < prev_block["quarter"]:
            raise ValueError(f"quarter {q} is older than last week's {prev_block['quarter']}")
        gdp = fetched["gdp"]
        block = {
            "quarter": q,
            "label": "US income side of GDP, NIPA Tables 1.7.5, 1.10 and 2.1",
            "unit": "billions of dollars, seasonally adjusted annual rate (pop: thousands)",
            "source": "U.S. Bureau of Economic Analysis (via FRED)",
            "fred": dict(INCOME),
            "values": {k: round(x, 3) for k, x in v.items()},
            "chain": {k: round(x, 3) for k, x in book.items()},
        }
        if gdp[-1][0] > q:
            block["gdpAhead"] = {"date": gdp[-1][0], "value": round(gdp[-1][1], 3)}
        return block
    except Exception as e:
        warnings.append(f"incomeSide: {e}; kept last week's good copy")
        return prev_block


# ---------- Ch 23 "Follow the Dollar": Madison County, IL personal income by type (BEA CAINC4, yearly) ----------
# BEA's public bulk download (no key). Values are thousands of dollars; population is people.
CAINC4_URL = "https://apps.bea.gov/regional/zip/CAINC4.zip"
MADISON_FIPS = "17119"
CAINC4_LINES = {"10": "pi", "20": "pop", "35": "earningsWork", "45": "earningsRes", "46": "dir",
                "47": "transfers", "50": "wages", "60": "supplements", "70": "proprietors"}


def _get_bytes(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"download failed after {tries} tries: {last}")


def parse_cainc4(rows, fips=MADISON_FIPS):
    """rows: csv.reader rows of a CAINC4 state file. Returns {year: {key: value}} for one county."""
    header = None
    out = {}
    for r in rows:
        if header is None:
            if r and r[0].strip() == "GeoFIPS":
                header = [h.strip() for h in r]
            continue
        if len(r) < len(header) or r[0].strip().strip('"').strip() != fips:
            continue
        rec = dict(zip(header, r))
        key = CAINC4_LINES.get(str(rec.get("LineCode", "")).strip())
        if not key:
            continue
        for col, val in rec.items():
            if col.isdigit() and len(col) == 4:
                v = str(val).strip()
                try:
                    out.setdefault(col, {})[key] = float(v)
                except ValueError:
                    pass  # "(D)" or "(NA)": not disclosed
    if header is None:
        raise ValueError("no GeoFIPS header found")
    return out


def build_madison(years, prev_block, warnings):
    try:
        full = [y for y, v in years.items() if all(k in v for k in CAINC4_LINES.values())]
        if not full:
            raise ValueError("no year has every line")
        y = max(full)
        v = years[y]
        if not 2015 <= int(y) <= today().year:
            raise ValueError(f"year {y} out of range")
        if not 100000 <= v["pop"] <= 1000000:
            raise ValueError(f"population {v['pop']} looks wrong")
        if not 5e6 <= v["pi"] <= 1e8:
            raise ValueError(f"personal income {v['pi']} looks wrong")
        tol = max(2000.0, 0.0005 * v["pi"])  # thousands of dollars; BEA rounds each line
        if abs(v["earningsWork"] - (v["wages"] + v["supplements"] + v["proprietors"])) > tol:
            raise ValueError("wages + supplements + proprietors does not match earnings by place of work")
        if abs(v["pi"] - (v["earningsRes"] + v["dir"] + v["transfers"])) > tol:
            raise ValueError("earnings + dividends/interest/rent + transfers does not match personal income")
        if prev_block and prev_block.get("year") and y < prev_block["year"]:
            raise ValueError(f"year {y} is older than last week's {prev_block['year']}")
        return {"year": y, "label": "Personal income by type, Madison County, Illinois",
                "unit": "thousands of dollars (pop: people)", "source": "U.S. Bureau of Economic Analysis, CAINC4",
                "values": {k: v[k] for k in CAINC4_LINES.values()}}
    except Exception as e:
        warnings.append(f"madisonIncome: {e}; kept last week's good copy")
        return prev_block


def fetch_madison():
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(_get_bytes(CAINC4_URL)))
    name = next((n for n in z.namelist() if n.upper().startswith("CAINC4_IL_") and n.lower().endswith(".csv")), None)
    if not name:
        raise ValueError("no Illinois file in CAINC4.zip")
    text = z.read(name).decode("latin-1")
    return parse_cainc4(csv.reader(io.StringIO(text)))


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run():
    prev = load(FEED) or {}
    manual = load(MANUAL) or {}
    warnings, series = [], {}
    for key, spec in SERIES.items():
        prev_s = (prev.get("series") or {}).get(key)
        try:
            series[key] = build_series(spec, clean(fetch_fred(spec["fred"])), prev_s)
        except Exception as e:
            warnings.append(f"{key} ({spec['fred']}): {e}; kept last week's good values")
            if prev_s:
                series[key] = prev_s
    feed = {
        "schema": SCHEMA,
        "about": "Public economic numbers for eKnomics modules. Numbers only; no student data.",
        "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": series,
        "manual": build_manual(manual, prev.get("manual"), warnings),
        "warnings": warnings,
    }
    tax_years = build_tax_years(manual, prev.get("manual"), warnings)
    if tax_years:
        feed["manual"]["taxYears"] = tax_years
    fuel = build_fuel(manual, prev.get("manual"), warnings)
    if fuel:
        feed["manual"]["ilMotorFuel"] = fuel
    fetched = {}
    for key, sid in INCOME.items():
        try:
            fetched[key] = clean(fetch_fred(sid))
        except Exception as e:
            warnings.append(f"incomeSide {key} ({sid}): {e}")
    income = build_income(fetched, prev.get("incomeSide"), warnings) if len(fetched) == len(INCOME) \
        else (prev.get("incomeSide") or None)
    if income:
        feed["incomeSide"] = income
    try:
        madison = build_madison(fetch_madison(), prev.get("madisonIncome"), warnings)
    except Exception as e:
        warnings.append(f"madisonIncome: {e}; kept last week's good copy")
        madison = prev.get("madisonIncome")
    if madison:
        feed["madisonIncome"] = madison
    feed["warnings"] = warnings
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    with open(FEED, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=1, ensure_ascii=False)
        f.write("\n")
    for key, s in series.items():
        print(f"{key:28s} latest {s['latest']['date']} = {s['latest']['value']}")
    if feed.get("incomeSide"):
        b = feed["incomeSide"]
        print(f"{'incomeSide':28s} quarter {b['quarter']}" + (f" (GDP ahead: {b['gdpAhead']['date']})" if b.get("gdpAhead") else ""))
    if feed.get("madisonIncome"):
        print(f"{'madisonIncome':28s} year {feed['madisonIncome']['year']}")
    for w in warnings:
        print("WARNING:", w)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(run())
