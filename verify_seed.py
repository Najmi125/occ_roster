from utils.db import get_engine
from sqlalchemy import text

engine = get_engine()
print(f"Database: {engine.url}")

with engine.begin() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM flights")).scalar()
    print(f"Total flights in DB: {count}")
    
    aircrafts = conn.execute(text("SELECT DISTINCT aircraft FROM flights")).fetchall()
    print(f"Aircrafts: {[a[0] for a in aircrafts]}")
    
    sample = conn.execute(text("""
        SELECT aircraft, callsign, origin, destination, dep_time, flight_date 
        FROM flights ORDER BY dep_time LIMIT 5
    """)).fetchall()
    print("First 5 flights:")
    for row in sample:
        print(f"  {row}")
