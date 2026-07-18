#!/usr/bin/env python3
"""
UK Address Verifier API — CallerID-style address lookup.
Backend: PostgreSQL (Code-Point Open + OS Open Names)
Usage:  python3 server.py [port]
        python3 server.py 5050
"""
import os, sys, json, re, ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

def format_pc(raw):
    """Normalise any postcode: 'ch412zd' → 'CH41 2ZD', 'ch412z' → 'CH41 2Z'"""
    s = re.sub(r'[^A-Z0-9]', '', raw.upper().strip())
    if len(s) <= 3:
        return s

    # Full UK postcodes end with an inward code: digit + two letters.
    if len(s) >= 5 and re.match(r'^\d[A-Z]{2}$', s[-3:]):
        return f'{s[:-3]} {s[-3:]}'

    if len(s) == 6:
        # Partial inward postcode, e.g. "CH412Z" -> "CH41 2Z".
        if len(s) >= 4 and s[3].isdigit():
            return f'{s[:4]} {s[4:]}'
        return f'{s[:3]} {s[3:]}'
    if len(s) == 5:
        # "M11AD" → "M1 1AD" (2+3). "CH412" → "CH41 2" (4+1 partial)
        if re.match(r'^\d[A-Z]{2}$', s[-3:]):
            return f'{s[:2]} {s[2:]}'
        return f'{s[:4]} {s[4:]}'
    return s

# ── Config ──────────────────────────────────────────────────────
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
DB_CONN = os.environ.get('DATABASE_URL', 'postgresql://localhost:5432/uk_addr')
HERE_API_KEY = os.environ.get('HERE_API_KEY', 'pRO8xtDpLEhCcI99Y3mVl9UDqxL5XOCzsmbHlr5Lpb4')

# Lazy DB connection
_conn = None
def get_db():
    global _conn
    if _conn is None:
        try:
            import psycopg2
            _conn = psycopg2.connect(DB_CONN)
        except ImportError:
            print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
            sys.exit(1)
    return _conn

def json_response(data, status=200):
    return (json.dumps(data, default=str).encode(), status,
            [('Content-Type', 'application/json'),
             ('Access-Control-Allow-Origin', '*'),
             ('Access-Control-Allow-Methods', 'GET, OPTIONS'),
             ('Access-Control-Allow-Headers', '*')])

def error(msg, status=400):
    return json_response({'ok': False, 'error': msg}, status)

# ── API Handlers ────────────────────────────────────────────────

