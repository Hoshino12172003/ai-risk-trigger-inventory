# Stage 5A raw risk corpus acquisition

## Scope and non-goals

Stage 5A archives source-specific public records for Ecuador, Colombia, and
Peru from 2015-01-01 through 2026-08-31. It does not merge sources, fill
missing values, infer fields, create canonical events, call GenAI, train a
model, run the inventory optimizer, or recalibrate demand/route impacts.

Raw payloads remain under the ignored path
`data/local/raw_risk_corpus/<source>/raw/`. Each local source directory also
contains `manifest.json`, `download_log.csv`, and `schema_snapshot.json`.
Only the compact provenance summaries under
`artifacts/risk_corpus_raw_stage5a/` are versioned.

## Acquisition outcome

| Source | Status | In-scope records | Files | Bytes | Actual in-scope dates |
|---|---:|---:|---:|---:|---|
| GDELT | `API_LIMITATION` | 0 | 0 | 0 | unavailable |
| ReliefWeb | `AUTH_BLOCKED` | 0 | 0 | 0 | unavailable |
| GDACS | `PARTIAL_SOURCE_COVERAGE` | 2,270 | 81 | 12,938,596 | 2015-01-11 to 2026-08-31 |
| DesInventar | `PARTIAL_SOURCE_COVERAGE` | 10,108 | 2 | 21,676,604 | 2015-01-01 to 2018-02-23 |
| USGS | `COMPLETE_FOR_REQUESTED_SCOPE` | 5,676 | 12 | 4,016,169 | 2015-01-01 to 2026-08-31 |
| Copernicus EMS | `COMPLETE_FOR_REQUESTED_SCOPE` | 11 | 13 | 1,615,981 | 2016-04-17 to 2026-08-10 |

The aggregate is 108 downloaded raw files, 40,247,350 bytes, and 18,065
in-scope source records. This count is not a count of unique real-world
events: exact GDACS API duplicates are removed only for its source summary,
while no records are fused across sources.

## Source-specific provenance and limits

### GDELT

The planned corpus uses quarterly windows, the three country names, and three
frozen query families: natural hazards, transport/supply disruption, and
consumer-demand surge. The single official DOC API probe returned HTTP 429.
The interface begins in 2017 and its ArticleList response is capped, so it
cannot provide a complete 2015--2026 corpus through this workflow. The global
GDELT archive was deliberately not downloaded. Record count and duplicates
are therefore both zero, and no actual date range is claimed.

### ReliefWeb

No approved API `appname` was supplied. The collector records the reports and
disasters endpoints plus the intended country, date, concept, and pagination
filters, but does not bypass authorization. Status is `AUTH_BLOCKED`; the
required action is to register and provide a pre-approved `appname`.

### GDACS

Country-filtered event-list requests cover `EQ`, `FL`, `TC`, `DR`, `WF`, and
`VO`, all alert levels, annual date windows, and API paging. The 2,291 returned
discovery records reduce to 2,270 records by exact official
`(eventtype, eventid, episodeid)` identity; 21 exact duplicates are recorded.
To limit API load, separate detail and geometry calls were preregistered only
for the 21 orange/red events. Twenty detail requests succeeded, so the source
is conservatively classified as partial even though the country-filtered
discovery list spans the target window.

### DesInventar

The original official ZIP/XML exports for Colombia and Peru are retained
unchanged. The Ecuador server response was interrupted, and no incomplete file
was retained. Streaming XML inspection reports 54,374 raw Colombia rows and
23,657 raw Peru rows; 9,703 and 405 respectively fall in the requested window.
The actual downloaded records end on 2018-02-23 (Colombia) and 2015-12-31
(Peru). Pre-2015 source rows remain in the unchanged archives and are counted
separately from the 10,108 in-scope rows.

### USGS

Twelve annual FDSN Event API GeoJSON queries use the frozen bounding box
latitude `[-19, 13]`, longitude `[-83, -66]`. They retain official IDs,
timestamps, coordinates, depth, magnitude/type, place, status, tsunami,
network/source, and detail links. The 5,676 events range from magnitude 2.3 to
8.0. No road impact, transport disruption, or model severity is inferred.

### Copernicus EMS

Rapid Mapping and Risk & Recovery activation lists were queried, then filtered
by country and date before detail acquisition. Eleven Rapid Mapping
activations were retained, no Risk & Recovery activation matched, and the
categories are Earthquake, Flood, and Other. No raster or satellite imagery
was downloaded.

## Integrity and reproduction

Every successful file has its endpoint, parameters, timestamp, byte size,
SHA-256, status, and readable record count in the local source log and the
combined committed manifest. Run the deterministic summary-only audit without
network access as follows:

```powershell
python experiments/risk_corpus_raw_stage5a/acquire_stage5a.py `
  --summarize-only `
  --paper2-checkout <path-to-frozen-paper2-checkout>
```

The execution audit pins the Stage 4A base commit, verifies that prior-stage
artifact paths have no diff, and records the frozen Paper-2 SHA and cleanliness.
The five committed summary artifacts disclose all blocked and partial sources;
they are not a substitute for the ignored raw payloads.
