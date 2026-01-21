#!/bin/bash
#
# Garmin Data Export to Google Drive with Claude Health Analysis
# 1. Exports essential data as a summarized markdown file with tables
# 2. Runs Claude health analyzer to generate personalized coaching report
# 3. Uploads both data summary and health report to Google Drive
#

# Configuration
DAYS=7  # Number of days to export (optimal for daily exports)
GARMIN_DIR="$HOME/garmin-grafana"
TEMP_DIR="/tmp/garmin-export"
GDRIVE_PATH="gdrive:/garmin-data"
KEEP_LOCAL_DAYS=7  # Keep local exports for this many days

# Ensure pandoc is installed for PDF generation
if ! command -v pandoc &> /dev/null || ! command -v xelatex &> /dev/null; then
    echo "Installing pandoc and xelatex for PDF generation..."
    sudo apt-get update && sudo apt-get install -y pandoc texlive-xetex texlive-fonts-recommended lmodern
fi

# CSVs to include in summary
SUMMARY_CSVS=(
    "ActivitySummary.csv"
    "ClimbingRoutes.csv"
    "BoulderProblems.csv"
    "SleepSummary.csv"
    "TrainingReadiness.csv"
    "VO2_Max.csv"
)

# Create temp directory
mkdir -p "$TEMP_DIR"

# Generate timestamp for filename
TODAY=$(date '+%Y-%m-%d')
EXPORT_NAME="garmin_export"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Function to convert CSV to markdown table
csv_to_markdown_table() {
    local csv_file="$1"
    local title="$2"

    if [ ! -f "$csv_file" ]; then
        echo "## $title"
        echo ""
        echo "*No data available*"
        echo ""
        return
    fi

    # Check if file has content (more than just header)
    local line_count=$(wc -l < "$csv_file")
    if [ "$line_count" -lt 2 ]; then
        echo "## $title"
        echo ""
        echo "*No data available*"
        echo ""
        return
    fi

    echo "## $title"
    echo ""

    # Use Python to convert CSV to markdown table (handles edge cases better)
    python3 << PYEOF
import csv
import sys

with open('$csv_file', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)

if not rows:
    print("*No data available*")
    sys.exit(0)

header = rows[0]
data = rows[1:]

# Print header
print("| " + " | ".join(header) + " |")
print("| " + " | ".join(["---"] * len(header)) + " |")

# Print all data rows
for row in data:
    # Escape pipe characters and handle missing columns
    escaped_row = []
    for i, cell in enumerate(row):
        escaped_row.append(str(cell).replace("|", "\\|"))
    # Pad row if it has fewer columns than header
    while len(escaped_row) < len(header):
        escaped_row.append("")
    print("| " + " | ".join(escaped_row) + " |")
PYEOF

    echo ""
}

log "Starting Garmin export (markdown summary)..."

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
docker cp "garmin-fetch-data:$EXPORT_FILE" "$TEMP_DIR/full_export.zip"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to copy export from container"
    exit 1
fi

# Clean up the export from container
docker exec garmin-fetch-data rm "$EXPORT_FILE"

log "Extracting and creating markdown summary..."

# Extract full export
mkdir -p "$TEMP_DIR/extracted"
unzip -q "$TEMP_DIR/full_export.zip" -d "$TEMP_DIR/extracted"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to extract export"
    exit 1
fi

# Create markdown summary file
SUMMARY_FILE="$TEMP_DIR/${EXPORT_NAME}.md"
cd "$TEMP_DIR/extracted" || exit 1

# Write header
cat > "$SUMMARY_FILE" << EOF
# Garmin Data Summary

**Export Date:** $(date '+%A, %B %d, %Y')
**Data Range:** Last $DAYS days

---

EOF

# Track which CSVs we found
FOUND_COUNT=0

# Convert each CSV to markdown table
for csv in "${SUMMARY_CSVS[@]}"; do
    # Create title from filename (remove .csv and add spaces)
    title=$(echo "$csv" | sed 's/\.csv$//' | sed 's/\([a-z]\)\([A-Z]\)/\1 \2/g')

    if [ -f "$csv" ]; then
        csv_to_markdown_table "$csv" "$title" >> "$SUMMARY_FILE"
        FOUND_COUNT=$((FOUND_COUNT + 1))
        log "Added $csv to summary"
    else
        log "WARNING: $csv not found in export"
        echo "## $title" >> "$SUMMARY_FILE"
        echo "" >> "$SUMMARY_FILE"
        echo "*No data available*" >> "$SUMMARY_FILE"
        echo "" >> "$SUMMARY_FILE"
    fi
done

if [ $FOUND_COUNT -eq 0 ]; then
    log "ERROR: No CSVs found in export"
    exit 1
fi

# Get file size
SUMMARY_SIZE=$(du -h "$SUMMARY_FILE" | cut -f1)
log "Created summary: $SUMMARY_SIZE with $FOUND_COUNT data tables"

