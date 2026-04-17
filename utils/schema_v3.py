import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db import get_engine
from sqlalchemy import text

def upgrade_v3():
    engine = get_engine()
    with engine.connect() as conn:

        # 1. FDTL VIOLATIONS TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ftl_violations (
            violation_id   SERIAL PRIMARY KEY,
            crew_id        VARCHAR(20) REFERENCES crew(crew_id),
            flight_id      INTEGER,
            violation_type VARCHAR(50),
            rule           VARCHAR(100),
            actual_value   NUMERIC(8,2),
            limit_value    NUMERIC(8,2),
            severity       VARCHAR(10),
            duty_date      DATE,
            details        TEXT,
            resolved       BOOLEAN DEFAULT FALSE,
            created_at     TIMESTAMP DEFAULT NOW()
        )
        """))

        # 2. ASSIGNMENT LOG TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS assignment_log (
            log_id           SERIAL PRIMARY KEY,
            crew_id          VARCHAR(20) REFERENCES crew(crew_id),
            flight_id        INTEGER,
            duty_date        DATE,
            assignment_score NUMERIC(6,2),
            hours_28day      NUMERIC(6,2),
            hours_7day       NUMERIC(6,2),
            rest_hours       NUMERIC(6,2),
            is_standby       BOOLEAN DEFAULT FALSE,
            docs_valid       BOOLEAN DEFAULT TRUE,
            at_origin        BOOLEAN DEFAULT TRUE,
            candidates_count INTEGER,
            alternatives     TEXT,
            decision_reason  TEXT,
            created_at       TIMESTAMP DEFAULT NOW()
        )
        """))

        # 3. COMPLIANCE REPORT TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS compliance_report (
            report_id      SERIAL PRIMARY KEY,
            report_date    DATE DEFAULT CURRENT_DATE,
            total_crew     INTEGER,
            total_duties   INTEGER,
            violations     INTEGER,
            warnings       INTEGER,
            compliant      INTEGER,
            compliance_pct NUMERIC(5,2),
            generated_at   TIMESTAMP DEFAULT NOW()
        )
        """))

        conn.commit()
        print("✅ Schema v3 — FTL validation tables created")

if __name__ == "__main__":
    upgrade_v3()
