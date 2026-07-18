# UK Address Finder

CallerID-style UK address lookup API for takeaway POS systems.
Backend: PostgreSQL (Code-Point Open + OS Open Names) with Nominatim & Here Maps fallback.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/lookup?postcode=` | Streets & addresses for a postcode |
| `GET /api/verify?postcode=&number=` | Verify a house number at a postcode |
| `GET /api/search?q=` | Search by street name or area |
| `GET /api/street-addresses?street=` | All house numbers for a street |
| `POST /api/save-address` | Save a customer-verified address |
| `GET /api/saved-lookup?postcode=` | Get saved addresses for a postcode |
| `GET /api/nominatim?q=` | Proxy to OpenStreetMap Nominatim API |
| `GET /api/here?q=` | Proxy to Here Maps API |

## Quick Start

```bash
# Install dependencies
pip install psycopg2-binary

# Create database (requires PostgreSQL running)
createdb uk_addr
psql -d uk_addr -f api/schema.sql

# Load data (requires Code-Point Open & OS Open Names CSV files)
python3 api/load_uk.py

# Start server
python3 api/server.py 5050
```

## Database

The `uk_addr` database contains:
- **postcodes** — 1.7M UK postcodes from Code-Point Open
- **roads** — 880K UK roads from OS Open Names with MBR coordinates
- **saved_addresses** — Customer-learned addresses auto-saved on verification

Verification chain: Local DB → Saved Addresses → Nominatim (OSM) → Here Maps (250K free/mo)
