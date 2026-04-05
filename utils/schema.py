import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db import get_engine
from sqlalchemy import text

def create_tables():
    engine = get_engine()
    with engine.connect() as conn:

        # 1. CREW TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS crew (
            crew_id VARCHAR(20) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            role VARCHAR(10) NOT NULL,
            fleet VARCHAR(10) NOT NULL,
            phone VARCHAR(20),
            base VARCHAR(10) DEFAULT 'KHI',
            contract_expiry DATE,
            medical_exp DATE,
            sep_exp DATE,
            crm_exp DATE,
            dg_exp DATE,
            atpl_exp DATE,
            type_rating_exp DATE,
            lpc_opc_exp DATE,
            line_check_exp DATE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 2. FLIGHTS TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS flights (
            flight_id SERIAL PRIMARY KEY,
            aircraft VARCHAR(20),
            callsign VARCHAR(20) UNIQUE,
            origin VARCHAR(10),
            destination VARCHAR(10),
            dep_time TIMESTAMP,
            arr_time TIMESTAMP,
            flight_date DATE,
            status VARCHAR(20) DEFAULT 'SCHEDULED',
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 3. ROSTER TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS roster (
            roster_id SERIAL PRIMARY KEY,
            crew_id VARCHAR(20) REFERENCES crew(crew_id),
            flight_id INTEGER REFERENCES flights(flight_id),
            duty_date DATE,
            report_time TIMESTAMP,
            debrief_time TIMESTAMP,
            fdp_hours NUMERIC(5,2),
            duty_type VARCHAR(20) DEFAULT 'FLIGHT',
            status VARCHAR(20) DEFAULT 'ASSIGNED',
            override_flag BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 4. DISRUPTIONS TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS disruptions (
            disruption_id SERIAL PRIMARY KEY,
            disruption_type VARCHAR(50),
            affected_flight VARCHAR(20),
            affected_crew VARCHAR(20),
            reason VARCHAR(200),
            reported_by VARCHAR(100),
            disruption_time TIMESTAMP,
            resolved BOOLEAN DEFAULT FALSE,
            resolution_notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 5. REST TRACKER TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS rest_tracker (
            rest_id SERIAL PRIMARY KEY,
            crew_id VARCHAR(20) REFERENCES crew(crew_id),
            duty_date DATE,
            fdp_start TIMESTAMP,
            fdp_end TIMESTAMP,
            fdp_hours NUMERIC(5,2),
            rest_start TIMESTAMP,
            rest_end TIMESTAMP,
            rest_hours NUMERIC(5,2),
            accum_7days NUMERIC(5,2),
            accum_28days NUMERIC(5,2),
            accum_365days NUMERIC(5,2),
            compliant BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 6. ALERTS TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id SERIAL PRIMARY KEY,
            crew_id VARCHAR(20) REFERENCES crew(crew_id),
            alert_type VARCHAR(50),
            alert_message VARCHAR(200),
            expiry_date DATE,
            days_remaining INTEGER,
            severity VARCHAR(10),
            acknowledged BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        conn.commit()
        print("✅ All tables created successfully")

if __name__ == "__main__":
    create_tables()