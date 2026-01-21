#!/usr/bin/env python3
"""
Garmin Health Analyzer with Claude API
Processes Garmin CSV exports and generates comprehensive health reports using Claude
"""

import pandas as pd
import sys
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic

# Load environment variables
load_dotenv()

def load_skill_file(skill_path):
    """Load the personal trainer skill from SKILL.md file and extract user profile path"""
    import re

    try:
        with open(skill_path, 'r') as f:
            content = f.read()

        # Extract just the content after the frontmatter
        if content.startswith('---'):
            # Split by --- and take everything after the second ---
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()

        # Extract USER_PROFILE_PATH from content
        user_profile_path = None
        match = re.search(r'USER_PROFILE_PATH:\s*(.+)', content)
        if match:
            relative_path = match.group(1).strip()
            # Resolve relative path based on SKILL.md location
            skill_dir = Path(skill_path).parent
            user_profile_path = skill_dir / relative_path

        return content, user_profile_path

    except Exception as e:
        print(f"Error loading personal trainer skill: {e}")
        sys.exit(1)


def load_user_profile(user_path):
    """Load user profile data from User.md file"""
    try:
        with open(user_path, 'r') as f:
            content = f.read()
        return content.strip()
    except Exception as e:
        print(f"Error loading user profile: {e}")
        sys.exit(1)


def parse_markdown_tables(md_path):
    """Parse markdown file and extract tables as pandas DataFrames"""
    import re
    from io import StringIO

    data = {}

    try:
        with open(md_path, 'r') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading markdown file: {e}")
        return data

    # Split by ## headers to get sections
    sections = re.split(r'^## ', content, flags=re.MULTILINE)

    for section in sections[1:]:  # Skip content before first ##
        lines = section.strip().split('\n')
        if not lines:
            continue

        # First line is the title
        title = lines[0].strip()
        # Convert title to key format (e.g., "Activity Summary" -> "ActivitySummary")
        key = title.replace(' ', '')

        # Find table lines (start with |)
        table_lines = [line for line in lines[1:] if line.strip().startswith('|')]

        if len(table_lines) < 2:
            # No valid table (need header + separator at minimum)
            continue

        # Skip "*No data available*" sections
        if any('No data available' in line for line in lines):
            continue

        # Parse the table
        header_line = table_lines[0]
        # Skip separator line (index 1)
        data_lines = table_lines[2:] if len(table_lines) > 2 else []

        # Extract header columns
        headers = [col.strip() for col in header_line.split('|')[1:-1]]

        # Extract data rows
        rows = []
        for line in data_lines:
            cols = [col.strip().replace('\\|', '|') for col in line.split('|')[1:-1]]
            # Pad row if needed
            while len(cols) < len(headers):
                cols.append('')
            rows.append(cols)

        if headers and rows:
            try:
                df = pd.DataFrame(rows, columns=headers)
                # Convert numeric columns
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='ignore')
                data[key] = df
                print(f"  Loaded {key}: {len(df)} rows")
            except Exception as e:
                print(f"  Warning: Could not parse {key}: {e}")

    return data

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
            'name': row.get('activityName', activity_type),
            'averageHR': row.get('averageHR'),
            'maxHR': row.get('maxHR'),
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

                # Include HR data for cardio activities
                hr_info = ""
                if act['averageHR'] and act['maxHR'] and act['type'] in ['running', 'cycling', 'indoor_cycling']:
                    avg_hr = int(act['averageHR']) if not pd.isna(act['averageHR']) else None
                    max_hr = int(act['maxHR']) if not pd.isna(act['maxHR']) else None
                    if avg_hr and max_hr:
                        hr_info = f", Avg HR {avg_hr}, Max HR {max_hr}"

                activity_strs.append(f"{act_type} ({duration_str}{hr_info})")

            summary_lines.append(f"* {day_name} {date_str}: {' + '.join(activity_strs)}")
        else:
            summary_lines.append(f"* {day_name} {date_str}: REST DAY (no activities recorded)")

    return '\n'.join(summary_lines)

