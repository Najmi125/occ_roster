import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="Alerts & Expiry", page_icon="🚨", layout="wide")
st.title("🚨 Alerts & Expiry Monitor")

engine = get_engine()
today = date.today()
alert_window = today + timedelta(days=7)

# --- Auto Generate Alerts ---
def generate_alerts():
    with engine.connect() as conn:
        crew_df = pd.read_sql(text("""
            SELECT crew_id, name, role, fleet,
                   medical_exp, sep_exp, crm_exp, dg_exp,
                   atpl_exp, type_rating_exp, lpc_opc_exp,
                   line_check_exp, contract_expiry
            FROM crew WHERE is_active = TRUE
        """), conn)

        # Clear existing unacknowledged alerts
        conn.execute(text("DELETE FROM alerts WHERE acknowledged = FALSE"))

        alert_fields = {
            'medical_exp':      'Medical',
            'sep_exp':          'SEP',
            'crm_exp':          'CRM',
            'dg_exp':           'DG',
            'atpl_exp':         'ATPL',
            'type_rating_exp':  'Type Rating',
            'lpc_opc_exp':      'LPC/OPC',
            'line_check_exp':   'Line Check',
            'contract_expiry':  'Contract',
        }

        for _, crew in crew_df.iterrows():
            for field, label in alert_fields.items():
                exp = crew[field]
                if exp:
                    exp_date = pd.to_datetime(exp).date()
                    days = (exp_date - today).days

                    if days < 0:
                        severity = 'RED'
                        msg = f"{label} EXPIRED {abs(days)} days ago"
                    elif days <= 7:
                        severity = 'YELLOW'
                        msg = f"{label} expires {exp_date.strftime('%d %b').upper()} ({days}d)"
                    else:
                        continue

                    conn.execute(text("""
                        INSERT INTO alerts
                        (crew_id, alert_type, alert_message,
                         expiry_date, days_remaining, severity)
                        VALUES (:cid, :at, :am, :ed, :dr, :sv)
                    """), {
                        'cid': crew['crew_id'],
                        'at':  label,
                        'am':  msg,
                        'ed':  exp_date,
                        'dr':  days,
                        'sv':  severity
                    })
        conn.commit()

# --- Refresh Button ---
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("🔄 Refresh Alerts", type="primary"):
        generate_alerts()
        st.success("✅ Alerts refreshed")
        st.rerun()

# Auto run on load
generate_alerts()

# --- Filters ---
col1, col2, col3 = st.columns(3)
with col1:
    severity_filter = st.selectbox("Severity", ["ALL", "RED", "YELLOW"])
with col2:
    fleet_filter = st.selectbox("Fleet", ["ALL", "A320", "A330"])
with col3:
    type_filter = st.selectbox("Type", ["ALL", "Medical", "SEP", "CRM", "DG",
                                         "ATPL", "Type Rating", "LPC/OPC",
                                         "Line Check", "Contract"])

# --- Load Alerts ---
alert_query = """
    SELECT
        a.alert_id,
        a.crew_id,
        c.name,
        c.role,
        c.fleet,
        a.alert_type,
        a.alert_message,
        a.expiry_date,
        a.days_remaining,
        a.severity,
        a.acknowledged
    FROM alerts a
    JOIN crew c ON a.crew_id = c.crew_id
    WHERE 1=1
"""
alert_params = {}

if severity_filter != "ALL":
    alert_query += " AND a.severity = :sev"
    alert_params["sev"] = severity_filter
if fleet_filter != "ALL":
    alert_query += " AND c.fleet = :fleet"
    alert_params["fleet"] = fleet_filter
if type_filter != "ALL":
    alert_query += " AND a.alert_type = :atype"
    alert_params["atype"] = type_filter

alert_query += " ORDER BY a.days_remaining ASC, a.severity DESC"

with engine.connect() as conn:
    alerts_df = pd.read_sql(text(alert_query), conn, params=alert_params)

# --- Summary ---
total_alerts = len(alerts_df)
red_count    = len(alerts_df[alerts_df['severity'] == 'RED'])
yellow_count = len(alerts_df[alerts_df['severity'] == 'YELLOW'])
ack_count    = len(alerts_df[alerts_df['acknowledged'] == True])

st.markdown(f"""
<div style="display:flex; gap:3rem; font-size:0.95rem;
margin-bottom:1rem; font-weight:600;">
    <span>🚨 Total Alerts: <b>{total_alerts}</b></span>
    <span>🔴 Expired: <b>{red_count}</b></span>
    <span>🟡 Expiring Soon: <b>{yellow_count}</b></span>
    <span>✅ Acknowledged: <b>{ack_count}</b></span>
</div>
""", unsafe_allow_html=True)

if alerts_df.empty:
    st.success("✅ No active alerts — all crew certifications valid.")
else:
    # --- Download ---
    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = alerts_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Alerts",
        data=csv,
        file_name=f"alerts_{timestamp}.csv",
        mime="text/csv"
    )

    # Format display
    display_df = alerts_df.copy()
    display_df['expiry_date'] = pd.to_datetime(
        display_df['expiry_date']).dt.strftime('%d/%m/%y')

    display_df['severity'] = display_df['severity'].apply(
        lambda x: '🔴 EXPIRED' if x == 'RED' else '🟡 EXPIRING')

    display_df = display_df[[
        'crew_id', 'name', 'role', 'fleet',
        'alert_type', 'alert_message',
        'expiry_date', 'days_remaining',
        'severity', 'acknowledged'
    ]]
    display_df.columns = [
        'ID', 'Name', 'Role', 'Fleet',
        'Type', 'Message',
        'Expiry', 'Days Left',
        'Severity', 'Acknowledged'
    ]

    def color_alert(row):
        if '🔴' in str(row['Severity']):
            return ['background-color: #4a0000'] * len(row)
        elif '🟡' in str(row['Severity']):
            return ['background-color: #4a3000'] * len(row)
        return [''] * len(row)

    styled = display_df.style.apply(color_alert, axis=1)
    st.dataframe(styled, use_container_width=True, height=500)

    # --- Acknowledge Alert ---
    st.divider()
    st.subheader("✅ Acknowledge Alert")

    unack_df = alerts_df[alerts_df['acknowledged'] == False]
    if not unack_df.empty:
        ack_options = [
            f"{row['alert_id']} | {row['name']} | {row['alert_type']} | {row['alert_message']}"
            for _, row in unack_df.iterrows()
        ]
        selected_alert = st.selectbox("Select Alert", ack_options)
        if st.button("✅ Acknowledge"):
            alert_id = int(selected_alert.split(" | ")[0])
            with engine.connect() as conn:
                conn.execute(text("""
                    UPDATE alerts SET acknowledged = TRUE
                    WHERE alert_id = :aid
                """), {'aid': alert_id})
                conn.commit()
            st.success("Alert acknowledged.")
            st.rerun()
    else:
        st.info("All alerts acknowledged.")