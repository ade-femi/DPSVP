"""
map_observation.py — FHIR Observation -> OMOP measurement + observation

OMOP splits what FHIR calls "Observation" into two tables based on whether
the value is a structured, typically-numeric result:
  - numeric/quantity results (vitals, labs) -> OMOP `measurement`
  - everything else (free text, non-numeric findings)   -> OMOP `observation`

We route on the presence of `valueQuantity` in the FHIR resource, which is
how Synthea encodes vitals and lab results.
"""
from __future__ import annotations

import pandas as pd

from concept_mapper import map_code_to_concept
from fhir_utils import as_dict

MEASUREMENT_TYPE_CONCEPT_ID = 32817  # "EHR"
OBSERVATION_TYPE_CONCEPT_ID = 32817
# OMOP's observation.value_as_string is varchar(60); Synthea free-text
# observation values (e.g. survey responses) routinely exceed that, so
# truncate rather than let the DB insert fail on StringDataRightTruncation.
VALUE_AS_STRING_MAX_LEN = 60


def _ref_id(reference: str | None) -> str | None:
    if not reference:
        return None
    return reference.split("/")[-1].split(":")[-1]


def _loinc_code(obs: dict) -> str | None:
    for coding in as_dict(obs.get("code")).get("coding", []):
        if "loinc" in (coding.get("system") or "").lower():
            return coding.get("code")
    return None


def map_observations(
    observations_df: pd.DataFrame,
    person_lookup: dict[str, int],
    visit_lookup: dict[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (measurement_df, observation_df)."""
    if observations_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    measurement_rows = []
    observation_rows = []

    for _, o in observations_df.iterrows():
        # Orphans retained with person_id = None so the FK validator can see
        # them; run_pipeline.py excludes them from load after validation.
        person_src = _ref_id(as_dict(o.get("subject")).get("reference"))
        person_id = person_lookup.get(person_src)

        encounter_src = _ref_id(as_dict(o.get("encounter")).get("reference"))
        visit_occurrence_id = visit_lookup.get(encounter_src)

        code = _loinc_code(o)
        concept_id, concept_name = (
            map_code_to_concept("LOINC", code) if code else (0, "Unmapped")
        )
        date = (o.get("effectiveDateTime") or "")[:10] or None
        value_qty = as_dict(o.get("valueQuantity"))

        if value_qty:
            measurement_rows.append(
                {
                    "measurement_source_value": code,
                    "measurement_source_concept_name": concept_name,
                    "person_id": person_id,
                    "visit_occurrence_id": visit_occurrence_id,
                    "measurement_concept_id": concept_id,
                    "measurement_date": date,
                    "measurement_type_concept_id": MEASUREMENT_TYPE_CONCEPT_ID,
                    "value_as_number": value_qty.get("value"),
                    "unit_source_value": value_qty.get("unit"),
                }
            )
        else:
            value_text = as_dict(o.get("valueCodeableConcept")).get("text")
            if value_text is None and isinstance(o.get("valueString"), str):
                value_text = o.get("valueString")
            if isinstance(value_text, str) and len(value_text) > VALUE_AS_STRING_MAX_LEN:
                value_text = value_text[:VALUE_AS_STRING_MAX_LEN]

            observation_rows.append(
                {
                    "observation_source_value": code,
                    "observation_source_concept_name": concept_name,
                    "person_id": person_id,
                    "visit_occurrence_id": visit_occurrence_id,
                    "observation_concept_id": concept_id,
                    "observation_date": date,
                    "observation_type_concept_id": OBSERVATION_TYPE_CONCEPT_ID,
                    "value_as_string": value_text,
                }
            )

    m_df = pd.DataFrame(measurement_rows)
    if not m_df.empty:
        m_df["measurement_id"] = range(1, len(m_df) + 1)
        m_df = m_df[
            [
                "measurement_id",
                "person_id",
                "measurement_concept_id",
                "measurement_date",
                "measurement_type_concept_id",
                "visit_occurrence_id",
                "value_as_number",
                "unit_source_value",
                "measurement_source_value",
                "measurement_source_concept_name",
            ]
        ]

    o_df = pd.DataFrame(observation_rows)
    if not o_df.empty:
        o_df["observation_id"] = range(1, len(o_df) + 1)
        o_df = o_df[
            [
                "observation_id",
                "person_id",
                "observation_concept_id",
                "observation_date",
                "observation_type_concept_id",
                "visit_occurrence_id",
                "value_as_string",
                "observation_source_value",
                "observation_source_concept_name",
            ]
        ]

    return m_df, o_df
