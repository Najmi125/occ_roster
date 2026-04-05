import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db import get_engine
from sqlalchemy import text
import pandas as pd
from datetime import date, datetime

def update_crew_positions():
    """
    After each roster cycle, track where each crew member
    physically ends up based on their last completed flight.
    """
    engine = get_engine()
    today = date.today()

    with engine.connect() as conn:
        # Get last flight for each crew member up to today
        last_flights = pd.read_sql(text("""
            SELECT DISTINCT ON (r.crew_id)
                r.crew_id,
                f.destination as current_city,
                f.flight_id as last_flight_id,
                f.arr_time as last_arr
            FROM roster r
            JOIN flights f ON r.flight_id = f.flight_id
            WHERE r.duty_date <= :today
            AND r.status NOT IN ('CANCELLED', 'DISRUPTED')
            ORDER BY r.crew_id, f.arr_time DESC
        """), conn, params={"today": today})

        for _, row in last_flights.iterrows():
            conn.execute(text("""
                UPDATE crew_position
                SET current_city = :city,
                    last_flight_id = :fid,
                    last_updated = NOW()
                WHERE crew_id = :cid
            """), {
                'city': row['current_city'],
                'fid': row['last_flight_id'],
                'cid': row['crew_id']
            })

        conn.commit()
    return True


def get_crew_at_city(city, fleet_type=None, role=None):
    """
    Returns crew physically present at a given city.
    Filters by fleet and role if provided.
    Also checks FTL compliance and doc validity.
    """
    engine = get_engine()
    today = date.today()

    query = """
        SELECT
            c.crew_id,
            c.name,
            c.role,
            c.fleet,
            cp.current_city,
            -- Hours flown in last 28 days
            COALESCE(SUM(r.fdp_hours), 0) as hours_28day,
            -- Last duty end time
            MAX(r.debrief_time) as last_debrief,
            -- Standby flag
            COALESCE(sp.is_active, FALSE) as is_standby,
            -- Doc validity check
            CASE WHEN
                c.medical_exp > :today AND
                c.sep_exp > :today AND
                c.type_rating_exp > :today AND
                c.lpc_opc_exp > :today
            THEN TRUE ELSE FALSE END as docs_valid
        FROM crew c
        JOIN crew_position cp ON c.crew_id = cp.crew_id
        LEFT JOIN roster r ON c.crew_id = r.crew_id
            AND r.duty_date BETWEEN :month_ago AND :today
        LEFT JOIN standby_pool sp ON c.crew_id = sp.crew_id
            AND sp.standby_date = :today
            AND sp.is_active = TRUE
        LEFT JOIN crew_leave cl ON c.crew_id = cl.crew_id
            AND :today BETWEEN cl.start_date AND cl.end_date
        WHERE cp.current_city = :city
        AND c.is_active = TRUE
        AND cl.leave_id IS NULL
    """
    params = {
        "city": city,
        "today": today,
        "month_ago": date(today.year, today.month - 1 if today.month > 1 else 12, today.day)
    }

    if fleet_type:
        query += " AND c.fleet = :fleet"
        params["fleet"] = fleet_type
    if role:
        query += " AND c.role = :role"
        params["role"] = role

    query += """
        GROUP BY c.crew_id, c.name, c.role, c.fleet,
                 cp.current_city, sp.is_active,
                 c.medical_exp, c.sep_exp,
                 c.type_rating_exp, c.lpc_opc_exp
        HAVING
            -- FTL: max 190 hours in 28 days
            COALESCE(SUM(r.fdp_hours), 0) < 180
        ORDER BY
            COALESCE(sp.is_active, FALSE) DESC,  -- standby first
            COALESCE(SUM(r.fdp_hours), 0) ASC,   -- least hours
            MAX(r.debrief_time) ASC NULLS FIRST   -- most rested
    """

    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn, params=params)

    return df


