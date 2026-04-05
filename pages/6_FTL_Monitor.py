import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="FTL Monitor", page_icon="⏱️", layout="wide")
st.title("⏱️ FTL Monitor — CAA Pakistan Compliance")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# --- CAA Pakistan FTL Limits ---
st.markdown("""
<div style="background:#1a1a2e; padding:0.8rem 1.2rem; border-radius:8px; 
font-size:0.8rem; margin-bottom:1rem; display:flex; gap:3rem; flex-wrap:wrap;">
<span>📋 <b>CAA FTL Rules</b></span>
<span>Max FDP: <b>13h (A320)</b> / <b>14h (A330)</b></span>
<span>Min Rest: <b>12h</b></span>
<span>Max 7-Day: <b>60h</b></span>
<span>Max 28-Day: <b>190h</b></span>
<span>Max Annual: <b>900h</b></span>
</div>
""", unsafe_allow_html=True)

FTL = {
    'A320': 13, 'A330': 14,
    'min_rest': 12,
    'max_7day': 60,
    'max_28day': 190,
    'max_annual': 900
}

# --- Filters ---
col1, col2, col3 = st.columns(3)
with col1:
    fleet_filter = st.selectbox("Fleet", ["ALL", "A320", "A330"])
with col2:
    role_filter = st.selectbox("Role", ["ALL", "CPT", "FO"])
with col3:
    show_violations = st.checkbox("Show Violations Only", value=False)

# --- Load roster data ---
query = """
    SELECT
        r.crew_id,
        c.name,
        c.role,
        c.fleet,
        r.duty_date,
        f.aircraft,
        r.fdp_hours,
        r.report_time,
        r.debrief_time,
        r.status
    FROM roster r
    JOIN crew c ON r.crew_id = c.crew_id
    JOIN flights f ON r.flight_id = f.flight_id
    WHERE r.duty_date BETWEEN :sd AND :ed
"""
params = {"sd": today, "ed": end_date}

if fleet_filter != "ALL":
    query += " AND c.fleet = :fleet"
    params["fleet"] = fleet_filter
if role_filter != "ALL":
    query += " AND c.role = :role"
    params["role"] = role_filter

query += " ORDER BY r.crew_id, r.duty_date"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

if df.empty:
    st.warning("No roster data found. Generate roster first.")
