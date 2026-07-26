"""
test_missing_fields.py

Regression tests for the class of bug that actually broke this pipeline the
first time it met real Synthea output rather than fixtures.

fhir_loader wraps raw FHIR resource dicts straight into a DataFrame, so any
field absent from *some* resources of a type becomes float NaN for those rows.
`resource.get("x") or {}` does not defend against that — NaN is truthy, so
`nan or {}` evaluates to `nan` and the following `.get(...)` raises
`AttributeError: 'float' object has no attribute 'get'`. Every mapper had this
shape, and it crashed the run on the first MedicationRequest lacking
medicationCodeableConcept.

Each test below builds a DataFrame where one row omits an optional nested
field — reproducing exactly what pandas does to ragged records — and asserts
the mapper degrades gracefully instead of raising.
"""
import sys
from pathlib import Path

import pandas as pd

ETL_DIR = Path(__file__).parent.parent / "etl"
sys.path.insert(0, str(ETL_DIR))

from fhir_utils import as_dict, as_list, stable_id  # noqa: E402
from map_condition_occurrence import map_condition_occurrence  # noqa: E402
from map_drug_exposure import map_drug_exposure  # noqa: E402
from map_observation import VALUE_AS_STRING_MAX_LEN, map_observations  # noqa: E402
from map_person import map_person  # noqa: E402
from map_visit_occurrence import map_visit_occurrence  # noqa: E402

PERSON_LOOKUP = {"p1": 111}
VISIT_LOOKUP = {"enc-1": 222}
SUBJECT = {"reference": "Patient/p1"}


def test_ragged_records_really_do_produce_nan():
    """Guard the premise: pandas turns absent keys into NaN, not None."""
    df = pd.DataFrame([{"a": 1, "nested": {"k": "v"}}, {"a": 2}])
    assert pd.isna(df.iloc[1]["nested"])
    assert not isinstance(df.iloc[1]["nested"], dict)


class TestFhirUtils:
    def test_as_dict_absorbs_nan_none_and_wrong_types(self):
        assert as_dict(float("nan")) == {}
        assert as_dict(None) == {}
        assert as_dict("a string") == {}
        assert as_dict({"k": "v"}) == {"k": "v"}

    def test_as_list_absorbs_nan_none_and_wrong_types(self):
        assert as_list(float("nan")) == []
        assert as_list(None) == []
        assert as_list({"k": "v"}) == []
        assert as_list([1, 2]) == [1, 2]

    def test_stable_id_is_positive_and_within_omop_integer_range(self):
        # OMOP surrogate keys are `integer` columns: must fit in int32.
        for value in ("abc", "", "a-very-long-uuid-" * 10):
            key = stable_id(value)
            assert 0 <= key < 2**31 - 1

    def test_stable_id_distinguishes_namespaces(self):
        assert stable_id("visit", "x") != stable_id("x")


def test_drug_exposure_survives_missing_medication_concept():
    """The exact resource shape that crashed the first real run."""
    meds = pd.DataFrame(
        [
            {
                "id": "m1",
                "subject": SUBJECT,
                "encounter": {"reference": "Encounter/enc-1"},
                "medicationCodeableConcept": {
                    "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": "308136"}]
                },
                "authoredOn": "2020-05-01T00:00:00Z",
                "status": "active",
            },
            # no medicationCodeableConcept, no encounter -> NaN in both columns
            {"id": "m2", "subject": SUBJECT, "authoredOn": "2020-05-02T00:00:00Z"},
        ]
    )
    out = map_drug_exposure(meds, PERSON_LOOKUP, VISIT_LOOKUP)
    assert len(out) == 2
    assert out.iloc[0]["drug_concept_id"] != 0
    assert out.iloc[1]["drug_concept_id"] == 0, "unmappable drug must be 0, not invented"
    # pandas represents the mapper's None as NaN once the column is built;
    # load_to_postgres coerces NA to SQL NULL, so either form is acceptable
    # here — what matters is that no value was invented.
    assert pd.isna(out.iloc[1]["drug_source_value"])
    assert pd.isna(out.iloc[1]["visit_occurrence_id"])


