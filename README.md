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
| Ch 12 tax figures (`taxYears`): federal brackets and standard deduction, Social Security wage cap, Illinois rate and exemption, tips deduction | yearly, a new block for each tax year (IRS and SSA publish in October; Illinois in December or January) | scheduled Claude check adds the block to `feed/manual.json` |
| Ch 23 income side of GDP (`incomeSide`): GDP, GDI, GNP, NNP, national income, personal income, disposable income, saving, and the income lines between them (NIPA Tables 1.7.5, 1.10, 2.1) | weekly check (data is quarterly) | same Action |
| Ch 12 Illinois gas tax (`ilMotorFuel`) | each July 1 (and whenever the state pauses or restarts an increase) | scheduled Claude check updates `feed/manual.json` |

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
  "manual": { "arm51": {...}, "insuranceIL200k": {...}, "taxRateMadison": {...},
              "taxYears": { "2026": { "checkedAt": "...", "fed": { "std": 16100, "brackets": [[0, 10], [12400, 12], ...] },
                                      "ss": {...}, "il": {...}, "tips": {...}, "fedSource": "...", "ssSource": "...", "ilSource": "..." } },
              "ilMotorFuel": { "cents": 48.3, "date": "2026-07-01", "source": "...", "checkedAt": "...", "next": { "cents": ..., "date": "..." } } },
  "warnings": []
}
```

## Ch 23 income side (`incomeSide`)

One block, every line from the same quarter (`quarter`), in billions of dollars at seasonally adjusted annual rates (`pop` in thousands). BEA's first GDP estimate for a quarter comes out about a month before its first GDI estimate, so GDP alone can run a quarter ahead; that newer GDP appears as `gdpAhead` and is never mixed into `values`. Before publishing, the script checks every accounting identity the module teaches (GDP to GNP to NNP to national income; GDI = GDP minus the statistical discrepancy; GDI as the sum of its income types; national income to personal income to disposable income to saving). If any line is missing or an identity is off by more than $3 billion, last week's block stays up and the usual issue opens.

New series are added as each eKnomics module is updated to read live numbers. Modules ignore series they don't use.

## Ch 12 tax figures: how a new year gets added

Each tax year is one block under `taxYears`, single-filer figures only. The Ch 12 module ("Who Pays?") uses the block for the current calendar year, or the latest year before it, and locks that year for each student once they start, so answers never shift mid-attempt.

The script checks every block before publishing it: brackets must rise in both threshold and rate, every number must be in a sane range, the sources must be filled in, and a new year may move the standard deduction, bracket thresholds and wage cap by only a few percent from the year before (a typo like 161000 for 16100 is refused). A block that fails keeps last week's good copy and opens the usual "Data refresh needs attention" issue. From February on, a missing block for the current year also opens the issue.

`ilMotorFuel` holds the current Illinois gas tax per gallon. To schedule a change in advance, add `"next": {"cents": ..., "date": "YYYY-MM-DD"}`; modules switch on that date.
