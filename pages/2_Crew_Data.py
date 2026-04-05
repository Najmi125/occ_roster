import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, datetime, timedelta

st.set_page_config(page_title="Crew Data", page_icon="👨‍✈️", layout="wide")
st.title("👨‍✈️ Crew Data & Certifications")

engine = get_engine()
today = date.today()
alert_window = today + timedelta(days=7)

# --- Filters ---
col1, col2 = st.columns(2)
with col1:
    fleet_filter = st.selectbox("Fleet", ["ALL", "A320", "A330"])
with col2:
    role_filter = st.selectbox("Role", ["ALL", "CPT", "FO"])

# --- Query ---
query = """
    SELECT crew_id, name, role, fleet, phone, base,
           contract_expiry, medical_exp, sep_exp, crm_exp,
           dg_exp, atpl_exp, type_rating_exp, lpc_opc_exp, line_check_exp
    FROM crew WHERE is_active = TRUE
"""
params = {}
if fleet_filter != "ALL":
    query += " AND fleet = :fleet"
    params["fleet"] = fleet_filter
if role_filter != "ALL":
    query += " AND role = :role"
    params["role"] = role_filter

query += " ORDER BY fleet, role, crew_id"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

if df.empty:
    st.warning("No crew found.")
else:
    # --- Expiry columns ---
    expiry_cols = ['contract_expiry', 'medical_exp', 'sep_exp', 'crm_exp',
                   'dg_exp', 'atpl_exp', 'type_rating_exp', 'lpc_opc_exp', 'line_check_exp']

    # Convert to datetime
    for col in expiry_cols:
        df[col] = pd.to_datetime(df[col]).dt.date

    # --- Alert dots function ---
    def make_alerts(row):
        alerts = []
        labels = {
            'medical_exp': 'Med',
            'sep_exp': 'SEP',
            'crm_exp': 'CRM',
            'dg_exp': 'DG',
            'atpl_exp': 'ATPL',
            'type_rating_exp': 'TR',
            'lpc_opc_exp': 'LPC',
            'line_check_exp': 'LC',
            'contract_expiry': 'CON'
        }
        for col, label in labels.items():
            exp = row[col]
            if exp:
                days = (exp - today).days
                if days < 0:
                    alerts.append(f"🔴 {label} EXP")
                elif days <= 7:
                    alerts.append(f"🟡 {label} {exp.strftime('%d%b').upper()}")
        return ' '.join(alerts) if alerts else '✅'

    df['alerts'] = df.apply(make_alerts, axis=1)

    # --- Format dates ---
    for col in expiry_cols:
        df[col] = df[col].apply(lambda x: x.strftime('%d/%m/%y') if x else '')

    # --- Display columns ---
    display_df = df[['crew_id', 'name', 'role', 'fleet', 'phone',
                     'medical_exp', 'sep_exp', 'crm_exp', 'dg_exp',
                     'atpl_exp', 'type_rating_exp', 'lpc_opc_exp',
                     'line_check_exp', 'contract_expiry', 'alerts']]

    display_df.columns = ['ID', 'Name', 'Role', 'Fleet', 'Phone',
                          'Medical', 'SEP', 'CRM', 'DG',
                          'ATPL', 'Type Rating', 'LPC/OPC',
                          'Line Check', 'Contract', 'Alerts']

    # --- Summary metrics ---
    total = len(df)
    alerts_count = len(df[df['alerts'] != '✅'])
    expired = len(df[df['alerts'].str.contains('🔴', na=False)])
    warning = len(df[df['alerts'].str.contains('🟡', na=False)])

st.markdown(f"""
    <div style="display:flex; gap:2rem; font-size:0.75rem; margin-bottom:1rem;">
        <span>👥 Total Crew: <b>{total}</b></span>
        <span>🟡 Expiring Soon: <b>{warning}</b></span>
        <span>🔴 Expired: <b>{expired}</b></span>
        <span>✅ All Valid: <b>{total - alerts_count}</b></span>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- Download ---
timestamp = datetime.now().strftime("%d%m%H%M")
csv = display_df.to_csv(index=False).encode('utf-8')
st.download_button(
        label="⬇️ Download Crew Data",
        data=csv,
        file_name=f"crew_data_{timestamp}.csv",
        mime="text/csv"
    )

    # --- Color alerts ---
def color_alerts(val):
        if '🔴' in str(val):
            return 'background-color: #5c0000; color: white'
        elif '🟡' in str(val):
            return 'background-color: #5c4400; color: white'
        return ''

styled = display_df.style.map(color_alerts, subset=['Alerts'])
st.dataframe(styled, use_container_width=True, height=600)