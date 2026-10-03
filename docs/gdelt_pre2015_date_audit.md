# GDELT pre-2015 date audit

## Scope

This Stage 5B diagnostic reads the existing `gdelt_event_standardized.tsv.gz` without modifying it. It does not remove records, correct dates, classify risks, use GenAI, enrich with GKG, cluster events, or link records across sources.

## Findings

| Check | Result |
|---|---:|
| `event_date < 2015-02-19` | 6,645 |
| `event_date < 1979-01-01` | 1,553 |
| `event_date < 1900-01-01` | 0 |
| Earliest event date | 1920-01-01 |
| 1920-related rows | 1,553 |
| `SOURCE_NATIVE_HISTORICAL_DATE` | 6,645 |
| `SUSPICIOUS_DATE_ENCODING` | 0 |
| `INVALID_DATE` | 0 |
| `UNKNOWN` | 0 |

The year distribution is: 1920 (1,553), 2005 (203), 2006 (131), 2007 (86), 2008 (42), 2009 (36), 2010 (54), 2011 (15), 2012 (21), 2013 (58), 2014 (3,494), and pre-cutoff 2015 (952).

For all 6,645 records, SQLDATE agrees with MonthYear, Year, the year component of FractionDate, and the standardized event date. DATEADDED falls in the acquisition period for every record. In particular, the 1920 records consistently contain `SQLDATE=19200101`, `MonthYear=192001`, `Year=1920`, and a FractionDate whose integer component is 1920, while their DATEADDED values belong to the later acquisition period. This supports a source-native historical-date interpretation rather than an obvious field shift.

`SOURCE_NATIVE_HISTORICAL_DATE` is deliberately narrow: it confirms internal source-field consistency and an acquisition-period DATEADDED. It does not independently verify that the historical event date is factually correct. GlobalEventID is preserved and checked as a numeric identifier, but is not decoded as a date; DATEADDED is the direct field used to distinguish historical event date from acquisition period.

## Downstream recommendation

Stage 5B-2 may add the deterministic field:

`in_analysis_window = 2015-02-19 <= event_date <= 2026-08-31`

Records outside that window should remain in the standardized data but should not enter the formal risk-candidate pool. This is a downstream inclusion rule, not a source-data correction.

## Geo fields

All 18 requested Actor1Geo, Actor2Geo, and ActionGeo fields are present in the standardized table. The audit reports header preservation only; missing values in individual source rows remain source-native missingness.

## Reproduction

Run:

```powershell
$env:PYTHONPATH = "src"
python experiments/risk_corpus_standardization/audit_gdelt_dates.py
```

The command writes the deterministic 100-row stratified sample, JSON summary, and local report under `data/local/processed_risk_corpus/audit/`.
