#!/bin/bash
set -euo pipefail

# Configuration
# Project root: PROJECT_ROOT env var wins, otherwise the folder holding this script.
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# GCS_BUCKET is required - there is no default bucket.
BUCKET="${GCS_BUCKET:-}"
if [ -z "$BUCKET" ]; then
  echo "Error: GCS_BUCKET is not set. Export it to the name of your Cloud Storage bucket, e.g. GCS_BUCKET=my-market-db" >&2
  exit 1
fi

OBJECT=${OBJECT:-stock_market_new.db}
DEST=${DB_PATH:-$PROJECT_ROOT/App/database/stock_market_new.db}
SERVICE=${BACKEND_SERVICE:-dalal-backend.service}
TMP="$DEST.tmp"

echo "Starting download..."
echo "User: $(whoami)"
echo "Destination: $DEST"

# Ensure directory exists
mkdir -p "$(dirname "$DEST")"

# Download Database
if command -v gsutil >/dev/null 2>&1; then
  echo "Using gsutil..."
  gsutil -m cp "gs://$BUCKET/$OBJECT" "$TMP"
elif command -v gcloud >/dev/null 2>&1; then
  echo "Using gcloud storage..."
  gcloud storage cp "gs://$BUCKET/$OBJECT" "$TMP"
else
  echo "Error: Neither gsutil nor gcloud found"
  exit 1
fi

# Atomic move
mv "$TMP" "$DEST"
echo "Database downloaded successfully."

# Download CF-CA CSVs if requested
if [ "${FETCH_CFCA:-0}" -eq 1 ]; then
    echo "Downloading CF-CA CSVs..."
    CSV_DEST=$(dirname "$DEST")
    if command -v gsutil >/dev/null 2>&1; then
        gsutil -m cp "gs://$BUCKET/CF-CA*.csv" "$CSV_DEST/" || true
    else
        gcloud storage cp "gs://$BUCKET/CF-CA*.csv" "$CSV_DEST/" || true
    fi
fi

# Restart Backend Service
echo "Restarting backend service ($SERVICE)..."
sudo systemctl restart "$SERVICE"
sudo systemctl status "$SERVICE" --no-pager
echo "Service restarted."