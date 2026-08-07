"""Database layer backed by Turso (libSQL) in production, a local SQLite file in dev.

Two kinds of table, same split as the receivables tracker:

  synced  (customers, documents)         — wiped and rewritten on every Odoo sync
  local   (everything else)              — the actual collector's work: contacts,
                                            visits, reconciliations, locations, the
                                            weekly plan, the script bank

Turso/libSQL speaks the SQLite dialect, so the schema and queries are the familiar
SQLite ones. What differs is the client: libsql_client talks HTTP to a remote
database, and its result objects are not sqlite3.Row, so Row/Result below
reproduce that interface (row['col'], dict(row), .fetchone()) so the rest of the
app doesn't need to know the difference.

Lessons carried over from the two earlier deployments of this pattern:
  - Normalise libsql:// to https:// (websocket mode fails in some hosting
    environments; HTTP suits a stateless function better anyway).
  - Batch multi-row writes in large chunks (8000), not small ones — round-trip
    count dominates cost far more than payload size.
  - select_many() batches independent SELECTs into one round trip too, so a
    page that needs five queries doesn't pay five sequential latencies.

Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN to point at a hosted database; leave
them unset to fall back to a local tracker.db file for local development.
"""

import os
import re
from datetime import datetime

import libsql_client

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'tracker.db')

TURSO_URL = os.environ.get('TURSO_DATABASE_URL')
TURSO_TOKEN = os.environ.get('TURSO_AUTH_TOKEN')

# See db.py in the sales-tracker app for how this number was chosen: a single
# batch of ~14,700 single-row INSERTs took 11.5s; the same rows chunked at 400
# per round trip took ~60s. Round-trip count is the cost, not payload size.
BATCH_CHUNK = 8000

STATUSES = [
    ('new', 'New'),
    ('contacted', 'Contacted'),
    ('promised', 'Promised to Pay'),
    ('partial', 'Partial Payment'),
    ('disputed', 'Disputed'),
    ('escalated', 'Escalated'),
    ('legal', 'Legal / Write-off'),
    ('resolved', 'Resolved'),
]
STATUS_KEYS = {k for k, _ in STATUSES}

