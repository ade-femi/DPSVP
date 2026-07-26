# Architecture

## Data flow

1. **Synthea** (`setup/01_get_synthea.sh`, `setup/02_generate_patients.sh`)
   generates N synthetic patients. Each patient's full clinical history —
   demographics, encounters, conditions, medications, observations — is
   written as one FHIR R4 transaction Bundle (one JSON file per patient) to
   `data/synthea_output/fhir/`.

2. **`etl/fhir_loader.py`** reads every bundle, flattens `entry[].resource`
   into per-resource-type lists, and wraps each as a DataFrame of raw
   (unflattened) FHIR JSON. This is the "staging" layer — nothing here is
   OMOP-shaped yet.

3. **`etl/map_*.py`** — one module per OMOP domain table. Each takes the
   relevant staging DataFrame(s) plus lookup dicts for already-mapped parent
   entities (`person_lookup`, `visit_lookup`) and returns a DataFrame shaped
   exactly like the target OMOP table's columns. Mapping order matters
   because of foreign keys:

   ```
   person  →  visit_occurrence  →  condition_occurrence
                                 →  drug_exposure
                                 →  measurement / observation
   ```

4. **`etl/concept_mapper.py`** is the vocabulary-mapping seam used by every
   domain mapper — SNOMED/LOINC/RxNorm source codes go in, OMOP standard
   `concept_id`s come out. See its docstring for the Athena-vocabulary
   limitation and the production integration point.

5. **`governance/validators.py`** runs four check families against every
   mapped table before load: not-null on required fields, referential
   integrity against `person_id`, date-range sanity, and concept-mapping
   coverage.

   **Validation order is deliberate.** The domain mappers do *not* discard
   rows whose subject reference can't be resolved to a person; they keep them
   with `person_id = None`. That is what lets `check_referential_integrity`
   actually observe and count them. If the mappers dropped orphans on sight
   — the obvious implementation — the frame reaching the FK check would be
   clean by construction and the check could never fail, reporting a
   reassuring `0 orphans` no matter how broken the input was.

   Orphans are excluded only *after* validation, in a single place
   (`exclude_orphans` in `etl/run_pipeline.py`), which logs a warning per
   affected table and returns a count. Those counts appear in the quality
   report under "Rows excluded from load". Every other row loads with its
   validation status recorded.

6. **`etl/run_pipeline.py`** is the orchestrator: loads → maps in dependency
   order → validates → loads into Postgres → writes the quality report.

7. **`governance/quality_report.py`** turns the validation results into a
   timestamped Markdown file under `reports/` — the auditable artifact of
   the run.

## Why this table subset

`person`, `visit_occurrence`, `condition_occurrence`, `drug_exposure`,
`measurement`, `observation` covers the OMOP domains that map most directly
and unambiguously from FHIR's most common resource types, and are the tables
almost every downstream OHDSI analysis (cohort definition, characterization,
population-level estimation) actually queries first. That's why this is the
minimum viable *correct* slice rather than an arbitrary subset.

## Future work (explicitly out of scope for this reference build)

- **Full Athena vocabulary load** — replace `COMMON_CONCEPT_MAP` with the
  real `CONCEPT`/`CONCEPT_RELATIONSHIP`/`CONCEPT_ANCESTOR` tables from
  https://athena.ohdsi.org, and use `CONCEPT_RELATIONSHIP` to resolve
  non-standard → standard concept mappings properly (right now we assume the
  hand-curated map already points to standard concepts).
- **OHDSI Data Quality Dashboard (DQD)** — run the populated CDM through
  OHDSI's own DQD R package for a standards-recognized quality score, rather
  than only this repo's custom validators.
- **Procedure, Immunization, DeviceExposure domains** — same mapping pattern,
  not yet implemented.
- **Incremental/idempotent loads** — current pipeline assumes a fresh schema
  per run; a real pipeline needs upsert logic and change-data-capture.
- **ATLAS cohort definition** — once loaded, define a cohort in OHDSI ATLAS
  against this synthetic CDM to demonstrate the full "data → cohort →
  analysis" chain, which is the same chain used against real OMOP-mapped data
  including NHANES-derived cohorts.
