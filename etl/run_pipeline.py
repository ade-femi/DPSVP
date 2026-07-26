"""
run_pipeline.py

Orchestrates the full Synthea FHIR -> OMOP pipeline:
  1. Load FHIR bundles (fhir_loader)
  2. Map each domain in dependency order: person -> visit_occurrence ->
     condition_occurrence / drug_exposure / measurement / observation
  3. Run governance validators against each mapped table
  4. Load into the OMOP Postgres schema
  5. Write a timestamped data-quality Markdown report

Usage:
  python etl/run_pipeline.py --input data/synthea_output/fhir --report-out reports/
  python etl/run_pipeline.py --input data/synthea_output/fhir --report-out reports/ --no-load
      (--no-load: run mapping + validation + report, skip the DB write —
       useful for iterating on mapping logic without a DB running)
  python etl/run_pipeline.py --input data/synthea_output/fhir --report-out reports/ --truncate
      (--truncate: empty the target OMOP tables first. The load is
       append-only, so re-running without this against the same input
       duplicates every row. Surrogate keys are stable, so truncate+reload
       is an idempotent refresh.)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "governance"))

from fhir_loader import load_bundles, bundles_to_dataframes  # noqa: E402
from map_person import map_person  # noqa: E402
from map_visit_occurrence import map_visit_occurrence  # noqa: E402
from map_condition_occurrence import map_condition_occurrence  # noqa: E402
from map_drug_exposure import map_drug_exposure  # noqa: E402
from map_observation import map_observations  # noqa: E402
import validators  # noqa: E402
from quality_report import generate_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_pipeline")


def _git_revision() -> str:
    """Short commit SHA of the code that produced a run, for the report.

    An audit artifact that can't say which version of the ETL generated it
    isn't much of an audit artifact. Degrades gracefully outside a checkout.
    """
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "-C", str(Path(__file__).parent.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        # --untracked-files=no: only uncommitted edits to *tracked* files mean
        # the code that ran differs from the recorded commit. New untracked
        # files (the report being written, scratch data) do not.
        dirty = subprocess.run(
            ["git", "-C", str(Path(__file__).parent.parent), "status",
             "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:  # not a git checkout, git absent, etc.
        return "unknown"


# Columns declared NOT NULL by the official OMOP CDM v5.4 DDL for the tables
# this pipeline populates (read off sql/CommonDataModel/inst/ddl/5.4/
# postgresql/OMOPCDM_postgresql_5.4_ddl.sql). A row missing any of these
# cannot be inserted, and because psycopg2 sends a whole page in one
# statement, a single such row aborts the entire table's load. They are
# therefore excluded and counted here rather than allowed to fail the run —
# e.g. a Synthea MedicationRequest with no authoredOn yields a null
# drug_exposure_start_date.
OMOP_REQUIRED_COLUMNS: dict[str, list[str]] = {
    "person": [
        "person_id", "gender_concept_id", "year_of_birth",
        "race_concept_id", "ethnicity_concept_id",
    ],
    "visit_occurrence": [
        "visit_occurrence_id", "person_id", "visit_concept_id",
        "visit_start_date", "visit_end_date", "visit_type_concept_id",
    ],
    "condition_occurrence": [
        "condition_occurrence_id", "person_id", "condition_concept_id",
        "condition_start_date", "condition_type_concept_id",
    ],
    "drug_exposure": [
        "drug_exposure_id", "person_id", "drug_concept_id",
        "drug_exposure_start_date", "drug_exposure_end_date", "drug_type_concept_id",
    ],
    "measurement": [
        "measurement_id", "person_id", "measurement_concept_id",
        "measurement_date", "measurement_type_concept_id",
    ],
    "observation": [
        "observation_id", "person_id", "observation_concept_id",
        "observation_date", "observation_type_concept_id",
    ],
}


def exclude_unloadable(df: pd.DataFrame, table_name: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """Removes rows the CDM cannot accept, and says exactly why.

    This is the single place rows leave the pipeline. Two exclusion reasons:

      unresolved_person_id  — the source subject reference matched no person.
          The domain mappers deliberately KEEP these (person_id = None) so
          check_referential_integrity can observe and count them; see the
          comment in etl/map_condition_occurrence.py. Filtering them earlier
          would make that check unable to fail.

      missing_required_<col> — the row lacks a column the OMOP v5.4 DDL
          declares NOT NULL, so Postgres would reject it (and take the rest
          of the batch with it).

    Every exclusion is logged here and itemised in the quality report, so no
    row disappears without appearing in the audit trail. Returns
    (kept_rows, {reason: count}).
    """
    reasons: dict[str, int] = {}
    if df.empty:
        return df, reasons

    drop_mask = pd.Series(False, index=df.index)

    if "person_id" in df.columns:
        orphan_mask = df["person_id"].isna()
        n_orphaned = int(orphan_mask.sum())
        if n_orphaned:
            reasons["unresolved_person_id"] = n_orphaned
            logger.warning(
                "Excluding %d row(s) from %s load — subject references a "
                "person_id not present in the person table",
                n_orphaned, table_name,
            )
        drop_mask |= orphan_mask

    for col in OMOP_REQUIRED_COLUMNS.get(table_name, []):
        if col not in df.columns:
            continue
        # count only rows not already being dropped, so reasons don't double-count
        missing_mask = df[col].isna() & ~drop_mask
        n_missing = int(missing_mask.sum())
        if n_missing:
            reasons[f"missing_required_{col}"] = n_missing
            logger.warning(
                "Excluding %d row(s) from %s load — %s is NULL but OMOP "
                "declares it NOT NULL",
                n_missing, table_name, col,
            )
        drop_mask |= missing_mask

    kept = df.loc[~drop_mask].copy()
    if not kept.empty and "person_id" in kept.columns:
        kept["person_id"] = kept["person_id"].astype("int64")
    return kept, reasons


def load_to_postgres(
    tables: dict[str, pd.DataFrame], schema: str, truncate_first: bool = False
) -> None:
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5433"),
        dbname=os.environ.get("PGDATABASE", "omop_cdm"),
        user=os.environ.get("PGUSER", "omop"),
        password=os.environ.get("PGPASSWORD", "omop_dev_password"),
    )
    try:
        with conn.cursor() as cur:
            if truncate_first:
                # Load is append-only and the CDM has no unique constraint on
                # the source-value columns, so re-running against the same
                # input would duplicate every row. Surrogate keys are stable
                # (fhir_utils.stable_id), so a truncate + reload is
                # effectively an idempotent refresh.
                targets = ", ".join(f"{schema}.{t}" for t in tables)
                logger.warning("--truncate set: emptying %s before load", targets)
                cur.execute(f"TRUNCATE {targets}")

            for table_name, df in tables.items():
                if df.empty:
                    logger.info("Skipping load for %s — no rows", table_name)
                    continue
                cols = list(df.columns)
                col_list = ", ".join(cols)
                # pandas represents a missing value in a numeric column as
                # float NaN, which psycopg2 sends verbatim to an integer/date
                # column and Postgres rejects. Nullable OMOP FKs such as
                # visit_occurrence_id legitimately go missing when a resource
                # references an encounter that isn't in the input, so coerce
                # every NA to None (SQL NULL) before insert.
                clean = df[cols].astype(object).where(pd.notna(df[cols]), None)
                values = [tuple(row) for row in clean.itertuples(index=False, name=None)]
                sql = f"INSERT INTO {schema}.{table_name} ({col_list}) VALUES %s"
                execute_values(cur, sql, values, page_size=500)
                logger.info("Loaded %d rows into %s.%s", len(values), schema, table_name)
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Directory of Synthea FHIR bundle JSON files")
    parser.add_argument("--report-out", default="reports", help="Directory for the quality report")
    parser.add_argument("--schema", default=os.environ.get("CDM_SCHEMA", "cdm"))
    parser.add_argument("--no-load", action="store_true", help="Skip loading into Postgres")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="TRUNCATE the target OMOP tables before loading. The load is "
             "append-only, so without this a re-run against the same input "
             "duplicates every row.",
    )
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:8]
    logger.info("Starting pipeline run %s", run_id)

    # 1. Load raw FHIR
    buckets = load_bundles(args.input)
    dfs = bundles_to_dataframes(buckets)
    n_patient_bundles = len(buckets.get("Patient", []))

    # 2. Map, in dependency order
    person_df = map_person(dfs["Patient"])
    person_lookup = dict(zip(person_df["person_source_value"], person_df["person_id"]))
    logger.info("Mapped %d persons", len(person_df))

    visit_df = map_visit_occurrence(dfs["Encounter"], person_lookup)
    # Build the visit lookup from non-orphaned visits only: an orphaned visit
    # is excluded from the load, so letting a child row point at it would
    # leave a dangling visit_occurrence_id in the loaded CDM.
    if visit_df.empty:
        visit_lookup = {}
    else:
        loadable_visits = visit_df[visit_df["person_id"].notna()]
        visit_lookup = dict(
            zip(loadable_visits["visit_source_value"], loadable_visits["visit_occurrence_id"])
        )
    logger.info("Mapped %d visit_occurrences", len(visit_df))

    condition_df = map_condition_occurrence(dfs["Condition"], person_lookup, visit_lookup)
    drug_df = map_drug_exposure(dfs["MedicationRequest"], person_lookup, visit_lookup)
    measurement_df, observation_df = map_observations(dfs["Observation"], person_lookup, visit_lookup)

    logger.info(
        "Mapped rows — condition_occurrence: %d, drug_exposure: %d, measurement: %d, observation: %d",
        len(condition_df), len(drug_df), len(measurement_df), len(observation_df),
    )

    tables = {
        "person": person_df,
        "visit_occurrence": visit_df,
        "condition_occurrence": condition_df,
        "drug_exposure": drug_df,
        "measurement": measurement_df,
        "observation": observation_df,
    }

    # 3. Governance validation
    results: list[validators.ValidationResult] = []
    person_ids = set(person_df["person_id"]) if not person_df.empty else set()

    results += validators.check_not_null(person_df, "person", ["person_id", "gender_concept_id"])
    for tname, df, date_col, concept_col in [
        ("visit_occurrence", visit_df, "visit_start_date", "visit_concept_id"),
        ("condition_occurrence", condition_df, "condition_start_date", "condition_concept_id"),
        ("drug_exposure", drug_df, "drug_exposure_start_date", "drug_concept_id"),
        ("measurement", measurement_df, "measurement_date", "measurement_concept_id"),
        ("observation", observation_df, "observation_date", "observation_concept_id"),
    ]:
        results.append(validators.check_referential_integrity(df, "person_id", person_ids, tname))
        results.append(validators.check_date_sanity(df, date_col, tname))
        results.append(validators.check_concept_coverage(df, concept_col, tname))

    # 4. Exclude orphaned rows, then load.
    #
    # Order matters and is the point: validation above ran against the FULL
    # mapped tables, including orphans, so the FK check reports real numbers.
    # Only now are orphans removed, and every removal is logged and counted.
    excluded_counts: dict[str, dict[str, int]] = {}
    # drop internal helper columns not present in the real OMOP DDL before load
    load_tables = {
        "person": person_df,
        "visit_occurrence": visit_df.drop(columns=["visit_source_value", "visit_class_source_value"], errors="ignore"),
        "condition_occurrence": condition_df.drop(columns=["condition_source_concept_name", "condition_status_source_value"], errors="ignore"),
        "drug_exposure": drug_df.drop(columns=["drug_source_concept_name", "status_source_value"], errors="ignore"),
        "measurement": measurement_df.drop(columns=["measurement_source_concept_name"], errors="ignore"),
        "observation": observation_df.drop(columns=["observation_source_concept_name"], errors="ignore"),
    }
    for tname in list(load_tables):
        load_tables[tname], excluded_counts[tname] = exclude_unloadable(load_tables[tname], tname)

    if args.no_load:
        logger.info("--no-load set: skipping Postgres load")
    else:
        load_to_postgres(load_tables, args.schema, truncate_first=args.truncate)

    # 5. Report
    row_counts = {t: len(df) for t, df in tables.items()}
    provenance = {
        "input_directory": str(Path(args.input).resolve()),
        "patient_bundles_read": n_patient_bundles,
        "target_schema": args.schema,
        "target_database": (
            "(not loaded)" if args.no_load
            else f"{os.environ.get('PGHOST', 'localhost')}:"
                 f"{os.environ.get('PGPORT', '5433')}/"
                 f"{os.environ.get('PGDATABASE', 'omop_cdm')}"
        ),
        "loaded_to_database": "no (--no-load)" if args.no_load else "yes",
        "truncated_before_load": (
            "n/a" if args.no_load else ("yes (--truncate)" if args.truncate else "no (appended)")
        ),
        "code_version": _git_revision(),
    }
    report_path = generate_report(
        run_id,
        row_counts,
        results,
        args.report_out,
        excluded_counts=excluded_counts,
        provenance=provenance,
    )
    logger.info("Quality report written to %s", report_path)


if __name__ == "__main__":
    main()
