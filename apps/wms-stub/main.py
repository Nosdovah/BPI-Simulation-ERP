# -*- coding: utf-8 -*-
"""ERP/WMS Simulator (Perpack) — blueprint v2.

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
# Bearer token untuk receiver webstore (opsional; receiver-nya mewajibkan ini).
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", os.environ.get("OSR_WEBHOOK_TOKEN", ""))
OSR_MODE = os.environ.get("OSR_MODE", "baseline")
ZERO_CLICK_SLA_SEC = 5.0

STATUS_FLOW = ["paid", "process", "pick", "packing", "packed", "ready_to_pickup", "sent", "completed"]
TERMINAL = {"cancelled", "returned", "completed"}
VALID_STATUS = set(STATUS_FLOW) | TERMINAL

# Enum internal ERP -> enum baku receiver storefront (`lib/webhooks.ts` ORDER_STATUSES).
STATUS_ENUM_MAP = {
    "paid": "PAID",
    "process": "PROCESSING",
    "pick": "PICKING",
    "packing": "PROCESSING",
    "packed": "PACKED",
    "ready_to_pickup": "HANDOVER_TO_COURIER",
    "sent": "IN_TRANSIT",
    "completed": "COMPLETED",
    "cancelled": "CANCELLED",
    "returned": "RETURNED",
}
FOCAL_BUNDLE = "Sanctum+Noctis-20ml-A1"

app = FastAPI(title="osr-wms (ERP Perpack Simulator) — blueprint v2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Read-model untuk webstore (`lib/erp/pull.mjs`) — /api/v2/*, sumber SQLite yang sama.
try:
    from .compat import router as compat_router
except ImportError:  # dijalankan langsung sebagai skrip
    from compat import router as compat_router

app.include_router(compat_router)


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
        # nama event yang di-handle receiver webstore (`lib/webhooks.ts`)
        "event": "order.status_changed",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": {
            "order_ref": order_row["order_ref"],
            "wms_order_id": order_row["wms_order_id"],
            "channel": order_row["channel"],
            "previous_status": previous_status,
            "current_status": new_status,
            # alias yang dibaca receiver webstore (enum baku huruf besar)
            "ref": order_row["order_ref"],
            "status": STATUS_ENUM_MAP.get(new_status, new_status.upper()),
            "tracking_no": order_row["tracking_number"],
            "actor": order_row["actor_last"] or "SYSTEM",
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
    headers = {
        "Content-Type": "application/json",
        # kontrak v2 (dokumen blueprint)
        "X-Perpack-Signature": f"sha256={sig}",
        # alias yang dibaca receiver webstore (`lib/webhooks.ts`)
        "X-OSR-Signature": f"sha256={sig}",
    }
    if WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {WEBHOOK_TOKEN}"
    if payload.get("event_id"):
        headers["Idempotency-Key"] = str(payload["event_id"])
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
    return {"ok": True, "service": "osr-wms (ERP Simulator v2)", "mode": OSR_MODE}


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
                    raise HTTPException(409, f"bundle {it.sku} tanpa relasi BOM (baseline)")
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
    n_total = q1("SELECT COUNT(*) c FROM orders")["c"]
    n_stuck = q1("SELECT COUNT(*) c FROM orders WHERE status='sent'")["c"]
    n_gt2 = q1("SELECT COUNT(*) c FROM orders WHERE status='sent' AND sent_at < datetime('now','-2 days')")["c"]
    n_comp = q1("SELECT COUNT(*) c FROM orders WHERE status='completed'")["c"]
    n_manual = q1("SELECT COUNT(*) c FROM orders WHERE status='completed' AND actor_last='STAFF:manual'")["c"]
    n_ev = q1("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2'")["c"]
    n_valid = q1("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2' AND payload_valid=1")["c"]
    quota = q1("SELECT qty FROM bundle_quota_manual WHERE bundle_sku=?", (FOCAL_BUNDLE,))
    n_bom = q1("SELECT COUNT(*) c FROM sku_bom")["c"]
    m2291 = q1("SELECT available FROM stocks WHERE variant_sku='M2291'")
    m2292 = q1("SELECT available FROM stocks WHERE variant_sku='M2292'")
    dpmo = round((n_ev - n_valid) / n_ev * 1_000_000) if n_ev else 0
    return envelope({
        "mode": OSR_MODE,
        "orders": {"total": n_total, "stuck_sent": n_stuck, "stuck_gt2d": n_gt2,
                   "completed": n_comp, "manual": n_manual,
                   "stuck_pct": round(n_stuck / n_total * 100, 1) if n_total else 0},
        "webhook_4_2": {"events": n_ev, "valid": n_valid,
                        "validity_pct": round(n_valid / n_ev * 100, 1) if n_ev else 0, "dpmo": dpmo},
        "bundle": {"sku": FOCAL_BUNDLE, "quota": quota["qty"] if quota else None,
                   "bundle_components_rows": n_bom,
                   "component_m2291": m2291["available"] if m2291 else None,
                   "component_m2292": m2292["available"] if m2292 else None},
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
        n_bom = q1("SELECT COUNT(*) c FROM sku_bom WHERE bundle_sku=?", (sku,))["c"]
        is_bundle = q1("SELECT is_bundle FROM variants WHERE sku=?", (sku,))
        st = q("SELECT available FROM stocks WHERE variant_sku=? LIMIT 1", (sku,))
        out.append({"bundle_sku": sku,
                    "is_bundle": bool(is_bundle["is_bundle"]) if is_bundle else None,
                    "quota_static": qu["qty"], "bundle_components_rows": n_bom,
                    "stock_available": st[0]["available"] if st else None, "set_by": qu["set_by"]})
    return envelope(out)


@app.post("/v1/open/catalog/push")
def push_catalog():
    return envelope({"note": "v2: katalog disajikan via GET /api/v1/inventory/{sku}", "sent": True})


# ============================ static admin UI ============================

ADMIN_DIST = os.path.join(BASE, "apps", "admin", "dist")
if os.path.isdir(ADMIN_DIST):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=ADMIN_DIST, html=True), name="admin-ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)