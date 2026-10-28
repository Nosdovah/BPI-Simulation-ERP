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
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
    """Autentikasi opsional untuk demo.

    Perpack produksi MENOLAK tanpa token (401). Replica ini (simulator untuk
    tugas/demo) sengaja MELONGGARKAN-nya: tanpa token tetap bisa akses sebagai
    guest/tamu, supaya data langsung terlihat tanpa repot tempel token.
    Token tetap diterima bila disertakan (klaim di-decode untuk konteks).
    """
    claims = decode_jwt(authorization)
    if not claims:
        return {"id": 0, "client_id": 0, "is_guest": True, "warehouse_id": 0}
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


# ============================ Webhook API (mutasi DB via query) ===


def apply_stock_update(data):
    """stock.updated -> kurangi available stok varian (delta = unit terjual)."""
    sku = data.get("materialCode") or data.get("material_code") or data.get("sku") or data.get("component_sku")
    delta = data.get("delta")
    if delta is None:
        delta = data.get("qty_sold") or data.get("quantity_sold") or data.get("quantity")
    if not sku or delta is None:
        return {"applied": False, "note": "field wajib: sku/materialCode + delta"}
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return {"applied": False, "note": "delta bukan angka"}
    v = q1("SELECT id, sku, alias FROM product_variants WHERE sku=? OR alias=?", (sku, sku))
    if not v:
        return {"applied": False, "note": f"SKU {sku} tidak ditemukan di product_variants"}
    c = conn()
    cur = c.cursor()
    for r in cur.execute("SELECT variant_id, warehouse_id, available FROM variant_stocks "
                         "WHERE variant_id=? AND available>0 ORDER BY available DESC", (v["id"],)).fetchall():
        if delta <= 0:
            break
        take = min(r["available"], delta)
        cur.execute("UPDATE variant_stocks SET available=available-? WHERE variant_id=? AND warehouse_id=?",
                    (take, v["id"], r["warehouse_id"]))
        delta -= take
    remaining = cur.execute("SELECT COALESCE(SUM(available),0) s FROM variant_stocks WHERE variant_id=?",
                            (v["id"],)).fetchone()["s"]
    c.commit()
    c.close()
    return {"applied": True, "sku": v["sku"], "variant_id": v["id"], "sisa_stok_total": remaining}


def apply_order_status(data):
    """order.status_changed/updated -> ubah status order di DB."""
    ref = data.get("ref") or data.get("reference_no") or data.get("order_ref")
    status = data.get("status") or data.get("current_status") or data.get("to_status")
    if not ref or not status:
        return {"applied": False, "note": "field wajib: ref/reference_no + status"}
    c = conn()
    cur = c.execute("UPDATE orders SET status=?, updated_at=datetime('now') WHERE reference_no=?",
                    (status, ref))
    n = cur.rowcount
    tn = data.get("tracking_no") or data.get("tracking_number") or data.get("awb")
    if tn:
        c.execute("UPDATE orders SET awb=? WHERE reference_no=?", (tn, ref))
    c.commit()
    c.close()
    return {"applied": n > 0, "reference_no": ref, "status": status, "rows_updated": n}


@app.post("/api/webhooks/wms")
async def webhook_wms(body: dict = None):
    body = body if isinstance(body, dict) else {}
    event = body.get("event", "")
    data = body.get("data") or {}
    event_id = body.get("event_id") or f"evt-{int(time.time() * 1000)}"
    if event in ("stock.updated", "inventory.updated"):
        effect = apply_stock_update(data)
    elif event in ("order.status_changed", "order.status_updated", "order.updated"):
        effect = apply_order_status(data)
    else:
        effect = {"applied": False, "note": f"event {event!r} diterima (ping/unknown) — tanpa mutasi DB"}
    c = conn()
    cur = c.execute("INSERT INTO webhook_events (event_id, event, payload, applied, effect) "
                    "VALUES (?,?,?,?,?)",
                    (event_id, event, json.dumps(body, ensure_ascii=False),
                     1 if effect.get("applied") else 0, json.dumps(effect, ensure_ascii=False)))
    total = cur.execute("SELECT COUNT(*) c FROM webhook_events").fetchone()["c"]
    c.commit()
    c.close()
    return env({"applied": bool(effect.get("applied")), "event": event, "event_id": event_id,
                "effect": effect, "total_events": total})