def analyze_climbing_bouldering(data, today=None):
    """Analyze climbing and bouldering performance"""
    if today is None:
        today = pd.Timestamp.now(tz='UTC')
    elif not isinstance(today, pd.Timestamp):
        today = pd.Timestamp(today, tz='UTC')

    climbing_routes = data.get('ClimbingRoutes')
    boulder_problems = data.get('BoulderProblems')
    activities = data.get('ActivitySummary')

    week_start = (today - pd.Timedelta(days=7)).normalize()

    summary_parts = []

    # Get recent climbing activity IDs
    recent_climbing_ids = set()
    if activities is not None and not activities.empty:
        recent_activities = activities[
            (activities['time'] >= week_start) &
            (activities['activityType'].isin(['indoor_climbing', 'bouldering']))
        ]
        recent_climbing_ids = set(recent_activities['ActivityID'].tolist())

    # Analyze climbing routes
    if climbing_routes is not None and not climbing_routes.empty:
        recent_routes = climbing_routes[climbing_routes['ActivityID'].isin(recent_climbing_ids)]

        if not recent_routes.empty:
            summary_parts.append("\n**Climbing Routes (Last 7 Days):**")

            total_routes = len(recent_routes)
            summary_parts.append(f"- Total routes attempted: {total_routes}")

            # Analyze by grade if available
            if 'climbingGrade' in recent_routes.columns:
                grade_counts = recent_routes['climbingGrade'].value_counts()
                grades_str = ", ".join([f"{grade}: {count}" for grade, count in grade_counts.items()])
                summary_parts.append(f"- Grades attempted: {grades_str}")

            # Success rate if result type available
            if 'resultType' in recent_routes.columns:
                completed = recent_routes[recent_routes['resultType'].isin(['completed', 'Completed', 'COMPLETED', 'sent', 'Sent'])].shape[0]
                success_rate = (completed / total_routes * 100) if total_routes > 0 else 0
                summary_parts.append(f"- Completion rate: {success_rate:.0f}% ({completed}/{total_routes})")

            # Average attempts if available
            if 'attemptCount' in recent_routes.columns:
                avg_attempts = recent_routes['attemptCount'].mean()
                summary_parts.append(f"- Average attempts per route: {avg_attempts:.1f}")

            # Total climbing duration
            total_duration = recent_routes['Duration'].sum() if 'Duration' in recent_routes.columns else 0
            summary_parts.append(f"- Total climbing time: {format_duration(total_duration)}")

    # Analyze boulder problems
    if boulder_problems is not None and not boulder_problems.empty:
        recent_problems = boulder_problems[boulder_problems['ActivityID'].isin(recent_climbing_ids)]

        if not recent_problems.empty:
            summary_parts.append("\n**Bouldering Problems (Last 7 Days):**")

            total_problems = len(recent_problems)
            summary_parts.append(f"- Total problems attempted: {total_problems}")

            # Analyze by grade if available
            if 'boulderingGrade' in recent_problems.columns:
                grade_counts = recent_problems['boulderingGrade'].value_counts()
                grades_str = ", ".join([f"{grade}: {count}" for grade, count in grade_counts.items()])
                summary_parts.append(f"- Grades attempted: {grades_str}")

            # Success rate if result type available
            if 'resultType' in recent_problems.columns:
                completed = recent_problems[recent_problems['resultType'].isin(['completed', 'Completed', 'COMPLETED', 'sent', 'Sent', 'topped', 'Topped'])].shape[0]
                success_rate = (completed / total_problems * 100) if total_problems > 0 else 0
                summary_parts.append(f"- Completion rate: {success_rate:.0f}% ({completed}/{total_problems})")

            # Average attempts if available
            if 'attemptCount' in recent_problems.columns:
                avg_attempts = recent_problems['attemptCount'].mean()
                summary_parts.append(f"- Average attempts per problem: {avg_attempts:.1f}")

            # Total bouldering duration
            total_duration = recent_problems['Duration'].sum() if 'Duration' in recent_problems.columns else 0
            summary_parts.append(f"- Total bouldering time: {format_duration(total_duration)}")

    if not summary_parts:
        return ""

    return '\n'.join(summary_parts)


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
        analyze_climbing_bouldering(data),
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

    prompt_parts.append("\n\nPlease provide a comprehensive health report with:\n1. Daily Performance Summary\n2. Activity-Specific Coaching (including climbing/bouldering analysis if data is present)\n3. Recovery & Readiness Insights\n4. Recommendations for today and tomorrow")

    return '\n'.join(prompt_parts)