CHANNELS = [
    ('visit', 'In-person visit'),
    ('call', 'Phone call'),
    ('whatsapp', 'WhatsApp'),
    ('email', 'Email'),
]
CHANNEL_KEYS = {k for k, _ in CHANNELS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    partner_id INTEGER PRIMARY KEY,
    name    TEXT NOT NULL DEFAULT '',
    phone   TEXT DEFAULT '',
    mobile  TEXT DEFAULT '',
    email   TEXT DEFAULT '',
    vat     TEXT DEFAULT '',
    city    TEXT DEFAULT '',
    company TEXT DEFAULT '',
    area    TEXT DEFAULT '',
    payment_term TEXT DEFAULT '',
    term_days    INTEGER DEFAULT NULL,
    credit_limit REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    line_id    INTEGER PRIMARY KEY,
    partner_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL DEFAULT 0,
    company    TEXT NOT NULL DEFAULT '',
    doc        TEXT DEFAULT '',
    ref        TEXT DEFAULT '',
    journal    TEXT DEFAULT '',
    inv_date   TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    original   REAL NOT NULL DEFAULT 0,
    residual   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_partner ON documents(partner_id);
CREATE INDEX IF NOT EXISTS idx_documents_due ON documents(due_date);

CREATE TABLE IF NOT EXISTS sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at  TEXT NOT NULL,
    lines      INTEGER NOT NULL DEFAULT 0,
    customers  INTEGER NOT NULL DEFAULT 0,
    total_open REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Current state per customer: where they are, what's owed to be followed up on.
-- Never touched by a sync. The equivalent of the receivables tracker's
-- `followups`, extended with a map location for route planning.
CREATE TABLE IF NOT EXISTS customer_meta (
    partner_id       INTEGER PRIMARY KEY,
    lat              REAL,
    lng              REAL,
    location_note    TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'new',
    needs_visit      INTEGER NOT NULL DEFAULT 0,
    -- Part of this collector's assigned book, not just anyone in the synced
    -- Odoo receivables. Set by matching a name list (see assign.py) against
    -- the synced customers the same way the receivables tracker flags
    -- agency-assigned accounts.
    assigned         INTEGER NOT NULL DEFAULT 0,
    assigned_source  TEXT NOT NULL DEFAULT '',
    -- Handed off to a collection agency -- still a real receivable (stays in
    -- every total), but the collector is done actively chasing it: excluded
    -- from the weekly plan, broken-promise/follow-up alerts, and hidden from
    -- the customer list by default.
    agency           INTEGER NOT NULL DEFAULT 0,
    agency_date      TEXT NOT NULL DEFAULT '',
    agency_note      TEXT NOT NULL DEFAULT '',
    promise_date     TEXT NOT NULL DEFAULT '',
    promise_amount   REAL NOT NULL DEFAULT 0,
    next_action_date TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL DEFAULT ''
);

-- Contact people at each customer -- a collector deals with a specific person,
-- not an abstract company, and the same business can have several over time.
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id INTEGER NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    position   TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_partner ON contacts(partner_id);

-- The actual collector's diary: one row per call or visit, who was spoken to,
-- what they said, what was promised. customer_meta is updated alongside this
-- to reflect the latest state, the same way notes+followups worked before,
-- just merged into a single richer action instead of two separate ones.
CREATE TABLE IF NOT EXISTS visits (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id       INTEGER NOT NULL,
    contact_id       INTEGER,
    channel          TEXT NOT NULL DEFAULT 'visit',
    status           TEXT NOT NULL DEFAULT 'new',
    outcome          TEXT NOT NULL DEFAULT '',
    promise_date     TEXT NOT NULL DEFAULT '',
    promise_amount   REAL NOT NULL DEFAULT 0,
    next_action_date TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_partner ON visits(partner_id);
CREATE INDEX IF NOT EXISTS idx_visits_created ON visits(created_at);

-- Signed account-reconciliation proof. This is the strongest evidence a
-- collector can hold against a disputed balance, so the photo lives right
-- next to the amount and date it was signed for.
CREATE TABLE IF NOT EXISTS reconciliations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id      INTEGER NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    reconciled_date TEXT NOT NULL,
    signed_by       TEXT NOT NULL DEFAULT '',
    signed_position TEXT NOT NULL DEFAULT '',
    image_base64    TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recon_partner ON reconciliations(partner_id);

-- The weekly route plan. Persisted rather than only ever computed on the fly,
-- so a suggested plan can be adjusted and then stays put instead of
-- reshuffling every time the page reloads.
CREATE TABLE IF NOT EXISTS visit_plan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL,
    planned_date TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'planned',
    note         TEXT NOT NULL DEFAULT '',
    -- Why this stop is on this day -- the reasoning behind a recommended
    -- visit (biggest overdue balance, a promise date landing this week, an
    -- explicit note about a planned visit, never having been contacted...).
    -- Blank for a stop added by hand or by the plain proximity generator,
    -- which has nothing to say beyond the route itself.
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE(partner_id, planned_date)
);
CREATE INDEX IF NOT EXISTS idx_plan_date ON visit_plan(planned_date);

-- Arabic script bank: what to say back for common things customers say.
-- Seeded with a starting set, see scripts_seed.py -- is_custom marks entries
-- the collector added themselves, so a reseed never overwrites their own.
CREATE TABLE IF NOT EXISTS scripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT NOT NULL DEFAULT '',
    trigger_text TEXT NOT NULL DEFAULT '',
    script_ar    TEXT NOT NULL DEFAULT '',
    tip          TEXT NOT NULL DEFAULT '',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    is_custom    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scripts_category ON scripts(category);
"""


# --------------------------------------------------------------------------- Row/Result

class Row:
    """Mimics sqlite3.Row: index or column-name access, and dict(row) support."""
    __slots__ = ('_cols', '_vals')

    def __init__(self, columns, values):
        self._cols = columns
        self._vals = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._cols.index(key)]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __repr__(self):
        return repr(dict(zip(self._cols, self._vals)))


class Result:
    def __init__(self, result_set):
        self._rs = result_set

    def fetchall(self):
        cols = self._rs.columns
        return [Row(cols, list(r)) for r in self._rs.rows]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._rs.last_insert_rowid


# --------------------------------------------------------------------------- Connection

class Conn:
    """Thin wrapper around libsql_client.ClientSync with a sqlite3-like surface."""

    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=None):
        rs = self._client.execute(sql, params if params is not None else [])
        return Result(rs)

    def executemany(self, sql, seq):
        seq = list(seq)
        if not seq:
            return
        stmts = [libsql_client.Statement(sql, p) for p in seq]
        for i in range(0, len(stmts), BATCH_CHUNK):
            self._client.batch(stmts[i:i + BATCH_CHUNK])

    def select_many(self, queries):
        """Run several independent SELECTs in one HTTP round trip.

        queries: list of (sql, params). Returns a list of Result, same order.
        Never chunked — a page needing a handful of SELECTs is nowhere near
        BATCH_CHUNK, and chunking a read would just reintroduce the extra
        round trips this exists to avoid.
        """
        stmts = [libsql_client.Statement(sql, params) for sql, params in queries]
        result_sets = self._client.batch(stmts)
        return [Result(rs) for rs in result_sets]

    def batch(self, statements):
        stmts = [libsql_client.Statement(s[0], s[1]) if isinstance(s, tuple)
                 else libsql_client.Statement(s, [])
                 for s in statements]
        for i in range(0, len(stmts), BATCH_CHUNK):
            self._client.batch(stmts[i:i + BATCH_CHUNK])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        self._client.close()


def connect():
    if TURSO_URL:
        url = re.sub(r'^libsql://', 'https://', TURSO_URL)
        client = libsql_client.create_client_sync(url, auth_token=TURSO_TOKEN)
    else:
        client = libsql_client.create_client_sync(f'file:{DB_PATH}')
    return Conn(client)


# Columns added to customer_meta after it already held real collector data
# (assignments, statuses, locations) — CREATE TABLE IF NOT EXISTS is a no-op
# against an existing table, so a brand-new column needs an explicit ALTER
# TABLE instead, the same way the receivables tracker migrates old databases
# forward without losing anyone's follow-up history.
MIGRATIONS = {
    'customer_meta': [
        ('agency', 'INTEGER NOT NULL DEFAULT 0'),
        ('agency_date', "TEXT NOT NULL DEFAULT ''"),
        ('agency_note', "TEXT NOT NULL DEFAULT ''"),
    ],
    'visit_plan': [
        ('reason', "TEXT NOT NULL DEFAULT ''"),
    ],
}


def init():
    """Create every table and index if it does not already exist, add any
    columns an older database is missing, then seed the script bank."""
    conn = connect()
    try:
        statements = [st.strip() for st in SCHEMA.split(';') if st.strip()]
        tables = [st for st in statements if 'CREATE INDEX' not in st.upper()]
        indexes = [st for st in statements if 'CREATE INDEX' in st.upper()]

        for st in tables:
            conn.execute(st)
        for table, columns in MIGRATIONS.items():
            existing = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})', []).fetchall()}
            if not existing:
                continue
            for name, spec in columns:
                if name not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {spec}')
        for st in indexes:
            conn.execute(st)

        import scripts_seed
        scripts_seed.seed_if_empty(conn)
    finally:
        conn.close()


def get_setting(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key = ?', [key]).fetchone()
    return row['value'] if row else default


def set_setting(conn, key, value):
    conn.execute(
        'INSERT INTO settings (key, value) VALUES (?,?)'
        ' ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        [key, str(value)],
    )


def ensure_meta(conn, partner_id):
    conn.execute(
        'INSERT OR IGNORE INTO customer_meta (partner_id, updated_at) VALUES (?,?)',
        [partner_id, datetime.now().isoformat(timespec='seconds')],
    )


def has_data(conn):
    return conn.execute('SELECT COUNT(*) AS n FROM documents').fetchone()['n'] > 0
