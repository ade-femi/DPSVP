"""
map_person.py — FHIR Patient -> OMOP person

OMOP `person` required fields we populate:
  person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth,
  race_concept_id, ethnicity_concept_id, person_source_value,
  gender_source_value, race_source_value, ethnicity_source_value

Synthea Patient resources include US-Core race/ethnicity extensions, which we
read for the *_source_value fields. Mapping those to OMOP standard
race/ethnicity concept_ids uses OMOP's fixed small vocabulary (not Athena-scale
needed, since there are only a handful of standard race/ethnicity concepts).
"""
from __future__ import annotations

import pandas as pd

from fhir_utils import as_list, stable_id

# OMOP's gender/race/ethnicity concepts are a small fixed set (Vocabulary:
# Gender, Race, Ethnicity) — safe to hardcode, unlike condition/drug/observation
# concepts which number in the millions.
GENDER_CONCEPT = {"male": 8507, "female": 8532}
# OMOP Race vocabulary (subset covering US-Core race categories)
RACE_CONCEPT = {
    "white": 8527,
    "black or african american": 8516,
    "asian": 8515,
    "american indian or alaska native": 8657,
    "native hawaiian or other pacific islander": 8557,
}
ETHNICITY_CONCEPT = {
    "hispanic or latino": 38003563,
    "not hispanic or latino": 38003564,
}
UNKNOWN_CONCEPT_ID = 0


def _extract_us_core_extension(patient: dict, url_fragment: str) -> str | None:
    """Pulls the display text out of a US-Core race/ethnicity extension."""
    for ext in as_list(patient.get("extension")):
        if url_fragment in ext.get("url", ""):
            for sub in ext.get("extension", []):
                if sub.get("url") == "text":
                    return sub.get("valueString")
    return None


def map_person(patients_df: pd.DataFrame) -> pd.DataFrame:
    """Takes the raw Patient resource DataFrame from fhir_loader and returns
    a DataFrame matching OMOP's person table columns."""
    if patients_df.empty:
        return pd.DataFrame()

    rows = []
    for _, p in patients_df.iterrows():
        birth_date = p.get("birthDate")  # 'YYYY-MM-DD'
        year, month, day = (None, None, None)
        if isinstance(birth_date, str) and len(birth_date) == 10:
            year, month, day = birth_date.split("-")

        gender_src = (p.get("gender") or "").lower()
        race_text = (_extract_us_core_extension(p, "us-core-race") or "").lower()
        ethnicity_text = (
            _extract_us_core_extension(p, "us-core-ethnicity") or ""
        ).lower()

        rows.append(
            {
                # person_id assigned later via a stable hash of the FHIR id,
                # so re-runs on the same data are idempotent.
                "person_source_value": p.get("id"),
                "gender_concept_id": GENDER_CONCEPT.get(gender_src, UNKNOWN_CONCEPT_ID),
                "gender_source_value": gender_src,
                "year_of_birth": int(year) if year else None,
                "month_of_birth": int(month) if month else None,
                "day_of_birth": int(day) if day else None,
                "race_concept_id": RACE_CONCEPT.get(race_text, UNKNOWN_CONCEPT_ID),
                "race_source_value": race_text,
                "ethnicity_concept_id": ETHNICITY_CONCEPT.get(
                    ethnicity_text, UNKNOWN_CONCEPT_ID
                ),
                "ethnicity_source_value": ethnicity_text,
            }
        )

    out = pd.DataFrame(rows)
    # Stable integer person_id from the source FHIR id, so the same patient
    # always lands on the same person_id across pipeline re-runs. Must be a
    # real digest, not builtin hash() — see fhir_utils.stable_id.
    out["person_id"] = out["person_source_value"].apply(stable_id)
    return out[
        [
            "person_id",
            "gender_concept_id",
            "year_of_birth",
            "month_of_birth",
            "day_of_birth",
            "race_concept_id",
            "ethnicity_concept_id",
            "person_source_value",
            "gender_source_value",
            "race_source_value",
            "ethnicity_source_value",
        ]
    ]
