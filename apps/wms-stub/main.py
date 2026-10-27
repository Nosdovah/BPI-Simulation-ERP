# -*- coding: utf-8 -*-
"""ERP/WMS Simulator (Perpack) — blueprint v2 (TO-BE).

FastAPI Data-Bridge server side: BOM/Virtual Kitting engine (Modul A),
Zero-Click order lifecycle (Modul B), outbound webhook ber-tanda tangan
HMAC-SHA256 (Modul C).

API v2:
  GET  /api/v1/inventory/{sku}                     -> stok hasil kalkulasi BOM
  POST /api/v1/storefront/checkout                 -> potong stok atomik + Zero-Click
  POST /api/v1/wms/orders/{order_ref}/transition   -> transisi status fulfillment
  GET  /api/v1/audit/webhook-logs                  -> log webhook outbound + signature

Envelope: {"code":200, "msg":{...}, "data":...}
"""
import hashlib
import hmac as hm
import json
import os
import sqlite3
import time

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import httpx

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WMS_DB = os.path.join(BASE, "db", "wms.db")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", os.environ.get("OSR_WEBHOOK_URL", "http://127.0.0.1:3000/api/webhooks/wms"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", os.environ.get("OSR_WEBHOOK_HMAC_SECRET", "osr-local-hmac-secret"))
ZERO_CLICK_SLA_SEC = 5.0

STATUS_FLOW = ["paid", "process", "pick", "packing", "packed", "ready_to_pickup", "sent", "completed"]
TERMINAL = {"cancelled", "returned", "completed"}
VALID_STATUS = set(STATUS_FLOW) | TERMINAL
FOCAL_BUNDLE = "Sanctum+Noctis-20ml-A1"

app = FastAPI(title="osr-wms (ERP Perpack Simulator) — blueprint v2 to-be")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ============================ Pydantic v2 ============================

class CheckoutItem(BaseModel):
    sku: str
    qty: int = Field(default=1, ge=1)


class CheckoutRequest(BaseModel):
    order_ref: str
    channel: str = "WEBSTORE"
    items: list[CheckoutItem]


# ============================ DB helpers ============================

def conn():
    c = sqlite3.connect(WMS_DB)
    c.row_factory = sqlite3.Row
    return c


def q(sql, args=()):
    c = conn()
    rows = c.execute(sql, args).fetchall()
    c.close()
    return rows


def q1(sql, args=()):
    rows = q(sql, args)
    return dict(rows[0]) if rows else None


def envelope(data):
    return {"code": 200, "msg": {"id": "Sukses", "en": "Success"}, "data": data}


def event_id(prefix):
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() % 1000)}"


# ============================ Modul A — BOM engine ============================

def component_stock(cur, sku):
    row = cur.execute("SELECT COALESCE(SUM(available),0) s FROM stocks WHERE variant_sku=?", (sku,)).fetchone()
    return row["s"]


def deduct_stock(cur, sku, qty):
    """Potong qty dari stok (distributed, greedy dari gudang terbanyak)."""
    rows = cur.execute("SELECT variant_sku, warehouse_id, available FROM stocks "
                       "WHERE variant_sku=? AND available > 0 ORDER BY available DESC", (sku,)).fetchall()
    remaining = qty
    for r in rows:
        if remaining <= 0:
            break
        take = min(r["available"], remaining)
        cur.execute("UPDATE stocks SET available = available - ?, updated_at=datetime('now') "
                    "WHERE variant_sku=? AND warehouse_id=?",
                    (take, sku, r["warehouse_id"]))
        remaining -= take
    if remaining > 0:
        raise HTTPException(409, f"stok {sku} tidak cukup (kurang {remaining})")


def bom_available(sku):
    """Stok bundle = min(floor(stok_komponen / multiplier)). Non-bundle = stok fisik."""
    c = conn()
    cur = c.cursor()
    v = cur.execute("SELECT sku, name, gender, price_idr, is_bundle FROM variants WHERE sku=?", (sku,)).fetchone()
    if not v:
        c.close()
        return None
    if not bool(v["is_bundle"]):
        out = {"sku": sku, "name": v["name"], "is_bundle": False,
               "available_stock": component_stock(cur, sku), "components": []}
        c.close()
        return out
    comps = cur.execute("SELECT component_sku, multiplier_qty FROM sku_bom WHERE bundle_sku=?", (sku,)).fetchall()
    components = []
    for comp in comps:
        stk = component_stock(cur, comp["component_sku"])
        components.append({"component_sku": comp["component_sku"], "stock_available": stk,
                           "multiplier": comp["multiplier_qty"]})
    c.close()
    if not components:
        return {"sku": sku, "name": v["name"], "is_bundle": True, "available_stock": 0, "components": []}
    avail = min(stk // comp["multiplier"] for comp in components for stk in [comp["stock_available"]])
    return {"sku": sku, "name": v["name"], "is_bundle": True, "available_stock": avail, "components": components}


# ============================ Modul C — HMAC + webhook ============================

def _sign(raw_body: bytes) -> str:
    return hm.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def build_status_payload(order_row, previous_status, new_status):
    return {
        "event_id": event_id("evt_wms_status"),
        "event": "order.status_updated",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": {
            "order_ref": order_row["order_ref"],
            "wms_order_id": order_row["wms_order_id"],
            "channel": order_row["channel"],
            "previous_status": previous_status,
            "current_status": new_status,
            "courier": {
                "name": order_row["courier_name"],
                "service": order_row["courier_service"],
                "tracking_number": order_row["tracking_number"],
            },
            "updated_by": order_row["actor_last"] or "SYSTEM",
        },
    }


