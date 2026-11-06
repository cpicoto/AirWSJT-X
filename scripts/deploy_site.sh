#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must use MAJOR.MINOR.PATCH format" >&2
  exit 2
fi
if [[ -z "${AZURE_STORAGE_KEY:-}" ]]; then
  echo "AZURE_STORAGE_KEY is required" >&2
  exit 2
fi

sed -i "s/__VERSION__/$version/g" site/index.html

upload() {
  az storage blob upload \
    --account-name ad7npus \
    --account-key "$AZURE_STORAGE_KEY" \
    --container-name '$web' \
    --name "$1" \
    --file "$2" \
    --content-type "$3" \
    --content-cache-control "$4" \
    --overwrite
}

no_cache="no-store, no-cache, must-revalidate, max-age=0"
public_cache="public, max-age=3600"
html_type="text/html; charset=utf-8"
json_type="application/json; charset=utf-8"
upload AirWSJT-X/index.html site/index.html "$html_type" "$no_cache"
upload AirWSJT-X site/index.html "$html_type" "$no_cache"
upload Privacy/AirWSJT-X/index.html site/privacy.html "$html_type" "$public_cache"
upload Privacy/AirWSJT-X site/privacy.html "$html_type" "$public_cache"
upload Support/AirWSJT-X/index.html site/support.html "$html_type" "$public_cache"
upload Support/AirWSJT-X site/support.html "$html_type" "$public_cache"
upload airwsjtx/callsign-grid-db.json site/callsign-grid-db.json "$json_type" "$public_cache"
upload airwsjtx/callsign-grid-db-version.json site/callsign-grid-db-version.json "$json_type" "$no_cache"
