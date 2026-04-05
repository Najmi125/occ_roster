import streamlit as st
import pandas as pd
from utils.db import get_engine
from sqlalchemy import text
from datetime import date, timedelta, datetime

st.set_page_config(page_title="Flight Schedule", page_icon="🛫", layout="wide")
st.title("🛫 Flight Schedule — 28 Day Rolling")

engine = get_engine()

# --- Filters ---
today = date.today()
end_date = today + timedelta(days=27)

col1, col2 = st.columns(2)
with col1:
    date_range = st.date_input(
        "Select Date Range",
        value=(today, end_date),
        min_value=today,
        max_value=end_date,
        format="DD/MM/YYYY"
    )
with col2:
    fleet_filter = st.selectbox("Fleet", ["ALL", "A320-1", "A320-2", "A320-3", "A330-1", "A330-2"])

# Handle single date vs range
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d = end_d = date_range

# --- Query ---
query = """
    SELECT 
        aircraft,
        SPLIT_PART(callsign, '-', 1) || '-' || SPLIT_PART(callsign, '-', 2) AS callsign,
        origin,
        destination,
        TO_CHAR(dep_time, 'HH24MI') AS dep,
        TO_CHAR(arr_time, 'HH24MI') AS arr,
        flight_date
    FROM flights
    WHERE flight_date BETWEEN :sd AND :ed
"""
params = {"sd": start_d, "ed": end_d}

if fleet_filter != "ALL":
    query += " AND aircraft = :fleet"
    params["fleet"] = fleet_filter

query += " ORDER BY flight_date, aircraft, dep_time"

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn, params=params)

# --- Add formatted date column ---
if not df.empty:
    df['date'] = pd.to_datetime(df['flight_date']).dt.strftime('%d/%m/%y-%a')
    display_df = df[['date', 'aircraft', 'callsign', 'origin', 'destination', 'dep', 'arr']]

    st.markdown(f"**{len(display_df)} flights** from {start_d.strftime('%d/%m/%Y')} to {end_d.strftime('%d/%m/%Y')}")

    timestamp = datetime.now().strftime("%d%m%H%M")
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download FLT SKD",
        data=csv,
        file_name=f"flt_skd_{timestamp}.csv",
        mime="text/csv"
    )

    st.dataframe(display_df, use_container_width=True, height=600)

else:
    st.warning("No flights found for selected filters.")