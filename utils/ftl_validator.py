import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db import get_engine
from sqlalchemy import text
import pandas as pd
from datetime import date, timedelta, datetime

# ============================================================
# CAA PAKISTAN FTL RULES — SINGLE SOURCE OF TRUTH
# ============================================================
CAA_RULES = {
    'max_fdp': {
        'A320': 13.0,
        'A330': 14.0,
    },
    'min_rest_hours':   12.0,
    'max_7day_hours':   60.0,
    'max_28day_hours':  190.0,
    'max_annual_hours': 900.0,
    'report_before_dep': 60,   # minutes
    'debrief_after_arr': 30,   # minutes
    'max_sectors_per_day': {
        'A320': 6,
        'A330': 4,
    },
    'min_days_off_per_7days': 1,
}

# ============================================================
# CORE VALIDATOR
# ============================================================
def validate_roster(start_d=None, end_d=None, crew_id=None):
    """
    Full post-generation FTL validation.
    Runs ALL CAA Pakistan rules against roster in DB.
    Returns: dict with violations, warnings, summary
    Can validate full roster or single crew member.
    """
    engine = get_engine()
    today  = date.today()

    if start_d is None:
        start_d = today - timedelta(days=28)
    if end_d is None:
        end_d = today + timedelta(days=27)

    # Load roster with full details
    query = """
        SELECT
            r.roster_id,
            r.crew_id,
            c.name,
            c.role,
            c.fleet,
            r.flight_id,
            f.aircraft,
            f.origin,
            f.destination,
            r.duty_date,
            r.report_time,
            r.debrief_time,
            r.fdp_hours,
            r.duty_id,
            r.status,
            r.override_flag,
            f.dep_time,
            f.arr_time,
            c.medical_exp,
            c.sep_exp,
            c.type_rating_exp,
            c.lpc_opc_exp,
            c.line_check_exp,
            c.contract_expiry
        FROM roster r
        JOIN crew    c ON r.crew_id   = c.crew_id
        JOIN flights f ON r.flight_id = f.flight_id
        WHERE r.duty_date BETWEEN :sd AND :ed
        AND r.status NOT IN ('CANCELLED','REST','REMOVED')
    """
    params = {"sd": start_d, "ed": end_d}
    if crew_id:
        query += " AND r.crew_id = :cid"
        params["cid"] = crew_id

    query += " ORDER BY r.crew_id, r.report_time"

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn, params=params)

    if df.empty:
        return {
            'violations': [],
            'warnings':   [],
            'summary':    {},
            'crew_stats': {}
        }

    violations = []
    warnings   = []
    crew_stats = {}

    # Convert timestamps
    df['report_time']  = pd.to_datetime(df['report_time'])
    df['debrief_time'] = pd.to_datetime(df['debrief_time'])
    df['dep_time']     = pd.to_datetime(df['dep_time'])
    df['arr_time']     = pd.to_datetime(df['arr_time'])
    df['duty_date']    = pd.to_datetime(df['duty_date']).dt.date

    # ── Validate per crew ────────────────────────────────
    for cid, group in df.groupby('crew_id'):
        group = group.sort_values(['report_time', 'duty_id']).reset_index(drop=True)

        # Collapse multi-flight duties into single duty row
        group = (
            group.groupby(['duty_id'], as_index=False)
            .agg({
                'crew_id':         'first',
                'report_time':     'first',
                'debrief_time':    'first',
                'fdp_hours':       'first',
                'duty_date':       'first',
                'name':            'first',
                'role':            'first',
                'fleet':           'first',
                'aircraft':        'first',
                'medical_exp':     'first',
                'sep_exp':         'first',
                'type_rating_exp': 'first',
                'lpc_opc_exp':     'first',
                'line_check_exp':  'first',
                'contract_expiry': 'first',
            })
            .sort_values('report_time')
            .reset_index(drop=True)
        )
        crew_name  = group['name'].iloc[0]
        crew_fleet = group['fleet'].iloc[0]
        fleet_type = 'A330' if crew_fleet == 'A330' else 'A320'
        max_fdp    = CAA_RULES['max_fdp'][fleet_type]

        crew_violations = []
        crew_warnings   = []

        total_28day = 0.0
        total_annual = 0.0
        daily_hours  = {}
        duty_dates   = set()

        for i, row in group.iterrows():
            d_str      = row['duty_date'].isoformat()
            fdp        = float(row['fdp_hours'])
            report_dt  = row['report_time']
            debrief_dt = row['debrief_time']
            duty_date  = row['duty_date']

            daily_hours[d_str] = daily_hours.get(d_str, 0.0) + fdp
            total_28day       += fdp
            total_annual      += fdp
            duty_dates.add(duty_date)

            # ── Rule 1: Max FDP per flight ───────────────
            if fdp > max_fdp:
                crew_violations.append({
                    'crew_id':    cid,
                    'crew_name':  crew_name,
                    'flight_id': None,
                    'duty_date':  duty_date,
                    'type':       'MAX_FDP_EXCEEDED',
                    'rule':       f"Max FDP {max_fdp}h ({fleet_type})",
                    'actual':     round(fdp, 2),
                    'limit':      max_fdp,
                    'severity':   'RED',
                    'details':    f"{crew_name} FDP {fdp:.2f}h exceeds "
                                  f"max {max_fdp}h on {duty_date}"
                })

            # ── Rule 2: Min rest between duties ──────────
            if i > 0:
                prev_debrief = group.iloc[i-1]['debrief_time']
                rest_hrs = (report_dt - prev_debrief
                            ).total_seconds() / 3600

                if rest_hrs < CAA_RULES['min_rest_hours']:
                    crew_violations.append({
                        'crew_id':   cid,
                        'crew_name': crew_name,
                        'flight_id': None,
                        'duty_date': duty_date,
                        'type':      'INSUFFICIENT_REST',
                        'rule':      f"Min rest {CAA_RULES['min_rest_hours']}h",
                        'actual':    round(rest_hrs, 2),
                        'limit':     CAA_RULES['min_rest_hours'],
                        'severity':  'RED',
                        'details':   f"{crew_name} only {rest_hrs:.2f}h "
                                     f"rest before {duty_date} duty "
                                     f"(min {CAA_RULES['min_rest_hours']}h)"
                    })
                elif rest_hrs < CAA_RULES['min_rest_hours'] + 2:
                    crew_warnings.append({
                        'crew_id':   cid,
                        'crew_name': crew_name,
                        'flight_id': None,
                        'duty_date': duty_date,
                        'type':      'REST_MARGIN_LOW',
                        'rule':      "Rest within 2h of minimum",
                        'actual':    round(rest_hrs, 2),
                        'limit':     CAA_RULES['min_rest_hours'],
                        'severity':  'YELLOW',
                        'details':   f"{crew_name} rest {rest_hrs:.2f}h "
                                     f"— close to {CAA_RULES['min_rest_hours']}h minimum"
                    })

            # ── Rule 3: 7-day rolling hours ──────────────
            week_start  = duty_date - timedelta(days=6)
            hours_7day  = sum(
                v for d, v in daily_hours.items()
                if week_start <= date.fromisoformat(d) <= duty_date
            )
            max_7day = CAA_RULES['max_7day_hours']

            if hours_7day > max_7day:
                crew_violations.append({
                    'crew_id':   cid,
                    'crew_name': crew_name,
                    'flight_id': None,
                    'duty_date': duty_date,
                    'type':      '7DAY_HOURS_EXCEEDED',
                    'rule':      f"Max 7-day hours {max_7day}h",
                    'actual':    round(hours_7day, 2),
                    'limit':     max_7day,
                    'severity':  'RED',
                    'details':   f"{crew_name} 7-day total "
                                 f"{hours_7day:.2f}h exceeds {max_7day}h"
                })
            elif hours_7day > max_7day * 0.85:
                crew_warnings.append({
                    'crew_id':   cid,
                    'crew_name': crew_name,
                    'flight_id': None,
                    'duty_date': duty_date,
                    'type':      '7DAY_HOURS_WARNING',
                    'rule':      "7-day hours >85% of limit",
                    'actual':    round(hours_7day, 2),
                    'limit':     max_7day,
                    'severity':  'YELLOW',
                    'details':   f"{crew_name} 7-day total "
                                 f"{hours_7day:.2f}h approaching {max_7day}h limit"
                })

            # ── Rule 4: Doc expiry on duty date ──────────
            doc_checks = {
                'Medical':     row['medical_exp'],
                'SEP':         row['sep_exp'],
                'Type Rating': row['type_rating_exp'],
                'LPC/OPC':     row['lpc_opc_exp'],
                'Line Check':  row['line_check_exp'],
                'Contract':    row['contract_expiry'],
            }
            for doc_name, exp in doc_checks.items():
                if exp is None:
                    continue
                exp_date = pd.to_datetime(exp).date()
                if exp_date < duty_date:
                    crew_violations.append({
                        'crew_id':   cid,
                        'crew_name': crew_name,
                        'flight_id': None,
                        'duty_date': duty_date,
                        'type':      'EXPIRED_DOCUMENT',
                        'rule':      f"{doc_name} must be valid",
                        'actual':    0,
                        'limit':     0,
                        'severity':  'RED',
                        'details':   f"{crew_name} {doc_name} expired "
                                     f"{exp_date} — cannot fly on {duty_date}"
                    })

            # ── Rule 5: Overlapping duties ───────────────
            for j, other in group.iterrows():
                if j <= i:
                    continue
                other_rep = other['report_time']
                other_deb = other['debrief_time']
                if report_dt < other_deb and debrief_dt > other_rep:
                    crew_violations.append({
                        'crew_id':   cid,
                        'crew_name': crew_name,
                        'flight_id': None,
                        'duty_date': duty_date,
                        'type':      'OVERLAPPING_DUTIES',
                        'rule':      "No simultaneous duties",
                        'actual':    0,
                        'limit':     0,
                        'severity':  'RED',
                        'details':   f"{crew_name} has overlapping "
                                     f"duties on {duty_date}"
                    })

        # ── Rule 6: 28-day total hours ───────────────────
        max_28 = CAA_RULES['max_28day_hours']
        if total_28day > max_28:
            crew_violations.append({
                'crew_id':   cid,
                'crew_name': crew_name,
                'flight_id': None,
                'duty_date': end_d,
                'type':      '28DAY_HOURS_EXCEEDED',
                'rule':      f"Max 28-day hours {max_28}h",
                'actual':    round(total_28day, 2),
                'limit':     max_28,
                'severity':  'RED',
                'details':   f"{crew_name} 28-day total "
                             f"{total_28day:.2f}h exceeds {max_28}h"
            })
        elif total_28day > max_28 * 0.85:
            crew_warnings.append({
                'crew_id':   cid,
                'crew_name': crew_name,
                'flight_id': None,
                'duty_date': end_d,
                'type':      '28DAY_HOURS_WARNING',
                'rule':      "28-day hours >85% of limit",
                'actual':    round(total_28day, 2),
                'limit':     max_28,
                'severity':  'YELLOW',
                'details':   f"{crew_name} 28-day total "
                             f"{total_28day:.2f}h — approaching {max_28}h"
            })

        # ── Crew stats ───────────────────────────────────
        crew_stats[cid] = {
            'crew_id':      cid,
            'name':         crew_name,
            'fleet':        crew_fleet,
            'total_28day':  round(total_28day, 2),
            'total_duties': len(group),
            'violations':   len(crew_violations),
            'warnings':     len(crew_warnings),
            'status': (
                'RED'    if crew_violations else
                'YELLOW' if crew_warnings   else
                'GREEN'
            )
        }

        violations.extend(crew_violations)
        warnings.extend(crew_warnings)

    # ── Summary ──────────────────────────────────────────
    total_crew      = len(crew_stats)
    red_crew        = sum(1 for s in crew_stats.values()
                          if s['status'] == 'RED')
    yellow_crew     = sum(1 for s in crew_stats.values()
                          if s['status'] == 'YELLOW')
    green_crew      = sum(1 for s in crew_stats.values()
                          if s['status'] == 'GREEN')
    compliance_pct  = round(
        (green_crew / total_crew * 100) if total_crew else 0, 1)

    summary = {
        'total_crew':      total_crew,
        'total_duties':    len(df),
        'violations':      len(violations),
        'warnings':        len(warnings),
        'red_crew':        red_crew,
        'yellow_crew':     yellow_crew,
        'green_crew':      green_crew,
        'compliance_pct':  compliance_pct,
        'validated_at':    datetime.now().strftime('%d/%m/%Y %H:%M'),
        'period':          f"{start_d} → {end_d}",
    }

    return {
        'violations': violations,
        'warnings':   warnings,
        'summary':    summary,
        'crew_stats': crew_stats,
    }


