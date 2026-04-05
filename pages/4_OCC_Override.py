import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime, time
from utils.crew_position import (
    update_crew_positions, get_best_crew_options,
    get_available_aircraft, compute_disruption_score,
    log_audit
)

st.set_page_config(page_title="OCC Override Engine", page_icon="⚡", layout="wide")
st.title("⚡ OCC Override Engine")

engine = get_engine()
today = date.today()
end_date = today + timedelta(days=27)

# Refresh positions on load
update_crew_positions()

# =====================
# HELPER FUNCTIONS
# =====================
def get_flights_dropdown(date_from=None, date_to=None):
    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT
                flight_id,
                SPLIT_PART(callsign,'-',1)||'-'||SPLIT_PART(callsign,'-',2) AS callsign,
                aircraft, origin, destination,
                TO_CHAR(dep_time,'HH24MI') AS dep,
                TO_CHAR(arr_time,'HH24MI') AS arr,
                flight_date, status,
                dep_time, arr_time
            FROM flights
            WHERE flight_date BETWEEN :sd AND :ed
            AND status NOT IN ('FLIGHT CANCELLED','AIRCRAFT AOG')
            ORDER BY flight_date, dep_time
        """), conn, params={
            "sd": date_from or today,
            "ed": date_to or end_date
        })
    return df

def get_flight_crew(flight_id):
    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT
                r.roster_id, r.crew_id, c.name, c.role,
                r.status, r.remarks, r.fdp_hours
            FROM roster r
            JOIN crew c ON r.crew_id = c.crew_id
            WHERE r.flight_id = :fid
        """), conn, params={"fid": flight_id})
    return df

def format_flight_option(row):
    return (f"{pd.to_datetime(row['flight_date']).strftime('%d/%m/%y')} | "
            f"{row['callsign']} | {row['aircraft']} | "
            f"{row['origin']}-{row['destination']} | "
            f"DEP {row['dep']} ARR {row['arr']}")

# =====================
# TABS
# =====================
tab1, tab2, tab3, tab4 = st.tabs([
    "👨‍✈️ Crew Override",
    "✈️ Flight Override",
    "➕ Add Flight",
    "🟢 Standby Pool"
])