# Clean up extraction
rm -rf "$TEMP_DIR/extracted"
rm "$TEMP_DIR/full_export.zip"

# Change back to home directory (the extracted dir was deleted)
cd "$HOME"

log "Uploading summary to Google Drive..."

# Upload to Google Drive
rclone copy "$SUMMARY_FILE" "$GDRIVE_PATH/" --verbose

if [ $? -eq 0 ]; then
    log "SUCCESS: Uploaded to Google Drive as garmin_export.md"

    # Keep a local copy in garmin-reports directory
    mkdir -p "$HOME/garmin-reports"
    cp "$SUMMARY_FILE" "$HOME/garmin-reports/garmin_export.md"
    log "Local copy saved as ~/garmin-reports/garmin_export.md"

    # Bidirectional sync of SKILL.md and User.md (newer file wins)
    log "Syncing skill and user profile with Google Drive..."
    ANALYZER_DIR="$HOME/garmin-claude-analyzer"

    # Upload local to gdrive if local is newer
    rclone copy "$ANALYZER_DIR/SKILL.md" "$GDRIVE_PATH/" --update --verbose 2>/dev/null
    rclone copy "$ANALYZER_DIR/User.md" "$GDRIVE_PATH/" --update --verbose 2>/dev/null

    # Download from gdrive to local if gdrive is newer
    rclone copy "$GDRIVE_PATH/SKILL.md" "$ANALYZER_DIR/" --update --verbose 2>/dev/null
    rclone copy "$GDRIVE_PATH/User.md" "$ANALYZER_DIR/" --update --verbose 2>/dev/null

    # Run Claude health analyzer (only at 8am)
    CURRENT_HOUR=$(date '+%H')

    if [ "$CURRENT_HOUR" -eq 8 ]; then
        log "Running Claude health analyzer (daily 8am analysis)..."
        ANALYZER_SCRIPT="$HOME/garmin-claude-analyzer/analyze_health.py"

        if [ -f "$ANALYZER_SCRIPT" ]; then
            # Run the analyzer and capture output
            ANALYZER_OUTPUT=$(python3 "$ANALYZER_SCRIPT" 2>&1)
            ANALYZER_EXIT_CODE=$?

            if [ $ANALYZER_EXIT_CODE -eq 0 ]; then
                log "SUCCESS: Health analysis completed"

                # Find the generated analysis file (today's date)
                ANALYSIS_FILE="$HOME/garmin-reports/training_analysis_${TODAY}.md"

                if [ -f "$ANALYSIS_FILE" ]; then
                    log "Uploading training analysis to Google Drive..."
                    rclone copy "$ANALYSIS_FILE" "$GDRIVE_PATH/" --verbose

                    if [ $? -eq 0 ]; then
                        log "SUCCESS: Uploaded training_analysis_${TODAY}.md"
                    else
                        log "WARNING: Failed to upload training analysis to Google Drive"
                    fi

                    # Generate PDF from training analysis
                    PDF_FILE="$HOME/garmin-reports/training_analysis_${TODAY}.pdf"
                    log "Generating PDF from training analysis..."
                    pandoc "$ANALYSIS_FILE" -o "$PDF_FILE" --pdf-engine=xelatex -V geometry:margin=1in 2>&1

                    if [ $? -eq 0 ] && [ -f "$PDF_FILE" ]; then
                        log "SUCCESS: Generated PDF at $PDF_FILE"
                        log "Uploading PDF to Google Drive..."
                        rclone copy "$PDF_FILE" "$GDRIVE_PATH/" --verbose

                        if [ $? -eq 0 ]; then
                            log "SUCCESS: Uploaded training_analysis_${TODAY}.pdf"
                        else
                            log "WARNING: Failed to upload PDF to Google Drive"
                        fi
                    else
                        log "WARNING: Failed to generate PDF from training analysis"
                    fi
                else
                    log "WARNING: Training analysis not found at $ANALYSIS_FILE"
                fi
            else
                log "WARNING: Health analyzer failed with exit code $ANALYZER_EXIT_CODE"
                log "Analyzer output: $ANALYZER_OUTPUT"
            fi
        else
            log "INFO: Claude analyzer not found at $ANALYZER_SCRIPT - skipping analysis"
        fi
    else
        log "INFO: Skipping health analysis (only runs at 8am, current hour: ${CURRENT_HOUR})"
    fi

    # Clean up old exports from Google Drive (keep last 7 days)
    log "Cleaning up old exports from Google Drive..."
    rclone delete "$GDRIVE_PATH/" --min-age ${KEEP_LOCAL_DAYS}d --include "training_analysis_*.md" 2>/dev/null
    rclone delete "$GDRIVE_PATH/" --min-age ${KEEP_LOCAL_DAYS}d --include "training_analysis_*.pdf" 2>/dev/null

    # Clean up temp file
    rm "$SUMMARY_FILE"
else
    log "ERROR: Failed to upload to Google Drive"
    exit 1
fi

log "Export complete!"
