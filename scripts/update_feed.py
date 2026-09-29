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
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    with open(FEED, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=1, ensure_ascii=False)
        f.write("\n")
    for key, s in series.items():
        print(f"{key:28s} latest {s['latest']['date']} = {s['latest']['value']}")
    for w in warnings:
        print("WARNING:", w)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(run())
