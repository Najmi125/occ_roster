import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="28 Day Roster", page_icon="📋", layout="wide")
st.title("📋 28 Day Rolling Crew Roster")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# --- CAA Pakistan FTL Rules ---
# Max FDP: 13hrs (A320), 14hrs (A330)
# Min Rest: 12hrs between duties
# Max 7 day hours: 60hrs
# Max 28 day hours: 190hrs
# Max annual: 900hrs
# Max 3 consecutive night duties then mandatory rest

FTL_RULES = {
    'A320': {'max_fdp': 13, 'max_sectors': 6},
    'A330': {'max_fdp': 14, 'max_sectors': 4},
    'min_rest': 12,
    'max_7day': 60,
    'max_28day': 190,
}

# --- Filters ---
col1, col2, col3 = st.columns(3)
with col1:
    date_range = st.date_input(
        "Roster Period",
        value=(today, end_date),
        min_value=today,
        max_value=end_date,
        format="DD/MM/YYYY"
    )
with col2:
    fleet_filter = st.selectbox("Fleet", ["ALL", "A320-1", "A320-2", "A320-3", "A330-1", "A330-2"])
with col3:
    role_filter = st.selectbox("Role", ["ALL", "CPT", "FO"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# --- Generate Roster Button ---
if st.button("🔄 Generate / Refresh Roster", type="primary"):
    with st.spinner("Generating roster..."):

        # Get flights
        with engine.connect() as conn:
            flights = pd.read_sql(text("""
                SELECT flight_id, aircraft, callsign, origin, destination,
                       dep_time, arr_time, flight_date
                FROM flights
                WHERE flight_date BETWEEN :sd AND :ed
                ORDER BY flight_date, aircraft, dep_time
            """), conn, params={"sd": start_d, "ed": end_d})

            # Get crew
            crew = pd.read_sql(text("""
                SELECT crew_id, name, role, fleet
                FROM crew WHERE is_active = TRUE
                ORDER BY fleet, role, crew_id
            """), conn)

        # Track crew duty hours and last duty end
        crew_tracker = {}
        for _, c in crew.iterrows():
            crew_tracker[c['crew_id']] = {
                'last_end': None,
                'hours_28day': 0,
                'hours_7day': 0,
                'role': c['role'],
                'fleet': c['fleet'],
                'name': c['name']
            }

        roster_records = []

        # Group flights by date and aircraft
        for _, flight in flights.iterrows():
            ac = flight['aircraft']  # e.g. A320-1
            fleet_type = ac.split('-')[0]  # A320 or A330
            dep = pd.to_datetime(flight['dep_time'])
            arr = pd.to_datetime(flight['arr_time'])
            fdp_hrs = (arr - dep).total_seconds() / 3600
            report_time = dep - timedelta(minutes=60)
            debrief_time = arr + timedelta(minutes=30)

            # Find available CPT
            assigned_cpt = None
            assigned_fo = None

            for crew_id, tracker in crew_tracker.items():
                if len(roster_records) > 0 and tracker['role'] == 'CPT':
                    pass

                # Match fleet type
                if fleet_type not in tracker['fleet']:
                    continue

                # Check rest rule
                if tracker['last_end']:
                    rest_hrs = (dep - tracker['last_end']).total_seconds() / 3600
                    if rest_hrs < FTL_RULES['min_rest']:
                        continue

                # Check 28 day hours
                max_fdp = FTL_RULES[fleet_type]['max_fdp']
                if tracker['hours_28day'] + fdp_hrs > FTL_RULES['max_28day']:
                    continue

                if tracker['role'] == 'CPT' and assigned_cpt is None:
                    assigned_cpt = crew_id
                elif tracker['role'] == 'FO' and assigned_fo is None:
                    assigned_fo = crew_id

                if assigned_cpt and assigned_fo:
                    break

            # Record assignments
            for crew_id in [assigned_cpt, assigned_fo]:
                if crew_id:
                    roster_records.append({
                        'crew_id': crew_id,
                        'flight_id': flight['flight_id'],
                        'callsign': flight['callsign'],
                        'aircraft': ac,
                        'duty_date': flight['flight_date'],
                        'report_time': report_time,
                        'debrief_time': debrief_time,
                        'fdp_hours': round(fdp_hrs, 2),
                        'status': 'ASSIGNED' if crew_id else 'UNASSIGNED'
                    })
                    # Update tracker
                    crew_tracker[crew_id]['last_end'] = debrief_time
                    crew_tracker[crew_id]['hours_28day'] += fdp_hrs

        # Save to DB
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM roster WHERE duty_date BETWEEN :sd AND :ed"),
                        {"sd": start_d, "ed": end_d})
            for r in roster_records:
                conn.execute(text("""
                    INSERT INTO roster (crew_id, flight_id, duty_date, report_time,
                                       debrief_time, fdp_hours, status)
                    VALUES (:cid, :fid, :dd, :rt, :dt, :fdp, :st)
                """), {
                    'cid': r['crew_id'], 'fid': r['flight_id'],
                    'dd': r['duty_date'], 'rt': r['report_time'],
                    'dt': r['debrief_time'], 'fdp': r['fdp_hours'],
                    'st': r['status']
                })
            conn.commit()

        st.success(f"✅ Roster generated — {len(roster_records)} assignments")

# --- Display Roster ---
st.divider()

query = """
    SELECT
        r.duty_date,
        r.crew_id,
        c.name,
        c.role,
        c.fleet,
        f.aircraft,
        SPLIT_PART(f.callsign,'-',1)||'-'||SPLIT_PART(f.callsign,'-',2) AS callsign,
        f.origin,
        f.destination,
        TO_CHAR(f.dep_time,'HH24MI') AS dep,
        TO_CHAR(f.arr_time,'HH24MI') AS arr,
        r.fdp_hours,
        TO_CHAR(r.report_time,'HH24MI') AS report,
        TO_CHAR(r.debrief_time,'HH24MI') AS debrief,
        r.status,
        r.override_flag
    FROM roster r
    JOIN crew c ON r.crew_id = c.crew_id
    JOIN flights f ON r.flight_id = f.flight_id
    WHERE r.duty_date BETWEEN :sd AND :ed
"""
params = {"sd": start_d, "ed": end_d}

if fleet_filter != "ALL":
    query += " AND f.aircraft = :fleet"
    params["fleet"] = fleet_filter
if role_filter != "ALL":
    query += " AND c.role = :role"
    params["role"] = role_filter

query += " ORDER BY r.duty_date, f.aircraft, c.role DESC, f.dep_time"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

if df.empty:
    st.info("No roster found. Click 'Generate Roster' above.")
else:
    df['date'] = pd.to_datetime(df['duty_date']).dt.strftime('%d/%m/%y-%a')

    display_df = df[['date', 'role', 'name', 'crew_id', 'aircraft',
                     'callsign', 'origin', 'destination',
                     'dep', 'arr', 'report', 'debrief',
                     'fdp_hours', 'status', 'override_flag']]

    display_df.columns = ['Date', 'Role', 'Name', 'ID', 'Aircraft',
                          'Callsign', 'From', 'To',
                          'DEP', 'ARR', 'Report', 'Debrief',
                          'FDP Hrs', 'Status', 'Override']

    # --- Summary ---
    total = len(df)
    overrides = len(df[df['override_flag'] == True])
    unassigned = len(df[df['status'] == 'UNASSIGNED'])

    st.markdown(f"""
    <div style="display:flex; gap:3rem; font-size:0.95rem; margin-bottom:1rem; font-weight:600;">
        <span>📋 Total Assignments: <b>{total}</b></span>
        <span>⚠️ Overrides: <b>{overrides}</b></span>
        <span>🔴 Unassigned: <b>{unassigned}</b></span>
    </div>
    """, unsafe_allow_html=True)

    # --- Download ---
    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Roster",
        data=csv,
        file_name=f"roster_{timestamp}.csv",
        mime="text/csv"
    )

    # --- Color rows ---
    def color_row(row):
        if row['Override'] == True:
            return ['background-color: #4a3000'] * len(row)
        elif row['Status'] == 'UNASSIGNED':
            return ['background-color: #4a0000'] * len(row)
        return [''] * len(row)

    styled = display_df.style.apply(color_row, axis=1)
    st.dataframe(styled, use_container_width=True, height=600)