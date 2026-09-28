# Stage 5A Supplement — coverage recovery audit

## Outcome

The supplement is blocked: none of the three disclosed gaps yielded an
accepted supplemental raw record. The accepted Stage 5A baseline remains
unchanged. Consequently, neither `STAGE_5A_SUPPLEMENT_COMPLETE` nor
`STAGE_5A_SUPPLEMENT_COMPLETE_WITH_DISCLOSED_GAPS` is valid under the
preregistered success-label conditions. The execution audit uses the explicit
interim state `STAGE_5A_SUPPLEMENT_BLOCKED` rather than misclassifying a
zero-recovery run as complete.

The frozen batch identifier is `STAGE5A_SUPPLEMENT_001`; the analytical window
is 2015-01-01 through 2026-08-31 and the geography is Ecuador, Colombia, and
Peru. No source fusion, imputation, GenAI/LLM extraction, ML training,
optimization, impact recalibration, event atoms, or canonical events were
created.

## GDELT

The recovery used official GDELT 2.0 historical downloadable files rather than
retrying the DOC API. GDELT 2.0's native start is 2015-02-19, leaving a
`SOURCE_NATIVE_START_DATE_GAP` from 2015-01-01 through 2015-02-18.

The bounded GKG recovery repeatedly failed to complete its first official ZIP
stream. Three bounded runs were stopped; no partial GKG file was accepted. The
independent Event-family audit then inspected one official 12:00 UTC file per
frozen monthly anchor, plus the native-start and requested-end anchors. An
8 MB compressed-file safety cap prevented uncontrolled archive acquisition.

- Event files inspected successfully: 132
- Event records inspected: 187,375
- Event records retained: 0
- GKG records retained: 0
- stored supplemental files/bytes: 0 / 0
- actual retained coverage and geography counts: unavailable

Every Event record was tested against both structured GDELT geography fields
and the frozen broad retrieval terms. The zero result was not repaired by
adding post-hoc keywords, using textual inference, or weakening the geographic
rule. Empty filtered placeholders were deleted and are not counted as raw
files. The acquisition audit retains the official source-file URLs, HTTP
status, source ZIP hashes, inspected/discarded counts, and unavailable anchors.

## ReliefWeb

Neither `RELIEFWEB_APPNAME` nor `RELIEFWEB_API_APPNAME` was present. No secret
value was read, printed, or committed, and no unauthorized request was made.
Status remains `AUTH_BLOCKED`; disaster and report counts are both zero.

Required action: supply a pre-approved ReliefWeb appname through one of those
environment variables, then implement/review the authenticated deterministic
reports/disasters pagination before acquisition. Publisher pages must not be
scraped as a substitute.

## DesInventar Ecuador

Exactly two fresh requests were made to the official Ecuador ZIP URL. Both
returned HTTP 200 but ended before the advertised 30,917,488 bytes:

1. 3,795,624 bytes received;
2. 3,819,464 bytes received.

Both incomplete `.part` files were deleted. No Ecuador ZIP, SHA-256, records,
date coverage, disaster types, or province coverage can therefore be claimed.
Colombia and Peru were not redownloaded or modified.

## Combined six-source coverage

The combined report is read-only and does not merge source tables. It remains
identical in raw-data volume to the accepted Stage 5A baseline: 18,065
in-window records, 108 raw files, and 40,247,350 bytes.

| Source | Final status | Records | Files | Bytes |
|---|---|---:|---:|---:|
| GDELT | `DOWNLOAD_FAILED` | 0 | 0 | 0 |
| ReliefWeb | `AUTH_BLOCKED` | 0 | 0 | 0 |
| GDACS | `PARTIAL_SOURCE_COVERAGE` | 2,270 | 81 | 12,938,596 |
| DesInventar | `PARTIAL_SOURCE_COVERAGE` | 10,108 | 2 | 21,676,604 |
| USGS | `COMPLETE_FOR_REQUESTED_SCOPE` | 5,676 | 12 | 4,016,169 |
| Copernicus EMS | `COMPLETE_FOR_REQUESTED_SCOPE` | 11 | 13 | 1,615,981 |

## Provenance and integrity

Supplement metadata lives locally beside each affected source under
`data/local/raw_risk_corpus/<source>/supplement/`; raw paths remain ignored by
Git. Versioned audit files live under
`artifacts/risk_corpus_stage5a_supplement/`. The execution audit verifies all
108 accepted baseline raw hashes before and after recovery, confirms no diff in
accepted Stage 5A or Stage 1–4A artifacts, and records the frozen Paper-2 SHA
and clean worktree state.

The supplement can be finalized from existing local manifests without any
network request:

```powershell
python experiments/risk_corpus_stage5a_supplement/acquire_supplement.py `
  --finalize-existing `
  --paper2-checkout <path-to-frozen-paper2-checkout>
```
