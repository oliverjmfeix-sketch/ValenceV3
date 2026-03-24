#!/bin/sh
# Auto-reseed TypeDB when .tql seed files change.
# Compares checksum of all .tql files against last successful seed.
# Runs init_schema --force only when files have changed.

SEED_DIR="app/data"
CHECKSUM_FILE="/tmp/.valence_seed_checksum"

# Compute checksum of all .tql files (sorted for determinism)
CURRENT=$(find "$SEED_DIR" -name "*.tql" -type f | sort | xargs cat | sha256sum | cut -d' ' -f1)

# Read previous checksum if it exists
PREVIOUS=""
if [ -f "$CHECKSUM_FILE" ]; then
    PREVIOUS=$(cat "$CHECKSUM_FILE")
fi

if [ "$CURRENT" != "$PREVIOUS" ]; then
    echo "Seed files changed (${CURRENT:0:12}... != ${PREVIOUS:0:12}...), running init_schema..."
    python -m app.scripts.init_schema --force
    if [ $? -eq 0 ]; then
        echo "$CURRENT" > "$CHECKSUM_FILE"
        echo "Reseed complete."
    else
        echo "ERROR: init_schema failed!"
        exit 1
    fi
else
    echo "Seed files unchanged, skipping reseed."
fi
