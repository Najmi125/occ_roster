import streamlit as st
from utils.db import test_connection, get_engine
from datetime import datetime, date, timedelta
import pandas as pd
from sqlalchemy import text

# ================= CONFIG =================
st.set_page_config(
    page_title="OCC Optimization Platform",
    page_icon="✈️",
    layout="wide"
)

# ================= HEADER =================
st.title("✈️ Airline Operations Control Platform")

st.markdown("""
### This is a Demo Operations Model  
**XYZ Airline — KHI Base |2xA330 + 3xA320 | 17 Daily Flights | 50 Pilots**
""")

# ---------------- STATUS + TIME ----------------
db_status = test_connection()
status_text = "🟢 NeonDB Connected" if db_status == True else f"🔴 DB Error: {db_status}"
current_time = datetime.now().strftime('%A, %d %B %Y — %H:%M UTC')

st.markdown(f"**{status_text} &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; 🕒 {current_time}**")

st.markdown("---")
# ================= TAGLINE =================
st.markdown("### AI-powered Planning & Scheduling Management")
st.markdown("---")
# ================= WHAT DOES IT DO =================
st.markdown("### <u><b>What does it do?</b></u>", unsafe_allow_html=True)

st.markdown("""
- **Intelligent Crew Rostering** — 28-day rolling schedules. Fair duty distribution 
- **Compliance Engine** — Enforces PCAA FDTL regulations. Tracks crew qualifications expiry.  
- **OCC Override Control** — Real-time disruption handling. Airline OCC can enforce any flt/crew changes.  
- **Re-Optimization Engine** — Regenerates roster after human inserted changes in less than 1 minute  
- **Audit & Traceability** — Full operational transparency and record keeping.
""")

# ================= AI VALUE =================
st.markdown("""
AI-driven OCC systems reduce human error risks by replacing time consuming,fatigue-prone decisions with consistent, constraint-driven automation.
""")

# ================= FLEXIBILITY =================
st.markdown("""
👉 **Fully flexible and customizable to match any airline’s fleet, routes, regulatory framework, and operational layout.**
""")
st.markdown("---")
# ================= FOOTER =================
st.markdown("""
<h3 style="color:#D98E00;">
Navigate through sidebar, make changes and evaluate if system output is satisfactory.
</h3>
""", unsafe_allow_html=True)