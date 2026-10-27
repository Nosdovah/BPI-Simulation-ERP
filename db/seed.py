# -*- coding: utf-8 -*-
"""Seeder ERP/WMS (blueprint v2) — BOM, order lifecycle, webhook.

Baseline (OSR_MODE=baseline): sku_bom 0 baris + is_bundle FALSE + kuota statis
  -> phantom stock (cacat SoW). To-be (OSR_MODE=tobe): sku_bom terisi + is_bundle
  TRUE -> engine MIN(floor(stok/multiplier)) aktif.
"""
import json, os, random, sqlite3
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data", "base_catalog.json")
SCHEMA = os.path.join(BASE, "db", "schema.sql")
WMS_DB = os.path.join(BASE, "db", "wms.db")

TOTAL_VARIANTS = 3696
TOTAL_STOCK_ROWS = 7325
SEED = 20260918
FOCAL_BUNDLE = "Sanctum+Noctis-20ml-A1"     # SKU persis blueaprint v2 §5.1
FOCAL_COMPONENTS = [("M2291", 1, 11927), ("M2292", 1, 10932)]

TOTAL_ORDERS = 139
STUCK_AT_SENT = 82
STUCK_GT_2D = 21

OSR_MODE = os.environ.get("OSR_MODE", "baseline")

WAREHOUSES = [(8, "Warehouse Bogor"), (2, "Warehouse Jakarta"), (3, "Warehouse Surabaya")]


def now_iso(delta_days=0):
    return (datetime.utcnow() - timedelta(days=delta_days)).isoformat()


