# -*- coding: utf-8 -*-
"""osr-wms-stub — ERP Perpack (tiruan). Jalur A (Katalog) + Jalur B (Order & Status).

Backend memakai endpoint WEBHOOK sebagai jembatan data ke storefront:
  * Jalur A: outbound catalog.updated  (push)
  * Jalur B: outbound order.status_updated (baseline: body kosong = cacat SoW)

Target webhook (storefront/core) dikonfigurasi lewat env:
  CORE_WEBHOOK_URL      (default http://localhost:8000/webhook/octarine-wms)
  WEBHOOK_TOKEN         (default osr-local-token-2026)
  OSR_MODE              baseline | tobe   (default baseline)

Run:  uvicorn main:app --host 0.0.0.0 --port 8100
"""
import json, os, sqlite3, time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WMS_DB = os.path.join(BASE, "db", "wms.db")
CORE_WEBHOOK_URL = os.environ.get("OSR_WEBHOOK_URL",
    os.environ.get("CORE_WEBHOOK_URL", "http://127.0.0.1:3000/api/webhooks/wms"))
WEBHOOK_TOKEN = os.environ.get("OSR_WEBHOOK_TOKEN",
    os.environ.get("WEBHOOK_TOKEN", "osr-local-token-2026"))
OSR_MODE = os.environ.get("OSR_MODE", "baseline")

STATUS_FLOW = ["paid", "process", "pick", "packing", "packed", "ready_to_pickup", "sent", "completed"]
TERMINAL = {"cancelled", "returned", "completed"}
FOCAL_BUNDLE = "BND-SANCTUM-NOCTIS-20-A1"

# Pemetaan enum internal (lowercase) ke enum baku receiver core (UPPERCASE)
STATUS_ENUM_MAP = {
    "paid": "PAID", "process": "PROCESSING", "pick": "PICKING", "packing": "PICKING",
    "packed": "PACKED", "ready_to_pickup": "HANDOVER_TO_COURIER", "sent": "IN_TRANSIT",
    "completed": "COMPLETED", "cancelled": "CANCELLED", "returned": "RETURNED",
}

app = FastAPI(title="osr-wms-stub (ERP Perpack tiruan)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def q(sql, args=()):
    conn = sqlite3.connect(WMS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def q1(sql, args=()):
    rows = q(sql, args)
    return dict(rows[0]) if rows else None


def envelope(data):
    return {"code": 200, "msg": {"id": "Sukses", "en": "Success"}, "data": data}


def event_id(prefix):
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() % 1000)}"


