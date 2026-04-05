import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="OCC Dashboard", page_icon="🎯", layout="wide")
st.title("🎯 OCC Dashboard — Operations Control Centre")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

st.markdown(f"**Live as of:** {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC")
st.divider()

# =====================
# ROW 1 — KEY METRICS
# =====================
with engine.connect() as conn:

    total_flights_today = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM flights
        WHERE flight_date = :td
    """), conn, params={"td": today}).iloc[0]['cnt']

    delayed_today = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM flights
        WHERE flight_date = :td AND status = 'DELAYED'
    """), conn, params={"td": today}).iloc[0]['cnt']

    cancelled_today = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM flights
        WHERE flight_date = :td AND status = 'FLIGHT CANCELLED'
    """), conn, params={"td": today}).iloc[0]['cnt']

    total_crew = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM crew WHERE is_active = TRUE
    """), conn).iloc[0]['cnt']

    active_disruptions = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM disruptions WHERE resolved = FALSE
    """), conn).iloc[0]['cnt']

    red_alerts = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM alerts
        WHERE severity = 'RED' AND acknowledged = FALSE
    """), conn).iloc[0]['cnt']

    yellow_alerts = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM alerts
        WHERE severity = 'YELLOW' AND acknowledged = FALSE
    """), conn).iloc[0]['cnt']

    roster_today = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM roster
        WHERE duty_date = :td
    """), conn, params={"td": today}).iloc[0]['cnt']

    unassigned_today = pd.read_sql(text("""
        SELECT COUNT(*) as cnt FROM roster
        WHERE duty_date = :td AND status = 'UNASSIGNED'
    """), conn, params={"td": today}).iloc[0]['cnt']

# Metrics Row 1
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("✈️ Flights Today",    int(total_flights_today))
c2.metric("⏱️ Delayed",          int(delayed_today))
c3.metric("❌ Cancelled",         int(cancelled_today))
c4.metric("👨‍✈️ Total Crew",      int(total_crew))
c5.metric("📋 Duties Today",     int(roster_today))

# Metrics Row 2
c6,c7,c8,c9,c10 = st.columns(5)
c6.metric("⚡ Active Disruptions", int(active_disruptions),
          delta=f"-{int(active_disruptions)}" if active_disruptions > 0 else None,
          delta_color="inverse")
c7.metric("🔴 Red Alerts",        int(red_alerts),
          delta=f"-{int(red_alerts)}" if red_alerts > 0 else None,
          delta_color="inverse")
c8.metric("🟡 Yellow Alerts",     int(yellow_alerts),
          delta=f"-{int(yellow_alerts)}" if yellow_alerts > 0 else None,
          delta_color="inverse")
c9.metric("🔴 Unassigned",        int(unassigned_today),
          delta=f"-{int(unassigned_today)}" if unassigned_today > 0 else None,
          delta_color="inverse")
c10.metric("📅 Roster Window",    "28 Days")

st.divider()

# =====================
# ROW 2 — TODAY'S FLIGHTS + DISRUPTIONS
# =====================
col_left, col_right = st.columns(2)

# --- Today's Flight Status ---
with col_left:
    st.subheader("🛫 Today's Flights")
    with engine.connect() as conn:
        flights_today = pd.read_sql(text("""
            SELECT
                SPLIT_PART(callsign,'-',1)||'-'||SPLIT_PART(callsign,'-',2) AS callsign,
                aircraft,
                origin,
                destination,
                TO_CHAR(dep_time,'HH24MI') AS dep,
                TO_CHAR(arr_time,'HH24MI') AS arr,
                status
            FROM flights
            WHERE flight_date = :td
            ORDER BY dep_time
        """), conn, params={"td": today})

    if flights_today.empty:
        st.info("No flights today.")
    else:
        def color_flight_status(row):
            if row['status'] == 'DELAYED':
                return ['background-color: #4a3000'] * len(row)
            elif row['status'] in ['FLIGHT CANCELLED', 'AIRCRAFT AOG']:
                return ['background-color: #4a0000'] * len(row)
            return [''] * len(row)

        flights_today.columns = ['Callsign', 'Aircraft', 'From',
                                  'To', 'DEP', 'ARR', 'Status']

        def color_flight_status(row):
            if row['Status'] == 'DELAYED':
                return ['background-color: #4a3000'] * len(row)
            elif row['Status'] in ['FLIGHT CANCELLED', 'AIRCRAFT AOG']:
                return ['background-color: #4a0000'] * len(row)
            return [''] * len(row)

        styled = flights_today.style.apply(color_flight_status, axis=1)
        st.dataframe(styled, use_container_width=True, height=350)

# --- Active Disruptions ---
with col_right:
    st.subheader("⚡ Active Disruptions")
    with engine.connect() as conn:
        dis_df = pd.read_sql(text("""
            SELECT
                disruption_type,
                affected_flight,
                affected_crew,
                reason,
                reported_by,
                TO_CHAR(disruption_time,'DD/MM/YY HH24:MI') AS time
            FROM disruptions
            WHERE resolved = FALSE
            ORDER BY created_at DESC
            LIMIT 10
        """), conn)

    if dis_df.empty:
        st.success("✅ No active disruptions.")
    else:
        dis_df.columns = ['Type', 'Flight', 'Crew', 'Reason', 'Reported By', 'Time']
        def color_dis(row):
            return ['background-color: #4a0000'] * len(row)
        styled = dis_df.style.apply(color_dis, axis=1)
        st.dataframe(styled, use_container_width=True, height=350)

