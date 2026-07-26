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
        dirty = subprocess.run(
            ["git", "-C", str(Path(__file__).parent.parent), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:  # not a git checkout, git absent, etc.
        return "unknown"


def exclude_orphans(df: pd.DataFrame, table_name: str) -> tuple[pd.DataFrame, int]:
    """Removes rows whose person_id could not be resolved, and says so.

    The domain mappers deliberately keep these rows (with person_id = None) so
    that check_referential_integrity can observe and count them — see the
    comment in etl/map_condition_occurrence.py. This function is the single
    place where they are actually excluded from the load, and every exclusion
    is logged and reported. Returns (kept_rows, n_excluded).
    """
    if df.empty or "person_id" not in df.columns:
        return df, 0

    orphan_mask = df["person_id"].isna()
    n_orphaned = int(orphan_mask.sum())
    if n_orphaned:
        logger.warning(
            "Excluding %d orphaned row(s) from %s load — subject references a "
            "person_id not present in the person table",
            n_orphaned,
            table_name,
        )
    kept = df.loc[~orphan_mask].copy()
    if not kept.empty:
        kept["person_id"] = kept["person_id"].astype("int64")
    return kept, n_orphaned


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
    orphan_counts: dict[str, int] = {}
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
        load_tables[tname], orphan_counts[tname] = exclude_orphans(load_tables[tname], tname)

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
        orphan_counts=orphan_counts,
        provenance=provenance,
    )
    logger.info("Quality report written to %s", report_path)


if __name__ == "__main__":
    main()
