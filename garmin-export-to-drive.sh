#!/bin/bash
#
# Garmin Data Export to Google Drive (Filtered for Coaching)
# Exports only essential CSVs needed for recovery assessment and coaching
#

# Configuration
DAYS=7  # Number of days to export (optimal for daily exports)
GARMIN_DIR="$HOME/garmin-grafana"
TEMP_DIR="/tmp/garmin-export"
GDRIVE_PATH="gdrive:/garmin-data"
KEEP_LOCAL_DAYS=7  # Keep local exports for this many days

# Essential CSVs for coaching (reduces from ~5MB to ~500KB)
COACHING_CSVS=(
    "ActivitySummary.csv"
    "ActivityLap.csv"
    "ActivitySession.csv"
    "BoulderProblems.csv"
    "ClimbingRoutes.csv"
    "DailyStats.csv"
    "SleepSummary.csv"
    "VO2_Max.csv"
    "TrainingReadiness.csv"
)

# Optional: Include these if you want slightly larger but more complete exports
# Uncomment to include:
# COACHING_CSVS+=("StressIntraday.csv")  # +250KB
# COACHING_CSVS+=("HeartRateIntraday.csv")  # +400KB

# Create temp directory
mkdir -p "$TEMP_DIR"

# Generate timestamp for filename
EXPORT_NAME="garmin_export_last${DAYS}days"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting Garmin export (filtered for coaching)..."

# Navigate to garmin-grafana directory
cd "$GARMIN_DIR" || { log "ERROR: Cannot find $GARMIN_DIR"; exit 1; }

# Check if container is running
if ! docker ps | grep -q garmin-fetch-data; then
    log "ERROR: garmin-fetch-data container is not running"
    exit 1
fi

# Run export
log "Exporting last $DAYS days of data..."
EXPORT_OUTPUT=$(docker exec garmin-fetch-data uv run /app/garmin_grafana/influxdb_exporter.py --last-n-days=$DAYS 2>&1)

# Extract filename from output
EXPORT_FILE=$(echo "$EXPORT_OUTPUT" | grep -oP '/tmp/GarminStats_Export_[0-9_]+_Last[0-9]+Days\.zip' | head -1)

if [ -z "$EXPORT_FILE" ]; then
    log "ERROR: No export file found in output:"
    log "$EXPORT_OUTPUT"
    exit 1
fi

log "Found export: $EXPORT_FILE"

# Copy from container to temp directory
EXPORT_BASENAME=$(basename "$EXPORT_FILE")
docker cp "garmin-fetch-data:$EXPORT_FILE" "$TEMP_DIR/full_export.zip"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to copy export from container"
    exit 1
fi

# Clean up the export from container
docker exec garmin-fetch-data rm "$EXPORT_FILE"

log "Filtering CSVs for coaching essentials..."

# Extract full export
mkdir -p "$TEMP_DIR/extracted"
unzip -q "$TEMP_DIR/full_export.zip" -d "$TEMP_DIR/extracted"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to extract export"
    exit 1
fi

# Create filtered export with only coaching essentials
cd "$TEMP_DIR/extracted" || exit 1

# Check which CSVs exist and build the zip command
ZIP_FILES=""
FOUND_COUNT=0
MISSING_COUNT=0

for csv in "${COACHING_CSVS[@]}"; do
    if [ -f "$csv" ]; then
        ZIP_FILES="$ZIP_FILES $csv"
        FOUND_COUNT=$((FOUND_COUNT + 1))
    else
        log "WARNING: $csv not found in export"
        MISSING_COUNT=$((MISSING_COUNT + 1))
    fi
done

if [ $FOUND_COUNT -eq 0 ]; then
    log "ERROR: No coaching CSVs found in export"
    exit 1
fi

log "Packaging $FOUND_COUNT CSVs (skipped $MISSING_COUNT)"

# Create filtered zip
zip -q "$TEMP_DIR/${EXPORT_NAME}.zip" $ZIP_FILES

if [ $? -ne 0 ]; then
    log "ERROR: Failed to create filtered export"
    exit 1
fi

# Get file sizes for logging
FULL_SIZE=$(du -h "$TEMP_DIR/full_export.zip" | cut -f1)
FILTERED_SIZE=$(du -h "$TEMP_DIR/${EXPORT_NAME}.zip" | cut -f1)
log "Reduced from $FULL_SIZE to $FILTERED_SIZE"

# Clean up extraction
rm -rf "$TEMP_DIR/extracted"
rm "$TEMP_DIR/full_export.zip"

log "Uploading to Google Drive..."

# Upload to Google Drive
rclone copy "$TEMP_DIR/${EXPORT_NAME}.zip" "$GDRIVE_PATH/" --verbose

if [ $? -eq 0 ]; then
    log "SUCCESS: Uploaded to Google Drive as ${EXPORT_NAME}.zip"
    
    # Keep a local copy in home directory (always named latest for easy access)
    cp "$TEMP_DIR/${EXPORT_NAME}.zip" "$HOME/latest-garmin-export.zip"
    log "Local copy saved as ~/latest-garmin-export.zip"
    
    # Clean up old timestamped exports from Google Drive (keep last 7 days)
    log "Cleaning up old exports from Google Drive..."
    rclone delete "$GDRIVE_PATH/" --min-age ${KEEP_LOCAL_DAYS}d --include "garmin_export_*.zip" 2>/dev/null
    
    # Clean up temp file
    rm "$TEMP_DIR/${EXPORT_NAME}.zip"
else
    log "ERROR: Failed to upload to Google Drive"
    exit 1
fi

log "Export complete!"
