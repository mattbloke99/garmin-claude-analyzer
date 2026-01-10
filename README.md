# Garmin Claude Health Analyzer

Automated health and training analysis system that processes Garmin Connect CSV exports using Claude AI to generate comprehensive daily health reports.

## Overview

This project integrates with your existing Garmin-Grafana data export pipeline to provide AI-powered coaching insights. It reads the CSV files exported from your Garmin Connect data, analyzes them using Claude's API with a personal trainer skill, and generates detailed markdown reports covering:

- Daily performance summary
- Activity-specific coaching
- Recovery & readiness insights
- Health trends
- Personalized recommendations

## Architecture

```
garmin-grafana (existing) → latest-garmin-export.zip (hourly)
                                     ↓
                      garmin-claude-analyzer (daily at 11 PM)
                                     ↓
                               Claude API
                                     ↓
                          garmin-reports/*.md
```

## Prerequisites

- Python 3.11 or higher
- Anthropic API key (sign up at https://console.anthropic.com/)
- Existing Garmin CSV exports (from garmin-grafana project)

## Installation

### 1. Install Python Dependencies

If you don't have a virtual environment set up:

```bash
cd ~/garmin-claude-analyzer

# Option A: Using venv (requires python3-venv package)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Option B: Using system Python (not recommended but works)
pip3 install --user -r requirements.txt

# Option C: Using UV (if available)
uv sync
```

### 2. Configure Environment Variables

```bash
cp .env.template .env
nano .env  # Edit with your settings
```

Required environment variables:
- `ANTHROPIC_API_KEY`: Your Claude API key
- `EXPORT_ZIP_PATH`: Path to Garmin export ZIP (default: `/home/dietpi/latest-garmin-export.zip`)
- `REPORT_OUTPUT_DIR`: Where to save reports (default: `/home/dietpi/garmin-reports`)
- `CLAUDE_MODEL`: Model to use (default: `claude-sonnet-4-5-20250929`)

### 3. Test the Analyzer

Run manually to verify everything works:

```bash
# If using venv
source .venv/bin/activate
python analyze_health.py

# If using system Python
python3 analyze_health.py
```

If successful, you should see:
- ✓ Loaded personal trainer skill
- ✓ Loaded CSV data files
- ✓ Received analysis from Claude
- ✓ Report saved to ~/garmin-reports/YYYY-MM-DD-health-report.md

### 4. Schedule Daily Execution

Add to crontab to run daily at 11 PM:

```bash
crontab -e
```

Add this line:

```bash
# Run Claude health analyzer daily at 11:00 PM
0 23 * * * /home/dietpi/garmin-claude-analyzer/.venv/bin/python /home/dietpi/garmin-claude-analyzer/analyze_health.py >> /home/dietpi/garmin-reports/analyzer.log 2>&1
```

*Adjust the Python path if not using venv*

## Usage

### Manual Execution

```bash
cd ~/garmin-claude-analyzer
source .venv/bin/activate  # if using venv
python analyze_health.py
```

### View Reports

```bash
# List all reports
ls -lt ~/garmin-reports/

# View today's report
cat ~/garmin-reports/$(date +%Y-%m-%d)-health-report.md

# Or use your favorite markdown viewer
```

### Check Logs

```bash
# View recent analyzer logs
tail -f ~/garmin-reports/analyzer.log

# Check cron execution
grep "garmin-claude-analyzer" /var/log/syslog
```

## How It Works

### Data Processing

1. **Extract CSVs**: Reads the ZIP file exported by garmin-grafana
2. **Parse Data**: Loads 9 CSV files (ActivitySummary, DailyStats, SleepSummary, etc.)
3. **Calculate Metrics**:
   - Weekly activity summary with correct climbing durations (from route-level data)
   - HRV trends (7-day average)
   - Training readiness metrics
   - Sleep quality analysis
   - Daily health metrics

### Claude API Integration

1. **System Prompt**: Loads the comprehensive personal trainer skill (SKILL.md)
2. **User Prompt**: Formats parsed data into structured coaching request
3. **API Call**: Sends to Claude Sonnet 4.5 with 8000 max tokens
4. **Response**: Receives markdown-formatted comprehensive health report

### Personal Trainer Skill

The skill provides Claude with:
- Coaching philosophy and communication style
- Data interpretation protocols (HRV, heart rate zones, training load)
- Sport-specific guidance (running, cycling, climbing)
- Recovery assessment frameworks
- Injury prevention guidelines

## Cost Estimates

Based on daily usage with Claude Sonnet 4.5:

- **Input tokens**: ~5-10K (CSV data + skill context)
- **Output tokens**: ~2-5K (comprehensive report)
- **Cost per report**: ~$0.05-0.10
- **Monthly cost**: ~$1.50-3.00 (daily reports)

### Cost Optimization Options

**Switch to weekly reports** (~$0.50/month):
```bash
# In crontab, change to run weekly on Sundays
0 23 * * 0 /home/dietpi/garmin-claude-analyzer/.venv/bin/python ...
```

**Use Haiku for cheaper analysis** (~$0.30/month):
```bash
# In .env file
CLAUDE_MODEL=claude-haiku-4-20250514
```

## Troubleshooting

### "ANTHROPIC_API_KEY not set"
- Verify `.env` file exists in project directory
- Check API key is valid and not expired
- Ensure python-dotenv is installed

### "Export file not found"
- Check garmin-grafana export is running (hourly cron)
- Verify path in `.env` matches actual export location
- Run manually: `~/garmin-grafana/garmin-export-to-drive.sh`

### "Error loading personal trainer skill"
- Ensure `personal-trainer.skill` file exists
- Verify it's a valid ZIP file containing SKILL.md
- Re-copy from source if corrupted

### No reports generated
- Check cron is running: `systemctl status cron`
- Verify Python path in crontab
- Check logs: `tail ~/garmin-reports/analyzer.log`
- Test manual execution first

### API rate limits
- Claude API has rate limits (varies by plan)
- Daily execution should be well within limits
- If issues occur, space out reports or reduce frequency

## File Structure

```
garmin-claude-analyzer/
├── analyze_health.py          # Main analyzer script
├── personal-trainer.skill      # Compressed personal trainer skill (ZIP)
├── requirements.txt            # Python dependencies
├── .env                        # Configuration (create from template)
├── .env.template               # Environment template
├── .gitignore                  # Git ignore rules
├── .venv/                      # Virtual environment (if using venv)
└── README.md                   # This file

garmin-reports/                 # Output directory
├── 2026-01-10-health-report.md
├── 2026-01-11-health-report.md
├── analyzer.log                # Execution logs
└── ...
```

## Integration with Existing System

This analyzer is **completely separate** from your garmin-grafana project:

- **No changes** to garmin-grafana code or Docker containers
- **No shared dependencies** - separate Python environment
- **Shared data source**: reads the same `latest-garmin-export.zip` file
- **Independent execution**: runs on its own daily schedule
- **Simple rollback**: just remove cron job and delete directory

## Upgrading

### Update Dependencies

```bash
cd ~/garmin-claude-analyzer
source .venv/bin/activate
pip install --upgrade anthropic pandas python-dotenv
```

### Update Personal Trainer Skill

```bash
# If you have an updated skill file
cp /path/to/new-personal-trainer.skill ~/garmin-claude-analyzer/
```

### Update Analyzer Script

```bash
# Backup current version
cp analyze_health.py analyze_health.py.bak

# Update with new version
# (then test manually before relying on cron)
```

## Future Enhancements

Possible additions (not currently implemented):

- Email/Slack notifications when report is ready
- Web dashboard for viewing reports
- Trend analysis across multiple weeks
- Integration with other fitness platforms
- Voice summaries via text-to-speech
- Injury prediction using historical data

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review logs in `~/garmin-reports/analyzer.log`
3. Test manual execution to isolate the problem
4. Verify API key and quotas at https://console.anthropic.com/

## License

This project uses the Anthropic Claude API. Ensure compliance with:
- Anthropic's Terms of Service
- Your personal health data privacy requirements
- Any applicable data protection regulations (GDPR, HIPAA, etc.)