def score_crew_candidate(crew_row, fdp_hours):
    """
    Score a crew candidate for assignment.
    Higher = better fit.
    """
    score = 0

    # Standby bonus
    if crew_row.get('is_standby'):
        score += 20

    # Hours remaining (less hours = more available)
    hours_remaining = 190 - float(crew_row.get('hours_28day', 0))
    score += min(hours_remaining / 10, 10)

    # Rest score
    last_debrief = crew_row.get('last_debrief')
    if last_debrief is None:
        score += 10  # fully rested
    else:
        rest_hrs = (datetime.now() - pd.to_datetime(last_debrief)).total_seconds() / 3600
        if rest_hrs >= 12:
            score += min(rest_hrs / 3, 10)

    # Docs valid bonus
    if crew_row.get('docs_valid'):
        score += 5

    return round(score, 2)


def get_best_crew_options(city, fleet_type, fdp_hours, dep_time):
    """
    Returns top 2 CPT and top 2 FO candidates
    scored and ranked for a given flight.
    """
    from datetime import timedelta
    dep_dt = pd.to_datetime(dep_time)

    candidates = get_crew_at_city(city, fleet_type)

    if candidates.empty:
        return [], []

    # Filter: minimum 12hr rest before dep
    def has_min_rest(row):
        if pd.isnull(row['last_debrief']):
            return True
        rest = (dep_dt - pd.to_datetime(row['last_debrief'])).total_seconds() / 3600
        return rest >= 12

    candidates = candidates[candidates.apply(has_min_rest, axis=1)]
    candidates['score'] = candidates.apply(
        lambda row: score_crew_candidate(row.to_dict(), fdp_hours), axis=1
    )
    candidates = candidates.sort_values('score', ascending=False)

    cpts = candidates[candidates['role'] == 'CPT'].head(2).to_dict('records')
    fos  = candidates[candidates['role'] == 'FO'].head(2).to_dict('records')

    return cpts, fos


def log_audit(action_type, affected_flight=None, old_crew=None,
              new_crew=None, old_value=None, new_value=None,
              remarks=None, system_generated=False):
    """Log every OCC override action."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO override_audit
            (action_type, affected_flight, old_crew_id, new_crew_id,
             old_value, new_value, remarks, system_generated)
            VALUES (:at, :af, :oc, :nc, :ov, :nv, :rm, :sg)
        """), {
            'at': action_type,
            'af': affected_flight,
            'oc': old_crew,
            'nc': new_crew,
            'ov': str(old_value) if old_value else None,
            'nv': str(new_value) if new_value else None,
            'rm': remarks,
            'sg': system_generated
        })
        conn.commit()


def get_available_aircraft(origin, dep_time, arr_time, fleet_type=None):
    """
    Find aircraft available at origin for a given time window.
    Checks turnaround time: 45min A320, 60min A330.
    """
    engine = get_engine()
    dep_dt = pd.to_datetime(dep_time)

    query = """
        SELECT DISTINCT
            f.aircraft,
            MAX(f.arr_time) as last_arr,
            f.destination as last_dest
        FROM flights f
        WHERE f.arr_time < :dep
        AND f.destination = :origin
        AND f.status NOT IN ('FLIGHT CANCELLED', 'AIRCRAFT AOG')
        GROUP BY f.aircraft, f.destination
    """
    params = {"dep": dep_dt, "origin": origin}
    if fleet_type:
        query += " AND f.aircraft LIKE :fleet"
        params["fleet"] = f"%{fleet_type}%"

    with engine.connect() as conn:
        aircraft_df = pd.read_sql(text(query), conn, params=params)

    available = []
    for _, row in aircraft_df.iterrows():
        ac = row['aircraft']
        turnaround = 60 if 'A330' in ac else 45
        last_arr = pd.to_datetime(row['last_arr'])
        gap = (dep_dt - last_arr).total_seconds() / 60
        if gap >= turnaround:
            available.append({
                'aircraft': ac,
                'last_arr': last_arr.strftime('%H:%M'),
                'gap_mins': int(gap),
                'fleet_type': 'A330' if 'A330' in ac else 'A320'
            })

    return available


def compute_disruption_score(aircraft, cpt, fo, existing_roster_count):
    """
    Score a complete flight option (aircraft + crew).
    Lower disruption = higher score.
    """
    score = 0

    # Crew scores
    if cpt:
        score += cpt.get('score', 0)
    if fo:
        score += fo.get('score', 0)

    # Aircraft gap bonus (more gap = less rushed)
    gap = aircraft.get('gap_mins', 0)
    score += min(gap / 30, 5)

    return round(score, 2)