def handle_verify(params):
    """CallerID-style verification: ?postcode=CH41+2TL&number=61"""
    pc = format_pc(params.get('postcode', [''])[0])
    num = params.get('number', [''])[0].strip()
    if not pc:
        return error('postcode is required')

    # If no number provided, return nearby streets (like lookup)
    if not num:
        return handle_lookup(params)

    db = get_db()
    cur = db.cursor()

    # Get postcode coordinates for nearby street lookup
    is_partial = len(pc.replace(' ', '')) <= 6
    if is_partial:
        cur.execute('''SELECT easting, northing FROM postcodes WHERE postcode LIKE %s''', (pc + '%',))
    else:
        cur.execute('''SELECT easting, northing FROM postcodes WHERE postcode=%s''', (pc,))
    coords_for_suggest = cur.fetchone()

    # Exact match (full postcode) or prefix match (partial)
    if is_partial:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE postcode LIKE %s AND house_num=%s''', (pc + '%', num))
    else:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE postcode=%s AND house_num=%s''', (pc, num))
    row = cur.fetchone()
    if row:
        return json_response({
            'ok': True, 'verified': True, 'source': 'exact',
            'house_num': row[0], 'street': row[1],
            'postcode': row[2], 'lat': row[3], 'lon': row[4]
        })

    # Try case-insensitive fuzzy
    if is_partial:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE postcode LIKE %s
                       AND LOWER(house_num) = LOWER(%s)''', (pc + '%', num))
    else:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE postcode=%s
                       AND LOWER(house_num) = LOWER(%s)''', (pc, num))
    row = cur.fetchone()
    if row:
        return json_response({
            'ok': True, 'verified': True, 'source': 'fuzzy',
            'house_num': row[0], 'street': row[1],
            'postcode': row[2], 'lat': row[3], 'lon': row[4]
        })

    # Not found — return nearby streets so caller can pick
    # Try UK-wide roads first, then legacy Wirral postcode_roads
    uk_roads = []
    if coords_for_suggest:
        east, north = coords_for_suggest
        cur.execute('''SELECT name, populated_place, postcode_district,
                              SQRT(POWER(easting - %s, 2) + POWER(northing - %s, 2)) as dist
                       FROM roads
                       WHERE mbr_xmin <= %s AND mbr_xmax >= %s
                         AND mbr_ymin <= %s AND mbr_ymax >= %s
                       ORDER BY dist
                       LIMIT 8''', (east, north, east, east, north, north))
        for r in cur.fetchall():
            uk_roads.append({
                'street': r[0], 'district': r[2] or '', 'area': r[1] or '',
                'distance_m': round(float(r[3]), 1), 'in_mbr': True
            })

    if is_partial:
        cur.execute('''SELECT DISTINCT street, pcd, place, distance_m, in_mbr
                       FROM postcode_roads WHERE postcode LIKE %s
                       ORDER BY in_mbr DESC, distance_m LIMIT 8''', (pc + '%',))
    else:
        cur.execute('''SELECT DISTINCT street, pcd, place, distance_m, in_mbr
                       FROM postcode_roads WHERE postcode=%s
                       ORDER BY in_mbr DESC, distance_m LIMIT 8''', (pc,))
    legacy_roads = [{'street': r[0], 'district': r[1], 'area': r[2],
                     'distance_m': float(r[3]), 'in_mbr': bool(r[4])} for r in cur.fetchall()]

    # Merge: UK roads first, then add legacy Wirral roads not already listed
    seen = set(r['street'] for r in uk_roads)
    for r in legacy_roads:
        if r['street'] not in seen:
            uk_roads.append(r)
            seen.add(r['street'])

    # Get area name from the first road's populated_place
    area = ''
    if uk_roads:
        area = uk_roads[0].get('area', '')
    elif legacy_roads:
        area = legacy_roads[0].get('area', '')

    return json_response({
        'ok': True, 'verified': False,
        'postcode': pc, 'house_num': num,
        'area': area,
        'message': 'Address not found in local data',
        'suggested_streets': uk_roads[:8]
    })

