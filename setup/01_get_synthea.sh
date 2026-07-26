#!/usr/bin/env bash
# Clones and builds Synthea (Apache 2.0, MITRE Corporation).
# Run once. Requires Java 17+ and Git.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -d "synthea" ]; then
  echo "synthea/ already exists — skipping clone. Delete the folder to re-clone."
else
  git clone https://github.com/synthetichealth/synthea.git
fi

cd synthea
echo "Building Synthea (first build downloads dependencies, can take a few minutes)..."
./gradlew build -x test

echo "Done. Synthea built at $REPO_ROOT/synthea"
