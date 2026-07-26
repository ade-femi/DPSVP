# Example quality report

`quality_report_ed46fe24.md` in this directory is a real, unedited report
from a full pipeline run — not a mock-up, and not trimmed to look better.

Reproduce it with:

```bash
python etl/run_pipeline.py --input data/synthea_output/fhir \
                           --report-out reports/ --truncate
```

Your run id, timestamp, and `code version` will differ. Row counts will differ
too unless you generate exactly the same Synthea population (`-p 300`, default
seed produced 365 patient bundles here — Synthea emits extra records for
patients who die mid-simulation).

## How to read it

The four `FLAGGED` rows are expected and are the honest part. They are all
`concept_coverage` checks, and they say that most source codes did not match
the small hand-curated concept map this repo ships in place of the full OHDSI
Athena vocabulary. See "Known limitations" in the root README. A run showing
100% coverage would mean the map had been quietly padded, not that the
pipeline was better.

Two things worth checking in the report, because they are the parts that were
once wrong and are now load-bearing:

- **`fk:person_id` reporting `0 failed` is a real result, not a vacuous one.**
  Validation runs against the full mapped tables before any row is excluded.
  Feed the pipeline a bundle with its `Patient` resource removed and these
  checks go `FLAGGED` with genuine counts.
- **"Rows excluded from load" is empty for this input, and says so
  explicitly.** When rows *are* excluded — unresolvable subject reference, or a
  NULL in a column OMOP declares NOT NULL — they appear there itemised by
  reason. Nothing leaves the pipeline without being counted here.
