import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="Crew Profile", page_icon="🪪", layout="wide")
st.title("🪪 Crew Profile & Individual Roster")

engine = get_engine()
today = date.today()
last_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
last_month_end = today.replace(day=1) - timedelta(days=1)

# --- Crew Selector ---
with engine.connect() as conn:
    crew_df = pd.read_sql(text("""
        SELECT crew_id, name, role, fleet
        FROM crew WHERE is_active = TRUE
        ORDER BY fleet, role, crew_id
    """), conn)

crew_options = [
    f"{row['crew_id']} | {row['name']} | {row['role']} | {row['fleet']}"
    for _, row in crew_df.iterrows()
]

selected = st.selectbox("Select Crew Member", crew_options)
crew_id = selected.split(" | ")[0]

# --- Load Crew Record ---
with engine.connect() as conn:
    crew = pd.read_sql(text("""
        SELECT * FROM crew WHERE crew_id = :cid
    """), conn, params={"cid": crew_id}).iloc[0]

# --- Profile Header ---
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(f"### {crew['name']}")
    st.markdown(f"**ID:** {crew['crew_id']}")
    st.markdown(f"**Role:** {crew['role']}")
    st.markdown(f"**Fleet:** {crew['fleet']}")
    st.markdown(f"**Base:** {crew['base']}")
    st.markdown(f"**Phone:** {crew['phone']}")

with col2:
    st.markdown("### 📋 Certifications")
    expiry_fields = {
        'Medical': crew['medical_exp'],
        'SEP': crew['sep_exp'],
        'CRM': crew['crm_exp'],
        'DG': crew['dg_exp'],
        'ATPL': crew['atpl_exp'],
        'Type Rating': crew['type_rating_exp'],
        'LPC/OPC': crew['lpc_opc_exp'],
        'Line Check': crew['line_check_exp'],
        'Contract': crew['contract_expiry'],
    }
    for label, exp in expiry_fields.items():
        if exp:
            exp_date = pd.to_datetime(exp).date()
            days = (exp_date - today).days
            if days < 0:
                icon = "🔴"
            elif days <= 7:
                icon = "🟡"
            else:
                icon = "✅"
            st.markdown(f"{icon} **{label}:** {exp_date.strftime('%d/%m/%y')} &nbsp; `{days}d`")

with col3:
    st.markdown("### 📊 28 Day Summary")
    with engine.connect() as conn:
        stats = pd.read_sql(text("""
            SELECT 
                COUNT(*) as total_duties,
                COALESCE(SUM(fdp_hours),0) as total_fdp,
                COUNT(CASE WHEN status='DISRUPTED' THEN 1 END) as disrupted
            FROM roster
            WHERE crew_id = :cid
            AND duty_date BETWEEN :sd AND :ed
        """), conn, params={
            "cid": crew_id,
            "sd": today,
            "ed": today + timedelta(days=27)
        }).iloc[0]

    st.metric("Total Duties", int(stats['total_duties']))
    st.metric("Total FDP Hours", f"{float(stats['total_fdp']):.1f} hrs")
    st.metric("Disrupted", int(stats['disrupted']))

# =====================
# TABS
# =====================
st.divider()
tab1, tab2, tab3, tab4 = st.tabs(["📅 28 Day Roster", "📖 Flight Logbook", "🏖️ Leave Management", "📝 Update Certifications"])

# --- TAB 1: 28 Day Roster ---
with tab1:
    st.subheader(f"📅 28 Day Roster — {crew['name']}")

    with engine.connect() as conn:
        roster_df = pd.read_sql(text("""
            SELECT
                r.duty_date,
                SPLIT_PART(f.callsign,'-',1)||'-'||SPLIT_PART(f.callsign,'-',2) AS callsign,
                f.aircraft,
                f.origin,
                f.destination,
                TO_CHAR(f.dep_time,'HH24MI') AS dep,
                TO_CHAR(f.arr_time,'HH24MI') AS arr,
                TO_CHAR(r.report_time,'HH24MI') AS report,
                TO_CHAR(r.debrief_time,'HH24MI') AS debrief,
                r.fdp_hours,
                r.status,
                r.override_flag
            FROM roster r
            JOIN flights f ON r.flight_id = f.flight_id
            WHERE r.crew_id = :cid
            AND r.duty_date BETWEEN :sd AND :ed
            ORDER BY r.duty_date, f.dep_time
        """), conn, params={
            "cid": crew_id,
            "sd": today,
            "ed": today + timedelta(days=27)
        })

    if roster_df.empty:
        st.info("No roster assigned. Generate roster first.")
    else:
        roster_df['date'] = pd.to_datetime(roster_df['duty_date']).dt.strftime('%d/%m/%y-%a')
        display = roster_df[['date', 'aircraft', 'callsign', 'origin',
                              'destination', 'dep', 'arr', 'report',
                              'debrief', 'fdp_hours', 'status', 'override_flag']]
        display.columns = ['Date', 'Aircraft', 'Callsign', 'From', 'To',
                           'DEP', 'ARR', 'Report', 'Debrief',
                           'FDP Hrs', 'Status', 'Override']

        timestamp = datetime.now().strftime("%d%m%H%M")
        csv = display.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Download Individual Roster",
            data=csv,
            file_name=f"roster_{crew_id}_{timestamp}.csv",
            mime="text/csv"
        )

        def color_row(row):
            if row['Override'] == True:
                return ['background-color: #4a3000'] * len(row)
            elif row['Status'] == 'DISRUPTED':
                return ['background-color: #4a0000'] * len(row)
            return [''] * len(row)

        styled = display.style.apply(color_row, axis=1)
        st.dataframe(styled, use_container_width=True, height=500)

