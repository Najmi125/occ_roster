from utils.db import get_engine
from sqlalchemy import text
from datetime import date, datetime, timedelta

rotations = {
    "A320-1": [
        ("KHI","ISB","06:00","08:00"),
        ("ISB","KHI","08:45","10:45"),
        ("KHI","LHE","11:30","13:20"),
        ("LHE","KHI","14:05","15:55"),
        ("KHI","DXB","20:40","22:55"),
        ("DXB","KHI","02:30","06:00"),
    ],
    "A320-2": [
        ("KHI","DXB","06:00","08:15"),
        ("DXB","KHI","09:15","11:30"),
        ("KHI","ISB","12:15","14:15"),
        ("ISB","KHI","15:00","17:00"),
        ("KHI","LHE","17:45","19:35"),
        ("LHE","KHI","20:20","22:10"),
    ],
    "A320-3": [
        ("KHI","LHE","06:00","07:50"),
        ("LHE","JED","08:35","13:05"),
        ("JED","LHE","14:30","19:00"),
        ("LHE","KHI","19:45","21:30"),
    ],
    "A330-1": [
        ("KHI","JED","06:00","10:30"),
        ("JED","KHI","12:00","16:30"),
    ],
    "A330-2": [
        ("KHI","DXB","06:00","08:15"),
        ("DXB","JED","09:15","13:15"),
        ("JED","KHI","14:45","22:15"),
    ]
}

today = date.today()
engine = get_engine()
print("DB:", engine.url)

with engine.begin() as conn:
    r = conn.execute(text("DELETE FROM flights"))
    print("DELETED:", r.rowcount)
    
    state = {ac: 0 for ac in rotations}
    inserted = 0
    
    for day_offset in range(28):
        flight_date = today + timedelta(days=day_offset)
        for ac, pattern in rotations.items():
            idx = state[ac]
            origin, dest, dep_t, arr_t = pattern[idx]
            cs = ac + "-" + str(idx+1) + "-" + flight_date.strftime("%d%m")
            dep = datetime.strptime(str(flight_date) + " " + dep_t, "%Y-%m-%d %H:%M")
            arr = datetime.strptime(str(flight_date) + " " + arr_t, "%Y-%m-%d %H:%M")
            if arr < dep:
                arr += timedelta(days=1)
            conn.execute(text("INSERT INTO flights (aircraft, callsign, origin, destination, dep_time, arr_time, flight_date) VALUES (:a, :c, :o, :d, :de, :ar, :f)"), {"a": ac, "c": cs, "o": origin, "d": dest, "de": dep, "ar": arr, "f": flight_date})
            inserted += 1
            state[ac] = (idx + 1) % len(pattern)
    
    print("INSERTED:", inserted)

with engine.begin() as conn:
    c = conn.execute(text("SELECT COUNT(*) FROM flights")).scalar()
    print("TOTAL IN DB:", c)
    s = conn.execute(text("SELECT aircraft, callsign, origin, destination, dep_time FROM flights ORDER BY dep_time LIMIT 3")).fetchall()
    for row in s:
        print(" ", row)
print("DONE")
