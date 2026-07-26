"""
validators.py

Governance checks run against every mapped OMOP table before load. Each
validator returns a ValidationResult; run_pipeline.py collects all of them
into the data-quality report. Nothing here blocks the pipeline by default
(this is a demo, not a production gate) — but every failure is recorded,
counted, and surfaced. Silent data loss is the thing this layer exists to
prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class ValidationResult:
    check_name: str
    table: str
    passed: bool
    n_total: int
    n_failed: int
    details: str = ""

    @property
    def failure_rate(self) -> float:
        return round(100 * self.n_failed / self.n_total, 2) if self.n_total else 0.0


def check_not_null(df: pd.DataFrame, table: str, required_cols: list[str]) -> list[ValidationResult]:
    results = []
    for col in required_cols:
        if col not in df.columns:
            results.append(
                ValidationResult(f"not_null:{col}", table, False, len(df), len(df),
                                  f"Column '{col}' missing entirely")
            )
            continue
        n_null = int(df[col].isna().sum())
        results.append(
            ValidationResult(f"not_null:{col}", table, n_null == 0, len(df), n_null)
        )
    return results


def check_referential_integrity(
    child_df: pd.DataFrame,
    child_fk_col: str,
    parent_ids: set,
    table: str,
) -> ValidationResult:
    """E.g. every condition_occurrence.person_id must exist in person.person_id."""
    if child_df.empty or child_fk_col not in child_df.columns:
        return ValidationResult(f"fk:{child_fk_col}", table, True, 0, 0)
    orphaned = ~child_df[child_fk_col].isin(parent_ids)
    n_orphaned = int(orphaned.sum())
    return ValidationResult(
        f"fk:{child_fk_col}", table, n_orphaned == 0, len(child_df), n_orphaned,
        f"{n_orphaned} rows reference a person_id not present in person table"
        if n_orphaned else "",
    )


def check_date_sanity(
    df: pd.DataFrame, date_col: str, table: str,
    min_date: str = "1900-01-01", max_date: str | None = None,
) -> ValidationResult:
    """Flags dates outside a plausible range (e.g. future dates, pre-1900)."""
    if df.empty or date_col not in df.columns:
        return ValidationResult(f"date_sanity:{date_col}", table, True, 0, 0)

    max_date = max_date or date.today().isoformat()
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    out_of_range = parsed.isna() | (parsed < min_date) | (parsed > max_date)
    n_bad = int(out_of_range.sum())
    return ValidationResult(
        f"date_sanity:{date_col}", table, n_bad == 0, len(df), n_bad,
        f"{n_bad} rows have null/unparseable/out-of-range {date_col}" if n_bad else "",
    )


def check_concept_coverage(
    df: pd.DataFrame, concept_id_col: str, table: str, min_coverage_pct: float = 50.0,
) -> ValidationResult:
    """Flags if too much of a table failed vocabulary mapping (concept_id = 0)."""
    if df.empty or concept_id_col not in df.columns:
        return ValidationResult(f"concept_coverage:{concept_id_col}", table, True, 0, 0)
    n_unmapped = int((df[concept_id_col] == 0).sum())
    coverage_pct = 100 * (len(df) - n_unmapped) / len(df)
    passed = coverage_pct >= min_coverage_pct
    details = (
        ""
        if passed
        else f"Only {coverage_pct:.1f}% of rows mapped to a standard concept "
             f"(threshold: {min_coverage_pct}%) — expected on the bundled demo "
             f"concept map; load full Athena vocab to improve this."
    )
    return ValidationResult(
        f"concept_coverage:{concept_id_col}", table, passed, len(df), n_unmapped, details,
    )