st.divider()

# =====================
# ROW 3 — ALERTS + CREW AVAILABILITY
# =====================
col_left2, col_right2 = st.columns(2)

# --- Active Alerts ---
with col_left2:
    st.subheader("🚨 Active Alerts")
    with engine.connect() as conn:
        alerts_df = pd.read_sql(text("""
            SELECT
                a.severity,
                c.name,
                c.role,
                c.fleet,
                a.alert_type,
                a.alert_message,
                a.days_remaining
            FROM alerts a
            JOIN crew c ON a.crew_id = c.crew_id
            WHERE a.acknowledged = FALSE
            ORDER BY a.days_remaining ASC
            LIMIT 15
        """), conn)

    if alerts_df.empty:
        st.success("✅ No active alerts.")
    else:
        alerts_df['severity'] = alerts_df['severity'].apply(
            lambda x: '🔴' if x == 'RED' else '🟡')
        alerts_df.columns = ['⚠️', 'Name', 'Role', 'Fleet',
                              'Type', 'Message', 'Days Left']

        def color_alert(row):
            if '🔴' in str(row['⚠️']):
                return ['background-color: #4a0000'] * len(row)
            return ['background-color: #4a3000'] * len(row)

        styled = alerts_df.style.apply(color_alert, axis=1)
        st.dataframe(styled, use_container_width=True, height=350)

# --- Crew Availability Today ---
with col_right2:
    st.subheader("👨‍✈️ Crew Availability Today")
    with engine.connect() as conn:
        avail_df = pd.read_sql(text("""
            SELECT
                c.crew_id,
                c.name,
                c.role,
                c.fleet,
                COUNT(r.roster_id) as duties,
                COALESCE(SUM(r.fdp_hours),0) as fdp_hrs
            FROM crew c
            LEFT JOIN roster r ON c.crew_id = r.crew_id
                AND r.duty_date = :td
            WHERE c.is_active = TRUE
            GROUP BY c.crew_id, c.name, c.role, c.fleet
            ORDER BY c.fleet, c.role, duties DESC
        """), conn, params={"td": today})

    if avail_df.empty:
        st.info("No crew data.")
    else:
        avail_df['availability'] = avail_df['duties'].apply(
            lambda x: '🟢 Available' if x == 0 else '🔵 On Duty')
        avail_df.columns = ['ID', 'Name', 'Role', 'Fleet',
                            'Duties', 'FDP Hrs', 'Status']

        def color_avail(row):
            if '🔵' in str(row['Status']):
                return ['background-color: #002244'] * len(row)
            return [''] * len(row)

        styled = avail_df.style.apply(color_avail, axis=1)
        st.dataframe(styled, use_container_width=True, height=350)

st.divider()

# =====================
# ROW 4 — 28 DAY OVERVIEW
# =====================
st.subheader("📊 28 Day Fleet Overview")

with engine.connect() as conn:
    overview_df = pd.read_sql(text("""
        SELECT
            aircraft,
            COUNT(*) as total_flights,
            SUM(EXTRACT(EPOCH FROM (arr_time - dep_time))/3600) as block_hrs
        FROM flights
        WHERE flight_date BETWEEN :sd AND :ed
        GROUP BY aircraft
        ORDER BY aircraft
    """), conn, params={"sd": today, "ed": end_date})

if not overview_df.empty:
    overview_df['block_hrs'] = overview_df['block_hrs'].apply(lambda x: round(x, 1))
    overview_df.columns = ['Aircraft', 'Total Flights', 'Block Hours']

    o1, o2, o3, o4, o5 = st.columns(5)
    cols = [o1, o2, o3, o4, o5]
    for i, row in overview_df.iterrows():
        if i < 5:
            cols[i].metric(
                row['Aircraft'],
                f"{int(row['Total Flights'])} flt",
                f"{row['Block Hours']} hrs"
            )

st.divider()

# --- Download Full Report ---
timestamp = datetime.now().strftime("%d%m%H%M")
with engine.connect() as conn:
    full_report = pd.read_sql(text("""
        SELECT
            r.duty_date,
            c.name,
            c.role,
            c.fleet,
            SPLIT_PART(f.callsign,'-',1)||'-'||SPLIT_PART(f.callsign,'-',2) AS callsign,
            f.origin,
            f.destination,
            TO_CHAR(f.dep_time,'HH24MI') AS dep,
            TO_CHAR(f.arr_time,'HH24MI') AS arr,
            r.fdp_hours,
            r.status,
            r.override_flag
        FROM roster r
        JOIN crew c ON r.crew_id = c.crew_id
        JOIN flights f ON r.flight_id = f.flight_id
        WHERE r.duty_date BETWEEN :sd AND :ed
        ORDER BY r.duty_date, f.aircraft, f.dep_time
    """), conn, params={"sd": today, "ed": end_date})

csv = full_report.to_csv(index=False).encode('utf-8')
st.download_button(
    label="⬇️ Download Full OCC Report",
    data=csv,
    file_name=f"occ_report_{timestamp}.csv",
    mime="text/csv"
)