def main():
    if os.path.exists(WMS_DB):
        os.remove(WMS_DB)
    conn = sqlite3.connect(WMS_DB)
    conn.executescript(open(SCHEMA).read())
    cur = conn.cursor()
    cur.executemany("INSERT INTO warehouses (id,name) VALUES (?,?)", WAREHOUSES)

    rng = random.Random(SEED)
    bases = json.load(open(DATA))

    is_bundle_flag = 1 if OSR_MODE == "tobe" else 0

    def ins_variant(sku, name, gender, price, is_bundle=0):
        cur.execute("INSERT INTO variants (sku,name,gender,price_idr,is_bundle) VALUES (?,?,?,?,?)",
                    (sku, name, gender, price, is_bundle))

    ins_variant("M2291", "Sanctum Eau de Parfum 20mL", "MEN", 89000)
    ins_variant("M2292", "Noctis Eau de Parfum 20mL", "MEN", 89000)
    ins_variant(FOCAL_BUNDLE, "Sanctum + Noctis - Bundling 2 Botol 20mL", "UNISEX", 169000,
                is_bundle=is_bundle_flag)

    variant_skus = ["M2291", "M2292", FOCAL_BUNDLE]
    n = 3
    while n < TOTAL_VARIANTS:
        b = bases[n % len(bases)]
        size = b["sizes"][(n // len(bases)) % len(b["sizes"])]
        ins_variant(f"M{3000 + n}", f"{b['name']} {size}", b["gender"],
                    min(189000, b["base_price"] + (n % 7) * 7000))
        variant_skus.append(f"M{3000 + n}")
        n += 1

    # stok fisik komponen (exact) + bundle (kuota statis di baseline)
    cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,11927)", ("M2291",))
    cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,10932)", ("M2292",))
    cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,10932)", (FOCAL_BUNDLE,))
    stock_rows = 3
    for sku in variant_skus[3:]:
        if stock_rows >= TOTAL_STOCK_ROWS:
            break
        cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,?)",
                    (sku, rng.randint(0, 20000)))
        stock_rows += 1
        if stock_rows < TOTAL_STOCK_ROWS:
            cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,2,?)",
                        (sku, rng.randint(0, 15000)))
            stock_rows += 1

    # Modul A — relasi BOM
    if OSR_MODE == "tobe":
        for comp_sku, mult, _stk in FOCAL_COMPONENTS:
            cur.execute("INSERT INTO sku_bom (bundle_sku,component_sku,multiplier_qty,source,synced_at) "
                        "VALUES (?,?,?,?,datetime('now'))",
                        (FOCAL_BUNDLE, comp_sku, mult, "PERPACK_API"))
    else:
        # baseline: kuota statis manual (cacat), sku_bom tetap 0 baris
        cur.execute("INSERT INTO bundle_quota_manual (bundle_sku,qty,set_by) VALUES (?,?,?)",
                    (FOCAL_BUNDLE, 10932, "STAFF:manual"))

    # ---- Modul B: orders (baseline snapshot) -------------------------------
    def ins_order(idx, status, actor_last, sent_days=None, completed=False):
        cur.execute(
            "INSERT INTO orders (order_ref,wms_order_id,channel,status,actor_last,"
            "courier_name,courier_service,tracking_number,total_idr,paid_at,sent_at,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ORD-WEBSTORE-PROD-{idx}", 5310000 + idx, "WEBSTORE", status, actor_last,
             "sicepat", "HALU", f"0043{rng.randint(10000000, 99999999)}",
             rng.randint(34000, 189000), now_iso(5),
             now_iso(sent_days) if sent_days else None,
             now_iso(0) if completed else None))
        sku = "M2291" if idx % 2 else "M2292"
        cur.execute("INSERT INTO order_items (order_id,variant_sku,qty,unit_price_idr) "
                    "VALUES (last_insert_rowid(),?,1,89000)", (sku,))

    for i in range(1, STUCK_AT_SENT + 1):
        ins_order(i, "sent", "COURIER", sent_days=(3 if i <= STUCK_GT_2D else 1))
    for i in range(STUCK_AT_SENT + 1, TOTAL_ORDERS + 1):
        ins_order(i, "completed", "STAFF:manual", completed=True)

    # ---- telemetri webhook baseline (103 event) -------------------------------
    ev = [("{}", 0, 0, None)] * 82
    for status in (["paid"] * 8 + ["process"] * 6 + ["cancelled"] * 6):
        ev.append((json.dumps({"event": "order.status_updated",
                               "data": {"current_status": status}}), 1, 0, None))
    ev.append((json.dumps({"event": "order.status_updated",
                           "data": {"current_status": "on_process"}}), 0, 1, "UNKNOWN_ENUM"))
    rng.shuffle(ev)
    for i, (raw, valid, rej, reason) in enumerate(ev):
        cur.execute(
            "INSERT INTO wms_webhook_events (event_id,lane,raw_body,payload_valid,mapper_rejected,reject_reason) "
            "VALUES (?,?,?,?,?,?)",
            (f"evt_wms_20260918_{i:06d}", "4.2", raw, valid, rej, reason))

    conn.commit()

    # ---- summary ----
    n_v = cur.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
    n_s = cur.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    n_bom = cur.execute("SELECT COUNT(*) FROM sku_bom").fetchone()[0]
    n_stuck = cur.execute("SELECT COUNT(*) FROM orders WHERE status='sent'").fetchone()[0]
    n_comp = cur.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
    n_gt2 = cur.execute("SELECT COUNT(*) FROM orders WHERE status='sent' AND sent_at < ?",
                        (now_iso(2),)).fetchone()[0]
    n_ev = cur.execute("SELECT COUNT(*) FROM wms_webhook_events").fetchone()[0]
    n_valid = cur.execute("SELECT COUNT(*) FROM wms_webhook_events WHERE payload_valid=1").fetchone()[0]
    conn.close()

    dpmo = (82 + 1) / 103 * 1_000_000
    print(f"[seed] mode={OSR_MODE} | variants={n_v} | stocks={n_s} | sku_bom={n_bom}")
    print(f"[seed] Modul A: kuota statis 10.932 vs M2291 11.927 / M2292 10.932 -> phantom {11927-10932:,}")
    print(f"[seed] Modul B: {n_stuck} macet 'sent' (>2hri {n_gt2}) | {n_comp} completed manual")
    print(f"[seed] Modul C: {n_ev} event | valid {n_valid} ({n_valid/n_ev*100:.1f}%) | DPMO {dpmo:,.0f}")
    print(f"[seed] DB -> {WMS_DB}")


if __name__ == "__main__":
    main()