# ====================
# TAB 1: CREW OVERRIDE
# ====================
with tab1:
    st.subheader("👨‍✈️ Crew Override — Force Swap / Remove")

    flights_df = get_flights_dropdown()
    if flights_df.empty:
        st.warning("No flights found. Generate roster first.")
    else:
        flight_options = [format_flight_option(row) for _, row in flights_df.iterrows()]
        selected_flt = st.selectbox("Select Flight", flight_options, key="co_flt")
        flt_idx = flight_options.index(selected_flt)
        flt_row = flights_df.iloc[flt_idx]
        flight_id = int(flt_row['flight_id'])
        fleet_type = 'A330' if 'A330' in flt_row['aircraft'] else 'A320'
        origin = flt_row['origin']
        dep_time = flt_row['dep_time']
        fdp_hrs = (pd.to_datetime(flt_row['arr_time']) -
                   pd.to_datetime(flt_row['dep_time'])).total_seconds() / 3600

        # Show current crew
        st.markdown("#### Current Crew Assigned")
        current_crew = get_flight_crew(flight_id)
        if current_crew.empty:
            st.info("No crew assigned to this flight.")
        else:
            st.dataframe(current_crew[['crew_id','name','role','status','fdp_hours']],
                        use_container_width=True)

        st.divider()
        st.markdown("#### 🔄 Replace Crew Member")

        col1, col2 = st.columns(2)
        with col1:
            if not current_crew.empty:
                remove_options = [
                    f"{row['crew_id']} | {row['name']} | {row['role']}"
                    for _, row in current_crew.iterrows()
                ]
                remove_selected = st.selectbox("Select Crew to Remove", remove_options)
                remove_crew_id = remove_selected.split(" | ")[0]
                remove_role = remove_selected.split(" | ")[2]
            else:
                st.info("No crew to remove.")
                remove_crew_id = None
                remove_role = None

        with col2:
            remarks = st.text_area("Reason / Remarks", placeholder="Sick, No Show, FTL breach...", key="co_rem")

        # Find replacements
        if remove_crew_id and remove_role:
            st.markdown("#### 🤖 Available Replacements at Origin — Ranked by Priority")

            cpts, fos = get_best_crew_options(origin, fleet_type, fdp_hrs, dep_time)
            candidates = cpts if remove_role == 'CPT' else fos

            # Filter out already assigned crew
            assigned_ids = current_crew['crew_id'].tolist()
            candidates = [c for c in candidates if c['crew_id'] not in assigned_ids]

            if not candidates:
                st.error(f"❌ No legal replacements available at {origin} for this flight.")
                st.info("Consider: repositioning crew or checking standby pool.")
            else:
                # Show candidates in modal style
                st.markdown("""
                <style>
                .option-box {
                    border: 1px solid #444;
                    border-radius: 10px;
                    padding: 1rem;
                    margin: 0.5rem 0;
                    background: #1a1a2e;
                }
                .option-a { border-color: #00cc44; }
                .option-b { border-color: #0088ff; }
                </style>
                """, unsafe_allow_html=True)

                replace_options = []
                for i, c in enumerate(candidates[:2]):
                    label = "🥇 BEST OPTION" if i == 0 else "🥈 OPTION 2"
                    color = "option-a" if i == 0 else "option-b"
                    standby = "✅ Designated Standby" if c.get('is_standby') else "Regular Crew"
                    hrs = float(c.get('hours_28day', 0))
                    score = c.get('score', 0)

                    st.markdown(f"""
                    <div class="option-box {color}">
                        <b>{label}</b><br>
                        👤 {c['name']} ({c['crew_id']}) — {c['role']}<br>
                        📍 Currently at: {c['current_city']}<br>
                        {standby}<br>
                        ⏱️ 28-Day Hours: {hrs:.1f} hrs<br>
                        📋 Docs Valid: {'✅' if c.get('docs_valid') else '🔴'}<br>
                        🎯 Priority Score: {score}
                    </div>
                    """, unsafe_allow_html=True)
                    replace_options.append(f"{c['crew_id']} | {c['name']} | Score: {score}")

                selected_replacement = st.selectbox("Confirm Replacement", replace_options)
                new_crew_id = selected_replacement.split(" | ")[0]

                if st.button("✅ Confirm Crew Swap", type="primary", key="co_confirm"):
                    if not remarks:
                        st.error("Please enter a reason/remarks.")
                    else:
                        with engine.connect() as conn:
                            # Remove old crew
                            conn.execute(text("""
                                UPDATE roster SET
                                    status = 'REMOVED',
                                    override_flag = TRUE,
                                    remarks = :rem
                                WHERE flight_id = :fid AND crew_id = :cid
                            """), {'rem': remarks, 'fid': flight_id, 'cid': remove_crew_id})

                            # Get flight details for new roster entry
                            flt_detail = pd.read_sql(text("""
                                SELECT dep_time, arr_time, flight_date
                                FROM flights WHERE flight_id = :fid
                            """), conn, params={"fid": flight_id}).iloc[0]

                            dep_dt = pd.to_datetime(flt_detail['dep_time'])
                            arr_dt = pd.to_datetime(flt_detail['arr_time'])
                            report_t = dep_dt - timedelta(minutes=60)
                            debrief_t = arr_dt + timedelta(minutes=30)
                            fdp = (arr_dt - dep_dt).total_seconds() / 3600

                            # Add new crew
                            conn.execute(text("""
                                INSERT INTO roster
                                (crew_id, flight_id, duty_date, report_time,
                                 debrief_time, fdp_hours, status, override_flag,
                                 replaced_crew_id, remarks)
                                VALUES (:cid, :fid, :dd, :rt, :dt, :fdp,
                                        'ASSIGNED', TRUE, :old_cid, :rem)
                            """), {
                                'cid': new_crew_id,
                                'fid': flight_id,
                                'dd': flt_detail['flight_date'],
                                'rt': report_t,
                                'dt': debrief_t,
                                'fdp': round(fdp, 2),
                                'old_cid': remove_crew_id,
                                'rem': remarks
                            })

                            # Update position of old crew — stays at current city
                            # Update position of new crew — will move to destination
                            conn.execute(text("""
                                UPDATE crew_position
                                SET current_city = :dest, last_updated = NOW()
                                WHERE crew_id = :cid
                            """), {'dest': flt_row['destination'], 'cid': new_crew_id})

                            conn.commit()

                        # Audit log
                        log_audit(
                            action_type='CREW_SWAP',
                            affected_flight=flt_row['callsign'],
                            old_crew=remove_crew_id,
                            new_crew=new_crew_id,
                            old_value=f"Removed: {remove_crew_id}",
                            new_value=f"Assigned: {new_crew_id}",
                            remarks=remarks
                        )

                        st.success(f"✅ Crew swapped — {remove_crew_id} → {new_crew_id}")
                        st.rerun()

