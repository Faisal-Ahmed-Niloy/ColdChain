-- ColdChain SQLite schema. Times are UTC epoch seconds (REAL).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'pharmacist', 'inspector')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);

-- One row per fridge, holding its live state plus AI outputs.
CREATE TABLE IF NOT EXISTS fridges (
    id               TEXT PRIMARY KEY,           -- e.g. FRIDGE-A
    name             TEXT NOT NULL,
    location         TEXT,
    capacity_units   INTEGER NOT NULL DEFAULT 500,
    device_id        TEXT UNIQUE,
    online           INTEGER NOT NULL DEFAULT 0,
    last_seen        REAL,
    state_ts         REAL,                       -- measurement time of the live state
    temp             REAL,
    state            TEXT,
    breach_since     REAL,                       -- measurement time the current breach began
    door             INTEGER,
    door_open_since  REAL,
    comp             INTEGER,
    power            TEXT,
    batt             INTEGER,
    led_latch        INTEGER NOT NULL DEFAULT 0, -- desired red-LED latch (discard)
    led_reported     INTEGER,                    -- latch state the node last reported
    backlog          INTEGER NOT NULL DEFAULT 0,
    rssi             INTEGER,
    last_proc_ts     REAL,                       -- heat-budget integrator position
    last_proc_temp   REAL,
    last_door_alert_for REAL,                    -- door episode already alerted
    pred_temp_30     REAL,
    pred_upper_30    REAL,
    pred_warning     INTEGER NOT NULL DEFAULT 0,
    pred_note        TEXT,
    pred_ts          REAL,
    health_score     REAL,
    health_baseline  REAL,
    health_note      TEXT,
    health_ts        REAL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    fridge_id  TEXT NOT NULL REFERENCES fridges(id),
    hmac_key   TEXT NOT NULL,
    boot       TEXT,
    last_seq   INTEGER NOT NULL DEFAULT 0,
    accepted   INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    gaps       INTEGER NOT NULL DEFAULT 0,
    rejected   INTEGER NOT NULL DEFAULT 0
);

-- Sensor readings, hash-chained (prev_hash/hash) and sealed with a server key.
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY,
    device_id   TEXT NOT NULL,
    fridge_id   TEXT NOT NULL,
    boot        TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    ts          REAL NOT NULL,
    temp        REAL,
    door        INTEGER NOT NULL,
    comp        INTEGER NOT NULL,
    power       TEXT NOT NULL,
    batt        INTEGER,
    state       TEXT,
    reason      TEXT,
    led         INTEGER,
    buffered    INTEGER NOT NULL DEFAULT 0,
    received_at REAL NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL,
    seal        TEXT NOT NULL,
    UNIQUE (device_id, boot, seq)
);
CREATE INDEX IF NOT EXISTS idx_readings_fridge_ts ON readings (fridge_id, ts);

CREATE TABLE IF NOT EXISTS batches (
    id           INTEGER PRIMARY KEY,
    public_id    TEXT UNIQUE NOT NULL,           -- random, used in the public QR link
    vaccine      TEXT NOT NULL,
    lot_no       TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    expiry_date  TEXT NOT NULL,                  -- YYYY-MM-DD
    fridge_id    TEXT REFERENCES fridges(id),
    exposure_min REAL NOT NULL DEFAULT 0,        -- weighted minutes above 8 C
    status       TEXT NOT NULL DEFAULT 'SAFE',   -- SAFE / AT_RISK / DISCARD
    discarded_at REAL,
    active       INTEGER NOT NULL DEFAULT 1,
    notes        TEXT,
    created_at   REAL NOT NULL,
    created_by   TEXT
);

CREATE TABLE IF NOT EXISTS batch_moves (
    id          INTEGER PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    from_fridge TEXT,
    to_fridge   TEXT,
    ts          REAL NOT NULL,
    by_user     TEXT,
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_moves_batch ON batch_moves (batch_id, ts);

-- Every security-relevant or business action, hash-chained and sealed.
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    details   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL,
    seal      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    device_id TEXT,
    kind      TEXT NOT NULL,
    detail    TEXT,
    topic     TEXT
);

CREATE TABLE IF NOT EXISTS nonces (
    device_id TEXT NOT NULL,
    nonce     TEXT NOT NULL,
    ts        REAL NOT NULL,
    PRIMARY KEY (device_id, nonce)
);

CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    fridge_id TEXT,
    kind      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    message   TEXT NOT NULL,
    delivery  TEXT,                              -- JSON: channel -> result
    ack_by    TEXT,
    ack_ts    REAL
);

CREATE TABLE IF NOT EXISTS relocations (
    id            INTEGER PRIMARY KEY,
    created_ts    REAL NOT NULL,
    updated_ts    REAL NOT NULL,
    source_fridge TEXT NOT NULL,
    reason        TEXT NOT NULL,
    plan          TEXT NOT NULL,                 -- JSON
    status        TEXT NOT NULL,                 -- PENDING / CONFIRMED / DISMISSED / EXPIRED
    decided_by    TEXT,
    decided_ts    REAL
);

CREATE TABLE IF NOT EXISTS predictions (
    id         INTEGER PRIMARY KEY,
    fridge_id  TEXT NOT NULL,
    ts         REAL NOT NULL,
    temp_now   REAL,
    pred_temp  REAL,
    pred_upper REAL,
    health     REAL
);
CREATE INDEX IF NOT EXISTS idx_pred_fridge_ts ON predictions (fridge_id, ts);
