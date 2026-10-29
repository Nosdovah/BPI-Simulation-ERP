# -*- coding: utf-8 -*-
"""Seeder — replica data Perpack/Octarine (diambil dari API nyata octarine-api.perpack.id).

Sumber: data/perpack_seed.json (channels, warehouses, product_groups+nested variants,
orders channel WEBSTORE). Tidak ada data sintetis.
"""
import json, os, sqlite3
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE, "db", "schema.sql")
WMS_DB = os.path.join(BASE, "db", "wms.db")
SEED_FILE = os.path.join(BASE, "data", "perpack_seed.json")


def norm_ts(s):
    """Normalisasi timestamp -> RFC3339 UTC 'Z'. Go-zero ('0001-01-01…') / kosong -> None."""
    if not s:
        return None
    s = str(s).strip()
    if s.startswith("0001-01-01"):
        return None
    if " " in s and "T" not in s:
        s = s.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00") if s.endswith("Z") else s)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s


def main():
    if not os.path.exists(SEED_FILE):
        raise SystemExit(f"[seed] tidak ada {SEED_FILE} — jalankan fetch data dulu")
    if os.path.exists(WMS_DB):
        os.remove(WMS_DB)

    d = json.load(open(SEED_FILE))
    conn = sqlite3.connect(WMS_DB)
    conn.executescript(open(SCHEMA).read())
    cur = conn.cursor()

    chan_id = {c.get("name"): c.get("id") for c in d.get("channels", [])}

    for c in d.get("channels", []):
        cur.execute("INSERT INTO channels (id,name,image,status) VALUES (?,?,?,?)",
                    (c.get("id"), c.get("name"), c.get("image"), c.get("status")))

    for w in d.get("warehouses", []):
        cur.execute("INSERT INTO warehouses "
                    "(id,name,address,contact_phone,contact_email,warehouse_type,status,locations_json) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (w.get("id"), w.get("name"), w.get("address"), w.get("contact_phone"),
                     w.get("contact_email"), w.get("warehouse_type"), w.get("status"),
                     json.dumps(w.get("locations") or {})))

    for g in d.get("product_groups", []):
        cur.execute("INSERT INTO product_groups "
                    "(id,name,sku,description,thumbnail,brand_json,client_json,integration_json,"
                    "sell_price_min,sell_price_max,weight,height,width,length,created_at,update_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g.get("id"), g.get("name"), g.get("sku"), g.get("description"),
                     g.get("thumbnail"), json.dumps(g.get("brand") or {}),
                     json.dumps(g.get("client") or {}), json.dumps(g.get("integration") or {}),
                     g.get("sell_price_min"), g.get("sell_price_max"),
                     g.get("weight"), g.get("height"), g.get("width"), g.get("length"),
                     norm_ts(g.get("created_at")), norm_ts(g.get("update_at"))))

        for v in (g.get("variants") or []):
            if not isinstance(v, dict):
                continue
            cur.execute("INSERT INTO product_variants "
                        "(id,product_group_id,name,sku,sku_code,alias,sell_price,purchase_price,"
                        "variation_label,variation_value,weight,height,width,length,type,is_bundle,"
                        "stock_type,stock_sort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (v.get("id"), v.get("product_group_id"), v.get("name"), v.get("sku"),
                         v.get("sku_code"), v.get("alias"), v.get("sell_price"), v.get("purchase_price"),
                         v.get("variation_label"), v.get("variation_value"),
                         v.get("weight"), v.get("height"), v.get("width"), v.get("length"),
                         v.get("type"), 1 if v.get("is_bundle") else 0,
                         v.get("stock_type"), v.get("stock_sort")))
            vid = v.get("id")
            for comp in (v.get("components") or []):
                if isinstance(comp, dict):
                    cur.execute("INSERT INTO variant_components "
                                "(variant_id,product_id,component_sku,multiplier_qty) VALUES (?,?,?,?)",
                                (vid, comp.get("product_id"), comp.get("component_sku"),
                                 comp.get("multiplier_qty")))
            for st in (v.get("stocks") or []):
                if isinstance(st, dict):
                    cur.execute("INSERT INTO variant_stocks "
                                "(variant_id,warehouse_id,warehouse_name,available,on_order,reserved) "
                                "VALUES (?,?,?,?,?,?)",
                                (vid, st.get("id"), st.get("name"), st.get("available"),
                                 st.get("on_order"), st.get("reserved")))
            for im in (v.get("images") or []):
                if isinstance(im, dict):
                    cur.execute("INSERT INTO variant_images (variant_id,url,filename) VALUES (?,?,?)",
                                (vid, im.get("url"), im.get("filename")))

    b = lambda x: 1 if x else 0
    for o in d.get("orders", []):
        cur.execute("INSERT INTO orders "
                    "(id,reference_no,order_at,created_at,process_at,returned_at,deadline_at,qty,amount,"
                    "cogs,shipping_fee,cod_fee,courier,courier_label,warehouse_name,channel_name,"
                    "channel_image,channel_id,customer_username,customer_name,customer_phone,customer_email,"
                    "picklist_id,picklist_no,delivery_note_id,delivery_note_no,awb,is_shipping_label_printed,"
                    "printed,product_summary,integration_id,integration_store,is_cod,is_pre_order,is_instant,"
                    "is_booking,is_fake_order,status,status_mp,sent_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (o.get("id"), o.get("reference_no"), norm_ts(o.get("order_at")), norm_ts(o.get("created_at")),
                     norm_ts(o.get("process_at")), norm_ts(o.get("returned_at")), norm_ts(o.get("deadline_at")), o.get("qty"),
                     o.get("amount"), o.get("cogs"), o.get("shipping_fee"), o.get("cod_fee"),
                     o.get("courier"), o.get("courier_label"), o.get("warehouse_name"),
                     o.get("channel_name"), o.get("channel_image"),
                     chan_id.get(o.get("channel_name"), 0),
                     o.get("customer_username"), o.get("customer_name"), o.get("customer_phone"),
                     o.get("customer_email"), o.get("picklist_id"), o.get("picklist_no"),
                     o.get("delivery_note_id"), o.get("delivery_note_no"), o.get("awb"),
                     b(o.get("is_shipping_label_printed")), o.get("printed"), o.get("product_summary"),
                     o.get("integration_id"), o.get("integration_store"),
                     b(o.get("is_cod")), b(o.get("is_pre_order")), b(o.get("is_instant")),
                     b(o.get("is_booking")), b(o.get("is_fake_order")),
                     o.get("status"), o.get("status_mp"), norm_ts(o.get("sent_at")), norm_ts(o.get("updated_at"))))

    conn.commit()
    ng = cur.execute("SELECT COUNT(*) FROM product_groups").fetchone()[0]
    nv = cur.execute("SELECT COUNT(*) FROM product_variants").fetchone()[0]
    nb = cur.execute("SELECT COUNT(*) FROM product_variants WHERE is_bundle=1").fetchone()[0]
    no = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    print(f"[seed] groups={ng} | variants={nv} | bundles={nb} | orders={no}")
    print(f"[seed] DB -> {WMS_DB}")


if __name__ == "__main__":
    main()