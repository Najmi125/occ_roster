st.write("SYNC TEST: 18-Apr v1")
import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime
from utils.ftl_validator import (
    generate_compliance_report,
    save_violations,
    CAA_RULES
)

st.set_page_config(
    page_title="28 Day Roster",
    page_icon="📋",
    layout="wide"
)
st.title("📋 28 Day Rolling Crew Roster")

engine = get_engine()
today    = date.today()
end_date = today + timedelta(days=27)

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
        "Fleet",
        ["ALL", "A320-1", "A320-2", "A320-3", "A330-1", "A330-2"]
    )
with col3:
    role_filter = st.selectbox("Role", ["ALL", "CPT", "FO"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# =====================
# ROSTER ENGINE v2
# Single source of truth: CAA_RULES from ftl_validator
# =====================
def generate_roster(start_d, end_d):
    """
    FTL-compliant roster engine v2.
    - Uses CAA_RULES as single source of truth
    - Rest check uses actual per-slot debrief times
    - Fair rotation: least hours crew assigned first
    - 7-day and 28-day rolling hour caps enforced
    - Overlap detection per crew slot
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

    # ── Crew state tracker ──────────────────────────────
    crew_state = {}
    for _, c in crew_df.iterrows():
        crew_state[c['crew_id']] = {
            'name':            c['name'],
            'role':            c['role'],
            'fleet':           c['fleet'],
            'assigned_slots':  [],   # (report_dt, debrief_dt, fdp_hrs, date)
            'daily_hours':     {},   # date_str -> float hours
            'hours_28day':     0.0,
        }

    roster_records = []
    unassigned_log = []

    # ── Helper functions ────────────────────────────────
    def get_last_debrief(state):
        if not state['assigned_slots']:
            return None
        return max(slot[1] for slot in state['assigned_slots'])

    def get_7day_hours(state, flight_date):
        week_start = flight_date - timedelta(days=6)
        return sum(
            v for d_str, v in state['daily_hours'].items()
            if week_start <= date.fromisoformat(d_str) <= flight_date
        )

    def has_overlap(state, report_dt, debrief_dt):
        for (slot_rep, slot_deb, _, _) in state['assigned_slots']:
            if report_dt < slot_deb and debrief_dt > slot_rep:
                return True
        return False

    def has_sufficient_rest(state, report_dt):
        last_deb = get_last_debrief(state)
        if last_deb is None:
            return True, 999.0
        rest_hrs = (report_dt - last_deb).total_seconds() / 3600
        return rest_hrs >= CAA_RULES['min_rest_hours'], rest_hrs

    # ── Process each flight ─────────────────────────────
    for _, flight in flights.iterrows():
        aircraft    = flight['aircraft']
        fleet_type  = aircraft.split('-')[0]   # A320 or A330
        dep_dt      = pd.to_datetime(flight['dep_time'])
        arr_dt      = pd.to_datetime(flight['arr_time'])
        flight_date = pd.to_datetime(flight['flight_date']).date()

        fdp_hrs    = (arr_dt - dep_dt).total_seconds() / 3600
        report_dt  = dep_dt  - timedelta(
                         minutes=CAA_RULES['report_before_dep'])
        debrief_dt = arr_dt  + timedelta(
                         minutes=CAA_RULES['debrief_after_arr'])
        max_fdp    = CAA_RULES['max_fdp'][fleet_type]

        # Skip flights that exceed max FDP
        if fdp_hrs > max_fdp:
            unassigned_log.append({
                'callsign': flight['callsign'],
                'reason':   f"FDP {fdp_hrs:.1f}h exceeds max {max_fdp}h"
            })
            continue

        # Assign CPT then FO
        for role in ['CPT', 'FO']:
            candidates = []

            for cid, state in crew_state.items():

                # Filter 1: Role
                if state['role'] != role:
                    continue

                # Filter 2: Fleet type
                if fleet_type not in state['fleet']:
                    continue

                # Filter 3: No overlapping duty
                if has_overlap(state, report_dt, debrief_dt):
                    continue

                # Filter 4: Minimum rest
                legal_rest, rest_hrs = has_sufficient_rest(
                    state, report_dt)
                if not legal_rest:
                    continue

                # Filter 5: 7-day rolling hours
                h7 = get_7day_hours(state, flight_date)
                if h7 + fdp_hrs > CAA_RULES['max_7day_hours']:
                    continue

                # Filter 6: 28-day hours
                if (state['hours_28day'] + fdp_hrs >
                        CAA_RULES['max_28day_hours']):
                    continue

                # Score — fair rotation + rest
                score = (
                    -state['hours_28day'] * 0.6 +
                     rest_hrs * 0.4
                )
                candidates.append((score, cid, rest_hrs, h7))

            if not candidates:
                unassigned_log.append({
                    'callsign': flight['callsign'],
                    'role':     role,
                    'reason':   f"No legal {role} for {fleet_type}"
                })
                continue

            # Pick highest score
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, best_cid, best_rest, best_h7 = candidates[0]

            # Record
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

            # Update state
            st_ref = crew_state[best_cid]
            st_ref['assigned_slots'].append(
                (report_dt, debrief_dt, fdp_hrs, flight_date))
            st_ref['hours_28day'] += fdp_hrs
            d_str = flight_date.isoformat()
            st_ref['daily_hours'][d_str] = (
                st_ref['daily_hours'].get(d_str, 0.0) + fdp_hrs)

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

    st.success(
        f"✅ Roster generated — "
        f"{len(records)} assignments | "
        f"{len(unassigned)} unassigned"
    )

    # ── FTL Post-Validation ──────────────────────────
    with st.spinner("Running CAA Pakistan FTL validation..."):
        ftl_result = generate_compliance_report(start_d, end_d)
        s = ftl_result['summary']

    if s['violations'] == 0 and s['warnings'] == 0:
        st.success(
            f"✅ FTL Validation PASSED — "
            f"100% compliant | "
            f"{s['total_duties']} duties validated"
        )
    elif s['violations'] > 0:
        st.error(
            f"🔴 FTL VIOLATIONS — "
            f"{s['violations']} violations | "
            f"{s['warnings']} warnings | "
            f"{s['compliance_pct']}% compliant"
        )
        for v in ftl_result['violations'][:5]:
            st.error(f"🔴 {v['crew_name']} — {v['details']}")
    else:
        st.warning(
            f"🟡 FTL Warnings — "
            f"{s['warnings']} warnings | "
            f"{s['compliance_pct']}% compliant"
        )
        for w in ftl_result['warnings'][:5]:
            st.warning(f"🟡 {w['crew_name']} — {w['details']}")

    # ── Utilization Summary ──────────────────────────
    st.markdown("#### 📊 Crew Utilization")
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
                'Status': (
                    '🔴 OVER'  if state['hours_28day'] > 190
                    else '🟡 HIGH' if state['hours_28day'] > 150
                    else '✅ OK'
                )
            })

    if util_rows:
        util_df = pd.DataFrame(util_rows).sort_values(
            '28D Hrs', ascending=False)

        def color_util(row):
            if '🔴' in str(row['Status']):
                return ['background-color:#4a0000'] * len(row)
            if '🟡' in str(row['Status']):
                return ['background-color:#4a3000'] * len(row)
            return [''] * len(row)

        st.dataframe(
            util_df.style.apply(color_util, axis=1),
            use_container_width=True,
            height=300
        )

    if unassigned:
        st.warning(f"⚠️ {len(unassigned)} unassigned slots")
        st.dataframe(
            pd.DataFrame(unassigned),
            use_container_width=True
        )

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
        SPLIT_PART(f.callsign,'-',1) || '-' ||
        SPLIT_PART(f.callsign,'-',2) AS callsign,
        f.origin,
        f.destination,
        TO_CHAR(f.dep_time,  'HH24MI') AS dep,
        TO_CHAR(f.arr_time,  'HH24MI') AS arr,
        r.fdp_hours,
        TO_CHAR(r.report_time,  'HH24MI') AS report,
        TO_CHAR(r.debrief_time, 'HH24MI') AS debrief,
        r.status,
        r.override_flag
    FROM roster r
    JOIN crew    c ON r.crew_id   = c.crew_id
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

query += """
    ORDER BY r.duty_date, f.aircraft, c.role DESC, f.dep_time
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

if df.empty:
    st.info("No roster found. Click 'Generate Roster' above.")
else:
    df['date'] = pd.to_datetime(
        df['duty_date']).dt.strftime('%d/%m/%y-%a')

    display_df = df[[
        'date', 'role', 'name', 'crew_id', 'aircraft',
        'callsign', 'origin', 'destination',
        'dep', 'arr', 'report', 'debrief',
        'fdp_hours', 'status', 'override_flag'
    ]].copy()

    display_df.columns = [
        'Date', 'Role', 'Name', 'ID', 'Aircraft',
        'Callsign', 'From', 'To',
        'DEP', 'ARR', 'Report', 'Debrief',
        'FDP Hrs', 'Status', 'Override'
    ]

    total     = len(df)
    overrides = int(df['override_flag'].sum())
    unassign  = len(df[df['status'] == 'UNASSIGNED'])

    st.markdown(f"""
    <div style="display:flex; gap:3rem; font-size:0.95rem;
    margin-bottom:1rem; font-weight:600;">
        <span>📋 Total Assignments: <b>{total}</b></span>
        <span>⚠️ Overrides: <b>{overrides}</b></span>
        <span>🔴 Unassigned: <b>{unassign}</b></span>
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
            return ['background-color:#4a2800; color:#FFD580'] * len(row)
        if row['Status'] == 'UNASSIGNED':
            return ['background-color:#4a0000'] * len(row)
        return [''] * len(row)

    st.dataframe(
        display_df.style.apply(color_row, axis=1),
        use_container_width=True,
        height=600
    )