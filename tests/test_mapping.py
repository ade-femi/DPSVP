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
sys.path.insert(0, str(Path(__file__).parent.parent / "governance"))

from map_person import map_person  # noqa: E402
from map_condition_occurrence import map_condition_occurrence  # noqa: E402
from concept_mapper import map_code_to_concept  # noqa: E402
from run_pipeline import exclude_orphans  # noqa: E402
import validators  # noqa: E402


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


def _condition(cond_id: str, patient_ref: str) -> dict:
    return {
        "id": cond_id,
        "subject": {"reference": patient_ref},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]},
        "onsetDateTime": "2020-01-01",
    }


def test_orphaned_rows_are_retained_for_validation_not_dropped_silently():
    """Orphans must survive mapping so the FK validator can count them.

    Regression test for the governance hole where mappers dropped orphans on
    sight: that made check_referential_integrity structurally incapable of
    failing, so it reported '0 orphans, PASS' regardless of input.
    """
    conditions = pd.DataFrame([_condition("cond-1", "Patient/does-not-exist")])
    out = map_condition_occurrence(conditions, person_lookup={}, visit_lookup={})
    assert len(out) == 1, "orphan must be retained, not dropped inside the mapper"
    assert pd.isna(out.iloc[0]["person_id"]), "orphan person_id must be null, never guessed"


def test_fk_check_actually_detects_orphans():
    """The FK check must fail on orphaned input — not pass vacuously."""
    conditions = pd.DataFrame(
        [
            _condition("keep", "Patient/p1"),
            _condition("orphan-1", "Patient/ghost-1"),
            _condition("orphan-2", "Patient/ghost-2"),
        ]
    )
    mapped = map_condition_occurrence(conditions, {"p1": 111}, {})
    result = validators.check_referential_integrity(
        mapped, "person_id", {111}, "condition_occurrence"
    )
    assert result.n_total == 3
    assert result.n_failed == 2
    assert not result.passed


def test_exclude_orphans_removes_them_and_reports_the_count():
    """After validation, orphans are excluded exactly once, with a count."""
    conditions = pd.DataFrame(
        [_condition("keep", "Patient/p1"), _condition("orphan", "Patient/ghost")]
    )
    mapped = map_condition_occurrence(conditions, {"p1": 111}, {})
    kept, n_excluded = exclude_orphans(mapped, "condition_occurrence")
    assert n_excluded == 1
    assert len(kept) == 1
    assert kept.iloc[0]["person_id"] == 111
    assert kept["person_id"].dtype == "int64", "person_id must be int, not float, after filtering"


def test_concept_mapper_known_code():
    concept_id, name = map_code_to_concept("SNOMED", "38341003")
    assert concept_id == 320128
    assert "hypertension" in name.lower()


def test_concept_mapper_unknown_code_returns_zero():
    concept_id, name = map_code_to_concept("SNOMED", "does-not-exist")
    assert concept_id == 0
