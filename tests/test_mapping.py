"""
test_mapping.py

Unit tests on the mapping logic against small hand-built fixture FHIR
resources (not full Synthea output) — fast, deterministic, no external deps.
Run with: pytest tests/
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ETL_DIR = Path(__file__).parent.parent / "etl"
sys.path.insert(0, str(ETL_DIR))
sys.path.insert(0, str(Path(__file__).parent.parent / "governance"))

from map_person import map_person  # noqa: E402
from map_condition_occurrence import map_condition_occurrence  # noqa: E402
from concept_mapper import COMMON_CONCEPT_MAP, map_code_to_concept  # noqa: E402
import concept_mapper  # noqa: E402
from run_pipeline import exclude_unloadable  # noqa: E402
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


def test_exclude_unloadable_removes_orphans_and_reports_the_reason():
    """After validation, orphans are excluded exactly once, with a reason."""
    conditions = pd.DataFrame(
        [_condition("keep", "Patient/p1"), _condition("orphan", "Patient/ghost")]
    )
    mapped = map_condition_occurrence(conditions, {"p1": 111}, {})
    kept, reasons = exclude_unloadable(mapped, "condition_occurrence")
    assert reasons == {"unresolved_person_id": 1}
    assert len(kept) == 1
    assert kept.iloc[0]["person_id"] == 111
    assert kept["person_id"].dtype == "int64", "person_id must be int, not float, after filtering"


def test_exclude_unloadable_removes_rows_missing_omop_required_columns():
    """A row with no onset date cannot be inserted — OMOP requires it.

    Regression test: a Synthea MedicationRequest with no authoredOn produced a
    null drug_exposure_start_date, and because psycopg2 sends a whole page in
    one statement, that single row aborted the entire table's load with a
    NotNullViolation instead of being excluded and counted.
    """
    conditions = pd.DataFrame(
        [
            _condition("dated", "Patient/p1"),
            # same patient, but no onsetDateTime at all
            {
                "id": "undated",
                "subject": {"reference": "Patient/p1"},
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]},
            },
        ]
    )
    mapped = map_condition_occurrence(conditions, {"p1": 111}, {})
    assert len(mapped) == 2, "both rows must survive mapping so validators see them"

    kept, reasons = exclude_unloadable(mapped, "condition_occurrence")
    assert reasons == {"missing_required_condition_start_date": 1}
    assert len(kept) == 1
    assert kept.iloc[0]["condition_source_value"] is not None


def test_exclusion_reasons_do_not_double_count_a_row():
    """A row that is both orphaned and incomplete is counted under one reason."""
    conditions = pd.DataFrame(
        [{"id": "both", "subject": {"reference": "Patient/ghost"}}]  # no person, no code, no date
    )
    mapped = map_condition_occurrence(conditions, {"p1": 111}, {})
    kept, reasons = exclude_unloadable(mapped, "condition_occurrence")
    assert kept.empty
    assert sum(reasons.values()) == 1, f"row counted more than once: {reasons}"
    assert reasons == {"unresolved_person_id": 1}


def test_surrogate_keys_are_stable_across_processes():
    """person_id / visit_occurrence_id must survive a change of PYTHONHASHSEED.

    Regression test for the use of builtin hash() for surrogate keys. Python
    salts string hashing per process, so the previous implementation produced
    a different person_id for the same patient on every run — re-loading the
    same source data would duplicate every person under a fresh id, which is
    exactly what the documented idempotency claim rules out. A single-process
    assertion cannot catch this; the seed has to differ.
    """
    snippet = (
        "import sys; sys.path.insert(0, %r);"
        "from fhir_utils import stable_id;"
        "print(stable_id('abc-123'), stable_id('visit', 'enc-9'))" % str(ETL_DIR)
    )
    outputs = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True, text=True, check=True, env=env,
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, f"surrogate keys varied across hash seeds: {outputs}"


def test_person_id_is_reproducible_for_the_same_patient(fixture_patient):
    first = map_person(fixture_patient)["person_id"].tolist()
    second = map_person(fixture_patient)["person_id"].tolist()
    assert first == second


def test_concept_mapper_known_code():
    concept_id, name = map_code_to_concept("SNOMED", "38341003")
    assert concept_id == 320128
    assert "hypertension" in name.lower()


def test_concept_mapper_unknown_code_returns_zero():
    concept_id, name = map_code_to_concept("SNOMED", "does-not-exist")
    assert concept_id == 0


def test_no_concept_id_maps_to_two_different_names():
    """A repeated concept_id under two names means at least one row is wrong.

    This caught a real error: acute bronchitis and pneumonia both carried
    concept_id 255848. Distinct conditions cannot share a standard concept,
    so the collision was proof of a bad entry without needing Athena access.
    """
    names_by_id: dict[int, set[str]] = {}
    for (_vocab, _code), (concept_id, name) in COMMON_CONCEPT_MAP.items():
        names_by_id.setdefault(concept_id, set()).add(name)

    collisions = {cid: names for cid, names in names_by_id.items() if len(names) > 1}
    assert not collisions, f"concept_id reused under conflicting names: {collisions}"


def test_removed_bronchitis_code_falls_through_to_unmapped():
    """The dropped entry must degrade to concept_id 0, not a guessed value."""
    concept_id, _name = map_code_to_concept("SNOMED", "10509002")
    assert concept_id == 0


def test_unverified_vocabulary_is_declared_not_hidden():
    """The map ships unverified; that must be discoverable programmatically."""
    assert concept_mapper.VERIFIED_AGAINST_ATHENA is False, (
        "If the concept map has now been verified against Athena, update this "
        "test along with the flag — don't flip the flag without checking rows."
    )
