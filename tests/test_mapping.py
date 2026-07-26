"""
test_mapping.py

Unit tests on the mapping logic against small hand-built fixture FHIR
resources (not full Synthea output) — fast, deterministic, no external deps.
Run with: pytest tests/
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "etl"))

from map_person import map_person  # noqa: E402
from map_condition_occurrence import map_condition_occurrence  # noqa: E402
from concept_mapper import map_code_to_concept  # noqa: E402


@pytest.fixture
def fixture_patient():
    return pd.DataFrame(
        [
            {
                "id": "abc-123",
                "gender": "female",
                "birthDate": "1985-06-15",
                "extension": [
                    {
                        "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                        "extension": [{"url": "text", "valueString": "White"}],
                    },
                    {
                        "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                        "extension": [{"url": "text", "valueString": "Not Hispanic or Latino"}],
                    },
                ],
            }
        ]
    )


def test_map_person_basic_fields(fixture_patient):
    out = map_person(fixture_patient)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["gender_concept_id"] == 8532  # female
    assert row["year_of_birth"] == 1985
    assert row["month_of_birth"] == 6
    assert row["day_of_birth"] == 15
    assert row["race_concept_id"] == 8527  # white
    assert row["ethnicity_concept_id"] == 38003564  # not hispanic or latino


def test_map_person_unknown_gender_falls_back_to_zero():
    df = pd.DataFrame([{"id": "x", "gender": "unknown", "birthDate": "2000-01-01"}])
    out = map_person(df)
    assert out.iloc[0]["gender_concept_id"] == 0


def test_condition_occurrence_drops_orphaned_person():
    conditions = pd.DataFrame(
        [
            {
                "id": "cond-1",
                "subject": {"reference": "Patient/does-not-exist"},
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]},
                "onsetDateTime": "2020-01-01",
            }
        ]
    )
    out = map_condition_occurrence(conditions, person_lookup={}, visit_lookup={})
    assert out.empty  # orphaned row correctly excluded, not silently mapped to person_id=None


def test_concept_mapper_known_code():
    concept_id, name = map_code_to_concept("SNOMED", "38341003")
    assert concept_id == 320128
    assert "hypertension" in name.lower()


def test_concept_mapper_unknown_code_returns_zero():
    concept_id, name = map_code_to_concept("SNOMED", "does-not-exist")
    assert concept_id == 0