def handle_lookup(params):
    """Lookup streets & addresses for a postcode: ?postcode=CH41+2TL

    Uses UK-wide roads table (on-the-fly MBR matching) + legacy Wirral data.
    """
    pc = format_pc(params.get('postcode', [''])[0])
    if not pc:
        return error('postcode is required')

    db = get_db()
    cur = db.cursor()
    is_partial = len(pc.replace(' ', '')) <= 6

    if is_partial:
        cur.execute('''SELECT easting, northing FROM postcodes WHERE postcode LIKE %s''', (pc + '%',))
    else:
        cur.execute('''SELECT easting, northing FROM postcodes WHERE postcode=%s''', (pc,))
    coords = cur.fetchone()

    roads = []

    if coords:
        east, north = coords[0], coords[1]
        # UK-wide: find roads whose MBR contains this postcode
        cur.execute('''SELECT name, populated_place, postcode_district,
                              SQRT(POWER(easting - %s, 2) + POWER(northing - %s, 2)) as dist
                       FROM roads
                       WHERE mbr_xmin <= %s AND mbr_xmax >= %s
                         AND mbr_ymin <= %s AND mbr_ymax >= %s
                       ORDER BY dist
                       LIMIT 8''', (east, north, east, east, north, north))
        for r in cur.fetchall():
            roads.append({
                'street': r[0],
                'district': r[2] or '',
                'area': r[1] or '',
                'distance_m': round(float(r[3]), 1),
                'in_mbr': True
            })

        # If fewer than 3 MBR matches, also find nearest roads by distance
        if len(roads) < 3:
            cur.execute('''SELECT name, populated_place, postcode_district,
                                  SQRT(POWER(easting - %s, 2) + POWER(northing - %s, 2)) as dist
                           FROM roads
                           WHERE easting BETWEEN %s-20000 AND %s+20000
                             AND northing BETWEEN %s-20000 AND %s+20000
                           ORDER BY dist
                           LIMIT 8''', (east, north, east, east, north, north))
            seen = set(r['street'] for r in roads)
            for r in cur.fetchall():
                if r[0] not in seen:
                    roads.append({
                        'street': r[0],
                        'district': r[2] or '',
                        'area': r[1] or '',
                        'distance_m': round(float(r[3]), 1),
                        'in_mbr': False
                    })
                    seen.add(r[0])

    # Also include legacy Wirral postcode_roads data if available
    if is_partial:
        cur.execute('''SELECT DISTINCT street, pcd, place, distance_m, in_mbr
                       FROM postcode_roads WHERE postcode LIKE %s
                       ORDER BY in_mbr DESC, distance_m LIMIT 8''', (pc + '%',))
    else:
        cur.execute('''SELECT DISTINCT street, pcd, place, distance_m, in_mbr
                       FROM postcode_roads WHERE postcode=%s
                       ORDER BY in_mbr DESC, distance_m LIMIT 8''', (pc,))
    legacy_roads = [{'street': r[0], 'district': r[1], 'area': r[2],
                     'distance_m': float(r[3]), 'in_mbr': bool(r[4])} for r in cur.fetchall()]

    # Merge: UK roads first, then add legacy Wirral roads not already listed
    seen_streets = set(r['street'] for r in roads)
    for r in legacy_roads:
        if r['street'] not in seen_streets:
            roads.append(r)
            seen_streets.add(r['street'])

    # Limit to 8
    roads = roads[:8]

    # Lookup addresses (Wirral-only from legacy table)
    if is_partial:
        cur.execute('''SELECT house_num, street, lat, lon
                       FROM addresses WHERE postcode LIKE %s
                       ORDER BY house_num LIMIT 50''', (pc + '%',))
    else:
        cur.execute('''SELECT house_num, street, lat, lon
                       FROM addresses WHERE postcode=%s
                       ORDER BY house_num LIMIT 50''', (pc,))
    addrs = [{'house_num': a[0], 'street': a[1], 'lat': a[2], 'lon': a[3]} for a in cur.fetchall()]

    # Fallback: fetch addresses by street name from legacy table
    if not addrs and roads:
        street_names = [r['street'] for r in roads if r['in_mbr']][:3]
        if street_names:
            placeholders = ','.join('%s' for _ in street_names)
            cur.execute(f'''SELECT house_num, street, lat, lon
                           FROM addresses WHERE street IN ({placeholders})
                           ORDER BY street, house_num LIMIT 50''', street_names)
            addrs = [{'house_num': a[0], 'street': a[1], 'lat': a[2], 'lon': a[3]} for a in cur.fetchall()]

    return json_response({
        'ok': True,
        'postcode': pc,
        'easting': float(coords[0]) if coords else None,
        'northing': float(coords[1]) if coords else None,
        'roads': roads,
        'addresses': addrs
    })

def handle_search(params):
    """Search by street name or area: ?q=Oxton+Road"""
    q = params.get('q', [''])[0].strip()
    if not q:
        return error('search query is required')

    db = get_db()
    cur = db.cursor()

    cur.execute('''SELECT street, addr_count FROM streets
                   WHERE street ILIKE %s
                   ORDER BY addr_count DESC LIMIT 20''', (f'%{q}%',))
    streets = [{'street': r[0], 'addresses': r[1]} for r in cur.fetchall()]

    return json_response({
        'ok': True, 'query': q,
        'streets': streets
    })