@app.get("/api/webhooks/wms")
async def webhook_wms_get(limit: int = 50):
    rows = q("SELECT id, event_id, event, applied, effect, received_at FROM webhook_events "
             "ORDER BY id DESC LIMIT ?", (limit,))
    return env([dict(r) for r in rows])


# ============================ Webhook untuk storefront ============================

@app.get("/api/webhooks/catalog")
async def webhook_catalog(page: int = 1, length: int = 250):
    """Master-data product untuk ditampilkan di storefront (GET -> data katalog)."""
    page = max(1, page)
    length = max(1, min(length, 500))
    count = q1("SELECT COUNT(*) c FROM product_variants")["c"]
    rows = q("SELECT id, product_group_id, name, sku, sku_code, alias, sell_price, "
             "purchase_price, is_bundle FROM product_variants ORDER BY id LIMIT ? OFFSET ?",
             (length, (page - 1) * length))
    data = []
    for r in rows:
        v = dict(r)
        v["is_bundle"] = bool(v["is_bundle"])
        v["components"] = [dict(c) for c in q(
            "SELECT product_id, component_sku, multiplier_qty FROM variant_components WHERE variant_id=?",
            (v["id"],))]
        v["images"] = [im["url"] for im in q(
            "SELECT url FROM variant_images WHERE variant_id=? LIMIT 1", (v["id"],))]
        v["stock_available"] = q1(
            "SELECT COALESCE(SUM(available),0) s FROM variant_stocks WHERE variant_id=?", (v["id"],))["s"]
        data.append(v)
    return env(data, pagemeta(page, length, count))


@app.post("/api/webhooks/purchase-order")
async def webhook_purchase_order(body: dict = None):
    """Terima purchase order baru dari storefront -> buat order di DB (status awal)."""
    body = body if isinstance(body, dict) else {}
    ref = (body.get("reference_no") or body.get("order_ref") or body.get("ref")
           or f"ORD-WEBSTORE-PROD-{int(time.time())}")
    items = body.get("items") or []
    if isinstance(items, dict):
        items = [items]
    items = [it for it in items if isinstance(it, dict)]
    product_summary = ", ".join(f"{int(it.get('qty', 1))} {it.get('sku', '')}" for it in items)
    qty = sum(int(it.get("qty", 1)) for it in items)
    amount = body.get("amount")
    if amount is None:
        amount = sum((int(it.get("qty", 1)) or 1) * (int(it.get("price") or it.get("sell_price") or 0))
                     for it in items)
    status = body.get("status") or "paid"
    chan_name = body.get("channel") or "WEBSTORE"
    chan_id = body.get("channel_id")
    if chan_id is None:
        ch = q1("SELECT id FROM channels WHERE name=?", (chan_name,))
        chan_id = ch["id"] if ch else 13
    exist = q1("SELECT id FROM orders WHERE reference_no=?", (ref,))
    if exist:
        return env({"applied": False, "note": f"reference_no {ref} sudah ada", "order_id": exist["id"]})
    c = conn()
    cur = c.execute(
        "INSERT INTO orders (reference_no, order_at, created_at, channel_name, channel_id, "
        "customer_name, customer_phone, customer_email, qty, amount, product_summary, status, "
        "is_cod, is_fake_order) VALUES (?, datetime('now'), datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (ref, chan_name, chan_id, body.get("customer_name") or "", body.get("customer_phone") or "",
         body.get("customer_email") or "", qty, int(amount or 0), product_summary, status))
    oid = cur.lastrowid
    cur.execute("INSERT INTO webhook_events (event_id, event, payload, applied, effect) VALUES (?,?,?,?,?)",
                (body.get("event_id") or f"evt-po-{int(time.time() * 1000)}", "purchase-order.created",
                 json.dumps(body, ensure_ascii=False), 1,
                 json.dumps({"order_id": oid, "reference_no": ref, "status": status})))
    c.commit()
    c.close()
    return env({"applied": True, "order_id": oid, "reference_no": ref, "status": status,
                "qty": qty, "amount": int(amount or 0), "product_summary": product_summary})


