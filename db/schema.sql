-- SQLite schema — faithful replica of Perpack/Octarine WMS (octarine-api.perpack.id/v1).
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS channels (
  id     INTEGER PRIMARY KEY,
  name   TEXT,
  image  TEXT,
  status TEXT
);

CREATE TABLE IF NOT EXISTS warehouses (
  id             INTEGER PRIMARY KEY,
  name           TEXT,
  address        TEXT,
  contact_phone  TEXT,
  contact_email  TEXT,
  warehouse_type TEXT,
  status         TEXT,
  locations_json TEXT
);

CREATE TABLE IF NOT EXISTS product_groups (
  id               INTEGER PRIMARY KEY,
  name             TEXT,
  sku              TEXT,
  description      TEXT,
  thumbnail        TEXT,
  brand_json       TEXT,
  client_json      TEXT,
  integration_json TEXT,
  sell_price_min   INTEGER,
  sell_price_max   INTEGER,
  weight           REAL,
  height           REAL,
  width            REAL,
  length           REAL,
  created_at       TEXT,
  update_at        TEXT
);

CREATE TABLE IF NOT EXISTS product_variants (
  id               INTEGER PRIMARY KEY,
  product_group_id INTEGER,
  name             TEXT,
  sku              TEXT,
  sku_code         TEXT,
  alias            TEXT,
  sell_price       INTEGER,
  purchase_price   INTEGER,
  variation_label  TEXT,
  variation_value  TEXT,
  weight           REAL,
  height           REAL,
  width            REAL,
  length           REAL,
  type             TEXT,
  is_bundle        INTEGER NOT NULL DEFAULT 0,
  stock_type       TEXT,
  stock_sort       TEXT
);

CREATE TABLE IF NOT EXISTS variant_components (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id     INTEGER,
  product_id     INTEGER,
  component_sku  TEXT,
  multiplier_qty INTEGER
);

CREATE TABLE IF NOT EXISTS variant_stocks (
  variant_id     INTEGER,
  warehouse_id   INTEGER,
  warehouse_name TEXT,
  available      INTEGER,
  on_order       INTEGER,
  reserved       INTEGER
);

CREATE TABLE IF NOT EXISTS variant_images (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER,
  url        TEXT,
  filename   TEXT
);

CREATE TABLE IF NOT EXISTS orders (
  id                        INTEGER PRIMARY KEY,
  reference_no              TEXT,
  order_at                  TEXT,
  created_at                TEXT,
  process_at                TEXT,
  returned_at               TEXT,
  deadline_at               TEXT,
  qty                       INTEGER,
  amount                    INTEGER,
  cogs                      INTEGER,
  shipping_fee              INTEGER,
  cod_fee                   INTEGER,
  courier                   TEXT,
  courier_label             TEXT,
  warehouse_name            TEXT,
  channel_name              TEXT,
  channel_image             TEXT,
  channel_id                INTEGER,
  customer_username         TEXT,
  customer_name             TEXT,
  customer_phone            TEXT,
  customer_email            TEXT,
  picklist_id               INTEGER,
  picklist_no               TEXT,
  delivery_note_id          INTEGER,
  delivery_note_no          TEXT,
  awb                       TEXT,
  is_shipping_label_printed INTEGER,
  printed                   INTEGER,
  product_summary           TEXT,
  integration_id            INTEGER,
  integration_store         TEXT,
  is_cod                    INTEGER,
  is_pre_order              INTEGER,
  is_instant                INTEGER,
  is_booking                INTEGER,
  is_fake_order             INTEGER,
  status                    TEXT,
  status_mp                 TEXT,
  sent_at                   TEXT,
  updated_at                TEXT
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    TEXT,
  event       TEXT,
  payload     TEXT,
  applied     INTEGER DEFAULT 0,
  effect      TEXT,
  received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_variants_group ON product_variants(product_group_id);
CREATE INDEX IF NOT EXISTS idx_components_variant ON variant_components(variant_id);
CREATE INDEX IF NOT EXISTS idx_stocks_variant ON variant_stocks(variant_id);
CREATE INDEX IF NOT EXISTS idx_images_variant ON variant_images(variant_id);
CREATE INDEX IF NOT EXISTS idx_orders_channel ON orders(channel_id);