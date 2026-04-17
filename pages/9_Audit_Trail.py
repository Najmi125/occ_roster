import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="Audit Trail", page_icon="📜", layout="wide")
st.title("📜 OCC Audit Trail")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# --- Filters ---
col1, col2, col3 = st.columns(3)
with col1:
    date_range = st.date_input(
        "Date Range",
        value=(today - timedelta(days=7), today),
        format="DD/MM/YYYY"
    )
with col2:
    action_filter = st.selectbox("Action Type", [
        "ALL", "CREW_SWAP", "FLIGHT_CANCELLED",
        "FLIGHT_DELAYED", "FLIGHT_DIVERTED", "FLIGHT_ADDED"
    ])
with col3:
    system_filter = st.selectbox("Source", ["ALL", "Manual", "System"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# --- Query ---
query = """
    SELECT
        audit_id,
        action_type,
        affected_flight,
        old_crew_id,
        new_crew_id,
        old_value,
        new_value,
        remarks,
        system_generated,
        TO_CHAR(created_at, 'DD/MM/YY HH24:MI') AS timestamp
    FROM override_audit
    WHERE DATE(created_at) BETWEEN :sd AND :ed
"""
params = {"sd": start_d, "ed": end_d}

if action_filter != "ALL":
    query += " AND action_type = :at"
    params["at"] = action_filter
if system_filter == "Manual":
    query += " AND system_generated = FALSE"
elif system_filter == "System":
    query += " AND system_generated = TRUE"

query += " ORDER BY created_at DESC"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

# --- Summary ---
total = len(df)
crew_swaps = len(df[df['action_type'] == 'CREW_SWAP'])
cancellations = len(df[df['action_type'] == 'FLIGHT_CANCELLED'])
delays = len(df[df['action_type'] == 'FLIGHT_DELAYED'])
diversions = len(df[df['action_type'] == 'FLIGHT_DIVERTED'])
additions = len(df[df['action_type'] == 'FLIGHT_ADDED'])

st.markdown(f"""
<div style="display:flex; gap:2rem; font-size:0.9rem;
margin-bottom:1rem; font-weight:600; flex-wrap:wrap;">
    <span>📜 Total Actions: <b>{total}</b></span>
    <span>🔄 Crew Swaps: <b>{crew_swaps}</b></span>
    <span>❌ Cancellations: <b>{cancellations}</b></span>
    <span>⏱️ Delays: <b>{delays}</b></span>
    <span>🔀 Diversions: <b>{diversions}</b></span>
    <span>➕ Additions: <b>{additions}</b></span>
</div>
""", unsafe_allow_html=True)

st.divider()

if df.empty:
    st.info("No audit records found for selected filters.")
else:
    # --- Download ---
    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Audit Log",
        data=csv,
        file_name=f"audit_log_{timestamp}.csv",
        mime="text/csv"
    )

    # --- Color by action type ---
    def color_action(row):
        colors = {
            'CREW_SWAP':         'background-color: #eef3fb',  # light blue
            'FLIGHT_CANCELLED':  'background-color: #fbeeee',  # light red
            'FLIGHT_DELAYED':    'background-color: #f6f5f2',  # light brown
            'FLIGHT_DIVERTED':   'background-color: #f3eefb',  # light purple
            'FLIGHT_ADDED':      'background-color: #eefbea',  # light green
        }
        color = colors.get(row['Action'], '')
        return [color] * len(row)

    # These lines should remain at the same level as 'def' (inside the else block)
    display_df = df[[
        'timestamp', 'action_type', 'affected_flight',
        'old_crew_id', 'new_crew_id', 'old_value',
        'new_value', 'remarks', 'system_generated'
    ]].copy()
"""
    # --- Color by action type ---
    def color_action(row):
    colors = {
        'CREW_SWAP':         'background-color: #eef3fb',  # light blue
        'FLIGHT_CANCELLED':  'background-color: #fbeeee',  # light red
        'FLIGHT_DELAYED':    'background-color: #f6f5f2',  # light brown
        'FLIGHT_DIVERTED':   'background-color: #f3eefb',  # light purple
        'FLIGHT_ADDED':      'background-color: #eefbea',  # light green
    }
    color = colors.get(row['Action'], '')
    return [color] * len(row)

    display_df = df[[
        'timestamp', 'action_type', 'affected_flight',
        'old_crew_id', 'new_crew_id', 'old_value',
        'new_value', 'remarks', 'system_generated'
    ]].copy()
"""
    display_df.columns = [
        'Time', 'Action', 'Flight',
        'Old Crew', 'New Crew', 'Old Value',
        'New Value', 'Remarks', 'System'
    ]

    styled = display_df.style.apply(color_action, axis=1)
    st.dataframe(styled, use_container_width=True, height=600)

    # --- Detail Expander ---
    st.divider()
    st.subheader("🔍 Action Detail")

    if not df.empty:
        audit_options = [
            f"{row['audit_id']} | {row['timestamp']} | {row['action_type']} | {row['affected_flight'] or ''}"
            for _, row in df.iterrows()
        ]
        selected_audit = st.selectbox("Select Record", audit_options)
        audit_id = int(selected_audit.split(" | ")[0])
        record = df[df['audit_id'] == audit_id].iloc[0]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"""
            <div style="border:1px solid #444; border-radius:10px;
            padding:1rem; background:#1a1a1a;">
                <b>Action:</b> {record['action_type']}<br>
                <b>Flight:</b> {record['affected_flight'] or 'N/A'}<br>
                <b>Time:</b> {record['timestamp']}<br>
                <b>Source:</b> {'🤖 System' if record['system_generated'] else '👤 Manual'}
            </div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
            <div style="border:1px solid #444; border-radius:10px;
            padding:1rem; background:#1a1a1a;">
                <b>Old Crew:</b> {record['old_crew_id'] or 'N/A'}<br>
                <b>New Crew:</b> {record['new_crew_id'] or 'N/A'}<br>
                <b>Old Value:</b> {record['old_value'] or 'N/A'}<br>
                <b>New Value:</b> {record['new_value'] or 'N/A'}
            </div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
        <div style="border:1px solid #555; border-radius:10px;
        padding:1rem; background:#1a1a1a; margin-top:1rem;">
            <b>Remarks:</b> {record['remarks'] or 'No remarks entered'}
        </div>
        """, unsafe_allow_html=True)
