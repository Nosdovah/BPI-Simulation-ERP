# -*- coding: utf-8 -*-
"""Seeder ERP/WMS (blueprint v2, TO-BE).

Kondisi sehat: BOM terisi (sku_bom), is_bundle aktif, stok fisik komponen
akurat, dan beberapa order contoh pada lifecycle normal (tanpa macet).
"""
import json, os, random, sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data", "base_catalog.json")
SCHEMA = os.path.join(BASE, "db", "schema.sql")
WMS_DB = os.path.join(BASE, "db", "wms.db")

TOTAL_VARIANTS = 3696
TOTAL_STOCK_ROWS = 7325
SEED = 20260918
FOCAL_BUNDLE = "Sanctum+Noctis-20ml-A1"     # SKU persis blueprint v2 §5.1
FOCAL_COMPONENTS = [("M2291", 1, 11927), ("M2292", 1, 10932)]

# contoh order sehat (lifecycle normal, bukan macet)
DEMO_ORDERS = [
    ("ORD-DEMO-0001", "completed", "COURIER", "SCP00001001"),
    ("ORD-DEMO-0002", "sent", "COURIER", "SCP00001002"),
    ("ORD-DEMO-0003", "process", "SYSTEM", None),
]

WAREHOUSES = [(8, "Warehouse Bogor"), (2, "Warehouse Jakarta"), (3, "Warehouse Surabaya")]


def main():
    if os.path.exists(WMS_DB):
        os.remove(WMS_DB)
    conn = sqlite3.connect(WMS_DB)
    conn.executescript(open(SCHEMA).read())
    cur = conn.cursor()
    cur.executemany("INSERT INTO warehouses (id,name) VALUES (?,?)", WAREHOUSES)

    rng = random.Random(SEED)
    bases = json.load(open(DATA))

    def ins_variant(sku, name, gender, price, is_bundle=0):
        cur.execute("INSERT INTO variants (sku,name,gender,price_idr,is_bundle) VALUES (?,?,?,?,?)",
                    (sku, name, gender, price, is_bundle))

    ins_variant("M2291", "Sanctum Eau de Parfum 20mL", "MEN", 89000)
    ins_variant("M2292", "Noctis Eau de Parfum 20mL", "MEN", 89000)
    ins_variant(FOCAL_BUNDLE, "Sanctum + Noctis - Bundling 2 Botol 20mL", "UNISEX", 169000, is_bundle=1)

    variant_skus = ["M2291", "M2292", FOCAL_BUNDLE]
    n = 3
    while n < TOTAL_VARIANTS:
        b = bases[n % len(bases)]
        size = b["sizes"][(n // len(bases)) % len(b["sizes"])]
        ins_variant(f"M{3000 + n}", f"{b['name']} {size}", b["gender"],
                    min(189000, b["base_price"] + (n % 7) * 7000))
        variant_skus.append(f"M{3000 + n}")
        n += 1

    # stok fisik komponen (exact); bundle tidak punya stok fisik (virtual kitting)
    cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,11927)", ("M2291",))
    cur.execute("INSERT INTO stocks (variant_sku,warehouse_id,available) VALUES (?,8,10932)", ("M2292",))
    stock_rows = 2
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

    # Modul A — relasi BOM (aktif): 1 bundle = 1 M2291 + 1 M2292
    for comp_sku, mult, _stk in FOCAL_COMPONENTS:
        cur.execute("INSERT INTO sku_bom (bundle_sku,component_sku,multiplier_qty,source,synced_at) "
                    "VALUES (?,?,?,?,datetime('now'))",
                    (FOCAL_BUNDLE, comp_sku, mult, "PERPACK_API"))

    # Modul B — contoh order sehat
    for idx, (oref, status, actor, tracking) in enumerate(DEMO_ORDERS, start=1):
        cur.execute(
            "INSERT INTO orders (order_ref,wms_order_id,channel,status,actor_last,"
            "courier_name,courier_service,tracking_number,total_idr,paid_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now','-1 hours'))",
            (oref, 5310000 + idx, "WEBSTORE", status, actor,
             "sicepat", "HALU", tracking, 169000))
        sku = "M2291" if idx % 2 else "M2292"
        cur.execute("INSERT INTO order_items (order_id,variant_sku,qty,unit_price_idr) "
                    "VALUES (last_insert_rowid(),?,1,89000)", (sku,))

    conn.commit()

    n_v = cur.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
    n_s = cur.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    n_bom = cur.execute("SELECT COUNT(*) FROM sku_bom").fetchone()[0]
    n_o = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()

    print(f"[seed] mode=tobe | variants={n_v} | stocks={n_s} | sku_bom={n_bom} | demo_orders={n_o}")
    print(f"[seed] BOM: {FOCAL_BUNDLE} = min(11927, 10932) = 10932")
    print(f"[seed] DB -> {WMS_DB}")


if __name__ == "__main__":
    main()