# --- TAB 2: Flight Logbook ---
with tab2:
    st.subheader(f"📖 Flight Logbook — {crew['name']}")

    log_col1, log_col2 = st.columns(2)
    with log_col1:
        log_start = st.date_input("From", value=last_month_start, format="DD/MM/YYYY")
    with log_col2:
        log_end = st.date_input("To", value=last_month_end, format="DD/MM/YYYY")

    with engine.connect() as conn:
        log_df = pd.read_sql(text("""
            SELECT
                r.duty_date,
                SPLIT_PART(f.callsign,'-',1)||'-'||SPLIT_PART(f.callsign,'-',2) AS callsign,
                f.aircraft,
                f.origin,
                f.destination,
                TO_CHAR(f.dep_time,'HH24MI') AS dep,
                TO_CHAR(f.arr_time,'HH24MI') AS arr,
                r.fdp_hours,
                r.status
            FROM roster r
            JOIN flights f ON r.flight_id = f.flight_id
            WHERE r.crew_id = :cid
            AND r.duty_date BETWEEN :sd AND :ed
            ORDER BY r.duty_date, f.dep_time
        """), conn, params={
            "cid": crew_id,
            "sd": log_start,
            "ed": log_end
        })

    if log_df.empty:
        st.info("No flights found for selected period.")
    else:
        log_df['date'] = pd.to_datetime(log_df['duty_date']).dt.strftime('%d/%m/%y-%a')
        display_log = log_df[['date', 'aircraft', 'callsign', 'origin',
                               'destination', 'dep', 'arr', 'fdp_hours', 'status']]
        display_log.columns = ['Date', 'Aircraft', 'Callsign', 'From',
                                'To', 'DEP', 'ARR', 'FDP Hrs', 'Status']

        # Logbook summary
        total_hrs = float(log_df['fdp_hours'].sum())
        total_sectors = len(log_df)
        st.markdown(f"""
        <div style="display:flex; gap:3rem; font-size:0.95rem; margin-bottom:1rem; font-weight:600;">
            <span>✈️ Total Sectors: <b>{total_sectors}</b></span>
            <span>⏱️ Total Hours: <b>{total_hrs:.1f} hrs</b></span>
            <span>📅 Period: <b>{log_start.strftime('%d/%m/%y')} → {log_end.strftime('%d/%m/%y')}</b></span>
        </div>
        """, unsafe_allow_html=True)

        timestamp = datetime.now().strftime("%d%m%H%M")
        csv = display_log.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Download Logbook",
            data=csv,
            file_name=f"logbook_{crew_id}_{timestamp}.csv",
            mime="text/csv"
        )

        st.dataframe(display_log, use_container_width=True, height=500)

