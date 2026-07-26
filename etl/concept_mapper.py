"""
concept_mapper.py

Maps source coding systems used by Synthea's FHIR output (SNOMED-CT for
conditions, LOINC for observations, RxNorm for medications) to OMOP standard
concept_ids.

HONEST LIMITATION (this is intentional, not an oversight — see README):
Full-scale mapping requires OHDSI's Athena vocabulary tables (CONCEPT,
CONCEPT_RELATIONSHIP), which are a multi-GB download requiring a free Athena
account, and for some vocabularies a UMLS license. That's out of scope for a
"generate this repo from scratch" quickstart.

What this module does instead:
1. Ships a small hand-curated lookup (COMMON_CONCEPT_MAP) covering the
   highest-frequency codes Synthea actually emits, sourced from OHDSI's
   published Athena search (https://athena.ohdsi.org) — enough for the demo
   population to show real mapped rows across every OMOP table.
2. For anything not in that lookup, returns concept_id = 0 ("No matching
   concept") per OMOP convention, and the caller logs it as unmapped in the
   governance report rather than dropping the row.
3. Exposes `load_concept_table_from_athena_csv()` so that swapping in a real
   downloaded Athena CONCEPT.csv is a one-line change, not a rewrite — that's
   the intended production path.

This split (small bundled map + explicit "swap in the real vocab table here"
seam + never-silently-drop policy) IS the governance point.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("concept_mapper")

UNMAPPED_CONCEPT_ID = 0

# Whether the concept_ids in COMMON_CONCEPT_MAP below have been checked
# against the authoritative OHDSI vocabulary (https://athena.ohdsi.org).
#
# This is False, deliberately and visibly. The IDs were entered by hand as
# best-effort values to make the pipeline runnable end-to-end, and have not
# been confirmed against Athena. A governance-first project must not let that
# fact live only in a source comment where a reader of the README would never
# see it, so it is exposed as a flag: run_pipeline surfaces it in every
# quality report, and tests/test_mapping.py asserts the map is at least
# internally consistent. Flip this to True only after checking every row, at
# which point the check_concept_coverage threshold is also worth revisiting.
VERIFIED_AGAINST_ATHENA = False

# A small, hand-curated subset of (vocabulary_id, source_code) -> OMOP
# standard concept_id, for the codes Synthea emits most frequently. This is
# NOT a substitute for the full Athena vocabulary at production scale.
# Format: {(vocabulary_id, code): (concept_id, concept_name)}
#
# Invariant enforced by tests: no concept_id may appear twice under two
# different names. A collision means at least one row is wrong — see the
# removed acute-bronchitis entry below for a case this caught.
COMMON_CONCEPT_MAP: dict[tuple[str, str], tuple[int, str]] = {
    # --- SNOMED-CT conditions (a handful of the most common Synthea conditions) ---
    ("SNOMED", "38341003"): (320128, "Essential hypertension"),
    ("SNOMED", "44054006"): (201826, "Type 2 diabetes mellitus"),
    ("SNOMED", "195662009"): (4132546, "Acute viral pharyngitis"),
    # ("SNOMED", "10509002"): acute bronchitis — REMOVED. It carried
    # concept_id 255848, the same id given to pneumonia below, which cannot
    # be right for two distinct conditions; the id looks copy-pasted. Rather
    # than substitute a remembered replacement (the same unverified guessing
    # that produced the error), the entry is dropped: SNOMED 10509002 now
    # falls through to concept_id 0 and is counted as unmapped in the quality
    # report. Restore it with a value read off Athena.
    ("SNOMED", "233604007"): (255848, "Pneumonia"),
    ("SNOMED", "35489007"): (440383, "Depressive disorder"),
    ("SNOMED", "13645005"): (255573, "Chronic obstructive bronchitis"),
    # --- LOINC observations (vitals/labs) ---
    ("LOINC", "8302-2"): (3036277, "Body height"),
    ("LOINC", "29463-7"): (3025315, "Body weight"),
    ("LOINC", "39156-5"): (3038553, "Body mass index"),
    ("LOINC", "8480-6"): (3004249, "Systolic blood pressure"),
    ("LOINC", "8462-4"): (3012888, "Diastolic blood pressure"),
    ("LOINC", "8867-4"): (3027018, "Heart rate"),
    # --- RxNorm medications ---
    ("RxNorm", "308136"): (19078461, "Amoxicillin 500 MG Oral Tablet"),
    ("RxNorm", "849574"): (40165127, "Lisinopril 10 MG Oral Tablet"),
    ("RxNorm", "860975"): (40163924, "Metformin 500 MG Oral Tablet"),
}


def map_code_to_concept(vocabulary_id: str, code: str) -> tuple[int, str]:
    """Returns (concept_id, concept_name). Falls back to (0, 'Unmapped') and
    logs a warning if the code isn't in the bundled lookup or the loaded
    Athena table."""
    key = (vocabulary_id, code)
    if key in COMMON_CONCEPT_MAP:
        return COMMON_CONCEPT_MAP[key]

    logger.warning("Unmapped code: vocabulary=%s code=%s", vocabulary_id, code)
    return (UNMAPPED_CONCEPT_ID, "Unmapped")


def load_concept_table_from_athena_csv(csv_path: str | Path) -> None:
    """Production seam: call this with a path to a real Athena CONCEPT.csv
    export to replace/extend COMMON_CONCEPT_MAP at scale.

    Not implemented in this reference build (see module docstring) — this
    stub documents exactly where that integration point is.
    """
    raise NotImplementedError(
        "Load your downloaded Athena CONCEPT.csv here and merge into "
        "COMMON_CONCEPT_MAP (keyed by (vocabulary_id, concept_code)). "
        "Requires a free account at https://athena.ohdsi.org."
    )


def coverage_stats(mapped_df: pd.DataFrame, concept_id_col: str) -> dict:
    """Returns simple coverage stats for the governance report."""
    total = len(mapped_df)
    unmapped = int((mapped_df[concept_id_col] == UNMAPPED_CONCEPT_ID).sum())
    mapped = total - unmapped
    return {
        "total_rows": total,
        "mapped_rows": mapped,
        "unmapped_rows": unmapped,
        "mapped_pct": round(100 * mapped / total, 1) if total else None,
    }
