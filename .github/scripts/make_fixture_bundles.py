#!/usr/bin/env python3
"""
Writes a couple of minimal FHIR R4 transaction bundles for CI.

Building Synthea in CI means a Gradle build plus several minutes of
simulation, which is a lot of runtime to prove something narrow: that the
FHIR -> OMOP -> Postgres path works end to end against the real OHDSI DDL.
These bundles are deliberately small and hand-shaped, and include the awkward
cases the pipeline is supposed to survive:

  - a resource with no coded value (falls through to concept_id 0)
  - a resource whose optional nested fields are absent entirely (the NaN case)
  - an over-length free-text observation value (varchar(60) truncation)
  - a clinical resource whose subject is not in the bundle (orphan handling)

Usage: python .github/scripts/make_fixture_bundles.py <output-dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
US_CORE_RACE = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"


def _entry(resource: dict) -> dict:
    return {"fullUrl": f"urn:uuid:{resource['id']}", "resource": resource}


def _patient(pid: str, gender: str, birth: str, race: str) -> dict:
    return {
        "resourceType": "Patient",
        "id": pid,
        "gender": gender,
        "birthDate": birth,
        "extension": [
            {"url": US_CORE_RACE, "extension": [{"url": "text", "valueString": race}]}
        ],
    }


def build_bundle(pid: str, gender: str, birth: str, race: str, orphan: bool) -> dict:
    subject = {"reference": f"urn:uuid:{pid}"}
    enc_id = f"enc-{pid}"
    entries = [
        _entry(_patient(pid, gender, birth, race)),
        _entry(
            {
                "resourceType": "Encounter",
                "id": enc_id,
                "subject": subject,
                "class": {"code": "AMB"},
                "period": {"start": "2021-04-05T09:00:00Z", "end": "2021-04-05T09:30:00Z"},
            }
        ),
        # mapped by the bundled concept map
        _entry(
            {
                "resourceType": "Condition",
                "id": f"cond-mapped-{pid}",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "code": {"coding": [{"system": SNOMED, "code": "38341003"}]},
                "onsetDateTime": "2019-02-03T00:00:00Z",
            }
        ),
        # deliberately unmapped code -> must land as concept_id 0, not dropped
        _entry(
            {
                "resourceType": "Condition",
                "id": f"cond-unmapped-{pid}",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "code": {"coding": [{"system": SNOMED, "code": "999999999"}]},
                "onsetDateTime": "2020-06-07T00:00:00Z",
            }
        ),
        # no medicationCodeableConcept, no encounter, no authoredOn:
        # the ragged-record / NaN shape that used to crash the mappers
        _entry({"resourceType": "MedicationRequest", "id": f"med-bare-{pid}", "subject": subject}),
        _entry(
            {
                "resourceType": "MedicationRequest",
                "id": f"med-{pid}",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "medicationCodeableConcept": {"coding": [{"system": RXNORM, "code": "308136"}]},
                "authoredOn": "2021-04-05T09:15:00Z",
                "status": "active",
            }
        ),
        # numeric -> measurement
        _entry(
            {
                "resourceType": "Observation",
                "id": f"obs-qty-{pid}",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "code": {"coding": [{"system": LOINC, "code": "8302-2"}]},
                "effectiveDateTime": "2021-04-05T09:20:00Z",
                "valueQuantity": {"value": 171.5, "unit": "cm"},
            }
        ),
        # long free text -> observation, must be truncated to varchar(60)
        _entry(
            {
                "resourceType": "Observation",
                "id": f"obs-text-{pid}",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "code": {"coding": [{"system": LOINC, "code": "72166-2"}]},
                "effectiveDateTime": "2021-04-05T09:25:00Z",
                "valueCodeableConcept": {"text": "LONG " * 40},
            }
        ),
    ]

    if orphan:
        # references a patient that appears in no bundle: must be counted by
        # the fk:person_id check and excluded from load, not silently dropped
        entries.append(
            _entry(
                {
                    "resourceType": "Condition",
                    "id": f"cond-orphan-{pid}",
                    "subject": {"reference": "urn:uuid:no-such-patient"},
                    "code": {"coding": [{"system": SNOMED, "code": "38341003"}]},
                    "onsetDateTime": "2018-01-01T00:00:00Z",
                }
            )
        )

    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fhir")
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        ("patient-alpha", "female", "1985-06-15", "White", False),
        ("patient-beta", "male", "1972-01-30", "Asian", True),
        ("patient-gamma", "female", "2001-09-09", "Black or African American", False),
    ]
    for pid, gender, birth, race, orphan in specs:
        bundle = build_bundle(pid, gender, birth, race, orphan)
        (out_dir / f"{pid}.json").write_text(json.dumps(bundle, indent=2))

    # a non-patient bundle, as Synthea emits: must be skipped, not counted
    (out_dir / "hospitalInformation-fixture.json").write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "type": "transaction",
                "entry": [
                    _entry({"resourceType": "Organization", "id": "org-1", "name": "CI Hospital"})
                ],
            },
            indent=2,
        )
    )

    print(f"Wrote {len(specs)} patient bundle(s) + 1 non-patient bundle to {out_dir}")


if __name__ == "__main__":
    main()
