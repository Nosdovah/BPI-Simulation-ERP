# -*- coding: utf-8 -*-
"""Replika API Perpack/Octarine WMS (octarine-api.perpack.id/v1).

Faithful replica dari endpoint nyata:
  GET  /v1/channels
  GET  /v1/warehouses
  GET  /v1/products/group/page   -> master-data/product (grup produk + nested variants)
  GET  /v1/products/list         -> varian produk (dropdown)
  POST /v1/orders/page           -> outbound/sales (DataTables page/length/channel_id)

Auth: header Authorization berisi JWT MENTAH (tanpa "Bearer "). Signature tidak
diverifikasi (replica; tidak memegang secret produksi). Tanpa auth -> 401.
Envelope: {"code":200,"msg":{"id":"Sukses","en":"Success"},"data":...,"metadata":{...}}
"""
import base64
import json
import math
import os
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WMS_DB = os.path.join(BASE, "db", "wms.db")

app = FastAPI(title="Perpack WMS (Octarine) — replica", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request, exc):
    if exc.status_code == 404:
        return JSONResponse(status_code=404, content={
            "code": 40401,
            "msg": {"id": "Api tidak ditemukan", "en": "Api not found"},
            "data": None})
    return JSONResponse(status_code=exc.status_code,
                        content=exc.detail if isinstance(exc.detail, (dict, list)) else {"detail": exc.detail})


# ============================ Auth ============================

def decode_jwt(auth: str):
    """Decode payload JWT (tanpa verifikasi signature). Menerima raw / Bearer."""
    if not auth:
        return None
    t = auth.strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    try:
        parts = t.split(".")
        if len(parts) < 2:
            return None
        p = parts[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return None


def require_auth(authorization: str = Header(None)):
    claims = decode_jwt(authorization)
    if not claims:
        raise HTTPException(status_code=401, detail={
            "code": 401,
            "msg": {"id": "tidak memiliki akses", "en": "you don't have permission to access"},
            "data": None,
        })
    return claims


# ============================ DB ============================

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


def env(data, meta=None):
    out = {"code": 200, "msg": {"id": "Sukses", "en": "Success"}, "data": data}
    if meta is not None:
        out["metadata"] = meta
    return out


def pagemeta(page, length, count):
    return {"page": page, "length": length, "count": count,
            "total_page": max(1, math.ceil(count / length)) if count else 0}


# ============================ Master data ============================

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "perpack-wms-replica"}


@app.get("/v1/channels")
def channels(_=Depends(require_auth)):
    rows = q("SELECT id, name, image, status FROM channels ORDER BY id")
    return env([dict(r) for r in rows])


@app.get("/v1/warehouses")
def warehouses(_=Depends(require_auth)):
    rows = q("SELECT id, name, address, contact_phone, contact_email, warehouse_type, status, "
             "locations_json FROM warehouses")
    out = []
    for r in rows:
        d = dict(r)
        d["locations"] = json.loads(d.pop("locations_json") or "{}")
        out.append(d)
    return env(out)


# ============================ Master-data / product ============================

def group_variants(group_id):
    rows = q("SELECT * FROM product_variants WHERE product_group_id=? ORDER BY id", (group_id,))
    out = []
    for r in rows:
        v = dict(r)
        v.pop("product_group_id", None)
        v["is_bundle"] = bool(v["is_bundle"])
        v["components"] = [dict(c) for c in q(
            "SELECT product_id, component_sku, multiplier_qty FROM variant_components WHERE variant_id=?",
            (v["id"],))]
        stocks = q("SELECT warehouse_id, warehouse_name, available, on_order, reserved "
                   "FROM variant_stocks WHERE variant_id=?", (v["id"],))
        v["stocks"] = [{"id": s["warehouse_id"], "name": s["warehouse_name"],
                        "available": s["available"], "on_order": s["on_order"],
                        "reserved": s["reserved"]} for s in stocks]
        imgs = q("SELECT id, url, filename FROM variant_images WHERE variant_id=?", (v["id"],))
        v["images"] = [{"id": im["id"], "url": im["url"], "filename": im["filename"]} for im in imgs]
        out.append(v)
    return out


@app.get("/v1/products/group/page")
def products_group_page(page: int = 1, length: int = 100, search: str = None,
                        _=Depends(require_auth)):
    page = max(1, page)
    length = max(1, min(length, 250))
    where, args = "", []
    if search:
        where = "WHERE name LIKE ? OR sku LIKE ?"
        args = [f"%{search}%", f"%{search}%"]
    count = q1(f"SELECT COUNT(*) c FROM product_groups {where}", args)["c"]
    rows = q(f"SELECT * FROM product_groups {where} ORDER BY id DESC LIMIT ? OFFSET ?",
             args + [length, (page - 1) * length])
    data = []
    for r in rows:
        g = dict(r)
        g["brand"] = json.loads(g.pop("brand_json") or "{}")
        g["client"] = json.loads(g.pop("client_json") or "{}")
        g["integration"] = json.loads(g.pop("integration_json") or "{}")
        g["variants"] = group_variants(g["id"])
        data.append(g)
    return env(data, pagemeta(page, length, count))


