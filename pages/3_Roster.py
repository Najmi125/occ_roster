import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime
from utils.ftl_validator import generate_compliance_report, CAA_RULES
from utils.duty_builder import build_duties_for_date

st.set_page_config(page_title="28 Day Roster", page_icon="📋", layout="wide")
st.title("📋 28 Day Rolling Crew Roster")

engine   = get_engine()
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
    fleet_filter = st.selectbox("Fleet",
        ["ALL","A320-1","A320-2","A320-3","A330-1","A330-2"])
with col3:
    role_filter = st.selectbox("Role", ["ALL","CPT","FO"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# =====================
# ROSTER ENGINE v8
# All fixes applied:
# FIX 1: next_available time lock in crew state
# FIX 2: Hard guards in is_legal()
# FIX 3: commit() sets next_available
# FIX 4: Full is_legal() check before commit
# FIX 5: Utilization moved below roster
# FIX 6: width='stretch' throughout
# =====================
def generate_roster(start_d, end_d):

    with engine.connect() as conn:
        flights_df = pd.read_sql(text("""
            SELECT flight_id, aircraft, callsign,
                   origin, destination,
                   dep_time, arr_time, flight_date
            FROM flights
            WHERE flight_date BETWEEN :sd AND :ed
            AND status = 'SCHEDULED'
            ORDER BY flight_date, dep_time
        """), conn, params={"sd": start_d, "ed": end_d})

        crew_df = pd.read_sql(text("""
            SELECT crew_id, name, role, fleet
            FROM crew WHERE is_active = TRUE
            ORDER BY fleet, role, crew_id
        """), conn)

    def get_ft(s):
        return s.split('-')[0]

    # Crew state — FIX 1: next_available added
    crew_state = {}
    for _, c in crew_df.iterrows():
        crew_state[c['crew_id']] = {
            'name':            c['name'],
            'role':            c['role'],
            'fleet':           c['fleet'],
            'current_city':    'KHI',
            'assigned_duties': [],
            'daily_hours':     {},
            'hours_28day':     0.0,
            'last_debrief':    None,
            'last_fdp':        0.0,
            'next_available':  None,   # FIX 1: rest time lock
        }

    roster_records = []
    unassigned_log = []

    # ── Helpers ─────────────────────────────────────────

    def get_7day(state, duty_date):
        ws = duty_date - timedelta(days=6)
        return sum(v for d, v in state['daily_hours'].items()
                   if ws <= date.fromisoformat(d) <= duty_date)

    def has_overlap(state, rep, deb):
        """Clean interval overlap check."""
        for p in state['assigned_duties']:
            if rep < p['debrief_dt'] and deb > p['report_dt']:
                return True
        return False

    def is_legal(state, duty, duty_date):
        """
        Full legality check.
        FIX 2: Hard guards added — next_available and last_debrief.
        """
        ft      = get_ft(duty['fleet'])
        max_fdp = CAA_RULES['max_fdp'][ft]
        fdp     = duty['fdp_hrs']

        # HARD GUARD: report before last debrief (kills negative rest)
        if state['last_debrief'] and duty['report_dt'] < state['last_debrief']:
            return False

        # HARD AVAILABILITY GUARD: crew not yet available
        if state.get('next_available') and duty['report_dt'] < state['next_available']:
            return False

        # Overlap check
        if has_overlap(state, duty['report_dt'], duty['debrief_dt']):
            return False

        # FDP limit
        if fdp > max_fdp:
            return False

        # FDP-based rest check
        if state['last_debrief'] is not None:
            rest = (duty['report_dt'] - state['last_debrief']).total_seconds() / 3600
            req  = 14.0 if state['last_fdp'] > 10.0 else 12.0
            if rest < req:
                return False

        # 7-day hours
        if get_7day(state, duty_date) + fdp > CAA_RULES['max_7day_hours']:
            return False

        # 28-day hours
        if state['hours_28day'] + fdp > CAA_RULES['max_28day_hours']:
            return False

        return True

    def find_crew(role, duty, duty_date, state_to_use, exclude_cid=None):
        """Find best legal crew from given state."""
        ft     = get_ft(duty['fleet'])
        origin = duty['requires_at'] if duty.get('requires_at') else duty['origin']
        best   = []

        for cid, s in state_to_use.items():
            if s['role'] != role:
                continue
            if exclude_cid and cid == exclude_cid:
                continue
            if get_ft(s['fleet']) != ft:
                continue
            if s['current_city'] != origin:
                continue
            if not is_legal(s, duty, duty_date):
                continue

            score = -s['hours_28day']
            if duty['ends_at'] == 'KHI' and s['current_city'] != 'KHI':
                score += 5
            best.append((score, cid))

        if not best:
            return None
        best.sort(reverse=True)
        return best[0][1]

    def commit(cid, duty, duty_date):
        """
        Commit duty to real crew state.
        FIX 3: Sets next_available after each duty.
        """
        s = crew_state[cid]
        s['assigned_duties'].append(duty)
        s['hours_28day'] += duty['fdp_hrs']
        d = duty_date.isoformat()
        s['daily_hours'][d] = s['daily_hours'].get(d, 0.0) + duty['fdp_hrs']
        s['last_debrief']  = duty['debrief_dt']
        s['last_fdp']      = duty['fdp_hrs']
        s['current_city']  = duty['ends_at']

        # FIX 3: Set next_available based on FDP-based rest
        rest_req = 14.0 if s['last_fdp'] > 10.0 else 12.0
        s['next_available'] = duty['debrief_dt'] + timedelta(hours=rest_req)

    def copy_state(state):
        """Deep copy crew state for isolated FO selection."""
        result = {}
        for cid, s in state.items():
            result[cid] = {
                'name':            s['name'],
                'role':            s['role'],
                'fleet':           s['fleet'],
                'current_city':    s['current_city'],
                'assigned_duties': list(s['assigned_duties']),
                'daily_hours':     dict(s['daily_hours']),
                'hours_28day':     s['hours_28day'],
                'last_debrief':    s['last_debrief'],
                'last_fdp':        s['last_fdp'],
                'next_available':  s['next_available'],
            }
        return result

    def temp_commit_state(state, cid, duty, duty_date):
        """Apply temporary commit to copied state."""
        s = state[cid]
        s['assigned_duties'] = list(s['assigned_duties']) + [duty]
        s['hours_28day'] += duty['fdp_hrs']
        d = duty_date.isoformat()
        s['daily_hours'] = dict(s['daily_hours'])
        s['daily_hours'][d] = s['daily_hours'].get(d, 0.0) + duty['fdp_hrs']
        s['last_debrief']  = duty['debrief_dt']
        s['last_fdp']      = duty['fdp_hrs']
        s['current_city']  = duty['ends_at']
        rest_req = 14.0 if s['last_fdp'] > 10.0 else 12.0
        s['next_available'] = duty['debrief_dt'] + timedelta(hours=rest_req)

    # ── Process day by day ───────────────────────────────
    current = start_d
    while current <= end_d:
        day_df = flights_df[
            pd.to_datetime(flights_df['flight_date']).dt.date == current
        ]
        if day_df.empty:
            current += timedelta(days=1)
            continue

        duties = build_duties_for_date(day_df, current)

        for duty in duties:

            # STEP 1: Find CPT from real state
            cpt = find_crew('CPT', duty, current, crew_state)
            if cpt is None:
                unassigned_log.append({
                    'date':   current.strftime('%d/%m/%y'),
                    'duty':   duty['duty_id'],
                    'reason': f"No CPT at {duty.get('requires_at', duty['origin'])}"
                })
                continue

            # STEP 2: Simulate CPT on temp state
            temp = copy_state(crew_state)
            temp_commit_state(temp, cpt, duty, current)

            # STEP 3: Find FO from updated temp state
            fo = find_crew('FO', duty, current, temp, exclude_cid=cpt)
            if fo is None:
                unassigned_log.append({
                    'date':   current.strftime('%d/%m/%y'),
                    'duty':   duty['duty_id'],
                    'reason': f"No FO at {duty.get('requires_at', duty['origin'])}"
                })
                continue

            # FIX 4: Full is_legal() on REAL state before commit
            if not is_legal(crew_state[cpt], duty, current):
                unassigned_log.append({
                    'date':   current.strftime('%d/%m/%y'),
                    'duty':   duty['duty_id'],
                    'reason': "CPT failed legality at commit"
                })
                continue

            if not is_legal(crew_state[fo], duty, current):
                unassigned_log.append({
                    'date':   current.strftime('%d/%m/%y'),
                    'duty':   duty['duty_id'],
                    'reason': "FO failed legality at commit"
                })
                continue

            # STEP 4: Safe atomic commit
            commit(cpt, duty, current)
            commit(fo,  duty, current)

            # STEP 5: Record
            layover = duty['ends_at'] if duty['ends_at'] != 'KHI' else None
            for cid in [cpt, fo]:
                for fid in duty['flight_ids']:
                    roster_records.append({
                        'crew_id':    cid,
                        'flight_id':  fid,
                        'duty_date':  current,
                        'duty_id':    duty['duty_id'],
                        'report_dt':  duty['report_dt'],
                        'debrief_dt': duty['debrief_dt'],
                        'fdp_hours':  duty['fdp_hrs'],
                        'status':     'ASSIGNED',
                        'layover':    layover,
                    })

        current += timedelta(days=1)

    return roster_records, unassigned_log, crew_state


# =====================
# GENERATE BUTTON
# =====================
if st.button("🔄 Generate / Refresh Roster", type="primary"):
    with st.spinner("Generating roster..."):
        records, unassigned, crew_state = generate_roster(start_d, end_d)

        with engine.connect() as conn:
            for col in ["ADD COLUMN IF NOT EXISTS duty_id VARCHAR(20)",
                        "ADD COLUMN IF NOT EXISTS layover VARCHAR(10)"]:
                try:
                    conn.execute(text(f"ALTER TABLE roster {col}"))
                    conn.commit()
                except Exception:
                    conn.rollback()

            conn.execute(text(
                "DELETE FROM roster WHERE duty_date BETWEEN :sd AND :ed"
            ), {"sd": start_d, "ed": end_d})

            for r in records:
                conn.execute(text("""
                    INSERT INTO roster
                    (crew_id, flight_id, duty_date, duty_id,
                     report_time, debrief_time, fdp_hours, status, layover)
                    VALUES
                    (:cid, :fid, :dd, :did, :rt, :dt, :fdp, :st, :lo)
                """), {
                    'cid': r['crew_id'], 'fid': r['flight_id'],
                    'dd':  r['duty_date'], 'did': r['duty_id'],
                    'rt':  r['report_dt'], 'dt':  r['debrief_dt'],
                    'fdp': r['fdp_hours'], 'st':  r['status'],
                    'lo':  r['layover'],
                })
            conn.commit()

    st.success(f"✅ {len(records)} assignments | {len(unassigned)} unassigned")

    # FTL validation
    with st.spinner("Validating FTL..."):
        ftl = generate_compliance_report(start_d, end_d)
        s   = ftl.get('summary', {})

    if not s:
        st.info("FTL: no data.")
    elif s.get('violations', 0) == 0 and s.get('warnings', 0) == 0:
        st.success(
            f"✅ FTL PASSED — 100% compliant | "
            f"{s.get('total_duties', 0)} duties validated"
        )
    elif s.get('violations', 0) > 0:
        st.error(
            f"🔴 {s.get('violations')} violations | "
            f"{s.get('compliance_pct')}% compliant"
        )
        for v in ftl.get('violations', [])[:5]:
            st.error(f"🔴 {v['crew_name']} — {v['details']}")
    else:
        st.warning(
            f"🟡 {s.get('warnings')} warnings | "
            f"{s.get('compliance_pct')}% compliant"
        )

    # Store crew_state for utilization display below
    st.session_state['crew_state_util'] = {
        cid: {
            'name':    s['name'],
            'role':    s['role'],
            'fleet':   s['fleet'],
            'city':    s['current_city'],
            'fdp':     round(s['hours_28day'], 1),
            'duties':  len(s['assigned_duties']),
        }
        for cid, s in crew_state.items()
        if s['hours_28day'] > 0
    }

    if unassigned:
        st.warning(f"⚠️ {len(unassigned)} unassigned")
        st.dataframe(pd.DataFrame(unassigned), width='stretch')

# =====================
# DISPLAY ROSTER  — FIX 5: before utilization
# =====================
st.divider()

query = """
    SELECT r.duty_date, c.name, c.role, f.aircraft,
        SPLIT_PART(f.callsign,'-',1)||'-'||
        SPLIT_PART(f.callsign,'-',2) AS callsign,
        f.origin, f.destination,
        TO_CHAR(f.dep_time,'HH24MI')    AS dep,
        TO_CHAR(f.arr_time,'HH24MI')    AS arr,
        TO_CHAR(r.report_time,'HH24MI') AS report,
        TO_CHAR(r.debrief_time,'HH24MI')AS debrief,
        r.fdp_hours,
        COALESCE(r.layover,'') AS layover,
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
query += " ORDER BY r.duty_date, f.aircraft, c.role DESC, f.dep_time"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

if df.empty:
    st.info("No roster found. Click Generate Roster above.")
else:
    df['date'] = pd.to_datetime(df['duty_date']).dt.strftime('%d/%m/%y-%a')
    for col in ['dep','arr','report','debrief']:
        df[col] = df[col].apply(
            lambda x: str(x).zfill(4) if pd.notna(x) else x)

    disp = df[[
        'date','role','name','aircraft','callsign',
        'origin','destination','dep','arr',
        'report','debrief','fdp_hours','layover','override_flag'
    ]].copy()
    disp.columns = [
        'Date','Role','Name','Aircraft','Callsign',
        'From','To','DEP','ARR',
        'Report','Debrief','FDP Hrs','Layover','Override'
    ]

    total = len(df)
    ovr   = int(df['override_flag'].sum())
    layo  = len(df[df['layover'] != ''])

    st.markdown(
        f"📋 **{total}** assignments &nbsp;|&nbsp; "
        f"✈️ Layovers: **{layo}** &nbsp;|&nbsp; "
        f"⚠️ Overrides: **{ovr}**"
    )

    ts = datetime.now().strftime("%d%m%H%M")
    st.download_button(
        "⬇️ Download Roster",
        disp.to_csv(index=False).encode(),
        f"roster_{ts}.csv", "text/csv"
    )

    def cr(row):
        if row['Override']: return ['background-color:#4a2800;color:#FFD580']*len(row)
        if row['Layover']:  return ['background-color:#003333']*len(row)
        return ['']*len(row)

    st.dataframe(
        disp.style.apply(cr, axis=1),
        width='stretch', height=600
    )

# =====================
# UTILIZATION — FIX 5: shown BELOW roster
# =====================
st.divider()
st.markdown("#### 📊 Crew Utilization")

util_data = st.session_state.get('crew_state_util', {})
if util_data:
    rows = []
    for cid, s in util_data.items():
        rows.append({
            'ID':      cid,
            'Name':    s['name'],
            'Role':    s['role'],
            'Fleet':   s['fleet'],
            'City':    s['city'],
            'FDP Hrs': s['fdp'],
            'Duties':  s['duties'],
            'Status': ('🔴 OVER'  if s['fdp'] > 190
                       else '🟡 HIGH' if s['fdp'] > 150
                       else '✅ OK')
        })

    udf = pd.DataFrame(rows).sort_values('FDP Hrs', ascending=False)

    def cu(row):
        if '🔴' in str(row['Status']): return ['background-color:#4a0000']*len(row)
        if '🟡' in str(row['Status']): return ['background-color:#4a3000']*len(row)
        return ['']*len(row)

    st.dataframe(udf.style.apply(cu, axis=1), width='stretch', height=300)
else:
    st.info("Generate roster to see utilization.")