else:
    # --- Calculate FTL per crew ---
    ftl_records = []

    for crew_id, group in df.groupby('crew_id'):
        crew_name = group['name'].iloc[0]
        crew_role = group['role'].iloc[0]
        crew_fleet = group['fleet'].iloc[0]
        aircraft = group['aircraft'].iloc[0]

        total_28day = float(group['fdp_hours'].sum())
        max_fdp = FTL['A330'] if 'A330' in aircraft else FTL['A320']

        # 7 day hours
        week_start = today - timedelta(days=7)
        total_7day = float(group[
            pd.to_datetime(group['duty_date']).dt.date >= week_start
        ]['fdp_hours'].sum())

        # Max single FDP
        max_single_fdp = float(group['fdp_hours'].max())

        # Rest check — minimum rest between duties
        rest_violation = False
        sorted_group = group.sort_values('duty_date')
        for i in range(1, len(sorted_group)):
            prev_debrief = pd.to_datetime(sorted_group.iloc[i-1]['debrief_time'])
            curr_report  = pd.to_datetime(sorted_group.iloc[i]['report_time'])
            rest_hrs = (curr_report - prev_debrief).total_seconds() / 3600
            if rest_hrs < FTL['min_rest']:
                rest_violation = True
                break

        # Violations
        violations = []
        if total_28day > FTL['max_28day']:
            violations.append(f"🔴 28-Day: {total_28day:.1f}h > {FTL['max_28day']}h")
        if total_7day > FTL['max_7day']:
            violations.append(f"🔴 7-Day: {total_7day:.1f}h > {FTL['max_7day']}h")
        if max_single_fdp > max_fdp:
            violations.append(f"🔴 FDP: {max_single_fdp:.1f}h > {max_fdp}h")
        if rest_violation:
            violations.append(f"🔴 Rest < {FTL['min_rest']}h")

        # Warnings
        warnings = []
        if total_28day > FTL['max_28day'] * 0.85:
            warnings.append(f"🟡 28-Day at {total_28day:.1f}h")
        if total_7day > FTL['max_7day'] * 0.85:
            warnings.append(f"🟡 7-Day at {total_7day:.1f}h")

        status = "🔴 VIOLATION" if violations else ("🟡 WARNING" if warnings else "✅ OK")

        ftl_records.append({
            'Crew ID': crew_id,
            'Name': crew_name,
            'Role': crew_role,
            'Fleet': crew_fleet,
            '7-Day Hrs': round(total_7day, 1),
            '28-Day Hrs': round(total_28day, 1),
            'Max FDP': max_single_fdp,
            'Rest OK': '✅' if not rest_violation else '🔴',
            'Status': status,
            'Details': ' | '.join(violations + warnings) if violations or warnings else '✅ Compliant'
        })

    ftl_df = pd.DataFrame(ftl_records)

    if show_violations:
        ftl_df = ftl_df[ftl_df['Status'] != '✅ OK']

    # --- Summary ---
    total_crew = len(ftl_df)
    violations_count = len(ftl_df[ftl_df['Status'] == '🔴 VIOLATION'])
    warnings_count = len(ftl_df[ftl_df['Status'] == '🟡 WARNING'])
    ok_count = len(ftl_df[ftl_df['Status'] == '✅ OK'])

    st.markdown(f"""
    <div style="display:flex; gap:3rem; font-size:0.95rem; margin-bottom:0.5rem; font-weight:600;">
        <span>👥 Total Crew Rostered: <b>{total_crew}</b></span>
        <span>🔴 FTL Violations (exceeding legal limits): <b>{violations_count}</b></span>
        <span>🟡 Warnings (above 85% of limit): <b>{warnings_count}</b></span>
        <span>✅ Fully Compliant: <b>{ok_count}</b></span>
    </div>
    <div style="font-size:0.75rem; color:#aaa; margin-bottom:1rem;">
        ⚠️ Violations require immediate OCC action. Warnings need monitoring.
        Roster must be revised to bring all crew within CAA Pakistan FTL limits.
    </div>
    """, unsafe_allow_html=True)

    # --- Download ---
    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = ftl_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download FTL Report",
        data=csv,
        file_name=f"ftl_report_{timestamp}.csv",
        mime="text/csv"
    )

    # --- Color rows ---
    def color_ftl(row):
        if '🔴 VIOLATION' in str(row['Status']):
            return ['background-color: #ffe5e5; color:#000'] * len(row)
        elif '🟡 WARNING' in str(row['Status']):
            return ['background-color: #fff8e1; color:#000'] * len(row)
        return [''] * len(row)

    styled = ftl_df.style.apply(color_ftl, axis=1)
    st.dataframe(styled, use_container_width=True, height=600)

    # --- Per Crew Detail Expander ---
    st.divider()
    st.subheader("🔍 Crew Detail Drill Down")
    st.markdown("""
    <div style="font-size:0.8rem; color:#aaa; margin-bottom:1rem;">
        Select any crew member below to view their individual daily duty breakdown 
        for the 28-day roster period. This shows each flight duty, FDP hours, 
        report and debrief times — useful for investigating specific FTL concerns.
    </div>
    """, unsafe_allow_html=True)

    crew_options = [
        f"{row['Crew ID']} | {row['Name']} | {row['Role']}"
        for _, row in ftl_df.iterrows()
    ]
    selected = st.selectbox("Select Crew", crew_options)
    sel_id = selected.split(" | ")[0]

    crew_detail = df[df['crew_id'] == sel_id].copy()
    crew_detail['date'] = pd.to_datetime(crew_detail['duty_date']).dt.strftime('%d/%m/%y-%a')
    crew_detail['report_time'] = pd.to_datetime(crew_detail['report_time']).dt.strftime('%H:%M')
    crew_detail['debrief_time'] = pd.to_datetime(crew_detail['debrief_time']).dt.strftime('%H:%M')

    detail_display = crew_detail[[
        'date', 'aircraft', 'fdp_hours', 'report_time', 'debrief_time', 'status'
    ]]
    detail_display.columns = ['Date', 'Aircraft', 'FDP Hrs', 'Report', 'Debrief', 'Status']

    st.dataframe(detail_display, use_container_width=True, height=300)