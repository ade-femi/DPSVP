# Data Quality Report — run `78f03925`

Generated: 2026-07-26T17:14:19Z

## Summary

- Checks run: **17**
- Passed: **13**
- Failed/flagged: **4**

## Provenance

|                       |                                           |
|-----------------------|-------------------------------------------|
| input directory       | /home/user/DPSVP/data/synthea_output/fhir |
| patient bundles read  | 365                                       |
| target schema         | cdm                                       |
| target database       | localhost:5432/omop_cdm                   |
| loaded to database    | yes                                       |
| truncated before load | yes (--truncate)                          |
| code version          | 03bb174                                   |

## Row counts by OMOP table

| table                |   row_count |
|----------------------|-------------|
| person               |         365 |
| visit_occurrence     |       21819 |
| condition_occurrence |       13730 |
| drug_exposure        |       19321 |
| measurement          |      158803 |
| observation          |       46536 |

## Validation results

| table                | check                                   | status   |   n_total |   n_failed | failure_rate   | details                                                                                                                                                |
|----------------------|-----------------------------------------|----------|-----------|------------|----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| person               | not_null:person_id                      | PASS     |       365 |          0 | 0.0%           |                                                                                                                                                        |
| person               | not_null:gender_concept_id              | PASS     |       365 |          0 | 0.0%           |                                                                                                                                                        |
| visit_occurrence     | fk:person_id                            | PASS     |     21819 |          0 | 0.0%           |                                                                                                                                                        |
| visit_occurrence     | date_sanity:visit_start_date            | PASS     |     21819 |          0 | 0.0%           |                                                                                                                                                        |
| visit_occurrence     | concept_coverage:visit_concept_id       | PASS     |     21819 |          0 | 0.0%           |                                                                                                                                                        |
| condition_occurrence | fk:person_id                            | PASS     |     13730 |          0 | 0.0%           |                                                                                                                                                        |
| condition_occurrence | date_sanity:condition_start_date        | PASS     |     13730 |          0 | 0.0%           |                                                                                                                                                        |
| condition_occurrence | concept_coverage:condition_concept_id   | FLAGGED  |     13730 |      13473 | 98.13%         | Only 1.9% of rows mapped to a standard concept (threshold: 50.0%) — expected on the bundled demo concept map; load full Athena vocab to improve this.  |
| drug_exposure        | fk:person_id                            | PASS     |     19321 |          0 | 0.0%           |                                                                                                                                                        |
| drug_exposure        | date_sanity:drug_exposure_start_date    | PASS     |     19321 |          0 | 0.0%           |                                                                                                                                                        |
| drug_exposure        | concept_coverage:drug_concept_id        | FLAGGED  |     19321 |      17410 | 90.11%         | Only 9.9% of rows mapped to a standard concept (threshold: 50.0%) — expected on the bundled demo concept map; load full Athena vocab to improve this.  |
| measurement          | fk:person_id                            | PASS     |    158803 |          0 | 0.0%           |                                                                                                                                                        |
| measurement          | date_sanity:measurement_date            | PASS     |    158803 |          0 | 0.0%           |                                                                                                                                                        |
| measurement          | concept_coverage:measurement_concept_id | FLAGGED  |    158803 |     138985 | 87.52%         | Only 12.5% of rows mapped to a standard concept (threshold: 50.0%) — expected on the bundled demo concept map; load full Athena vocab to improve this. |
| observation          | fk:person_id                            | PASS     |     46536 |          0 | 0.0%           |                                                                                                                                                        |
| observation          | date_sanity:observation_date            | PASS     |     46536 |          0 | 0.0%           |                                                                                                                                                        |
| observation          | concept_coverage:observation_concept_id | FLAGGED  |     46536 |      46536 | 100.0%         | Only 0.0% of rows mapped to a standard concept (threshold: 50.0%) — expected on the bundled demo concept map; load full Athena vocab to improve this.  |

## Rows excluded from load

No rows were excluded — every mapped row resolved to a `person_id` and carried all columns OMOP requires.

## Notes

- `concept_coverage` flags are expected on this reference build — it ships a small hand-curated concept map, not the full Athena vocabulary. See `etl/concept_mapper.py` for the production seam.
- **The bundled concept map is UNVERIFIED.** Its `concept_id` values were entered by hand and have not been checked against https://athena.ohdsi.org, so rows counted as *mapped* above may still carry an incorrect standard concept. Coverage percentages measure how many rows found *a* mapping, not that the mapping is right. Treat mapped-concept semantics as unvalidated until `concept_mapper.VERIFIED_AGAINST_ATHENA` is True.
- Validation above runs against the **full** mapped tables, before any row is excluded, so the `fk:person_id` counts reflect the real input. Exclusions happen afterwards and are itemised in the section above — nothing is dropped without appearing in this report.
- Deciding what to do about a given failure class (quarantine, block load, accept) is a downstream governance policy decision, not something this ETL layer should decide unilaterally.