#!/usr/bin/env python3
"""
Garmin Health Analyzer with Claude API
Processes Garmin CSV exports and generates comprehensive health reports using Claude
"""

import pandas as pd
import sys
import os
import zipfile
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic

# Load environment variables
load_dotenv()

def extract_personal_trainer_skill(skill_path):
    """Extract and load the personal trainer skill from ZIP file"""
    try:
        temp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(skill_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Read the SKILL.md file
        skill_file = Path(temp_dir) / 'personal-trainer' / 'SKILL.md'
        with open(skill_file, 'r') as f:
            content = f.read()

        shutil.rmtree(temp_dir)

        # Extract just the content after the frontmatter
        if content.startswith('---'):
            # Split by --- and take everything after the second ---
            parts = content.split('---', 2)
            if len(parts) >= 3:
                return parts[2].strip()

        return content.strip()

    except Exception as e:
        print(f"Error loading personal trainer skill: {e}")
        sys.exit(1)

def extract_zip(zip_path):
    """Extract ZIP file to temporary directory"""
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)
    return temp_dir

def load_csv_data(data_dir):
    """Load all CSV files from the data directory"""
    data = {}
    csv_files = [
        'ActivitySummary.csv',
        'ActivityLap.csv',
        'ActivitySession.csv',
        'BoulderProblems.csv',
        'ClimbingRoutes.csv',
        'DailyStats.csv',
        'SleepSummary.csv',
        'VO2_Max.csv',
        'TrainingReadiness.csv'
    ]

    for csv_file in csv_files:
        file_path = Path(data_dir) / csv_file
        if file_path.exists():
            try:
                df = pd.read_csv(file_path)
                data[csv_file.replace('.csv', '')] = df
            except Exception as e:
                print(f"Warning: Could not load {csv_file}: {e}")

    return data

def parse_timestamps(data):
    """Parse timestamp columns in all dataframes"""
    timestamp_columns = ['time']

    for key, df in data.items():
        if df is not None and not df.empty:
            for col in timestamp_columns:
                if col in df.columns:
                    data[key][col] = pd.to_datetime(df[col])

    return data

def calculate_climbing_duration(activity_id, climbing_routes, boulder_problems):
    """Calculate actual climbing duration from route/problem data"""
    total_duration = 0

    if climbing_routes is not None and not climbing_routes.empty:
        routes = climbing_routes[climbing_routes['ActivityID'] == activity_id]
        total_duration += routes['Duration'].sum()

    if boulder_problems is not None and not boulder_problems.empty:
        problems = boulder_problems[boulder_problems['ActivityID'] == activity_id]
        total_duration += problems['Duration'].sum()

    return total_duration  # in seconds

def format_duration(seconds):
    """Format duration in seconds to readable string"""
    if pd.isna(seconds) or seconds == 0:
        return "0 min"

    minutes = seconds / 60
    if minutes >= 90:
        hours = minutes / 60
        return f"{hours:.1f} hours"
    else:
        return f"{int(minutes)} min"

def analyze_weekly_activities(data, today=None):
    """Generate structured weekly activity summary"""
    if today is None:
        today = pd.Timestamp.now(tz='UTC')
    elif not isinstance(today, pd.Timestamp):
        today = pd.Timestamp(today, tz='UTC')

    activities = data.get('ActivitySummary')
    climbing_routes = data.get('ClimbingRoutes')
    boulder_problems = data.get('BoulderProblems')

    if activities is None or activities.empty:
        return "No activity data found."

    # Filter and sort
    activities = activities[activities['activityType'] != 'No Activity'].copy()
    activities = activities.sort_values('time')

    # Get last 7 days
    week_start = (today - pd.Timedelta(days=7)).normalize()
    recent_activities = activities[activities['time'] >= week_start]

    # Group by date
    daily_activities = {}
    for idx, row in recent_activities.iterrows():
        date = row['time'].date()
        activity_type = row['activityType']

        # Calculate duration
        if activity_type in ['indoor_climbing', 'bouldering']:
            duration_seconds = calculate_climbing_duration(
                row['ActivityID'], climbing_routes, boulder_problems
            )
        else:
            duration_seconds = row.get('movingDuration', row.get('elapsedDuration', 0))

        activity_info = {
            'type': activity_type,
            'duration': duration_seconds,
            'name': row.get('activityName', activity_type)
        }

        if date not in daily_activities:
            daily_activities[date] = []
        daily_activities[date].append(activity_info)

    # Build weekly summary
    summary_lines = ["**Previous Week's Activities:**\n"]

    for i in range(7, 0, -1):
        check_date = (today - pd.Timedelta(days=i)).date()
        day_name = check_date.strftime('%a')
        date_str = check_date.strftime('%b %d')

        if check_date in daily_activities:
            activities_list = daily_activities[check_date]
            activity_strs = []

            for act in activities_list:
                act_type = act['type'].replace('_', ' ').title()
                if act_type == "Indoor Climbing":
                    act_type = "Climbing"
                duration_str = format_duration(act['duration'])
                activity_strs.append(f"{act_type} ({duration_str})")

            summary_lines.append(f"* {day_name} {date_str}: {' + '.join(activity_strs)}")
        else:
            summary_lines.append(f"* {day_name} {date_str}: REST DAY (no activities recorded)")

    return '\n'.join(summary_lines)

