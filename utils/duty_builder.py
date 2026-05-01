import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
from datetime import timedelta

DUTY_TEMPLATES = [
    {
        'duty_id':   'A320-1-D1',
        'aircraft':  'A320-1',
        'fleet':     'A320',
        'flights':   ['XYZ-101','XYZ-102','XYZ-103','XYZ-104'],
        'ends_at':   'KHI',
        'overnight': False,
    },
    {
        'duty_id':   'A320-1-D2',
        'aircraft':  'A320-1',
        'fleet':     'A320',
        'flights':   ['XYZ-105','XYZ-106'],
        'ends_at':   'KHI',
        'overnight': False,
    },
    {
        'duty_id':   'A320-2-D1',
        'aircraft':  'A320-2',
        'fleet':     'A320',
        'flights':   ['XYZ-201','XYZ-202'],
        'ends_at':   'KHI',
        'overnight': False,
    },
    {
        'duty_id':   'A320-2-D2',
        'aircraft':  'A320-2',
        'fleet':     'A320',
        'flights':   ['XYZ-203','XYZ-204','XYZ-205','XYZ-206'],
        'ends_at':   'KHI',
        'overnight': False,
    },
    {
        'duty_id':   'A320-3-D1',
        'aircraft':  'A320-3',
        'fleet':     'A320',
        'flights':   ['XYZ-301','XYZ-302'],
        'ends_at':   'JED',
        'overnight': True,
    },
    {
        'duty_id':     'A320-3-D2',
        'aircraft':    'A320-3',
        'fleet':       'A320',
        'flights':     ['XYZ-303'],
        'ends_at':     'KHI',
        'overnight':   False,
        'requires_at': 'JED',
    },
    {
        'duty_id':   'A330-1-D1',
        'aircraft':  'A330-1',
        'fleet':     'A330',
        'flights':   ['XYZ-401','XYZ-402'],
        'ends_at':   'KHI',
        'overnight': False,
    },
    {
        'duty_id':   'A330-2-D1',
        'aircraft':  'A330-2',
        'fleet':     'A330',
        'flights':   ['XYZ-501','XYZ-502'],
        'ends_at':   'JED',
        'overnight': True,
    },
    {
        'duty_id':     'A330-2-D2',
        'aircraft':    'A330-2',
        'fleet':       'A330',
        'flights':     ['XYZ-503'],
        'ends_at':     'KHI',
        'overnight':   False,
        'requires_at': 'JED',
    },
]


def build_duties_for_date(flights_df, duty_date):
    """
    Build duty objects from flights_df for a given date.
    Returns list of duty dicts with all fields needed by roster engine.
    """
    duties = []

    for tmpl in DUTY_TEMPLATES:
        duty_flights = []

        for base_cs in tmpl['flights']:
            match = flights_df[
                flights_df['callsign'].apply(
                    lambda cs: '-'.join(str(cs).split('-')[:2]) == base_cs
                )
            ]
            if match.empty:
                continue
            duty_flights.append(match.iloc[0])

        if not duty_flights:
            continue
        if len(duty_flights) != len(tmpl['flights']):
            continue

        duty_flights = sorted(
            duty_flights,
            key=lambda x: pd.to_datetime(x['dep_time'])
        )

        first_dep  = pd.to_datetime(duty_flights[0]['dep_time'])
        last_arr   = pd.to_datetime(duty_flights[-1]['arr_time'])
        report_dt  = first_dep - timedelta(minutes=60)
        debrief_dt = last_arr  + timedelta(minutes=30)
        fdp_hrs    = (debrief_dt - report_dt).total_seconds() / 3600
        block_hrs  = sum(
            (pd.to_datetime(f['arr_time']) -
             pd.to_datetime(f['dep_time'])).total_seconds() / 3600
            for f in duty_flights
        )

        duties.append({
            'duty_id':     tmpl['duty_id'],
            'aircraft':    tmpl['aircraft'],
            'fleet':       tmpl['fleet'],
            'duty_date':   duty_date,
            'flights':     duty_flights,
            'flight_ids':  [int(f['flight_id']) for f in duty_flights],
            'callsigns':   [f['callsign'] for f in duty_flights],
            'origin':      duty_flights[0]['origin'],
            'destination': duty_flights[-1]['destination'],
            'report_dt':   report_dt,
            'debrief_dt':  debrief_dt,
            'fdp_hrs':     round(fdp_hrs, 2),
            'block_hrs':   round(block_hrs, 2),
            'ends_at':     tmpl['ends_at'],
            'overnight':   tmpl.get('overnight', False),
            'requires_at': tmpl.get('requires_at', None),
        })

    return duties