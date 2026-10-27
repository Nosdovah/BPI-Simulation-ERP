-- SQLite schema (relational in-memory simulator) — blueprint v2.
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS variants (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sku         TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  gender      TEXT CHECK(gender IN ('MEN','UNISEX','WOMEN')),
  price_idr   INTEGER NOT NULL,
  is_bundle   INTEGER NOT NULL DEFAULT 0,        -- baseline: 0; to-be: 1
  is_active   INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT DEFAULT (datetime('now'))
);

-- Modul A — relasi BOM (sku_bom): bundle -> komponen + multiplier.
-- BASELINE: 0 baris (cacat). TO-BE: terisi dari config WMS.
CREATE TABLE IF NOT EXISTS sku_bom (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  bundle_sku     TEXT NOT NULL,
  component_sku  TEXT NOT NULL,
  multiplier_qty INTEGER NOT NULL CHECK(multiplier_qty > 0),
  source         TEXT DEFAULT 'PERPACK_API',
  synced_at      TEXT,
  UNIQUE(bundle_sku, component_sku)
);

CREATE TABLE IF NOT EXISTS warehouses (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stocks (
  variant_sku  TEXT NOT NULL,
  warehouse_id INTEGER NOT NULL,
  available    INTEGER NOT NULL DEFAULT 0,
  on_order     INTEGER NOT NULL DEFAULT 0,
  reserved     INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (variant_sku, warehouse_id)
);

-- BASELINE: static manual quota for bundles (di-nonaktifkan di to-be).
CREATE TABLE IF NOT EXISTS bundle_quota_manual (
  bundle_sku TEXT PRIMARY KEY,
  qty        INTEGER NOT NULL,
  set_by     TEXT,
  set_at     TEXT DEFAULT (datetime('now'))
);

-- ===== Modul B — Order & Status =====
CREATE TABLE IF NOT EXISTS orders (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  order_ref        TEXT UNIQUE NOT NULL,
  wms_order_id     INTEGER,
  channel          TEXT DEFAULT 'WEBSTORE',
  status           TEXT NOT NULL,
  actor_last       TEXT,          -- SYSTEM | STAFF:<id> | COURIER
  courier_name     TEXT,
  courier_service  TEXT,
  tracking_number  TEXT,
  total_idr        INTEGER NOT NULL DEFAULT 0,
  paid_at          TEXT,
  processing_at    TEXT,
  sent_at          TEXT,
  delivered_at     TEXT,
  created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
  order_id       INTEGER REFERENCES orders(id) ON DELETE CASCADE,
  variant_sku    TEXT REFERENCES variants(sku),
  qty            INTEGER NOT NULL,
  unit_price_idr INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_status_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id    INTEGER REFERENCES orders(id),
  from_status TEXT,
  to_status   TEXT NOT NULL,
  actor       TEXT NOT NULL,   -- SYSTEM | STAFF | COURIER
  source      TEXT NOT NULL,   -- WEBHOOK | CHECKOUT | MANUAL_UI | TRACKER
  event_id    TEXT,
  occurred_at TEXT DEFAULT (datetime('now'))
);

-- ===== Modul C — telemetri webhook =====
CREATE TABLE IF NOT EXISTS wms_webhook_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id      TEXT,
  lane          TEXT,              -- 4.1 | 4.2
  received_at   TEXT DEFAULT (datetime('now')),
  raw_body      TEXT,
  payload_valid INTEGER DEFAULT 0,
  mapper_rejected INTEGER DEFAULT 0,
  reject_reason TEXT,
  signature_ok  INTEGER,
  processed_at  TEXT
);

-- Log pengiriman outbound webhook (untuk /api/v1/audit/webhook-logs)
CREATE TABLE IF NOT EXISTS webhook_delivery_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT,
  event        TEXT,
  lane         TEXT,
  attempt      INTEGER,
  http_status  INTEGER,
  signature    TEXT,
  sent_at      TEXT DEFAULT (datetime('now'))
);