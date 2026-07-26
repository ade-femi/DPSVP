"""
map_condition_occurrence.py — FHIR Condition -> OMOP condition_occurrence

Synthea encodes conditions with a SNOMED-CT code in
Condition.code.coding[0]. We route that through concept_mapper to get the
OMOP standard concept_id, and keep the raw code as condition_source_value
for auditability (never overwrite source data — governance principle).
"""
from __future__ import annotations

import pandas as pd

from concept_mapper import map_code_to_concept
from fhir_utils import as_dict, as_str

CONDITION_TYPE_CONCEPT_ID = 32817  # "EHR" — record derived from an EHR encounter


def _ref_id(reference: str | None) -> str | None:
    if not reference:
        return None
    return reference.split("/")[-1].split(":")[-1]


def _snomed_code(condition: dict) -> str | None:
    for coding in as_dict(condition.get("code")).get("coding", []):
        if "snomed" in (coding.get("system") or "").lower():
            return coding.get("code")
    return None


def map_condition_occurrence(
    conditions_df: pd.DataFrame,
    person_lookup: dict[str, int],
    visit_lookup: dict[str, int],
) -> pd.DataFrame:
    if conditions_df.empty:
        return pd.DataFrame()

    rows = []
    for _, c in conditions_df.iterrows():
        # Orphans (subject references a patient not in the person set) are
        # deliberately RETAINED here with person_id = None, so that
        # governance/validators.py::check_referential_integrity can actually
        # observe and count them. run_pipeline.py excludes them from the load
        # after validation, logging the count. Dropping them here instead
        # would make the FK check structurally incapable of failing.
        person_src = _ref_id(as_dict(c.get("subject")).get("reference"))
        person_id = person_lookup.get(person_src)

        encounter_src = _ref_id(as_dict(c.get("encounter")).get("reference"))
        visit_occurrence_id = visit_lookup.get(encounter_src)

        code = _snomed_code(c)
        concept_id, concept_name = (
            map_code_to_concept("SNOMED", code) if code else (0, "Unmapped")
        )

        onset = as_str(c.get("onsetDateTime"))
        rows.append(
            {
                "condition_source_value": code,
                "condition_source_concept_name": concept_name,
                "person_id": person_id,
                "visit_occurrence_id": visit_occurrence_id,
                "condition_concept_id": concept_id,
                "condition_start_date": onset[:10] or None,
                "condition_type_concept_id": CONDITION_TYPE_CONCEPT_ID,
                "condition_status_source_value": (
                    as_dict(as_dict(c.get("clinicalStatus")).get("coding", [{}])[0]).get("code")
                ),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["condition_occurrence_id"] = range(1, len(out) + 1)
    return out[
        [
            "condition_occurrence_id",
            "person_id",
            "condition_concept_id",
            "condition_start_date",
            "condition_type_concept_id",
            "visit_occurrence_id",
            "condition_source_value",
            "condition_source_concept_name",
            "condition_status_source_value",
        ]
    ]
