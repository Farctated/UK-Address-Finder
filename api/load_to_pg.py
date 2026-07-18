#!/usr/bin/env python3
"""
Load Wirral address data from JSON to PostgreSQL.
Usage: python3 load_to_pg.py [connection_string]
Default: postgresql://localhost:5432/wirral_addr
"""
import json, sys, os

conn_str = sys.argv[1] if len(sys.argv) > 1 else 'postgresql://localhost:5432/wirral_addr'

try:
    import psycopg2
except ImportError:
    print("Installing psycopg2...")
    os.system(f'{sys.executable} -m pip install psycopg2-binary')
    import psycopg2

print(f"Connecting to PostgreSQL: {conn_str}")
conn = psycopg2.connect(conn_str)
cur = conn.cursor()

# Run schema
print("Creating schema...")
with open(os.path.join(os.path.dirname(__file__), 'schema.sql')) as f:
    cur.execute(f.read())
conn.commit()

# Load JSON
print("Loading wirral_db.json...")
with open('Data/wirral_db.json') as f:
    db = json.load(f)

print(f"Inserting {len(db['postcodes'])} postcodes...")
for pc, entry in db['postcodes'].items():
    cur.execute(
        'INSERT INTO postcodes (postcode, easting, northing) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
        (pc, entry['e'], entry['n'])
    )

    for r in entry['roads']:
        cur.execute(
            '''INSERT INTO postcode_roads (postcode, street, pcd, place, distance_m, in_mbr)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (pc, r['s'], r.get('pcd',''), r.get('pl',''), r['d'], bool(r['m']))
        )

    for a in entry.get('addrs', []):
        lat = float(a['lat']) if a.get('lat') else None
        lon = float(a['lon']) if a.get('lon') else None
        cur.execute(
            '''INSERT INTO addresses (postcode, house_num, street, lat, lon)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (pc, a.get('h',''), a['s'], lat, lon)
        )

print(f"Inserting {len(db['streets'])} street names...")
for street, info in db['streets'].items():
    cur.execute(
        'INSERT INTO streets (street, addr_count) VALUES (%s,%s) ON CONFLICT DO NOTHING',
        (street, info['c'])
    )

conn.commit()

# Stats
cur.execute('SELECT COUNT(*) FROM postcodes')
pcs = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM addresses')
addrs = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM postcode_roads')
roads = cur.fetchone()[0]

print(f"\nDone! Loaded:")
print(f"  {pcs} postcodes")
print(f"  {addrs} addresses")
print(f"  {roads} postcode->road mappings")

cur.close()
conn.close()
