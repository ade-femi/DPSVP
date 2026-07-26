#!/usr/bin/env bash
# Loads the official OMOP CDM v5.4.1 DDL into the running Postgres container.
# Run setup/03_get_omop_ddl.sh first, and make sure `docker compose up -d`
# is already running.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a; source "$REPO_ROOT/.env" 2>/dev/null || source "$REPO_ROOT/.env.example"; set +a

DDL_DIR="$REPO_ROOT/sql/CommonDataModel/inst/ddl/5.4/postgresql"

if [ ! -d "$DDL_DIR" ]; then
  echo "DDL not found at $DDL_DIR — run setup/03_get_omop_ddl.sh first." >&2
  exit 1
fi

export PGPASSWORD="$PGPASSWORD"
PSQL="psql -h $PGHOST -p $PGPORT -U $PGUSER -d $PGDATABASE"

echo "Creating schema '$CDM_SCHEMA'..."
$PSQL -c "CREATE SCHEMA IF NOT EXISTS $CDM_SCHEMA;"

# NOTE: filenames below match the standard OHDSI release layout. If the
# exact filenames differ in the version you pulled, `ls "$DDL_DIR"` and
# adjust — the pattern is always: ddl (tables) -> primary_keys -> indices ->
# constraints, run in that order.
#
# The OHDSI DDL files use SqlRender template syntax (`@cdmDatabaseSchema`),
# not psql's `:variable` syntax — `psql -v` silently does not substitute
# `@`-prefixed tokens, so we do a literal text substitution before piping
# into psql.
run_if_exists () {
  local f="$1"
  if [ -f "$f" ]; then
    echo "Running $(basename "$f")..."
    sed "s/@cdmDatabaseSchema/$CDM_SCHEMA/g" "$f" | $PSQL -v ON_ERROR_STOP=1
  else
    echo "SKIP (not found): $f"
  fi
}

run_if_exists "$DDL_DIR"/OMOPCDM_postgresql_5.4_ddl.sql
run_if_exists "$DDL_DIR"/OMOPCDM_postgresql_5.4_primary_keys.sql
run_if_exists "$DDL_DIR"/OMOPCDM_postgresql_5.4_indices.sql
run_if_exists "$DDL_DIR"/OMOPCDM_postgresql_5.4_constraints.sql

echo "Done. Verify with: $PSQL -c '\dt $CDM_SCHEMA.*'"
