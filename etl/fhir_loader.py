"""
fhir_loader.py

Reads Synthea-generated FHIR R4 Bundle JSON files and flattens them into
one pandas DataFrame per FHIR resource type (Patient, Condition, Observation,
MedicationRequest, Encounter). This is the "staging" layer between raw FHIR
JSON and the OMOP mapping functions in etl/map_*.py.

Design note: Synthea writes one Bundle (a "transaction" bundle) per patient,
containing every resource for that patient's lifetime. We iterate every
bundle in the input directory and bucket entries by resourceType.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

import pandas as pd

logger = logging.getLogger("fhir_loader")

RESOURCE_TYPES_OF_INTEREST = [
    "Patient",
    "Encounter",
    "Condition",
    "Observation",
    "MedicationRequest",
]


def load_bundles(input_dir: str | Path) -> Dict[str, List[dict]]:
    """
    Reads every *.json file in input_dir as a FHIR Bundle and returns a dict
    mapping resourceType -> list of raw resource dicts.
    """
    input_dir = Path(input_dir)
    files = sorted(input_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"No .json bundle files found in {input_dir}. "
            "Did you run setup/02_generate_patients.sh?"
        )

    buckets: Dict[str, List[dict]] = {rt: [] for rt in RESOURCE_TYPES_OF_INTEREST}
    n_bundles = 0
    n_skipped_files = 0

    for f in files:
        # Synthea also writes a few non-patient bundles (e.g. hospitalInformation,
        # practitionerInformation) when hospital/practitioner export is on.
        # Skip anything that isn't a patient transaction bundle.
        try:
            bundle = json.loads(f.read_text())
        except json.JSONDecodeError:
            logger.warning("Could not parse %s as JSON — skipping", f.name)
            n_skipped_files += 1
            continue

        if bundle.get("resourceType") != "Bundle":
            n_skipped_files += 1
            continue

        n_bundles += 1
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            rtype = resource.get("resourceType")
            if rtype in buckets:
                buckets[rtype].append(resource)

    logger.info(
        "Loaded %d patient bundles from %d files (%d skipped) — resource counts: %s",
        n_bundles,
        len(files),
        n_skipped_files,
        {k: len(v) for k, v in buckets.items()},
    )
    return buckets


def bundles_to_dataframes(buckets: Dict[str, List[dict]]) -> Dict[str, pd.DataFrame]:
    """Wraps each resource-type bucket as a DataFrame of raw (unflattened) dicts.

    Flattening FHIR's nested structure (e.g. CodeableConcept.coding[]) happens
    downstream in each domain-specific mapper, because the fields that matter
    differ per resource type.
    """
    return {rtype: pd.DataFrame(resources) for rtype, resources in buckets.items()}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Inspect loaded FHIR bundles")
    parser.add_argument("input_dir", help="Directory of Synthea FHIR bundle JSON files")
    args = parser.parse_args()

    buckets = load_bundles(args.input_dir)
    for rtype, resources in buckets.items():
        print(f"{rtype}: {len(resources)} resources")