# =======================
# TAB 2: FLIGHT OVERRIDE
# =======================
with tab2:
    st.subheader("✈️ Flight Override — Cancel / Delay / Divert")

    sub1, sub2, sub3 = st.tabs(["❌ Cancel", "⏱️ Delay", "🔀 Divert"])

    # --- CANCEL ---
    with sub1:
        st.markdown("#### ❌ Cancel Flight")
        flights_df2 = get_flights_dropdown()
        flt_opts2 = [format_flight_option(row) for _, row in flights_df2.iterrows()]
        sel_cancel = st.selectbox("Select Flight to Cancel", flt_opts2, key="cancel_flt")
        cancel_idx = flt_opts2.index(sel_cancel)
        cancel_row = flights_df2.iloc[cancel_idx]
        cancel_reason = st.text_area("Cancellation Reason", key="cancel_rem")

        if st.button("❌ Cancel Flight", type="primary", key="cancel_btn"):
            if not cancel_reason:
                st.error("Please enter cancellation reason.")
            else:
                fid = int(cancel_row['flight_id'])
                with engine.connect() as conn:
                    # Cancel flight
                    conn.execute(text("""
                        UPDATE flights SET
                            status = 'FLIGHT CANCELLED',
                            cancel_reason = :reason
                        WHERE flight_id = :fid
                    """), {'reason': cancel_reason, 'fid': fid})

                    # Mark crew as REST
                    conn.execute(text("""
                        UPDATE roster SET
                            status = 'REST',
                            override_flag = TRUE,
                            remarks = :reason
                        WHERE flight_id = :fid
                    """), {'reason': f"Flight cancelled: {cancel_reason}", 'fid': fid})

                    conn.commit()

                log_audit(
                    action_type='FLIGHT_CANCELLED',
                    affected_flight=cancel_row['callsign'],
                    old_value='SCHEDULED',
                    new_value='CANCELLED',
                    remarks=cancel_reason
                )
                st.success(f"✅ {cancel_row['callsign']} cancelled — crew marked REST")
                st.rerun()

    # --- DELAY ---
    with sub2:
        st.markdown("#### ⏱️ Delay Flight")
        flights_df3 = get_flights_dropdown()
        flt_opts3 = [format_flight_option(row) for _, row in flights_df3.iterrows()]
        sel_delay = st.selectbox("Select Flight to Delay", flt_opts3, key="delay_flt")
        delay_idx = flt_opts3.index(sel_delay)
        delay_row = flights_df3.iloc[delay_idx]

        col1, col2 = st.columns(2)
        with col1:
            delay_mins = st.number_input("Delay (minutes)", min_value=1,
                                          max_value=600, value=60, step=15)
        with col2:
            delay_reason = st.text_area("Delay Reason", key="delay_rem")

        # Calculate new times
        orig_dep = pd.to_datetime(delay_row['dep_time'])
        orig_arr = pd.to_datetime(delay_row['arr_time'])
        new_dep = orig_dep + timedelta(minutes=int(delay_mins))
        new_arr = orig_arr + timedelta(minutes=int(delay_mins))
        new_fdp = (new_arr - new_dep).total_seconds() / 3600

        fleet_type_d = 'A330' if 'A330' in delay_row['aircraft'] else 'A320'
        max_fdp = 14 if fleet_type_d == 'A330' else 13

        st.markdown(f"""
        <div style="background:#1a1a2e; padding:1rem; border-radius:8px; margin:1rem 0;">
            <b>Original DEP:</b> {orig_dep.strftime('%H:%M')} →
            <b>New DEP:</b> {new_dep.strftime('%H:%M')}<br>
            <b>Original ARR:</b> {orig_arr.strftime('%H:%M')} →
            <b>New ARR:</b> {new_arr.strftime('%H:%M')}<br>
            <b>FDP:</b> {new_fdp:.1f} hrs |
            <b>Max Allowed:</b> {max_fdp} hrs |
            {'🔴 FTL BREACH — Crew swap required' if new_fdp > max_fdp else '✅ FTL OK'}
        </div>
        """, unsafe_allow_html=True)

        # FTL breach — suggest crew swap
        if new_fdp > max_fdp:
            st.warning("⚠️ FTL breach detected — finding legal replacement crew...")
            origin_d = delay_row['origin']
            cpts_d, fos_d = get_best_crew_options(
                origin_d, fleet_type_d, new_fdp, new_dep)

            for label, candidates in [("👨‍✈️ CPT Options", cpts_d),
                                        ("🧑‍✈️ FO Options", fos_d)]:
                st.markdown(f"**{label}**")
                for i, c in enumerate(candidates[:2]):
                    opt = "🥇 BEST" if i == 0 else "🥈 OPTION 2"
                    st.markdown(f"""
                    <div style="border:1px solid #444; border-radius:8px;
                    padding:0.8rem; margin:0.3rem 0; background:#1a2a1a;">
                        {opt} — {c['name']} ({c['crew_id']}) |
                        📍 {c['current_city']} |
                        ⏱️ {float(c.get('hours_28day',0)):.1f}h |
                        🎯 Score: {c.get('score',0)}
                    </div>
                    """, unsafe_allow_html=True)

        if st.button("⏱️ Apply Delay", type="primary", key="delay_btn"):
            if not delay_reason:
                st.error("Please enter delay reason.")
            else:
                fid = int(delay_row['flight_id'])
                with engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE flights SET
                            status = 'DELAYED',
                            delay_minutes = :dm,
                            original_dep = dep_time,
                            original_arr = arr_time,
                            dep_time = :ndep,
                            arr_time = :narr
                        WHERE flight_id = :fid
                    """), {
                        'dm': delay_mins, 'ndep': new_dep,
                        'narr': new_arr, 'fid': fid
                    })

                    # Update roster report/debrief times
                    conn.execute(text("""
                        UPDATE roster SET
                            report_time = :rt,
                            debrief_time = :dt,
                            fdp_hours = :fdp,
                            remarks = :rem
                        WHERE flight_id = :fid
                    """), {
                        'rt': new_dep - timedelta(minutes=60),
                        'dt': new_arr + timedelta(minutes=30),
                        'fdp': round(new_fdp, 2),
                        'rem': f"Delayed {delay_mins}min: {delay_reason}",
                        'fid': fid
                    })
                    conn.commit()

                log_audit(
                    action_type='FLIGHT_DELAYED',
                    affected_flight=delay_row['callsign'],
                    old_value=f"DEP {orig_dep.strftime('%H:%M')}",
                    new_value=f"DEP {new_dep.strftime('%H:%M')} (+{delay_mins}min)",
                    remarks=delay_reason
                )
                st.success(f"✅ {delay_row['callsign']} delayed by {delay_mins} mins")
                st.rerun()

    # --- DIVERT ---
    with sub3:
        st.markdown("#### 🔀 Divert Flight")
        flights_df4 = get_flights_dropdown()
        flt_opts4 = [format_flight_option(row) for _, row in flights_df4.iterrows()]
        sel_divert = st.selectbox("Select Flight to Divert", flt_opts4, key="div_flt")
        div_idx = flt_opts4.index(sel_divert)
        div_row = flights_df4.iloc[div_idx]

        col1, col2 = st.columns(2)
        with col1:
            divert_dest = st.selectbox("Divert To",
                ["KHI", "ISB", "LHE", "DXB", "JED", "MCT", "KUL", "DOH"])
        with col2:
            divert_reason = st.text_area("Divert Reason", key="div_rem")

        if st.button("🤖 Assess Diversion Options", type="primary", key="div_assess"):
            fleet_type_v = 'A330' if 'A330' in div_row['aircraft'] else 'A320'
            orig_dest = div_row['destination']
            origin_v = div_row['origin']

            # Option A: Continue to original dest next day
            # Option B: Return to KHI
            dep_v = pd.to_datetime(div_row['dep_time'])
            arr_v = pd.to_datetime(div_row['arr_time'])

            cpts_v, fos_v = get_best_crew_options(
                divert_dest, fleet_type_v,
                (arr_v - dep_v).total_seconds() / 3600, dep_v)

            st.markdown("---")
            col_a, col_b = st.columns(2)

            with col_a:
                st.markdown("""
                <div style="border:2px solid #00cc44; border-radius:10px;
                padding:1rem; background:#001a00;">
                    <h4>🥇 Option A — Continue to Original Dest</h4>
                """, unsafe_allow_html=True)
                st.markdown(f"**Divert to:** {divert_dest}")
                st.markdown(f"**Recovery:** Reposition to {orig_dest} next available slot")
                if cpts_v:
                    st.markdown(f"**Best CPT:** {cpts_v[0]['name']} ({cpts_v[0]['crew_id']})")
                if fos_v:
                    st.markdown(f"**Best FO:** {fos_v[0]['name']} ({fos_v[0]['crew_id']})")
                st.markdown("**Disruption:** Minimal — one sector delay")
                st.markdown("</div>", unsafe_allow_html=True)

            with col_b:
                st.markdown("""
                <div style="border:2px solid #0088ff; border-radius:10px;
                padding:1rem; background:#00001a;">
                    <h4>🥈 Option B — Return to Base</h4>
                """, unsafe_allow_html=True)
                st.markdown(f"**Divert to:** {divert_dest}")
                st.markdown(f"**Recovery:** Return to KHI, reschedule from base")
                if cpts_v and len(cpts_v) > 1:
                    st.markdown(f"**Best CPT:** {cpts_v[1]['name']} ({cpts_v[1]['crew_id']})")
                if fos_v and len(fos_v) > 1:
                    st.markdown(f"**Best FO:** {fos_v[1]['name']} ({fos_v[1]['crew_id']})")
                st.markdown("**Disruption:** Moderate — full rotation reset")
                st.markdown("</div>", unsafe_allow_html=True)

            st.session_state['divert_ready'] = True
            st.session_state['divert_fid'] = int(div_row['flight_id'])
            st.session_state['divert_dest'] = divert_dest
            st.session_state['divert_callsign'] = div_row['callsign']
            st.session_state['divert_reason'] = divert_reason

        if st.session_state.get('divert_ready'):
            st.markdown("---")
            confirm_opt = st.radio("Select Option to Apply",
                ["Option A — Continue to Original", "Option B — Return to Base"])

            if st.button("✅ Confirm Diversion", key="div_confirm"):
                fid = st.session_state['divert_fid']
                with engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE flights SET
                            status = 'DIVERTED',
                            divert_dest = :dd,
                            cancel_reason = :reason
                        WHERE flight_id = :fid
                    """), {
                        'dd': st.session_state['divert_dest'],
                        'reason': st.session_state['divert_reason'],
                        'fid': fid
                    })
                    conn.commit()

                log_audit(
                    action_type='FLIGHT_DIVERTED',
                    affected_flight=st.session_state['divert_callsign'],
                    old_value=div_row['destination'],
                    new_value=st.session_state['divert_dest'],
                    remarks=f"{confirm_opt} | {st.session_state['divert_reason']}"
                )
                st.success(f"✅ Diversion confirmed — {st.session_state['divert_callsign']} → {st.session_state['divert_dest']}")
                st.session_state['divert_ready'] = False
                st.rerun()