def post_webhook(payload):
    """Kirim webhook ke receiver core (Bearer token). Return (status_code, ok)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {WEBHOOK_TOKEN}",
        "Idempotency-Key": str(payload.get("event_id") or ""),
    }
    try:
        r = httpx.post(CORE_WEBHOOK_URL, json=payload, headers=headers, timeout=20)
        return r.status_code, (r.status_code in (200, 202))
    except Exception as e:
        return 0, False


# ============================ Jalur A ============================

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "osr-wms-stub", "mode": OSR_MODE}


@app.get("/v1/open/products/group/page")
def products_page(gender: str = None, page: int = 1, limit: int = 50):
    sql = "SELECT sku, name, gender, price_idr, is_bundle FROM variants WHERE is_active=1"
    args = []
    if gender:
        sql += " AND gender=?"
        args.append(gender.upper())
    sql += " LIMIT ? OFFSET ?"
    args += [limit, (page - 1) * limit]
    return envelope([dict(r) for r in q(sql, args)])


@app.get("/v1/open/products/details")
def product_details(sku: str):
    row = q1("SELECT sku, name, gender, price_idr, is_bundle FROM variants WHERE sku=?", (sku,))
    if not row:
        raise HTTPException(404, "SKU tidak ditemukan")
    return envelope(row)


@app.get("/v1/open/stocks")
def get_stocks():
    return envelope([dict(r) for r in q(
        "SELECT s.variant_sku, s.warehouse_id, w.name AS warehouse_name, "
        "s.available, s.on_order, s.reserved FROM stocks s "
        "JOIN warehouses w ON w.id=s.warehouse_id")])


def build_catalog_payload():
    products = q("SELECT sku, name, gender, price_idr, is_bundle FROM variants WHERE is_active=1")
    stocks = q("SELECT variant_sku, warehouse_id, available, on_order, reserved FROM stocks")
    stock_map = {}
    for r in stocks:
        stock_map.setdefault(r["variant_sku"], []).append({
            "warehouse_id": r["warehouse_id"], "available": r["available"],
            "on_order": r["on_order"], "reserved": r["reserved"]})
    data = [{"sku": p["sku"], "name": p["name"], "gender": p["gender"],
             "price_idr": p["price_idr"], "is_bundle": p["is_bundle"],
             "stocks": stock_map.get(p["sku"], [])} for p in products]
    return {"event_id": event_id("evt_wms_catalog"), "event": "catalog.updated",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lane": "4.1", "data": {"products": data}}


@app.post("/v1/open/catalog/push")
def push_catalog():
    payload = build_catalog_payload()
    status, ok = post_webhook(payload)
    return {"sent": ok, "to": f"{CORE_WEBHOOK_URL}/{WEBHOOK_TOKEN}",
            "products": len(payload["data"]["products"]), "upstream_status": status}


# ============================ Jalur B ============================

def build_status_payload(order_row, new_status):
    """Payload order.status_changed (kontrak receiver core)."""
    return {"event": "order.status_changed", "event_id": event_id("evt_wms_status"),
            "source": "wms-4.2", "occurred_at": int(time.time()),
            "data": {"ref": order_row["order_ref"],
                     "status": STATUS_ENUM_MAP.get(new_status, new_status.upper()),
                     "tracking_no": order_row["tracking_number"],
                     "actor": order_row["actor_last"] or "SYSTEM"}}


def build_stock_payload(material_code, delta):
    """Payload stock.updated (to-be): komponen terjual -> stok bundle turun."""
    return {"event": "stock.updated", "event_id": event_id("evt_wms_stock"),
            "source": "wms-4.1", "occurred_at": int(time.time()),
            "data": {"materialCode": material_code, "delta": delta}}


@app.post("/v1/open/sales-order")
def create_order(body: dict):
    oref = body.get("order_ref", f"ORD-{int(time.time())}")
    conn = sqlite3.connect(WMS_DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (order_ref,channel,status,actor_last,total_idr,paid_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (oref, body.get("channel", "WEBSTORE"), "paid", "SYSTEM", body.get("total_idr", 0)))
    oid = cur.lastrowid
    for it in body.get("items", []):
        cur.execute("INSERT INTO order_items (order_id,variant_sku,qty,unit_price_idr) VALUES (?,?,?,?)",
                    (oid, it["sku"], it.get("qty", 1), it.get("price", 0)))
    cur.execute("INSERT INTO order_status_history (order_id,to_status,actor,source) VALUES (?,?,?,?)",
                (oid, "paid", "SYSTEM", "WEBHOOK"))
    conn.commit()
    conn.close()
    return envelope({"id": oid, "order_ref": oref, "status": "paid",
                     "note": "baseline: status stagnan (auto-ack off)"})


@app.get("/v1/open/sales-order/list")
def list_orders(status: str = None):
    sql = ("SELECT id, order_ref, wms_order_id, channel, status, actor_last, "
           "courier_name, tracking_number, total_idr FROM orders")
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    return envelope([dict(r) for r in q(sql, args)])


@app.post("/v1/open/sales-order/{oid}/process")
def process_order(oid: int):
    if OSR_MODE == "baseline":
        raise HTTPException(404, "endpoint /process belum tersedia (cacat SoW Modul B.2)")
    o = q1("SELECT * FROM orders WHERE id=?", (oid,))
    if not o:
        raise HTTPException(404, "order tidak ditemukan")
    if o["status"] != "paid":
        raise HTTPException(409, "hanya order berstatus paid yang bisa diproses")
    return _transition(oid, "process", "SYSTEM", "WEBHOOK")


@app.post("/v1/open/sales-order/{oid}/cancel")
def cancel_order(oid: int):
    o = q1("SELECT * FROM orders WHERE id=?", (oid,))
    if not o:
        raise HTTPException(404, "order tidak ditemukan")
    if o["status"] in TERMINAL:
        raise HTTPException(409, "order terminal, tidak bisa dibatalkan")
    return _transition(oid, "cancelled", "STAFF", "MANUAL_UI")


@app.post("/v1/open/sales-order/{oid}/status")
def manual_status(oid: int, body: dict):
    """Simulasi klik manual staf; memicu outbound status webhook (baseline: kosong)."""
    o = q1("SELECT * FROM orders WHERE id=?", (oid,))
    if not o:
        raise HTTPException(404, "order tidak ditemukan")
    new_status = body.get("status")
    if new_status not in STATUS_FLOW and new_status not in TERMINAL:
        raise HTTPException(422, "enum tidak dikenal")
    return _transition(oid, new_status, body.get("actor", "STAFF:manual"), "MANUAL_UI")


def _transition(oid, new_status, actor, source):
    o = q1("SELECT * FROM orders WHERE id=?", (oid,))
    prev = o["status"]
    if prev in TERMINAL:
        raise HTTPException(409, "order terminal — immutable")
    if OSR_MODE == "tobe" and new_status in STATUS_FLOW and prev in STATUS_FLOW and \
            STATUS_FLOW.index(new_status) < STATUS_FLOW.index(prev):
        raise HTTPException(409, "no-regression: status tidak boleh mundur")
    conn = sqlite3.connect(WMS_DB)
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=?, actor_last=? WHERE id=?", (new_status, actor, oid))
    cur.execute("INSERT INTO order_status_history (order_id,from_status,to_status,actor,source) "
                "VALUES (?,?,?,?,?)", (oid, prev, new_status, actor, source))
    conn.commit()
    conn.close()
    # outbound webhook (jembatan data)
    if OSR_MODE == "baseline":
        # cacat SoW: event dengan data kosong (payload tidak membawa status)
        out = {"event": "order.status_changed", "event_id": event_id("evt_wms_status"),
               "source": "wms-4.2", "data": {}}
    else:
        out = build_status_payload(q1("SELECT * FROM orders WHERE id=?", (oid,)), new_status)
    status, ok = post_webhook(out)
    return {"id": oid, "from": prev, "to": new_status, "webhook_sent": ok,
            "webhook_status": status, "mode": OSR_MODE}


@app.get("/v1/open/logistic/track")
def logistic_track(airway_bill: str):
    if OSR_MODE == "baseline":
        raise HTTPException(404, "integrasi resi 3PL belum tersedia (cacat SoW Modul C.5)")
    return envelope({"airway_bill": airway_bill, "status": "completed", "courier": "sicepat"})


@app.post("/v1/open/components/{sku}/sold")
def component_sold(sku: str, body: dict):
    """To-be: komponen terjual -> kurangi stok + kirim webhook stock.updated."""
    delta = int(body.get("delta", body.get("qty", 1)))
    conn = sqlite3.connect(WMS_DB)
    cur = conn.cursor()
    cur.execute("UPDATE stocks SET available = MAX(0, available - ?) "
                "WHERE variant_sku=? AND warehouse_id=8", (delta, sku))
    conn.commit()
    conn.close()
    payload = build_stock_payload(sku, delta)
    status, ok = post_webhook(payload)
    return envelope({"sku": sku, "delta": delta, "webhook_sent": ok, "webhook_status": status})


# ============================ Admin (osr-admin) ============================

@app.get("/v1/open/admin/summary")
def admin_summary():
    n_total = q1("SELECT COUNT(*) c FROM orders")["c"]
    n_stuck = q1("SELECT COUNT(*) c FROM orders WHERE status='sent'")["c"]
    n_gt2 = q1("SELECT COUNT(*) c FROM orders WHERE status='sent' AND sent_at < datetime('now','-2 days')")["c"]
    n_comp = q1("SELECT COUNT(*) c FROM orders WHERE status='completed'")["c"]
    n_manual = q1("SELECT COUNT(*) c FROM orders WHERE status='completed' AND actor_last='STAFF:manual'")["c"]
    n_ev = q1("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2'")["c"]
    n_valid = q1("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2' AND payload_valid=1")["c"]
    quota = q1("SELECT qty FROM bundle_quota_manual WHERE bundle_sku=?", (FOCAL_BUNDLE,))
    n_bc = q1("SELECT COUNT(*) c FROM bundle_components")["c"]
    m2291 = q1("SELECT available FROM stocks WHERE variant_sku='M2291'")
    m2292 = q1("SELECT available FROM stocks WHERE variant_sku='M2292'")
    dpmo = round((n_ev - n_valid) / n_ev * 1_000_000) if n_ev else 0
    return envelope({
        "mode": OSR_MODE,
        "orders": {"total": n_total, "stuck_sent": n_stuck, "stuck_gt2d": n_gt2,
                   "completed": n_comp, "manual": n_manual,
                   "stuck_pct": round(n_stuck / n_total * 100, 1) if n_total else 0},
        "webhook_4_2": {"events": n_ev, "valid": n_valid,
                        "validity_pct": round(n_valid / n_ev * 100, 1) if n_ev else 0,
                        "dpmo": dpmo},
        "bundle": {"sku": FOCAL_BUNDLE, "quota": quota["qty"] if quota else None,
                   "bundle_components_rows": n_bc,
                   "component_m2291": m2291["available"] if m2291 else None,
                   "component_m2292": m2292["available"] if m2292 else None},
    })


@app.get("/v1/open/admin/webhook-events")
def admin_webhook_events(lane: str = None, valid: str = None):
    sql = ("SELECT event_id, lane, payload_valid, mapper_rejected, reject_reason, raw_body "
           "FROM wms_webhook_events")
    conds, args = [], []
    if lane:
        conds.append("lane=?"); args.append(lane)
    if valid in ("1", "0"):
        conds.append("payload_valid=?"); args.append(int(valid))
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT 200"
    return envelope([dict(r) for r in q(sql, args)])


@app.get("/v1/open/admin/bundles")
def admin_bundles():
    quotas = q("SELECT bundle_sku, qty, set_by, set_at FROM bundle_quota_manual")
    out = []
    for qu in quotas:
        sku = qu["bundle_sku"]
        n_comp = q1("SELECT COUNT(*) c FROM bundle_components WHERE bundle_sku=?", (sku,))["c"]
        is_bundle = q1("SELECT is_bundle FROM variants WHERE sku=?", (sku,))
        st = q("SELECT available FROM stocks WHERE variant_sku=? LIMIT 1", (sku,))
        out.append({
            "bundle_sku": sku,
            "is_bundle": bool(is_bundle["is_bundle"]) if is_bundle else None,
            "quota_static": qu["qty"],
            "bundle_components_rows": n_comp,
            "stock_available": st[0]["available"] if st else None,
            "set_by": qu["set_by"],
        })
    return envelope(out)


@app.on_event("startup")
def auto_push_catalog():
    try:
        push_catalog()
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)