def handle_street_addresses(params):
    """Return all house numbers for a given street: ?street=Quarrybank+Street"""
    street = params.get('street', [''])[0].strip()
    if not street:
        return error('street is required')
    pc = format_pc(params.get('postcode', [''])[0])

    db = get_db()
    cur = db.cursor()
    if pc:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE street=%s AND postcode LIKE %s
                       ORDER BY house_num LIMIT 100''', (street, pc + '%'))
    else:
        cur.execute('''SELECT house_num, street, postcode, lat, lon
                       FROM addresses WHERE street=%s
                       ORDER BY house_num LIMIT 100''', (street,))
    addrs = [{'house_num': a[0], 'street': a[1], 'postcode': a[2],
              'lat': a[3], 'lon': a[4]} for a in cur.fetchall()]

    return json_response({
        'ok': True, 'street': street,
        'addresses': addrs
    })


def handle_save_address(params):
    """Save a customer-verified address.
    POST /api/save-address  body: {postcode, house_num, street}
    Accepts params as dict (from JSON body) or as parsed query string.
    """
    if isinstance(params, dict):
        pc = format_pc(params.get('postcode', ''))
        num = params.get('house_num', '').strip()
        street = params.get('street', '').strip()
    else:
        pc = format_pc(params.get('postcode', [''])[0])
        num = params.get('house_num', [''])[0].strip()
        street = params.get('street', [''])[0].strip()
    if not pc or not num or not street:
        return error('postcode, house_num, and street are required')

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute('''INSERT INTO saved_addresses (postcode, house_num, street)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (postcode, house_num, street)
                       DO UPDATE SET updated_at = NOW()''',
                    (pc, num, street))
        db.commit()
        return json_response({'ok': True, 'saved': True,
                              'postcode': pc, 'house_num': num, 'street': street})
    except Exception as e:
        db.rollback()
        return error(str(e), 500)


def handle_saved_lookup(params):
    """Return saved addresses for a postcode: ?postcode=CH41+2TL"""
    pc = format_pc(params.get('postcode', [''])[0])
    if not pc:
        return error('postcode is required')

    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT house_num, street, source, created_at
                   FROM saved_addresses WHERE postcode=%s
                   ORDER BY updated_at DESC LIMIT 20''', (pc,))
    addrs = [{'house_num': r[0], 'street': r[1],
              'source': r[2], 'created_at': str(r[3])} for r in cur.fetchall()]

    return json_response({
        'ok': True, 'postcode': pc,
        'saved_addresses': addrs
    })


def handle_nominatim_proxy(params):
    """Proxy Nominatim API requests to avoid CORS issues.
    GET /api/nominatim?q=... or GET /api/nominatim?street=...&postalcode=...
    """
    import urllib.request, urllib.parse
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except (ImportError, NameError):
        import ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    base = 'https://nominatim.openstreetmap.org/search'
    query_params = {}
    for key, vals in params.items():
        query_params[key] = vals[0] if isinstance(vals, list) else vals

    query_params['format'] = 'json'
    query_params['addressdetails'] = '1'
    query_params['limit'] = query_params.get('limit', '5')
    query_params['countrycodes'] = 'gb'

    url = base + '?' + urllib.parse.urlencode(query_params)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'UKAddressVerifier/1.0'})
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
            data = json.loads(resp.read())
        return json_response({'ok': True, 'results': data})
    except Exception as e:
        return json_response({'ok': False, 'error': str(e), 'results': []})