# ====================
# TAB 3: ADD FLIGHT
# ====================
with tab3:
    st.subheader("➕ Add New Flight")

    col1, col2, col3 = st.columns(3)
    with col1:
        new_origin = st.selectbox("Origin", ["KHI","ISB","LHE","DXB","JED"])
        new_dest   = st.selectbox("Destination", ["ISB","LHE","DXB","JED","KHI"])
    with col2:
        new_date   = st.date_input("Start Date", value=today, format="DD/MM/YYYY")
        new_dep    = st.time_input("Departure Time (UTC)", value=time(6, 0))
    with col3:
        new_arr    = st.time_input("Arrival Time (UTC)", value=time(8, 0))
        frequency  = st.selectbox("Frequency", [
            "One-Off", "Daily (28 days)", "Weekly"])

    if frequency == "Weekly":
        weekdays = st.multiselect("Select Days",
            ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
            default=["Monday","Wednesday","Friday"])

    fleet_pref = st.selectbox("Preferred Fleet", ["ANY", "A320", "A330"])

    if st.button("🤖 Assess Best Options", type="primary", key="addflt_assess"):
        dep_dt = datetime.combine(new_date, new_dep)
        arr_dt = datetime.combine(new_date, new_arr)
        if arr_dt <= dep_dt:
            arr_dt += timedelta(days=1)
        fdp_new = (arr_dt - dep_dt).total_seconds() / 3600

        # Find available aircraft
        fleet_filter = None if fleet_pref == "ANY" else fleet_pref
        available_ac = get_available_aircraft(
            new_origin, dep_dt, arr_dt, fleet_filter)

        # Get crew options
        cpts_new, fos_new = get_best_crew_options(
            new_origin, fleet_filter or 'A320', fdp_new, dep_dt)

        if not available_ac:
            st.error(f"❌ No aircraft available at {new_origin} for this time slot.")
            st.info("Consider adjusting departure time or check aircraft positions.")
        elif not cpts_new or not fos_new:
            st.error(f"❌ No legal crew available at {new_origin}.")
        else:
            # Score combinations
            options = []
            for ac in available_ac[:2]:
                for cpt in cpts_new[:2]:
                    for fo in fos_new[:2]:
                        score = compute_disruption_score(ac, cpt, fo, 0)
                        options.append({
                            'aircraft': ac,
                            'cpt': cpt,
                            'fo': fo,
                            'score': score,
                            'fdp': fdp_new
                        })

            options = sorted(options, key=lambda x: x['score'], reverse=True)

            st.markdown("---")
            st.markdown("### 🤖 AI Recommended Options")
            col_a, col_b = st.columns(2)

            for i, (col, opt) in enumerate(zip([col_a, col_b], options[:2])):
                label = "🥇 BEST OPTION" if i == 0 else "🥈 OPTION 2"
                color = "#001a00" if i == 0 else "#00001a"
                border = "#00cc44" if i == 0 else "#0088ff"
                with col:
                    st.markdown(f"""
                    <div style="border:2px solid {border}; border-radius:10px;
                    padding:1rem; background:{color};">
                        <h4>{label}</h4>
                        ✈️ <b>Aircraft:</b> {opt['aircraft']['aircraft']}<br>
                        🕐 <b>Avail after:</b> {opt['aircraft']['last_arr']} 
                        ({opt['aircraft']['gap_mins']} min gap)<br>
                        👨‍✈️ <b>CPT:</b> {opt['cpt']['name']} ({opt['cpt']['crew_id']})<br>
                        📍 At: {opt['cpt']['current_city']} |
                        ⏱️ {float(opt['cpt'].get('hours_28day',0)):.1f}h<br>
                        🧑‍✈️ <b>FO:</b> {opt['fo']['name']} ({opt['fo']['crew_id']})<br>
                        📍 At: {opt['fo']['current_city']} |
                        ⏱️ {float(opt['fo'].get('hours_28day',0)):.1f}h<br>
                        ⏱️ <b>FDP:</b> {opt['fdp']:.1f} hrs<br>
                        🎯 <b>Score:</b> {opt['score']}
                    </div>
                    """, unsafe_allow_html=True)

            # Store options in session
            st.session_state['add_flt_options'] = options[:2]
            st.session_state['add_flt_params'] = {
                'origin': new_origin, 'dest': new_dest,
                'dep_dt': dep_dt, 'arr_dt': arr_dt,
                'fdp': fdp_new, 'frequency': frequency,
                'start_date': new_date,
                'weekdays': weekdays if frequency == "Weekly" else []
            }

    # Confirm add flight
    if st.session_state.get('add_flt_options'):
        st.markdown("---")
        opt_labels = ["🥇 Best Option", "🥈 Option 2"]
        chosen = st.radio("Select Option to Add", opt_labels)
        chosen_idx = opt_labels.index(chosen)
        chosen_opt = st.session_state['add_flt_options'][chosen_idx]
        params = st.session_state['add_flt_params']

        if st.button("✅ Confirm Add Flight", type="primary", key="addflt_confirm"):
            # Generate dates based on frequency
            dates_to_add = []
            freq = params['frequency']
            start = params['start_date']

            if freq == "One-Off":
                dates_to_add = [start]
            elif freq == "Daily (28 days)":
                dates_to_add = [start + timedelta(days=i) for i in range(28)]
            elif freq == "Weekly":
                day_map = {"Monday":0,"Tuesday":1,"Wednesday":2,
                           "Thursday":3,"Friday":4,"Saturday":5,"Sunday":6}
                sel_days = [day_map[d] for d in params['weekdays']]
                for i in range(28):
                    d = start + timedelta(days=i)
                    if d.weekday() in sel_days:
                        dates_to_add.append(d)

            with engine.connect() as conn:
                for d in dates_to_add:
                    dep = datetime.combine(d, params['dep_dt'].time())
                    arr = datetime.combine(d, params['arr_dt'].time())
                    if arr <= dep:
                        arr += timedelta(days=1)

                    callsign = f"XYZ-OCC-{d.strftime('%d%m')}"

                    # Insert flight
                    result = conn.execute(text("""
                        INSERT INTO flights
                        (aircraft, callsign, origin, destination,
                         dep_time, arr_time, flight_date, status)
                        VALUES (:ac, :cs, :org, :dst, :dep, :arr, :fd, 'SCHEDULED')
                        ON CONFLICT (callsign) DO NOTHING
                        RETURNING flight_id
                    """), {
                        'ac': chosen_opt['aircraft']['aircraft'],
                        'cs': callsign,
                        'org': params['origin'],
                        'dst': params['dest'],
                        'dep': dep, 'arr': arr, 'fd': d
                    })
                    row = result.fetchone()
                    if row:
                        flt_id = row[0]
                        fdp = (arr - dep).total_seconds() / 3600

                        # Assign CPT
                        conn.execute(text("""
                            INSERT INTO roster
                            (crew_id, flight_id, duty_date, report_time,
                             debrief_time, fdp_hours, status, override_flag, remarks)
                            VALUES (:cid, :fid, :dd, :rt, :dt, :fdp,
                                    'ASSIGNED', TRUE, 'OCC Added Flight')
                        """), {
                            'cid': chosen_opt['cpt']['crew_id'],
                            'fid': flt_id, 'dd': d,
                            'rt': dep - timedelta(minutes=60),
                            'dt': arr + timedelta(minutes=30),
                            'fdp': round(fdp, 2)
                        })

                        # Assign FO
                        conn.execute(text("""
                            INSERT INTO roster
                            (crew_id, flight_id, duty_date, report_time,
                             debrief_time, fdp_hours, status, override_flag, remarks)
                            VALUES (:cid, :fid, :dd, :rt, :dt, :fdp,
                                    'ASSIGNED', TRUE, 'OCC Added Flight')
                        """), {
                            'cid': chosen_opt['fo']['crew_id'],
                            'fid': flt_id, 'dd': d,
                            'rt': dep - timedelta(minutes=60),
                            'dt': arr + timedelta(minutes=30),
                            'fdp': round(fdp, 2)
                        })

                conn.commit()

            log_audit(
                action_type='FLIGHT_ADDED',
                affected_flight=f"{params['origin']}-{params['dest']}",
                new_crew=f"{chosen_opt['cpt']['crew_id']}+{chosen_opt['fo']['crew_id']}",
                new_value=f"{len(dates_to_add)} flights added",
                remarks=f"OCC Added | {freq} | {chosen_opt['aircraft']['aircraft']}"
            )

            st.success(f"✅ {len(dates_to_add)} flight(s) added to schedule and roster updated!")
            st.session_state['add_flt_options'] = None
            st.rerun()