def call_claude_api(skill_content, user_data, api_key, model):
    """Call Claude API with personal trainer skill and training data"""
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
    filename = f"training_analysis_{today.strftime('%Y-%m-%d')}.md"
    filepath = Path(output_dir) / filename

    try:
        report_content = f"# Training Analysis - {today.strftime('%A, %B %d, %Y')}\n\n{content}"

        with open(filepath, 'w') as f:
            f.write(report_content)
        print(f"\n✓ Report saved to: {filepath}")

        return filepath

    except Exception as e:
        print(f"Error saving report: {e}")
        return None

def main():
    # Load configuration
    summary_file_path = os.getenv('SUMMARY_FILE_PATH', '/home/dietpi/garmin-reports/garmin_export.md')
    report_output_dir = os.getenv('REPORT_OUTPUT_DIR', '/home/dietpi/garmin-reports')
    skill_path = os.getenv('PERSONAL_TRAINER_SKILL_PATH', '/home/dietpi/garmin-claude-analyzer/SKILL.md')
    api_key = os.getenv('ANTHROPIC_API_KEY')
    model = os.getenv('CLAUDE_MODEL', 'claude-opus-4-5-20251101')

    # Validate configuration
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set in environment")
        print("Please create a .env file with your API key")
        sys.exit(1)

    if not Path(summary_file_path).exists():
        print(f"Error: Summary file not found: {summary_file_path}")
        sys.exit(1)

    if not Path(skill_path).exists():
        print(f"Error: Personal trainer skill file not found: {skill_path}")
        sys.exit(1)

    print("="*60)
    print("GARMIN CLAUDE HEALTH ANALYZER")
    print("="*60)
    print(f"Summary file: {summary_file_path}")
    print(f"Output directory: {report_output_dir}")
    print(f"Model: {model}\n")

    # Load personal trainer skill (extracts user profile path from SKILL.md)
    print("Loading personal trainer skill...")
    skill_content, user_profile_path = load_skill_file(skill_path)
    print(f"✓ Loaded skill ({len(skill_content)} characters)")

    # Load user profile from path specified in SKILL.md
    if user_profile_path and user_profile_path.exists():
        print(f"Loading user profile from {user_profile_path}...")
        user_profile = load_user_profile(user_profile_path)
        print(f"✓ Loaded user profile ({len(user_profile)} characters)\n")
    else:
        print("Warning: No user profile path found in SKILL.md or file doesn't exist")
        user_profile = ""

    # Parse markdown summary file
    print("Parsing markdown summary...")
    data = parse_markdown_tables(summary_file_path)
    data = parse_timestamps(data)
    print(f"✓ Loaded {len(data)} data tables\n")

    # Format data for Claude
    print("Formatting data for analysis...")
    training_data = format_data_for_claude(data)

    # Combine user profile with training data
    if user_profile:
        user_prompt = f"# Athlete Profile\n\n{user_profile}\n\n---\n\n# Training Data\n\n{training_data}"
    else:
        user_prompt = training_data
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

if __name__ == "__main__":
    main()
