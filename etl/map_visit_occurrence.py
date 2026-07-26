"""
map_visit_occurrence.py — FHIR Encounter -> OMOP visit_occurrence

Every other clinical table (condition_occurrence, drug_exposure, observation)
has an optional visit_occurrence_id foreign key, so this mapper needs to run
before those — the orchestrator (run_pipeline.py) enforces that order.
"""
from __future__ import annotations

import pandas as pd

from fhir_utils import as_dict

# FHIR Encounter.class -> OMOP Visit vocabulary concept_id
VISIT_TYPE_CONCEPT = {
    "AMB": 9202,       # Outpatient Visit
    "EMER": 9203,       # Emergency Room Visit
    "IMP": 9201,        # Inpatient Visit
    "WELLNESS": 9202,    # treat wellness encounters as outpatient
    "AMBULATORY": 9202,
}
DEFAULT_VISIT_CONCEPT = 9202  # Outpatient Visit — reasonable default, logged if used
EHR_TYPE_CONCEPT_ID = 32817   # "EHR" as the visit_type_concept_id, standard OMOP convention


def _patient_ref_to_source_value(reference: str | None) -> str | None:
    """FHIR references look like 'Patient/<uuid>' or 'urn:uuid:<uuid>'."""
    if not reference:
        return None
    return reference.split("/")[-1].split(":")[-1]


def map_visit_occurrence(
    encounters_df: pd.DataFrame, person_lookup: dict[str, int]
) -> pd.DataFrame:
    """person_lookup: {person_source_value -> person_id}, from map_person output."""
    if encounters_df.empty:
        return pd.DataFrame()

    rows = []
    for _, e in encounters_df.iterrows():
        # Orphaned encounters (no matching person) are retained with
        # person_id = None so check_referential_integrity can count them;
        # run_pipeline.py excludes them from load after validation and builds
        # visit_lookup only from non-orphaned visits.
        patient_src = _patient_ref_to_source_value(
            as_dict(e.get("subject")).get("reference")
        )
        person_id = person_lookup.get(patient_src)

        period = as_dict(e.get("period"))
        class_code = as_dict(e.get("class")).get("code", "")

        rows.append(
            {
                "visit_source_value": e.get("id"),
                "person_id": person_id,
                "visit_start_date": (period.get("start") or "")[:10] or None,
                "visit_end_date": (period.get("end") or period.get("start") or "")[:10]
                or None,
                "visit_concept_id": VISIT_TYPE_CONCEPT.get(class_code, DEFAULT_VISIT_CONCEPT),
                "visit_type_concept_id": EHR_TYPE_CONCEPT_ID,
                "visit_class_source_value": class_code,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["visit_occurrence_id"] = out["visit_source_value"].apply(
        lambda s: abs(hash(("visit", s))) % (10**9)
    )
    return out[
        [
            "visit_occurrence_id",
            "person_id",
            "visit_concept_id",
            "visit_start_date",
            "visit_end_date",
            "visit_type_concept_id",
            "visit_source_value",
            "visit_class_source_value",
        ]
    ]
