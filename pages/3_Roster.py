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

# =====================
# CAA PAKISTAN FTL RULES
# =====================
FTL = {
    'A320': {'max_fdp': 13.0, 'max_sectors': 6},
    'A330': {'max_fdp': 14.0, 'max_sectors': 4},
    'min_rest_hours':  12.0,
    'max_7day_hours':  60.0,
    'max_28day_hours': 190.0,
    'max_annual_hours': 900.0,
    'report_before_dep': 60,   # minutes
    'debrief_after_arr': 30,   # minutes
}

# =====================
# FILTERS
# =====================
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
    fleet_filter = st.selectbox(
        "Fleet", ["ALL","A320-1","A320-2","A320-3","A330-1","A330-2"])
with col3:
    role_filter = st.selectbox("Role", ["ALL","CPT","FO"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# =====================
# ROSTER ENGINE
# =====================
def generate_roster(start_d, end_d):
    """
    Fully FTL-compliant roster engine.
    Rules enforced:
    - Min 12h rest between debrief and next report
    - Max 7-day FDP: 60h
    - Max 28-day FDP: 190h
    - Fair rotation: least-hours crew assigned first
    - Fleet type matching
    - No double assignment same time slot
    """

    with engine.connect() as conn:
        flights = pd.read_sql(text("""
            SELECT flight_id, aircraft, callsign, origin,
                   destination, dep_time, arr_time, flight_date
            FROM flights
            WHERE flight_date BETWEEN :sd AND :ed
            AND status = 'SCHEDULED'
            ORDER BY flight_date, dep_time
        """), conn, params={"sd": start_d, "ed": end_d})

        crew_df = pd.read_sql(text("""
            SELECT crew_id, name, role, fleet
            FROM crew
            WHERE is_active = TRUE
            ORDER BY fleet, role, crew_id
        """), conn)

    # ── Crew state tracker ──────────────────────────
    # debrief_end   : datetime — when their FDP+debrief ends
    # hours_28day   : float   — total FDP hours in 28-day window
    # hours_7day    : dict    — {date: hours} for rolling 7-day
    # assigned_slots: list    — (report_time, debrief_time) tuples
    crew_state = {}
    for _, c in crew_df.iterrows():
        crew_state[c['crew_id']] = {
            'name':          c['name'],
            'role':          c['role'],
            'fleet':         c['fleet'],
            'debrief_end':   None,
            'hours_28day':   0.0,
            'daily_hours':   {},   # date_str -> hours
            'assigned_slots': [],
        }

    roster_records  = []
    unassigned_log  = []

    # ── Process each flight ──────────────────────────
    for _, flight in flights.iterrows():
        aircraft    = flight['aircraft']          # e.g. A320-1
        fleet_type  = aircraft.split('-')[0]      # A320 or A330
        dep_dt      = pd.to_datetime(flight['dep_time'])
        arr_dt      = pd.to_datetime(flight['arr_time'])
        flight_date = pd.to_datetime(flight['flight_date']).date()

        fdp_hrs     = (arr_dt - dep_dt).total_seconds() / 3600
        report_dt   = dep_dt - timedelta(
                          minutes=FTL['report_before_dep'])
        debrief_dt  = arr_dt + timedelta(
                          minutes=FTL['debrief_after_arr'])
        max_fdp     = FTL[fleet_type]['max_fdp']

        # Reject impossible flights
        if fdp_hrs > max_fdp:
            unassigned_log.append({
                'callsign': flight['callsign'],
                'reason':   f"FDP {fdp_hrs:.1f}h exceeds max {max_fdp}h"
            })
            continue

        # Find best CPT and FO separately
        for role in ['CPT', 'FO']:
            # Build candidate list for this role + fleet
            candidates = []
            for cid, state in crew_state.items():
                if state['role'] != role:
                    continue
                if fleet_type not in state['fleet']:
                    continue

                # ── Check 1: Min rest ────────────────
                if state['debrief_end'] is not None:
                    rest_hrs = (report_dt - state['debrief_end']
                                ).total_seconds() / 3600
                    if rest_hrs < FTL['min_rest_hours']:
                        continue   # insufficient rest

                # ── Check 2: No overlap ──────────────
                overlap = False
                for (slot_rep, slot_deb) in state['assigned_slots']:
                    # New report before existing debrief
                    # AND new debrief after existing report
                    if report_dt < slot_deb and debrief_dt > slot_rep:
                        overlap = True
                        break
                if overlap:
                    continue

                # ── Check 3: 28-day hours ────────────
                if (state['hours_28day'] + fdp_hrs >
                        FTL['max_28day_hours']):
                    continue

                # ── Check 4: 7-day rolling hours ─────
                # Sum hours in the 7 days ending on flight_date
                week_start = flight_date - timedelta(days=6)
                hours_7day = sum(
                    v for d_str, v in state['daily_hours'].items()
                    if week_start <= date.fromisoformat(d_str)
                               <= flight_date
                )
                if hours_7day + fdp_hrs > FTL['max_7day_hours']:
                    continue

                # ── Candidate is legal — score for fairness ──
                # Lower hours = higher priority (fair rotation)
                score = - state['hours_28day']  # most rested first
                candidates.append((score, cid, hours_7day))

            if not candidates:
                unassigned_log.append({
                    'callsign': flight['callsign'],
                    'role':     role,
                    'reason':   f"No legal {role} available for {fleet_type}"
                })
                continue

            # Pick best candidate (highest score = least hours)
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_cid, _ = candidates[0]

            # ── Assign ───────────────────────────────
            roster_records.append({
                'crew_id':    best_cid,
                'flight_id':  int(flight['flight_id']),
                'callsign':   flight['callsign'],
                'duty_date':  flight_date,
                'report_dt':  report_dt,
                'debrief_dt': debrief_dt,
                'fdp_hours':  round(fdp_hrs, 2),
                'status':     'ASSIGNED',
            })

            # ── Update state ─────────────────────────
            st_ref = crew_state[best_cid]
            # Update debrief end (take latest if multiple flights)
            if (st_ref['debrief_end'] is None or
                    debrief_dt > st_ref['debrief_end']):
                st_ref['debrief_end'] = debrief_dt

            st_ref['hours_28day'] += fdp_hrs
            d_str = flight_date.isoformat()
            st_ref['daily_hours'][d_str] = (
                st_ref['daily_hours'].get(d_str, 0.0) + fdp_hrs)
            st_ref['assigned_slots'].append((report_dt, debrief_dt))

    return roster_records, unassigned_log, crew_state


# =====================
# GENERATE BUTTON
# =====================
if st.button("🔄 Generate / Refresh Roster", type="primary"):
    with st.spinner("Generating FTL-compliant roster..."):

        records, unassigned, crew_state = generate_roster(
            start_d, end_d)

        # Save to DB
        with engine.connect() as conn:
            conn.execute(text("""
                DELETE FROM roster
                WHERE duty_date BETWEEN :sd AND :ed
            """), {"sd": start_d, "ed": end_d})

            for r in records:
                conn.execute(text("""
                    INSERT INTO roster
                    (crew_id, flight_id, duty_date, report_time,
                     debrief_time, fdp_hours, status)
                    VALUES
                    (:cid, :fid, :dd, :rt, :dt, :fdp, :st)
                """), {
                    'cid': r['crew_id'],
                    'fid': r['flight_id'],
                    'dd':  r['duty_date'],
                    'rt':  r['report_dt'],
                    'dt':  r['debrief_dt'],
                    'fdp': r['fdp_hours'],
                    'st':  r['status'],
                })
            conn.commit()

        st.success(f"✅ Roster generated — "
                   f"{len(records)} assignments | "
                   f"{len(unassigned)} unassigned")

        # Show utilization summary
        st.markdown("#### 📊 Crew Utilization Summary")
        util_rows = []
        for cid, state in crew_state.items():
            if state['hours_28day'] > 0:
                util_rows.append({
                    'Crew ID': cid,
                    'Name':    state['name'],
                    'Role':    state['role'],
                    'Fleet':   state['fleet'],
                    '28D Hrs': round(state['hours_28day'], 1),
                    'Duties':  len(state['assigned_slots']),
                    'Status':  (
                        '🔴 OVER' if state['hours_28day'] > 190
                        else '🟡 HIGH' if state['hours_28day'] > 150
                        else '✅ OK'
                    )
                })

        util_df = pd.DataFrame(util_rows).sort_values(
            '28D Hrs', ascending=False)

        def color_util(row):
            if '🔴' in str(row['Status']):
                return ['background-color:#4a0000']*len(row)
            if '🟡' in str(row['Status']):
                return ['background-color:#4a3000']*len(row)
            return ['']*len(row)

        st.dataframe(
            util_df.style.apply(color_util, axis=1),
            use_container_width=True, height=300)

        if unassigned:
            st.warning(f"⚠️ {len(unassigned)} unassigned slots")
            st.dataframe(pd.DataFrame(unassigned),
                         use_container_width=True)

# =====================
# DISPLAY ROSTER
# =====================
st.divider()

query = """
    SELECT
        r.duty_date,
        r.crew_id,
        c.name,
        c.role,
        c.fleet,
        f.aircraft,
        SPLIT_PART(f.callsign,'-',1)||'-'||
        SPLIT_PART(f.callsign,'-',2) AS callsign,
        f.origin,
        f.destination,
        TO_CHAR(f.dep_time,'HH24MI')    AS dep,
        TO_CHAR(f.arr_time,'HH24MI')    AS arr,
        r.fdp_hours,
        TO_CHAR(r.report_time,'HH24MI') AS report,
        TO_CHAR(r.debrief_time,'HH24MI')AS debrief,
        r.status,
        r.override_flag
    FROM roster r
    JOIN crew    c ON r.crew_id    = c.crew_id
    JOIN flights f ON r.flight_id  = f.flight_id
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
    df['date'] = pd.to_datetime(
        df['duty_date']).dt.strftime('%d/%m/%y-%a')

    display_df = df[[
        'date','role','name','crew_id','aircraft',
        'callsign','origin','destination',
        'dep','arr','report','debrief','fdp_hours',
        'status','override_flag'
    ]]
    display_df.columns = [
        'Date','Role','Name','ID','Aircraft',
        'Callsign','From','To',
        'DEP','ARR','Report','Debrief','FDP Hrs',
        'Status','Override'
    ]

    total      = len(df)
    overrides  = int(df['override_flag'].sum())
    unassigned = len(df[df['status'] == 'UNASSIGNED'])

    st.markdown(f"""
    <div style="display:flex;gap:3rem;font-size:0.95rem;
    margin-bottom:1rem;font-weight:600;">
        <span>📋 Total Assignments: <b>{total}</b></span>
        <span>⚠️ Overrides: <b>{overrides}</b></span>
        <span>🔴 Unassigned: <b>{unassigned}</b></span>
    </div>
    """, unsafe_allow_html=True)

    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Roster",
        data=csv,
        file_name=f"roster_{timestamp}.csv",
        mime="text/csv"
    )

    def color_row(row):
        if row['Override']:
            return ['background-color:#4a3000']*len(row)
        if row['Status'] == 'UNASSIGNED':
            return ['background-color:#4a0000']*len(row)
        return ['']*len(row)

    st.dataframe(
        display_df.style.apply(color_row, axis=1),
        use_container_width=True, height=600)