def handle_here_geocode(params):
    """Proxy Here Maps Geocoding API.
    GET /api/here?q=82+Everton+Valley+L4+4EX&limit=5
    """
    import urllib.request, urllib.parse
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except (ImportError, NameError):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    q = params.get('q', [''])[0] if isinstance(params, dict) else ''
    if not q:
        return json_response({'ok': False, 'error': 'query is required', 'results': []})
    limit = params.get('limit', ['5'])[0] if isinstance(params, dict) else '5'
    url = f'https://geocode.search.hereapi.com/v1/geocode?q={urllib.parse.quote(q)}&apiKey={HERE_API_KEY}&limit={limit}'
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
            data = json.loads(resp.read())
        items = data.get('items', [])
        return json_response({'ok': True, 'results': items})
    except Exception as e:
        return json_response({'ok': False, 'error': str(e), 'results': []})


def handle_delete_saved(params):
    """Delete a saved address.
    POST /api/delete-saved  body: {postcode, house_num, street}
    """
    if isinstance(params, dict):
        pc = format_pc(params.get('postcode', ''))
        num = params.get('house_num', '').strip()
        street = params.get('street', '').strip()
    else:
        pc = format_pc(params.get('postcode', [''])[0])
        num = params.get('house_num', [''])[0].strip()
        street = params.get('street', [''])[0].strip()
    if not pc or not num or not street:
        return error('postcode, house_num, and street are required')

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute('''DELETE FROM saved_addresses
                       WHERE postcode=%s AND house_num=%s AND street=%s''',
                    (pc, num, street))
        deleted = cur.rowcount
        db.commit()
        return json_response({'ok': True, 'deleted': deleted > 0})
    except Exception as e:
        db.rollback()
        return error(str(e), 500)

# ── HTTP Router ─────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip('/')

        try:
            if path == '/api/verify':
                data, status, headers = handle_verify(params)
            elif path == '/api/lookup':
                data, status, headers = handle_lookup(params)
            elif path == '/api/search':
                data, status, headers = handle_search(params)
            elif path == '/api/street-addresses':
                data, status, headers = handle_street_addresses(params)
            elif path == '/api/saved-lookup':
                data, status, headers = handle_saved_lookup(params)
            elif path == '/api/nominatim':
                data, status, headers = handle_nominatim_proxy(params)
            elif path == '/api/here':
                data, status, headers = handle_here_geocode(params)
            elif path == '/api/health':
                data, status, headers = json_response({'ok': True, 'service': 'UK Address Verifier'})
            else:
                data, status, headers = error('Not found. Endpoints: /api/verify, /api/lookup, /api/search, /api/street-addresses, /api/save-address, /api/saved-lookup, /api/nominatim, /api/here, /api/health', 404)

            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode())

    def do_POST(self):
        """Handle POST requests"""
        path = urlparse(self.path).path.rstrip('/')
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length else b'{}'
            params = json.loads(body) if body else {}

            if path == '/api/save-address':
                data, status, headers = handle_save_address(params)
            elif path == '/api/delete-saved':
                data, status, headers = handle_delete_saved(params)
            else:
                data, status, headers = error('Not found', 404)

            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode())

# ── Start ───────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f"╔══════════════════════════════════════════╗")
    print(f"║  UK Address Verifier API                ║")
    print(f"║  Listening on http://0.0.0.0:{PORT}       ║")
    print(f"║                                          ║")
    print(f"║  Endpoints:                              ║")
    print(f"║    GET /api/verify?postcode=&number=     ║")
    print(f"║    GET /api/lookup?postcode=             ║")
    print(f"║    GET /api/search?q=                    ║")
    print(f"║    GET /api/street-addresses?street=     ║")
    print(f"║    POST /api/save-address               ║")
    print(f"║    GET /api/saved-lookup?postcode=      ║")
    print(f"║    GET /api/nominatim?q=                ║")
    print(f"║    GET /api/here?q=                     ║")
    print(f"║    GET /api/health                       ║")
    print(f"╚══════════════════════════════════════════╝")
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
