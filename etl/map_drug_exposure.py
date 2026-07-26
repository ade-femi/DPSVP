"""
map_drug_exposure.py — FHIR MedicationRequest -> OMOP drug_exposure

Synthea encodes the drug as an RxNorm code in
MedicationRequest.medicationCodeableConcept.coding[0].
"""
from __future__ import annotations

import pandas as pd

from concept_mapper import map_code_to_concept
from fhir_utils import as_dict

DRUG_TYPE_CONCEPT_ID = 32838  # "Prescription written" per OMOP convention


def _ref_id(reference: str | None) -> str | None:
    if not reference:
        return None
    return reference.split("/")[-1].split(":")[-1]


def _rxnorm_code(med_request: dict) -> str | None:
    concept = as_dict(med_request.get("medicationCodeableConcept"))
    for coding in concept.get("coding", []):
        if "rxnorm" in (coding.get("system") or "").lower():
            return coding.get("code")
    return None


def map_drug_exposure(
    med_requests_df: pd.DataFrame,
    person_lookup: dict[str, int],
    visit_lookup: dict[str, int],
) -> pd.DataFrame:
    if med_requests_df.empty:
        return pd.DataFrame()

    rows = []
    for _, m in med_requests_df.iterrows():
        # Orphans retained with person_id = None so the FK validator can see
        # them; run_pipeline.py excludes them from load after validation.
        person_src = _ref_id(as_dict(m.get("subject")).get("reference"))
        person_id = person_lookup.get(person_src)

        encounter_src = _ref_id(as_dict(m.get("encounter")).get("reference"))
        visit_occurrence_id = visit_lookup.get(encounter_src)

        code = _rxnorm_code(m)
        concept_id, concept_name = (
            map_code_to_concept("RxNorm", code) if code else (0, "Unmapped")
        )

        authored = m.get("authoredOn") or ""
        rows.append(
            {
                "drug_source_value": code,
                "drug_source_concept_name": concept_name,
                "person_id": person_id,
                "visit_occurrence_id": visit_occurrence_id,
                "drug_concept_id": concept_id,
                "drug_exposure_start_date": authored[:10] or None,
                "drug_exposure_end_date": authored[:10] or None,  # Synthea rarely gives an explicit end; refine per-drug if needed
                "drug_type_concept_id": DRUG_TYPE_CONCEPT_ID,
                "status_source_value": m.get("status"),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["drug_exposure_id"] = range(1, len(out) + 1)
    return out[
        [
            "drug_exposure_id",
            "person_id",
            "drug_concept_id",
            "drug_exposure_start_date",
            "drug_exposure_end_date",
            "drug_type_concept_id",
            "visit_occurrence_id",
            "drug_source_value",
            "drug_source_concept_name",
            "status_source_value",
        ]
    ]