@app.get("/v1/products/list")
def products_list(page: int = 1, length: int = 100, _=Depends(require_auth)):
    page = max(1, page)
    length = max(1, min(length, 250))
    count = q1("SELECT COUNT(*) c FROM product_variants")["c"]
    rows = q("SELECT * FROM product_variants ORDER BY id LIMIT ? OFFSET ?",
             (length, (page - 1) * length))
    data = []
    for r in rows:
        v = dict(r)
        v["is_bundle"] = bool(v["is_bundle"])
        v["components"] = [dict(c) for c in q(
            "SELECT product_id, component_sku, multiplier_qty FROM variant_components WHERE variant_id=?",
            (v["id"],))]
        v["stocks"] = None
        imgs = q("SELECT id, url, filename FROM variant_images WHERE variant_id=?", (v["id"],))
        v["images"] = [{"id": im["id"], "url": im["url"], "filename": im["filename"]} for im in imgs]
        data.append(v)
    return env(data, pagemeta(page, length, count))


# ============================ Outbound / sales ============================

ORDER_FIELDS = ["id", "reference_no", "order_at", "created_at", "process_at", "returned_at",
                "deadline_at", "qty", "amount", "cogs", "shipping_fee", "cod_fee", "courier",
                "courier_label", "warehouse_name", "channel_name", "channel_image",
                "customer_username", "customer_name", "customer_phone", "customer_email",
                "picklist_id", "picklist_no", "delivery_note_id", "delivery_note_no", "awb",
                "is_shipping_label_printed", "printed", "product_summary", "integration_id",
                "integration_store", "is_cod", "is_pre_order", "is_instant", "is_booking",
                "is_fake_order", "status", "status_mp", "sent_at", "updated_at"]
BOOL_FIELDS = {"is_shipping_label_printed", "is_cod", "is_pre_order",
               "is_instant", "is_booking", "is_fake_order"}


def order_to_dict(r):
    d = {}
    for f in ORDER_FIELDS:
        d[f] = bool(r[f]) if f in BOOL_FIELDS else r[f]
    return d


class OrdersPage(BaseModel):
    page: int = 1
    length: int = 100
    channel_id: int = None
    search: str = None
    status: str = None


@app.post("/v1/orders/page")
def orders_page(body: OrdersPage, _=Depends(require_auth)):
    page = max(1, body.page)
    length = max(1, min(body.length, 250))
    conds, args = [], []
    if body.channel_id is not None:
        conds.append("channel_id=?")
        args.append(body.channel_id)
    if body.status:
        conds.append("status=?")
        args.append(body.status)
    if body.search:
        conds.append("(reference_no LIKE ? OR customer_name LIKE ? OR product_summary LIKE ?)")
        args += [f"%{body.search}%"] * 3
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    count = q1(f"SELECT COUNT(*) c FROM orders {where}", args)["c"]
    rows = q(f"SELECT * FROM orders {where} ORDER BY id DESC LIMIT ? OFFSET ?",
             args + [length, (page - 1) * length])
    data = [order_to_dict(dict(r)) for r in rows]
    return env(data, pagemeta(page, length, count))


# ============================ Webhook API (jembatan data ERP <-> storefront) ===

_webhook_log = []


@app.post("/api/webhooks/wms")
async def webhook_wms(body: dict = None):
    """Receiver webhook ERP -> storefront/core (kontrak: event + data)."""
    body = body if isinstance(body, dict) else {}
    event_id = body.get("event_id") or f"evt-{len(_webhook_log) + 1}"
    _webhook_log.append({"event_id": event_id, "event": body.get("event"), "at": _now()})
    return env({"applied": True, "event": body.get("event"), "event_id": event_id,
                "received_total": len(_webhook_log)})


@app.get("/api/webhooks/wms")
async def webhook_wms_get():
    """GET untuk page-load: memastikan path webhook terlihat di Network (200)."""
    return env({"applied": True, "event": "ping", "received_total": len(_webhook_log),
                "note": "webhook endpoint aktif — path ini terlihat di Network dev tools"})


@app.get("/", response_class=HTMLResponse)
def demo_page():
    return """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Octarine ERP — Webhook Demo</title>
<style>body{font-family:system-ui,sans-serif;padding:32px;max-width:720px;margin:auto;color:#111}
code{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px}
.card{border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:16px 0}
.ok{color:#047857}.badge{background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:999px;font-size:12px}</style></head>
<body><h1>Octarine ERP <span class="badge">replica perpack.id</span></h1>
<p>Endpoint webhook API dipanggil otomatis saat halaman ini <b>load</b>.</p>
<div class="card"><strong>Webhook:</strong> <code>POST /api/webhooks/wms</code>
<p id="status">memuat…</p><pre id="resp" style="font-size:12px;background:#f9fafb;padding:8px;border-radius:6px"></pre></div>
<p>👉 Buka <b>DevTools &rarr; Network</b>, filter <code>webhooks</code> — request <code>POST /api/webhooks/wms</code> terlihat (status 200).</p>
<script>
fetch('/api/webhooks/wms',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({event:'order.status_updated',event_id:'evt-page-load-'+Date.now(),
  data:{order_ref:'ORD-WEBSTORE-PROD-2731',current_status:'process',updated_by:'SYSTEM'}})})
 .then(r=>r.json()).then(j=>{document.getElementById('status').textContent='✅ webhook terkirim saat page load';
  document.getElementById('resp').textContent=JSON.stringify(j,null,2)})
 .catch(e=>{document.getElementById('status').textContent='❌ gagal: '+e});
</script></body></html>"""


def _now():
    import datetime
    return datetime.datetime.now().isoformat()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)