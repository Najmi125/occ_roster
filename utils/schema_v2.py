import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db import get_engine
from sqlalchemy import text

def upgrade_schema():
    engine = get_engine()
    with engine.connect() as conn:

        # 1. CREW POSITION TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS crew_position (
            position_id SERIAL PRIMARY KEY,
            crew_id VARCHAR(20) REFERENCES crew(crew_id),
            current_city VARCHAR(10) DEFAULT 'KHI',
            last_flight_id INTEGER,
            last_updated TIMESTAMP DEFAULT NOW(),
            UNIQUE(crew_id)
        )
        """))

        # 2. STANDBY POOL TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS standby_pool (
            standby_id SERIAL PRIMARY KEY,
            crew_id VARCHAR(20) REFERENCES crew(crew_id),
            standby_date DATE,
            city VARCHAR(10) DEFAULT 'KHI',
            fleet VARCHAR(10),
            start_time TIME,
            end_time TIME,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 3. OVERRIDE AUDIT TABLE
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS override_audit (
            audit_id SERIAL PRIMARY KEY,
            action_type VARCHAR(30),
            affected_flight VARCHAR(20),
            old_crew_id VARCHAR(20),
            new_crew_id VARCHAR(20),
            old_value TEXT,
            new_value TEXT,
            remarks TEXT,
            system_generated BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """))

        # 4. ALTER ROSTER — add remarks + standby flag
        try:
            conn.execute(text("""
                ALTER TABLE roster
                ADD COLUMN IF NOT EXISTS remarks TEXT,
                ADD COLUMN IF NOT EXISTS replaced_crew_id VARCHAR(20),
                ADD COLUMN IF NOT EXISTS standby_flag BOOLEAN DEFAULT FALSE
            """))
        except:
            pass

        # 5. ALTER FLIGHTS — add delay/divert/cancel fields
        try:
            conn.execute(text("""
                ALTER TABLE flights
                ADD COLUMN IF NOT EXISTS delay_minutes INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS divert_dest VARCHAR(10),
                ADD COLUMN IF NOT EXISTS cancel_reason TEXT,
                ADD COLUMN IF NOT EXISTS original_dep TIMESTAMP,
                ADD COLUMN IF NOT EXISTS original_arr TIMESTAMP
            """))
        except:
            pass

        conn.commit()
        print("✅ Schema v2 upgraded successfully")

        # 6. INITIALIZE crew positions — all start at KHI
        conn.execute(text("""
            INSERT INTO crew_position (crew_id, current_city)
            SELECT crew_id, 'KHI' FROM crew
            ON CONFLICT (crew_id) DO NOTHING
        """))
        conn.commit()
        print("✅ Crew positions initialized — all at KHI")

if __name__ == "__main__":
    upgrade_schema()