# ============================================================
# PERSIST VIOLATIONS TO DB
# ============================================================
def save_violations(violations):
    """Save violation records to ftl_violations table."""
    if not violations:
        return
    engine = get_engine()
    with engine.connect() as conn:
        # Clear existing unresolved
        conn.execute(text(
            "DELETE FROM ftl_violations WHERE resolved = FALSE"))
        for v in violations:
            conn.execute(text("""
                INSERT INTO ftl_violations
                (crew_id, flight_id, violation_type, rule,
                 actual_value, limit_value, severity,
                 duty_date, details)
                VALUES
                (:cid, :fid, :vt, :rl,
                 :av, :lv, :sv, :dd, :dt)
            """), {
                'cid': v['crew_id'],
                'fid': v.get('flight_id'),
                'vt':  v['type'],
                'rl':  v['rule'],
                'av':  v.get('actual', 0),
                'lv':  v.get('limit', 0),
                'sv':  v['severity'],
                'dd':  v['duty_date'],
                'dt':  v['details'],
            })
        conn.commit()


# ============================================================
# VALIDATE SINGLE CREW ASSIGNMENT (used by override guard)
# ============================================================
def validate_single_assignment(crew_id, flight_id,
                                report_dt, debrief_dt, fdp_hrs):
    """
    Validate one proposed assignment before writing to DB.
    Returns: {'legal': True/False, 'violations': [], 'warnings': []}
    """
    engine  = get_engine()
    today   = date.today()
    result  = {'legal': True, 'violations': [], 'warnings': []}

    # Get crew info
    with engine.connect() as conn:
        crew = pd.read_sql(text("""
            SELECT c.*, cp.current_city
            FROM crew c
            LEFT JOIN crew_position cp ON c.crew_id = cp.crew_id
            WHERE c.crew_id = :cid
        """), conn, params={"cid": crew_id})

        if crew.empty:
            result['legal'] = False
            result['violations'].append("Crew not found")
            return result

        crew = crew.iloc[0]
        fleet_type = 'A330' if crew['fleet'] == 'A330' else 'A320'
        max_fdp    = CAA_RULES['max_fdp'][fleet_type]
        report_dt  = pd.to_datetime(report_dt)
        debrief_dt = pd.to_datetime(debrief_dt)
        duty_date  = report_dt.date()

        # Rule 1: FDP check
        if fdp_hrs > max_fdp:
            result['legal'] = False
            result['violations'].append(
                f"FDP {fdp_hrs:.2f}h exceeds max {max_fdp}h for {fleet_type}")

        # Rule 2: Rest check
        last_duty = pd.read_sql(text("""
            SELECT MAX(debrief_time) as last_debrief
            FROM roster
            WHERE crew_id = :cid
            AND status NOT IN ('CANCELLED','REST','REMOVED')
        """), conn, params={"cid": crew_id})

        last_debrief = last_duty.iloc[0]['last_debrief']
        if last_debrief is not None:
            last_debrief = pd.to_datetime(last_debrief)
            rest_hrs = (report_dt - last_debrief
                        ).total_seconds() / 3600
            if rest_hrs < CAA_RULES['min_rest_hours']:
                result['legal'] = False
                result['violations'].append(
                    f"Insufficient rest: {rest_hrs:.2f}h "
                    f"(min {CAA_RULES['min_rest_hours']}h required)")
            elif rest_hrs < CAA_RULES['min_rest_hours'] + 2:
                result['warnings'].append(
                    f"Rest margin low: {rest_hrs:.2f}h "
                    f"(min {CAA_RULES['min_rest_hours']}h)")

        # Rule 3: 7-day hours
        week_start = duty_date - timedelta(days=6)
        hours_7 = pd.read_sql(text("""
            SELECT COALESCE(SUM(fdp_hours), 0) as total
            FROM roster
            WHERE crew_id = :cid
            AND duty_date BETWEEN :ws AND :wd
            AND status NOT IN ('CANCELLED','REST','REMOVED')
        """), conn, params={
            "cid": crew_id,
            "ws":  week_start,
            "wd":  duty_date
        }).iloc[0]['total']

        hours_7 = float(hours_7)
        if hours_7 + fdp_hrs > CAA_RULES['max_7day_hours']:
            result['legal'] = False
            result['violations'].append(
                f"7-day hours {hours_7 + fdp_hrs:.2f}h would exceed "
                f"{CAA_RULES['max_7day_hours']}h limit")

        # Rule 4: 28-day hours
        month_start = duty_date - timedelta(days=27)
        hours_28 = pd.read_sql(text("""
            SELECT COALESCE(SUM(fdp_hours), 0) as total
            FROM roster
            WHERE crew_id = :cid
            AND duty_date BETWEEN :ms AND :md
            AND status NOT IN ('CANCELLED','REST','REMOVED')
        """), conn, params={
            "cid": crew_id,
            "ms":  month_start,
            "md":  duty_date
        }).iloc[0]['total']

        hours_28 = float(hours_28)
        if hours_28 + fdp_hrs > CAA_RULES['max_28day_hours']:
            result['legal'] = False
            result['violations'].append(
                f"28-day hours {hours_28 + fdp_hrs:.2f}h would exceed "
                f"{CAA_RULES['max_28day_hours']}h limit")

        # Rule 5: Document validity
        doc_checks = {
            'Medical':     crew['medical_exp'],
            'SEP':         crew['sep_exp'],
            'Type Rating': crew['type_rating_exp'],
            'LPC/OPC':     crew['lpc_opc_exp'],
            'Contract':    crew['contract_expiry'],
        }
        for doc_name, exp in doc_checks.items():
            if exp is None:
                continue
            exp_date = pd.to_datetime(exp).date()
            if exp_date < duty_date:
                result['legal'] = False
                result['violations'].append(
                    f"{doc_name} expired {exp_date} — "
                    f"crew cannot fly on {duty_date}")

        # Rule 6: Leave conflict
        on_leave = pd.read_sql(text("""
            SELECT COUNT(*) as cnt FROM crew_leave
            WHERE crew_id = :cid
            AND :dd BETWEEN start_date AND end_date
        """), conn, params={
            "cid": crew_id,
            "dd":  duty_date
        }).iloc[0]['cnt']

        if int(on_leave) > 0:
            result['legal'] = False
            result['violations'].append(
                f"Crew is on leave on {duty_date}")

    return result