def analyze_hrv(data):
    """Analyze HRV trends"""
    sleep_data = data.get('SleepSummary')

    if sleep_data is None or sleep_data.empty:
        return "No HRV data available."

    sleep_data = sleep_data.sort_values('time')
    recent_hrv = sleep_data['avgOvernightHrv'].dropna().tail(7)

    if len(recent_hrv) == 0:
        return "No HRV data available."

    avg_hrv = recent_hrv.mean()
    last_hrv = recent_hrv.iloc[-1]

    hrv_lines = [
        "\n**HRV Status:**\n",
        f"Average overnight HRV for last 7 days is {int(avg_hrv)} ms",
        f"Last overnight HRV is {int(last_hrv)} ms"
    ]

    return '\n'.join(hrv_lines)

def gather_recent_metrics(data):
    """Gather recent training metrics for detailed analysis"""
    metrics = {}

    # Training readiness
    tr_data = data.get('TrainingReadiness')
    if tr_data is not None and not tr_data.empty:
        latest = tr_data.sort_values('time').iloc[-1]
        metrics['training_readiness'] = {
            'score': latest.get('score', 'N/A'),
            'level': latest.get('level', 'N/A'),
            'acute_load': latest.get('acuteLoad', 'N/A'),
        }

    # Sleep
    sleep_data = data.get('SleepSummary')
    if sleep_data is not None and not sleep_data.empty:
        recent_sleep = sleep_data.sort_values('time').tail(7)
        metrics['sleep'] = {
            'avg_duration_hours': (recent_sleep['sleepTimeSeconds'].mean() / 3600),
            'avg_score': recent_sleep['sleepScore'].mean(),
            'latest_score': recent_sleep['sleepScore'].iloc[-1] if len(recent_sleep) > 0 else 'N/A'
        }

    # Daily stats
    daily_data = data.get('DailyStats')
    if daily_data is not None and not daily_data.empty:
        recent_daily = daily_data.sort_values('time').tail(7)
        metrics['daily'] = {
            'avg_steps': recent_daily['totalSteps'].mean() if 'totalSteps' in recent_daily else 'N/A',
            'avg_resting_hr': recent_daily['restingHeartRate'].mean() if 'restingHeartRate' in recent_daily else 'N/A',
            'avg_stress': recent_daily['stressDuration'].mean() / 60 if 'stressDuration' in recent_daily else 'N/A',
        }

    # VO2 Max
    vo2_data = data.get('VO2_Max')
    if vo2_data is not None and not vo2_data.empty:
        latest_vo2 = vo2_data.sort_values('time').tail(3)
        metrics['vo2_max'] = {
            'recent_values': latest_vo2['vo2MaxValue'].tolist() if 'vo2MaxValue' in latest_vo2 else []
        }

    return metrics

