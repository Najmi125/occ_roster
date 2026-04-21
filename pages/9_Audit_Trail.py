import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(
    page_title="Audit Trail",
    page_icon="📜",
    layout="wide"
)
st.title("📜 OCC Audit Trail")

engine = get_engine()
today = date.today()

# =====================
# FILTERS
# =====================
col1, col2, col3 = st.columns(3)
with col1:
    date_range = st.date_input(
        "Date Range",
        value=(today - timedelta(days=28), today),
        format="DD/MM/YYYY"
    )
with col2:
    action_filter = st.selectbox("Action Type", [
        "ALL", "CREW_SWAP", "FLIGHT_CANCELLED",
        "FLIGHT_DELAYED", "FLIGHT_DIVERTED",
        "FLIGHT_ADDED", "CERT_UPDATE"
    ])
with col3:
    source_filter = st.selectbox(
        "Source", ["ALL", "Manual", "System"])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# =====================
# QUERY
# =====================
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
if source_filter == "Manual":
    query += " AND system_generated = FALSE"
elif source_filter == "System":
    query += " AND system_generated = TRUE"

query += " ORDER BY created_at DESC"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

# =====================
# SUMMARY
# =====================
total        = len(df)
crew_swaps   = len(df[df['action_type'] == 'CREW_SWAP'])
cancels      = len(df[df['action_type'] == 'FLIGHT_CANCELLED'])
delays       = len(df[df['action_type'] == 'FLIGHT_DELAYED'])
diversions   = len(df[df['action_type'] == 'FLIGHT_DIVERTED'])
additions    = len(df[df['action_type'] == 'FLIGHT_ADDED'])
cert_updates = len(df[df['action_type'] == 'CERT_UPDATE'])

summary = (
    f"📜 Total: **{total}** &nbsp;|&nbsp; "
    f"🔄 Swaps: **{crew_swaps}** &nbsp;|&nbsp; "
    f"❌ Cancels: **{cancels}** &nbsp;|&nbsp; "
    f"⏱️ Delays: **{delays}** &nbsp;|&nbsp; "
    f"🔀 Diversions: **{diversions}** &nbsp;|&nbsp; "
    f"➕ Added: **{additions}** &nbsp;|&nbsp; "
    f"📋 Cert Updates: **{cert_updates}**"
)
st.markdown(summary)
st.divider()

# =====================
# DISPLAY
# =====================
if df.empty:
    st.info("No audit records found for selected filters.")
else:
    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Audit Log",
        data=csv,
        file_name=f"audit_log_{timestamp}.csv",
        mime="text/csv"
    )

    display_df = df[[
        'timestamp', 'action_type', 'affected_flight',
        'old_crew_id', 'new_crew_id',
        'old_value', 'new_value',
        'remarks', 'system_generated'
    ]].copy()

    display_df.columns = [
        'Time', 'Action', 'Flight',
        'Old Crew', 'New Crew',
        'Old Value', 'New Value',
        'Remarks', 'System'
    ]

    action_colors = {
        'CREW_SWAP':        'background-color:#002244',
        'FLIGHT_CANCELLED': 'background-color:#4a0000',
        'FLIGHT_DELAYED':   'background-color:#4a2800; color:#FFD580',
        'FLIGHT_DIVERTED':  'background-color:#2a0044',
        'FLIGHT_ADDED':     'background-color:#004400',
        'CERT_UPDATE':      'background-color:#003333',
    }

    def color_action(row):
        color = action_colors.get(row['Action'], '')
        return [color] * len(row)

    st.dataframe(
        display_df.style.apply(color_action, axis=1),
        use_container_width=True,
        height=500
    )

    # =====================
    # DETAIL VIEW
    # =====================
    st.divider()
    st.subheader("🔍 Record Detail")

    audit_options = [
        f"{row['audit_id']} | {row['timestamp']} | "
        f"{row['action_type']} | "
        f"{row['affected_flight'] or row['old_crew_id'] or ''}"
        for _, row in df.iterrows()
    ]

    selected_audit = st.selectbox("Select Record", audit_options)
    audit_id = int(selected_audit.split(" | ")[0])
    record = df[df['audit_id'] == audit_id].iloc[0]

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Action Details**")
        st.write({
            'Action':  record['action_type'],
            'Flight':  record['affected_flight'] or 'N/A',
            'Time':    record['timestamp'],
            'Source':  'System' if record['system_generated']
                       else 'Manual'
        })
    with col2:
        st.markdown("**Crew and Values**")
        st.write({
            'Old Crew':  record['old_crew_id'] or 'N/A',
            'New Crew':  record['new_crew_id'] or 'N/A',
            'Old Value': record['old_value'] or 'N/A',
            'New Value': record['new_value'] or 'N/A',
        })

    st.markdown("**Remarks**")
    st.info(record['remarks'] or 'No remarks entered')