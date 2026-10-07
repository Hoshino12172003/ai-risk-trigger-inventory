# Local data retention audit

This audit is a non-destructive classification of the 833 files found in the
local third-paper data inventory. It does not move, overwrite, or delete any
file. The proposed formal data chain is:

`raw source -> curated record -> source event -> canonical event -> model input`

## Retention result

| Retention class | Files |
| --- | ---: |
| KEEP_RAW | 166 |
| KEEP_CURATED | 20 |
| KEEP_EVENT | 2 |
| KEEP_MODEL | 0 |
| KEEP_BASELINE | 15 |
| SUMMARY_ONLY | 72 |
| ARCHIVE_OPTIONAL | 22 |
| DISCARD_AFTER_APPROVAL | 534 |
| MANUAL_REVIEW | 2 |
| **Total** | **833** |

The repository tracks the deterministic audit scripts and compact aggregate
tables only. The complete file inventory, migration plan, and retention
manifest contain machine-specific absolute paths and therefore remain local.
They can be regenerated with:

```bash
python experiments/risk_corpus_standardization/prepare_data_layout_audit.py
python experiments/risk_corpus_standardization/prepare_simplified_retention_manifest.py
```

No file classified as `DISCARD_AFTER_APPROVAL` has been deleted. That label is
only a future retention recommendation and still requires explicit approval.
The current audit also confirms that model-ready artifacts had not yet been
identified in the 833-file snapshot.

The compact outputs are:

- `artifacts/data_retention_audit/retention_class_counts.csv`
- `artifacts/data_retention_audit/screening_summary.csv`
