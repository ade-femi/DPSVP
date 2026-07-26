"""
quality_report.py

Turns a list of ValidationResult objects, plus basic row counts per OMOP
table, into a single timestamped Markdown report. This is the artifact that
makes the governance claim concrete and auditable: every pipeline run
produces one of these, not just a pass/fail exit code.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tabulate import tabulate

from validators import ValidationResult


def generate_report(
    run_id: str,
    table_row_counts: dict[str, int],
    validation_results: list[ValidationResult],
    output_dir: str | Path,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    n_total = len(validation_results)
    n_passed = sum(1 for r in validation_results if r.passed)
    n_failed = n_total - n_passed

    lines = []
    lines.append(f"# Data Quality Report — run `{run_id}`")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Checks run: **{n_total}**")
    lines.append(f"- Passed: **{n_passed}**")
    lines.append(f"- Failed/flagged: **{n_failed}**")
    lines.append("")

    lines.append("## Row counts by OMOP table")
    lines.append("")
    lines.append(
        tabulate(
            [[t, c] for t, c in table_row_counts.items()],
            headers=["table", "row_count"],
            tablefmt="github",
        )
    )
    lines.append("")

    lines.append("## Validation results")
    lines.append("")
    rows = [
        [
            r.table,
            r.check_name,
            "PASS" if r.passed else "FLAGGED",
            r.n_total,
            r.n_failed,
            f"{r.failure_rate}%",
            r.details,
        ]
        for r in validation_results
    ]
    lines.append(
        tabulate(
            rows,
            headers=["table", "check", "status", "n_total", "n_failed", "failure_rate", "details"],
            tablefmt="github",
        )
    )
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- `concept_coverage` flags are expected on this reference build — "
        "it ships a small hand-curated concept map, not the full Athena "
        "vocabulary. See `etl/concept_mapper.py` for the production seam."
    )
    lines.append(
        "- No rows are silently dropped for failing validation; failures are "
        "recorded here. Deciding what to do about a given failure class "
        "(quarantine, block load, accept) is a downstream governance policy "
        "decision, not something this ETL layer should decide unilaterally."
    )

    report_path = output_dir / f"quality_report_{run_id}.md"
    report_path.write_text("\n".join(lines))
    return report_path
