#!/usr/bin/env bash
# Builds the mobile snapshot page: real API responses on the sample data + the same React UI in one page.
# Output: packaging/artifact/build/{index.html,data/*.json}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/packaging/artifact/build"
rm -rf "$OUT"
(cd "$ROOT/backend" && . .venv/bin/activate && python "$ROOT/packaging/artifact/export_snapshot.py" "$OUT/data")
(cd "$ROOT/frontend" && NEXT_TELEMETRY_DISABLED=1 npm run build --silent && node snapshot/build.mjs "$OUT")