# ============================ TO-BE — BOM virtual kitting ============================


def _load_bundle_bom():
    """Bulk-load stok + komponen bundle dalam 3 query (bukan per-bundle)."""
    c = conn()
    cur = c.cursor()
    stocks = {r["variant_id"]: r["s"] for r in cur.execute(
        "SELECT variant_id, COALESCE(SUM(available),0) s FROM variant_stocks GROUP BY variant_id")}
    comps = {}
    for r in cur.execute("SELECT variant_id, product_id, component_sku, multiplier_qty FROM variant_components"):
        comps.setdefault(r["variant_id"], []).append(
            (r["product_id"], r["component_sku"], r["multiplier_qty"]))
    bundles = [(r["id"], r["sku"]) for r in cur.execute(
        "SELECT id, sku FROM product_variants WHERE is_bundle=1")]
    c.close()
    return stocks, comps, bundles


@app.get("/api/to-be/bundle-stock")
def to_be_bundle_stock(sku: str = None, limit: int = 200):
    """TO-BE: hitung ulang stok bundle dari BOM (min komponen) vs AS-IS statis (phantom)."""
    stocks, comps, bundles = _load_bundle_bom()
    out = []
    for bid, bsku in bundles:
        cs = comps.get(bid, [])
        as_is = stocks.get(bid, 0)
        detail, to_be = [], None
        for pid, csku, mult in cs:
            sa = stocks.get(pid, 0)
            detail.append({"component_sku": csku, "multiplier": mult, "stock": sa})
            units = sa // mult if mult else sa
            to_be = units if to_be is None else min(to_be, units)
        to_be = to_be or 0
        out.append({"sku": bsku, "is_bundle": True, "stock_as_is": as_is,
                    "stock_to_be": to_be, "phantom": as_is - to_be, "components": detail})
    out.sort(key=lambda x: abs(x["phantom"]), reverse=True)
    if sku:
        hit = next((b for b in out if b["sku"] == sku), None)
        return env(hit if hit else {"sku": sku, "note": "bukan bundle / tidak ditemukan"})
    oversell = sum(1 for b in out if b["phantom"] > 0)   # stok statis > kapasitas komponen
    understate = sum(1 for b in out if b["phantom"] < 0)  # stok statis < kapasitas (rugi penjualan)
    matched = sum(1 for b in out if b["phantom"] == 0)
    return env({
        "summary": {"total_bundles": len(out), "oversell": oversell,
                    "understate": understate, "matched": matched},
        "bundles": out[:limit],
    })


@app.get("/api/to-be/sales-summary")
def sales_summary():
    """KPI outbound/sales ala Perpack (Sales Order + Sales Product per status)."""
    rows = q("SELECT status, COUNT(*) c, COALESCE(SUM(qty),0) q FROM orders GROUP BY status")
    by = {r["status"]: {"order": r["c"], "qty": r["q"]} for r in rows}
    BUCKETS = {
        "on_process": ("paid", "process", "processing"),
        "on_picking": ("pick", "picking"),
        "on_packing": ("packing", "pack"),
        "packed": ("packed",),
    }
    def agg(field):
        return {k: sum(by.get(s, {}).get(field, 0) for s in grp) for k, grp in BUCKETS.items()}
    return env({
        "sales_order": agg("order"),
        "sales_product": agg("qty"),
        "total_orders": sum(v["order"] for v in by.values()),
    })


@app.get("/", response_class=HTMLResponse)
def demo_page():
    index = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    return FileResponse(index)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)