def format_data_for_claude(data):
    """Format all data into a structured prompt for Claude"""
    today_str = datetime.now().strftime("%A, %B %d, %Y")

    prompt_parts = [
        f"Today is {today_str}. Please analyze my recent Garmin health and training data and provide a comprehensive daily health report.\n",
        analyze_weekly_activities(data),
        analyze_hrv(data),
    ]

    # Add detailed metrics
    metrics = gather_recent_metrics(data)

    if 'training_readiness' in metrics:
        tr = metrics['training_readiness']
        prompt_parts.append(f"\n**Training Readiness:**\n- Score: {tr['score']}\n- Level: {tr['level']}\n- Acute Load: {tr['acute_load']}")

    if 'sleep' in metrics:
        sleep = metrics['sleep']
        prompt_parts.append(f"\n**Recent Sleep (7-day average):**\n- Duration: {sleep['avg_duration_hours']:.1f} hours\n- Average Score: {sleep['avg_score']:.0f}\n- Latest Score: {sleep['latest_score']}")

    if 'daily' in metrics:
        daily = metrics['daily']
        prompt_parts.append(f"\n**Daily Metrics (7-day average):**\n- Steps: {daily['avg_steps']:.0f}" if daily['avg_steps'] != 'N/A' else "")
        if daily['avg_resting_hr'] != 'N/A':
            prompt_parts.append(f"- Resting Heart Rate: {daily['avg_resting_hr']:.0f} bpm")

    prompt_parts.append("\n\nPlease provide a comprehensive health report with:\n1. Daily Performance Summary\n2. Activity-Specific Coaching (if applicable)\n3. Recovery & Readiness Insights\n4. Recommendations for today and tomorrow")

    return '\n'.join(prompt_parts)

def call_claude_api(skill_content, user_data, api_key, model):
    """Call Claude API with personal trainer skill and user data"""
    try:
        client = Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=skill_content,
            messages=[
                {"role": "user", "content": user_data}
            ]
        )

        return response.content[0].text

    except Exception as e:
        print(f"Error calling Claude API: {e}")
        return None

def save_report(content, output_dir):
    """Save markdown report to file"""
    today = datetime.now()
    filename = f"{today.strftime('%Y-%m-%d')}-health-report.md"
    filepath = Path(output_dir) / filename

    try:
        with open(filepath, 'w') as f:
            f.write(f"# Health Report - {today.strftime('%A, %B %d, %Y')}\n\n")
            f.write(content)

        print(f"\n✓ Report saved to: {filepath}")
        return filepath

    except Exception as e:
        print(f"Error saving report: {e}")
        return None

def main():
    # Load configuration
    export_zip_path = os.getenv('EXPORT_ZIP_PATH', '/home/dietpi/latest-garmin-export.zip')
    report_output_dir = os.getenv('REPORT_OUTPUT_DIR', '/home/dietpi/garmin-reports')
    skill_path = os.getenv('PERSONAL_TRAINER_SKILL_PATH', '/home/dietpi/garmin-claude-analyzer/personal-trainer.skill')
    api_key = os.getenv('ANTHROPIC_API_KEY')
    model = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929')

    # Validate configuration
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set in environment")
        print("Please create a .env file with your API key")
        sys.exit(1)

    if not Path(export_zip_path).exists():
        print(f"Error: Export file not found: {export_zip_path}")
        sys.exit(1)

    if not Path(skill_path).exists():
        print(f"Error: Personal trainer skill file not found: {skill_path}")
        sys.exit(1)

    print("="*60)
    print("GARMIN CLAUDE HEALTH ANALYZER")
    print("="*60)
    print(f"Export file: {export_zip_path}")
    print(f"Output directory: {report_output_dir}")
    print(f"Model: {model}\n")

    # Load personal trainer skill
    print("Loading personal trainer skill...")
    skill_content = extract_personal_trainer_skill(skill_path)
    print(f"✓ Loaded skill ({len(skill_content)} characters)\n")

    # Extract and load data
    print("Extracting CSV data...")
    data_dir = extract_zip(export_zip_path)
    data = load_csv_data(data_dir)
    data = parse_timestamps(data)
    print(f"✓ Loaded {len(data)} data files\n")

    # Format data for Claude
    print("Formatting data for analysis...")
    user_prompt = format_data_for_claude(data)
    print(f"✓ Prepared {len(user_prompt)} characters of data\n")

    # Call Claude API
    print("Calling Claude API for analysis...")
    report_content = call_claude_api(skill_content, user_prompt, api_key, model)

    if report_content:
        print("✓ Received analysis from Claude\n")

        # Save report
        print("Saving report...")
        output_path = save_report(report_content, report_output_dir)

        if output_path:
            print(f"\n{'='*60}")
            print("REPORT GENERATED SUCCESSFULLY")
            print(f"{'='*60}")
    else:
        print("✗ Failed to generate report")
        sys.exit(1)

    # Cleanup
    shutil.rmtree(data_dir)

if __name__ == "__main__":
    main()
