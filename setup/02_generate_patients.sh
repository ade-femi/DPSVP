#!/usr/bin/env bash
# Generates N synthetic patients as FHIR R4 Bundles using Synthea.
# Usage: bash setup/02_generate_patients.sh 300
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N="${1:-300}"
OUT_DIR="$REPO_ROOT/data/synthea_output"

if [ ! -d "$REPO_ROOT/synthea" ]; then
  echo "synthea/ not found — run setup/01_get_synthea.sh first." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
cd "$REPO_ROOT/synthea"

# -p N          : generate N patients
# --exporter.fhir.export=true          : export FHIR R4 bundles
# --exporter.hospital.fhir.export=false: skip hospital/practitioner bundles for this minimal scope
# --exporter.baseDirectory              : where output lands
#
# LANG/LC_ALL force a UTF-8 locale: Synthea generates plenty of patient
# names with accented characters (e.g. "María", "Benítez"), and under the
# default POSIX locale the JVM's file-path encoding can't represent them,
# silently dropping those patients' export ("Malformed input or input
# contains unmappable characters").
LANG=C.utf8 LC_ALL=C.utf8 ./run_synthea -p "$N" \
  --exporter.fhir.export=true \
  --exporter.csv.export=false \
  --exporter.baseDirectory="$OUT_DIR"

echo "Generated $N synthetic patients as FHIR R4 bundles in $OUT_DIR/fhir"
