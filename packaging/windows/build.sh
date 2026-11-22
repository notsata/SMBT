#!/usr/bin/env bash
# Builds dist/SMBT-Setup.exe on Linux (tested on Ubuntu 24.04): official Windows CPython (NuGet "python"
# package) + win_amd64 wheels (installed by uv for the Windows platform) + backend + static PWA, packed by NSIS.
# Needs: curl, unzip, uv, node/npm, makensis (apt install nsis), python3 with Pillow (icon) - Pillow is fetched by uv.
set -euo pipefail
PYVER="${PYVER:-3.13.13}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$HERE/build"
STAGE="$WORK/stage"
APPVER="$(cd "$ROOT" && git describe --tags --always 2>/dev/null || echo dev)"
rm -rf "$STAGE" && mkdir -p "$STAGE/backend" "$STAGE/frontend" "$ROOT/dist"

echo "== Windows Python $PYVER"
NUPKG="$WORK/python.$PYVER.nupkg"
[ -f "$NUPKG" ] || curl -fsSL -o "$NUPKG" "https://api.nuget.org/v3-flatcontainer/python/$PYVER/python.$PYVER.nupkg"
rm -rf "$WORK/nupkg" && unzip -q "$NUPKG" 'tools/*' -d "$WORK/nupkg"
mv "$WORK/nupkg/tools" "$STAGE/python"
rm -rf "$STAGE/python/Lib/test" "$STAGE/python/Lib/idlelib" "$STAGE/python/include" "$STAGE/python/libs"

echo "== dependencies (win_amd64, cp${PYVER%.*})"
PYMINOR="${PYVER%.*}"
uv pip compile "$ROOT/backend/pyproject.toml" -q --python-platform x86_64-pc-windows-msvc --python-version "$PYMINOR" -o "$WORK/requirements-win.txt"
# Source builds are allowed only because the one sdist-only dependency (http-ece) is pure Python;
# the check below fails the build if a Linux binary ever lands in the Windows bundle.
uv pip install -q --target "$STAGE/python/Lib/site-packages" --python-platform x86_64-pc-windows-msvc \
  --python-version "$PYMINOR" -r "$WORK/requirements-win.txt"
if find "$STAGE/python/Lib/site-packages" -name '*.so' | grep -q .; then echo "Linux binaries in Windows bundle" >&2; exit 1; fi
find "$STAGE/python/Lib/site-packages" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

echo "== app"
cp -r "$ROOT/backend/app" "$STAGE/backend/app"
find "$STAGE/backend" -name __pycache__ -prune -exec rm -rf {} +
(cd "$ROOT/frontend" && npm ci --no-audit --no-fund --silent && NEXT_TELEMETRY_DISABLED=1 npm run build --silent)
cp -r "$ROOT/frontend/out" "$STAGE/frontend/out"
cp "$ROOT/README.md" "$STAGE/README.md"

echo "== icon"
uv run -q --with pillow python - "$ROOT/frontend/public/icons/icon-512.png" "$WORK/smbt.ico" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).save(sys.argv[2], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
PY

echo "== installer"
makensis -V2 -DVERSION="$APPVER" -DSTAGE="$STAGE" -DICON="$WORK/smbt.ico" -DOUT="$ROOT/dist/SMBT-Setup.exe" "$HERE/smbt.nsi"
ls -lh "$ROOT/dist/SMBT-Setup.exe"