def post_webhook(payload, lane="4.2"):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = _sign(raw)
    headers = {"Content-Type": "application/json", "X-Perpack-Signature": f"sha256={sig}"}
    try:
        r = httpx.post(WEBHOOK_URL, content=raw, headers=headers, timeout=20)
        status, ok = r.status_code, r.status_code in (200, 202, 204)
    except Exception:
        status, ok = 0, False
    c = conn()
    c.execute("INSERT INTO webhook_delivery_log (event_id,event,lane,attempt,http_status,signature,sent_at) "
              "VALUES (?,?,?,?,?,?,datetime('now'))",
              (payload.get("event_id"), payload.get("event"), lane, 1, status, f"sha256={sig[:12]}\u2026"))
    c.commit()
    c.close()
    return status, ok


def dispatch_status_webhook(order_row, previous_status, new_status):
    payload = build_status_payload(order_row, previous_status, new_status)
    post_webhook(payload, lane="4.2")
    return payload


# ============================ Modul A — endpoint ============================

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "osr-wms (ERP Simulator v2)", "mode": "tobe"}


@app.get("/api/v1/inventory/{sku}")
def inventory(sku: str):
    result = bom_available(sku)
    if result is None:
        raise HTTPException(404, "SKU tidak ditemukan")
    return envelope(result)


# ============================ Modul B — checkout (Zero-Click) ============================

@app.post("/api/v1/storefront/checkout")
def checkout(req: CheckoutRequest, background: BackgroundTasks):
    c = conn()
    c.execute("BEGIN IMMEDIATE")          # write lock = atomic (simulator)
    cur = c.cursor()
    try:
        total = 0
        for it in req.items:
            v = cur.execute("SELECT sku, price_idr, is_bundle FROM variants WHERE sku=?", (it.sku,)).fetchone()
            if not v:
                raise HTTPException(404, f"SKU {it.sku} tidak ada")
            if v["is_bundle"]:
                comps = cur.execute("SELECT component_sku, multiplier_qty FROM sku_bom WHERE bundle_sku=?", (it.sku,)).fetchall()
                if not comps:
                    raise HTTPException(409, f"bundle {it.sku} tanpa relasi BOM")
                for comp in comps:
                    deduct_stock(cur, comp["component_sku"], it.qty * comp["multiplier_qty"])
            else:
                deduct_stock(cur, it.sku, it.qty)
            total += v["price_idr"] * it.qty

        cur.execute("INSERT INTO orders (order_ref,channel,status,actor_last,total_idr,paid_at,processing_at) "
                    "VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
                    (req.order_ref, req.channel, "paid", "SYSTEM", total))
        oid = cur.lastrowid
        for it in req.items:
            v = cur.execute("SELECT price_idr FROM variants WHERE sku=?", (it.sku,)).fetchone()
            cur.execute("INSERT INTO order_items (order_id,variant_sku,qty,unit_price_idr) VALUES (?,?,?,?)",
                        (oid, it.sku, it.qty, v["price_idr"]))
        # Zero-Click: paid -> process (<5s, actor SYSTEM) — dalam transaksi yang sama
        cur.execute("UPDATE orders SET status='process', actor_last='SYSTEM' WHERE id=?", (oid,))
        cur.execute("INSERT INTO order_status_history (order_id,to_status,actor,source) VALUES (?,?,?,?)",
                    (oid, "process", "SYSTEM", "CHECKOUT"))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

    order_row = q1("SELECT * FROM orders WHERE id=?", (oid,))
    background.add_task(dispatch_status_webhook, order_row, "paid", "process")
    return {
        "message": "Pesanan terbayar dan otomatis diproses ke gudang (Zero-Click).",
        "order_ref": req.order_ref, "status": "process", "updated_by": "SYSTEM",
        "sla_seconds": ZERO_CLICK_SLA_SEC,
    }


# ============================ Modul B — transisi status ============================

