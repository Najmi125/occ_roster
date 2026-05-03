import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(
    page_title="Analytics & Statistics",
    page_icon="📊",
    layout="wide"
)
st.title("📊 Operations Analytics")
st.markdown("**CFO & Management View — Block Hours, Crew Utilization, Cost Indicators**")

engine = get_engine()
today  = date.today()
end_dt = today + timedelta(days=27)

# ── Filters ──────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    date_range = st.date_input(
        "Analysis Period",
        value=(today, end_dt),
        format="DD/MM/YYYY"
    )
with col2:
    fleet_filter = st.selectbox(
        "Fleet", ["ALL","A320-1","A320-2","A320-3","A330-1","A330-2"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

params = {"sd": start_d, "ed": end_d}
fleet_clause = ""
if fleet_filter != "ALL":
    fleet_clause = " AND f.aircraft = :fleet"
    params["fleet"] = fleet_filter

ts = datetime.now().strftime("%d%m%H%M")

# ══════════════════════════════════════════════════════════
# SECTION 1 — FLEET BLOCK HOURS
# ══════════════════════════════════════════════════════════
st.divider()
st.subheader("✈️ Fleet Block Hours")

with engine.connect() as conn:
    block_df = pd.read_sql(text(f"""
        SELECT
            f.aircraft,
            COUNT(DISTINCT f.flight_id) AS total_flights,
            ROUND(SUM(
                EXTRACT(EPOCH FROM (f.arr_time - f.dep_time))/3600
            )::numeric, 2) AS total_block_hrs,
            ROUND(AVG(
                EXTRACT(EPOCH FROM (f.arr_time - f.dep_time))/3600
            )::numeric, 2) AS avg_block_per_flight,
            COUNT(DISTINCT f.flight_date) AS days_operated
        FROM flights f
        WHERE f.flight_date BETWEEN :sd AND :ed
        AND f.status = 'SCHEDULED'
        {fleet_clause}
        GROUP BY f.aircraft
        ORDER BY f.aircraft
    """), conn, params=params)

if not block_df.empty:
    total_flights = int(block_df['total_flights'].sum())
    total_block   = float(block_df['total_block_hrs'].sum())
    total_days    = int((end_d - start_d).days + 1)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Flights",     total_flights)
    c2.metric("Total Block Hours", f"{total_block:.1f}h")
    c3.metric("Avg Block/Day",     f"{total_block/total_days:.1f}h")
    c4.metric("Period (days)",     total_days)

    block_df.columns = [
        'Aircraft','Flights','Block Hrs','Avg/Flight (h)','Days Operated']
    st.dataframe(block_df, use_container_width=True, height=220)
    st.download_button(
        "⬇️ Download Block Hours",
        block_df.to_csv(index=False).encode(),
        f"block_hours_{ts}.csv", "text/csv"
    )
else:
    st.warning("No flight data found.")

# ══════════════════════════════════════════════════════════
# SECTION 2 — CREW DUTY HOURS (correct: per duty)
# ══════════════════════════════════════════════════════════
st.divider()
st.subheader("👨‍✈️ Crew Duty Hours")

with engine.connect() as conn:
    # Get one FDP value per duty per crew (not per flight)
    raw = pd.read_sql(text("""
        SELECT DISTINCT
            r.crew_id,
            r.duty_id,
            r.fdp_hours
        FROM roster r
        WHERE r.duty_date BETWEEN :sd AND :ed
        AND r.status = 'ASSIGNED'
        AND r.duty_id IS NOT NULL
    """), conn, params={"sd": start_d, "ed": end_d})

    crew_info = pd.read_sql(text("""
        SELECT crew_id, name, role, fleet FROM crew
    """), conn)

# Deduplicate: one row per crew per duty
raw = raw.drop_duplicates(subset=['crew_id','duty_id'])

# Sum FDP per crew
fdp_df = raw.groupby('crew_id').agg(
    fdp_hours=('fdp_hours','sum'),
    total_duties=('duty_id','count')
).reset_index()

# Merge crew info
fdp_df = fdp_df.merge(crew_info, on='crew_id')

# Apply fleet filter
if fleet_filter != "ALL":
    fdp_df = fdp_df[fdp_df['fleet'].str.startswith(fleet_filter[:4])]

fdp_df = fdp_df.sort_values('fdp_hours', ascending=False)

if not fdp_df.empty:
    total_crew   = len(fdp_df)
    max_fdp      = float(fdp_df['fdp_hours'].max())
    min_fdp      = float(fdp_df['fdp_hours'].min())
    mean_fdp     = float(fdp_df['fdp_hours'].mean())
    std_fdp      = float(fdp_df['fdp_hours'].std())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Crew Assigned",  total_crew)
    c2.metric("Max FDP Hours",  f"{max_fdp:.1f}h")
    c3.metric("Min FDP Hours",  f"{min_fdp:.1f}h")
    c4.metric("Mean FDP Hours", f"{mean_fdp:.1f}h")
    c5.metric("Std Dev",        f"{std_fdp:.1f}h",
              help="Lower = fairer distribution")

    over_cap = fdp_df[fdp_df['fdp_hours'] > 190]
    near_cap = fdp_df[
        (fdp_df['fdp_hours'] > 161) & (fdp_df['fdp_hours'] <= 190)]
    if len(over_cap) > 0:
        st.error(f"🔴 {len(over_cap)} crew exceed 190h CAA limit")
    elif len(near_cap) > 0:
        st.warning(f"🟡 {len(near_cap)} crew above 85% of 190h limit")
    else:
        st.success("✅ All crew within CAA 28-day FDP limits")

    fdp_df['CAA Status'] = fdp_df['fdp_hours'].apply(
        lambda x: '🔴 OVER'  if x > 190
        else ('🟡 HIGH'      if x > 161 else '✅ OK')
    )
    fdp_df.columns = [
        'ID','Name','Role','Fleet','Duties','FDP Hours','CAA Status']

    def color_crew(row):
        if '🔴' in str(row['CAA Status']):
            return ['background-color:#4a0000'] * len(row)
        if '🟡' in str(row['CAA Status']):
            return ['background-color:#4a3000'] * len(row)
        return [''] * len(row)

    st.dataframe(
        fdp_df.style.apply(color_crew, axis=1),
        use_container_width=True, height=500
    )
    st.download_button(
        "⬇️ Download Crew Hours",
        fdp_df.to_csv(index=False).encode(),
        f"crew_hours_{ts}.csv", "text/csv"
    )
else:
    st.warning("No roster data found. Generate roster first.")

# ══════════════════════════════════════════════════════════
# SECTION 3 — ROUTE ANALYSIS
# ══════════════════════════════════════════════════════════
st.divider()
st.subheader("🗺️ Route Analysis")

with engine.connect() as conn:
    route_df = pd.read_sql(text(f"""
        SELECT
            f.origin || ' to ' || f.destination AS route,
            f.aircraft,
            COUNT(*)   AS sectors,
            ROUND(SUM(
                EXTRACT(EPOCH FROM (f.arr_time - f.dep_time))/3600
            )::numeric, 2) AS block_hrs,
            ROUND(AVG(
                EXTRACT(EPOCH FROM (f.arr_time - f.dep_time))/3600
            )::numeric, 2) AS avg_block
        FROM flights f
        WHERE f.flight_date BETWEEN :sd AND :ed
        AND f.status = 'SCHEDULED'
        {fleet_clause}
        GROUP BY f.origin, f.destination, f.aircraft
        ORDER BY block_hrs DESC
    """), conn, params=params)

if not route_df.empty:
    route_df.columns = [
        'Route','Aircraft','Sectors','Block Hrs','Avg Block (h)']
    st.dataframe(route_df, use_container_width=True, height=300)
    st.download_button(
        "⬇️ Download Route Analysis",
        route_df.to_csv(index=False).encode(),
        f"route_analysis_{ts}.csv", "text/csv"
    )

# ══════════════════════════════════════════════════════════
# SECTION 4 — DAILY OPERATIONS SUMMARY
# ══════════════════════════════════════════════════════════
st.divider()
st.subheader("📅 Daily Operations Summary")

with engine.connect() as conn:
    daily_df = pd.read_sql(text(f"""
        SELECT
            f.flight_date,
            COUNT(DISTINCT f.flight_id) AS flights,
            ROUND(SUM(
                EXTRACT(EPOCH FROM (f.arr_time - f.dep_time))/3600
            )::numeric, 2) AS block_hrs,
            COUNT(DISTINCT r.crew_id)   AS crew_on_duty
        FROM flights f
        LEFT JOIN roster r
               ON f.flight_id = r.flight_id
              AND r.status = 'ASSIGNED'
        WHERE f.flight_date BETWEEN :sd AND :ed
        AND f.status = 'SCHEDULED'
        {fleet_clause}
        GROUP BY f.flight_date
        ORDER BY f.flight_date
    """), conn, params=params)

if not daily_df.empty:
    daily_df['flight_date'] = pd.to_datetime(
        daily_df['flight_date']).dt.strftime('%d/%m/%y-%a')
    daily_df.columns = ['Date','Flights','Block Hrs','Crew on Duty']
    st.dataframe(daily_df, use_container_width=True, height=400)
    st.download_button(
        "⬇️ Download Daily Summary",
        daily_df.to_csv(index=False).encode(),
        f"daily_ops_{ts}.csv", "text/csv"
    )

# ══════════════════════════════════════════════════════════
# SECTION 5 — DISRUPTION SUMMARY
# ══════════════════════════════════════════════════════════
st.divider()
st.subheader("⚡ Disruption Summary")

with engine.connect() as conn:
    dis_df = pd.read_sql(text("""
        SELECT
            disruption_type,
            COUNT(*) AS total,
            SUM(CASE WHEN resolved     THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN NOT resolved THEN 1 ELSE 0 END) AS open
        FROM disruptions
        WHERE DATE(created_at) BETWEEN :sd AND :ed
        GROUP BY disruption_type
        ORDER BY total DESC
    """), conn, params={"sd": start_d, "ed": end_d})

if dis_df.empty:
    st.info("No disruptions in selected period.")
else:
    dis_df.columns = ['Type','Total','Resolved','Open']
    st.dataframe(dis_df, use_container_width=True, height=200)

st.divider()
st.caption(
    f"Analytics generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC  |  "
    f"Period: {start_d.strftime('%d/%m/%Y')} to {end_d.strftime('%d/%m/%Y')}"
)