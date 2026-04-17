import streamlit as st
from utils.db import test_connection, get_engine
from datetime import datetime, date, timedelta
import pandas as pd
from sqlalchemy import text

st.set_page_config(
    page_title="OCC Optimization Platform",
    page_icon="✈️",
    layout="wide"
)

# ---------------- HEADER ----------------
st.title("✈️ Airline Operations Control Platform")

st.markdown("""
### XYZ Airline — Demo Operations Model

A configurable **Operations Control Center (OCC) platform** designed to simulate real-world airline operations.  
This demo represents a mid-size airline environment and can be **customized to match any airline’s fleet, network, and regulatory framework**.

---

### Intelligent Crew & Flight Operations Management

Optimize crew scheduling, manage disruptions, and ensure full regulatory compliance — in real time.
""")

st.markdown(f"🕒 **{datetime.now().strftime('%A, %d %B %Y — %H:%M')} UTC**")

st.divider()

# ---------------- CORE VALUE PROPOSITION ----------------
c1, c2, c3 = st.columns(3)

c1.markdown("""
### ✅ Legally Compliant  
Every roster is validated against FDTL regulations with continuous compliance monitoring.
""")

c2.markdown("""
### ⚡ Override-Controlled  
Human-in-the-loop decision making with OCC override capabilities — without compromising legality.
""")

c3.markdown("""
### 📊 Audit-Traceable  
All operational decisions are logged, traceable, and fully explainable for audit and review.
""")

st.divider()

# ---------------- CONFIGURABILITY ----------------
st.subheader("🧩 Configurable Airline Model")

st.markdown("""
The **XYZ Airline Demo Model** can be adapted to different airline environments:

- Fleet types and aircraft configurations  
- Route network and scheduling patterns  
- Crew structures and base locations  
- Regulatory frameworks (PCAA / EASA / FAA)  
- Airline-specific operational policies  

This enables a seamless transition from **demo system to production-ready OCC platform**.
""")

st.info("Designed for startup airlines, regional carriers, and OCC transformation initiatives.")

st.divider()

# ---------------- SYSTEM STATUS ----------------
db_status = test_connection()
if db_status == True:
    st.success("🟢 System Status: Database Connected")
else:
    st.error(f"🔴 System Error: {db_status}")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# ---------------- METRICS ----------------
try:
    with engine.connect() as conn:
        total_crew = pd.read_sql(
            text("SELECT COUNT(*) as cnt FROM crew WHERE is_active=TRUE"), conn
        ).iloc[0]['cnt']

        total_flights = pd.read_sql(
            text("SELECT COUNT(*) as cnt FROM flights WHERE flight_date BETWEEN :sd AND :ed"),
            conn,
            params={"sd": today, "ed": end_date}
        ).iloc[0]['cnt']

        total_roster = pd.read_sql(
            text("SELECT COUNT(*) as cnt FROM roster WHERE duty_date BETWEEN :sd AND :ed"),
            conn,
            params={"sd": today, "ed": end_date}
        ).iloc[0]['cnt']

        disruptions = pd.read_sql(
            text("SELECT COUNT(*) as cnt FROM disruptions WHERE resolved=FALSE"),
            conn
        ).iloc[0]['cnt']

        red_alerts = pd.read_sql(
            text("SELECT COUNT(*) as cnt FROM alerts WHERE severity='RED' AND acknowledged=FALSE"),
            conn
        ).iloc[0]['cnt']

except Exception as e:
    st.warning("Metrics unavailable — database not fully initialized")
    total_crew = total_flights = total_roster = disruptions = red_alerts = 0

m1, m2, m3, m4, m5 = st.columns(5)

m1.metric("👨‍✈️ Crew", int(total_crew))
m2.metric("✈️ Flights (28d)", int(total_flights))
m3.metric("📋 Duties", int(total_roster))
m4.metric("⚡ Disruptions", int(disruptions))
m5.metric("🔴 Critical Alerts", int(red_alerts))

st.divider()

# ---------------- OPERATIONAL FLOW ----------------
st.subheader("🧭 Operational Workflow")

st.markdown("""
**1. Generate Roster → 2. Validate Compliance → 3. Detect Disruptions →  
4. Apply OCC Overrides → 5. Re-optimize → 6. Audit & Review**

This platform ensures every operational decision is:

- **Legally compliant**
- **Operationally feasible**
- **Fully traceable**
""")

st.divider()

# ---------------- FOOTER ----------------
st.caption("OCC Optimization Platform — Demo System for Airline Operations | Built with Streamlit")
