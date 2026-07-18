-- PostgreSQL schema for UK Address Verifier

-- UK-wide postcodes from Code-Point Open
CREATE TABLE IF NOT EXISTS postcodes (
    postcode    VARCHAR(10) PRIMARY KEY,
    easting     DOUBLE PRECISION,
    northing    DOUBLE PRECISION
);

-- UK-wide roads from OS Open Names (Named Roads with MBR)
CREATE TABLE IF NOT EXISTS roads (
    id              SERIAL PRIMARY KEY,
    os_id           TEXT,
    name            TEXT NOT NULL,
    local_type      TEXT,
    easting         DOUBLE PRECISION,
    northing        DOUBLE PRECISION,
    mbr_xmin        DOUBLE PRECISION,
    mbr_ymin        DOUBLE PRECISION,
    mbr_xmax        DOUBLE PRECISION,
    mbr_ymax        DOUBLE PRECISION,
    postcode_district TEXT,
    populated_place TEXT,
    district_name   TEXT,
    county_name     TEXT,
    region_name     TEXT,
    country_name    TEXT
);

-- Legacy: Wirral postcode->road MBR matches
CREATE TABLE IF NOT EXISTS postcode_roads (
    id          SERIAL PRIMARY KEY,
    postcode    VARCHAR(10) NOT NULL REFERENCES postcodes(postcode),
    street      TEXT NOT NULL,
    pcd         VARCHAR(10),
    place       TEXT,
    distance_m  DOUBLE PRECISION,
    in_mbr      BOOLEAN DEFAULT FALSE
);

-- Legacy: Wirral OSM addresses
CREATE TABLE IF NOT EXISTS addresses (
    id          SERIAL PRIMARY KEY,
    postcode    VARCHAR(10) NOT NULL REFERENCES postcodes(postcode),
    house_num   TEXT,
    street      TEXT NOT NULL,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION
);

-- Legacy: Wirral street summary
CREATE TABLE IF NOT EXISTS streets (
    street      TEXT PRIMARY KEY,
    addr_count  INTEGER DEFAULT 0
);

-- Indexes for UK-wide roads (fast MBR lookups)
CREATE INDEX IF NOT EXISTS idx_roads_mbr_x   ON roads(mbr_xmin, mbr_xmax);
CREATE INDEX IF NOT EXISTS idx_roads_mbr_y   ON roads(mbr_ymin, mbr_ymax);
CREATE INDEX IF NOT EXISTS idx_roads_name    ON roads(name);
CREATE INDEX IF NOT EXISTS idx_roads_pcd     ON roads(postcode_district);

-- Indexes for postcodes
CREATE INDEX IF NOT EXISTS idx_postcode_east ON postcodes(easting);
CREATE INDEX IF NOT EXISTS idx_postcode_north ON postcodes(northing);

-- Legacy indexes
CREATE INDEX IF NOT EXISTS idx_pc_roads_pc    ON postcode_roads(postcode);
CREATE INDEX IF NOT EXISTS idx_addr_pc       ON addresses(postcode);
CREATE INDEX IF NOT EXISTS idx_addr_street   ON addresses(street);
CREATE INDEX IF NOT EXISTS idx_addr_hnum     ON addresses(house_num);
CREATE INDEX IF NOT EXISTS idx_roads_mbr     ON postcode_roads(in_mbr) WHERE in_mbr = TRUE;

-- Customer-saved addresses (learned from verification)
CREATE TABLE IF NOT EXISTS saved_addresses (
    id          SERIAL PRIMARY KEY,
    postcode    VARCHAR(10) NOT NULL REFERENCES postcodes(postcode),
    house_num   TEXT NOT NULL,
    street      TEXT NOT NULL,
    source      TEXT DEFAULT 'customer',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(postcode, house_num, street)
);
CREATE INDEX IF NOT EXISTS idx_saved_pc ON saved_addresses(postcode);