# --- TAB 3: Leave Management ---
with tab3:
    st.subheader(f"🏖️ Leave Management — {crew['name']}")

    # Add leave table if not exists
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crew_leave (
                leave_id SERIAL PRIMARY KEY,
                crew_id VARCHAR(20) REFERENCES crew(crew_id),
                leave_type VARCHAR(20),
                start_date DATE,
                end_date DATE,
                notes TEXT,
                approved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()

    # Leave entry form
    st.markdown("#### ➕ Add Leave")
    lv_col1, lv_col2, lv_col3 = st.columns(3)
    with lv_col1:
        leave_type = st.selectbox("Leave Type", ["ANNUAL", "SICK", "TRAINING", "SIM", "OTHER"])
    with lv_col2:
        leave_start = st.date_input("From", value=today, format="DD/MM/YYYY", key="lv_start")
    with lv_col3:
        leave_end = st.date_input("To", value=today + timedelta(days=3), format="DD/MM/YYYY", key="lv_end")

    leave_notes = st.text_input("Notes (optional)")

    if st.button("➕ Add Leave Block"):
        if leave_end < leave_start:
            st.error("End date cannot be before start date.")
        else:
            days = (leave_end - leave_start).days + 1
            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO crew_leave (crew_id, leave_type, start_date, end_date, notes)
                    VALUES (:cid, :lt, :sd, :ed, :nt)
                """), {
                    'cid': crew_id,
                    'lt': leave_type,
                    'sd': leave_start,
                    'ed': leave_end,
                    'nt': leave_notes
                })
                conn.commit()
            st.success(f"✅ {leave_type} leave added — {days} days ({leave_start.strftime('%d/%m/%y')} to {leave_end.strftime('%d/%m/%y')})")
            st.rerun()

    # Display leave records
    st.markdown("#### 📋 Leave Records")
    with engine.connect() as conn:
        leave_df = pd.read_sql(text("""
            SELECT leave_id, leave_type, start_date, end_date,
                   (end_date - start_date + 1) AS days,
                   notes, approved, created_at
            FROM crew_leave
            WHERE crew_id = :cid
            ORDER BY start_date DESC
        """), conn, params={"cid": crew_id})

    if leave_df.empty:
        st.info("No leave records found.")
    else:
        leave_df['start_date'] = pd.to_datetime(leave_df['start_date']).dt.strftime('%d/%m/%y')
        leave_df['end_date'] = pd.to_datetime(leave_df['end_date']).dt.strftime('%d/%m/%y')
        leave_df.columns = ['ID', 'Type', 'From', 'To', 'Days', 'Notes', 'Approved', 'Created']

        total_leave = int(leave_df['Days'].sum())
        st.markdown(f"""
        <div style="display:flex; gap:3rem; font-size:0.95rem; margin-bottom:1rem; font-weight:600;">
            <span>🏖️ Total Leave Days: <b>{total_leave}</b></span>
        </div>
        """, unsafe_allow_html=True)

        timestamp = datetime.now().strftime("%d%m%H%M")
        csv = leave_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Download Leave Record",
            data=csv,
            file_name=f"leave_{crew_id}_{timestamp}.csv",
            mime="text/csv"
        )

        # Delete leave option
        del_col1, del_col2 = st.columns(2)
        with del_col1:
            del_options = [f"{row['ID']} | {row['Type']} | {row['From']} to {row['To']}"
                          for _, row in leave_df.iterrows()]
            del_selected = st.selectbox("Select to Delete", del_options)
        with del_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🗑️ Delete Leave"):
                del_id = int(del_selected.split(" | ")[0])
                with engine.connect() as conn:
                    conn.execute(text("DELETE FROM crew_leave WHERE leave_id = :lid"),
                                {'lid': del_id})
                    conn.commit()
                st.success("Leave record deleted.")
                st.rerun()

        st.dataframe(leave_df, use_container_width=True, height=300)

# .... tab 4     
with tab4:
    st.subheader(f"📝 Update Certifications — {crew['name']}")
    st.markdown("#### Enter new expiry dates")

    def safe_date(val):
        try:
            return pd.to_datetime(val).date()
        except:
            return today

    u1, u2, u3 = st.columns(3)
    with u1:
        new_medical  = st.date_input("Medical Expiry",    value=safe_date(crew['medical_exp']),    format="DD/MM/YYYY", key="u_med")
        new_sep      = st.date_input("SEP Expiry",        value=safe_date(crew['sep_exp']),        format="DD/MM/YYYY", key="u_sep")
        new_crm      = st.date_input("CRM Expiry",        value=safe_date(crew['crm_exp']),        format="DD/MM/YYYY", key="u_crm")

    with u2:
        new_dg       = st.date_input("DG Expiry",         value=safe_date(crew['dg_exp']),         format="DD/MM/YYYY", key="u_dg")
        new_atpl     = st.date_input("ATPL Expiry",       value=safe_date(crew['atpl_exp']),       format="DD/MM/YYYY", key="u_atpl")
        new_tr       = st.date_input("Type Rating Expiry",value=safe_date(crew['type_rating_exp']),format="DD/MM/YYYY", key="u_tr")

    with u3:
        new_lpc      = st.date_input("LPC/OPC Expiry",   value=safe_date(crew['lpc_opc_exp']),    format="DD/MM/YYYY", key="u_lpc")
        new_lc       = st.date_input("Line Check Expiry", value=safe_date(crew['line_check_exp']), format="DD/MM/YYYY", key="u_lc")
        new_contract = st.date_input("Contract Expiry",   value=safe_date(crew['contract_expiry']),format="DD/MM/YYYY", key="u_con")

    st.markdown("---")
    u4, u5 = st.columns(2)
    with u4:
        new_phone = st.text_input("Phone", value=str(crew['phone']))
    with u5:
        base_val = str(crew['base']).strip().upper()
        base_list = ["KHI", "ISB", "LHE"]
        new_base = st.selectbox("Base", base_list,
            index=base_list.index(base_val) if base_val in base_list else 0)

    if st.button("💾 Save Updates", type="primary"):
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE crew SET
                    medical_exp     = :med,
                    sep_exp         = :sep,
                    crm_exp         = :crm,
                    dg_exp          = :dg,
                    atpl_exp        = :atpl,
                    type_rating_exp = :tr,
                    lpc_opc_exp     = :lpc,
                    line_check_exp  = :lc,
                    contract_expiry = :con,
                    phone           = :phone,
                    base            = :base
                WHERE crew_id = :cid
            """), {
                'med': new_medical, 'sep': new_sep,
                'crm': new_crm,     'dg':  new_dg,
                'atpl': new_atpl,   'tr':  new_tr,
                'lpc': new_lpc,     'lc':  new_lc,
                'con': new_contract,'phone': new_phone,
                'base': new_base,   'cid': crew_id
            })
            conn.commit()
        st.success(f"✅ Certifications updated for {crew['name']}")
        st.rerun()