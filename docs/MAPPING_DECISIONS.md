# FHIR → OMOP Mapping Decisions

This is the audit trail for *why* each field maps the way it does — the
document a reviewer or auditor would actually want, and the thing that turns
"I wrote an ETL script" into "I made and documented defensible mapping
decisions."

## person (from FHIR Patient)

| OMOP field | FHIR source | Decision / rationale |
|---|---|---|
| `person_id` | hash of `Patient.id` | Deterministic hash so re-runs are idempotent — same source patient always gets the same person_id, rather than a fresh auto-increment each run. |
| `gender_concept_id` | `Patient.gender` | Mapped via fixed OMOP Gender vocabulary (only 2 standard concepts in Synthea's output: male/female). Unrecognized values → `0`, not guessed. |
| `year/month/day_of_birth` | `Patient.birthDate` | Split from ISO date string. Nulled if Synthea omits the field (rare). |
| `race_concept_id` / `ethnicity_concept_id` | US-Core race/ethnicity extensions | Synthea populates these extensions in every Patient resource; text value matched against OMOP's fixed race/ethnicity vocabulary. |

## visit_occurrence (from FHIR Encounter)

| OMOP field | FHIR source | Decision / rationale |
|---|---|---|
| `visit_concept_id` | `Encounter.class.code` | Mapped AMB→Outpatient, EMER→ER, IMP→Inpatient. Anything unrecognized defaults to Outpatient (9202) rather than failing — logged so the default's usage rate is visible in the quality report. |
| `visit_type_concept_id` | (fixed) | Always `32817` ("EHR"), the standard OMOP convention for EHR-derived records — this is metadata about provenance, not something FHIR encodes per-encounter. |

## condition_occurrence (from FHIR Condition)

| OMOP field | FHIR source | Decision / rationale |
|---|---|---|
| `condition_concept_id` | `Condition.code.coding[system=SNOMED]` | Routed through `concept_mapper`. Synthea always encodes conditions in SNOMED-CT, so we don't fall back to other systems. |
| `condition_source_value` | same SNOMED code, unmapped | Always preserved verbatim — the point of a `*_source_value` field in OMOP is exactly this: never lose the original code even after mapping to standard concepts. |
| `condition_start_date` | `Condition.onsetDateTime` | Truncated to date (OMOP condition_occurrence is date-, not datetime-, grained). |

## drug_exposure (from FHIR MedicationRequest)

| OMOP field | FHIR source | Decision / rationale |
|---|---|---|
| `drug_concept_id` | `medicationCodeableConcept.coding[system=RxNorm]` | Synthea always uses RxNorm for medications. |
| `drug_exposure_end_date` | same as start date (`authoredOn`) | **Known simplification.** Synthea's FHIR export doesn't reliably give an explicit end date for every prescription; using start==end is a conservative placeholder rather than fabricating a duration. A production mapper would pull `dispenseRequest.expectedSupplyDuration` where present. |

## measurement vs. observation (both from FHIR Observation)

| Decision | Rationale |
|---|---|
| Route to `measurement` if `valueQuantity` present, else `observation` | This matches OMOP's own domain-assignment convention: numeric/quantitative results are measurements, everything else (categorical findings, free text) is an observation. |
| `value_as_number` / `unit_source_value` only on measurement rows | Observation rows use `value_as_string` instead — OMOP's observation table isn't structured for numeric values the way measurement is. |

## Cross-cutting decisions

- **Orphaned records are excluded from load, not force-linked.** If a
  Condition references a Patient not present in the loaded person set (can
  happen with partial/filtered input), the row is dropped and the drop is
  implicit in the referential-integrity check's row count — never silently
  attached to a wrong or null person_id.
- **`concept_id = 0` is used, never left null**, for anything that fails
  vocabulary mapping — this is the OMOP-standard convention for "no matching
  concept" and keeps every row present-and-flagged rather than missing.
- **Every `*_concept_id` column has a paired `*_source_value` column**
  wherever OMOP's schema supports it, so the original FHIR-coded value is
  always recoverable even after standardization.