# ====================
# TAB 4: STANDBY POOL
# ====================
with tab4:
    st.subheader("🟢 Standby Pool — Today")

    col1, col2 = st.columns(2)
    with col1:
        sb_fleet = st.selectbox("Fleet", ["ALL","A320","A330"], key="sb_fleet")
    with col2:
        sb_city = st.selectbox("City", ["ALL","KHI","ISB","LHE","DXB","JED"], key="sb_city")

    # Add to standby
    st.markdown("#### ➕ Add to Standby")
    sb_col1, sb_col2, sb_col3 = st.columns(3)
    with sb_col1:
        with engine.connect() as conn:
            all_crew = pd.read_sql(text("""
                SELECT crew_id, name, role, fleet
                FROM crew WHERE is_active=TRUE
                ORDER BY fleet, role, crew_id
            """), conn)
        sb_crew_opts = [f"{r['crew_id']} | {r['name']} | {r['role']} | {r['fleet']}"
                       for _, r in all_crew.iterrows()]
        sb_selected = st.selectbox("Select Crew", sb_crew_opts, key="sb_crew")
    with sb_col2:
        sb_start = st.time_input("Standby Start", value=time(6,0), key="sb_start")
    with sb_col3:
        sb_end = st.time_input("Standby End", value=time(18,0), key="sb_end")

    if st.button("➕ Add to Standby Pool", key="sb_add"):
        sb_cid = sb_selected.split(" | ")[0]
        sb_fleet_val = sb_selected.split(" | ")[3]
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO standby_pool
                (crew_id, standby_date, city, fleet, start_time, end_time)
                VALUES (:cid, :sd, 'KHI', :fleet, :st, :et)
            """), {
                'cid': sb_cid, 'sd': today,
                'fleet': sb_fleet_val,
                'st': sb_start, 'et': sb_end
            })
            conn.commit()
        st.success(f"✅ Added to standby pool")
        st.rerun()

    st.divider()

    # Display standby pool
    sb_query = """
        SELECT
            sp.crew_id, c.name, c.role, c.fleet,
            cp.current_city,
            sp.start_time, sp.end_time,
            COALESCE(SUM(r.fdp_hours),0) as hours_28day
        FROM standby_pool sp
        JOIN crew c ON sp.crew_id = c.crew_id
        JOIN crew_position cp ON sp.crew_id = cp.crew_id
        LEFT JOIN roster r ON sp.crew_id = r.crew_id
            AND r.duty_date BETWEEN :month_ago AND :today
        WHERE sp.standby_date = :today AND sp.is_active = TRUE
        GROUP BY sp.crew_id, c.name, c.role, c.fleet,
                 cp.current_city, sp.start_time, sp.end_time
    """
    sb_params = {
        "today": today,
        "month_ago": today - timedelta(days=28)
    }
    if sb_fleet != "ALL":
        sb_query += " AND c.fleet = :fleet"
        sb_params["fleet"] = sb_fleet
    if sb_city != "ALL":
        sb_query += " AND cp.current_city = :city"
        sb_params["city"] = sb_city

    with engine.connect() as conn:
        sb_df = pd.read_sql(text(sb_query), conn, params=sb_params)

    if sb_df.empty:
        st.info("No crew in standby pool today. Add crew above.")
    else:
        sb_df['hours_28day'] = sb_df['hours_28day'].apply(lambda x: round(float(x),1))
        sb_df.columns = ['ID','Name','Role','Fleet','City',
                         'SBY Start','SBY End','28D Hrs']

        def color_sb(row):
            return ['background-color: #002200'] * len(row)

        styled = sb_df.style.apply(color_sb, axis=1)
        st.dataframe(styled, use_container_width=True, height=400)

        # Download
        timestamp = datetime.now().strftime("%d%m%H%M")
        csv = sb_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Download Standby List",
            data=csv,
            file_name=f"standby_{timestamp}.csv",
            mime="text/csv"
        )