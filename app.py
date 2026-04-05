import streamlit as st
from utils.db import test_connection
from datetime import datetime, date, timedelta
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text

st.set_page_config(
    page_title="Airline Operations Control & Optimization Platform",
    page_icon="✈️",
    layout="wide"
)

st.title("✈️ Airline Operations Control & Optimization Platform")
st.markdown(f"**{datetime.now().strftime('%A, %d %B %Y — %H:%M')} UTC**")
st.divider()

# DB Status
db_status = test_connection()
if db_status == True:
    st.success("✅ Database Connected — NeonDB Online")
else:
    st.error(f"❌ DB Error: {db_status}")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# --- Quick Stats ---
with engine.connect() as conn:
    total_crew = pd.read_sql(text("SELECT COUNT(*) as cnt FROM crew WHERE is_active=TRUE"), conn).iloc[0]['cnt']
    total_flights = pd.read_sql(text("SELECT COUNT(*) as cnt FROM flights WHERE flight_date BETWEEN :sd AND :ed"), conn, params={"sd":today,"ed":end_date}).iloc[0]['cnt']
    total_roster = pd.read_sql(text("SELECT COUNT(*) as cnt FROM roster WHERE duty_date BETWEEN :sd AND :ed"), conn, params={"sd":today,"ed":end_date}).iloc[0]['cnt']
    disruptions = pd.read_sql(text("SELECT COUNT(*) as cnt FROM disruptions WHERE resolved=FALSE"), conn).iloc[0]['cnt']
    red_alerts = pd.read_sql(text("SELECT COUNT(*) as cnt FROM alerts WHERE severity='RED' AND acknowledged=FALSE"), conn).iloc[0]['cnt']
    yellow_alerts = pd.read_sql(text("SELECT COUNT(*) as cnt FROM alerts WHERE severity='YELLOW' AND acknowledged=FALSE"), conn).iloc[0]['cnt']

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("👨‍✈️ Total Crew",       int(total_crew))
c2.metric("✈️ Flights (28 Day)",  int(total_flights))
c3.metric("📋 Roster Duties",     int(total_roster))
c4.metric("⚡ Disruptions",       int(disruptions))
c5.metric("🔴 Red Alerts",        int(red_alerts))
c6.metric("🟡 Yellow Alerts",     int(yellow_alerts))

st.divider()

# --- Navigation Guide ---
st.subheader("📌 Quick Navigation")
col1, col2 = st.columns(2)

with col1:
    st.markdown("""
    | Page | Description |
    |------|-------------|
    | 🛫 Flight Schedule | 28 day rolling flight schedule |
    | 👨‍✈️ Crew Data | Crew records & certifications |
    | 📋 Roster | 28 day crew roster |
    | ⚡ OCC Override | Log & manage disruptions |
    """)

with col2:
    st.markdown("""
    | Page | Description |
    |------|-------------|
    | 🪪 Crew Profile | Individual crew records & logbook |
    | ⏱️ FTL Monitor | CAA Pakistan FTL compliance |
    | 🚨 Alerts | Expiry & certification alerts |
    | 🎯 OCC Dashboard | Live operations overview |
    """)

st.divider()
st.caption("Airline Operations Control & Optimization Platform — Powered by Streamlit + NeonDB")