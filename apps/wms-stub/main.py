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
import hashlib
import hmac
import io
import json
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
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


# ============================ User & Role (RBAC + Access Control) ============================

ROLES = ("admin", "sales", "warehouse")
AUTH_ENFORCED = os.getenv("AUTH_ENFORCED", "0") == "1"   # 1 = mutasi wajib token; 0 = demo terbuka


def _auth_err(status, id_msg, en_msg):
    return {"code": status, "msg": {"id": id_msg, "en": en_msg}, "data": None}


def _err_env(status, id_msg, en_msg, data=None):
    return {"code": status, "msg": {"id": id_msg, "en": en_msg}, "data": data}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
    return f"{salt}${dk}"


def verify_password(password, stored):
    try:
        salt, dk = stored.split("$", 1)
        return secrets.compare_digest(
            dk, hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex())
    except Exception:
        return False


def _issue_token(user_id, ttl_hours=24):
    token = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = conn()
    c.execute("INSERT INTO auth_tokens (token_hash, user_id, expires_at) VALUES (?,?,?)",
              (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
    c.commit()
    c.close()
    return token


def current_user(authorization: str = Header(None)):
    """Resolve token (Authorization: Bearer <t> atau Authorization: <t>) -> user dict / None."""
    if not authorization:
        return None
    t = authorization.strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if not t:
        return None
    th = hashlib.sha256(t.encode()).hexdigest()
    return q1("SELECT u.id, u.username, u.email, u.name, u.role, u.warehouse_id, u.status "
              "FROM auth_tokens t JOIN users u ON u.id = t.user_id "
              "WHERE t.token_hash=? AND t.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ','now') AND u.status='active'",
              (th,))


def require_role(*roles):
    """Dependency: wajib token aktif; kalau `roles` terisi, role user harus cocok."""
    def dep(authorization: str = Header(None)):
        user = current_user(authorization)
        if not user:
            raise HTTPException(401, detail=_auth_err(
                401, "Autentikasi diperlukan (token tidak valid/kadaluarsa)", "Authentication required"))
        if roles and user["role"] not in roles:
            raise HTTPException(403, detail=_auth_err(
                403, "Akses ditolak: role tidak cukup", "Insufficient role"))
        return user
    return dep


def require_write():
    """Dependency mutasi: AUTH_ENFORCED=1 -> wajib login (role aktif apa pun); kalau 0 -> demo terbuka."""
    def dep(authorization: str = Header(None)):
        if not AUTH_ENFORCED:
            return {"role": "guest", "guest": True}
        user = current_user(authorization)
        if not user:
            raise HTTPException(401, detail=_auth_err(
                401, "Autentikasi diperlukan (token tidak valid/kadaluarsa)", "Authentication required"))
        return user
    return dep


@app.get("/api/auth/me")
def auth_me(user=Depends(require_role())):
    return env(user)


@app.post("/api/auth/register")
def auth_register(body: dict = None, authorization: str = Header(None)):
    body = body if isinstance(body, dict) else {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        raise HTTPException(422, detail=_auth_err(422, "username & password wajib diisi", "username & password required"))
    if len(password) < 6:
        raise HTTPException(422, detail=_auth_err(422, "password minimal 6 karakter", "password min 6 chars"))
    if q1("SELECT id FROM users WHERE username=?", (username,)):
        raise HTTPException(409, detail=_auth_err(409, "username sudah terdaftar", "username already exists"))
    role = (body.get("role") or "sales").lower()
    if role not in ROLES:
        role = "sales"
    if role == "admin":
        admin = current_user(authorization)   # Super Admin hanya boleh dibuat oleh Super Admin
        if not admin or admin["role"] != "admin":
            raise HTTPException(403, detail=_auth_err(
                403, "Super Admin hanya bisa dibuat oleh Super Admin yang login",
                "Super Admin role requires an authenticated Super Admin"))
    c = conn()
    cur = c.execute("INSERT INTO users (username, email, name, password_hash, role, status) "
                    "VALUES (?,?,?,?,?, 'active')",
                    (username, (body.get("email") or "").strip() or None,
                     body.get("name") or "", hash_password(password), role))
    uid = cur.lastrowid
    c.commit()
    c.close()
    return env({"id": uid, "username": username, "name": body.get("name") or "",
                "email": (body.get("email") or "").strip(), "role": role})


@app.post("/api/auth/login")
def auth_login(body: dict = None):
    body = body if isinstance(body, dict) else {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    u = q1("SELECT id, username, email, name, password_hash, role, warehouse_id, status "
           "FROM users WHERE username=?", (username,))
    if not u or u["status"] != "active" or not verify_password(password, u["password_hash"]):
        raise HTTPException(401, detail=_auth_err(401, "Username atau password salah", "Invalid credentials"))
    token = _issue_token(u["id"])
    return env({"token": token, "user": {k: u[k] for k in ("id", "username", "email", "name", "role", "warehouse_id", "status")}})


@app.get("/api/auth/users")
def auth_users(_=Depends(require_role("admin"))):
    rows = q("SELECT id, username, email, name, role, warehouse_id, status, created_at FROM users ORDER BY id")
    return env([dict(r) for r in rows])


@app.patch("/api/auth/users/{user_id}/role")
def auth_set_role(user_id: int, body: dict = None, _=Depends(require_role("admin"))):
    body = body if isinstance(body, dict) else {}
    role = (body.get("role") or "").lower()
    if role not in ROLES:
        raise HTTPException(422, detail=_auth_err(422, f"role harus salah satu dari {ROLES}", "invalid role"))
    c = conn()
    cur = c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    c.commit()
    c.close()
    if cur.rowcount == 0:
        raise HTTPException(404, detail=_auth_err(404, "User tidak ditemukan", "User not found"))
    return env({"updated": True, "user_id": user_id, "role": role})


@app.post("/api/auth/logout")
def auth_logout(authorization: str = Header(None)):
    t = (authorization or "").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if t:
        c = conn()
        c.execute("DELETE FROM auth_tokens WHERE token_hash=?", (hashlib.sha256(t.encode()).hexdigest(),))
        c.commit()
        c.close()
    return env({"logged_out": True})


# ============================ DB ============================

def conn():
    c = sqlite3.connect(WMS_DB)
    c.row_factory = sqlite3.Row
    return c


@app.on_event("startup")
def _ensure_schema():
    """Pastikan schema lengkap secara idempotent (semua DDL pakai IF NOT EXISTS),
    termasuk tabel baru seperti push_outbox — tanpa destruktif ke data yang ada."""
    c = conn()
    c.executescript(open(os.path.join(BASE, "db", "schema.sql")).read())
    c.commit()
    c.close()


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
    statuses: list = None


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
    if body.statuses:
        conds.append(f"status IN ({','.join('?' * len(body.statuses))})")
        args += list(body.statuses)
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


ORDER_STATUS_ALLOWED = ("paid", "process", "picking", "packing", "packed",
                        "ready_to_pickup", "sent", "completed", "cancelled", "returned", "pending")

# alur maju lifecycle order (untuk deteksi regresi status)
ORDER_FLOW = ["pending", "paid", "process", "picking", "packing", "packed",
              "ready_to_pickup", "sent", "completed"]


def apply_order_status(data, event_timestamp=None):
    """order.status_changed/updated -> ubah status order di DB.
    G1: validasi enum ketat (status di luar whitelist ditolak).
    G2: guard out-of-order (event_timestamp lebih tua dari updated_at terakhir -> ditolak)."""
    ref = data.get("ref") or data.get("reference_no") or data.get("order_ref")
    status = (data.get("status") or data.get("current_status") or data.get("to_status") or "").lower()
    if not ref or not status:
        return {"applied": False, "reason": "missing_fields", "note": "field wajib: ref/reference_no + status"}
    if status not in ORDER_STATUS_ALLOWED:
        return {"applied": False, "reason": "invalid_status", "status": status,
                "allowed": list(ORDER_STATUS_ALLOWED),
                "note": f"status {status!r} di luar whitelist enum"}
    c = conn()
    cur = c.cursor()
    row = cur.execute("SELECT status, updated_at FROM orders WHERE reference_no=?", (ref,)).fetchone()
    if not row:
        c.close()
        return {"applied": False, "reason": "not_found", "note": f"order {ref} tidak ditemukan"}
    if event_timestamp and row["updated_at"] and event_timestamp < row["updated_at"]:
        c.close()
        return {"applied": False, "reason": "out_of_order", "ref": ref,
                "event_timestamp": event_timestamp, "last_updated_at": row["updated_at"],
                "note": "event lebih tua dari status terakhir (out-of-order)"}
    prev_status = (row["status"] or "").lower()
    if status in ORDER_FLOW and prev_status in ORDER_FLOW:
        if ORDER_FLOW.index(status) < ORDER_FLOW.index(prev_status):
            c.close()
            return {"applied": False, "reason": "status_regression", "ref": ref,
                    "from": row["status"], "to": status,
                    "note": f"status mundur (regresi) dari {row['status']} ke {status}"}
    new_at = event_timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = cur.execute("UPDATE orders SET status=?, updated_at=? WHERE reference_no=?",
                      (status, new_at, ref))
    n = cur.rowcount
    tn = data.get("tracking_no") or data.get("tracking_number") or data.get("awb")
    if tn:
        cur.execute("UPDATE orders SET awb=? WHERE reference_no=?", (tn, ref))
    c.commit()
    c.close()
    return {"applied": n > 0, "reference_no": ref, "status": status,
            "previous_status": row["status"], "rows_updated": n}


# ============================ Push Outbound (L3 realtime) ============================

ERP_PUSH_URL = os.getenv("ERP_PUSH_WEBHOOK_URL", "")
ERP_PUSH_SECRET = os.getenv("ERP_PUSH_WEBHOOK_SECRET", "")


def _sign_payload(raw_body: str) -> str:
    return hmac.new(ERP_PUSH_SECRET.encode(), raw_body.encode(), hashlib.sha256).hexdigest()


def _enqueue_push(event_id: str, event: str, data: dict):
    """L3: kirim webhook outbound (HMAC) ke webstore + catat di push_outbox (best-effort)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {"event": event, "event_id": event_id, "timestamp": ts, "data": data}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sig = _sign_payload(raw)
    if not ERP_PUSH_URL:
        return {"pushed": False, "status": "skipped", "note": "ERP_PUSH_WEBHOOK_URL tidak diset"}
    c = conn()
    try:
        r = httpx.post(ERP_PUSH_URL, content=raw.encode(),
                       headers={"Content-Type": "application/json",
                                "X-Perpack-Signature": sig,
                                "X-Perpack-Event-Id": event_id,
                                "X-Perpack-Timestamp": ts}, timeout=3.0)
        status = "sent" if r.status_code < 300 else "failed"
        note = f"HTTP {r.status_code}"
        attempts = 1
    except Exception as e:
        status, note, attempts = "failed", str(e), 0
    c.execute("INSERT INTO push_outbox (event_id, event, payload, status, attempts, note) VALUES (?,?,?,?,?,?)",
              (event_id, event, raw, status, attempts, note))
    c.commit()
    c.close()
    return {"pushed": status == "sent", "status": status, "note": note, "signature": sig[:16] + "…"}


@app.post("/api/webhooks/wms")
async def webhook_wms(body: dict = None, actor=Depends(require_write())):
    body = body if isinstance(body, dict) else {}
    event = body.get("event", "")
    data = body.get("data") or {}
    event_id = body.get("event_id") or f"evt-{int(time.time() * 1000)}"
    # idempotence: event_id sudah pernah diproses -> jangan mutasi ulang (cegah dobel potong)
    c = conn()
    dup = c.execute("SELECT effect FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
    if dup:
        c.close()
        total = q1("SELECT COUNT(*) c FROM webhook_events")["c"]
        return env({"applied": False, "event": event, "event_id": event_id,
                    "note": "event_id sudah diproses (idempotent)",
                    "effect": json.loads(dup["effect"]) if dup["effect"] else None,
                    "total_events": total})

    ts = (body.get("timestamp") or body.get("event_timestamp")
          or data.get("updated_at") or data.get("event_timestamp")
          or data.get("timestamp") or data.get("occurred_at"))
    if event in ("stock.updated", "inventory.updated"):
        effect = apply_stock_update(data)
    elif event in ("order.status_changed", "order.status_updated", "order.updated"):
        effect = apply_order_status(data, event_timestamp=ts)
    else:
        effect = {"applied": False, "note": f"event {event!r} diterima (ping/unknown) — tanpa mutasi DB"}

    # audit (selalu dicatat — termasuk event yang gagal validasi)
    cur = c.execute("INSERT INTO webhook_events (event_id, event, payload, applied, effect) "
                    "VALUES (?,?,?,?,?)",
                    (event_id, event, json.dumps(body, ensure_ascii=False),
                     1 if effect.get("applied") else 0, json.dumps(effect, ensure_ascii=False)))
    total = cur.execute("SELECT COUNT(*) c FROM webhook_events").fetchone()["c"]
    c.commit()
    c.close()

    # L3: push outbound bila ada mutasi yang benar-benar diterapkan
    push = _enqueue_push(event_id, event, data) if effect.get("applied") else None

    reason = effect.get("reason")
    if reason == "invalid_status":
        return JSONResponse(status_code=422, content=_err_env(422, "Status di luar whitelist enum", "Status outside whitelist", effect))
    if reason == "out_of_order":
        return JSONResponse(status_code=409, content=_err_env(409, "Event out-of-order (lebih tua)", "Out-of-order event", effect))
    if reason == "status_regression":
        return JSONResponse(status_code=409, content=_err_env(409, "Status regresi (mundur dari alur)", "Status regression", effect))
    if reason == "not_found":
        return JSONResponse(status_code=404, content=_err_env(40401, "Order tidak ditemukan", "Order not found", effect))

    return env({"applied": bool(effect.get("applied")), "event": event, "event_id": event_id,
                "effect": effect, "total_events": total, "push": push})


@app.get("/api/webhooks/wms")
async def webhook_wms_get(limit: int = 50):
    rows = q("SELECT id, event_id, event, applied, effect, received_at FROM webhook_events "
             "ORDER BY id DESC LIMIT ?", (limit,))
    return env([dict(r) for r in rows])


@app.get("/api/push-outbox")
async def push_outbox_list(limit: int = 50):
    """Audit outbox push L3 (event outbound ERP -> webstore)."""
    rows = q("SELECT id, event_id, event, status, attempts, note, created_at FROM push_outbox "
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


def _resolve_check_deduct_stock(cur, items):
    """Atomik: resolve item -> kebutuhan stok (ekspansi BOM), cek ketersediaan, lalu potong.
    Semua via cursor `cur`; commit/rollback oleh pemanggil. Return (ok, info)."""
    need, detail = {}, []
    for it in items:
        sku = (it.get("sku") or "").strip()
        try:
            qty = int(it.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 0
        if not sku or qty <= 0:
            return False, {"error": "SKU kosong / qty invalid", "sku": sku or None}
        v = cur.execute("SELECT id, sku, is_bundle FROM product_variants WHERE sku=? OR alias=?",
                        (sku, sku)).fetchone()
        if not v:
            return False, {"error": "SKU tidak ditemukan", "sku": sku}
        if v["is_bundle"]:
            comps = cur.execute("SELECT component_sku, multiplier_qty FROM variant_components WHERE variant_id=?",
                                (v["id"],)).fetchall()
            detail.append({"sku": sku, "qty": qty, "bundle": True,
                           "components": [{"component_sku": c["component_sku"], "multiplier_qty": c["multiplier_qty"]} for c in comps]})
            if not comps:
                need[v["sku"]] = need.get(v["sku"], 0) + qty
            else:
                for c in comps:
                    need[c["component_sku"]] = need.get(c["component_sku"], 0) + qty * c["multiplier_qty"]
        else:
            need[v["sku"]] = need.get(v["sku"], 0) + qty
            detail.append({"sku": sku, "qty": qty, "bundle": False, "components": []})

    insufficient = []
    for sku, qty_needed in need.items():
        avail = cur.execute(
            "SELECT COALESCE(SUM(vs.available),0) s FROM variant_stocks vs "
            "JOIN product_variants pv ON pv.id=vs.variant_id WHERE pv.sku=?", (sku,)).fetchone()["s"]
        if avail < qty_needed:
            insufficient.append({"sku": sku, "needed": qty_needed, "available": avail})
    if insufficient:
        return False, {"error": "Stok tidak cukup", "insufficient": insufficient}

    for sku, qty_needed in need.items():
        rows = cur.execute(
            "SELECT vs.variant_id, vs.warehouse_id, vs.available FROM variant_stocks vs "
            "JOIN product_variants pv ON pv.id=vs.variant_id WHERE pv.sku=? AND vs.available>0 "
            "ORDER BY vs.available DESC", (sku,)).fetchall()
        remaining = qty_needed
        for r in rows:
            if remaining <= 0:
                break
            take = min(r["available"], remaining)
            cur.execute("UPDATE variant_stocks SET available=available-? WHERE variant_id=? AND warehouse_id=?",
                        (take, r["variant_id"], r["warehouse_id"]))
            remaining -= take
    return True, {"resolved": detail, "deducted": need}


@app.post("/api/webhooks/purchase-order")
async def webhook_purchase_order(body: dict = None, actor=Depends(require_write())):
    """Terima PO + potong stok DALAM SATU TRANSAKSI (atomik, idempoten by reference_no)."""
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

    c = conn()
    cur = c.cursor()
    try:
        chan_id = body.get("channel_id")
        if chan_id is None:
            ch = cur.execute("SELECT id FROM channels WHERE name=?", (chan_name,)).fetchone()
            chan_id = ch["id"] if ch else 13

        # idempotence (retry reference_no sama -> kembalikan order lama, tanpa potong ulang)
        exist = cur.execute("SELECT id, status FROM orders WHERE reference_no=?", (ref,)).fetchone()
        if exist:
            c.close()
            return env({"applied": False, "note": f"reference_no {ref} sudah ada (idempotent)",
                        "order_id": exist["id"], "status": exist["status"]})

        ok, info = _resolve_check_deduct_stock(cur, items)
        if not ok:
            c.rollback()
            c.close()
            return JSONResponse(status_code=409, content={
                "code": 409,
                "msg": {"id": info.get("error", "Stok tidak cukup"),
                        "en": info.get("error", "Insufficient stock")},
                "data": info})

        shipping_fee = int(body.get("shipping_fee") or 0)
        courier = body.get("courier") or None
        cur.execute(
            "INSERT INTO orders (reference_no, order_at, created_at, channel_name, channel_id, "
            "customer_name, customer_phone, customer_email, qty, amount, shipping_fee, courier, product_summary, status, "
            "is_cod, is_fake_order) VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (ref, chan_name, chan_id, body.get("customer_name") or "", body.get("customer_phone") or "",
             body.get("customer_email") or "", qty, int(amount or 0), shipping_fee, courier, product_summary, status))
        oid = cur.lastrowid
        cur.execute("INSERT INTO webhook_events (event_id, event, payload, applied, effect) VALUES (?,?,?,?,?)",
                    (body.get("event_id") or f"evt-po-{int(time.time() * 1000)}", "purchase-order.created",
                     json.dumps(body, ensure_ascii=False), 1,
                     json.dumps({"order_id": oid, "reference_no": ref, "status": status, "stock": info})))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return env({"applied": True, "order_id": oid, "reference_no": ref, "status": status,
                "qty": qty, "amount": int(amount or 0), "product_summary": product_summary,
                "stock_deducted": info})


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
        "status_counts": {k: by[k]["order"] for k in by},
    })


# ============================ Export Excel ============================

@app.get("/api/export/orders")
def export_orders():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    rows = q("SELECT reference_no, order_at, deadline_at, customer_name, warehouse_name, "
             "qty, amount, channel_name, courier, status FROM orders ORDER BY id DESC")
    wb = Workbook(); ws = wb.active; ws.title = "Outbound Sales"
    headers = ["Reference No", "Tgl Order", "Deadline", "Penerima", "Lokasi", "Qty", "Total", "Sumber", "Kurir", "Status"]
    ws.append(headers)
    for r in rows:
        ws.append([r["reference_no"], (r["order_at"] or "")[:19], (r["deadline_at"] or "")[:19],
                   r["customer_name"], r["warehouse_name"], r["qty"], r["amount"],
                   r["channel_name"], r["courier"], r["status"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, w in enumerate([26, 20, 20, 20, 14, 8, 14, 14, 14, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO(); wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=outbound_sales.xlsx"})


@app.get("/api/export/products")
def export_products():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    rows = q("SELECT id, name, sku, sku_code, sell_price, is_bundle FROM product_variants ORDER BY id")
    wb = Workbook(); ws = wb.active; ws.title = "Master Data Produk"
    ws.append(["ID", "Nama", "SKU", "SKU Code", "Harga Jual", "Bundle"])
    for r in rows:
        ws.append([r["id"], r["name"], r["sku"], r["sku_code"], r["sell_price"],
                   "Ya" if r["is_bundle"] else "Tidak"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, w in enumerate([8, 40, 28, 20, 14, 10], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO(); wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=master_data_product.xlsx"})


@app.get("/", response_class=HTMLResponse)
def demo_page():
    index = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    if not os.path.exists(index):
        return HTMLResponse("<h1>Halaman tidak ditemukan</h1>", status_code=404)
    return HTMLResponse(open(index, encoding="utf-8").read(),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                 "Pragma": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)