# ============================================================
# GENERATE COMPLIANCE REPORT
# ============================================================
def generate_compliance_report(start_d=None, end_d=None):
    """
    Full compliance report — validates roster and saves to DB.
    Returns summary dict.
    """
    today  = date.today()
    if start_d is None:
        start_d = today
    if end_d is None:
        end_d = today + timedelta(days=27)

    result = validate_roster(start_d, end_d)
    save_violations(result['violations'])

    # Save compliance report
    s = result['summary']
    if s:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO compliance_report
                (report_date, total_crew, total_duties,
                 violations, warnings, compliant, compliance_pct)
                VALUES (:rd, :tc, :td, :vl, :wn, :cp, :pct)
            """), {
                'rd':  today,
                'tc':  s.get('total_crew', 0),
                'td':  s.get('total_duties', 0),
                'vl':  s.get('violations', 0),
                'wn':  s.get('warnings', 0),
                'cp':  s.get('green_crew', 0),
                'pct': s.get('compliance_pct', 0),
            })
            conn.commit()

    return result


if __name__ == "__main__":
    print("Running full FTL validation...")
    result = generate_compliance_report()
    s = result['summary']
    print(f"\n{'='*50}")
    print(f"COMPLIANCE REPORT — {s.get('validated_at')}")
    print(f"{'='*50}")
    print(f"Period:      {s.get('period')}")
    print(f"Total Crew:  {s.get('total_crew')}")
    print(f"Total Duties:{s.get('total_duties')}")
    print(f"Violations:  {s.get('violations')} 🔴")
    print(f"Warnings:    {s.get('warnings')} 🟡")
    print(f"Compliant:   {s.get('green_crew')} ✅")
    print(f"Compliance:  {s.get('compliance_pct')}%")
    print(f"{'='*50}")

    if result['violations']:
        print(f"\nVIOLATIONS FOUND:")
        for v in result['violations'][:10]:
            print(f"  🔴 {v['crew_name']} — {v['type']}: {v['details']}")

    if result['warnings']:
        print(f"\nWARNINGS:")
        for w in result['warnings'][:10]:
            print(f"  🟡 {w['crew_name']} — {w['type']}: {w['details']}")