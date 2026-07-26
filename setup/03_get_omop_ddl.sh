#!/usr/bin/env bash
# Downloads the OFFICIAL OHDSI OMOP CDM v5.4.1 DDL (Postgres dialect).
# We deliberately use the official DDL rather than a hand-written one —
# a "standards-based" claim only holds if the schema actually comes from
# the standards body.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_DIR="$REPO_ROOT/sql"
mkdir -p "$SQL_DIR"
cd "$SQL_DIR"

if [ -d "CommonDataModel" ]; then
  echo "sql/CommonDataModel already exists — skipping clone."
else
  git clone --branch v5.4.1 --depth 1 \
    https://github.com/OHDSI/CommonDataModel.git
fi

echo "Postgres DDL files are at:"
echo "  $SQL_DIR/CommonDataModel/inst/ddl/5.4/postgresql/"
ls "$SQL_DIR/CommonDataModel/inst/ddl/5.4/postgresql/"
