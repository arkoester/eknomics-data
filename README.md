# eknomics-data

Public economic numbers for **eKnomics** SCORM modules (AP Economics, Edwardsville High School).
Modules download one file when a student opens them, so current numbers show up without re-uploading anything to Schoology.

**Numbers only.** Nothing about students is ever stored here or sent here. Modules only download; they never upload.

## The file modules read

- Primary: `https://cdn.jsdelivr.net/gh/arkoester/eknomics-data@main/feed/eknomics.json`
- Backup: `https://raw.githubusercontent.com/arkoester/eknomics-data/main/feed/eknomics.json`

If both are unreachable (a web filter, no Wi-Fi), each module falls back to the numbers built into it, shows their date, and asks students to look up current rates when those numbers are old. Nothing breaks.

## How it stays current

| What | How often | How |
|---|---|---|
| 30-yr and 15-yr fixed mortgage rates (Freddie Mac) | weekly | GitHub Action, Fridays, from FRED |
| Illinois active listings, Madison County median listing price and days on market (Realtor.com) | weekly check (data is monthly) | same Action |
| Madison County house price index (FHFA) | weekly check (data is yearly) | same Action |
| 5/1 ARM rate and ARM share (Mortgage Bankers Association) | quarterly | scheduled Claude check updates `feed/manual.json` |
| Illinois homeowners insurance average, Madison County property tax rate | yearly | scheduled Claude check updates `feed/manual.json` |

Every number is checked against a sane range before it is published. If a download fails or a number looks wrong, the Action keeps last week's good numbers and opens an issue titled **"Data refresh needs attention"** (you get an email). Students are not affected while it is open.

## Files

- `feed/eknomics.json`: the published numbers (written by the Action; do not edit by hand)
- `feed/manual.json`: hand-checked numbers (ARM rate, insurance, tax rate), each with its source and date
- `scripts/update_feed.py`: builds the feed from FRED plus `manual.json` (Python standard library only)
- `scripts/test_update_feed.py`: offline tests; the Action runs them before every refresh
- `.github/workflows/weekly.yml`: the schedule

## If FRED ever blocks the download

Get a free FRED API key at fredaccount.stlouisfed.org, then add it here under **Settings → Secrets and variables → Actions → New repository secret**, named `FRED_API_KEY`. The script switches to the FRED API automatically.

## Feed format (schema 1)

```
{
  "schema": 1,
  "checkedAt": "2026-10-02T14:20:00Z",
  "series": {
    "mortgage30": { "latest": {"date": "2026-10-01", "value": 7.03}, "recent": [[date, value], ...13 weeks],
                    "annual": {"2016": 3.65, ...}, "annualThrough": "2026-10-01", "source": "...", ... },
    "mortgage15": { "latest": {...}, "recent": [...] },
    "ilActiveListings": { "latest": {...}, "july": {"2016": 61100, ...} },
    "madisonMedianListPrice": { "latest": {...} },
    "madisonMedianDaysOnMarket": { "latest": {...} },
    "madisonHPI": { "annual": {"2021": 153.1, ...} }
  },
  "manual": { "arm51": {...}, "insuranceIL200k": {...}, "taxRateMadison": {...} },
  "warnings": []
}
```

New series are added as each eKnomics module is updated to read live numbers. Modules ignore series they don't use.