@app.post("/api/v1/wms/orders/{order_ref}/transition")
def transition(order_ref: str, target_status: str, tracking_number: str = None,
               background: BackgroundTasks = None):
    if target_status not in VALID_STATUS:
        raise HTTPException(422, "enum status tidak dikenal")
    o = q1("SELECT * FROM orders WHERE order_ref=?", (order_ref,))
    if not o:
        raise HTTPException(404, "order tidak ditemukan")
    prev = o["status"]
    if prev in TERMINAL:
        raise HTTPException(409, "order terminal — immutable")
    # no-regression: status tidak boleh mundur
    if target_status in STATUS_FLOW and prev in STATUS_FLOW and \
            STATUS_FLOW.index(target_status) < STATUS_FLOW.index(prev):
        raise HTTPException(409, "no-regression: status tidak boleh mundur")

    actor = "COURIER" if target_status in ("sent", "completed") else "SYSTEM"
    c = conn()
    c.execute("UPDATE orders SET status=?, actor_last=?, courier_name=COALESCE(?,courier_name), "
              "tracking_number=COALESCE(?,tracking_number) WHERE id=?",
              (target_status, actor, (o["courier_name"] or "sicepat"), tracking_number, o["id"]))
    c.execute("INSERT INTO order_status_history (order_id,from_status,to_status,actor,source) VALUES (?,?,?,?,?)",
              (o["id"], prev, target_status, actor, "WMS"))
    c.commit()
    c.close()

    order_row = q1("SELECT * FROM orders WHERE id=?", (o["id"],))
    if background:
        background.add_task(dispatch_status_webhook, order_row, prev, target_status)
        status, ok = None, True
    else:
        status, ok = post_webhook(build_status_payload(order_row, prev, target_status), lane="4.2")

    envelope_d = {"order_ref": order_ref, "from": prev, "to": target_status,
                  "updated_by": actor, "webhook_sent": ok, "webhook_status": status}
    return envelope(envelope_d)


# ============================ Modul C — audit ============================

@app.get("/api/v1/audit/webhook-logs")
def audit_logs(limit: int = 100):
    rows = q("SELECT id, event_id, event, lane, attempt, http_status, signature, sent_at "
             "FROM webhook_delivery_log ORDER BY id DESC LIMIT ?", (limit,))
    return envelope([dict(r) for r in rows])


# ============================ Admin (osr-admin) ============================

@app.get("/v1/open/admin/summary")
def admin_summary():
    n_var = q1("SELECT COUNT(*) c FROM variants")["c"]
    n_bundle = q1("SELECT COUNT(*) c FROM variants WHERE is_bundle=1")["c"]
    n_bom = q1("SELECT COUNT(*) c FROM sku_bom")["c"]
    focal = bom_available(FOCAL_BUNDLE)
    n_orders = q1("SELECT COUNT(*) c FROM orders")["c"]
    by_status = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM orders GROUP BY status")}
    n_delivered = q1("SELECT COUNT(*) c FROM webhook_delivery_log WHERE http_status IN (200,202,204)")["c"]
    n_attempt = q1("SELECT COUNT(*) c FROM webhook_delivery_log")["c"]
    return envelope({
        "mode": "tobe",
        "catalog": {"variants": n_var, "bundles": n_bundle, "bom_rows": n_bom},
        "focal_bundle": focal,
        "orders": {"total": n_orders, "by_status": by_status},
        "webhook": {"delivered": n_delivered, "attempts": n_attempt},
    })


@app.get("/v1/open/sales-order/list")
def list_orders(status: str = None):
    sql = ("SELECT id, order_ref, wms_order_id, channel, status, actor_last, "
           "courier_name, tracking_number, total_idr FROM orders")
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    return envelope([dict(r) for r in q(sql, args)])


@app.get("/v1/open/admin/webhook-events")
def admin_webhook_events():
    rows = q("SELECT event_id, event, lane, attempt, http_status, signature, sent_at "
             "FROM webhook_delivery_log ORDER BY id DESC LIMIT 200")
    return envelope([dict(r) for r in rows])


@app.get("/v1/open/admin/bundles")
def admin_bundles():
    bundles = q("SELECT DISTINCT bundle_sku FROM sku_bom")
    out = []
    for b in bundles:
        sku = b["bundle_sku"]
        inv = bom_available(sku)
        n_bom = q1("SELECT COUNT(*) c FROM sku_bom WHERE bundle_sku=?", (sku,))["c"]
        is_bundle = q1("SELECT is_bundle FROM variants WHERE sku=?", (sku,))
        out.append({"bundle_sku": sku,
                    "is_bundle": bool(is_bundle["is_bundle"]) if is_bundle else None,
                    "bom_rows": n_bom,
                    "available_stock": inv["available_stock"] if inv else None,
                    "components": inv["components"] if inv else []})
    return envelope(out)


@app.post("/v1/open/catalog/push")
def push_catalog():
    return envelope({"note": "to-be: katalog disajikan via GET /api/v1/inventory/{sku}", "sent": True})


# ============================ static admin UI ============================

ADMIN_DIST = os.path.join(BASE, "apps", "admin", "dist")
if os.path.isdir(ADMIN_DIST):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=ADMIN_DIST, html=True), name="admin-ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)