#!/usr/bin/env python3
"""
Load UK-wide postcode and road data into PostgreSQL.
Sources:
  - Code-Point Open (all 120 postcode areas) → postcodes table
  - OS Open Names (all 819 tiles, Named Roads only) → roads table

Usage: python3 load_uk.py [connection_string]
Default: postgresql://localhost:5432/uk_addr
"""
import csv, json, os, sys, math, glob, time

conn_str = sys.argv[1] if len(sys.argv) > 1 else 'postgresql://localhost:5432/uk_addr'

try:
    import psycopg2
except ImportError:
    print("Installing psycopg2...")
    os.system(f'{sys.executable} -m pip install psycopg2-binary')
    import psycopg2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("  UK Address Data Loader")
print("  Loading Code-Point Open + OS Open Names")
print("=" * 60)

# ── Connect & create schema ─────────────────────────────────────
print(f"\nConnecting to: {conn_str}")
conn = psycopg2.connect(conn_str)
cur = conn.cursor()

print("Creating schema...")
with open(os.path.join(BASE, 'api', 'schema.sql')) as f:
    cur.execute(f.read())
conn.commit()

# ── Load Code-Point Open (all UK postcodes) ────────────────────
codepo_dir = os.path.join(BASE, 'codepo_gb', 'Data', 'CSV')
cp_files = sorted(glob.glob(os.path.join(codepo_dir, '*.csv')))
print(f"\n📮 Loading {len(cp_files)} Code-Point Open files...")

total_pcs = 0
batch = []
for fpath in cp_files:
    area = os.path.basename(fpath).replace('.csv', '').upper()
    with open(fpath, newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 4:
                continue
            pc = row[0].strip().upper()
            try:
                east = float(row[2])
                north = float(row[3])
            except (ValueError, IndexError):
                continue
            batch.append((pc, east, north))
            total_pcs += 1

    # Flush batch every 5000 rows
    if len(batch) >= 5000:
        cur.executemany(
            'INSERT INTO postcodes (postcode, easting, northing) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
            batch
        )
        conn.commit()
        batch = []
    if total_pcs % 100000 == 0:
        print(f"  ... {total_pcs:,} postcodes loaded")

# Final flush
if batch:
    cur.executemany(
        'INSERT INTO postcodes (postcode, easting, northing) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
        batch
    )
    conn.commit()

print(f"  ✅ {total_pcs:,} postcodes loaded")

# ── Load OS Open Names (Named Roads only) ──────────────────────
data_dir = os.path.join(BASE, 'Data')
os_files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))
# Exclude Wirral-specific files
os_files = [f for f in os_files if 'wirral' not in os.path.basename(f).lower()]
print(f"\n🛣️  Loading Named Roads from {len(os_files)} OS tiles...")

# OS Open Names CSV columns (0-indexed):
# 0=ID, 1=URI, 2=NAME1, 3=NAME1_LANG, 4=NAME2, 5=NAME2_LANG,
# 6=TYPE, 7=LOCAL_TYPE, 8=GEOMETRY_X, 9=GEOMETRY_Y,
# 10=?, 11=?, 12=MBR_XMIN, 13=MBR_YMIN, 14=MBR_XMAX, 15=MBR_YMAX,
# 16=POSTCODE_DISTRICT, 17=POSTCODE_DISTRICT_URI,
# 18=POPULATED_PLACE, 19=POPULATED_PLACE_URI,
# 20=DISTRICT_NAME, 21=DISTRICT_URI, 22=DISTRICT_TYPE,
# 23=COUNTY_NAME, 24=COUNTY_URI, 25=COUNTY_TYPE,
# 26=REGION_NAME, 27=REGION_URI, 28=COUNTRY_NAME, 29=COUNTRY_URI,
# 30=SAME_AS_DBPEDIA, 31=SAME_AS_GEONAMES

total_roads = 0
road_batch = []
t0 = time.time()

for fpath in os_files:
    tile = os.path.basename(fpath).replace('.csv', '')
    with open(fpath, newline='', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            # Only process Named Roads
            if len(row) < 16 or row[7] != 'Named Road':
                continue
            try:
                name = row[2].strip()
                os_id = row[0].strip()
                east = float(row[8]) if row[8] else None
                north = float(row[9]) if row[9] else None
                mbr_xmin = float(row[12]) if row[12] else None
                mbr_ymin = float(row[13]) if row[13] else None
                mbr_xmax = float(row[14]) if row[14] else None
                mbr_ymax = float(row[15]) if row[15] else None
                pcd = row[16].strip() if len(row) > 16 else ''
                place = row[18].strip() if len(row) > 18 else ''
                district = row[20].strip() if len(row) > 20 else ''
                county = row[23].strip() if len(row) > 23 else ''
                region = row[26].strip() if len(row) > 26 else ''
                country = row[28].strip() if len(row) > 28 else ''
            except (ValueError, IndexError):
                continue

            if not name:
                continue

            road_batch.append((os_id, name, 'Named Road', east, north,
                               mbr_xmin, mbr_ymin, mbr_xmax, mbr_ymax,
                               pcd, place, district, county, region, country))
            total_roads += 1

            if len(road_batch) >= 2000:
                cur.executemany(
                    '''INSERT INTO roads
                       (os_id, name, local_type, easting, northing,
                        mbr_xmin, mbr_ymin, mbr_xmax, mbr_ymax,
                        postcode_district, populated_place,
                        district_name, county_name, region_name, country_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING''',
                    road_batch
                )
                conn.commit()
                road_batch = []

    if total_roads % 50000 == 0 and total_roads > 0:
        elapsed = time.time() - t0
        rate = total_roads / elapsed
        print(f"  ... {total_roads:,} roads loaded ({rate:.0f}/sec)")

# Final flush
if road_batch:
    cur.executemany(
        '''INSERT INTO roads
           (os_id, name, local_type, easting, northing,
            mbr_xmin, mbr_ymin, mbr_xmax, mbr_ymax,
            postcode_district, populated_place,
            district_name, county_name, region_name, country_name)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT DO NOTHING''',
        road_batch
    )
    conn.commit()

elapsed = time.time() - t0
print(f"  ✅ {total_roads:,} roads loaded in {elapsed:.0f}s ({total_roads/elapsed:.0f}/sec)")

# ── Stats ─────────────────────────────────────────────────────
cur.execute('SELECT COUNT(*) FROM postcodes')
pcs = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM roads')
rds = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM addresses')
addrs = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM postcode_roads')
pcr = cur.fetchone()[0]

print(f"\n{'=' * 60}")
print(f"  📊 Database Summary")
print(f"  {'=' * 60}")
print(f"  Postcodes:     {pcs:>8,}")
print(f"  Roads:         {rds:>8,}")
print(f"  Addresses:     {addrs:>8,} (Wirral only)")
print(f"  PC→Road links: {pcr:>8,} (Wirral only)")
print(f"\n  To load Wirral addresses too, run: python3 load_to_pg.py {conn_str}")
print(f"{'=' * 60}")

cur.close()
conn.close()
print("\nDone! 🎉")