def test_condition_survives_missing_code_and_clinical_status():
    conditions = pd.DataFrame(
        [
            {
                "id": "c1",
                "subject": SUBJECT,
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]},
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "onsetDateTime": "2019-01-01T00:00:00Z",
            },
            {"id": "c2", "subject": SUBJECT},  # no code, status, or onset
        ]
    )
    out = map_condition_occurrence(conditions, PERSON_LOOKUP, VISIT_LOOKUP)
    assert len(out) == 2
    assert out.iloc[1]["condition_concept_id"] == 0
    assert pd.isna(out.iloc[1]["condition_start_date"])


def test_visit_survives_missing_period_and_class():
    encounters = pd.DataFrame(
        [
            {
                "id": "enc-1",
                "subject": SUBJECT,
                "class": {"code": "EMER"},
                "period": {"start": "2021-03-04T10:00:00Z", "end": "2021-03-04T12:00:00Z"},
            },
            {"id": "enc-2", "subject": SUBJECT},  # no class, no period
        ]
    )
    out = map_visit_occurrence(encounters, PERSON_LOOKUP)
    assert len(out) == 2
    assert out.iloc[0]["visit_concept_id"] == 9203  # EMER -> ER visit
    assert out.iloc[1]["visit_concept_id"] == 9202  # documented default
    assert pd.isna(out.iloc[1]["visit_start_date"])


def test_observation_routes_quantity_to_measurement_and_survives_missing_value():
    observations = pd.DataFrame(
        [
            {
                "id": "o1",
                "subject": SUBJECT,
                "code": {"coding": [{"system": "http://loinc.org", "code": "8302-2"}]},
                "effectiveDateTime": "2022-02-02T00:00:00Z",
                "valueQuantity": {"value": 170.2, "unit": "cm"},
            },
            {
                "id": "o2",
                "subject": SUBJECT,
                "code": {"coding": [{"system": "http://loinc.org", "code": "72166-2"}]},
                "effectiveDateTime": "2022-02-02T00:00:00Z",
                "valueCodeableConcept": {"text": "Never smoker"},
            },
            {"id": "o3", "subject": SUBJECT},  # no code, no value of any kind
        ]
    )
    measurements, obs = map_observations(observations, PERSON_LOOKUP, VISIT_LOOKUP)
    assert len(measurements) == 1
    assert measurements.iloc[0]["value_as_number"] == 170.2
    assert measurements.iloc[0]["unit_source_value"] == "cm"
    # the coded-text row and the empty row both belong in `observation`
    assert len(obs) == 2
    assert obs.iloc[0]["value_as_string"] == "Never smoker"
    assert pd.isna(obs.iloc[1]["value_as_string"])


def test_observation_value_as_string_truncated_to_omop_column_width():
    """OMOP observation.value_as_string is varchar(60); Synthea exceeds it."""
    long_text = "x" * 200
    observations = pd.DataFrame(
        [
            {
                "id": "o1",
                "subject": SUBJECT,
                "code": {"coding": [{"system": "http://loinc.org", "code": "72166-2"}]},
                "effectiveDateTime": "2022-02-02T00:00:00Z",
                "valueCodeableConcept": {"text": long_text},
            }
        ]
    )
    _measurements, obs = map_observations(observations, PERSON_LOOKUP, VISIT_LOOKUP)
    assert len(obs.iloc[0]["value_as_string"]) == VALUE_AS_STRING_MAX_LEN


def test_person_survives_missing_extensions_and_birthdate():
    patients = pd.DataFrame(
        [
            {"id": "p1", "gender": "male", "birthDate": "1970-11-02",
             "extension": [
                 {"url": ".../us-core-race", "extension": [{"url": "text", "valueString": "Asian"}]}
             ]},
            {"id": "p2", "gender": "female"},  # no extension, no birthDate
        ]
    )
    out = map_person(patients)
    assert len(out) == 2
    assert out.iloc[0]["race_concept_id"] == 8515  # asian
    assert out.iloc[1]["race_concept_id"] == 0     # absent -> 0, never guessed
    assert pd.isna(out.iloc[1]["year_of_birth"])
