#!/usr/bin/env bash
# Build an unsigned (ad-hoc) macOS Safari app wrapping the extension.
# macOS only. Output: extension/safari/out/tdmm-extension-safari-<version>.zip
# Usage: extension/safari/build.sh [version]
set -euo pipefail

[ "$(uname)" = "Darwin" ] || { echo "build.sh runs on macOS only" >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
ext="$(cd "$here/.." && pwd)"
version="${1:-$(jq -r .version "$ext/manifest.json")}"
out="$here/out"
work="$here/build"
rm -rf "$out" "$work"
mkdir -p "$out" "$work/src-ext"

cp -R "$ext/src" "$ext/icons" "$work/src-ext/"
node "$ext/scripts/build-safari-manifest.mjs" "$work/src-ext/manifest.json"

log="$work/converter.log"
xcrun safari-web-extension-converter "$work/src-ext" \
  --project-location "$work/xcode" --macos-only --no-open --no-prompt \
  --copy-resources --force \
  --app-name "3D Model Manager" --bundle-identifier io.github.metril.tdmm 2>&1 | tee "$log"
if grep -qiE "warning.*(unsupported|not supported|unrecognized)|unsupported.*key" "$log"; then
  echo "converter reported unsupported manifest keys" >&2
  exit 1
fi

proj="$(find "$work/xcode" -maxdepth 2 -name '*.xcodeproj' | head -1)"
xcodebuild -project "$proj" -scheme "3D Model Manager" -configuration Release \
  -derivedDataPath "$work/derived" \
  CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= CODE_SIGNING_REQUIRED=NO build

app="$(find "$work/derived/Build/Products/Release" -maxdepth 1 -name '*.app' | head -1)"
[ -n "$app" ] || { echo "no .app produced" >&2; exit 1; }
for appex in "$app"/Contents/PlugIns/*.appex; do
  codesign -dv --entitlements - "$appex" 2>&1 | tee "$work/entitlements.txt"
  grep -q "com.apple.security.app-sandbox" "$work/entitlements.txt" || { echo "appex missing app-sandbox entitlement" >&2; exit 1; }
done
zip="$out/tdmm-extension-safari-${version}.zip"
ditto -c -k --keepParent "$app" "$zip"
(cd "$out" && shasum -a 256 "$(basename "$zip")" > "$(basename "$zip").sha256")
ls -l "$out"
